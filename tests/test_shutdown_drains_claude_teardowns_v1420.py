"""v1.420.0: a claude-cli call's CLI exits in the BACKGROUND after its answer
left (Lane I, `claude_native/transport._release`), so the daemon's shutdown
waits for those teardowns — bounded — or a CLI child could outlive the
daemon it belongs to.

The pin reads the lifespan's source: the drain is called, inside the same
"never raises" shape as the Codex pool's stop, with a bound."""

from __future__ import annotations

import inspect
import re

from iron_jarvis.daemon import app as app_module
from iron_jarvis.providers.adapters.claude_native import transport


def _lifespan_source() -> str:
    return inspect.getsource(app_module)


def test_the_lifespan_drains_claude_cli_teardowns_at_shutdown_bounded():
    src = _lifespan_source()
    m = re.search(
        r"iron_jarvis\.providers\.adapters\.claude_native\.transport\"\)\s*\n"
        r"\s*if _claude_t is not None:\s*\n\s*try:\s*\n"
        r"\s*await asyncio\.wait_for\(_claude_t\.drain_teardowns\(\), timeout=(\d+(?:\.\d+)?)\)",
        src,
    )
    assert m, "the lifespan must drain the claude-cli teardowns at shutdown"
    assert 0 < float(m.group(1)) <= 30, "the drain is bounded (a stuck exit must not hold shutdown)"


def test_the_drain_sits_after_the_codex_pool_stop_and_before_the_repl_dispose():
    src = _lifespan_source()
    codex = src.index("_codex.POOL.stop_all")
    drain = src.index("_claude_t.drain_teardowns()")
    repl = src.index("_repl.dispose_all()")
    assert codex < drain < repl


def test_drain_teardowns_is_the_transports_public_wait():
    assert inspect.iscoroutinefunction(transport.drain_teardowns)
    assert transport.pending_teardowns() == 0
