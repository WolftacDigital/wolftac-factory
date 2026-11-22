#!/usr/bin/env python3
"""WD-586 scratch-only CLI fixture. Never discovers a real Hermes home."""
import argparse
import json
import os
from pathlib import Path
import re
import sys


def main():
    # Required explicit environment: no live/default fallback.
    home = Path(os.environ["FAKE_HERMES_HOME"])
    log = Path(os.environ["FAKE_HERMES_LOG"])
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", required=True)
    parser.add_argument("command", choices=["cron"])
    subs = parser.add_subparsers(dest="action", required=True)
    for action in ("create", "edit"):
        sub = subs.add_parser(action)
        if action == "create":
            sub.add_argument("schedule")
            sub.add_argument("prompt")
        else:
            sub.add_argument("job_id")
            sub.add_argument("--schedule")
            sub.add_argument("--prompt")
        for field in ("name", "deliver", "failure-deliver", "workdir", "model", "provider", "reasoning-effort"):
            sub.add_argument("--" + field)
        sub.add_argument("--skill", action="append")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", args.p):
        parser.error("invalid profile")
    with log.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(sys.argv[1:], ensure_ascii=False) + "\n")
    mode = os.environ.get("FAKE_HERMES_MODE", "")
    if mode == "fail":
        print("SENTINEL-PROMPT-LINE", file=sys.stderr)
        return 1
    if mode == "noop":
        return 0
    path = home / "profiles" / args.p / "cron/jobs.json"
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    if args.action == "create":
        job: dict = dict(id="resid0000001", enabled=True, state="scheduled", no_agent=False,
                   script=None, base_url=None, monitor_script=None, monitor_url=None,
                   context_from=None, enabled_toolsets=None, repeat={"times": None})
        document["jobs"].append(job)
    else:
        job = next(job for job in document["jobs"] if job["id"] == args.job_id)
    for field in ("name", "prompt", "deliver", "failure_deliver", "workdir", "model", "provider", "reasoning_effort"):
        value = getattr(args, field)
        if value is not None:
            job[field] = value or None
    if args.skill is not None:
        job["skills"] = args.skill
        job["skill"] = args.skill[0]
    if args.schedule is not None:
        match = re.fullmatch(r"every (\d+)m", args.schedule)
        if not match:
            parser.error("invalid schedule")
        job["schedule"] = dict(kind="interval", minutes=int(match[1]), display=args.schedule)
    if mode == "mangle":
        job["prompt"] = job["prompt"].replace("—", "â€”")
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
