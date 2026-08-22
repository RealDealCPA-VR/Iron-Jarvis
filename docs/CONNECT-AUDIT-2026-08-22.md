# Connectedness Audit — 2026-08-22

*Method: 5 parallel dimension-finders (context spine, event bus, knowledge flows,
lane parity, recorded-but-never-surfaced) over the whole repo, each required to
evidence BOTH sides of a seam (producer file:line + the greps proving the consumer
absent on the REGISTERED code path), with the ~20 already-tracked/deliberate items
excluded up front. 19 raw findings → deduped → 9 adversarially verified (one
refuter per finding, briefed to refute). 6 confirmed net-new, 1 real-but-recorded,
2 refuted. Run at v1.199.0 (09bdff8). Full JSON: session scratchpad
`connect_audit.json`; per-agent transcripts in the workflow journal.*

The product thesis says "prefer features that CONNECT existing surfaces." Every
item below is a seam where one side is fully built and the other side never
receives it.

---

## Confirmed net-new (ranked)

### 1. HIGH — Chat- and Studio-made artifacts never reach the project's Media view
**Seam:** chat/Studio artifact production → `ArtifactRecord.project_id` → project Media view.
The consumer chain is COMPLETE: `artifacts/models.py:29` (indexed column),
`creative/service.py:333-334` (filter), `routes/creative.py:447-451`
(`?project_id`), `ProjectSurfaces.tsx:290-293` (the Media view). The inheritance
mechanism exists too (`artifacts/store.py:126-131` inherits from the producing
Session row). But every non-agent-session producer writes NULL:
- Both chat lanes hardcode `ToolContext(session_id="chat")` (`chat_turn.py:2181`,
  `routes/chat.py:1458`) and no Session row `"chat"` exists, so the inheritance
  lookup returns None — even when the turn resolved a project
  (`chat_turn.py:1862`), `ToolContext` has no project field to carry it.
- The pixio media sink (`platform.py:811-815`) and code sink
  (`platform.py:525-553`) pass only `session_id`.
