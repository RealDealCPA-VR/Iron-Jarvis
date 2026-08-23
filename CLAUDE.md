# Iron Jarvis — Agent Operating Manual

You are working on Iron Jarvis: a local-first AI operating system. One Python
daemon (FastAPI), one Next.js dashboard, one Electron desktop wrapper. The user
runs the PACKAGED desktop app daily — treat every change as production.

## The three processes

| Process | What | Port | Source |
|---|---|---|---|
| Daemon | FastAPI, all state + agents + tools | 127.0.0.1:8787 | `src/iron_jarvis/` |
| Dashboard | Next.js 15 (43 routes), arc-reactor-cyan aesthetic | 127.0.0.1:8788 | `dashboard/` |
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
# Backend tests (4800+, offline). ALWAYS run before shipping.
# Serial ~16min; -n auto runs one worker per core (~4.5min) and is what CI
# uses. Measured parallel-safe over three runs — identical pass counts.
uv run pytest -q --no-header -n auto
# Dashboard build (must show "Generating static pages (43/43)")
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
   bump, RUNS THE OFFLINE SUITE AS A GATE (the `suite` job; the installer job
   `needs:` it), PRE-CREATES the tag+release (electron-builder 422s otherwise),
   builds the frozen daemon + installer, publishes `Iron-Jarvis-Setup-X.Y.Z.exe`
   + blockmap + `latest.yml` (~35 min now that the gate runs first; a push with
   NO version bump still costs nothing — the gate script skips the suite too).
   **The gate is new in v1.177.0 and this file used to claim it already
   existed.** It did not: `Tests` and `Release` are separate workflows and ran
   CONCURRENTLY, so on v1.176.0 Release published a green installer in 8 minutes
   and Tests went red 16 minutes later — a red suite shipped to the user's
   daily driver, which auto-downloads. If you ever split these again, the
   installer must still not be reachable without a green suite.
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
  measured envelope (v1.201.0, probed/partial/tuned profiles only — seeded and
  trusted never speak here) → fleet probe → default) and report what they
  dropped. Do not reintroduce a fixed
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
- **Keep big results OUT of the context, don't trim them after** (v1.159.0).
  `repl/` is a per-session persistent Python namespace (a subprocess speaking
  newline-JSON, spawned by re-executing the app itself via the hidden
  `repl-worker` subcommand — `run_code` uses `shutil.which("python")` and so
  cannot run Python at all on a packaged install). Any tool call may carry
  `_store_as="name"`: `registry.invoke` strips it, binds the result into that
  session's namespace and returns a ONE-LINE RECEIPT, and the `repl` tool then
  reaches the value by name. A 5,000-entry listing becomes `len(files)` and a
  slice. This is the counterpart to the budget (v1.152.0) and compaction
  (v1.153.0): those decide what to throw away once a payload has arrived, this
  decides what never has to arrive. The value crosses as a JSON string LITERAL,
  never interpolated as code — a tool result must not be executable. `repl` is
  on the DENY FLOOR and defaults to `ask`: it runs model-written code AND the
  namespace persists for a whole session, so consent to one call is not consent
  to what accumulates. `_store_as` is advertised only on `VERBOSE_TOOLS` —
  putting it on all ~60 tools would spend more context than the feature saves.
- **Every chat reply is ACCOUNTABLE where the user is standing** (v1.165.0).
  Three honesty mechanisms existed and ALL THREE missed the mock-answer
  incident: the downgrade event fired (ledger had it), the banner rendered on
  the Overview only, and the "answered by X" chip suppressed itself on the
  default route (it compared against the EXPLICIT pick). The fix is
  server-side truth: `RouteResult` carries `requested` (`""` = no explicit
  pick — the default's name is NOT echoed, or "picked X" and "default is X"
  become indistinguishable) and `reason`
  (explicit/default/failover/prompted-tools/auto-tier/local-oracle/mock), with
  `reason=="mock"` iff `provider=="mock"` (`_disclosed_reason` — applied at
  EVERY terminal site in BOTH `complete()` and `stream()`, a lock-step pair).
  Both chat lanes emit `route:{requested,provider,model,reason}`; error paths
  emit NO route (a half-built route is an authoritative-looking lie).
  `documents` now also merges `result.created_paths` from every SUCCESSFUL
  tool (failed tools' paths are excluded to match the undo-ledger convention;
  RELATIVE paths are dropped, not resolved — resolving a lying tool's path
  against a guessed base could disclose the wrong file). The dashboard renders
  this as the TurnReceipt under each reply (mock/failover/mismatch warnings
  amber and visible WITHOUT expanding; "prompted-tools"/"auto-tier"/
  "local-oracle" stay quiet — user-configured automation is not substitution),
  the ArtifactsRail (per-conversation files: preview/download/copy/dismiss),
  and the PreflightNote above the composer (the active provider is
  known-unreachable BEFORE the user types; watches the explicit pick, else the
  DEFAULT — silent for "auto" and on first-poll-failure). The legacy
  viaProvider chip and "used:" footer render ONLY for pre-v1.165.0 messages
  (`!m.route`) — showing both would say everything twice.
