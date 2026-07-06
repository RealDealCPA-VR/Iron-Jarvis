# Iron Jarvis — Agent Operating Manual

You are working on Iron Jarvis: a local-first AI operating system. One Python
daemon (FastAPI), one Next.js dashboard, one Electron desktop wrapper. The user
runs the PACKAGED desktop app daily — treat every change as production.

## The three processes

| Process | What | Port | Source |
|---|---|---|---|
| Daemon | FastAPI, all state + agents + tools | 127.0.0.1:8787 | `src/iron_jarvis/` |
| Dashboard | Next.js 15 (37 routes), arc-reactor-cyan aesthetic | 127.0.0.1:8788 | `dashboard/` |
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
# Backend tests (~1013, offline, ~2min). ALWAYS run before shipping.
uv run pytest -q --no-header
# Dashboard build (must show "Generating static pages (37/37)")
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

## Hard rules (each one was learned the expensive way)

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
  object); the ~170 endpoint handlers live in `routes/<domain>.py` (17 modules;
  search by route string ACROSS routes/), request models in `schemas.py`.
  Handlers reach shared state via `d.*`; tests monkeypatch `_MAX_UPLOAD_BYTES`
  and `_graceful_stop` on the app module, so routes access those via
  `_app.<name>` at call time — keep that pattern.
- `agents/` — orchestrator (sessions/reviews/continue), runtime (the
  perceive→act loop), dynamic agents. `providers/` — manager (per-provider
  factories), router (routing/failover), adapters/. `terminals/` — manager
  (+ restart-survival snapshot), session (scrollback), ai_clis (Launch
  detection), shells, backend (ConPTY/pipe/Fake).
- `skills/` — recursive discovery incl. `~/.claude/skills`, `~/.claude/plugins`,
  `~/.codex/skills` (`framework.py::external_skill_roots`); registry
  repopulates IN PLACE; skills inject into prompts (provider-agnostic), the
  agent-facing tools are just search/load. `workflows/` — store + engine
  (note: `POST /workflows/run` blocks until all steps finish). `ltm/`,
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
