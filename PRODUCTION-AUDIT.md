# Iron Jarvis — Production Red-Team & Wiring Audit (multi-agent, looped)

A swarm of read-only auditor agents red-teamed the entire project across **four
iterations**; every candidate finding was **adversarially verified against the
real code** (a second agent tried to refute it) before being accepted, and each
accepted finding was fixed and re-audited. The loop ran until a pass came back
with nothing material left to address.

**Result:** all confirmed findings fixed · a new gated **self-development**
capability added · **312 → 385 offline tests pass** (no network, no keys).

## Convergence (findings confirmed per iteration)

| Iteration | Scope | Confirmed | Sev |
|---|---|---|---|
| 1 | 11-dimension full red-team + 2 re-runs | **24** | 2 high · 11 med · 11 low |
| 2 | verify fixes · red-team new self-dev · regressions · fresh sweep | **6** | 1 high · 2 med · 3 low |
| 3 | verify iter-2 · self-dev/git final · fresh sweep | **2** | 1 high · 1 med |
| 4 | verify iter-3 · re-cover · final convergence sweep | see end | — |

The count fell 24 → 6 → 2, i.e. the audit converged.

## What was fixed (by area)

**Security**
- **FS-allowlist bypass (HIGH):** agent file tools (`read_document`/`extract_pdf`/
  `file_search`) ignored `IRONJARVIS_FS_ALLOWLIST` and could read any absolute
  path, incl. the Fernet key. New `core/fs_policy.py` (allowlist + **protected
  roots** for the secrets/browser key dirs); enforced in the tools **and** (iter-2)
  the daemon HTTP file endpoints `/documents/read`, `/fs/list`, `/filesearch`.
- **OAuth-callback reflected XSS (HIGH):** the auth-exempt `/oauth/{provider}/callback`
  reflected the provider into an inline script. Now HTML-escaped + `JSON.parse`-encoded
  + restrictive CSP.
- **SSRF via outbound webhooks:** `assert_safe_webhook_url` (DNS-resolves and rejects
  private/loopback/metadata) at register **and** delivery; `IRONJARVIS_WEBHOOK_ALLOW_INTERNAL`
  opt-in (default off).
- Constant-time WebSocket token check; inbound-webhook replay/timestamp hardening.

**Providers / OAuth**
- Google OAuth token sent as `Authorization: Bearer` (was `x-goog-api-key`, always 401→mock);
  corrected scope.
- `complete_oauth` now raises on a failed exchange instead of storing an error body and
  marking "connected".
- Availability is **presence-only** (`has_credential`) and credential resolution runs
  off the event loop (`asyncio.to_thread`) — no blocking OAuth refresh on the loop.

**Robustness / concurrency / resources**
- Background session tasks held with a strong ref + failure logging (no GC mid-run).
- Playwright browser closed on shutdown; Docker client closed per run.
- `/events` WS detects idle disconnects and frees the subscriber; bounded subscriber
  queues (drop-oldest).
- Event handlers run **off the loop** (`asyncio.to_thread`) so an offline Slack/webhook
  POST can't freeze the daemon; handler errors are logged, not silently swallowed.
- **Cross-thread event publish (MED):** the APScheduler thread publishes via a foreign
  loop — `EventBus` now records each subscriber queue's owning loop and delivers via
  `call_soon_threadsafe`.
- `TerminalManager` evicts dead sessions; short connect timeouts on outbound HTTP.

**Sandbox / computer-use**
- Native shell prefers Docker when an isolating policy is set and flags an unconfined
  native fallback; Docker network is fail-closed (`internet != "allow"`) with CPU/pid caps.
- Computer-use credential/payment field detection works against the real browser; the
  human-approval queue now actually unblocks the next action (consume-on-use).

**Data persistence**
- SQLite hardened with WAL + `busy_timeout`; `init_db` runs an **additive-column
  reconciler** so a new model field can't brick existing `.ironjarvis` DBs.
- Full-width (128-bit) ids for the append-only event log.

**Packaging**
- Removed the unused `pydantic-settings` dep; added `py.typed`; the symlink-escape
  security test now runs on Windows via a junction fallback.

## New capability — Self-development (the agents can fix the project itself)

A gated, opt-in way for an Iron Jarvis agent to read/edit/test/fix **Iron Jarvis's own
source**, safely:

- **`AgentType.MAINTAINER`** with a focused, honest prompt.
- `Orchestrator.create_session(self_dev=True)` roots the session's **git worktree at the
  Iron Jarvis repo itself** (`core/self_dev.py` locates it; `self_dev_root` override is
  held to the same identity check). **OFF by default** (`config.self_dev_enabled`);
  refused otherwise.
- Changes land **only via the existing review/approve gate — never auto-merge**; an
  approval merge can't strand the developer's checkout (conflict → `merge --abort` +
  branch restore).
- Orphaned worktrees (from a restart while a review was pending) are garbage-collected:
  `prune_orphan_worktrees`, a startup sweep, `POST /worktrees/prune`, and
  `ironjarvis prune-worktrees`.
- Surfaces: `SessionCreate.self_dev`, `GET /self-dev`, `ironjarvis self-dev "<task>" --enable`.

## Proof

- `uv run pytest -q` → **385 passed** offline (was 312); the previously-skipped symlink
  test now runs.
- `uv run ironjarvis demo` completes on the MockLLM with no network.
- Cross-thread event delivery, self-dev gating, FS-policy enforcement, and the OAuth-callback
  escaping are each covered by dedicated regression tests
  (`tests/test_fix_core.py`, `test_fix_security.py`, `test_self_dev.py`, plus the swarm's
  `test_fix_{sandbox,cu_fields,cu_approval,terminals,webhooks,oauth}.py`).
