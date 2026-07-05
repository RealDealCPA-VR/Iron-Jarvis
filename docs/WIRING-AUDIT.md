# Iron Jarvis — Wiring Audit: Required Fixes (verified)

A read-only auditor swept the project for missing/broken connections (registry
↔ agents ↔ permissions, daemon ↔ dashboard routes, events, and end-to-end
subsystem loops). Below: what was found, the fix, and how it was verified.
**312 tests pass after the fixes.**

## MUST-FIX (broken behavior) — all fixed ✅

| # | Identified | Fix | Verified |
|---|-----------|-----|----------|
| 1 | `researcher` / `memory` / `automation` agent types **silently ran as Builder** (only 4 of 7 had definitions); the Supervisor + `delegate` actively tell the model to delegate to `researcher`. | Added real `AgentDefinition`s for **Researcher / Memory / Automation** (distinct prompts + curated tool allowlists) in `agents/types.py`. | probe: each resolves to its own type with its own tools (19/13/26); `/agents` now lists **7** built-ins. |
| 2 | **Two divergent SUPERVISOR definitions** — `run_supervised` used a `tools=["delegate"]`-only one, while `/agents/spawn` used the richer `types.py` one → different behavior per entry path. | `run_supervised` now uses `get_agent_definition(AgentType.SUPERVISOR)` — one source of truth. | suite green; supervisor behaves identically on both paths. |
| 3 | Schedules with **`kind="callback"` were accepted but did nothing** (no dispatcher branch → silent no-op on cron). | Removed `callback` from `KINDS` + the tool schema enum → rejected at creation. | probe: `POST /schedules {kind:"callback"}` → **400**. |

## SHOULD-FIX (missing/incomplete) — fixed ✅

| # | Identified | Fix | Verified |
|---|-----------|-----|----------|
| 4 | **12 registered tools unreachable** by any built-in agent — most importantly **all computer-use tools** (`browse`/`web_extract`/`web_action`/`computer_use_status`), plus `notify`, `create_agent`/`list_agents`/`spawn_agent`. Computer use was effectively undrivable. | Gave the new **Automation** agent the computer-use + integration + agent-management + notify tools (Researcher gets `browse`/`web_extract`); added `notify`/`list_agents`/`spawn_agent` to Supervisor/Planner. Computer use is now drivable (still gated by `policy.enabled` + approvals). | probe: Automation tools include `browse`/`web_action`/`notify`/`create_agent`. |
| 5 | **Dead permission keys defaulting to `allow`** (`create_document`, `search_codebase`, `image_analysis`, `git_status`, `git_diff`) — latent fail-open if such a tool is ever registered. | Flipped all five to **`deny`** (fail-closed). | config; suite green. |
| 6 | `config.default_skills` declared "auto-injected" but **never consumed**. | `AgentRuntime` now injects configured default skills into the system prompt (before lessons). | suite green. |
| 8 | `webhook.received` / `schedule.fired` / `computeruse.run_finished` emitted as **string literals** not in the `EventType` enum. | Promoted to first-class `EventType` members. | events.py. |

## VERIFIED OK (loops already close correctly)
Cron scheduler starts on daemon boot · outbound webhooks fire on events · notifier
alerts on review/workflow/provider events · `connections.credential` gates provider
availability/routing · the learning loop is injected end-to-end · git-native review
reachable · sandbox policy honored · permission engine fail-closed for unknown tools ·
every dashboard fetch/WS maps to a real route (no 404/405) · no dangling agent→tool refs.

## Noted (low-priority, not fixed this round)
- **#7** `ARTIFACT_GENERATED` event type is never published (ArtifactStore has no event bus). Cosmetic; the UI color entry simply never fires.
- **#9** Git-native reviews are in process memory (lost on restart); scheduled-task event publishing crosses the BackgroundScheduler thread → main loop (a latent concurrency edge under heavy scheduled-event load). Both are hardening follow-ups, not active breakage.
