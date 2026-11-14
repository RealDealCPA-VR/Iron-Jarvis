# Agents: read CLAUDE.md

The operating manual for AI agents working on this repo is
[CLAUDE.md](./CLAUDE.md). It is short (under 300 lines) and applies to every
agent (Claude Code, Codex, or any other), not just Claude. Read all of it.

The rules for one area of the code are in `docs/rules/<area>.md`. The table
"Before you touch an area" in CLAUDE.md names them. Each starts with a contents
list; read the list, then only the entries your change touches.

If you read nothing else:

- **Never run the full test suites on this PC.** Run the test files your
  change touches (`uv run pytest -q --no-header -n 4 tests/test_<area>*.py`);
  the whole suite runs on GitHub (`gh workflow run tests.yml --ref <branch>`).
- **Every push to master gets a version bump**, test-only and docs-only
  commits included: `uv run python scripts/bump_version.py OLD NEW`. Never edit
  a version by hand.
- **Never let a failed real provider return demo output**, and never move work
  to a provider the user did not choose.
- **Tests and scratch daemons never reach the owner's live services** (their
  own root, port, and no real keys).
- When two goals pull against each other, the "North star" list in CLAUDE.md
  decides, in its order.
