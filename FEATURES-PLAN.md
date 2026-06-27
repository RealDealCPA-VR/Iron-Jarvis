# Iron Jarvis — Feature Completeness Plan (master to-do)

> ✅ **COMPLETE (2026-06-26).** All 34 discovered gaps built and shipped. New-features
> audit found 9 issues (2 high, 3 med, 4 low) — all fixed; a verification pass then
> returned **0 findings (converged)**. **440 offline tests pass** (from 385), dashboard
> production build is clean (26 routes), offline demo works, README reconciled.


A 7-perspective, multi-agent feature/UX red-team (daily-driver, onboarding, agent-capability,
ops/admin, dashboard, README-trust, competitive) surfaced **34 verified gaps** (each confirmed
genuinely-missing AND end-user-valuable). They are built in priority waves; each wave ends with
tests/build green and a correctness loop. Status: ✅ done · ◻ pending.

## Wave 0 — quick correctness win
- ✅ **#6 Sub-agents inherit the parent provider/model** (was hardcoded `mock` → real multi-agent never used Claude). `delegate_tool.py`, `agent_tools.py`, test updated.

## Wave 1 — Backend session lifecycle + observability (P1, coupled core: app.py/orchestrator/runtime/cli/models/db)
- ◻ **#1 Cancel/stop a running session** — key `bg_tasks` by session; `Orchestrator.cancel_session`; `CancelledError`→CANCELLED; `POST /sessions/{id}/cancel`; CLI `cancel`.
- ◻ **#2 Rerun a session** — `POST /sessions/{id}/rerun` (clone task/agent/provider/model); CLI `rerun`.
- ◻ **#10 Continue / follow-up on a finished session** — `POST /sessions/{id}/continue {message}`; reuse workspace; seed a recap; CLI `continue`.
- ◻ **#15 Delete a session** — `DELETE /sessions/{id}` (refuse running; cascade runs/tools; GC worktree); CLI `delete`.
- ◻ **#14 Export a session** — `GET /sessions/{id}/export?format=md|json`.
- ◻ **#3 Per-run token/cost usage** — `usage` on `LLMResponse`; populate in adapters; accumulate per run; persist on `AgentRun`/`Session`; surface in views.
- ◻ **#22 File upload (backend)** — `POST /documents/upload` (workspace-confined via fs_policy).
- ◻ **#5 Settings (backend)** — `GET/PUT /settings` (whitelisted keys → config.toml).
- ◻ **#30 Diagnostics / state-health** — `GET /diagnostics` (integrity_check, db/wal sizes, key presence, orphan count).
- ◻ **#20 Backup / restore** — CLI `backup`/`restore` (WAL checkpoint + tar.gz; keys excluded by default).
- ◻ **#27 Event-log retention/prune** — `prune-events --older-than`; optional startup sweep.

## Wave 2 — Provider/agent capability (P1, adapter cluster + new modules)
- ◻ **#21 Local/offline LLM (Ollama / OpenAI-compatible)** — `base_url` on OpenAIAdapter; register `ollama`; fixes the "local-first, network optional" pitch.
- ◻ **#3b/#9 Image/vision** — image parts on `LLMMessage`; emit in anthropic/openai/google adapters.
- ◻ **#7 web_search tool** — `WebSearchTool` (DuckDuckGo, zero-setup) wrapped untrusted; register.
- ◻ **#8 MCP client** — minimal `mcp/` (stdio + HTTP), list/register remote tools at boot.
- ◻ **#28 Vault key rotation** — `rotate_key()` re-encrypt all secrets/vault entries atomically; CLI.
- ◻ **#29 Migration runner** — `schema_version` meta + ordered migrations (extends the additive reconciler).
- ◻ **#26 Log activity viewer (backend)** — optional rotating file log + `GET /events` filtered query.

## Wave 3 — Dashboard (UI surfaces for the above + UX polish)
- ◻ **#11 Self-dev page** + nav · **#18 prune-worktrees button** · **#4 live per-session feed**
- ◻ **#12 Confirm on destructive deletes** (shared `ConfirmButton`) · **#13 session search/filter/sort**
- ◻ **#16 Workflow run history** · **#17 responsive/mobile sidebar** · **#19 example-prompt chips**
- ◻ **#23 Notification center** (reviews + CU approvals bell) · **#5b Settings page** · **#24 Help/Guide page**
- ◻ **#22b File-upload UI** (documents + New Session) · **#32 daemon-token login box** (localStorage)
- ◻ UI surfaces for cancel/rerun/continue/delete/export/usage on the sessions pages.

## Wave 4 — README trust + final verification
- ◻ **#13r OAuth claim** — scope to Google OAuth + API-key for others. · **#33 Postgres** — mark roadmap.
- ◻ **#34 Railway button** — make honest. · **#25 spec-section codes** leaking into UI subtitles → remove.
- ◻ Re-verify every README claim against the now-current code; mark verified.
- ◻ Full re-audit (correctness loop): tests green + dashboard build green + offline demo.

> Verified already this session: dashboard prod build is clean (22 routes), 385 tests pass, README
> headline claims (voice/OAuth/computeruse/n8n/Kanban/PTY/desktop/documents) confirmed in code.
