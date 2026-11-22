"""WD-586: execute only against a synthetic home and a fake Hermes command."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "deploy/residual-build-job.py"
FAKE = ROOT / "deploy/tests/fixtures/fake_hermes_cron.py"
spec = importlib.util.spec_from_file_location("wd586", SCRIPT)
assert spec is not None and spec.loader is not None
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
gk = module.gatekeeper
LANE = "residual: agentic; Wolftac CRM v2; OMAS ATLAS; manual-lane"
NAME = "Wolftac Factory Build Worker - residual (Mason)"


class ResidualBuildJobTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="wd586-", dir=os.getenv("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.jobs = self.home / "profiles/builder/cron/jobs.json"
        self.jobs.parent.mkdir(parents=True)
        self.log = self.home / "argv.jsonl"
        self.template = dict(
            id=gk.DEFAULT_JOB_MAP["build"][1], name="Wolftac Factory Build Worker - agentic (Mason)",
            model="fake-model", provider="fake-provider", workdir=str(self.home / 'work dir'),
            deliver="local", skills=["wolftac-build"], skill="wolftac-build", no_agent=False,
            script=None, base_url=None, monitor_script=None, monitor_url=None,
            context_from=None, enabled_toolsets=None, repeat={"times": None},
            schedule={"kind": "interval", "minutes": 120},
            prompt='First — line\n- Lane filter: agentic. Only touch Linear issues labeled "Factory Queue" matching the lane.\nSENTINEL-PROMPT-LINE\n')
        self.other = dict(id="other", name="Unrelated fixture", prompt="leave untouched", schedule={"kind": "cron"})
        self.document: dict = dict(jobs=[self.template, self.other], updated_at="fixture")
        self.save()
        (self.home / "unrelated.txt").write_text("unchanged", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items()
                    if k in ("PATH", "SYSTEMROOT", "SystemRoot", "WINDIR", "COMSPEC", "PATHEXT")}
        self.env.update(FAKE_HERMES_HOME=str(self.home), FAKE_HERMES_LOG=str(self.log),
                        FAKE_HERMES_MODE="", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1",
                        HERMES_HOME=str(self.home), LOCALAPPDATA=str(self.home),
                        TEMP=str(self.home), TMP=str(self.home), TMPDIR=str(self.home))
        self.argv = ["--hermes-home", str(self.home), "--hermes-command", json.dumps([sys.executable, str(FAKE)])]

    def save(self):
        self.jobs.write_text(json.dumps(self.document, ensure_ascii=False), encoding="utf-8", newline="\n")

    def snapshot(self):
        return {p.relative_to(self.home).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in self.home.rglob("*") if p.is_file()}

    def calls(self):
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def run_cli(self, apply=False):
        result = subprocess.run([sys.executable, "-B", str(SCRIPT), *self.argv, *(["--apply"] if apply else [])],
                                env=self.env, capture_output=True, encoding="utf-8", timeout=20)
        self.assertNotIn("SENTINEL-PROMPT-LINE", result.stdout + result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1, result.stdout + result.stderr)
        return result

    def check(self, result, code, prefix):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        self.assertTrue(result.stdout.startswith(prefix), result.stdout)
        self.assertEqual(result.stderr, "")

    def desired_prompt(self):
        return self.template["prompt"].replace("Lane filter: agentic.", f"Lane filter: `{LANE}`.")

    def existing(self):
        job = copy.deepcopy(self.template)
        job.update(id="resid0000001", name=NAME, prompt=self.desired_prompt())
        self.document["jobs"].append(job)
        return job

    def test_dry_run_exact_line_zero_calls_and_immutable_files(self):
        before = self.snapshot()
        result = self.run_cli()
        self.check(result, 0, "PLAN    create ")
        self.assertEqual(result.stdout, f"PLAN    create builder '{NAME}': every 120m, lane '{LANE}'\n")
        self.assertEqual(self.calls(), [])
        self.assertEqual(before, self.snapshot())

    def test_create_exact_argv_backup_readback_and_idempotency(self):
        before = self.jobs.read_bytes()
        result = self.run_cli(True)
        self.check(result, 0, "APPLIED builder/resid0000001 ")
        self.assertEqual(result.stdout, f"APPLIED builder/resid0000001 '{NAME}' (verified)\n")
        self.assertEqual(self.calls(), [["-p", "builder", "cron", "create", "--name", NAME,
            "--skill", "wolftac-build", "--model", "fake-model", "--provider", "fake-provider",
            "--workdir", self.template["workdir"], "--deliver", "local", "every 120m", self.desired_prompt()]])
        backups = list(self.jobs.parent.glob("jobs.json.bak-*-pre-residual-lane"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), before)
        stored = json.loads(self.jobs.read_text(encoding="utf-8"))
        self.assertEqual(stored["jobs"][:2], [self.template, self.other])
        self.assertEqual(stored["jobs"][2]["prompt"], self.desired_prompt())
        self.assertEqual(stored["jobs"][2]["schedule"], {"kind": "interval", "minutes": 120, "display": "every 120m"})
        snapshot = self.snapshot()
        self.check(self.run_cli(True), 0, "OK      builder/resid0000001 ")
        self.assertEqual(self.snapshot(), snapshot)

    def test_prompt_only_edit_and_dry_run(self):
        job = self.existing()
        job["prompt"] = job["prompt"].replace("; manual-lane", "")
        self.save()
        before = self.snapshot()
        result = self.run_cli()
        self.assertEqual(result.stdout, f"PLAN    edit builder/resid0000001 '{NAME}': prompt\n")
        self.assertEqual(self.snapshot(), before)
        self.check(self.run_cli(True), 0, "APPLIED ")
        self.assertEqual(self.calls(), [["-p", "builder", "cron", "edit", job["id"], "--prompt", self.desired_prompt()]])

    def test_model_only_edit(self):
        self.existing()["model"] = "old-model"
        self.save()
        self.check(self.run_cli(True), 0, "APPLIED ")
        self.assertEqual(self.calls(), [["-p", "builder", "cron", "edit", "resid0000001", "--model", "fake-model"]])

    def test_all_other_field_edits_and_optional_clears(self):
        job = self.existing()
        job.update(schedule={"kind": "cron", "minutes": 5}, skills=["old"], provider="old",
                   workdir="old", deliver="old", reasoning_effort="high", failure_deliver="old")
        self.save()
        self.check(self.run_cli(True), 0, "APPLIED ")
        self.assertEqual(self.calls(), [["-p", "builder", "cron", "edit", "resid0000001",
            "--schedule", "every 120m", "--skill", "wolftac-build", "--provider", "fake-provider",
            "--workdir", self.template["workdir"], "--deliver", "local",
            "--reasoning-effort", "", "--failure-deliver", ""]])
        actual = json.loads(self.jobs.read_text(encoding="utf-8"))["jobs"][-1]
        self.assertEqual(module.differences(actual, {**self.template, "prompt": self.desired_prompt(),
                         "reasoning_effort": None, "failure_deliver": None}), [])

    def test_optional_fields_and_multiple_skills_are_cloned(self):
        self.template.update(reasoning_effort="high", failure_deliver="fixture-channel", skills=["one", "two"])
        self.save()
        self.check(self.run_cli(True), 0, "APPLIED ")
        args = self.calls()[0]
        self.assertIn("--reasoning-effort", args)
        self.assertIn("--failure-deliver", args)
        self.assertEqual([args[i+1] for i, s in enumerate(args) if s == "--skill"], ["one", "two"])
        actual = json.loads(self.jobs.read_text(encoding="utf-8"))["jobs"][-1]
        for key in ("reasoning_effort", "failure_deliver", "skills"):
            self.assertEqual(actual[key], self.template[key])

    def test_refusals_are_read_only_in_plan_and_apply(self):
        baseline = copy.deepcopy(self.document)
        mutations = [("name", "Something Else"), ("no_agent", True), ("enabled_toolsets", ["x"]),
                     ("model", ""), ("provider", None), ("workdir", ""), ("deliver", ""),
                     ("skills", []), ("prompt", "no lane"),
                     ("prompt", self.template["prompt"] + "Lane filter: agentic."),
                     ("repeat", {"times": 1})]
        mutations += [(key, "unsafe") for key in module.FORBIDDEN]
        for apply in (False, True):
            for key, value in mutations:
                with self.subTest(apply=apply, field=key, value=value):
                    self.document = copy.deepcopy(baseline)
                    self.document["jobs"][0][key] = value
                    self.save()
                    before = self.snapshot()
                    self.check(self.run_cli(apply), 1, "REFUSE  template ")
                    self.assertEqual(before, self.snapshot())
            for case in ("missing-template", "duplicates", "unreadable", "invalid-json", "invalid-shape"):
                with self.subTest(apply=apply, case=case):
                    self.document = copy.deepcopy(baseline)
                    if case == "missing-template":
                        self.document["jobs"] = [self.other]
                    if case == "duplicates":
                        self.document["jobs"] += [dict(id="a", name=NAME), dict(id="b", name=NAME)]
                    self.save()
                    if case == "unreadable":
                        self.jobs.unlink()
                    if case == "invalid-json":
                        self.jobs.write_text("{", encoding="utf-8")
                    if case == "invalid-shape":
                        self.jobs.write_text('{"jobs": [null]}', encoding="utf-8")
                    before = self.snapshot()
                    prefix = "REFUSE  residual: 2 jobs" if case == "duplicates" else "MISSING "
                    self.check(self.run_cli(apply), 1, prefix)
                    self.assertEqual(before, self.snapshot())
        self.assertEqual(self.calls(), [])

    def test_failed_command_captures_output(self):
        self.env["FAKE_HERMES_MODE"] = "fail"
        result = self.run_cli(True)
        self.check(result, 1, "FAILED  create builder: hermes exit 1")
        self.assertEqual(len(self.calls()), 1)

    def test_successful_noop_does_not_write_jobs(self):
        self.env["FAKE_HERMES_MODE"] = "noop"
        result = self.run_cli(True)
        self.check(result, 1, "FAILED  create builder: read-back ")
        backup = next(self.jobs.parent.glob("jobs.json.bak-*-pre-residual-lane"))
        self.assertEqual(self.jobs.read_bytes(), backup.read_bytes())

    def test_encoding_mangle_is_rejected(self):
        self.env["FAKE_HERMES_MODE"] = "mangle"
        self.assertEqual(self.run_cli(True).stdout, "FAILED  create builder: read-back prompt\n")

    def in_process(self, apply=False):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, self.env, clear=True), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = module.main([*self.argv, *(["--apply"] if apply else [])])
        self.assertNotIn("SENTINEL-PROMPT-LINE", stdout.getvalue() + stderr.getvalue())
        return code, stdout.getvalue()

    def test_dynamic_excludes_and_no_duplicate_constants(self):
        with patch.object(gk, "RESIDUAL_EXTRA_EXCLUDES", ("OMAS ATLAS", "manual-lane", "extra")):
            code, output = self.in_process()
        self.assertEqual(code, 0)
        self.assertTrue(output.endswith("; extra'\n"))
        self.assertEqual(self.calls(), [])
        source = SCRIPT.read_text(encoding="utf-8")
        for term in ("OMAS ATLAS", "manual-lane", "Wolftac CRM v2", "agentic", "9b7b5987bcd6", NAME):
            self.assertNotIn(term, source)

    def test_profile_mismatch_refuses_before_writes(self):
        mapping = {**gk.DEFAULT_JOB_MAP, "build": ("Todo", self.template["id"], "foreign")}
        before = self.snapshot()
        with patch.object(gk, "DEFAULT_JOB_MAP", mapping):
            code, output = self.in_process(True)
        self.assertEqual(code, 1)
        self.assertIn("profile mismatch", output)
        self.assertEqual(before, self.snapshot())

    def test_timeout_sanitized_and_bounded_no_shell(self):
        with patch.object(module.subprocess, "run", side_effect=subprocess.TimeoutExpired("SENTINEL-PROMPT-LINE", 120)) as run:
            code, output = self.in_process(True)
        self.assertEqual((code, output), (1, "FAILED  create builder: hermes timeout\n"))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.kwargs, dict(timeout=120, capture_output=True, shell=False))
        self.assertEqual(run.call_args.args[0][:2], [sys.executable, str(FAKE)])

    def test_backup_collision_never_overwrites_existing_backup(self):
        stamp = module.datetime.now()
        backup = self.jobs.with_name(self.jobs.name + stamp.strftime(".bak-%Y%m%d-%H%M%S-pre-residual-lane"))
        backup.write_bytes(b"preserve prior proof")
        before = self.snapshot()
        with patch.object(module, "datetime") as clock:
            clock.now.return_value = stamp
            code, output = self.in_process(True)
        self.assertEqual((code, output), (1, "FAILED  create builder: backup unavailable\n"))
        self.assertEqual(before, self.snapshot())

    def test_docs_are_indexed(self):
        self.assertIn("### Residual build job (operator only)", (ROOT / "docs/factory-gatekeeper.md").read_text(encoding="utf-8"))
        self.assertIn("test_residual_build_job.py", (ROOT / "deploy/tests/README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
