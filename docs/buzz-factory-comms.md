# Buzz factory comms lane — discovery (WD-354 AC-1)

Status: **discovery complete; the lane is buildable but blocked on two owner actions.**
Probed 2026-08-19 on AI-MSI against the live Wolftac relay.

This document is the AC-1 gate for WD-354. The spec required discovery to run *before*
any lane code, because a degraded lane built on guesses is worse than no lane. It also
answers the reaction question that WD-355 (merge-approval gate) depends on.

## Verdict

| Requirement | Finding |
|---|---|
| Reachable relay | **Yes** — HTTP 200, ~230 ms connect |
| Event schema for posting to a room | **Yes** — `buzz messages send`, documented below |
| Reactions as first-class signed events | **Yes** — own top-level command, 64-char hex event ids |
| Owner push / subscribe | **Yes** — Buzz Desktop running, `buzz-acp` live |
| Credential storage outside the repo | **Yes** — Windows Credential Manager |
| **Factory identity the factory may sign as** | **No — does not exist; relay refuses unenrolled keys with `relay_membership_required`, and an agent cannot create one** |
| **Factory room UUID** | **Unknown — needs an authenticated call as an identity we do not have** |

Buzz *can* meet the needs. Two gates are owner-only; see [Owner actions](#owner-actions-required).

## Relay

- Community relay: `wss://wolftac-digital.communities.buzz.xyz`
- The same host serves HTTPS for the CLI and Blossom media (`/media/<sha256>.png`).
- Reachability probe (unauthenticated, 2026-08-19):

  ```text
  GET https://wolftac-digital.communities.buzz.xyz/        -> HTTP 200
  GET https://wolftac-digital.communities.buzz.xyz/health  -> HTTP 200
  ```

- The CLI default relay is `http://localhost:3000`. **Every factory invocation must pass
  `--relay` or set `BUZZ_RELAY_URL` explicitly** — defaulting silently posts nowhere.

A reachable port is not health proof. Full proof requires an authenticated
`buzz channels list`, which is gated on the identity below.

## Client, CLI, and locations

Buzz Desktop is installed and running on AI-MSI.

| Item | Location |
|---|---|
| Binaries | `%LOCALAPPDATA%\Buzz\` — `buzz.exe`, `buzz-acp.exe`, `buzz-agent.exe`, `buzz-desktop.exe` |
| Managed-agent state | `%APPDATA%\xyz.block.buzz.app\agents\managed-agents.json` |
| Node ACP adapters | `%APPDATA%\Buzz\node-tools\` |
| Existing local broker | `%LOCALAPPDATA%\hermes\cache\buzz-local-acp\` (`entry.py`, `invoke-buzz.py`, `shim/`) |

`buzz.exe` is **not on `PATH`** — call it by absolute path or through the shim.
It has no `--version` flag; identify the build from the Buzz Desktop installer.

Live processes at probe time: `buzz-desktop`, `buzz-acp`.

## Identity model

Buzz is Nostr-based: every participant is a keypair and every message is a signed event.
`managed-agents.json` currently holds **14 records — 7 unkeyed drafts and 7 enrolled
identities**:

| Face | Public key (abbreviated) | Harness | `respond_to` |
|---|---|---|---|
| Claude | `4efe22840e…bbbe21` | `claude-agent-acp` | allowlist |
| Codex | `91878c6984…483f62` | `codex-acp` | allowlist |
| Logan | `f9cd29f204…dbea41` | brokered `hermes-acp` | allowlist |
| Zeus | `a36cb39bd3…edcd63` | `hermes-acp` | allowlist |
| Bumble | `332863fe7e…cc215f` | `codex-acp` | allowlist |
| Honey | `589739eeec…1f1497` | `codex-acp` | allowlist |
| Fizz | `f10503e6c4…b9ceb3` | `codex-acp` | owner-only |

**Claude / Codex / Logan / Zeus already exist and match the Slack identity map in
`deploy/approval-policy.json` exactly.** Convenient, but *not* reusable: each of those
keys backs a live ACP poller, and the collaboration-surface doctrine forbids running one
identity on two harnesses at once. The factory needs its own key — which is what NG-3's
"one factory identity" allows.

### Why an agent cannot enrol that identity

```text
buzz agents draft-create   Open a prefilled create-agent form in the owner's Buzz Desktop
buzz agents draft-update   Open a prefilled edit-agent form in the owner's Buzz Desktop
```

`draft-create` **only prefills a form**; it creates nothing. It additionally requires
`BUZZ_AUTH_TAG` (a NIP-OA owner attestation), and a returned form is not proof the relay
accepted a draft. Keypair provenance is therefore owner-held by design: Jonathan creates
the identity in Buzz Desktop, and Buzz writes the private key into Credential Manager.
No agent path exists, and manufacturing one would mean forging owner attestation.

## Credentials

Buzz stores the human identity and every managed-agent key in **Windows Credential
Manager**, generic target `secrets.buzz-desktop`, username `secrets`, as one JSON blob
with logical entries `identity` and `agent:<public-key>`.

Confirmed present on AI-MSI (presence only — the blob was never read or printed):

```text
Target: LegacyGeneric:target=secrets.buzz-desktop
```

Rules for the lane:

- **No Buzz key material in this repo, in Linear, or in Buzz message bodies** (NG-4).
- The factory key is read by a broker at call time and passed **only** into the
  `buzz.exe` child environment as `BUZZ_PRIVATE_KEY` / `BUZZ_AUTH_TAG`, then cleared.
- Hermes deliberately strips `BUZZ_PRIVATE_KEY` from model-authored terminal
  subprocesses. **Do not weaken that filter** — reuse the broker pattern already at
  `%LOCALAPPDATA%\hermes\cache\buzz-local-acp\`.
- Non-secret and safe to commit: relay URL, channel UUIDs, public keys, display names.
- Hermes `.env` naming for the non-key references, following the existing
  `SLACK_FACTORY_BOT_TOKEN` precedent: `BUZZ_FACTORY_RELAY_URL`,
  `BUZZ_FACTORY_ROOM_UUID`, `BUZZ_FACTORY_PUBKEY`. The **private key stays in Credential
  Manager**, never in `.env`.

## Event schema — posting a message

```text
buzz messages send --channel <UUID> --content <TEXT> [options]

  --channel <CHANNEL>    Channel UUID (from 'buzz channels list')
  --content <CONTENT>    Message text — @mentions and markdown; '-' reads stdin
  --kind <KIND>          Nostr event kind (default: channel default)
  --reply-to <REPLY_TO>  Event ID to reply to (creates a thread)
  --broadcast            Also publish to the wider Nostr network
  --file <FILES>         Attach file(s) as imeta tags
  --mention <MENTIONS>   Pubkey to mention (hex or npub; repeatable)
```

Points that matter for the factory:

- **Use `--content -` and pipe proof packs via stdin.** Packs are long and full of
  backticks and newlines; passing one as an argument risks shell mangling and leaks the
  body into process-command-line logs.
- **Never pass `--broadcast`.** Factory comms stay private to the community relay;
  broadcasting republishes them to the public Nostr network.
- `--reply-to` gives Buzz-native threading — the analogue of Slack's `thread_ts`.
- `--mention <pubkey>` is the reliable push trigger. Bare `@Name` text resolves only when
  unambiguous; an explicit pubkey always notifies. **Mention Jonathan's pubkey explicitly
  on packs and alarms** — AC-5 turns on him actually hearing them.

Output is JSON on stdout. Errors are JSON on stderr:
`{"error": "<category>", "message": "<detail>"}`.

Exit codes — the whole basis for AC-6 failure isolation:

| Code | Meaning | Lane behaviour |
|---|---|---|
| 0 | ok | record the event id |
| 1 | bad input | disclose; do not retry blindly |
| 2 | relay / network error | **Slack lane still posts; disclose the Buzz miss** |
| 3 | auth error | key/attestation expired — disclose, never crash |
| 4 | other | disclose |
| 5 | write conflict | safe to retry once |

Exit 2 and 3 are the expected relay-down and key-expiry paths. Buzz must never block or
silently swallow: Slack stays authoritative until WD-355 lands.

### Measured, not assumed

Run 2026-08-19 with a throwaway never-enrolled key, to confirm the table above rather
than trust the help text:

| Case | Exit | stderr |
|---|---|---|
| No credentials | 3 | `auth error: BUZZ_PRIVATE_KEY is required` |
| Valid-format key, dead port `127.0.0.1:9` | **2** | `network error: … tcp connect error … actively refused` |
| Valid-format key, live relay | **3** | `relay error 403: relay_membership_required` |

The dead-port case is the AC-6 relay-unreachable path, confirmed as a clean exit 2 with
structured JSON on stderr — a `buzz-post` wrapper can branch on it without parsing prose.

The third case is the load-bearing one for this discovery. It proves two things at once:
the relay is not merely answering HTTP but **actively authenticating**, and an identity
that is not enrolled is refused with `relay_membership_required`. The missing factory
identity is therefore a hard relay-side gate, not a local configuration gap — no amount
of lane code works around it.

## Reactions — first-class, and what WD-355 needs

This is the finding WD-355 was waiting on. Reactions are a **top-level command**, not a
message-body convention:

```text
buzz reactions add     --event <64-char hex> --emoji <EMOJI>
buzz reactions remove  --event <64-char hex> --emoji <EMOJI>
buzz reactions get     --event <64-char hex>
```

Each reaction is a signed Nostr event bound to one specific event id, so it carries
exactly the two properties the merge-approval gate needs:

1. **Authorship is cryptographic**, not an API claim. A 🚀 from Jonathan's pubkey is
   signed by his key. That is strictly stronger than Slack's `reactions.get`, where
   authorship is only ever the workspace API's assertion.
2. **It binds to one immutable event id** — the natural analogue of head immutability.
   An approval cannot drift onto a different pack.

`reactions get` is the direct replacement for the Slack blind-watcher pre-flight: call it
once before polling, so an unreachable relay cannot masquerade as "no reaction yet".

**None of this changes approval semantics in this issue** (NG-1). Buzz messages in this
phase are notify-only and must say so in the body. WD-355 owns the switchover.

## How Jonathan's client subscribes and pushes

Buzz Desktop subscribes to the community relay and raises native notifications; it is the
same client already hosting the ACP agents. Push is driven by mentions — hence the
explicit `--mention <pubkey>` rule above. Presence is separately queryable via
`buzz users presence`, which lets the watchdog record whether he was reachable when an
alarm fired.

## Worked example

Illustrative: the UUID and pubkey below are placeholders in the correct shape, because
the real room UUID cannot be read until the identity exists.

```bash
BUZZ="$LOCALAPPDATA/Buzz/buzz.exe"
ROOM="6f1c2b4a-8d3e-4f57-9a10-2c5b7e9d4f83"      # from: buzz channels list
JON="npub1jonathanexamplepubkeyplaceholder000000000000000000000000"

printf '%s\n' "$PACK_BODY" | "$BUZZ" \
  --relay https://wolftac-digital.communities.buzz.xyz \
  messages send --channel "$ROOM" --content - --mention "$JON"
```

Response shape:

```json
{ "id": "9f2c…e41a", "pubkey": "<factory pubkey>", "created_at": 1755561600, "kind": 42 }
```

The `id` is the artifact to record in Linear and to hand to `reactions get`. Credentials
never appear on the command line — the broker injects them into the child environment.

## Owner actions required

Everything above is settled. Two things only Jonathan can do, and both must land before
AC-2/AC-3/AC-5 can be built or tested:

1. **Create the factory identity** in Buzz Desktop — name it `Wolftac Factory`, matching
   `fallback_identity` in `deploy/approval-policy.json`. Do **not** screenshot or copy the
   private-key reveal modal; just close it. Buzz writes the key to Credential Manager as
   `agent:<pubkey>`. Then share the **public** key.
2. **Name the factory room and add that identity to it** — an existing room or a new
   private one — and confirm his own client is a member so pushes arrive. Then run
   `buzz channels list` once and share the room's UUID.

With the pubkey and room UUID in hand the rest of WD-354 is mechanical: `buzz-post` on
the existing broker pattern, the `buzz` section in `approval-policy.json`, dual-post
wording across the integrate and review surfaces, the AC-6 wrong-port negative test, and
the AC-5 live dual-run.

## Sources

Probed live on AI-MSI, 2026-08-19: `buzz.exe --help` plus the `agents`, `messages send`,
`reactions`, `channels`, and `users` subcommand help; a redacting whitelist parse of
`managed-agents.json`; `curl` reachability against the relay; `cmdkey /list` filtered to
Buzz (presence only). Doctrine cross-checked against the Hermes skill
`autonomous-ai-agents/multi-agent-collaboration-surfaces` and its Buzz references.
No private key, credential blob, or auth tag was read, printed, or stored.
