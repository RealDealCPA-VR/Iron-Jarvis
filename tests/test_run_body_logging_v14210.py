"""v1.421.0 (Wave 1 review) — A BRAIN BLOCK THAT FAILS NEVER FAILS THE RUN.

``AgentRuntime._run_body`` builds the brain-profile block inside a ``try``
whose ``except`` logs a warning and carries on ("the brain must never break a
run", v1.406.0). But the same method later does ``import logging`` inside
the Guide's base-knowledge branch, and an ``import`` statement anywhere in a
function makes the name LOCAL to the whole function. So at the brain block's
``except`` the module-level ``logging`` was shadowed by a local that had not
been bound yet: the warning itself raised ``UnboundLocalError``, and the run
the comment promised to protect FAILED with it. Found by the Wave 1 builder,
reproduced and fixed by its reviewer: the function-local import is gone (the
module already imports ``logging`` at the top).

Driven through the REAL orchestrator + runtime on a real platform; only the
model is scripted and the brain block is made to raise.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.models import SessionStatus
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.adapters.mock import MockLLMAdapter

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src" / "iron_jarvis" / "agents" / "runtime.py"


def test_a_brain_block_that_raises_leaves_the_run_to_finish(tmp_path, monkeypatch):
    import iron_jarvis.brain.turn as brain_turn

    def broken(*a, **kw):
        raise RuntimeError("memory base unreachable")

    monkeypatch.setattr(brain_turn, "build_turn_bounded", broken)
    p = build_platform(str(tmp_path / "home"))

    class Scripted(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            return LLMResponse(text="All done.", finish_reason="stop")

    p.providers.register("mock", lambda model=None: Scripted())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session("say you are done", provider="mock", model="mock-1")
        return await orch.run_session(s.id)

    s = asyncio.run(go())
    assert s.status is SessionStatus.COMPLETED, (s.status, s.summary)
    assert "UnboundLocalError" not in str(s.summary or "")


def test_no_runtime_function_uses_logging_before_a_local_import_of_it():
    """The shape that caused it: a function-local ``import logging`` shadows
    the module's own import for the WHOLE function, so a ``logging.`` use
    that runs before that import raises. Read off the source: in every
    function that imports ``logging`` itself, no use of the name may come
    earlier in the text than the first such import (nested functions are
    their own scopes and are read on their own)."""
    tree = ast.parse(RUNTIME.read_text(encoding="utf-8"))

    def own_nodes(fn):
        """The nodes of ``fn``'s own scope: not inside a nested def/lambda/class."""
        stack = list(ast.iter_child_nodes(fn))
        while stack:
            node = stack.pop()
            yield node
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                continue
            stack.extend(ast.iter_child_nodes(node))

    offenders = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes = list(own_nodes(fn))
        imports = [
            n.lineno for n in nodes
            if isinstance(n, ast.Import) and any(a.name == "logging" and not a.asname for a in n.names)
        ]
        if not imports:
            continue
        first = min(imports)
        early = [
            n.lineno for n in nodes
            if isinstance(n, ast.Name) and n.id == "logging" and n.lineno < first
        ]
        if early:
            offenders.append(f"{fn.name}: used at {early} before `import logging` at {first}")
    assert offenders == [], offenders
