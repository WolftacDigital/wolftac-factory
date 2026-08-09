# Human-approval and rebased-artifact review proof

Use this pattern when a feature introduces an approval-gated merge, deploy, publish, billing change, or other privileged action that runs across separate worker invocations.

## 1. Prove approval provenance, not just display name

An `author == owner` check is insufficient when automation authenticates through the owner's account. First identify the principal used by the reviewing/building/integration tools. Compare its immutable user ID with the alleged approver's ID and with the authors of automated reports.

Treat the approval gate as blocking if the worker can mint an event that passes its own approval predicate. A fixture where the same automation posts `APPROVE`, then accepts it as human approval, is evidence of a fail-open gate even if timestamps and author names look correct.

Require a provenance signal the merge worker cannot forge, such as:

- a human-only account while workers use a distinct bot/service identity;
- a separately scoped relay that records the source event ID, sender ID, and timestamp from an approval lane the merge worker cannot write to;
- a signed/verified approval record checked by immutable IDs, not display names.

Test both paths:

1. A worker-authored approval-shaped event must be rejected and produce no privileged action.
2. A genuine human-origin approval newer than the proof pack must be accepted.

Never expose tokens or message contents beyond the minimal IDs/timestamps needed for proof.

## 2. Prove the gate-tested artifact survives the handoff

A proof pack must name an immutable artifact that the next invocation can fetch. A rebased commit left only in a detached worktree or local object database is not durable proof.

After the proof-pack pass, start a fresh clone (ideally a separate process/session) and run:

```sh
git clone <remote> <fresh-dir>
git -C <fresh-dir> fetch --all --prune
git -C <fresh-dir> cat-file -e '<proof-pack-sha>^{commit}'
```

If `cat-file` fails, the approval pass cannot reliably execute the tested merge from another machine/session or after cleanup/GC. Fail the review unless the implementation either:

- publishes the rebased commit to a non-default integration/PR ref without rewriting the reviewed branch; or
- deterministically reconstructs the rebased commit after approval and re-runs all gates before the privileged action.

Do not accept an implementation that depends on a prior worker's temporary worktree path, reflog, or unadvertised local object.

## 3. Persist the default-branch baseline

A later approval pass cannot enforce “default has not moved since the proof pack” unless the proof pack records the exact ref and SHA used for verification. Ahead/behind counts, a timestamp, or an unpublished rebased SHA are insufficient.

Require every proof pack to include:

```text
- **Default head:** origin/<default> @ <exact SHA>
```

On the approval pass, fetch the remote and compare the current default SHA to that recorded value before reconstructing or merging. Any mismatch voids the approval and requires a fresh proof pack. Add a deterministic invariant/semantic check for this field when the workflow is implemented as agent instructions rather than executable code.

## 4. Verify both sides of an identity split

Do not accept documentation that merely assumes a dedicated worker identity exists. Read the authenticated worker/viewer ID and the workspace user/service-account inventory without exposing credentials. Then exercise both paths end to end:

1. A worker-authored approval-shaped event is rejected and no privileged action occurs.
2. A genuine human approval from the intended immutable human ID, distinct from the worker ID, is accepted and completes the state transition/result.

If the bot/service identity is not provisioned or connected, the negative path may be proven but the positive acceptance criterion remains unverified and currently inoperable. Treat that as a blocking delivery gap unless the approved spec explicitly makes identity provisioning a separate prerequisite issue.

Provisioning is not wiring. A bot appearing in the workspace user inventory and a one-off fixture run with ad hoc bot credentials do not prove the delivered worker will use that identity. Inspect all of the following:

- the actual scheduled/manual worker's authenticated viewer at runtime;
- onboarding and deploy instructions for account selection and token minting;
- the scheduler/profile/account binding used by the real worker;
- whether the worker process can also access the human's write credential.

The privileged worker must be capability-isolated to the bot/service identity. If stock setup still authenticates as the human, the gate either rejects every legitimate approval forever or becomes unsafe once a human-authenticated relay is added. A build report that leaves production bot wiring as “follow-up” does not satisfy an end-to-end approval criterion unless the approved spec explicitly separated that wiring into a prerequisite.

For relayed approvals (Telegram, Slack, webhooks), document provenance end to end. A relay writing through the worker's bot identity will be rejected; a relay impersonating the human through human OAuth is not automatically proof of human intent. Require an immutable source event/sender ID from a separately scoped lane that the merge worker cannot mint.

## 5. Exercise the real multi-invocation boundary

Run the workflow as separate phases with clean state:

1. Proof-pack pass from clone A: rebase, run gates, record SHA, perform no privileged action.
2. Dispose of or isolate clone A.
3. Approval pass from fresh clone B: fetch the recorded SHA, validate approval provenance, verify default has not moved, then perform the action only when authorized.
4. Re-fetch the remote and verify ancestry/result.
5. Repeat with an old approval, default-branch drift, missing artifact, and rebase conflict; each must fail closed.

