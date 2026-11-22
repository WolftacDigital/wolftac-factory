#!/usr/bin/env python3
"""Plan or reconcile the residual worker through the operator's Hermes CLI.

Default is read-only. Build/review must supply a scratch home and fake command.
Only constants and pure lane helpers are used from the adjacent Gatekeeper.
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess

_spec = importlib.util.spec_from_file_location(
    "residual_job_gatekeeper", Path(__file__).with_name("factory-gatekeeper.py"))
assert _spec is not None and _spec.loader is not None
gatekeeper = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gatekeeper)

REQUIRED = ("model", "provider", "workdir", "deliver")
OPTIONAL = ("reasoning_effort", "failure_deliver")
FORBIDDEN = ("script", "base_url", "monitor_script", "monitor_url", "context_from", "enabled_toolsets")
FIELDS = ("prompt", "schedule", "skills", *REQUIRED, *OPTIONAL)


def read_jobs(path: Path) -> list[dict]:
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    jobs = document.get("jobs") if isinstance(document, dict) else None
    if not isinstance(jobs, list) or any(not isinstance(job, dict) for job in jobs):
        raise ValueError("invalid jobs document")
    return jobs


def skills(job: dict) -> list[str]:
    value = job.get("skills")
    if value is None:
        value = [job["skill"]] if job.get("skill") else []
    return value


def optional(job: dict, field: str):
    return job.get(field) or None


def differences(job: dict, desired: dict) -> list[str]:
    changed = []
    for field in FIELDS:
        actual, expected = job.get(field), desired[field]
        if field == "schedule":
            actual = actual if isinstance(actual, dict) else {}
            actual = (actual.get("kind"), actual.get("minutes"))
            expected = (expected["kind"], expected["minutes"])
        elif field == "skills":
            actual = skills(job)
        elif field in OPTIONAL:
            actual = optional(job, field)
        if actual != expected:
            changed.append(field)
    return changed


def template_problem(template: dict, needle: str) -> str | None:
    name = template.get("name")
    if not isinstance(name, str) or not name.startswith("Wolftac Factory Build Worker"):
        return "name"
    if template.get("no_agent") not in (None, False):
        return "no_agent"
    for field in FORBIDDEN:
        if template.get(field) is not None:
            return field
    repeat = template.get("repeat")
    if repeat is not None and (not isinstance(repeat, dict) or repeat.get("times") is not None):
        return "repeat.times"
    for field in REQUIRED:
        if not isinstance(template.get(field), str) or not template[field].strip():
            return field
    for field in OPTIONAL:
        if template.get(field) is not None and not isinstance(template[field], str):
            return field
    value = skills(template)
    if not isinstance(value, list) or not value or any(not isinstance(s, str) or not s.strip() for s in value):
        return "skills"
    prompt = template.get("prompt")
    if not isinstance(prompt, str) or prompt.count(needle) != 1:
        return "lane filter count"
    return None


def flags(fields: Sequence[str], desired: dict) -> list[str]:
    result = []
    for field in fields:
        if field == "skills":
            for skill in desired[field]:
                result.extend(["--skill", skill])
        else:
            value = "every 120m" if field == "schedule" else desired[field] or ""
            result.extend(["--" + field.replace("_", "-"), value])
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--hermes-home", type=Path)
    parser.add_argument("--hermes-command", help="JSON argv list (test seam)")
    args = parser.parse_args(argv)
    home = args.hermes_home if args.hermes_home is not None else Path(gatekeeper.hermes_home())
    executable = home / "bin/hermes.exe"
    command = [str(executable)] if executable.is_file() else ["hermes"]
    if args.hermes_command is not None:
        try:
            command = json.loads(args.hermes_command)
            if not isinstance(command, list) or not command or any(not isinstance(s, str) or not s for s in command):
                raise ValueError
        except (ValueError, TypeError):
            parser.error("--hermes-command must be a nonempty JSON argv list")

    _, template_id, template_profile = gatekeeper.DEFAULT_JOB_MAP["build"]
    profile, name = gatekeeper.RESIDUAL_PROFILE, gatekeeper.RESIDUAL_JOB_NAME
    identity = f"{profile}/{template_id}"
    if template_profile != profile:
        print(f"REFUSE  template {identity}: profile mismatch")
        return 1
    path = home / "profiles" / profile / "cron/jobs.json"
    try:
        jobs = read_jobs(path)
    except (OSError, ValueError):
        print(f"MISSING {profile} jobs.json: {path} unreadable")
        return 1
    templates = [job for job in jobs if job.get("id") == template_id]
    if not templates:
        print(f"MISSING template {identity}: not found in {path}")
        return 1
    if len(templates) != 1:
        print(f"REFUSE  template {identity}: duplicate id")
        return 1
    template = templates[0]
    needle = f'Lane filter: {gatekeeper.LANE_FILTERS["build"]}.'
    problem = template_problem(template, needle)
    if problem:
        print(f"REFUSE  template {identity}: {problem}")
        return 1
    try:
        argument = gatekeeper.residual_argument()
        excludes = gatekeeper.residual_excludes()
        if gatekeeper.parse_residual_argument(argument) != excludes:
            raise ValueError
    except ValueError:
        print(f"REFUSE  template {identity}: invalid exclude list")
        return 1
    desired = {field: template[field] for field in REQUIRED}
    desired.update({field: optional(template, field) for field in OPTIONAL})
    desired.update(name=name, skills=skills(template), schedule={"kind": "interval", "minutes": 120},
                   prompt=template["prompt"].replace(needle, f"Lane filter: `{argument}`."))
    existing = [job for job in jobs if job.get("name") == name]
    if len(existing) > 1:
        print(f"REFUSE  residual: {len(existing)} jobs named '{name}'")
        return 1
    action = "edit" if existing else "create"
    changed = differences(existing[0], desired) if existing else list(FIELDS)
    job_id = existing[0].get("id") if existing else None
    if existing and (not isinstance(job_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", job_id)):
        print("REFUSE  residual: job id missing or invalid")
        return 1
    if not changed:
        print(f"OK      {profile}/{job_id} '{name}': matches template, lane '{argument}'")
        return 0
    if not args.apply:
        if existing:
            print(f"PLAN    edit {profile}/{job_id} '{name}': {', '.join(changed)}")
        else:
            print(f"PLAN    create {profile} '{name}': every 120m, lane '{argument}'")
        return 0

    def failed(reason: str) -> int:
        print(f"FAILED  {action} {profile}: {reason}")
        return 1

    # Exclusive creation refuses a timestamp collision rather than replacing proof.
    backup = path.with_name(path.name + datetime.now().strftime(".bak-%Y%m%d-%H%M%S-pre-residual-lane"))
    try:
        with path.open("rb") as source, backup.open("xb") as target:
            shutil.copyfileobj(source, target)
    except OSError:
        return failed("backup unavailable")
    cli = ["-p", profile, "cron", action]
    if existing:
        cli += [job_id] + flags(changed, desired)
    else:
        cli += ["--name", name] + flags(["skills", *REQUIRED], desired)
        cli += flags([field for field in OPTIONAL if desired[field] is not None], desired)
        cli += ["every 120m", desired["prompt"]]
    try:
        result = subprocess.run(command + cli, timeout=120, capture_output=True, shell=False)
    except subprocess.TimeoutExpired:
        return failed("hermes timeout")
    except OSError:
        return failed("hermes unavailable")
    if result.returncode:
        return failed(f"hermes exit {result.returncode}")
    try:
        matches = [job for job in read_jobs(path) if job.get("name") == name]
    except (OSError, ValueError):
        return failed("read-back jobs.json")
    if len(matches) != 1:
        return failed("read-back name")
    actual = matches[0]
    changed = differences(actual, desired)
    prompt = actual.get("prompt")
    lane = re.findall(r"Lane filter: `([^`\r\n]+)`\.", prompt if isinstance(prompt, str) else "")
    try:
        valid_lane = len(lane) == 1 and gatekeeper.parse_residual_argument(lane[0]) == excludes
    except ValueError:
        valid_lane = False
    if not valid_lane and "prompt" not in changed:
        changed.insert(0, "prompt")
    actual_id = actual.get("id")
    if (not isinstance(actual_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", actual_id)
            or existing and actual_id != job_id):
        changed.append("id")
    if changed:
        return failed("read-back " + ", ".join(changed))
    print(f"APPLIED {profile}/{actual_id} '{name}' (verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
