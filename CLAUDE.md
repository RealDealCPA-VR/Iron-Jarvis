# Iron Jarvis — Agent Operating Manual

You are working on Iron Jarvis: a local-first AI operating system. One Python
daemon (FastAPI), one Next.js dashboard, one Electron desktop wrapper. The user
runs the PACKAGED desktop app daily — treat every change as production.

## The three processes

| Process | What | Port | Source |
|---|---|---|---|
| Daemon | FastAPI, all state + agents + tools | 127.0.0.1:8787 | `src/iron_jarvis/` |
| Dashboard | Next.js 15 (42 routes), arc-reactor-cyan aesthetic | 127.0.0.1:8788 | `dashboard/` |
| Desktop | Electron: spawns both, tray, updates, Spotlight | — | `desktop/main.js` |

Packaged layout: PyInstaller-frozen daemon (`packaging/ironjarvis.spec`) +
Next standalone run by Electron's node + electron-builder NSIS installer.
State home: dev = `~/.ironjarvis` unless `--root`; packaged =
`%APPDATA%/Iron Jarvis/.ironjarvis` (config.toml, ironjarvis.db (SQLite),
secrets/, skills/, terminals.json, backups/). The desktop app's per-install
bearer token: `%APPDATA%/Iron Jarvis/token.txt` — every daemon request needs
`Authorization: Bearer <it>`.

## Commands

```bash
# Backend tests (~3100, offline, ~10min). ALWAYS run before shipping.
uv run pytest -q --no-header
# Dashboard build (must show "Generating static pages (42/42)")
cd dashboard && pnpm build
# Syntax-check desktop changes
cd desktop && node --check main.js
# Dev run
uv run ironjarvis serve            # daemon on 8787
cd dashboard && pnpm dev           # dashboard
```

## Release flow (how the user receives your work)

1. Bump the version in **three files, with ANCHORED edits** (never blanket
   search/replace — it once rewrote a dependency pin): `pyproject.toml`
   (`version = `), `src/iron_jarvis/__init__.py` (`__version__`),
   `desktop/package.json` (`"version"`).
2. Commit + push to master. CI (`.github/workflows/release.yml`) detects the
   bump, PRE-CREATES the tag+release (electron-builder 422s otherwise), builds
   the frozen daemon + installer, publishes `Iron-Jarvis-Setup-X.Y.Z.exe` +
   blockmap + `latest.yml` (~10 min).
3. The desktop app auto-downloads (checks at boot + every 30 min) and installs
   only when the user clicks Restart-to-update (tray item / notification /
   Updates page). `latest.yml` missing assets = release still uploading.
4. **State the current (or new target) app version in EVERY response** so the
   user always knows which version to expect when they pull an update. The
   SessionStart hook (`.claude/session-start.sh`) injects the live version +
   repo state at session start — trust it over stale docs.

## Hard rules (each one was learned the expensive way)

- **The identity spine reaches EVERY prompt seam** (v1.144.0). `profile/`
  renders the user's profile and `personas/voice.py` the assistant's voice;
  both are appended in `daemon/chat_turn.py`, the `/chat/stream` mirror in
  `routes/chat.py`, `agents/runtime.py`, and `agents/threads.py` (the round
  table takes `include=("how",)` only — panelists must stay distinct). A NEW
  surface that talks to the user adds its injection in the same change:
  `tests/test_profile_v1144.py::test_profile_reaches_every_prompt_seam` drives
  all of them end-to-end, and each seam is mutation-proven. "Chat has it,
  agents don't" is the exact bug that wave existed to fix.
- **History is BUDGETED, never sliced** (chat v1.146.0, agents v1.152.0). Both
  chat lanes call `_plan_context` → `context.plan_history`; the perceive→act
  loop calls `context.agent_window.plan_agent_transcript` once per step. Both
  fit the transcript to the answering model's window (`_context_window`: pin →
  probe → default) and report what they dropped. Do not reintroduce a fixed
  `messages[-N:]`, and if you add to the system prompt, add it BEFORE the
  planner runs or its cost is invisible to the budget.
- **Compaction is MODEL-written and LEDGER-checked** (v1.153.0). `context/
  compaction.py` lets a model write a real structured summary of the older
  conversation, then removes every line carrying a claim the record will not
  support: file paths must appear in the transcript or in
  `agents/outcome.session_result` (derived from `ToolInvocation` +
  `UndoJournal`), and quoted spans must appear verbatim. A summary that
  survives verification empty is NOT shown — the deterministic recap keeps the
  job. Same honest-mock rule as skill distill: no real model ⇒ no summary,
  because this text is injected into the system prompt of every later turn and
  read back as authoritative. The two lanes differ by who is present: chat
  SIGNALS at `SUGGEST_AT` (0.70) and lets the user choose via
  `POST /chat/compact`, and only acts alone at `AUTO_AT` (~0.92); an agent run
  has nobody to ask, so it compacts itself at the ceiling and emits
  `context.compacted`. Pressure is measured on RAW demand (`raw_tokens`), never
  on the planned transcript — that fits by construction and so can never report
  the 70% the whole feature keys off. Coverage always restarts from the
  beginning and feeds the previous summary back in as `prior`; covering only
  the new blocks would silently discard everything the first summary said.
