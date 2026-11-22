#!/usr/bin/env python3
"""Read-only inventory and explicit scratch export of staged skill edits (WD-671)."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import time

PROFILES = "builder,reviewer,integrator"
COMPONENT = re.compile(r"[a-z0-9][a-z0-9-]{0,63}\Z")
RECORD_ID = re.compile(r"[0-9a-f]{8}\Z")
ACTIONS = {"create", "patch", "write_file", "remove_file", "delete"}


def default_home():
    return Path(os.environ.get("HERMES_HOME") or
                (Path(os.environ["LOCALAPPDATA"]) / "hermes" if os.environ.get("LOCALAPPDATA")
                 else Path.home() / ".hermes"))


def inventory(home, profiles, now=None):
    """Keep raw bytes private; only the explicit export command can release them."""
    now = time.time() if now is None else now
    records, unreadable = [], {}
    home = home.resolve()
    for profile in dict.fromkeys(profiles.split(",")):
        if not COMPONENT.fullmatch(profile):
            unreadable["invalid-profile"] = unreadable.get("invalid-profile", 0) + 1
            continue
        folder = home / "profiles" / profile / "pending" / "skills"
        count = 0
        try:
            # Do not follow a pending-directory link outside the requested home.
            if not folder.resolve().is_relative_to(home):
                raise ValueError("outside home")
            paths = sorted(folder.glob("*.json"))
            for path in paths:
                try:
                    if path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
                        raise ValueError("linked record")
                    raw = path.read_bytes()
                    record = json.loads(raw)
                    if (not isinstance(record, dict) or not isinstance(record.get("id"), str)
                            or not RECORD_ID.fullmatch(record["id"])):
                        raise ValueError("invalid record")
                    payload = record.get("payload")
                    name = payload.get("name") if isinstance(payload, dict) else None
                    name = name if isinstance(name, str) and COMPONENT.fullmatch(name) else "?"
                    action = record.get("action")
                    action = action if isinstance(action, str) and action in ACTIONS else "?"
                    created = record.get("created_at")
                    age = None
                    if isinstance(created, (int, float)) and not isinstance(created, bool) and math.isfinite(created):
                        age = max(0, math.floor((now - created) / 3600))
                    records.append(({"profile": profile, "id": record["id"], "action": action,
                                     "skill": name, "age_hours": age}, raw))
                except (OSError, ValueError, TypeError, OverflowError, RecursionError):
                    count += 1
        except (OSError, ValueError, RuntimeError):
            count += 1
        if count:
            unreadable[profile] = count
    return records, unreadable


def summary(records):
    counts = {}
    for item, _ in records:
        counts[item["profile"]] = counts.get(item["profile"], 0) + 1
    return "learn-pending: " + (", ".join(f"{p}={n}" for p, n in counts.items()) or "none")


def export(records, home, out):
    """Validate the entire destination set before writing anything; never overwrite."""
    out = out.resolve()
    if out.is_relative_to(home.resolve()):
        raise ValueError("inside Hermes home")
    # .git is a directory in a checkout and a file in a linked worktree. Inspect
    # ancestors even for a not-yet-created output; never execute repository hooks.
    if any((parent / ".git").exists() for parent in (out, *out.parents)):
        raise ValueError("inside git work tree")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise ValueError("destination is not empty")
    names = [f"{item['profile']}-{item['id']}.json" for item, _ in records]
    if len(names) != len(set(names)):
        raise ValueError("duplicate record ids")
    out.mkdir(parents=True, exist_ok=True)
    for name, (_, raw) in zip(names, records):
        with (out / name).open("xb") as stream:
            stream.write(raw)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("list", "export"):
        sub = commands.add_parser(command)
        sub.add_argument("--hermes-home", type=Path, default=default_home())
        sub.add_argument("--profiles", default=PROFILES)
        if command == "list":
            sub.add_argument("--json", action="store_true")
        else:
            sub.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    records, unreadable = inventory(args.hermes_home, args.profiles)
    if args.command == "export":
        try:
            export(records, args.hermes_home, args.out)
        except (OSError, ValueError, RuntimeError):
            print("learn-pending: export refused (unsafe, non-empty or unavailable destination)")
            return 2
        print(f"learn-pending: exported {len(records)} record(s)")
        for profile, count in unreadable.items():
            print(f"learn-pending: {profile} unreadable={count}")
    elif args.json:
        print(json.dumps({"records": [item for item, _ in records],
                          "unreadable": unreadable, "summary": summary(records)}))
    else:
        for item, _ in records:
            age = item["age_hours"] if item["age_hours"] is not None else "?"
            print(f"{item['profile']} {item['id']} {item['action']} {item['skill']} {age}h")
        for profile, count in unreadable.items():
            print(f"learn-pending: {profile} unreadable={count}")
        print(summary(records))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
