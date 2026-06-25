# IRON JARVIS — Implementation Plan

**Status:** ✅ EXECUTED — all 12 phases complete. 71 tests pass; dashboard prod
build green with live-data screenshots (`dashboard/proof/`). See `TASKS.md` for
the per-task proof. (Slice deviation honored: a single `iron_jarvis` uv package
with submodules instead of multi-distribution packages.)
**Stack:** Python core + Next.js (TypeScript) dashboard.
**Spec basis:** `SPEC.MD` (§10–33) + `SPEC-SECTIONS-01-09.md` (reconstructed, all
assumptions tagged).

---

## 0. TL;DR

Iron Jarvis is a full local-first AI operating system, not a single feature. This
plan breaks it into a **Python `uv` workspace** of focused packages plus a
**Next.js dashboard**, and a **12-phase build order** where every phase is
runnable and offline-testable (MockLLM / MockBrowser / native sandbox) before the
next is started — the same offline-first pattern used in your other platforms.

The one process that owns mutable state is the **Daemon** (FastAPI). The CLI and
Dashboard are clients that talk to it over REST + WebSocket. Everything internal
flows through an **Event Bus**.

---

## 1. Stack Decisions

### Python core
| Concern | Choice | Why |
|---|---|---|
| Runtime | Python 3.12, `uv` workspace | Matches your office-automations 5-pkg pattern; fast, reproducible |
| API / daemon | FastAPI + Uvicorn | Async, SSE/WebSocket for live transcripts & events |
| Schemas | Pydantic v2 | Tool I/O schemas (§19), config, events |
| Persistence | SQLModel/SQLAlchemy + Alembic, **SQLite default** | Zero-setup local-first; Postgres+pgvector optional (§22) |
| Vector / retrieval | **sqlite-vec default**, pluggable Chroma/LanceDB/Qdrant/pgvector | Local-first; no server needed to start (§22) |
| Browser providers | Playwright | Drives Claude.ai/ChatGPT/etc. sessions (§7, §10) |
| Secrets / vault | `keyring` (Win Credential Mgr / macOS Keychain / Secret Service) + Fernet-encrypted local fallback | §10 storage backends |
| Sandbox | **native subprocess default**, Docker/Podman SDK adapters | Windows box may lack Docker; native works out of the box (§16) |
| LLM adapters | Anthropic SDK (default `claude-opus-4-8`) + **MockLLM** | Offline tests/demos; consult `claude-api` skill at impl time for exact ids/params |
| Event bus | in-process asyncio pub/sub abstraction; Redis Streams/NATS adapters later | §31 |
| Scheduler | APScheduler | cron/triggers (§25) |

### TS dashboard
| Concern | Choice | Why |
|---|---|---|
| Framework | Next.js 15 (App Router) | §4 dashboard |
| ⚠️ Packaging | **`.npmrc` with `node-linker=hoisted` (pnpm)** | Known gotcha: pnpm symlinks crash Next App Router prod build; verify with a real Chrome prod build, not dev/curl |
| Data | TanStack Query + WebSocket client | REST queries + live event stream |
| UI | Tailwind + shadcn/ui | Fast, consistent |

---

## 2. Repository Layout

```text
iron-jarvis/
  pyproject.toml                 # uv workspace root
  packages/
    ij_core/                     # config(§8), event bus(§31), errors, base schemas, persistence
    ij_providers/                # provider manager(§5), model router(§6), adapters, browser vault(§7,§10)
    ij_tools/                    # tool registry(§19), built-in tools(§18), permission engine(§20)
    ij_agents/                   # agent runtime + lifecycle(§11,§13), agent types, orchestrator(§12)
    ij_memory/                   # layered memory(§21) + retrieval(§22)
    ij_sandbox/                  # sandbox manager + policies(§16,§17)
    ij_workflows/                # workflow engine(§24) + triggers(§25)
    ij_git/                      # git integration(§27) + review engine(§28)
    ij_eval/                     # evaluation(§29) + observability(§30)
    ij_daemon/                   # FastAPI app wiring everything + `ironjarvis` CLI(§9)
  dashboard/                     # Next.js 15 control center(§4)
  .ironjarvis/                   # runtime state (mostly gitignored)
    browser/{claude,chatgpt,codex,grok,gemini}/   # §10 vault
    memory/{MEMORY.md,architecture.md,...}        # §21 Layer 2
    workspaces/session-NNN/                       # §15
    skills/{tax,quickbooks,research}/SKILL.md      # §23
    artifacts/                                    # §26
  tests/                         # cross-package integration + offline demo
```

**[ASSUMPTION]** Package boundaries chosen so each phase below maps to ~1
package and can be built + tested in isolation.

---

## 3. Core Data Models

Primary persisted entities (SQLite tables / Pydantic models):