- **An assistant turn and its `role="tool"` results are ONE unit** (v1.152.0).
  Any code that trims, slices, or replays an agent transcript must move them
  together — a `tool_use` without its `tool_result` makes strict providers
  reject the ENTIRE conversation, so a context fix that splits them is worse
  than the overflow it prevents. `plan_agent_transcript` sacrifices in order:
  stale tool output → whole blocks (oldest first) → the task itself, clipped.
  The task is `messages[0]` and is never dropped; dropped work is summarized
  into the SYSTEM prompt, never injected as fake assistant turns.
- **NOTHING BLOCKING RUNS ON THE EVENT LOOP** (v1.153.1). The daemon is ONE
  asyncio loop, so a synchronous filesystem walk, a big file read, or any
  CPU-bound work inside a tool freezes every request in the app — and it does
  not look like a freeze. It looks like "Daemon offline": the dashboard's fetch
  times out, `lib/api.ts` maps a dead fetch to status 0, Retry issues another
  request onto the same blocked loop, and no threads load. That was a real
  four-hour outage on the user's install, diagnosed as 84% CPU with the
  MainThread parked in `pathlib.is_file` under `ListFilesTool.execute`. Any
  tool touching the filesystem or CPU goes through `asyncio.to_thread` (as
  `ShellTool` always did) AND is bounded — `tools/builtins._walk_files` caps
  entries, enforces a deadline, and prunes heavy dirs with `os.walk` (`rglob`
  cannot prune). Truncation is always REPORTED: a silently short listing reads
  as complete and the model then says a file does not exist.
- **Frozen-build verification**: anything touching native deps or subprocess
  spawning MUST be verified in the packaged daemon, not just source. The
  terminals feature shipped dead once because PyInstaller dropped
  `OpenConsole.exe`/`winpty-agent.exe` (now bundled in the .spec). New Python
  deps with native wheels (paramiko/bcrypt/nacl style) need spec entries.
- **`GET /sessions/{id}` returns `{session, transcript}` — NESTED.**
  `POST /sessions`, `POST /sessions/{id}/continue`, `/cancel`, `/rerun` return
  the session FLAT. `GET /sessions` returns `{sessions: [...]}`. Reading
  `.status` off the nested endpoint's top level silently yields undefined —
  this exact bug shipped twice (chat spinner-forever, Spotlight notification
  never firing). When in doubt, curl the endpoint.
- **Never let a real-provider failure return mock output.** The router
  (`providers/router.py`) raises for a failed real provider; mock fallback is
  ONLY for the offline/mock-default path. Fabricated "Done. Wrote RESULT.md"
  answers destroy trust instantly.
- **OpenAI ChatGPT-account backend retires model ids** (gpt-5-codex, gpt-5.1*,
  codex-mini-latest are all dead). The adapter
  (`providers/adapters/openai.py`) keeps a fallback ladder
  (`_CHATGPT_FALLBACK_MODELS`) + rejected-id cache. If OpenAI-via-subscription
  starts 400ing "model is not supported", extend the ladder — do NOT hardcode
  a single id anywhere.
- **One-shot agent utilities** (terminal assist, workflow builder) go through
  `_complete_with_retry` + `_one_shot_complete` in `daemon/app.py`: transient
  429/overloaded retries, then cross-provider failover. Keep new one-shot
  endpoints on that path.
- **Event payloads**: `agent.state_changed` carries `{from, to}` (NOT
  `state`); `agent.completed` `{run_id, ok, result}`; `tool.executed`
  `{tool, ok, mode}`. All tagged with `session_id`. Grep
  `core/events.py` + `agents/runtime.py` before consuming events.
- **Parallel agent work**: one file per agent, period. Shared files
  (`daemon/app.py`, `Sidebar.tsx`, `types.ts`, `ui.tsx`, `main.js`) are owned
  by the coordinating session. Don't run the full test suite while agents are
  mid-edit.
- **Windows dev shell**: PowerShell 5.1 — no `&&` chaining; Git Bash available.
  This machine lacks ffmpeg on PATH.

## Map (where things live)