A same-process fixture proves nominal logic but does not prove scheduled/distributed reliability.

## 6. Probe transport capability in the actual scheduled runtime

A bot token that authenticates successfully is necessary but not sufficient. The worker is not capability-isolated if the same process/profile can also call a human-authenticated Linear MCP or another human write transport.

Run both read-only identity probes from the runtime that will execute integration:

1. Using the required bot token/transport, query GraphQL `viewer { id name }`. Print only the immutable ID, display name, and whether errors occurred — never the token.
2. Using every other Linear transport available to that process (especially the default MCP), query `me`/`viewer` and record only the immutable ID and name.
3. Compare the results. If the bot transport resolves to the service identity but another available transport resolves to the human approver, the process still possesses the human write capability. Prompt text saying “never use the default connection” does not remove that capability.
4. When write-path proof is needed, use only an in-scope, reversible operation on the issue already under review (for example claim with `assignee: me`, read back the immutable assignee ID, then clear it during the normal verdict transition). Never create an approval-shaped event as a probe.

Treat dual access as blocking for privileged automation even when a bot-authored fixture passes. The production fix must be mechanical: a dedicated bot-only profile/process, the human MCP disabled or absent from that runtime, or an executable wrapper/toolset that exposes only the bot transport. Keyword-only semantic checks (`LINEAR_FACTORY_BOT_TOKEN`, `fail closed`, `notify-only`) do not prove isolation; validation must also reject procedures that load or invoke the default human-authenticated transport.

## 7. Reconcile operator-approved scope amendments without weakening the contract

A later operator amendment can legitimately move runtime capability isolation, account wiring, or deployment into a named successor issue. When that happens:

1. Re-read the current Linear description and every comment after the newest failed review. Confirm the amendment is explicit, operator-authored, and reflected in Goals/Non-goals/Acceptance Criteria—not merely claimed by a build report.
2. Grade the current issue against the amended scope. Do not continue failing it for work now explicitly assigned to the successor issue.
3. Keep the boundary honest: a contract-only issue must still be internally executable as written. Search every canonical/port/plugin copy for instructions that contradict the contract, especially `ToolSearch`, default MCP/toolset preflights, and named `list_issues`/`get_issue`/`save_issue` operations when the same document mandates bot-token GraphQL only.
4. Require fail-closed wording before queue access and a viewer identity probe through the required bot credential. The procedure should state that every queue read, issue/comment read, claim, report, and state transition uses that authenticated path.
5. Add drift locks that test both presence and absence: positive invariants for the bot credential/fail-closed/default-transport prohibition, plus a semantic rejection for default/human transport instructions. A check that only searches for `LINEAR_FACTORY_BOT_TOKEN` is insufficient.

If the contradiction is limited to agent instructions and validation rules, requires no design change, and can be fixed quickly in the detached review worktree, it is eligible for PASS WITH FIXES. Commit and push the repair to the reviewed branch, verify the remote head equals the new SHA, rerun the full parity/semantic/EOL/diff checks, and cite the fix SHA. Runtime isolation remains a blocker only for the successor issue or before production enablement, not for the amended contract-only issue.

## 8. Audit Hermes capability-isolation preflights and cron binding

When a delivery claims that a Hermes profile is bot-only, do not accept a green `viewer` probe or bot-authored audit trail as proof of isolation. Those prove which transport was used, not which other transports remained available.

### Bind the proof to the actual runtime

- Resolve the active profile and its real `HERMES_HOME`; inspect the profile-local config/tool inventory actually used by the scheduled worker. A preflight must not certify an arbitrary caller-supplied `--config` or `--profile-root` while the executing process retains human-authenticated tools.
- List every existing profile and inspect the intended worker profile specifically. If no named isolated profile/runtime exists when the approved scope requires one, a recipe and synthetic YAML fixture are not delivery.
- Run a negative probe from a deliberately dual-capable runtime. Pointing the preflight at a no-MCP fixture or hard-coded bot-profile directory must not turn that same process green. Also set `HERMES_HOME` explicitly to the human/default profile and run the preflight with no path override; if it still scans the bot profile and exits 0, it is certifying a directory rather than the current runtime.
- Check inherited process credentials by printing key-presence booleans only (never values) for human-capable variables such as `LINEAR_API_KEY` and relevant proxy credentials. Hermes profile `.env` loading can override keys present in the profile file without clearing unrelated keys inherited by the process; therefore a bot-only profile `.env` does not by itself prove a clean process environment. Require a sanitized launch environment or executable rejection of forbidden inherited credentials.
- A profile-local `mcp list` showing no servers is necessary but not sufficient. Correlate it with the actual process environment, built-in/plugin tool inventory, and a worker invocation launched under that profile; otherwise a terminal/plugin path can retain the human capability while the profile YAML looks clean.

