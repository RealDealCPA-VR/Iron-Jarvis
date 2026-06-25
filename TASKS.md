# Iron Jarvis — Completion Task List (Phases 4–11)

> ## ✅ COMPLETE — all 14 tasks done & verified with proof
> - Phases 0–11 implemented. **71 Python tests pass** (23 original + 43 module + 5 integration).
> - 8 subsystems built by parallel agents, each green in isolation, then wired
>   centrally (`platform.py`, daemon, CLI) and re-verified with no regressions.
> - **Live daemon** serves every endpoint over HTTP (eval/metrics/memory/skills/
>   workflows/review/vault); **offline demo** exercises all subsystems.
> - **Dashboard** has a clean Next.js production build; real-Chrome screenshots in
>   `dashboard/proof/` confirm it renders live daemon data.

Goal: complete the platform. Phases 0–3 are done & verified (23 tests green).
This list decomposes the remainder into **agent-sized tasks** (each a focused
module + its own tests, well under 60% of an agent's context). Each task names
the files it **owns** (disjoint, so agents run in parallel without conflict) and
its **proof** (the verification that closes it). Shared files (`platform.py`,
`daemon/app.py`, `cli.py`, `pyproject.toml`) are wired **centrally** by the
orchestrating session, never by fan-out agents.

Legend: ⬜ todo · 🔄 in progress · ✅ verified with proof

---

## Central / integration (orchestrator-owned)
- **C1** Add deps (`psutil`, `numpy`, `apscheduler`, `pyyaml`, optional `docker`) + `uv sync`.
- **C2** `git init` + baseline commit (prerequisite for Phase 7).
- **C3** Expand daemon API: memory, eval/metrics, traces, review, workflows, artifacts, skills, vault endpoints.
- **C4** Wire every subsystem into `build_platform()` + register all new tools.
- **C5** Expand CLI with new subcommands.
- **C6** Full integrated suite green + expanded `ironjarvis demo` + live-daemon proof.
- **C7** Update README/PLAN/memory with completion + proof.

---

## WAVE A — independent backend modules (parallel fan-out)

### Phase 4 — Sandbox Manager (§16, §17)  → owns `src/iron_jarvis/sandbox/`
- **T4.1** `policy.py` (SandboxPolicy from config §17), `base.py` (Sandbox iface + SandboxResult), `native.py` (subprocess executor: workspace cwd, timeout, env scrub for `modify_env=deny`, output capture), `manager.py` (runtime select).
- **T4.2** `docker_runtime.py` (lazy docker SDK: container run, workspace mount, network off when policy denies, cpu/mem limits). Guarded; skips cleanly if Docker absent.
- **T4.3** `shell_tool.py` (`SandboxedShellTool` routing through SandboxManager).
- **Proof** `tests/test_sandbox.py`: native run captures stdout + returncode; timeout enforced; env-scrub verified; `internet=deny` honored; Docker path skipped-if-unavailable. Tests green.

### Phase 5 — Memory + Retrieval (§21, §22)  → owns `src/iron_jarvis/memory/`
- **T5.1** `models.py` (MemoryRecord table), `layers.py` (session/project/user/org read+write; project↔`.ironjarvis/memory/*.md`, user↔`~/.ironjarvis/memory/`).
- **T5.2** `embeddings.py` (deterministic offline MockEmbedding), `retrieval.py` (SQLite + numpy cosine; pluggable backend iface).
- **T5.3** `tools.py` (`memory_read`, `memory_write`, `memory_search`).
- **Proof** `tests/test_memory.py`: write→semantic search returns it ranked; layer precedence; tools work via registry. Green.

### Phase 9 — Evaluation + Observability (§29, §30)  → owns `src/iron_jarvis/eval/`
- **T9.1** `models.py` (Evaluation table), `evaluation.py` (per-run: completion, tool_success_rate, latency, step_count, cost placeholder, review_acceptance).
- **T9.2** `observability.py` (`metrics()` aggregate, `traces(session_id)` from EventRecord).
- **Proof** `tests/test_eval.py`: after a recorded run, evaluation computed (latency>0, tool_success_rate∈[0,1], completion correct); traces reconstruct event order. Green.

### Phase 11 — Skills Framework (§23)  → owns `src/iron_jarvis/skills/`
- **T11.1** `loader.py` (parse `SKILL.md` frontmatter via pyyaml → Skill: instructions/examples/scripts/templates), `framework.py` (registry, inject into agent system prompt, search by description).
- **T11.2** `tools.py` (`skill_search`, `skill_load`); example skills `assets/skills/{research,financial-analysis}/SKILL.md`.
- **Proof** `tests/test_skills.py`: load SKILL.md → instructions present in effective prompt; search finds by description. Green.

### Phase 8a — Artifacts (§26)  → owns `src/iron_jarvis/artifacts/`
- **T8.3** `store.py` (versioned ArtifactStore under `.ironjarvis/artifacts/`: save→versioned path, list versions, read, types).
- **Proof** `tests/test_artifacts.py`: save same name twice → two versions; retrieve latest + specific version. Green.

---

## WAVE B — orchestration-dependent modules (parallel fan-out)

### Phase 6 — Multi-Agent Orchestration (§12)  → owns `src/iron_jarvis/agents/supervisor.py`, `agents/delegate_tool.py`
- **T6.1** Supervisor decomposes a task and delegates to subagents (own context, own provider, summarized return, no user contact §12); `delegate` tool spawns a subagent run with `parent_id`.
- **T6.2** `run_supervised(platform, session)` entrypoint (orchestrator calls it; no edit to orchestrator.py by the agent).
- **Proof** `tests/test_multiagent.py`: supervisor (scripted MockLLM) delegates to a builder subagent; AgentRun rows show parent→child; subagent result summarized up. Green.

### Phase 7 — Git Integration + Review (§27, §28)  → owns `src/iron_jarvis/git/`
- **T7.1** `integration.py` (GitWorkspace: clone/worktree project repo into session workspace on branch `ironjarvis/session-<ts>-<slug>`; diff vs base; **no auto-merge**; export patch; merge only on explicit approve).
- **T7.2** `review.py` (ReviewRequest: modified files, inline diffs, risk heuristic, tool history, summary §28; approve→merge, reject→discard, export_patch).
- **Proof** `tests/test_git_review.py`: temp project repo → session branch → edit → diff lists file → review object populated → approve merges, reject leaves base untouched; assert merge requires explicit approve. Green.

### Phase 8b — Workflow Engine + Triggers (§24, §25)  → owns `src/iron_jarvis/workflows/`
- **T8.1** `models.py` (Workflow, WorkflowRun), `engine.py` (load TOML workflow; execute steps→sessions; collect outputs/notifications).
- **T8.2** `triggers.py` (manual + cron via APScheduler; parse `[[triggers]] schedule`; webhook/file/email/calendar/api stubs).
- **Proof** `tests/test_workflows.py`: TOML workflow w/ manual trigger → run → produces artifact + WorkflowRun row; cron schedule parses and registers. Green.

---

## WAVE C — central integration & full verification (orchestrator)
- C3, C4, C5 above. Then **C6**: full `uv run pytest` green; `ironjarvis demo` exercises sandbox+memory+skills+eval+git+review+workflow+artifact end-to-end offline; live daemon serves all new endpoints (curl proof).

---

## WAVE D — Dashboard (§4)  → owns `dashboard/`
- **T10.1** Next.js 15 (App Router) + `.npmrc` `node-linker=hoisted` + Tailwind; typed API client + WS event hook.
- **T10.2** Views: Sessions (list/create + live transcript), Agent tree, Review (diff + approve/reject), Providers & Vault, Observability (event stream + metrics), Memory browser.
- **Proof** `pnpm build` succeeds (prod, hoisted); puppeteer-core + real Chrome screenshot of the dashboard rendering live data from the daemon (per the Windows headless-screenshot method). Screenshot saved as proof.

---

## Definition of done
Every task ✅ with proof: the **full test suite green**, the **offline demo**
exercising all subsystems, the **live daemon** answering every endpoint, and a
**real-Chrome screenshot** of the working dashboard.
