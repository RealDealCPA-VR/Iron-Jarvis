<div align="center">

# ⚡ IRON JARVIS

[![Tests](https://github.com/RealDealCPA-VR/Iron-Jarvis/actions/workflows/tests.yml/badge.svg)](https://github.com/RealDealCPA-VR/Iron-Jarvis/actions/workflows/tests.yml)

### Your own local-first AI operating system.

**Agents that plan, build, review, schedule, remember, and wire themselves into your world — running on *your* machine, under *your* control.**

No cloud lock-in. No black boxes. Every action logged, every change reviewable, every secret encrypted on your disk.

</div>

---

> **TL;DR** — Iron Jarvis turns a fleet of AI agents into a real operating system: a supervisor delegates to specialist subagents, work runs in sandboxed git worktrees you approve before merge, a layered memory + long-term knowledge base keeps context, and a beautiful Next.js control center (with an **n8n-style workflow canvas** and **voice chat**) lets you drive it all. Runs **fully offline** with a deterministic mock model — bring your own Claude key when you want the real thing.

> **Platform support:** the packaged desktop app (installer + auto-update + multi-terminal ConPTY) ships for **Windows**. On macOS/Linux you can run the daemon + dashboard **from source** (`uv run ironjarvis serve` + `pnpm dev`); no installer is published for those yet.

<div align="center">

![Overview](dashboard/proof/overview-v2.png)

</div>

---

## 🔥 Why Iron Jarvis

You've used AI chat. This is the next thing: **AI that does the work and shows you exactly what it did.**

- **It's an OS, not a chatbot.** A Supervisor decomposes your goal, spins up specialist subagents (Planner, Builder, Reviewer, Researcher…), and each works in an isolated, disposable workspace.
- **You stay in control.** Every tool call passes a **fail-closed permission engine**. Risky actions ask first. Code changes land on a git branch and **never auto-merge** — you review the diff and approve.
- **It remembers.** Four-layer memory (session → project → user → org) plus pluggable **long-term memory** (Obsidian, Notion, or any markdown "brain").
- **It plugs into your world.** Encrypted secrets vault, integrations, Slack/Telegram/Discord alerts, inbound + outbound webhooks, cron-scheduled tasks, cross-drive file search.
- **Agents extend themselves.** They can create new agents, schedule their own jobs, add webhooks, write to long-term memory, and **build workflows you then see and edit on a visual canvas.**
- **Local-first & private.** SQLite by default, secrets encrypted at rest, sandboxed execution. The network is optional.

---

## ✨ Highlights

| | |
|---|---|
| 🧠 **Multi-agent orchestration** | Supervisor → subagents, isolated context, summarized results |
| 🔒 **Fail-closed permissions** | allow / ask / deny on every tool; `shell` stays locked down |
| 🌳 **Git-native sessions** | branch → work → diff → **you approve** → merge (no auto-merge) |
| 🧩 **n8n-style workflows** | drag step-nodes, wire them, run the graph — agents can build them too |
| 🎙️ **Voice chat** | hands-free in Chat: speak, it answers out loud — mic works in the desktop app too (daemon transcription) |
| 🗝️ **Encrypted secrets vault** | API keys / OAuth / tokens, shared by every subsystem, never shown to agents |
| 📅 **Scheduled tasks** | friendly repeat presets or a specific date/time — no cron syntax required |
| 🔭 **Observability** | live event stream, traces, per-run evaluation metrics |
| 🕰️ **Audit + time-travel** | one replayable **Activity** timeline of every action, tool, token & decision — and **undo** any reversible action (file writes, documents, notes, settings) with a since-changed guard so a rollback never clobbers newer work |
| 🖥️ **Beautiful dashboard** | arc-reactor dark UI, Kanban board, real-time everything |
| 📄 **Every file type** | read & write **PDF, Word, Excel, PowerPoint, CSV, Markdown, text** — like a colleague would |
| 🌱 **Self-correcting** | feedback + reflections become lessons — deduped and **distilled by a real model** into short reusable guidance injected into future runs |
| 🔌 **Connect a model in seconds** | a Connections page — paste an **API key** (Anthropic / OpenAI / Google), or just be logged into the **`claude` / `codex` CLI** and Iron Jarvis **inherits that subscription login automatically** (it never logs in for you) |
| 🦙 **Or stay fully local** | point it at a local **Ollama** / OpenAI-compatible endpoint — real intelligence, no cloud, no key |
| 🔎 **Web search + MCP** | a keyless `web_search` tool for agents, plus an **MCP client** to consume external MCP servers as native tools |
| 🛠️ **Edits itself** | an opt-in **Maintainer** agent can read/edit/test/fix Iron Jarvis's own source on a review-gated worktree |
| ⏹️ **Full session control** | stop, rerun, continue (multi-turn), delete, and export any run; per-run **token usage** is tracked |
| 🖥️ **Multi-terminal workspace** | tiled live terminals with a **+ tile** to add more + a **directory tree** to pick a project per terminal |
| 🪟 **Runs as a desktop app** | an Electron wrapper opens the whole thing in a native window |
| 🚀 **Guided first run** | a **code-signing-ready** Windows installer + an in-app onboarding wizard — connect a model, test your mic, run your first task, all before you leave the window |
| 🤖 **Opt-in computer use** | gated, DOM-first browser automation with human-approval for risky actions |
| ✅ **Tested offline, enforced in CI** | the whole suite runs green with no network and no API keys — the live count is on the [Tests badge](https://github.com/RealDealCPA-VR/Iron-Jarvis/actions/workflows/tests.yml), not hand-edited here |

<div align="center">

![Workflows](dashboard/proof/feat-workflows-n8n.png)

*The workflow canvas — agents can author these and you can drag the nodes around.*

</div>

---

## 📦 Installation

**Two ways to run it. Most people want Option A.**

### 🪟 Option A — the Windows desktop app (recommended · zero dependencies)

A single self-contained installer that bundles a PyInstaller-frozen daemon **and** the Next.js dashboard. **No Python, Node, uv, or pnpm needed on the machine** — install it and it opens in a native window.

#### 1 · Download

Go to the **[Releases page](https://github.com/RealDealCPA-VR/Iron-Jarvis/releases/latest)** and download the one file named **`Iron-Jarvis-Setup-<version>.exe`** (ignore `.blockmap` and `latest.yml` — those are for the auto-updater). Your browser may warn about an uncommonly-downloaded file — choose **Keep**.

#### 2 · Install

Run the installer. Windows SmartScreen will show *"Windows protected your PC"* because the app isn't code-signed yet ([why, and what signing would take → docs/SIGNING.md](docs/SIGNING.md)) — click **More info → Run anyway**. This happens **once per download**, not every launch. Pick an install folder (or keep the default) and finish; Iron Jarvis appears in the Start menu.

#### 3 · First launch

The app boots its own private daemon on loopback `127.0.0.1:8787` (token-protected, per-install — nothing is exposed to your network) and opens the dashboard in a native window. A **first-run guide** walks you through the two steps that matter:

1. **Connect a model** — on the **Connections** page, either paste an **API key** (Anthropic / OpenAI / Google), point it at a **local Ollama** (free, fully private), or simply be logged into the **`claude` / `codex` CLI** and Iron Jarvis inherits that subscription automatically. Until a model is available, a persistent **"Simulated mode"** banner reminds you that replies come from an offline mock.
2. **Run one real task** — type anything ("summarize the files on my desktop") and watch it work.

You can skip the guide and explore in demo mode — the banner keeps you honest.

#### 4 · Daily use

- **Closing the window doesn't quit.** Iron Jarvis minimizes to the **system tray** so schedules, webhooks, sentinels, and integrations keep running for weeks. Reopen with the tray icon or **Ctrl+Shift+J**; **Ctrl+Shift+Space** opens Spotlight (quick ask) from anywhere. To fully stop it: tray icon → **Quit Iron Jarvis**.
- **Updates are automatic.** The app checks GitHub Releases at launch and every 30 minutes, downloads new versions in the background, and installs only when you click **Restart to update** (tray menu, notification, or the Updates page).
- **Your data lives in `%APPDATA%\Iron Jarvis`** — config, the SQLite database, encrypted secrets, memory, and backups. It survives every update and reinstall. Uninstalling from Windows Settings removes the app but leaves that folder (delete it manually for a full wipe).

#### If something looks wrong

- *"Daemon offline" in the dashboard* → quit from the tray and relaunch; the app supervises and restarts its daemon automatically.
- *"Port 8787 already in use" on launch* → another program (or a second Iron Jarvis) owns the port; close it and relaunch.
- The **Settings → System health** card and `docs/`-linked doctor checks show exactly what's unhappy — errors are always shown honestly, never papered over.

#### Build the installer yourself (optional — needs Node 20 + pnpm + uv)

```powershell
pnpm --dir desktop run dist:full     # → desktop/release/Iron-Jarvis-Setup-<version>.exe
```
> Use **`dist:full`**, not bare `pnpm dist` (which ships a broken, daemon-less installer). Building locally needs **Windows Developer Mode** (Settings → Privacy & security → For developers → Developer Mode = On) or an elevated PowerShell — electron-builder unpacks a cache containing macOS symlinks. You only need this to *build* the installer, not to *run* it. Or let CI do it: bump the version in `pyproject.toml` + `src/iron_jarvis/__init__.py` + `desktop/package.json` and push to master — [`.github/workflows/release.yml`](.github/workflows/release.yml) builds and publishes the installer on a GitHub runner.

### 💻 Option B — run from source (for developers)

**Prerequisites:** **Python 3.12+**, **[uv](https://docs.astral.sh/uv/)**, **Node 20+**, **[pnpm](https://pnpm.io/)** (and **git** for git-native sessions).

```bash
git clone https://github.com/RealDealCPA-VR/Iron-Jarvis && cd Iron-Jarvis
uv run ironjarvis doctor                              # verify the machine is ready
uv sync --extra dev                                   # install the daemon + Python deps
cd dashboard && pnpm install && pnpm build && cd ..   # build the dashboard once
uv run ironjarvis up                                  # daemon :8787 + dashboard :3000, opens your browser
```

Prefer two terminals? `uv run ironjarvis serve` + `cd dashboard && pnpm start`. Want a native window over the source checkout? `cd desktop && pnpm install && pnpm start`.

> **Try it with zero setup / zero keys:** `uv run ironjarvis demo` runs end-to-end **offline** with a deterministic mock model, and `uv run pytest -q` runs the full offline suite — all green with no network.

### 🧠 One brain across every project

By default each working directory gets its **own** isolated `.ironjarvis/` home (DB, secrets, memory) — projects stay fully separate. To use **one shared brain — the same keys, memory, and history — across every project** you work in, point `IRONJARVIS_HOME` at a fixed location:

```bash
export IRONJARVIS_HOME="$HOME/.ironjarvis"                     # macOS / Linux
# PowerShell:  setx IRONJARVIS_HOME "$env:USERPROFILE\.ironjarvis"
```

Now `ironjarvis serve` from any folder shares one vault + memory while still operating on that folder's files. (The desktop app already pins one per-install home.)

Open the dashboard, hit **New Session**, and watch agents work in real time.

**Connect a real model** — in the dashboard's **Connections** page or the CLI:
```bash
uv run ironjarvis connect anthropic sk-ant-...   # stored encrypted in the vault
# the provider flips to "available" instantly — sessions route to it, no env vars
```
- **Use your Claude / ChatGPT subscription — by inheriting your CLI login.** Iron Jarvis **never performs an account login itself**. If you're already signed into the **Claude CLI** (`claude`) or **Codex CLI** (`codex`) on this machine, that subscription login is inherited automatically: a Claude/OpenAI request with no API key runs through the logged-in CLI, which owns the credential (Iron Jarvis never sees or stores it). This is the sanctioned way to use a Pro/Max or ChatGPT plan programmatically. Claude-backed agent sessions, workflows, and armed chat work the same on the inherited login as on an API key — just slightly slower (a fresh CLI process per step), and inline image analysis needs an API key.
- **API key** — paste one for **Anthropic, OpenAI, or Google** on the Connections page (or `ironjarvis connect anthropic sk-ant-...`); it's stored encrypted in the vault and used directly against the provider's API. A stored key always takes the direct-API path, unaffected by the CLI inheritance above.
- **Memory sources (Google Drive / Dropbox / OneDrive) and Gemini** connect with your **own** registered OAuth app (you bring the client id) — a standard OAuth 2.0 + PKCE flow used only for the accounts and files you point it at. Tokens live only in the encrypted vault.
- **Fully local?** Run a local **Ollama** (or any OpenAI-compatible server) and set `ollama_base_url` in config — no key, no network. Sessions can pick the `ollama` provider.

---

## ☁️ Deploy to your own server (optional)

Want it always-on? Ship it to a VPS in a couple of clicks — **full guide + one-click buttons in [`DEPLOY.md`](DEPLOY.md)** (Render, Railway, DigitalOcean, AWS, Azure).

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/RealDealCPA-VR/Iron-Jarvis) [![Deploy to DO](https://www.deploytodo.com/do-btn-blue.svg)](https://cloud.digitalocean.com/apps/new?repo=https://github.com/RealDealCPA-VR/Iron-Jarvis/tree/master)

*(The Render and DigitalOcean buttons carry this repo. **Railway** works too — follow the manual steps in [`DEPLOY.md`](DEPLOY.md); the generic "deploy on Railway" button doesn't carry a repo, so we link the guide instead of a one-click that wouldn't.)*

```bash
docker compose up        # daemon + dashboard, locally or on any Docker host
```

> 🔒 **Before exposing it publicly:** set `IRONJARVIS_TOKEN` (protects the API — it's RCE-by-design), serve over HTTPS, set `IRONJARVIS_CORS_ORIGINS` to your dashboard origin, persist `.ironjarvis/` on a volume, and keep **computer use off** unless you run it in a disposable VM. The `DEPLOY.md` security checklist walks through it.

---

## 📖 Using Iron Jarvis — a practical guide

### Update, recover & self-heal 🔄

**Stay current.**
- **Installed app:** it **auto-updates** from GitHub Releases on launch (electron-updater). Cut a release by pushing a version tag — `git tag v1.0.1 && git push --tags` — and CI builds + publishes the installer; the desktop app picks it up on next launch and rolls back automatically if a bad update won't boot.
- **From source:** `uv run ironjarvis self-update` (or the dashboard's **Updates** page) does `git pull` + `uv sync` + a dashboard rebuild, gated behind the test suite, then asks you to restart; `ironjarvis update-check` just reports whether you're behind upstream.

**When something breaks, fix it from within.** Iron Jarvis is built to self-correct — every recovery is a single command (or a dashboard button), and they work **even when the daemon won't boot**:

| Command | What it does |
|---|---|
| `ironjarvis doctor` | Diagnose the install (missing model, DB issue, secrets-key mismatch) with an actionable fix for each. |
| `ironjarvis repair` | Re-sync deps + check/recover the database — restores your latest backup if the DB is corrupt. |
| `ironjarvis rollback` | Undo a bad self-update: reset to the exact pre-update commit + re-sync. |
| `ironjarvis reset-config` | Restore a wedged `config.toml` to defaults (keeps a `.bak`). |
| `ironjarvis backup` · `ironjarvis restore <file>` | Snapshot / restore your whole state. An automatic backup also runs every 24h. |

On top of that it self-heals silently: a **corrupt database is quarantined at boot** and a fresh one created so the daemon **always starts**, a mistyped `config.toml` **falls back to defaults** instead of bricking boot, and interrupted **sessions, reviews, and schedules rehydrate** on restart.

> The packaged app bundles everything except the two opt-in *advanced* features — **computer-use** browser automation and the **Docker** sandbox — which need extra local setup; everything else (models, Ollama, memory, schedules, workflows, terminals…) works out of the box.

### Manage multiple terminals (and pick a project) 🖥️
**Dashboard → Terminals.** A tiled workspace of **live terminal sessions** — click the **`+` tile** to open another, so you can run/watch several agents or shells side by side. The **directory tree on the right** browses your computer (drives → folders, with git/python/node project badges); pick a folder and hit **"Open terminal here →"** to launch a terminal already `cd`'d into that project. Real PTYs (ConPTY on Windows), streamed over WebSocket.

### Run a session (and dictate it 🎙️)
**Dashboard → Sessions → New session.** Type a task — or **click an example chip**, **click the mic and speak it**, or **attach a file** for the agent to read. Pick an agent type (`builder`, `supervisor`, …) and a provider, then **Run**. The run streams its tool calls and events **live** on the session page. You can **Stop** a runaway run, **Rerun** it, **Continue** it (a multi-turn follow-up that reuses the workspace), **Export** the transcript (Markdown/JSON), or **Delete** it — and every run shows its **token usage**. Or from the terminal:
```bash
uv run ironjarvis run "Summarize the quarterly financials and draft an email"
uv run ironjarvis cancel <session-id>     # stop a background run
uv run ironjarvis rerun  <session-id>     # clone its inputs and run again
```

### Agents that build their own tools 🔧
Iron Jarvis can **grow new capabilities at runtime**. From **Dashboard → Tools** (or when an agent calls `tool_create`), you define a reusable tool: a name, typed parameters, and a command template whose `{param}` placeholders are filled from the call arguments — e.g. `wc_lines(file)` → `["wc", "-l", "{file}"]`. The tool is **persisted and instantly available to every future agent and session** (it's advertised to agents via a `custom:*` capability), so a tool one agent builds, the next agent can use. Each runs argv-style (no shell, so a parameter value can't inject commands) inside the session workspace, gated under its own `custom:<name>` permission (defaults to *ask* — you approve the first use, like `shell`). Manage them on the Tools page.

### Reuse tasks & watch your spend 📝💰
**Dashboard → Templates** is your library of saved prompts: name a frequent task once, then **Run** it to jump straight into a pre-filled New Session (no retyping). **Dashboard → Usage** charts your **token + dollar cost over time** — totals for the window, a by-day cost trend, and a per-provider/per-model breakdown — so a daily driver never surprises you on the bill. Press **⌘K / Ctrl+K** anywhere for the command palette to jump to any page or start a new session instantly.

### Settings, Self-development & Help
**Dashboard → Settings** edits the safe config keys (default model, sandbox runtime, self-dev, local Ollama endpoint…) without touching `config.toml`, and holds the **daemon access-token** box so you can log into a deployed instance without a rebuild. **Dashboard → Self-development** shows whether the Maintainer can edit Iron Jarvis's own source and starts a review-gated session. **Dashboard → Help** is an in-app guide to every subsystem. A **🔔 bell** in the top bar surfaces pending reviews and computer-use approvals.

### Watch it on the Kanban board
**Dashboard → Kanban.** Sessions flow across **Active → In Review → Completed / Failed** lanes. For git-native sessions, **drag a card from In Review onto Completed to approve** (merge) or onto Failed to reject. Approve/Reject buttons are on each review card too.

### Build a workflow visually (n8n-style)
**Dashboard → Workflows.** Drag step-nodes onto the canvas, wire `Trigger → Gather → Draft → Review`, set each node's agent + task (mic included), and hit **Run workflow** — each step spawns a session. **Load** a saved workflow to edit it, **Save** your own. *Agents can create workflows here too — when one does, it appears on your canvas to inspect and manipulate.*

### Let agents extend themselves
Agents have self-service tools, so a single high-level task can ripple out:
- `schedule_create` — an agent schedules a recurring job for itself
- `webhook_add` — an agent wires an inbound/outbound webhook
- `ltm_append` / `ltm_search` — an agent writes to & queries long-term memory
- `file_search` — an agent searches across your drives
- `workflow_create` — an agent authors a workflow **you then see and edit visually**
- `create_agent` / `spawn_agent` — agents that add more agents (now on the **same model** as the parent, not the mock)
- `web_search` — a keyless web search (DuckDuckGo by default; Brave with a vault key)
- **MCP tools** — any configured MCP server's tools appear as `mcp__<server>__<tool>` and are callable like native tools

### Fix Iron Jarvis with Iron Jarvis (self-development)
Opt-in (`self_dev_enabled` in config, or `--enable`): a **Maintainer** agent edits Iron Jarvis's *own* source on a git worktree. Changes are **review-gated — never auto-merged**; you approve the diff. Surfaces: `ironjarvis self-dev "fix X" --enable`, the **Self-development** dashboard page, or `POST /sessions {self_dev:true}`.

### Run it locally, no cloud (Ollama)
Set `ollama_base_url` in config (e.g. `http://localhost:11434/v1/chat/completions`) to route sessions through a local **Ollama** / OpenAI-compatible model — real intelligence with **no API key and no network**.

### Schedules (no cron required)
**Dashboard → Schedules.** Pick a **Repeat** preset (Hourly, Daily 9am, Weekdays 9am…) or choose **Once at a specific time** with a date picker. Each fire can run a workflow or emit an event.
```bash
uv run ironjarvis schedule-add nightly-books "0 2 * * *" --kind workflow
```

### Long-term memory (bring your own brain)
**Dashboard → Long-term Memory.** Search and append notes. **Add a custom source** — point it at an Obsidian vault / any markdown folder, or a Notion database (token from the vault). Custom sources show up in the search filter instantly.
```bash
uv run ironjarvis ltm-append "Client checklist" "EIN, prior returns, bank statements"
uv run ironjarvis ltm-search "onboarding"
```

### Secrets, integrations & channels
- **Secrets** — encrypted vault; values are write-only and never shown to agents or the UI.
- **Integrations** — enable / configure / **test** external services (each bound to a secret).
- **Channels** — connect Slack / Telegram / Discord; Iron Jarvis auto-alerts on review-requested, workflow-completed, and provider-failed events.

### Webhooks & file search
- **Webhooks** — **+ Add webhook** (inbound or outbound, HMAC-signed); inbound gives you a `POST /webhooks/{slug}` trigger URL.
- **File Search** — pick a **drive** (C:, D:, Home…) or a folder and search by name, content, or semantics.

> **CLI cheat sheet:** `init · serve · up · run · self-dev · demo · cancel · rerun · delete-session · backup · restore · doctor · repair · rollback · reset-config · rotate-keys · prune-events · prune-worktrees · migrate · metrics · evaluate · memory-search · ltm-search · ltm-append · file-search · schedule-add · schedules · secrets · integrations · agents · create-agent · notify · workflow · connect · status`

---

## 🏗️ Architecture

```
Dashboard (Next.js)  ──REST + WebSocket──►  Daemon (FastAPI)
                                              │  owns the Orchestrator + Event Bus
        ┌─────────────────────────────────────┼───────────────────────────────┐
   Orchestrator → Agent Runtime          Model Router → Provider Manager → Vault
        │                                      │
   Tool Registry + Permission Engine     Memory · Long-term Memory · Retrieval
        │                                      │
   Sandbox · Git/Review · Workflows · Scheduler · Webhooks · Integrations · Comm
        └──────────────── Event Bus · Evaluation · Observability ──────────────┘
                  Persistence: SQLite (WAL, self-healing additive migrations)
                  — the only backend today; Postgres+pgvector is a planned
                  engine-URL swap, not yet implemented.
```

```
src/iron_jarvis/
  core/        config, events, db, models, logging, ids
  tools/       registry, permissions (fail-closed), builtins
  providers/   manager, router, vault, adapters/{mock,anthropic}
  agents/      runtime, orchestrator, supervisor, dynamic agents
  sandbox/     native + Docker execution, §17 policies
  memory/      4-layer memory + numpy retrieval
  ltm/         Obsidian / Notion / markdown-brain connectors
  secrets/     Fernet-encrypted shared vault
  integrations/ comm/ webhooks/ scheduling/ filesearch/   (the "robust" layer)
  git/         worktree sessions + review engine
  workflows/   engine + triggers + persisted defs
  eval/        evaluation + observability
  daemon/      FastAPI app (REST + WS) + Typer CLI
dashboard/     Next.js 15 control center (Kanban, n8n canvas, voice)
```

Built from `SPEC.MD` (§10–33) + reconstructed `SPEC-SECTIONS-01-09.md`. See [`docs/`](docs/) for the build log and audit history.

---

## 🛡️ Security & privacy

- **Local-first.** All state lives under `.ironjarvis/` on your machine. The network is opt-in.
- **Fail-closed.** Unknown or unconfigured tool → denied. `shell` and other dangerous tools never auto-run headless.
- **Secrets encrypted at rest** (Fernet); agents can set/list names but **never read values**.
- **No auto-merge.** Agents stop at the diff; humans approve.
- **Sandboxed execution.** Structured file tools are workspace-confined; the **Docker** runtime adds a real filesystem/network/resource boundary (workspace-only mount, fail-closed network, CPU/memory/pid caps). The **native** runtime is best-effort (env scrubbing + timeouts only) — when an isolating policy is set, the shell tool prefers Docker and clearly flags any native fallback as unconfined. `shell` itself stays permission-gated (fail-closed headless).

---

## ✅ Proof it works

- **The full offline suite passes in CI on every push** (`uv run pytest -q`) — no network, no keys; the [Tests workflow](https://github.com/RealDealCPA-VR/Iron-Jarvis/actions/workflows/tests.yml) is the source of truth for the count.
- Live daemon serves every endpoint; the dashboard has a clean production build.
- Real-Chrome screenshots of every page live in [`dashboard/proof/`](dashboard/proof/).

<div align="center">

![Kanban](dashboard/proof/kanban.png)

</div>

---

## 🗺️ Roadmap

A mobile companion, distributed agent clusters, a skills/agent marketplace, and team-shared org memory. The foundation is built — everything else stacks on top.

---

<div align="center">

**Iron Jarvis** — *the AI operating system you actually own.*

Built with [Claude Code](https://claude.com/claude-code).

</div>