### Probe subprocess inheritance from the exact profile

Hermes' profile secret scope and `get_secret()` isolation do not automatically prove that terminal or code-execution children are clean: those children may be built from process-global `os.environ`, and a profile `.env` load can leave unrelated inherited keys intact. Reproduce the boundary without exposing values:

1. Launch a read-only one-shot under the exact worker profile, not from the reviewer's default profile.
2. In one terminal call, print only `KEY:True|False` for every forbidden human-capable credential (for example `LINEAR_API_KEY` and relevant proxy credentials). Never print, hash, or encode the values.
3. Run the submitted preflight in that same profile/runtime and record its exit code.
4. If a forbidden key is visible to the child while preflight exits 0, the runtime is dual-capable and both the green-path isolation claim and the human-path rejection criterion fail.
5. When necessary, inspect the current subprocess sanitization registry/blocklist by key name only. Do not infer that a credential is stripped merely because provider secrets or MCP config are profile-scoped; verify membership or execute the boolean probe.

Use a disposable fixture to test fail-closed ordering: monkeypatch network helpers to raise if called, run with missing credentials, and require the credential error exit before any helper is reached. For token rotation, copy only into a temporary profile, verify the fresh token's immutable viewer before persistence, scan output for raw and URL-encoded forms, and confirm the real profile file is unchanged.

### Enumerate every human-capable Linear transport

Probe more than a server literally named `linear`. Include direct Linear MCP, Composio Linear, generic/delegating MCPs that expose Linear tools, plugins, inherited toolsets, and any other configured write transport. Query the immutable viewer ID through each available path. A single path resolving to the human approver makes the runtime dual-capable.

If config inspection is part of the gate:

- parse YAML structurally rather than with a regex;
- test quoted keys, inline maps, alternate valid nesting, disabled entries, and generic providers such as Composio;
- add semantic negative tests that reject every known human-capable transport, not only positive checks for a bot token or a `linear:` key.

### Keep the trusted identity immutable and side effects ordered

The expected factory-bot ID is a security constant. Do not allow a normal env var or CLI flag to redefine it in production; otherwise a human token plus a matching override can satisfy the gate. After token re-mint, query `viewer` and verify the immutable bot ID before persisting the token. Persist atomically and preserve the old token if identity validation or the write fails. This is required when the contract promises zero side effects on failed preflight paths.

### Verify the scheduled command end to end

Cross-check both current Hermes documentation and live CLI help, then verify the exact operator recipe without creating the spend-gated job:

1. The cron command explicitly targets the isolated profile (named profile alias or the current supported profile-selection mechanism); plain `hermes cron create` is insufficient when the default profile is human-capable.
2. The preflight path is absolute or deployed inside the configured cron workdir. A skill saying “run from the factory repo” does not make `deploy/preflight.py` reachable from a different workdir.
3. Provider/model pinning uses a supported profile-local control plane and is read back after creation; do not rely on hand-editing `cron/jobs.json` when the current docs warn that direct edits can fail silently. When the current Hermes API exposes `cronjob(action="update", job_id=..., provider=..., model=...)`, use that supported update path (or the current documented equivalent), then list/read the persisted job and verify profile, provider, model, snapshots, skill, workdir, and schedule before enabling the worker.
4. The scheduled session itself reports the bot viewer and the absence of every human transport before queue access.
5. Read the persisted job back after any claimed repair. Verify the current prompt—not a prior build report—uses a stable path that exists, the approved workdir, and the expected skill/provider/model. Then inspect the newest saved run output to prove that exact persisted prompt passed preflight. A successful one-shot `chat` invocation does not prove the scheduled job is operable.
6. Correlate timestamps: the end-to-end fixture must postdate creation/configuration of the isolated runtime and appear in that runtime's session inventory. Correct bot/human comment authors from an earlier ad hoc fixture prove transport choice, not that the delivered scheduler/profile executed the flow.

A fixture whose comments are all bot-authored is useful audit evidence, but it passes the isolation criterion only when the fixture ran in that actual scheduled/profile runtime and the negative transport inventory was also proven.

## Review verdict guidance

These are major defects, not PASS WITH FIXES:

- the worker can satisfy its own human-approval predicate;
- the bot identity exists only in a fixture while the delivered worker still authenticates as the human, when runtime isolation/wiring remains in the current approved scope (if an explicit operator amendment moved it to a named successor issue, apply section 7 instead);
- a relay can create an accepted human-authored record without immutable source-event provenance;
- the approved SHA is unavailable from a fresh clone;
- the approval pass relies on unstated local worktree/object state;
- default drift does not invalidate approval and force a new proof pack.

Report concrete evidence: authenticated principal ID, event author IDs/timestamps, proof-pack SHA, fresh-clone `cat-file` result, remote refs, and ancestry exit status.