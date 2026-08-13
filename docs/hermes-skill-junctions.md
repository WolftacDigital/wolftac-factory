# Hermes skill junctions — making `/learn` edits land in the repo

**Status:** mechanism built and tested; **live cutover is gated on WD-346 landing.**
**Scope:** the Hermes port only (`ports/hermes/wolftac-*`). Claude Code, Codex,
Claude Desktop, the Codex plugin, the isolated `factory-integrate` profile and WSL
stay copy-deployed — migrating them is a follow-up (WD-349 NG-2).

## The problem this removes

Hermes resolves its skills directory as `get_hermes_home() / "skills"`
(`hermes_constants.py::get_skills_dir`). `/learn` therefore edits `SKILL.md` **in
place at the deployed path**, with ordinary file tools. `deploy/deploy.ps1` is a
publisher: it can only push repo → deployed, and nothing pulls. Every improvement
an agent learns is born outside version control and stays there until a human
notices — WD-282, WD-290 and WD-346 are the three reconciliations that resulted,
each larger than the last.

The fix is not a better reconciler. It is to make the location Hermes edits *be*
the repo, via a Windows **directory junction**, so a `/learn` edit is a working-tree
change from the moment it is written.

## What ships

| File | Role |
|---|---|
| `deploy/junctions.json` | The junctioned pair list — the single source of truth shared by all three scripts. |
| `deploy/junction-lib.ps1` | Shared helpers: LF-normalized tree comparison, reparse-point inspection, link-only removal, safety scan. |
| `deploy/junction-setup.ps1` | Validate-then-act migration to junctions. Idempotent; refuses rather than destroys. |
| `deploy/junction-teardown.ps1` | Rollback to real directories + copy-based deploy. Idempotent. |
| `deploy/deploy.ps1` | Recognizes live junctions: `-Check` reports them `JUNCTIONED` (never drift), `-Deploy` skips them (`JUNCTION-NOOP`). |

```powershell
pwsh deploy\junction-setup.ps1 -DryRun     # validate only, change nothing
pwsh deploy\junction-setup.ps1             # migrate
pwsh deploy\junction-teardown.ps1          # roll back
```

Both scripts take `-RepoRoot` and `-SkillsRoot` so they can be exercised against
scratch trees — which is how every claim below was tested.

## The four key decisions, and the evidence that settled them

### 1. Junction the main checkout, or a dedicated always-on-main worktree?

**Decision: a dedicated worktree, checked out detached at `main`, that no factory
stage ever switches.** Junctioning the canonical checkout is measurably unsafe.

Measured on a scratch clone with an uncommitted `/learn` edit present at the
junctioned path:

| Operation in the checkout the junction points at | Result for the uncommitted `/learn` edit |
|---|---|
| `git checkout <branch>` where the file is identical on both branches | survives (git carries the dirty file across) |
| `git checkout <branch>` where the file **differs** between branches | git refuses: `Your local changes would be overwritten` — edit survives, but the switch is blocked |
| `git stash push` | **silently removed** from the working tree; recoverable via `git stash list` / `git stash pop` |
| `git checkout -- <file>` | **destroyed**, unrecoverably — no reflog entry, no stash, nothing |

`git checkout -- .` is a routine cleanup in build/review/integrate. Pointing the
junction at a checkout that any stage may clean is a standing invitation to delete
knowledge that has never been committed anywhere.

Mitigation verified on the same scratch tree: with the junction pointed at a
dedicated detached worktree (`git worktree add --detach <path> main`), running
`git checkout other-branch`, `git checkout -- .` **and** `git stash push -u` in the
main checkout left both the deployed bytes and the uncommitted edit untouched, and
the live worktree still reported ` M ports/hermes/wolftac-build/SKILL.md`.

Operational rule that follows: **the live worktree is not a workspace.** Nobody
switches it, cleans it, or builds in it. It is fast-forwarded deliberately, after
its working tree is confirmed clean.

### 2. Does `git checkout` swap files under a live Hermes, and what is the blast radius?

**Yes it does, and the blast radius is one turn wide, not a restart.** Established
by driving the real Hermes code with `HERMES_HOME` pointed at a scratch home whose
`skills/` dir was junctioned into a worktree:

