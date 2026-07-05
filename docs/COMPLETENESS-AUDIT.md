# Iron Jarvis — Completeness, Deployment & Computer-Use Audit

A read-only auditor graded three areas; the swarm fixed the high/medium items.
**277 offline tests pass. 35 agent tools registered.**

## Area 1 — Completeness & tools

**Complete & tested:** orchestrator + multi-agent runtime, model router/providers
(+ OpenAI/Google/Anthropic via OAuth/key connections), tools + fail-closed
permissions, 4-layer memory, long-term memory (Obsidian/Notion/brain), skills,
documents (PDF/Word/Excel/PPT/CSV/MD/TXT), file search, secrets vault,
integrations, comm channels, webhooks, scheduler, workflows (+ n8n canvas),
learning loop, evaluation/observability, git worktree sessions + review, and the
dashboard.

| # | Finding | Fix |
|---|---------|-----|
| 1.1 | SPEC §18 "reach-outside" tools missing; `browser_use` defaulted **allow** with no tool behind it (pre-authorized an unbuilt capability). | Built `browse`/`web_extract`/`web_action` (computer use, gated). Flipped `browser_use` → **deny**. (`web_search`/`mcp_call`/`image_analysis` remain permission-only — documented as roadmap, default `ask`/`deny`.) |
| 1.2 | 8 tools registered + HTTP-exposed but not in any built-in agent's tool list (create_agent/spawn_agent/list_agents/notify/secret_*/integration_*). | Documented as **HTTP/dashboard/dynamic-agent only by design** (privileged config actions; not auto-granted to the default Builder/Planner). |
| 1.3 | 3 agent types (Researcher/Memory/Automation) fall back to Builder. | Known limitation — noted; Builder fallback is safe. (Define-or-remove is a small follow-up.) |

## Area 2 — VPS deployment readiness → **FIXED**

The auditor found it would *run* anywhere but was **unsafe to expose**. All HIGH
findings closed:

| # | Sev | Finding | Fix |
|---|-----|---------|-----|
| 2.1 | MED | No deploy artifacts. | `Dockerfile` + `Dockerfile.dashboard` + `docker-compose.yml` + `.dockerignore`; `render.yaml`, `railway.toml`, `.do/app.yaml`, AWS/Azure notes; **`DEPLOY.md`** with one-click buttons. |
| 2.2 | **HIGH** | Zero auth — anyone reaching the port could drive agents / write secrets / register SSRF webhooks. | **`TokenAuthMiddleware`** (env `IRONJARVIS_TOKEN`, constant-time): all routes require a bearer token when set; `/health` + OAuth callback exempt; WS guarded via `?token=`. Off = local dev. |
| 2.3 | **HIGH** | Unauthenticated arbitrary host file read (`/documents/read`, `/filesearch?root=`). | `IRONJARVIS_FS_ALLOWLIST` env — when set, reads are confined to allowlisted roots (403 otherwise). Unset = local UX preserved. |
| 2.4 | MED | CORS wide open. | `IRONJARVIS_CORS_ORIGINS` env allowlist (defaults open for local). |
| 2.6 | MED | Fernet key colocated with ciphertext under the data dir. | Documented in `DEPLOY.md`: mount `.ironjarvis/` as a volume + protect/back it up (externalizing the key to KMS noted as a follow-up). |
| 2.7 | LOW | Dashboard API base is build-time. | Documented; compose passes `NEXT_PUBLIC_IJ_API` as a build arg. Token support added to `lib/api.ts`. |

Verified live: token off → open; token on → 401 without header, 200 with, `/health`
exempt; fs allowlist → 403 outside roots. `docker compose config` validates.

## Area 3 — Computer use → **BUILT (opt-in)**

Was ~0% (a staged-but-unused playwright dep + a dead `browser_use` perm). Now a
full subsystem (`src/iron_jarvis/computeruse/`) implementing every best practice —
see **[`COMPUTER-USE.md`](COMPUTER-USE.md)** for the practice→code mapping.
**OFF by default**, gated by domain+action allowlists, human-approval for
credentials/payments/destructive actions, untrusted-content + prompt-injection
defense, programmatic verification, full tracing, step/retry budgets, and
checkpoints. 15 offline tests, one per practice.

## Bottom line
Iron Jarvis is now **complete for local use, safe to deploy to a VPS** (with a
token + the security checklist), and capable of **opt-in, safety-gated computer
use**. 277 tests green.
