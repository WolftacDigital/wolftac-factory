"""R1: the read-only Discord approval reader (WD-546).

One CLI command, ``hermes -p integrator wolftac-approval-read --channel <id>
--message <id> --emoji <rocket> --json``, reads one #approvals message and the
complete list of accounts that reacted with the rocket, then prints one JSON line.

Rules this module keeps (each is tested in deploy/tests/test_approval_read_plugin.py):

* Importing it does nothing: no I/O, no credential read, no thread.
* The only credential is Harper's bot token, obtained inside a run through
  ``agent.secret_scope.get_secret("DISCORD_BOT_TOKEN")``. It is never logged,
  printed, decoded or passed on.
* The first request proves the token belongs to Harper's bot. Nothing else is
  read until it does.
* GET only, against the fixed origin below. No proxy, verified TLS, no redirects.
* Scope is fixed: guild ``GUILD_ID``, and #approvals or a thread whose parent is
  #approvals.
* Every response is read in bounded chunks. The reactor list is complete or the
  run fails; it is never partial.
* One 60-second deadline covers the whole run. A watchdog thread prints the
  timeout JSON and ends the process with exit 124.
* Errors print fixed codes only, never a response body or exception text.

A reaction is attributed by Discord to the account that added it. This reader
reports facts; deciding whether they approve a merge is the verifier's job
(deploy/discord_approval.py).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import ssl
import sys
import threading
import time
import urllib.error
import urllib.request

COMMAND = "wolftac-approval-read"
CONTRACT = 1
API_ORIGIN = "https://discord.com/api/v10"
GUILD_ID = "923752167293222912"
APPROVALS_CHANNEL_ID = "1562236246158221323"
READER_BOT_ID = "1562178628211449966"
ROCKET = "\U0001F680"
ROCKET_PATH = "%F0%9F%9A%80"
SECRET_NAME = "DISCORD_BOT_TOKEN"
USER_AGENT = "DiscordBot (https://github.com/WolftacDigital/wolftac-factory, 1) wolftac-approval-read"

ID_RE = re.compile(r"[0-9]{17,20}", re.ASCII)
THREAD_TYPES = frozenset({10, 11, 12})

DEADLINE_SECONDS = 60.0
SOCKET_TIMEOUT_CAP = 10.0
RETRY_AFTER_CAP = 5.0
OBJECT_LIMIT = 64 * 1024
PAGE_LIMIT = 256 * 1024
CONTENT_LIMIT = 4000
OUTPUT_LIMIT = 1024 * 1024
PAGE_SIZE = 100
MAX_PAGES = 50
CHUNK = 8192

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_SCOPE = 3
EXIT_CREDENTIAL = 4
EXIT_READ = 5
EXIT_TIMEOUT = 124


class ReadError(Exception):
    """A fixed, non-sensitive failure code. Never carries remote text."""

    def __init__(self, code: str, exit_code: int, http_status: int | None = None):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code
        self.http_status = http_status


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        try:
            fp.close()
        except Exception:  # noqa: BLE001
            pass
        raise ReadError("redirect_refused", EXIT_READ, code)


def build_opener():
    """No proxy (environment proxy settings are ignored), verified TLS, no redirects."""
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RefuseRedirects(),
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
    )


def error_line(code: str, http_status: int | None = None) -> str:
    payload = {"ok": False, "contract": CONTRACT, "error": code}
    if http_status is not None:
        payload["http_status"] = int(http_status)
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def valid_id(value) -> bool:
    return isinstance(value, str) and ID_RE.fullmatch(value) is not None


class _Deadline:
    """Hard wall-clock limit for the whole run, enforced by a watchdog thread."""

    def __init__(self, seconds: float, clock, emit, exit_fn):
        self._clock = clock
        self._end = clock() + seconds
        self._emit = emit
        self._exit = exit_fn
        self._lock = threading.Lock()
        self._done = False
        self._timer = threading.Timer(seconds, self._fire)
        self._timer.daemon = True

    def start(self) -> None:
        self._timer.start()

    def remaining(self) -> float:
        return self._end - self._clock()

    def _fire(self) -> None:
        with self._lock:
            if self._done:
                return
            self._done = True
            self._emit(error_line("timeout"))
            self._exit(EXIT_TIMEOUT)

    def finish(self) -> bool:
        """Claim the right to print the result; False once the watchdog has fired."""
        with self._lock:
            if self._done:
                return False
            self._done = True
            self._timer.cancel()
            return True


class _Client:
    def __init__(self, token: str, opener, deadline: _Deadline, sleep):
        self._token = token
        self._opener = opener
        self._deadline = deadline
        self._sleep = sleep
        self.requests = 0

    def get(self, path: str, limit: int):
        """GET one JSON document. Returns (status, document); non-200 has document None."""
        retried = False
        while True:
            remaining = self._deadline.remaining()
            if remaining <= 0:
                raise ReadError("timeout", EXIT_TIMEOUT)
            request = urllib.request.Request(
                API_ORIGIN + path, method="GET",
                headers={"Authorization": "Bot " + self._token, "User-Agent": USER_AGENT},
            )
            self.requests += 1
            try:
                response = self._opener.open(request, timeout=min(SOCKET_TIMEOUT_CAP, remaining))
            except ReadError:
                raise
            except urllib.error.HTTPError as exc:
                status = exc.code
                try:
                    if status == 429:
                        wait = None if retried else _retry_after(exc)
                        if wait is not None and wait <= RETRY_AFTER_CAP and wait < self._deadline.remaining():
                            retried = True
                            self._sleep(wait)
                            continue
                        raise ReadError("rate_limited", EXIT_READ, 429)
                    return status, None
                finally:
                    exc.close()
            except Exception:  # noqa: BLE001 — never echo transport text
                raise ReadError("network_error", EXIT_READ) from None
            try:
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                if status != 200:
                    return status, None
                body = _read_bounded(response, limit)
            finally:
                response.close()
            try:
                return 200, json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ReadError("malformed_response", EXIT_READ) from None


def _read_bounded(response, limit: int) -> bytes:
    chunks = []
    total = 0
    while True:
        chunk = response.read(CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ReadError("response_too_large", EXIT_READ)
        chunks.append(chunk)
    return b"".join(chunks)


def _retry_after(exc) -> float | None:
    """Seconds to wait from a 429, or None when it cannot be read safely."""
    value = None
    try:
        body = _read_bounded(exc, OBJECT_LIMIT)
        document = json.loads(body.decode("utf-8")) if body else {}
        if isinstance(document, dict):
            value = document.get("retry_after")
    except Exception:  # noqa: BLE001
        value = None
    if value is None and exc.headers is not None:
        value = exc.headers.get("Retry-After")
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds < 0:
        return None
    return seconds


def _message_snapshot(document, channel_id: str, message_id: str) -> dict:
    if not isinstance(document, dict):
        raise ReadError("malformed_response", EXIT_READ)
    author = document.get("author")
    content = document.get("content")
    timestamp = document.get("timestamp")
    edited = document.get("edited_timestamp")
    if (document.get("id") != message_id or document.get("channel_id") != channel_id
            or not isinstance(author, dict) or not valid_id(author.get("id"))
            or ("bot" in author and not isinstance(author["bot"], bool))
            or not isinstance(content, str) or not isinstance(timestamp, str)
            or not (edited is None or isinstance(edited, str))):
        raise ReadError("malformed_response", EXIT_READ)
    if len(content) > CONTENT_LIMIT:
        raise ReadError("response_too_large", EXIT_READ)
    return {
        "id": message_id,
        "author": {"id": author["id"], "bot": author.get("bot", False)},
        "timestamp": timestamp,
        "edited_timestamp": edited,
        "content": content,
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
    }


def _check_identity(client: _Client) -> str:
    status, me = client.get("/users/@me", OBJECT_LIMIT)
    if status != 200:
        raise ReadError("identity_unverified", EXIT_CREDENTIAL, status)
    if not isinstance(me, dict) or not valid_id(me.get("id")) or not isinstance(me.get("bot", False), bool):
        raise ReadError("identity_unverified", EXIT_CREDENTIAL)
    if me.get("bot") is not True or me["id"] != READER_BOT_ID:
        raise ReadError("identity_mismatch", EXIT_CREDENTIAL)
    return me["id"]


def _check_channel(client: _Client, channel_id: str):
    status, channel = client.get(f"/channels/{channel_id}", OBJECT_LIMIT)
    if status != 200:
        raise ReadError("http_error", EXIT_READ, status)
    if not isinstance(channel, dict) or channel.get("id") != channel_id:
        raise ReadError("malformed_response", EXIT_READ)
    if channel.get("guild_id") != GUILD_ID:
        raise ReadError("channel_not_allowed", EXIT_SCOPE)
    parent = channel.get("parent_id")
    if channel_id == APPROVALS_CHANNEL_ID:
        return parent if valid_id(parent) else None
    if channel.get("type") in THREAD_TYPES and parent == APPROVALS_CHANNEL_ID:
        return parent
    raise ReadError("channel_not_allowed", EXIT_SCOPE)


def _read_message(client: _Client, channel_id: str, message_id: str, missing_code: str) -> dict:
    status, message = client.get(f"/channels/{channel_id}/messages/{message_id}", OBJECT_LIMIT)
    if status == 404:
        raise ReadError(missing_code, EXIT_READ, 404)
    if status != 200:
        raise ReadError("http_error", EXIT_READ, status)
    return _message_snapshot(message, channel_id, message_id)


def _read_reactors(client: _Client, channel_id: str, message_id: str):
    base = f"/channels/{channel_id}/messages/{message_id}/reactions/{ROCKET_PATH}?limit={PAGE_SIZE}"
    reactors = {}
    after = None
    pages = 0
    while True:
        if pages >= MAX_PAGES:
            raise ReadError("pagination_overflow", EXIT_READ)
        status, rows = client.get(base if after is None else f"{base}&after={after}", PAGE_LIMIT)
        if status != 200:
            raise ReadError("http_error", EXIT_READ, status)
        pages += 1
        if not isinstance(rows, list) or len(rows) > PAGE_SIZE:
            raise ReadError("malformed_response", EXIT_READ)
        page_ids = set()
        for row in rows:
            if (not isinstance(row, dict) or not valid_id(row.get("id"))
                    or ("bot" in row and not isinstance(row["bot"], bool)) or row["id"] in page_ids):
                raise ReadError("malformed_row", EXIT_READ)
            page_ids.add(row["id"])
            # Every row must be past the cursor; a repeat across pages means the
            # cursor did not advance, so the list cannot be trusted as complete.
            if after is not None and int(row["id"]) <= int(after):
                raise ReadError("pagination_stalled", EXIT_READ)
            if row["id"] in reactors:
                raise ReadError("pagination_stalled", EXIT_READ)
            # Discord's User resource: an absent bot flag means not a bot.
            reactors[row["id"]] = row.get("bot", False)
        if len(rows) < PAGE_SIZE:
            break
        cursor = max(page_ids, key=int)
        if after is not None and int(cursor) <= int(after):
            raise ReadError("pagination_stalled", EXIT_READ)
        after = cursor
    ordered = [{"id": rid, "bot": reactors[rid]} for rid in sorted(reactors, key=int)]
    return ordered, pages


def _resolve_token(secret_getter) -> str:
    try:
        if secret_getter is None:
            from agent.secret_scope import get_secret as secret_getter  # noqa: PLC0415 — only inside a run
        value = secret_getter(SECRET_NAME)
    except Exception:  # noqa: BLE001 — UnscopedSecretError and every resolver failure
        raise ReadError("token_unavailable", EXIT_CREDENTIAL) from None
    if not isinstance(value, str):
        raise ReadError("token_unavailable", EXIT_CREDENTIAL)
    value = value.strip()
    if not value or any(ch in value for ch in "\r\n\0"):
        raise ReadError("token_unavailable", EXIT_CREDENTIAL)
    return value


def _write(stream, line: str) -> None:
    stream.write(line + "\n")
    stream.flush()


def run(channel, message, emoji, json_flag, *, secret_getter=None, opener=None,
        deadline_seconds: float = DEADLINE_SECONDS, clock=time.monotonic, sleep=time.sleep,
        stdout=None, exit_fn=os._exit) -> int:
    """Read one approval message. Prints exactly one JSON line; returns the exit code.

    Keyword arguments exist for the offline test suite only. The CLI passes none
    of them, so the deadline cannot be overridden from the command line.
    """
    stream = stdout if stdout is not None else sys.stdout
    if (json_flag is not True or not valid_id(channel) or not valid_id(message)
            or not isinstance(emoji, str) or emoji != ROCKET):
        _write(stream, error_line("usage"))
        return EXIT_USAGE

    deadline = _Deadline(deadline_seconds, clock, lambda line: _write(stream, line), exit_fn)
    deadline.start()
    try:
        token = _resolve_token(secret_getter)
        client = _Client(token, opener if opener is not None else build_opener(), deadline, sleep)
        reader_id = _check_identity(client)
        parent_id = _check_channel(client, channel)
        first = _read_message(client, channel, message, "message_not_found")
        reactors, pages = _read_reactors(client, channel, message)
        final = _read_message(client, channel, message, "message_deleted")
        if (final["id"] != first["id"] or final["edited_timestamp"] != first["edited_timestamp"]
                or final["content_sha256"] != first["content_sha256"]):
            raise ReadError("message_changed", EXIT_READ)
        line = json.dumps({
            "ok": True, "contract": CONTRACT, "reader_bot_id": reader_id, "guild_id": GUILD_ID,
            "channel_id": channel, "parent_id": parent_id, "message": final, "emoji": ROCKET,
            "reactors": reactors, "reactor_pages": pages,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        if len(line.encode("ascii")) > OUTPUT_LIMIT:
            raise ReadError("output_too_large", EXIT_READ)
        result, code = line, EXIT_OK
    except ReadError as exc:
        result, code = error_line(exc.code, exc.http_status), exc.exit_code
    except Exception:  # noqa: BLE001 — never echo exception text
        result, code = error_line("internal_error"), EXIT_READ
    if not deadline.finish():
        return EXIT_TIMEOUT
    _write(stream, result)
    return code


def setup_parser(parser) -> None:
    parser.add_argument("--channel", default=None, help="#approvals channel or thread id")
    parser.add_argument("--message", default=None, help="approval request message id")
    parser.add_argument("--emoji", default=None, help="must be the rocket emoji")
    parser.add_argument("--json", action="store_true", help="print one JSON line (required)")


def handle(args) -> int:
    return run(getattr(args, "channel", None), getattr(args, "message", None),
               getattr(args, "emoji", None), getattr(args, "json", False))