- Studio's `creative_ingest` / `creative_upload` (`routes/creative.py:1087,
  1126-1132`) pass neither; the Studio page posts with zero project context.

**Worse: two places claim the opposite of reality** — `store.py:95-98`'s
docstring ("every generation a project task makes is scoped… without any caller
having to thread it through") and the Media empty state
(`ProjectSurfaces.tsx:320-321`: "generate something in Creative while this
project is active, and it lands here"). Both are false for the app's primary
surface.

**Fix shape:** `ToolContext` gains an optional `project_id`; both chat lanes pass
the resolved pid; sinks and the Studio ingest/upload accept and forward it; fix
the two lying texts in the same change.

### 2. HIGH — Job-origin approval pauses are invisible to the person who posted the job
**Seam:** runtime approval pause (origin `job:*`) → Agents page / bell.
`agents/runtime.py:969-971` allowlists `job` origins for the v1.189.0 mid-run
approval pause; the Agents page stamps every dispatched job `job:agents`
(`JobPostCard.tsx:30`). So these runs genuinely pause 300s
(`SESSION_APPROVAL_TIMEOUT_S`, `runtime.py:29`) and publish `approval.requested`
(`runtime.py:983-992`) — but only the CHAT surface renders the approval card
(`chat/page.tsx:3374`, `ProjectSurfaces.tsx:110`). Nothing the job-poster is
looking at (Agents page, bell) shows the ask; after 5 minutes the tool call is
refused and the job completes degraded, silently.

**Fix shape:** fold `approval.requested` into the NotificationBell (it already
carries computer-use approvals and workflow waits) + an inline answer on the
Agents page job card, reusing the same `POST /chat/approvals/{id}` route.

### 3. MEDIUM — `approval.requested` never reaches Telegram/notifications (while its sibling does)
**Seam:** `agents.runtime approval.requested` → comm/.
`grep approval src/iron_jarvis/comm` = zero matches. Not in
`notifier.py:26-36 DEFAULT_ALERT_EVENTS`, not in the Channels page's checkbox
list (`channels/page.tsx:169-174` — the user cannot even opt in), no answer path
in `comm/prompts.py` (hard-wired to `workflow.waiting` only) — while
`workflow.waiting` gets full multi-channel delivery plus a phone-answerable
`/answer` flow. Graded medium (not high) because the failure is fail-closed: the
timeout produces an honest refusal the model can route around. Still: the
two-way phone channel built for exactly this moment says nothing.

**Fix shape:** add the event to the notifier defaults + Channels list; extend
`comm/prompts.py`'s numbered-answer parser to approvals (approve/deny/once).

### 4. MEDIUM — Agent-authored workflows can't carry a project pin, and the store's save can EAT one
**Seam:** `workflow_create` tool → `WorkflowStore` project pin → every pinned-run
consumer (engine grounding `workflows/engine.py:359,440,942`, scheduler
`platform.py:1344`, reflex `reflex/router.py:173-177`). `WorkflowCreateTool`'s
input schema has no project field and calls `store.save(name, steps,
description)` (`workflows/tools.py:45-63,73-75`) against a signature that accepts
`project_id`. **Latent defect found during verification:** `store.save` with an
omitted `project_id` DELETES an existing pin row (`store.py:95-96`) — the
dashboard route works around it by pre-fetching the pin
(`routes/workflows.py:344-346`), so an agent re-saving a pinned workflow would
silently unpin it.

**Fix shape:** make omitted-`project_id` mean "keep the existing pin" in
`store.save` (the remote-agent-token lesson: absent ≠ delete); add the optional
project field to the tool, defaulting to the session's own project.

### 5. MEDIUM — Reflex-spawned sessions are never project-grounded
**Seam:** reflex `session` action → `Session.project_id` → the entire grounding
pipeline (`runtime.py:813-815 → _project_context`, memory fabric at 772/857).
`reflex/router.py:215` spawns `create_session(task, AgentType.SUPERVISOR)` with
no project, and structurally cannot carry one: `ReflexRule`
(`reflex/models.py:32-52`) has no project field; the reflex dashboard page never
mentions projects. An inbound "client emailed the missing 1099" reflex does its
work with zero client context.

**Fix shape:** `ReflexRule.project_id` (nullable) + a project picker on the
reflex page + pass-through at `router.py:215`.

### 6. MEDIUM — The round table and `consult()` are one-way knowledge valves
**Seam:** memory/lessons/project-knowledge → panel & consult prompts. Panels
WRITE into memory (thread remember via `memory/commit.py`; every round syncs
into the history index, `threads.py:497-541`, which feeds chat recall via
`fabric.py:83`) — but nothing flows IN: the panelist prompt is exactly
`base_prompt + role_line + PANEL_NO_TOOLS` + profile-how
(`threads.py:560-573,801-809`), and `consult` is the same shape
(`consult_tool.py:375-394,464-476`). No lessons, no fabric grounding, no project
knowledge. The advisors advise blind while the surfaces around them remember.

**Fix shape:** inject the lessons block + (when the thread is project-tagged)
project knowledge into panelist/consult prompts — same injection sites the
identity spine already established.

---

## Real, but partially recorded (worse than the record shows)

### 7. Telegram silently swallows photos/documents/voice — and advances the offset past them
`comm/channels.py:143-146` drops all non-text updates by design (the comment
says so), and `comm/inbound.py:356-368` persists the offset past them. The
recorded part is the drop; the UNRECORDED part is the silence: no reply ("I
can't read photos here yet"), no thread entry, no log — the user's message is
consumed forever with no acknowledgment, on the channel marketed as "same brain,
full tools, from your phone," while the desktop chat has a full attachment/OCR
pipeline the comm turn never touches (`ChatBody.attachments` exists,
`inbound.py:625` builds the body without it). Minimum honest fix: reply with the
limitation. Real fix: getFile → the existing attachment pipeline.

---

## Verified-refuted (for the record — do not re-file)

- **"delegation.started/completed have zero consumers"** — false: the roster's
  track-record attribution queries them (`roster.py:383-456`), and the session
  page's Live-activity card renders all session-tagged events generically.
- **"plan.* events never reach chat's escalated-turn progress"** — false on its
  load-bearing sentence: chat subscribes to the session stream on escalation and
  the sink's phase copy reaches it.

## Unverified backlog (found, deduped, dropped at the verify cap — triage later)

- feedback (weight-3 lessons) collectible only on session detail — not chat/phone (medium)
- ask-tier arming exists in the stream lane only; POST fallback + comm lane can never grant (medium)
- comm replies drop the whole v1.165/v1.199 accountability payload (route/doors/documents) (medium)
- ToolInvocation ledger has no per-tool analytics surface (the v1.196.0 review had to read SQLite by hand) (medium)
- fleet sampler retains ~7h of history; the Fleet page never fetches `/history` beyond sparkline width (medium)
- `schedule_create` inline-workflow payload loses project grounding the scheduler would honor (low)
- `session.queued` (concurrency governor) is a dead signal (low)
- notifier alerts on `autonomy.executed` but not `autonomy.proposed` — inverted for suggest-don't-act (low)
- `recall_lessons(scope="project")` advertised; nothing ever writes a project-scoped lesson (low)
- Usage page is a dead end — no drill-down links to the audit timeline (low)