* **Skill bodies are re-read from disk on every invocation.** `tools/skills_tool.py::skill_view`
  opens `SKILL.md` with `read_text` each call; there is no in-process body cache.
  Appending a marker to the deployed file and re-calling `skill_view` **in the same
  process** returned the marker. So a checkout that rewrites `SKILL.md` changes what
  the *next* skill invocation executes, with no restart.
* **The rendered skills *index* is cached, in two layers**
  (`agent/prompt_builder.py::build_skills_system_prompt`): an in-process LRU
  (`_SKILLS_PROMPT_CACHE`) and a disk snapshot keyed on an mtime-ns/size manifest
  (`.skills_prompt_snapshot.json`). Observed: `build_skills_system_prompt()` returned
  byte-identical output before and after an on-disk edit in the same process. Only
  frontmatter `name`/`description` live in that index, and it is rebuilt on gateway
  restart or by `clear_skills_system_prompt_cache()` (which Hermes' own skill
  mutations call).

Blast radius, stated plainly: changing branches under a live gateway does **not**
corrupt state or need a restart, but the *very next* `/wolftac-*` invocation runs
whatever bytes the checkout left behind. If the frontmatter description changed,
the index stays stale until the gateway restarts. Decision 1's dedicated worktree
is what keeps this from happening at all.

### 3. Junction or symlink?

**Junction.** Verified on this machine (AI-MSI, Windows 11, PowerShell 7.6.4,
non-elevated, Developer Mode registry key absent):

```
IsElevated: False
DeveloperMode: (key absent)
JUNCTION: OK
SYMLINK: FAILED - Administrator privilege required for this operation.
```

A symlink-based design would require every operator and every unattended worker to
run elevated. A junction needs nothing.

Secondary benefit found during testing: Python's `os.path.islink()` returns **False**
for a directory junction, so Hermes' `os.walk(...)` traversal treats the junctioned
skill dir as an ordinary directory. Discovery does not depend on any `followlinks`
behavior.

**Known consequence — a log-only security warning.** `skill_view` resolves the real
path and compares it against the trusted skills roots, so a junctioned skill logs:

```
Skill security warning for 'wolftac-build': skill file is outside the trusted
skills directory (~/.hermes/skills/): ...\skills\wolftac-build\SKILL.md
```

It is a `logging.warning` only (`tools/skills_tool.py:1354`) — it never reaches the
model, never blocks the load, and `skill_view` returned `success: True` with the
full body in every test. To silence it, add the junction's real parent to the
profile's `config.yaml`:

```yaml
skills:
  external_dirs:
    - D:/Wolftac/factory-skills-live/ports/hermes
```

Verified: the warning disappears, each skill is still discovered exactly once (the
candidate scan de-duplicates on the resolved path, so no collision refusal), and
bodies still load. This is optional log hygiene, not a requirement.

### 4. What does `deploy.ps1` do with a junctioned pair?

**Neither checked nor written — explicitly, and said out loud.** Writing to a
junctioned path writes *into the repo working tree*, which would clobber exactly
the uncommitted `/learn` edit this whole design exists to preserve.

`deploy.ps1` reads `deploy/junctions.json`, and for each pair confirms the deployed
path is a live reparse point **pointing at this repo's tree** before treating it as
junctioned. A stale manifest entry, a missing link, or a link to somewhere else
falls back to ordinary copy behavior — the manifest alone is never trusted.

* `-Check` prints `JUNCTIONED <n> file(s) across <m> linked skill dir(s)` and excludes
  them from the drift count.
* `-Deploy` prints `JUNCTION-NOOP` and writes nothing. The test precedes the `-Force`
  branch, so **`-Deploy -Force` cannot clobber the working tree either.**

Measured: with an uncommitted edit in the junctioned tree, `-Deploy` and
`-Deploy -Force` both exited 0, and the file's SHA-256 was identical before and
after both runs.

### Alternatives rejected

* **Forking Hermes to redirect `get_skills_dir()`** — NG-3. Hermes is a vendored
  upstream checkout; a path-resolution fork buys a permanent upgrade burden.
* **A filesystem watcher copying deployed → repo** — a background process that can
  silently die is how this problem class started (see the 12-day cron outage noted
  in `CLAUDE.md`).
