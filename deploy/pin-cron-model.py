#!/usr/bin/env python3
"""Pin a Hermes cron job's provider/model safely (WD-281).

`hermes cron edit` exposes no provider/model flags, and unpinned jobs skip every
run after any global model drift ("Skipped to prevent unintended spend"). This
tool is the supported pin flow: it backs up the profile's cron/jobs.json, edits
the named job's `provider`/`model` (and refreshes the creation snapshots),
writes atomically, then RE-READS the file and prints the pinned values so the
operator sees verified state rather than trusting the write.

Usage:
  python deploy/pin-cron-model.py --profile-root <root> --job-name "Wolftac Factory Integrate Worker" \
      --provider openai-codex --model gpt-5.6-sol

Exit codes: 0 pinned and verified · 1 job not found / readback mismatch · 5 jobs.json unreadable.
"""
import argparse, json, os, sys, time

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile-root", default=os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "hermes", "profiles", "factory-integrate"))
    ap.add_argument("--job-name", required=True)
    ap.add_argument("--provider", required=True)
    ap.add_argument("--model", required=True)
    a = ap.parse_args()

    path = os.path.join(os.path.abspath(a.profile_root), "cron", "jobs.json")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except OSError as e:
        print(f"PIN FAIL (5): cannot read {path}: {e.strerror}"); sys.exit(5)
    except json.JSONDecodeError as e:
        print(f"PIN FAIL (5): {path} is not valid JSON: {e}"); sys.exit(5)

    jobs = data.get("jobs", [])
    matches = [j for j in jobs if j.get("name") == a.job_name]
    if len(matches) != 1:
        print(f"PIN FAIL (1): expected exactly one job named '{a.job_name}', found {len(matches)}")
        sys.exit(1)
    job = matches[0]
    job["provider"] = a.provider
    job["model"] = a.model
    job["provider_snapshot"] = a.provider
    job["model_snapshot"] = a.model

    backup = path + ".bak-pin-" + time.strftime("%Y%m%d-%H%M%S")
    with open(backup, "w", encoding="utf-8", newline="\n") as f:
        json.dump(json.load(open(path, encoding="utf-8")), f, indent=2)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)

    verify = json.load(open(path, encoding="utf-8"))
    vjob = [j for j in verify.get("jobs", []) if j.get("name") == a.job_name][0]
    ok = (vjob.get("provider") == a.provider and vjob.get("model") == a.model
          and vjob.get("provider_snapshot") == a.provider and vjob.get("model_snapshot") == a.model)
    if not ok:
        print("PIN FAIL (1): readback does not match requested pin — inspect jobs.json and the backup")
        sys.exit(1)
    print(f"PINNED (readback-verified): job '{a.job_name}' -> provider={vjob['provider']} "
          f"model={vjob['model']} (snapshots refreshed); backup: {os.path.basename(backup)}")
    sys.exit(0)

if __name__ == "__main__":
    main()
