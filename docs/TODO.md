# The Comprehensive To-Do

*Everything known to be open as of v1.170.0 (2026-08-13), consolidated from
the deep-review wow track, deferred backlogs across waves, and known limits.
The deep review's 11 confirmed bugs are all FIXED (v1.166.2–v1.167.0) — this
file is what remains.*

## Needs validation before any fix (found, never reproduced)

- [ ] **DocPreview save-copy busy flicker** — the 409-overwrite confirm
  recurses inside the catch; the outer `finally` clears `saving` while the
  overwrite POST is still in flight (buttons re-enable mid-save).
  `dashboard/components/chat/DocPreview.tsx:252`
- [ ] **Already-finished session strands the live card** — `GET
  /sessions/{id}/stream` subscribes without checking terminal state; a run
  that finishes in the fetch→subscribe window leaves "waiting for the first
  token" forever. `daemon/routes/sessions.py` + `sessions/[id]/page.tsx`

## Wow wave 1 — five small connections, one story — **SHIPPED v1.168.0** ✔

*All five below landed in v1.168.0, plus kanban team nesting and
promote-to-knowledge from the lists further down.*

- [x] **Redaction receipt** — after `redact_pii`, diff source vs. redacted in
  DocPreview (the diff engine already exists), badge removed spans by
  category. The nervous pre-email check becomes a shown proof.
- [x] **Job deliverables on the session page** — render the ledger-proven
  `files_created/changed` from `GET /sessions/{id}/result` as an ArtifactsRail
  on session detail, per delegate in the TeamTree.
- [ ] **Undo where you look** — join rail/TurnReceipt paths against the undo
  journal; "Undo this write" under the message that did it.
- [x] **Origin chips** — `Session.origin` is indexed and serialized; render
  it (schedule:nightly · comm:telegram · job:agents · autonomy) on session
  rows + detail, with a filter.
- [x] **The bell knows it's waiting on you** — fold workflow runs parked on
  an *ask* step (question text in `waiting_json`) into the notification bell
  with an inline answer box.

## More small wins

- [x] Kanban nests delegation teams under the parent card — shipped v1.168.0.
- [x] Promote any chat answer / rail file into Project Knowledge — shipped v1.168.0.
- [ ] `terminal_tail` tool — chat reads the pane you're staring at (read-only).
- [x] Project heartbeat — shipped v1.169.0.
- [ ] Cost per client project — GROUP BY `project_id` over session tokens on
  the Usage page; per-engagement cost line on project cards.
- [x] Compaction summary inspectable any time — shipped v1.169.0.
- [x] Model report card — shipped v1.169.0.

## Medium bets (each ≈ one wave)

- [ ] **Client-scan triage** — attach a 60-page intake scan → proposed named
  split ("p.1–3 W-2 Acme, p.4–9 1099-B Schwab") → confirm → mechanical,
  journaled cut. The single most tedious tax-intake chore.
- [ ] **Batch extractions → client memory** — file batch_documents'
  facts/entities/figures into the project's knowledge (suggest-don't-act);
  months later recall answers "what was on the 1099-B?" with the source path.
- [ ] **Roll-forward guard** — accounts_diff → remap ranges → excel_edit →
  formula_check every rewritten cell; month-end proof instead of hope.
- [ ] **DraftCard → real Outlook draft/send** — per-call to/subject on the
  email channel + one route; confirm-first; ends the paste roundtrip.
- [ ] **Standing intake watch** — "watch this folder" chip → nightly batch
  (content-hash resumable, so quiet nights cost nothing) + morning digest.
- [ ] **Command palette searches the whole fabric** — one thin GET over
  `MemoryFabric.recall`, scoped to the active project.
- [ ] **Variables drawer** — list the session namespace's bound names
  (type/size, preview a slice, dispose); makes `_store_as` tangible.
- [ ] **Undo preview** — show the diff of what will come back before the
  Revert button; warn on drift up front.
- [ ] **Creative gallery → iterate in chat** — tile action opens chat with
  the image pre-attached through the vision path.
- [ ] **Terminals join the spine via cwd** — a pane under a project root is
  that project's terminal; assist gains project grounding.
- [ ] **Living documents, project-grounded** — add `project_id` to
  LiveDocRecord; regeneration gains project knowledge; freshness on the
  project surface.


## Workflows wave — SHIPPED v1.170.0 ✔

*Chat runs saved workflows (workflow_list/workflow_run tools + prompt block +
live run chip + "+"-menu), name-only POST /workflows/run resolves pin
server-side (reflex/canvas pin drops fixed), parameterized runs (inputs →
pre-seeded outputs), verified steps (expect: files/summary_contains against
ledger evidence), tool-step data handoffs ({{Step.data}}), resume interrupted
runs, PATCH rename, save-time validation, run pruning, canvas ask-gate +
DAG honesty + one serializer, 5 starter workflows, /workflows in default nav,
workflow:<name> origin stamped.*

Still open from the workflows analysis:
- [ ] Adversarial-verify step kind (fresh-context reviewer step) — expect
  checks shipped as the deterministic v1.
- [ ] workflow_list/workflow_run for headless agents (chat-first shipped;
  agents/types.py allowlists deliberately not extended yet).
- [ ] Webhook/email/calendar/api trigger stubs in triggers.py remain stubs.

## Deferred from earlier waves (still valid)

- [ ] Per-step agent re-grounding; workflow-engine memory tools; agent
  `add_knowledge` tool (v1.141 deferrals).
- [ ] Schedule pause/enable toggles (W-B deferral); skipped-week schedule
  still notifies.
- [ ] "Workflows that prove their work": verified steps (declared outcomes +
  ToolInvocation evidence), adversarial-verify step kind, step-level
  checkpointing, artifact handoffs (concepts from the 2026-08-06 analysis).
- [ ] Terminal survival backlog (assessed 2026-08-04, user-deferred).
- [ ] Remote-agent health probe (cached), and a session-shaped run for
  remotes beyond the supervisor bridge.
- [ ] Agent-type selection on schedules (today every scheduled task runs as
  builder).
- [ ] `origin` populated by the remaining callers (chat/user_task/comm/
  reflex/workflow lanes pass None today).

## Known limits (documented, not bugs)

- Packaged installs can't run `run_code` unless Python is on PATH (the
  `repl` tool is the packaged-safe path).
- Legacy Office formats (.doc/.xls/.ppt/.odt) must be converted before
  preview/extraction.
- Images are described, not OCR'd, in previews (scanned-PDF OCR exists on
  the read path).
- The REPL's write-confinement is a guardrail against a careless model, not
  a sandbox against hostile code (its own docs say so).
- A restart loses in-flight sessions (they reconcile honestly as FAILED
  "interrupted by a daemon restart"; QUEUED rows included).
