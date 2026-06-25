# IRON JARVIS — Sections 1–9 (RECONSTRUCTED)

> ⚠️ **Status: RECONSTRUCTED / ASSUMED.** `SPEC.MD` begins at §10 and is titled
> *"Continuation of Master Architecture."* Sections 1–9 were not provided. The
> content below is **inferred** to be consistent with §10–33 and with §33's
> final component list (Dashboard, Orchestrator, Agent Runtime, Model Router,
> Provider Manager, Sandbox Manager, Memory System, Tool Registry, Workflow
> Engine, Review Engine, Git Integration, Evaluation Engine, Observability).
> Every assumption is tagged **[ASSUMPTION]**. Replace this file with the real
> §1–9 if/when you have them.

---

# 1. Vision & Product Definition

Iron Jarvis is a **local-first AI operating system** that orchestrates agents,
models, tools, memory, and workflows to autonomously perform meaningful work
while preserving complete user ownership, transparency, and control. (This is
the same definition stated in §33; §1 is its full statement, §33 its recap.)

**[ASSUMPTION]** The product targets a single power-user on their own machine
first (Windows/macOS/Linux), with optional team/org expansion later (§21 Layer 4,
§32). It is not a hosted SaaS; the user's machine is the trust boundary.

---

# 2. Design Principles

1. **Local-first.** All state (sessions, memory, vault, artifacts) lives on the
   user's machine under `.ironjarvis/`. The network is optional, not required.
2. **User ownership & transparency.** Every action is logged, traceable (§30),
   and reviewable (§28). Nothing is hidden from the user.
3. **Fail-closed permissions.** The Permission Engine (§20) gates every tool
   call; unknown → treated as `ask`/`deny`, never silently `allow`.
4. **Provider-agnostic.** Models are reached through adapters behind the Model
   Router (§6). No subsystem hard-codes a vendor.
5. **Disposable workspaces.** Work happens in isolated, throwaway workspaces
   (§15); only explicitly approved changes become permanent (§27).
6. **Git-native.** Every unit of persistent work is a branch + diff the user
   reviews and merges; agents never auto-merge (§27).
7. **Pluggable everything.** Storage, vector DB, sandbox runtime, event bus, and
   providers are interfaces with swappable implementations.

---

# 3. System Architecture Overview

**[ASSUMPTION]** The layered stack (from §33), top to bottom, with data flow:

```text
┌──────────────────────────────────────────────────────────┐
│  Dashboard (Next.js)        ← REST + WebSocket/SSE         │  §4
├──────────────────────────────────────────────────────────┤
│  Daemon / API (FastAPI)     ← single long-running process  │  §9
├──────────────────────────────────────────────────────────┤
│  Orchestrator   ──drives──►  Agent Runtime                 │  §11–13
│       │                          │                         │
│       │        ┌─────────────────┼──────────────────┐      │
│       ▼        ▼                 ▼                  ▼      │
│  Workflow   Tool Registry   Model Router       Memory     │  §18–25
│  Engine     + Permissions   + Provider Mgr     System     │
│       │          │               │                 │      │
│       ▼          ▼               ▼                 ▼      │
│  Sandbox Mgr   Git/Review    Browser Vault     Retrieval  │  §10,16,27
├──────────────────────────────────────────────────────────┤
│  Event Bus  ·  Evaluation  ·  Observability               │  §29–31
├──────────────────────────────────────────────────────────┤
│  Persistence: SQLite (default) / Postgres+pgvector        │  §22
└──────────────────────────────────────────────────────────┘
```

Everything communicates through the **Event Bus** (§31). The Dashboard never
touches subsystems directly — it talks to the Daemon API, which owns the
Orchestrator and emits events the Dashboard subscribes to.

---

# 4. Dashboard

**[ASSUMPTION]** A Next.js 15 control center (the "+ Dashboard" of §33). Views:

- **Sessions** — list/create sessions (§14), live transcript stream.
- **Agent tree** — the supervisor → subagent hierarchy (§12) with live state
  (§13 lifecycle states) per agent.
- **Review** — modified files, inline diffs, test results, risk, tool history,
  approve/reject/PR/export (§28).
- **Memory browser** — view/edit the four memory layers (§21).
- **Workflows** — list, edit, trigger, and view runs (§24–25).
- **Providers & Vault** — provider health/balances, browser-session login status
  (§5, §10) — login is user-driven (MFA never automated).
- **Observability** — logs, metrics, traces, event stream (§29–30).

Communicates over REST (commands/queries) + WebSocket/SSE (live events,
transcripts, diffs).

---

# 5. Provider Manager

**[ASSUMPTION]** Manages all model providers and their health. Two provider
classes:

- **API providers** — reached with an API key (Anthropic, OpenAI, etc.). Key
  stored in OS keychain / encrypted vault (§10 storage backends).
- **Subscription / browser providers** — reached by driving the provider's web
  UI with a logged-in browser session from the Browser Session Vault (§10):
  `claude/ chatgpt/ codex/ grok/ gemini/`. Used when the user has a subscription
  but no API key.

Responsibilities: registration, health checks, rate-limit / balance tracking,
capability advertisement (context window, modalities, tool-use support), and
failover signaling to the Model Router (§6).

---

# 6. Model Router

**[ASSUMPTION]** Selects a `(provider, model)` for each request based on:

- **Capability** required (context size, vision, tool-use, JSON mode).
- **Policy** (per-agent / per-project preferred + forbidden models, §21 Layer 3).
- **Cost & latency** budgets.
- **Availability** (provider health + balance from §5; reroute on lock/overload).

Routing decisions and outcomes feed the Evaluation Engine (§29) so routing
improves over time. **[ASSUMPTION]** Default model = the latest Claude (model id
`claude-opus-4-8`); an offline **MockLLM** adapter exists for tests/demos.

---

# 7. Provider Authentication & Subscription Access

**[ASSUMPTION]** How providers authenticate:

- **API providers**: key retrieved from secure storage at call time; never
  written to disk in plaintext (consistent with §10's "never stores plaintext
  passwords / auth secrets outside secure storage").
- **Browser providers**: Playwright loads the encrypted session (cookies / local
  / session storage / fingerprint) from the vault (§10). If the session is
  expired, Iron Jarvis surfaces a **user-driven login** in the Dashboard. **MFA
  codes are entered by the human and never stored or automated.**
- Sessions are refreshed opportunistically; failures fail closed (request is
  blocked, event `provider.failed` emitted, §31).

---

# 8. Configuration System

**[ASSUMPTION]** Layered configuration mirroring the permission-override model
(§20: global / per-project / per-agent). Precedence: **agent > project > global**.

- Global: `~/.ironjarvis/config.toml`
- Project: `<repo>/.ironjarvis/config.toml`
- Per-agent: agent definition overrides.

Config covers: default models, permission defaults, sandbox policy (§17),
retrieval backend (§22), event-bus implementation (§31), and provider
enablement. Format: **TOML** (matches the trigger example in §25).

---

# 9. Entry Points — CLI & Daemon

**[ASSUMPTION]** Two entry points:

- **`ironjarvis` CLI** — start/stop the daemon, create/run sessions and
  workflows, inspect state, approve reviews from the terminal.
- **Daemon** — a single long-running FastAPI process hosting the Orchestrator,
  Event Bus, scheduler/triggers (§25), and the API the Dashboard consumes. The
  daemon is the one process that owns mutable global state; the CLI and Dashboard
  are clients.