* **`skills.external_dirs` alone, with no junction** — Hermes does load skills from
  external dirs, but they are read-only by convention for skill *creation*, and the
  deployed `skills/wolftac-*` copies would still exist and still drift. External
  dirs are used here only as optional trust-warning hygiene (decision 3), not as the
  mechanism.

## Safety properties (and how each was proven)

Both scripts run a **full validation pass over every pair, print the plan, and only
then act**. A refusal on any pair aborts the whole run with nothing changed — so
running them in the wrong order cannot destroy content.

`junction-setup.ps1` refuses, changing nothing, when:

1. **Repo and deployed content differ** (LF-normalized SHA-256 per file, the same
   comparison `deploy.ps1 -Check` uses). This is the WD-346 gate: junctioning while
   the sides differ silently discards one of them. Proven both on scratch and
   against the live tree, where today it correctly refuses:
   `REFUSE wolftac-build: repo and deployed content differ in 3 file(s)` /
   `REFUSE wolftac-review: ... 2 file(s)`.
2. **The deployed path is a reparse point aimed somewhere unexpected.** Proven by
   planting a junction to an unrelated directory: setup refused and teardown refused
   ("not ours to remove"), and the foreign target was untouched.
3. **The deployed path is a file, or the repo tree is missing.**
4. **The repo tree contains a credential-bearing file** (`.env`, `auth.json`,
   `*.pem`, `*.key`, `id_rsa`, `.netrc`, …) **or a nested reparse point.** Proven by
   dropping a `.env` into a skill dir: `REFUSE wolftac-spec: credential-bearing file
   inside the junctioned tree`. This is the security gate — a junction publishes the
   repo subtree at an agent-writable path and vice versa, so nothing outside
   `ports/hermes/wolftac-*` may be reachable through it and no secret may sit inside it.

`junction-teardown.ps1` never deletes through a link: it removes the link entry with
`[IO.Directory]::Delete($path, $false)` after asserting the path is a reparse point,
then re-materializes the real directory from the repo (LF-normalized) and verifies
the result is neither a reparse point nor different from the repo.

**Idempotence:** setup run twice reported `NOOP` for all four pairs and exit 0;
teardown run twice reported `RESTORED` for all four and exit 0.

**Rollback:** after teardown, `deploy.ps1 -Deploy` returned to ordinary copy
behavior — including reporting `DRIFT-SKIPPED` on a target whose restored copy
carried an uncommitted edit, and exiting 1. Copy-based deploy is fully restored.

## Cutover runbook

Do **not** run this while `deploy.ps1 -Check` shows drift on the Hermes targets —
the setup script will refuse, which is the point.

1. Land WD-346 so `ports/hermes/wolftac-*` and `%LOCALAPPDATA%\hermes\skills\wolftac-*`
   are byte-identical. Confirm with `pwsh deploy\deploy.ps1 -Check`.
2. Create the dedicated live worktree — a checkout no stage ever switches:
   ```powershell
   git -C D:\Wolftac\Factory worktree add --detach D:\Wolftac\factory-skills-live main
   ```
3. Dry-run, then migrate:
   ```powershell
   pwsh D:\Wolftac\Factory\deploy\junction-setup.ps1 -RepoRoot D:\Wolftac\factory-skills-live -DryRun
   pwsh D:\Wolftac\Factory\deploy\junction-setup.ps1 -RepoRoot D:\Wolftac\factory-skills-live
   ```
4. Restart the Hermes gateway (`hermes gateway restart`) so the cached skills index
   is rebuilt from the new location.
5. Verify: `pwsh D:\Wolftac\Factory\deploy\deploy.ps1 -Check` reports the Hermes
   targets `JUNCTIONED`; a `/wolftac-*` invocation still works.

**Steady state.** After a `/learn` edit, `git -C D:\Wolftac\factory-skills-live status`
shows it. Review with `git diff`, then commit and push it from that worktree like any
change. Nothing auto-commits (NG-4). To advance the live worktree to a newer `main`,
confirm it is clean first, then `git -C D:\Wolftac\factory-skills-live checkout --detach origin/main`.

**Rollback.** `pwsh D:\Wolftac\Factory\deploy\junction-teardown.ps1 -RepoRoot D:\Wolftac\factory-skills-live`,
then restart the gateway. Any uncommitted `/learn` work is preserved twice over: it
stays in the live worktree, and it is what teardown copies back out to the deployed
path.