- **A remote agent is EDITABLE, and an edit must never eat its credential**
  (v1.164.0). The row offered only Test and Delete, so one wrong character in a
  base URL meant re-entering the record. `PATCH /agents/remote/{name}` +
  `RemoteAgentRegistry.update` do a PARTIAL update. Do NOT "simplify" this back
  into a re-POST of the create body: `upsert` assigns EVERY column including
  `row.secret_name`, and the bearer token is stored encrypted and NEVER returned
  so no UI can prefill it — a re-post therefore sends an empty token and
  silently drops a working credential the user cannot retype, which is worse
  than the retyping it was meant to save. The token has THREE intents and
  conflating any two loses a secret: send one to replace, send NOTHING to keep,
  set `clear_token` to remove. An empty string is "I didn't type one", never
  "delete it" — mutation-proven, and the flag alone is not enough to assert
  (treating `""` as a new token leaves `has_credential` true while overwriting
  the vault entry with an empty string, so tests check the VALUE). `name` is
  deliberately immutable: panels and threads refer to a remote by name
  (`participantKey("remote", name)`), so renaming would orphan them silently.
- **A draft the user will SEND is fenced, boxed, and copied as RICH TEXT**
  (v1.161.0). `DRAFT_BLOCK` in `daemon/chat_turn.py` tells the model to wrap an
  email/message it drafts for the user in a ```email fence with a `Subject:`
  first line; `components/chat/DraftCard.tsx` renders that fence as a compact
  card whose Copy writes `text/html` AND `text/plain` in one go, so pasting
  into Outlook/Gmail keeps bold, lists and links instead of arriving as literal
  asterisks. The HTML is read off the RENDERED DOM (`cleanHtml`) rather than
  generated a second time — two renderers drift, and reading the node the user
  is looking at makes a mismatch impossible — with `class`/`style` stripped so
  the app's dark theme never lands in a composer. **STRIPPING ALONE IS NOT
  ENOUGH (v1.163.0):** semantic HTML pastes into Outlook FLAT, because Outlook
  renders through WORD's engine and Word gives a bare `<p>` a ZERO margin — the
  blank lines a browser shows come from the browser's own default stylesheet,
  which never crosses a clipboard (measured under a zero-margin reset: 0px
  paragraph gap before, 13px after). So the strip is FOLLOWED by `EMAIL_STYLES`
  — inline margins in POINTS, Word's unit — while colour/font stay out so the
  text adopts the composer's theme. `hardenLineBreaks` fixes the other half: a
  single newline is a SPACE in markdown, so a signature block pasted as one
  line. Both live behind `draftFromFence`, which `chat/page.tsx` and the tests
  BOTH call — they used to hold separate copies of that sequence, and a
  mutation deleting the real call site left every frontend test green, so the
  CALL SITE is asserted from Python (`tests/test_draft_spacing_v1163.py`).
  THIS IS A THREE-PARTY
  AGREEMENT and every party fails SILENTLY: the instruction must reach BOTH
  chat lanes (the streaming mirror in `routes/chat.py` is the one users watch),
  and `DRAFT_LANGS` must keep naming the same word as `DRAFT_BLOCK` or the
  model emits a fence nobody renders. `tests/test_draft_card_v1161.py` asserts
  both. A degraded copy SAYS SO ("Copied as text"): the desktop bridge
  (`clipboard:writeHtml`, one `clipboard.write` carrying both flavours — two
  calls clobber each other) is preferred because `navigator.clipboard` can be
  permission-gated in Electron, and claiming a rich copy that did not happen is
  a lie about the only thing the card does.
- **The REPL's writes are CONFINED; its reads are not** (v1.160.0). Every file
  tool routes through `core/fs_policy`; the `repl` child routed through nothing,
  and it was measurable — `read_file` refused the app's own Fernet key while a
  cell printed it, and a cell writing to an absolute path outside the workspace
  succeeded while `created_paths` said `[]`, because that diff only ever scans
  INSIDE the workspace. An invisible write is worse than an untidy one.
  `repl/worker.install_confinement` arms a `sys.addaudithook` before any cell
  runs; `repl/session.confinement_env` computes the policy from `fs_policy`
  (the worker is stdlib-only and cannot import it). READS STAY BROAD ON
  PURPOSE — the user's tax documents live all over the disk and a REPL that
  cannot open them is a worse tool — so only protected roots and an explicit
  `IRONJARVIS_FS_ALLOWLIST` restrict reading. WRITES pin to the workspace (the
  grounded project's folder when chat resolved one) plus a PRIVATE scratch dir:
  the whole system temp root would expose every other program's temp files, and
  with no redirect at all `tempfile` probes, fails, and silently falls back to
  `os.getcwd()` — filling the user's project with `tmpXXXX` files. Subprocess
  spawning and NEW `ctypes` loads are refused because each walks around the
  rule; `ctypes` is imported BEFORE the hook is armed, since Windows evaluates
  `windll.kernel32` at import time and a blanket refusal breaks `import ctypes`
  itself. `PYTHONDONTWRITEBYTECODE` is set for tidiness, NOT correctness —
  `importlib` swallows a refused `.pyc` write. Be honest in any doc you write
  about this: an audit hook inside the interpreter it polices is a guardrail
  against a careless model, NOT a sandbox against hostile code.
- **NOTHING BLOCKING RUNS ON THE EVENT LOOP** (v1.153.1). The daemon is ONE
  asyncio loop, so a synchronous filesystem walk, a big file read, or any
  CPU-bound work inside a tool freezes every request in the app — and it does
  not look like a freeze. It looks like "Daemon offline": the dashboard's fetch
  times out, `lib/api.ts` maps a dead fetch to status 0, Retry issues another
  request onto the same blocked loop, and no threads load. That was a real
  four-hour outage on the user's install, diagnosed as 84% CPU with the
  MainThread parked in `pathlib.is_file` under `ListFilesTool.execute`. Any
  tool touching the filesystem or CPU goes through `asyncio.to_thread` AND is
  bounded — `tools/builtins._walk_files` caps
  entries, enforces a deadline, and prunes heavy dirs with `os.walk` (`rglob`
  cannot prune). Truncation is always REPORTED: a silently short listing reads
  as complete and the model then says a file does not exist.
  **CHECK WHICH IMPLEMENTATION IS REGISTERED** (v1.175.0). This rule was
  obeyed by `tools/builtins.ShellTool` — and that class is DEAD CODE:
  `platform.py` registers `sandbox/shell_tool.SandboxedShellTool` under the
  same `shell` name, and for two years it ran `subprocess.run(shell=True)`
  straight on the loop. The protection sat in the shadowed copy where every
  reader (and this file) kept finding it. Both are offloaded now
  (`sandbox/shell_tool` hops ONCE for `manager.get()` + `sandbox.run()` —
  the Docker probe is a socket round-trip that hangs when Docker Desktop is
  wedged — and `tools/dynamic.CommandTool` for every `custom:*` tool), and
  `tests/test_event_loop_offload_v1175.py` asserts the worker thread AND that
  the loop kept ticking. When a rule cites a class, confirm that class is the
  one the registry actually holds.
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
- **PDF redaction edits the page; it never regenerates it** (v1.154.0).
  `documents/pdf_redact.py`: pikepdf (MPL-2.0) rewrites the content stream so
  the glyphs are genuinely deleted, pdfplumber supplies word geometry for true
  black boxes, and the written file is RE-READ to prove the values are gone —
  `RedactionUnverified` deletes the output rather than hand back a PDF that
  looks redacted and still carries the SSN. Only then does the old rebuild
  (`write_document` from extracted text) run, and the note says which path
  produced the file. Do not "simplify" this by trusting the transform: matching
  text in content streams is heuristic (values split across operators, odd
  encodings), and the verification is the only thing making it safe. pikepdf is
  a NATIVE wheel and has a `packaging/ironjarvis.spec` entry. Detection also
  runs PER LINE and the address pattern is case-insensitive — both were live
  defects: `\s` separators welded numbers across line breaks (6 of 7 "phone"
  hits on a real return were ownership percentages), and uppercase tax-document
  addresses were never matched at all.
- **A tool that writes a file says WHERE, absolutely** (v1.153.2), and a reply
  that CLAIMS a file is checked against the ledger. Two halves of one live
  report ("it told me it saved the file; the path it gave has no file"):
  (a) `redact_pii`/`write_document`/`convert_document` reported a
  WORKSPACE-RELATIVE path, which is a bare filename whenever the output lands
  in the workspace root — the model relays it and the user looks next to the
  source. They now report the absolute path and carry `abs_path` in `data`.
  (b) `_creation_honesty_note` keys off the USER's phrasing, so "redact this
  K-1" matched nothing and a reply announcing a saved file went unchecked —
  the ledger showed only `redact_scan`, which writes nothing.
  `_claimed_write_note` now judges the reply's own claim against what actually
  ran, in BOTH chat lanes. Note the destinations differ by lane: chat's tool
  workspace is `home/uploads`, an agent session's is its own session dir.
- **Never let a real-provider failure return mock output**, and since v1.162.0
  that includes a provider that is merely NOT CONNECTED. The router
  (`providers/router.py`) raises for a failed real provider; mock is ONLY for
  the offline/mock-default path (`wanted == "mock"` — a fresh install ships
  `default_provider = "mock"`, so first-run and the whole offline suite are
  untouched). The old code refused only an EXPLICIT pick under the strict pin
  and let the DEFAULT route fall through to mock — and chat sends no provider,
  so every chat turn took that branch. A user whose local fleet endpoint went
  down got the mock's scripted "Done. Wrote RESULT.md summarizing the task."
  and read it as finished work; the mock also EMITS a `write_file` call, so
  with a document tool armed the fabrication reaches DISK. `complete()` and
  `stream()` now both publish `provider.downgraded` (`used: "none"` — the
  banner still points at Connections) and then RAISE `_unavailable_error`,
  which names the provider. No automatic substitute even when other providers
  are connected: this box holds client tax documents, and moving a chat from a
  local endpoint to a cloud API is the user's privacy decision, not a routing
  fallback (asked and confirmed 2026-08-11). Mid-call failures were already
  guarded by `if wanted != "mock": raise` in both lanes — the gap was the
  PRE-RUN availability check only.
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
- **A frontend `waitFor` must wait for the THING YOU ARE ASSERTING**, never for
  a proxy signal that lands earlier. TWICE now this exact shape has cost a
  release: v1.177.1 (`JobPostCard` — waited for the POST to be recorded, then
  asserted the boxes had cleared, which happens in a LATER state update) and
  v1.178.0 (`canvas-v1170` — waited for `post("/workflows")`, then clicked Run
  and asserted the fork was unpinned; `setLoadedPin(null)` runs AFTER that
  awaited post). Both were green locally and on most CI runs — a contended
  runner is the only place the window is wide enough to see. The rule: put the
  real assertion INSIDE `waitFor`, or wait on a signal set at the END of the
  handler (the success note, the re-enabled button), not on the first
  observable side effect. A handler that does `await x` and then sets state has
  a window between the two, and CI will find it eventually.
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
- `repl/` — the session NAMESPACE (v1.159.0). `worker.py` is the child:
  stdlib-only (it is spawned from a frozen binary), newline-JSON on
  stdin/stdout, one persistent globals dict, output capped and truncation
  reported. `session.py` is the parent: one child per session, every
  blocking step through `asyncio.to_thread`, a reader thread so a deadline
  can actually be honoured, kill-on-timeout, and an honest `restarted` flag
  so a model is never told its variables survived when they did not.
  `tools/repl_tool.py` is the tool. Do not add a `get()`-first path: the
  registry creates a namespace on demand through `execute`, and a
  `get()`-first caller fails on the FIRST call of every session. Namespaces
  are keyed by `namespace_key(session_id, workspace)` — one per (session,
  FOLDER) pair, because chat runs every turn as session id `"chat"` while its
  workspace follows the grounded project, so keying on the id alone pinned the
  write root to whichever project opened first. The registry's PUBLIC surface
  (`get`/`dispose`/`sweep`/`session_ids`/`in`) still speaks session ids and
  covers every folder that session used; only the internal dict key is
  composite.
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
- `documents/` — `pdf_classify.py` is the per-PAGE scan router (v1.176.0,
  wraps the MIT `pdf-inspector` Rust extension). It answers "which pages of
  this PDF are scans", which `looks_scanned_pdf` structurally cannot: that
  asks ONE question of the whole file, so a native-text return with a scanned
  K-1 stapled in at page 12 reads as fully readable and the scan is silently
  invisible. THE ASYMMETRY IS THE SAFETY ARGUMENT and must survive any edit:
  `needs_ocr` ORs the classifier with the old heuristic and never ANDs, an
  empty plan falls back to harvesting rather than to harvesting nothing, and
  every entry point returns `None` (never raises) so an absent or broken
  extension degrades to exactly the v1.174.0 behaviour. A classifier may make
  the app read MORE of a client's document, never less. It is a NATIVE wheel:
  `packaging/ironjarvis.spec` entry + a RECOMMENDED `doctor` check, because a
  packaged build that dropped it would degrade silently forever (the pikepdf
  lesson). Cache version bumped to 2 — v1 records covered the first N pages
  and serving one for a mixed file would freeze that blindness in permanently.
  Also: readers (extract_text: pdf/docx/xlsx/pptx/csv/text/images),
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
