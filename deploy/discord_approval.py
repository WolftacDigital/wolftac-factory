#!/usr/bin/env python3
"""Discord approval verifier (WD-546). Stdlib only; handles no credential.

    python deploy/discord_approval.py verify --policy <path> --channel-id <id>
        --message-id <id> --issue WD-N --repo <owner/name> --pr <n|none> --sha <40hex>
        --proof-pack <Linear comment URL>

Runs R1 (``hermes -p integrator wolftac-approval-read``) as a subprocess with every
DISCORD_* environment name removed, then judges R1's FINAL message read and its
complete reactor list. Prints one JSON line ``{verdict, reason, issue, message_id}``.

Exit 0 only on APPROVED. Anything else is not approval:

* ``NOT_APPROVED approver-absent`` (exit 1): the request is genuine and current,
  and the approver has not reacted.
* ``REJECTED <reason>`` (exit 1): the request, the reader or the arguments do not
  match, so integrate must not merge.
* ``REJECTED bad-arguments`` / ``REJECTED policy-invalid`` (exit 2).

There is deliberately no token, env-path or reader-override argument.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ID_RE = re.compile(r"[0-9]{17,20}", re.ASCII)
SHA_RE = re.compile(r"[0-9a-f]{40}", re.ASCII)
ISSUE_RE = re.compile(r"WD-[0-9]{1,6}", re.ASCII)
REPO_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}", re.ASCII)
PR_RE = re.compile(r"[1-9][0-9]{0,6}", re.ASCII)
# The exact Linear comment URL of the proof pack this request belongs to.
PACK_RE = re.compile(r"https://linear\.app/[A-Za-z0-9._~%/#?=&-]{1,400}", re.ASCII)
ROCKET = "\U0001F680"
POLICY_KEYS = frozenset({
    "discord_guild_id", "discord_channel_id", "approver_discord_user_id",
    "pack_author_discord_user_ids", "reader_bot_discord_user_id", "approval_emoji",
})
HEADER = "FACTORY MERGE APPROVAL REQUEST"
# Requests posted before the Discord gate existed say no merge will run. They
# can never be approved; integrate posts a fresh request instead.
PRE_CUTOVER_MARKERS = ("REQUEST-ONLY RECOVERY", "No merge will run")
READER_TIMEOUT_SECONDS = 75
READER_SUBCOMMAND = "wolftac-approval-read"
READER_PROFILE = "integrator"


class PolicyError(Exception):
    pass


def hermes_home() -> str:
    return os.environ.get("HERMES_HOME") or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "hermes")


def hermes_cli() -> list[str]:
    for candidate in (os.path.join(hermes_home(), "bin", "hermes.exe"),
                      os.path.join(hermes_home(), "bin", "hermes")):
        if os.path.exists(candidate):
            return [candidate]
    return ["hermes"]


def reader_argv(channel_id: str, message_id: str, emoji: str) -> list[str]:
    return hermes_cli() + ["-p", READER_PROFILE, READER_SUBCOMMAND, "--channel", channel_id,
                           "--message", message_id, "--emoji", emoji, "--json"]


def reader_env() -> dict:
    """The parent environment minus every DISCORD_* name. Values are never read."""
    return {name: value for name, value in os.environ.items() if not name.upper().startswith("DISCORD_")}


def run_reader(argv: list[str], env: dict, timeout: int = READER_TIMEOUT_SECONDS):
    """Run R1. Returns (exit code, stdout text); (None, "") on timeout or launch failure."""
    try:
        proc = subprocess.run(argv, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,  # noqa: S603
                              stderr=subprocess.DEVNULL, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except OSError:
        return None, "launch-failed"
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def parse_reader_output(text: str):
    """The single R1 contract line in stdout, or None. Other output is ignored."""
    found = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            document = json.loads(line)
        except ValueError:
            continue
        if isinstance(document, dict) and document.get("contract") == 1 and "ok" in document:
            found.append(document)
    return found[0] if len(found) == 1 else None


def read_approval(channel_id: str, message_id: str, emoji: str):
    """Return R1's success document, or raise ReaderFailure(code)."""
    code, text = run_reader(reader_argv(channel_id, message_id, emoji), reader_env())
    if code is None:
        raise ReaderFailure(text)
    document = parse_reader_output(text)
    if document is None:
        raise ReaderFailure("malformed-output" if code == 0 else f"exit-{code}")
    if document.get("ok") is not True:
        error = document.get("error")
        raise ReaderFailure(error if isinstance(error, str) and re.fullmatch(r"[a-z_]{1,40}", error)
                            else "reader-error")
    if code != 0:
        raise ReaderFailure(f"exit-{code}")
    return document


