"""WD-564 pinned documentation proof; not a production skill generator.

Never writes repository files. Template diffs use auto-cleaned temporary extracts;
--render-out also writes the two fixed example renders to explicit external scratch.
Never installs skills, calls APIs or reads credentials.
"""
from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import itertools
import json
from pathlib import Path
import random
import re
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), *args], stderr=subprocess.PIPE
    ).decode("utf-8")


def ownership(data: dict, texts: dict[str, str]) -> dict:
    owned = {p: [[] for _ in text.splitlines()] for p, text in texts.items()}
    ids = set()
    for row in data["rows"] + data["reference_rows"]:
        assert row["id"] not in ids, ("duplicate row", row["id"])
        ids.add(row["id"])
        assert row["decision"] in {"shared", "platform-only", "stale"}
        assert row["reason"].strip() and row["sources"]
        for span in row["sources"]:
            p, lo, hi = span["path"], span["start"], span["end"]
            assert 1 <= lo <= hi <= len(owned[p]), (p, lo, hi)
            for n in range(lo, hi + 1):
                owned[p][n - 1].append(row["id"])
    for p, lines in owned.items():
        for n, row_ids in enumerate(lines, 1):
            assert len(row_ids) == 1, ("ownership", p, n, row_ids)
    return owned


def discover(data: dict, tree: list[str]) -> set[str]:
    """Discover whole stage-copy roots; exclusions are exact pinned assets."""
    stages = "(?:spec|build|review|integrate)"
    patterns = [
        rf"canonical/wolftac-{stages}/.+",
        rf"plugins/wolftac-factory/skills/wolftac-{stages}/.+",
        rf"ports/claude-desktop/wolftac-{stages}/.+",
        rf"ports/codex/wolftac-{stages}\.md",
        rf"ports/hermes/wolftac-{stages}/.+",
        r"ports/hermes-factory-integrate/skills/wolftac-integrate/.+",
    ]
    discovered = {p for p in tree if any(re.fullmatch(q, p) for q in patterns)}
    inventory = data["inventory"] + data["reference_inventory"] + data["supplemental_inventory"]
    prose = {i["path"] for i in inventory}
    scripts = {i["path"] for i in data["script_assets"]}
    assert len(prose) == len(inventory), "duplicate source inventory"
    assert len(scripts) == len(data["script_assets"]), "duplicate script inventory"
    assert not prose & scripts, "script/prose overlap"
    assert discovered == prose | scripts, "unmapped source/asset"
    return prose


def verify_script_assets(data: dict) -> None:
    families: dict[str, list[str]] = {}
    for item in data["script_assets"]:
        path = item["path"]
        blob = git("show", f"{data['base']}:{path}")
        digest = hashlib.sha256(blob.encode()).hexdigest()
        assert digest == item["sha256"], ("script hash", path)
        assert item["decision"] == "shared" and item["reason"]
        families.setdefault(Path(path).name, []).append(digest)
    for name in ("linear_bot.py", "run_gate.py", "slack_bot.py"):
        compat = git("show", f"{data['base']}:ports/codex/wolftac-integrate-scripts/{name}")
        assert len(families[name]) == 5
        assert set(families[name]) == {hashlib.sha256(compat.encode()).hexdigest()}, name
    assert len(families["skip_report.py"]) == 1