- `src/iron_jarvis/daemon/` — `app.py` is factory + glue only (platform build,
  lifespan boot-rehydration + background loops, middleware, the shared `d` deps
  object); the ~240 endpoint handlers live in `routes/<domain>.py` (24 modules;
  search by route string ACROSS routes/), request models in `schemas.py`.
  Handlers reach shared state via `d.*`; tests monkeypatch `_MAX_UPLOAD_BYTES`
  and `_graceful_stop` on the app module, so routes access those via
  `_app.<name>` at call time — keep that pattern.
- `agents/` — orchestrator (sessions/reviews/continue), runtime (the
  perceive→act loop), dynamic agents. `providers/` — manager (per-provider
  factories), router (routing/failover), adapters/. `terminals/` — manager
  (+ restart-survival snapshot), session (scrollback), ai_clis (Launch
  detection), shells, backend (ConPTY/pipe/Fake).
- `context/` — `budget.plan_history`: the per-turn CHAT history planner (pure,
  offline, deterministic recap), consumed by both chat lanes.
  `agent_window.plan_agent_transcript`: the same job for an agent RUN, and a
  separate module because it protects the oldest message (the task) instead of
  the newest, and because tool pairs are indivisible. Shares `budget.py`'s
  token estimator and reserves so both lanes count tokens identically.
  `compaction.py` is the layer above both: thresholds, the structured prompt,
  and the verification pass that makes a model-written summary admissible.
  `store.py` caches one summary per covered prefix, CONTENT-ADDRESSED (a chat
  turn carries no thread id, an unsaved thread has none, and a forked thread
  should inherit its parent's summary for free) — and it is registered in
  `core.db._LATE_MODEL_MODULES`, without which its lazily-created table lands
  on fresh test DBs and on no real install (the v1.151.2 lesson).
- `profile/` — the user profile (ONE row): `models` (record), `store`
  (read-never-writes, partial save), `presets` (vocabularies; unknown key =
  free text), `language` (pure script-level leakage detector), `block`
  (the one renderer, bounded + never raises). `personas/builtins.py` holds the
  built-in catalog (importable — `app.py` still exposes it as `d._PERSONAS`).
- `skills/` — recursive discovery incl. `~/.claude/skills`, `~/.claude/plugins`,
  `~/.codex/skills` (`framework.py::external_skill_roots`); registry
  repopulates IN PLACE; skills inject into prompts (provider-agnostic), the
  agent-facing tools are just search/load. `workflows/` — store + engine
  (note: `POST /workflows/run` spawns the run in the background and returns the
  record immediately; it does NOT block until steps finish). `ltm/`,
  `memory/`, `comm/`, `computeruse/`, `sandbox/`, `scheduling/`.
- `documents/` — readers (extract_text: pdf/docx/xlsx/pptx/csv/text/images),
  writers (markdown-AWARE rich creation: headings/lists/tables/code become
  real structure in docx/pdf/pptx/html; xlsx multi-sheet dict + formulas),
  markdown.py (the shared block parser), tools (read/write/extract_pdf/
  convert_document). `tools/images.py` — view_image (vision via the router),
  image_convert/resize/info (Pillow). `tools/pixio.py` — generative media.
- `dashboard/app/<route>/page.tsx` per page; shared in `dashboard/components/`
  (`ui.tsx` primitives, `Sidebar.tsx` nav incl. Simple/Advanced mode,
  `ModelSwitcher.tsx` quality dial) and `dashboard/lib/` (`api.ts` fetch+auth,
  `useEvents.ts` WS, `types.ts`). Canvas editors: `components/workflow/`
  (agents.ts lives HERE, not lib/). Terminals page = free-form react-rnd
  canvas; pane header class `ij-term-drag` is the drag handle.
- `desktop/main.js` — supervisor (auto-restart children), tray, global
  hotkeys (Ctrl+Shift+J window, Ctrl+Shift+Space Spotlight), updater +
  update IPC, native clipboard IPC, media permissions. `preload.js` exposes
  `window.ironjarvis` (token, clipboard, update bridge).

## Verifying against the LIVE app (the user's running install)

```bash
tok=$(cat "C:/Users/VR/AppData/Roaming/Iron Jarvis/token.txt")
curl -s -H "Authorization: Bearer $tok" http://127.0.0.1:8787/health
# Event forensics (provider failures etc.):
#   SQLite: %APPDATA%/Iron Jarvis/.ironjarvis/ironjarvis.db, table eventrecord
```
Live-probing beats speculation — most "it's broken" reports this project has
seen were diagnosed in one curl (wrong version running, retired model id,
rate-limited provider, mock default).

## Direction (the product thesis)

Daily driver for creative + coding + office work, with high
interconnectedness. The known missing link is a **context spine**: a
first-class Project/workspace concept that chat, terminals, workflows, and
documents all tag into, so every agent call carries "what the user is working
on". Prefer features that CONNECT existing surfaces over new standalone
surfaces. Never trade trust for magic: honest errors beat fabricated output,
suggest-don't-act for anything autonomous, everything reviewable.
