"""WD-564 pinned documentation proof; not a production skill generator.

Read-only by default. --render-out writes ONLY the two fixed example renders to
an explicit scratch directory. Never installs skills, calls APIs or reads env.
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
    inventory = data["inventory"] + data["reference_inventory"]
    assert len({i["path"] for i in inventory}) == len(inventory)
    tree = git("ls-tree", "-r", "--name-only", base).splitlines()
    stages = "(?:spec|build|review|integrate)"
    patterns = [
        rf"canonical/wolftac-{stages}/SKILL\.md",
        rf"plugins/wolftac-factory/skills/wolftac-{stages}/SKILL\.md",
        rf"ports/claude-desktop/wolftac-{stages}/SKILL\.md",
        rf"ports/codex/wolftac-{stages}\.md",
        rf"ports/hermes/wolftac-{stages}/SKILL\.md",
        r"ports/hermes-factory-integrate/skills/wolftac-integrate/SKILL\.md",
        rf"ports/hermes/wolftac-{stages}/references/.+",
        r"ports/hermes-factory-integrate/skills/wolftac-integrate/references/.+",
    ]
    discovered = {p for p in tree if any(re.fullmatch(q, p) for q in patterns)}
    assert discovered == {i["path"] for i in inventory}, "unmapped source/asset"
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
    print(f"INVENTORY {len(data['inventory'])} skill copies; {len(data['reference_inventory'])} reference assets")
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
