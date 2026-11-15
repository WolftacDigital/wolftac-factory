"""OPERATE Contract v1, pure stdlib and no installed-target I/O.

Validation returns {status: ok|malformed, error: field|null}. parse_operate
adds block (or None), with missing/none statuses when appropriate. Parsing is
structural; validate_block/build_record additionally bind IDs to the supplied
map. No implicit map discovery occurs in a deployed helper.

build_record returns {status, error, record, text}; Git errors return status
error with a sanitized field, never command output. parse_record returns the
record itself on success, a validation result otherwise. serialize_record
uses schema order independent of the input dictionary's insertion order.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import PurePosixPath
import re
import subprocess

CONTRACT_VERSION = 1
OPERATE_EPOCH = "2026-10-30T23:30:00Z"
PROSE_MARKER = re.compile(
    r"(?im)^[ \t]{0,3}(?:#{1,6}[ \t]+|[-*+][ \t]+)?(?:\*\*|__)?[ \t]*"
    r"(?:post-merge operator step|install[ \t]*/[ \t]*wiring)\b"
)
ID = re.compile(r"[a-z0-9-]{1,64}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
UUID = re.compile(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")
OWNER_PROVENANCE = {"spec", "policy-default", "operator"}
VALUE_PROVENANCE = {"spec", "operator", "none"}
_ABSENT = object()


def _result(error=None, status=None):
    return {"status": status or ("malformed" if error else "ok"), "error": error}


def _keys(obj, required, optional=()):
    return (type(obj) is dict and set(required) <= obj.keys()
            and obj.keys() <= set(required) | set(optional))


def _string(value, limit):
    return isinstance(value, str) and 1 <= len(value) <= limit and value.isprintable()


def _hex(value, size):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-fA-F]{%d}" % size, value) is not None


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z", value
    ):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _smoke(value):
    return (type(value) is list and 1 <= len(value) <= 10
            and all(_string(item, 300) for item in value))


def _map_error(target_map):
    if not _keys(target_map, ("version", "targets")):
        return "target_map"
    if type(target_map["version"]) is not int or target_map["version"] != 1:
        return "target_map.version"
    if type(target_map["targets"]) is not dict:
        return "target_map.targets"
    for key, item in target_map["targets"].items():
        if not isinstance(key, str) or not ID.fullmatch(key) or type(item) is not dict:
            return "target_map.targets"
        kind = item.get("kind")
        if kind not in ("file", "git-head") or not _keys(
            item, ("kind", "installed", "repo") + (("source",) if kind == "file" else ())
        ):
            return "target_map.targets"
        if not _string(item["installed"], 4096) or not isinstance(item["repo"], str) or not REPO.fullmatch(item["repo"]):
            return "target_map.targets"
        if kind == "file":
            source = item["source"]
            if (not _string(source, 4096) or "\\" in source or ":" in source
                    or PurePosixPath(source).is_absolute()
                    or any(part in ("", ".", "..") for part in source.split("/"))):
                return "target_map.targets.source"
    return None


def _block_error(obj, target_map=None):
    if not _keys(obj, ("version", "targets"), ("owner", "checkpoint_days", "approval", "smoke")):
        return "block"
    if type(obj["version"]) is not int or obj["version"] != CONTRACT_VERSION:
        return "version"
    if "owner" in obj and not _string(obj["owner"], 64):
        return "owner"
    if "checkpoint_days" in obj and (type(obj["checkpoint_days"]) is not int or not 1 <= obj["checkpoint_days"] <= 14):
        return "checkpoint_days"
    if "approval" in obj and not _string(obj["approval"], 300):
        return "approval"
    if "smoke" in obj and not _smoke(obj["smoke"]):
        return "smoke"
    targets = obj["targets"]
    if type(targets) is not list or not 1 <= len(targets) <= 10:
        return "targets"
    seen = set()
    for target in targets:
        if not _keys(target, ("id",)):
            return "targets"
        key = target["id"]
        if not isinstance(key, str) or not ID.fullmatch(key) or key in seen:
            return "targets"
        if target_map is not None and key not in target_map["targets"]:
            return "targets"
        seen.add(key)
    return None


def validate_block(obj, target_map):
    """Validate JSON-shaped input without I/O or exceptions."""
    try:
        return _result(_map_error(target_map) or _block_error(obj, target_map))
    except Exception:
        return _result("block")


def _json(text):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out:
                raise ValueError("duplicate key")
            out[key] = value
        return out

    def constant(_):
        raise ValueError("non-finite number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def _fenced(text, tag):
    """Recognize fenced blocks, excluding examples nested in longer fences."""
    if not isinstance(text, str):
        return None, "text"
    blocks, active, lines = [], None, []
    for line in text.splitlines():
        if active is None:
            match = re.fullmatch(r" {0,3}(`{3,}|~{3,})([^\r\n]*)", line)
            if match:
                fence, info = match.groups()
                active = (fence[0], len(fence), info.strip() == tag)
                lines = []
        elif re.fullmatch(r" {0,3}" + re.escape(active[0]) + "{" + str(active[1]) + r",}[ \t]*", line):
            if active[2]:
                blocks.append("\n".join(lines))
            active = None
        else:
            lines.append(line)
    if active and active[2]:
        return None, "blocks"
    if len(blocks) > 1:
        return None, "blocks"
    if not blocks:
        return _ABSENT, None
    try:
        return _json(blocks[0]), None
    except (ValueError, RecursionError):
        return None, "json"


def parse_operate(description):
    try:
        obj, error = _fenced(description, "operate")
        if error:
            return {**_result(error), "block": None}
        if obj is _ABSENT:
            status = "missing" if PROSE_MARKER.search(description) else "none"
            return {**_result(status=status), "block": None}
        error = _block_error(obj)
        return {**_result(error), "block": obj if not error else None}
    except Exception:
        return {**_result("block"), "block": None}


def _source_error(source, dated=True):
    fields = ("issue_id", "identifier", "repo", "merge_sha") + (("merged_at",) if dated else ())
    if not _keys(source, fields):
        return "source"
    if not isinstance(source["issue_id"], str) or not UUID.fullmatch(source["issue_id"]):
        return "source.issue_id"
    if not isinstance(source["identifier"], str) or not re.fullmatch(r"WD-[1-9][0-9]*", source["identifier"]):
        return "source.identifier"
    if not isinstance(source["repo"], str) or not REPO.fullmatch(source["repo"]):
        return "source.repo"
    if not _hex(source["merge_sha"], 40):
        return "source.merge_sha"
    if dated and _date(source["merged_at"]) is None:
        return "source.merged_at"
    return None


def _record_error(obj, target_map=None):
    if not _keys(obj, ("version", "source", "metadata", "metadata_error", "owner", "checkpoint_at", "checkpoint_provenance", "approval", "smoke", "targets")):
        return "record"
    if type(obj["version"]) is not int or obj["version"] != CONTRACT_VERSION:
        return "version"
    error = _source_error(obj["source"])
    if error:
        return error
    metadata = obj["metadata"]
    if metadata not in ("ok", "missing", "malformed"):
        return "metadata"
    if ((metadata == "malformed" and not _string(obj["metadata_error"], 128))
            or (metadata != "malformed" and obj["metadata_error"] is not None)):
        return "metadata_error"
    owner = obj["owner"]
    if not _keys(owner, ("value", "provenance")) or not _string(owner["value"], 64):
        return "owner"
    if owner["provenance"] not in OWNER_PROVENANCE:
        return "owner.provenance"
    if owner["provenance"] == "policy-default" and owner["value"] != "Jonathan":
        return "owner.value"
    checkpoint = _date(obj["checkpoint_at"])
    if checkpoint is None:
        return "checkpoint_at"
    provenance = obj["checkpoint_provenance"]
    if provenance not in OWNER_PROVENANCE:
        return "checkpoint_provenance"
    merged_at = _date(obj["source"]["merged_at"])
    if merged_at is None:
        return "source.merged_at"
    delta = checkpoint - merged_at
    if provenance == "policy-default" and delta != timedelta(days=2):
        return "checkpoint_at"
    if provenance == "spec" and (delta.seconds or delta.microseconds or not 1 <= delta.days <= 14):
        return "checkpoint_at"
    for field in ("approval", "smoke"):
        item = obj[field]
        if not _keys(item, ("value", "provenance")):
            return field
        if item["provenance"] not in VALUE_PROVENANCE:
            return field + ".provenance"
        if item["provenance"] == "none":
            if item["value"] != (None if field == "approval" else []):
                return field
        elif not (_string(item["value"], 300) if field == "approval" else _smoke(item["value"])):
            return field
    targets = obj["targets"]
    if type(targets) is not list or (metadata == "ok" and not 1 <= len(targets) <= 10) or (metadata != "ok" and targets):
        return "targets"
    seen = set()
    for item in targets:
        if not _keys(item, ("id", "kind", "expected", "previous")):
            return "targets"
        key, kind = item["id"], item["kind"]
        if not isinstance(key, str) or not ID.fullmatch(key) or key in seen or kind not in ("file", "git-head"):
            return "targets"
        seen.add(key)
        if target_map is not None:
            mapped = target_map["targets"].get(key)
            if not mapped or mapped["kind"] != kind or mapped["repo"] != obj["source"]["repo"]:
                return "targets"
        size = 64 if kind == "file" else 40
        if not _hex(item["expected"], size):
            return "targets.expected"
        if kind == "git-head" and item["expected"] != obj["source"]["merge_sha"]:
            return "targets.expected"
        previous = item["previous"]
        if not _keys(previous, ("value", "provenance")):
            return "targets.previous"
        if previous["provenance"] == "none":
            if previous["value"] is not None:
                return "targets.previous"
        elif previous["provenance"] != "merge-parent" or not _hex(previous["value"], size):
            return "targets.previous"
    return None


def validate_record(obj, target_map):
    try:
        return _result(_map_error(target_map) or _record_error(obj, target_map))
    except Exception:
        return _result("record")


def serialize_record(obj):
    """Return canonical fenced text, or a sanitized validation result."""
    try:
        error = _record_error(obj)
        if error:
            return _result(error)
        ordered = {key: obj[key] for key in ("version", "source", "metadata", "metadata_error", "owner", "checkpoint_at", "checkpoint_provenance", "approval", "smoke", "targets")}
        ordered["source"] = {key: obj["source"][key] for key in ("issue_id", "identifier", "repo", "merge_sha", "merged_at")}
        for field in ("owner", "approval", "smoke"):
            ordered[field] = {key: obj[field][key] for key in ("value", "provenance")}
        ordered["targets"] = [dict(id=t["id"], kind=t["kind"], expected=t["expected"], previous={key: t["previous"][key] for key in ("value", "provenance")}) for t in obj["targets"]]
        return "```operate-record\n" + json.dumps(ordered, ensure_ascii=True, separators=(",", ":"), allow_nan=False) + "\n```\n"
    except Exception:
        return _result("record")


def parse_record(text):
    try:
        obj, error = _fenced(text, "operate-record")
        error = error or _record_error(obj)
        return _result(error) if error else obj
    except Exception:
        return _result("record")


def build_record(source, parsed, target_map, repo_path):
    """Build solely from explicit data and committed Git objects. No working-file reads.

    source supplies issue_id, identifier, repo and merge_sha. merged_at is always
    derived from the commit, not supplied by the caller. A missing parent/blob
    means previous:none; an unreadable expected blob is a build error.
    """
    def failure(field):
        return {**_result(field, "error"), "record": None, "text": None}

    try:
        error = _source_error(source, dated=False) or _map_error(target_map)
        if error:
            return failure(error)
        if not _keys(parsed, ("status", "error", "block")):
            return failure("parsed")
        status = parsed["status"]
        if status not in ("ok", "missing", "malformed"):
            return failure("parsed.status")
        block = parsed["block"] if status == "ok" else {}
        error = parsed["error"] if status == "malformed" else None
        if status == "malformed" and not _string(error, 128):
            return failure("parsed.error")
        if status == "ok":
            error = validate_block(block, target_map)["error"]
            if error:
                status, block = "malformed", {}
        for target in block.get("targets", []):
            if target_map["targets"][target["id"]]["repo"] != source["repo"]:
                return failure("source.repo")

        def git(*args):
            result = subprocess.run(
                ["git", "--no-optional-locks", "-C", str(repo_path), *args],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
            )
            if result.returncode:
                raise ValueError("git")
            return result.stdout

        sha = source["merge_sha"]
        # Full object identity and type are mandatory; never accept a ref or tag.
        if git("cat-file", "-t", sha).strip() != b"commit":
            return failure("source.merge_sha")
        committed_at = git("show", "-s", "--format=%cI", sha).decode("ascii").strip()
        merged_at = datetime.fromisoformat(committed_at)
        if merged_at.tzinfo is None:
            return failure("git.merged_at")
        parents = git("show", "-s", "--format=%P", sha).decode("ascii").strip().split()
        parent = parents[0] if parents else None
        targets = []
        for target in block.get("targets", []):
            key = target["id"]
            entry = target_map["targets"][key]
            kind = entry["kind"]
            if kind == "file":
                path = entry["source"]
                if git("cat-file", "-t", sha + ":" + path).strip() != b"blob":
                    return failure("git.source")
                expected = hashlib.sha256(git("show", sha + ":" + path)).hexdigest()
                previous = None
                if parent:
                    # ls-tree distinguishes an absent parent path from Git failure.
                    exists = git("ls-tree", parent, "--", path)
                    if exists:
                        if git("cat-file", "-t", parent + ":" + path).strip() != b"blob":
                            return failure("git.previous")
                        previous = hashlib.sha256(git("show", parent + ":" + path)).hexdigest()
            else:
                expected, previous = sha, parent
            targets.append({"id": key, "kind": kind, "expected": expected,
                            "previous": {"value": previous, "provenance": "merge-parent" if previous else "none"}})
        record = {
            "version": CONTRACT_VERSION,
            "source": {**source, "merged_at": _iso(merged_at)},
            "metadata": status, "metadata_error": error,
            "owner": {"value": block.get("owner", "Jonathan"), "provenance": "spec" if "owner" in block else "policy-default"},
            "checkpoint_at": _iso(merged_at + timedelta(days=block.get("checkpoint_days", 2))),
            "checkpoint_provenance": "spec" if "checkpoint_days" in block else "policy-default",
            "approval": {"value": block.get("approval"), "provenance": "spec" if "approval" in block else "none"},
            "smoke": {"value": block.get("smoke", []), "provenance": "spec" if "smoke" in block else "none"},
            "targets": targets,
        }
        error = validate_record(record, target_map)["error"]
        if error:
            return failure(error)
        text = serialize_record(record)
        return {**_result(), "record": record, "text": text}
    except Exception:
        return failure("git")