- **Project** — git repo root, config, memory ref.
- **Session** (§14) — `session_id, project_id, status, agent_assignments, provider_assignments, workspace_id, created_at`.
- **Workspace** (§15) — isolated dir, disposable flag, artifact links.
- **Agent** (§11) — identity, type, capabilities, provider pref, permissions, tools, skills, policies.
- **AgentRun** (§13) — lifecycle state machine row (`created→initializing→running→waiting→paused→delegating→reviewing→completed|cancelled|failed`), parent_run_id (for subagents §12).
- **Provider / ModelRoute** (§5–6) — provider class (api|browser), health, balance, capabilities; routing decision log.
- **VaultEntry** (§10) — provider key + **metadata only** (never plaintext secrets); points at keychain/encrypted blob.
- **Tool / ToolInvocation** (§18–19) — registry entry + per-call log with permission verdict.
- **PermissionRule** (§20) — scope (global|project|agent) → `allow|ask|deny`.
- **MemoryEntry** (§21) — layer, key, body, links.
- **Artifact** (§26) — versioned output, type, path.
- **Workflow / Trigger / WorkflowRun** (§24–25).
- **Event** (§31) — append-only event log feeding observability & replay.
- **Evaluation** (§29) — per-run metrics (completion, tool success, hallucination, cost, latency).
- **ReviewRequest** (§28) — diff set, test results, risk, decision.

---

## 4. Build Order (12 phases)

Each phase ends in a **runnable, offline-testable** state. ✅ = offline demo/tests green is the exit gate.

| Ph | Delivers | Spec | Exit gate |
|----|----------|------|-----------|
| 0 | Workspace scaffold, config layering (§8), CLI+daemon skeleton (§9), Event Bus (§31), SQLite persistence, structured logging (§30) | 8,9,30,31 | daemon boots, CLI talks to it, events flow ✅ |
| 1 | Tool Registry + Permission Engine + built-in tools (read/write/edit/list/grep/shell) | 18,19,20 | tools run behind fail-closed permission gate ✅ |
| 2 | Provider Manager + Model Router + Anthropic adapter + **MockLLM** + browser-vault skeleton | 5,6,7,10 | route a prompt through MockLLM offline ✅ |
| 3 | Agent Runtime + lifecycle + Session + Workspace (single agent loop) | 11,13,14,15 | one agent completes a task in an isolated workspace ✅ |
| 4 | Sandbox Manager (native first, Docker adapter) + policies | 16,17 | shell tool runs sandboxed with limits/timeouts ✅ |
| 5 | Memory System (4 layers) + retrieval (sqlite-vec) | 21,22 | agent reads/writes project + user memory ✅ |
| 6 | Multi-agent orchestration: Supervisor + subagents | 12 | supervisor delegates to Planner/Builder/Reviewer offline ✅ |
| 7 | Git integration + Review Engine | 27,28 | session → branch → diff → review → approve (no auto-merge) ✅ |
| 8 | Workflow Engine + Triggers + Artifacts | 24,25,26 | cron-triggered workflow produces a versioned artifact ✅ |
| 9 | Evaluation Engine | 29 | per-run metrics recorded + queryable ✅ |
| 10 | Dashboard wired to daemon (sessions, agent tree, review, observability) | 4 | review/approve a real session from the UI (real-Chrome prod build) ✅ |
| 11 | Skills Framework + example skills (tax, research) | 23 | agent loads and applies a SKILL.md ✅ |

**End-to-end demo (final):** a single command runs a session that plans →
builds in a sandbox → reviews → produces a diff + artifact, fully offline via
MockLLM, mirroring the way your other platforms ship a reproducible offline demo.

---

## 5. Cross-Cutting Conventions

- **Offline-first testing.** MockLLM + MockBrowser + native sandbox mean the
  whole system runs and tests green with no network and no API keys.
- **Fail-closed.** Permission/auth/sandbox unknowns deny, not allow.
- **Event-sourced observability.** Every meaningful action emits an Event (§31);
  logs/metrics/traces (§30) and Evaluation (§29) are consumers, not bolt-ons.
- **No auto-merge.** Agents stop at the diff (§27); humans approve.

---

## 6. Assumptions & Open Questions (please confirm)

1. **§1–9 reconstructed** — see `SPEC-SECTIONS-01-09.md`. Confirm or replace.
2. **Default model `claude-opus-4-8`**, MockLLM for offline. OK?
3. **SQLite + sqlite-vec default** (Postgres/pgvector optional). OK for a
   local-first single-user start?
4. **Native sandbox default on this Windows box** (Docker optional) — Docker may
   not be installed here. OK to start native and add Docker as an adapter?
5. **Scope of first build** — do you want me to execute **Phase 0 only** for your
   review, **Phases 0–3** (a thin vertical slice: daemon → tools → a working
   single agent), or the **whole plan** before checking back?
6. **Browser providers** — confirm logins stay user-driven (MFA never automated),
   which matches §10's "never stores MFA codes / auth secrets."
```

