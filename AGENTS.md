# Agents: read CLAUDE.md

The full operating manual for AI agents working on this repo — architecture
map, commands, release flow, and the hard rules learned from production
incidents — lives in [CLAUDE.md](./CLAUDE.md). It applies to every agent
(Claude Code, Codex, or otherwise), not just Claude.

Quick essentials if you read nothing else:

- Test: `uv run pytest -q --no-header` (offline, ~3150 tests, ~11 min). Build:
  `cd dashboard && pnpm build` (must reach 43/43 routes).
- **EVERY PUSH GETS A VERSION BUMP — no exceptions, not even a test-only or
  docs-only commit.** Bump `pyproject.toml` + `src/iron_jarvis/__init__.py` +
  `desktop/package.json` with ANCHORED edits, push to master; CI publishes the
  installer. Standing instruction from the user; see the Release flow section
  of CLAUDE.md for why the "but nothing user-visible changed" argument does not
  apply.
- `GET /sessions/{id}` is `{session, transcript}` (nested); the POST session
  endpoints return the session flat. This mismatch has shipped real bugs twice.
- Never let a failed real provider return mock output. Never verify
  native-dependency changes only from source — check the frozen build.
- Anything that talks to the user carries the identity spine (`profile/` +
  `personas/voice.py`) and budgets its history (`context/`). A new surface adds
  both in the same change — see CLAUDE.md's hard rules.