class ReaderFailure(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def load_policy(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            policy = json.load(fh)
    except (OSError, ValueError):
        raise PolicyError("unreadable") from None
    if not isinstance(policy, dict) or set(policy) != POLICY_KEYS:
        raise PolicyError("keys")
    ids = [policy["discord_guild_id"], policy["discord_channel_id"], policy["approver_discord_user_id"],
           policy["reader_bot_discord_user_id"]]
    authors = policy["pack_author_discord_user_ids"]
    if (not all(isinstance(v, str) and ID_RE.fullmatch(v) for v in ids)
            or not isinstance(authors, list) or not authors
            or not all(isinstance(v, str) and ID_RE.fullmatch(v) for v in authors)
            or policy["approval_emoji"] != ROCKET):
        raise PolicyError("values")
    return policy


def parse_body(content: str):
    """Split the request into its fixed lines; None when the shape is wrong."""
    lines = [line.rstrip() for line in content.replace("\r\n", "\n").split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    if len(lines) != 7 or lines[0] != HEADER:
        return None
    fields = {}
    for line, key in zip(lines[1:6], ("Issue", "Repo", "PR", "Head", "Proof pack")):
        prefix = key + ": "
        if not line.startswith(prefix):
            return None
        fields[key] = line[len(prefix):].strip()
    fields["mention"] = lines[6]
    return fields


def pr_matches(value: str, repo: str, pr: str) -> bool:
    if pr == "none":
        return value == "none (branchless)"
    parts = value.split(" ")
    if parts[0] != "#" + pr or len(parts) > 2:
        return False
    return len(parts) == 1 or parts[1] == f"https://github.com/{repo}/pull/{pr}"


def judge(document: dict, policy: dict, args) -> tuple[str, str]:
    """Return (verdict, reason) for one R1 success document."""
    message = document.get("message")
    reactors = document.get("reactors")
    if not isinstance(message, dict) or not isinstance(reactors, list):
        return "REJECTED", "malformed-reader-output"
    author = message.get("author")
    if document.get("reader_bot_id") != policy["reader_bot_discord_user_id"]:
        return "REJECTED", "wrong-reader"
    if document.get("guild_id") != policy["discord_guild_id"]:
        return "REJECTED", "channel-not-allowed"
    if document.get("channel_id") != args.channel_id or message.get("id") != args.message_id:
        return "REJECTED", "message-mismatch"
    if policy["discord_channel_id"] not in (document.get("channel_id"), document.get("parent_id")):
        return "REJECTED", "channel-not-allowed"
    if document.get("emoji") != policy["approval_emoji"]:
        return "REJECTED", "wrong-emoji"
    if (not isinstance(author, dict) or author.get("id") not in policy["pack_author_discord_user_ids"]
            or author.get("bot") is not True):
        return "REJECTED", "author-not-allowed"
    if message.get("edited_timestamp") is not None:
        return "REJECTED", "message-edited"
    content = message.get("content")
    if not isinstance(content, str):
        return "REJECTED", "malformed-reader-output"
    if any(marker in content for marker in PRE_CUTOVER_MARKERS):
        return "REJECTED", "pre-cutover-request"
    fields = parse_body(content)
    if fields is None:
        return "REJECTED", "malformed-body"
    if fields["Issue"] != args.issue:
        return "REJECTED", "issue-mismatch"
    if fields["Repo"] != args.repo:
        return "REJECTED", "repo-mismatch"
    if not pr_matches(fields["PR"], args.repo, args.pr):
        return "REJECTED", "pr-mismatch"
    if fields["Head"] != args.sha:
        return "REJECTED", "sha-mismatch"
    if fields["Proof pack"] != args.proof_pack:
        return "REJECTED", "proof-pack-mismatch"
    expected_mention = (f"<@{policy['approver_discord_user_id']}> react {policy['approval_emoji']} to approve "
                        "this exact commit. A new commit cancels this approval.")
    if fields["mention"] != expected_mention:
        return "REJECTED", "malformed-body"
    for row in reactors:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("bot"), bool):
            return "REJECTED", "malformed-reader-output"
    if any(row["id"] == policy["approver_discord_user_id"] and row["bot"] is False for row in reactors):
        return "APPROVED", "approver-reacted"
    return "NOT_APPROVED", "approver-absent"


def emit(verdict: str, reason: str, issue, message_id) -> None:
    print(json.dumps({"verdict": verdict, "reason": reason, "issue": issue, "message_id": message_id},
                     sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def valid_args(args) -> bool:
    return bool(ID_RE.fullmatch(args.channel_id or "") and ID_RE.fullmatch(args.message_id or "")
                and ISSUE_RE.fullmatch(args.issue or "") and REPO_RE.fullmatch(args.repo or "")
                and (args.pr == "none" or PR_RE.fullmatch(args.pr or "")) and SHA_RE.fullmatch(args.sha or "")
                and PACK_RE.fullmatch(args.proof_pack or ""))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Verify Jonathan's Discord rocket on one exact merge request.")
    sub = parser.add_subparsers(dest="command", required=True)
    verify = sub.add_parser("verify")
    for name in ("--policy", "--channel-id", "--message-id", "--issue", "--repo", "--pr", "--sha",
                 "--proof-pack"):
        verify.add_argument(name, required=True)
    args = parser.parse_args(argv)
    issue = args.issue if ISSUE_RE.fullmatch(args.issue or "") else None
    message_id = args.message_id if ID_RE.fullmatch(args.message_id or "") else None
    if not valid_args(args):
        emit("REJECTED", "bad-arguments", issue, message_id)
        return 2
    try:
        policy = load_policy(args.policy)
    except PolicyError:
        emit("REJECTED", "policy-invalid", issue, message_id)
        return 2
    try:
        document = read_approval(args.channel_id, args.message_id, policy["approval_emoji"])
    except ReaderFailure as exc:
        emit("REJECTED", f"api-error({exc.code})", issue, message_id)
        return 1
    verdict, reason = judge(document, policy, args)
    emit(verdict, reason, issue, message_id)
    return 0 if verdict == "APPROVED" else 1


if __name__ == "__main__":
    sys.exit(main())
