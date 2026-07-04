# Agents: read CLAUDE.md

The full operating manual for AI agents working on this repo — architecture
map, commands, release flow, and the hard rules learned from production
incidents — lives in [CLAUDE.md](./CLAUDE.md). It applies to every agent
(Claude Code, Codex, or otherwise), not just Claude.

Quick essentials if you read nothing else:

- Test: `uv run pytest -q --no-header` (offline, ~790 tests). Build:
  `cd dashboard && pnpm build` (must reach 34/35 routes).
- Ship = bump the version in `pyproject.toml` + `src/iron_jarvis/__init__.py`
  + `desktop/package.json` with ANCHORED edits, push to master; CI publishes
  the installer.
- `GET /sessions/{id}` is `{session, transcript}` (nested); the POST session
  endpoints return the session flat. This mismatch has shipped real bugs twice.
- Never let a failed real provider return mock output. Never verify
  native-dependency changes only from source — check the frozen build.
