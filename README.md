# Iron Jarvis — Phases 0–3 (vertical slice)

A local-first AI operating system. This repo is the first vertical slice of the
plan in [`PLAN.md`](./PLAN.md): the spine that proves the architecture end-to-end,
**fully offline** (no network, no API keys) via a deterministic MockLLM.

What's implemented:

| Phase | Subsystem | Spec |
|------|-----------|------|
| 0 | Config (layered), Event Bus, SQLite persistence, structured logging, CLI + daemon | §8, §9, §30, §31 |
| 1 | Tool Registry, Permission Engine (fail-closed), built-in tools | §18, §19, §20 |
| 2 | Provider Manager, Model Router, Anthropic + MockLLM adapters, Browser Vault skeleton | §5, §6, §7, §10 |
| 3 | Agent Runtime + lifecycle, Sessions, isolated Workspaces, Orchestrator | §11, §13, §14, §15 |

## Quick start

```bash
uv sync                 # create venv + install deps
uv run ironjarvis demo  # offline end-to-end: agent → tool → workspace artifact
uv run pytest -q        # the offline test suite
```

Other commands:

```bash
uv run ironjarvis init           # scaffold .ironjarvis/ + config.toml
uv run ironjarvis run "build me a README"   # run one agent session
uv run ironjarvis tools          # list tools + permission modes
uv run ironjarvis sessions       # list past sessions
uv run ironjarvis serve          # start the daemon (FastAPI) for the dashboard
uv run ironjarvis status         # ping a running daemon
```

## Layout

```
src/iron_jarvis/
  core/        config, events, db, models, logging, ids        (Phase 0)
  tools/       base, permissions, registry, builtins           (Phase 1)
  providers/   manager, router, vault, adapters/{base,mock,anthropic}  (Phase 2)
  agents/      types, runtime, orchestrator                    (Phase 3)
  daemon/      app (FastAPI), client, cli                      (Phase 0/9)
  platform.py  composition root wiring everything together
```

Runtime state lives under `.ironjarvis/` (db, workspaces, browser vault,
memory, artifacts) — local-first and gitignored.

See [`SPEC.MD`](./SPEC.MD) (§10–33) and [`SPEC-SECTIONS-01-09.md`](./SPEC-SECTIONS-01-09.md)
(reconstructed §1–9, assumptions tagged).