def verify_templates(data: dict, texts: dict[str, str], owned: dict) -> tuple[int, int]:
    """Diff all five extracted template bodies, retaining original coordinates."""
    variants = data["template_variants"]
    assert set(variants) == {"CT", "PT", "D", "X", "H"}
    sources = []
    for key, span in variants.items():
        p, lo, hi = span["path"], span["start"], span["end"]
        lines = texts[p].splitlines()
        assert 1 <= lo <= hi <= len(lines)
        sources.extend((p, n) for n in range(lo, hi + 1))
        assert all(owned[p][n-1][0].startswith("S-template-") for n in range(lo, hi+1))
    expected = [(s["path"], n) for r in data["rows"] if r["id"].startswith("S-template-")
                for s in r["sources"] for n in range(s["start"], s["end"]+1)]
    assert len(sources) == len(set(sources)), "duplicate template coverage"
    assert set(sources) == set(expected), "missing template coverage"
    changes = set()
    pairs = 0
    with tempfile.TemporaryDirectory(prefix="wd564-template-diffs-") as tmp:
        paths = {}
        for key, span in variants.items():
            lines = texts[span["path"]].splitlines()[span["start"]-1:span["end"]]
            # Only Markdown wrapper indentation is normalized; line count stays exact.
            if key in {"D", "X", "H"}:
                assert all(not line or line.startswith("   ") for line in lines)
                lines = [line[3:] if line else "" for line in lines]
            paths[key] = Path(tmp) / f"{key}.md"
            paths[key].write_bytes(("\n".join(lines)+"\n").encode())
        for left, right in itertools.combinations(variants, 2):
            run = subprocess.run(["git", "diff", "--no-index", "--no-ext-diff", "--no-textconv",
                                  "--unified=0", "--", str(paths[left]), str(paths[right])],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            assert run.returncode in {0, 1}, run.stderr.decode()
            pairs += 1
            for side, n in diff_coordinates(run.stdout.decode()):
                span = variants[(left, right)[side]]
                p, original = span["path"], span["start"] + n - 1
                assert original <= span["end"]
                assert len(owned[p][original-1]) == 1
                assert owned[p][original-1][0].startswith("S-template-")
                changes.add((p, original))
    assert pairs == 10 and changes
    print(f"TP1 templates: {pairs} real Git extracted-body pair diffs; {len(changes)} distinct differing source lines; unmapped=0; multiply-mapped=0")
    for p, n in random.Random(564).sample(sorted(changes), 20):
        print(f"SAMPLE templates {p}:{n} -> {owned[p][n-1][0]} | {texts[p].splitlines()[n-1].strip()}".rstrip())
    return pairs, len(changes)


def diff_coordinates(diff: str):
    """Yield side and 1-based original-blob line from real git unified diffs."""
    old = new = None
    for line in diff.splitlines():
        match = re.match(r"@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@", line)
        if match:
            old, new = map(int, match.groups())
        elif old is not None and new is not None:
            if line.startswith("-"):
                yield 0, old
                old += 1
            elif line.startswith("+"):
                yield 1, new
                new += 1
            elif line.startswith(" "):
                old += 1
                new += 1


def render(platform: str) -> str:
    example = HERE / "example" / "build"
    core = (example / "core.md").read_text(encoding="utf-8")
    overlay = json.loads((example / "platform" / f"{platform}.json").read_text(encoding="utf-8"))
    markers = re.findall(r"\{\{overlay:([a-z0-9-]+)\}\}\n", core)
    assert len(markers) == len(set(markers))
    assert set(markers) == set(overlay), "missing/unused overlay slot"
    result = re.sub(r"\{\{overlay:([a-z0-9-]+)\}\}\n", lambda m: overlay[m[1]], core)
    assert "{{overlay:" not in result
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--render-out", type=Path)
    args = parser.parse_args()
    data = json.loads((HERE / "ledger.json").read_text(encoding="utf-8"))
    base = data["base"]
    assert re.fullmatch(r"[0-9a-f]{40}", base)
    inventory = data["inventory"] + data["reference_inventory"] + data["supplemental_inventory"]
    tree = git("ls-tree", "-r", "--name-only", base).splitlines()
    discover(data, tree)
    verify_script_assets(data)
    texts = {}
    for item in inventory:
        text = git("show", f"{base}:{item['path']}")
        assert hashlib.sha256(text.encode()).hexdigest() == item["sha256"], item["path"]
        assert len(text.splitlines()) == item["lines"]
        texts[item["path"]] = text
    owned = ownership(data, texts)
    document = (HERE.parent / "skill-source-audit.md").read_text(encoding="utf-8")
    assert document.splitlines()[2] == f"**Pinned origin/main:** `{base}`"
    for row in data["rows"] + data["reference_rows"]:
        assert document.count(f"| {row['id']} |") == 1, ("missing/duplicate table row", row["id"])
        assert f"| {row['decision']} |" in next(l for l in document.splitlines() if l.startswith(f"| {row['id']} |"))
        for q in row["conflicts"]:
            assert any(c["id"] == q for c in data["conflicts"])
    for conflict in data["conflicts"]:
        assert f"**{conflict['id']}**" in document
    print(f"BASE {base}")
    print(f"INVENTORY {len(data['inventory'])} skill entrypoints; {len(data['reference_inventory'])} reference assets; {len(data['supplemental_inventory'])} template/loader assets")
    print(f"EXECUTABLE ASSETS {len(data['script_assets'])} exact hashes checked; three Integrate families each identical across six copies; no scripts imported/executed")
    print(f"COVERAGE {sum(map(len, owned.values()))} source lines each owned exactly once")
    total_pairs = total_changed = 0
    for stage, copies in data["paths"].items():
        changes = set()
        pairs = 0
        for (_, left), (_, right) in itertools.combinations(copies.items(), 2):
            diff = git("diff", "--no-ext-diff", "--no-textconv", "--unified=0", f"{base}:{left}", f"{base}:{right}")
            pairs += 1
            for side, n in diff_coordinates(diff):
                p = (left, right)[side]
                assert len(owned[p][n - 1]) == 1
                changes.add((p, n))
        assert len(changes) >= 20
        total_pairs += pairs
        total_changed += len(changes)
        print(f"TP1 {stage}: {pairs} real Git pair diffs; {len(changes)} distinct differing source lines; unmapped=0; multiply-mapped=0")
        for p, n in random.Random(564).sample(sorted(changes), 20):
            quote = texts[p].splitlines()[n - 1].strip()[:130]
            print(f"SAMPLE {stage} {p}:{n} -> {owned[p][n-1][0]} | {quote}".rstrip())
    print(f"TP1 TOTAL pairs={total_pairs}; differing-source-lines={total_changed}; samples=80")
    verify_templates(data, texts, owned)
    metadata = [texts[i["path"]].split("dependencies:", 1)[1]
                for i in data["supplemental_inventory"] if i["path"].endswith("openai.yaml")]
    assert len(metadata) == 3 and len(set(metadata)) == 1
    print("LOADERS spec/build/review: all lines classified; identical Linear dependency block")
    count = len(data["rows"]) + len(data["reference_rows"])
    print(f"TP2 rows={count}; missing-decision=0; conflicts={len(data['conflicts'])}; sign-off=NOT CLAIMED")
    slots = json.loads((HERE / "example/build/slots.json").read_text(encoding="utf-8"))
    core = (HERE / "example/build/core.md").read_text(encoding="utf-8")
    assert {s["marker"] for s in slots} == set(re.findall(r"\{\{overlay:([a-z0-9-]+)\}\}", core))
    valid = {r["id"] for r in data["rows"]}
    for slot in slots:
        assert slot["rows"] and set(slot["rows"]) <= valid
        expected_rows = set()
        for platform, key in (("hermes", "H"), ("codex", "X")):
            span = slot[f"{platform}_lines"]
            if span:
                p = data["paths"]["build"][key]
                expected_rows.update(owned[p][n - 1][0] for n in range(span[0], span[1] + 1))
        assert expected_rows == set(slot["rows"])
    for platform, key in (("hermes", "H"), ("codex", "X")):
        result = render(platform)
        baseline = texts[data["paths"]["build"][key]]
        committed = (HERE / f"example/build/rendered/{platform}.md").read_bytes()
        assert result.encode() == committed == baseline.encode(), f"unexplained {platform} render delta"
        print(f"TP3 {platform}: rendered from core+overlay; baseline diff=0; sha256={hashlib.sha256(committed).hexdigest()}")
        if args.render_out:
            target = args.render_out.resolve()
            assert target != ROOT and ROOT not in target.parents, "render output must be external scratch"
            target.mkdir(parents=True, exist_ok=True)
            (target / f"{platform}.md").write_bytes(result.encode())
    # Mutation tests ensure a coverage hole/duplicate/undecided row is rejected.
    for name in ("hole", "duplicate", "undecided"):
        bad = copy.deepcopy(data)
        if name == "hole":
            bad["rows"][0]["sources"][0]["start"] += 1
        elif name == "duplicate":
            bad["rows"][0]["sources"].append(copy.deepcopy(bad["rows"][0]["sources"][0]))
        else:
            bad["rows"][0]["decision"] = ""
        try:
            ownership(bad, texts)
        except AssertionError:
            print(f"NEGATIVE {name}: rejected")
        else:
            raise AssertionError(f"negative control accepted: {name}")
    for omitted in data["supplemental_inventory"]:
        bad = copy.deepcopy(data)
        bad["supplemental_inventory"] = [i for i in bad["supplemental_inventory"] if i != omitted]
        try:
            discover(bad, tree)
        except AssertionError:
            print(f"NEGATIVE omitted-source: rejected {omitted['path']}")
        else:
            raise AssertionError("supplemental omission accepted")
    for name in ("unknown-file", "script-hash", "template-hole", "template-duplicate"):
        bad = copy.deepcopy(data)
        try:
            if name == "unknown-file":
                discover(bad, tree + ["canonical/wolftac-spec/new-helper.py"])
            elif name == "script-hash":
                bad["script_assets"][0]["sha256"] = "0" * 64
                verify_script_assets(bad)
            elif name == "template-hole":
                bad["template_variants"]["CT"]["start"] += 1
                verify_templates(bad, texts, owned)
            else:
                bad["template_variants"]["PT"] = copy.deepcopy(bad["template_variants"]["CT"])
                verify_templates(bad, texts, owned)
        except AssertionError:
            print(f"NEGATIVE {name}: rejected")
        else:
            raise AssertionError(f"negative control accepted: {name}")
    changed = set(git("diff", "--name-only", base).splitlines())
    changed.update(git("ls-files", "--others", "--exclude-standard").splitlines())
    assert changed and all(p.startswith("docs/") for p in changed), changed
    for p in changed:
        path = ROOT / p
        if path.is_file():
            assert b"\r" not in path.read_bytes(), f"CR byte: {p}"
    print(f"TP4 changed paths={len(changed)}; only docs/; LF-only")
    print("PASS: mechanical coverage and rendering; semantic judgment/sign-off remain human review")


if __name__ == "__main__":
    main()
