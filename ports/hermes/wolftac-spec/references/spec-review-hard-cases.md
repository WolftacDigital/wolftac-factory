# Spec classes that controller review rejects repeatedly

Check the matching section before each draft round. Each rule cost at least one CHANGES_NEEDED round when it was missing.

## Exclusion / lease / single-writer specs
- **Ownership changes only under a held lock.** No TTL takeover and no rename takeover. A stale owner is a health signal, never authority to replace it. Only the lock-holding process writes ownership state; clients file requests that the holder validates by token, so a delayed old release or renew cannot touch a successor.
- **Use an OS lock that dies with its holder** (Windows `msvcrt.locking` byte lock; child processes do not inherit it). Record identity as PID plus process creation time, because PIDs are reused.
- **A dead holder fails closed.** You cannot count a dead holder's surviving children afterwards: a named Job Object can no longer be opened once its creator dies, even while its members live. Make that state operator-blocked rather than inferring the children stopped.
- **Quiet does not mean stopped.** Two equal fingerprints minutes apart do not prove no writer, because an idle child can wake later. Require a drain proof (all job members exited, recorded by the live holder), or block.
- **Gate-time detection is best effort.** A checkpoint absorbs a foreign edit made during the owner's batch, and an edit reverted before the next gate is invisible. List these blind spots in the spec; never call detection containment.
- **Fingerprint the index too.** Plain `git diff` misses staged changes. Include `git ls-files --stage`, `git diff --binary HEAD`, the branch ref, untracked files with hashes, and reparse points (recorded, never followed). Define ignored, unreadable and filtered paths.
- **One state table shared by every reader** (acquire, gate, recover, dispatcher): each state's meaning and each operation's result. A "recovered" record that acquire does not recognise is a contradiction.
- **Recovery is not adoption.** Closing an orphaned grant preserves evidence; writing to that dirty tree needs a separate decision bound to the evidence hash. An approval id typed into a CLI is an audit note, not enforcement.
- **Identity.** Key the resource by physical identity (volume serial plus file index) and the branch ref, so junctions, 8.3 names and branch switches cannot create a second lock. A process-global env var such as a session id is not per-session in a gateway that hosts several sessions.
- **Deterministic race tests:** file barriers at lock/publish, request handling, between gate and write, and before release. Stress loops alone do not prove exclusion.
- **State the boundary honestly:** cooperative exclusion among participants. Preventing same-user non-participants needs separate accounts and ACLs, which is an owner decision.
- **Rollout:** name every source file, installed copy and deploy mapping. Participation means every writer path drained and proven by a dry run; matching hashes alone do not prove it.

## CI / installer proof specs
- **Inventory every workflow the proof PR will trigger before opening or updating it.** Evaluate each workflow's `pull_request` path filters against the PR's full changed-file list. A workflow that exists only on a merged-in harness branch still runs, because the PR's merge ref contains it. Note `cancel-in-progress` groups, since a later push cancels an approved attempt. Budget the whole event (every workflow and job), not one workflow.
- **Choose proof-file locations that match no unbudgeted filter.** Read pinned harness code from a second checkout at an immutable SHA instead of merging harness files into the PR, and pin candidate artifacts in a proof-owned file instead of a frozen manifest. When a frozen file must change (a manifest pin), name the exact allowed fields and the comparison base for each checkout.
- **A seam must reach the branch it claims.** Read the guard around the code path. For example, renaming the target executable makes an `if FileExists` guard skip the whole `Exec` block, so it never tests launch failure. Require milestone evidence that the intended branch ran.
- **Controls per scenario.** The old release is the right control only for defects it contains. New refusal or restore branches need their own proof that the candidate branch ran, not a blanket "must fail on the old version" rule.
- **Narrow the guarantee to what is tested.** Older installers do not contain new restore logic, and an uninstaller that ignores a helper's exit code may still delete files. Claim only the tested version pairs and paths.
- **Prior-state capture must fail closed.** A read failure, access denial or malformed definition is UNKNOWN and refuses before any destructive step; it is never recorded as "absent". Resolve the real object name from the installation identity, and preserve a disabled state as disabled.
- **Restoring configuration and restarting the process are separate outcomes.** Do not promise a healthy restart while the test still holds the port.
- **A distinct outcome needs a distinct marker.** When the exit code is shared with ordinary refusals (Inno Setup 7), use a fixed log and message line instead.

## Windows protected-install specs
- **Protecting a directory requires its whole ancestor chain.** Delete-child on the parent overrides the child's own protected DACL: the parent's owner can rename the protected folder and put a replacement at the same path.
- Under `C:\ProgramData`, `Users` may create folders and `CREATOR OWNER` gets full control, so any non-elevated process can pre-create the parent before an elevated install.
- Require Apply to create or validate every ancestor (owner SYSTEM, Administrators or TrustedInstaller; no delete-child, delete, `WRITE_DAC` or `WRITE_OWNER` for the operator's token), re-check after creating it, and have Verify check the same chain plus each installed file. A Verify that checks only the leaf directory overclaims.
- **Scratch modelling recipe** (non-elevated, own scratch folder):
  1. Give the child a protected DACL that grants the user ReadAndExecute only.
  2. Try a write, a delete, a rename via the parent, and a re-create at the same path.
  3. Clean up with `icacls <dir> /grant <user>:(OI)(CI)F /T`. A `Get-Acl` → `Set-Acl` round trip can demand `SeSecurityPrivilege` when not elevated.
  4. Do not name a PowerShell function `Try`; it is a keyword and the script fails to parse.

## Live credential-window packages
- **Separate provider facts from local history.** Same-scope client-credentials tokens can coexist, while a scope change or secret rotation revokes. A past incident stays "historical, unconfirmed" until a forced test observes it.
- **A renew-if-due command proves nothing by succeeding.** Require its output to show the token was minted, plus a new expiry.
- **State token-source precedence.** Set the home explicitly and drop inherited token variables on every command.
- **A forced second mint must not propagate back** to the home under test; narrow the propagation target first.
- **One stop and escalation tree:**
  - credential rejection stops the run;
  - rate limits and 5xx get one retry;
  - a timeout is ambiguous, so check the live homes without minting before anything else.
