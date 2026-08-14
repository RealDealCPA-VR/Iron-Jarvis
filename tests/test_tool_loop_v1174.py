"""The tool loop tells the truth and stops paying twice (v1.174.0).

THE RUN THIS FILE IS BUILT ON. A user asked an agent to rename the files in
`Organziation of messy tax documents` (26 entries) to names matching their
contents. It ended `FAILED — stopped: reached max steps before completion`
with ZERO files renamed, having spent 12 steps on 18 tool calls:

    step 1      list_files
    steps 2-6   five shell calls, each returning the whole string "exit 1"
    steps 7-12  extract_pdf x6 (one file read twice)
    steps 13-18 read_document x6 (three of them ALREADY read at steps 9/11/12)

Three separate defects are visible in that table, and this file pins one test
section to each:

1. THE MODEL WAS NEVER TOLD WHY. `ShellTool` captured stderr into `output`
   and both `registry._record` and the runtime read `error` alone on failure,
   so five commands' worth of diagnostics were thrown away at the door. A model
   that cannot see the error can only guess again.
2. NOTHING NOTICED THE REPETITION. The same failing call, over and over, cost
   the run its budget while telling it nothing new.
3. THE SAME FILES WERE READ TWICE, and once ACROSS TOOLS — `extract_pdf` then
   `read_document` on the same PDF, which is the same work through a different
   door. Twelve of eighteen calls re-derived text the run already had.

Plus contract 4's runtime half: a session may carry its own step budget.

WHAT THESE TESTS ARE CAREFUL ABOUT. Every assertion here is on a VALUE the
model or the ledger actually receives — the transcript message, the persisted
`ToolInvocation.output`, the number of times a tool's `execute` really ran —
never on an internal flag. A cache that "records a hit" while re-reading the
file, or a breaker that sets a bit but still lets the call through, must fail.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlmodel import select

from iron_jarvis.agents.runtime import (
    _BREAKER_NOTE_AT,
    _BREAKER_REFUSE_AT,
    AgentRuntime,
    call_signature,
    resolve_max_steps,
)
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, AgentType, ToolInvocation
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.tools.base import Reversibility, Tool, ToolContext, ToolResult
from iron_jarvis.tools.registry import (
    CACHEABLE_READ_TOOLS,
    _FAILURE_DIAGNOSTIC_CHARS,
    _MUTATING_TOOLS,
    compose_failure_text,
)


def _ctx(platform, workspace: Path, session_id: str = "s1", run_id: str = "r1"):
    workspace.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=workspace,
        session_id=session_id,
        agent_run_id=run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _rows(platform) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        return list(db.exec(select(ToolInvocation)))


# =============================================================================
# 1. CONTRACT 1 — a failure carries its real diagnostic
# =============================================================================


class _NoisyFailureTool(Tool):
    """The `shell` shape: the cause is in `output`, the code is in `error`."""

    name = "noisy_fail"
    permission_key = "read_file"  # borrow an allow-by-default key
    description = "test double"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, output: str, error: str | None = "exit 1", ok: bool = False):
        self._output, self._error, self._ok = output, error, ok

    async def execute(self, args, ctx):  # noqa: D102
        return ToolResult(
            ok=self._ok, output=self._output, error=self._error,
            data={"returncode": 1},
        )


def test_compose_keeps_a_bare_error_byte_identical():
    """The additive guarantee: a tool with no captured output is untouched."""
    assert compose_failure_text("exit 1", "") == "exit 1"
    assert compose_failure_text("no such file: a.txt", None) == "no such file: a.txt"


def test_compose_carries_both_halves():
    got = compose_failure_text("exit 1", "'rename' is not recognized")
    assert "exit 1" in got
    assert "'rename' is not recognized" in got


def test_compose_is_bounded_and_keeps_the_TAIL():
    """A command says WHY it failed on its LAST lines; the head is progress
    noise. A bound that kept the head would be a bound that keeps the wrong
    2000 characters."""
    body = "\n".join(f"line {i}" for i in range(5000)) + "\nfatal: the real cause"
    got = compose_failure_text("exit 2", body)

    assert len(got) <= _FAILURE_DIAGNOSTIC_CHARS
    assert "fatal: the real cause" in got  # the tail survived
    assert "line 0" not in got             # the head did not
    assert "[earlier output dropped]" in got  # ...and it says so


def test_compose_never_loses_the_error_to_a_huge_output():
    got = compose_failure_text("exit 9", "x" * 100_000)
    assert got.startswith("exit 9")


def test_compose_names_a_tool_that_reported_no_error_at_all():
    got = compose_failure_text("", "traceback: boom")
    assert "traceback: boom" in got
    assert got.strip() != ""


async def test_the_model_receives_the_stderr_not_just_the_exit_code(
    platform, tmp_path
):
    """THE EVIDENCE, in one assertion: five steps were spent on the string
    "exit 1" while the reason sat in `output`, discarded."""
    platform.registry.register(
        _NoisyFailureTool("bash: rename: command not found")
    )
    res = await platform.registry.invoke(
        "noisy_fail", {}, _ctx(platform, tmp_path / "ws"), platform.permissions
    )
    assert res.ok is False
    assert "exit 1" in (res.error or "")
    assert "rename: command not found" in (res.error or "")


async def test_the_ledger_row_carries_the_diagnostic_too(platform, tmp_path):
    """Composed ONCE in the registry, which is why the ledger improves without
    a line of change in it — and the ledger is where `agents/outcome` and the
    compaction verifier read what a run actually did."""
    platform.registry.register(_NoisyFailureTool("PermissionError: locked"))
    await platform.registry.invoke(
        "noisy_fail", {}, _ctx(platform, tmp_path / "ws"), platform.permissions
    )
    row = _rows(platform)[-1]
    assert row.ok is False
    assert "PermissionError: locked" in row.output


async def test_a_successful_call_is_not_rewritten(platform, tmp_path):
    """The composition must touch failures ONLY: rewriting a good result's
    error field (or folding output into it) would corrupt every happy path."""
    platform.registry.register(
        _NoisyFailureTool("all good", error=None, ok=True)
    )
    res = await platform.registry.invoke(
        "noisy_fail", {}, _ctx(platform, tmp_path / "ws"), platform.permissions
    )
    assert res.ok is True
    assert res.output == "all good"
    assert res.error is None


async def test_a_real_failing_shell_command_names_its_cause(platform, tmp_path):
    """End-to-end through the REAL ShellTool: the diagnostic must survive the
    subprocess, the registry and the permission grant."""
    ws = tmp_path / "ws"
    res = await platform.registry.invoke(
        "shell",
        {"command": 'python -c "import sys; sys.stderr.write(\'BOOM-42\'); sys.exit(3)"'},
        _ctx(platform, ws),
        platform.permissions,
        # `shell` is on the DENY FLOOR, so an agent-definition override cannot
        # raise it — the sanctioned grant is the per-session one the UI issues.
        session_allow={"shell"},
    )
    assert res.ok is False
    assert "exit 3" in (res.error or "")
    assert "BOOM-42" in (res.error or "")


# =============================================================================
# 2. CONTRACT 2 — the repeated-failure breaker
# =============================================================================


class _QueueTool(Tool):
    """Succeeds or fails according to a scripted queue, and counts its calls."""

    permission_key = "queue_tool"
    description = "test double"
    input_schema = {
        "type": "object",
        "properties": {"target": {"type": "string"}},
    }

    def __init__(self, name: str, outcomes: list[bool]):
        self.name = name
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

    async def execute(self, args, ctx):  # noqa: D102
        self.calls.append(dict(args))
        ok = self._outcomes.pop(0) if self._outcomes else False
        if ok:
            return ToolResult(ok=True, output="worked")
        return ToolResult(ok=False, output="stderr: no such thing", error="exit 1")


class _CaptureMock(MockLLMAdapter):
    """Scripted adapter that records the messages it was handed each turn."""

    provider = "loopfake"
    model = "loopfake-1"

    def __init__(self, script):
        super().__init__(script)
        self.seen: list[list] = []

    async def complete(self, *, system, messages, tools):  # type: ignore[override]
        self.seen.append(list(messages))
        return self._script.pop(0)


async def _run_loop(platform, tool: Tool, arg_script: list[dict]) -> list:
    """Drive the REAL AgentRuntime: one tool call per step, then finalize.

    Returns the `role="tool"` messages the model ended up with — the only place
    the breaker's behaviour is observable to the thing it exists to inform.
    """
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.agents.types import AgentDefinition

    platform.registry.register(tool)
    script = [
        LLMResponse(
            tool_calls=[ToolCall(f"c{i}", tool.name, dict(a))],
            finish_reason="tool_use",
        )
        for i, a in enumerate(arg_script)
    ]
    script.append(LLMResponse(text="done", finish_reason="stop"))
    adapter = _CaptureMock(script)
    platform.providers.register("loopfake", lambda: adapter)

    session = await Orchestrator(platform).create_session(
        "rename the files", AgentType.BUILDER, provider="loopfake",
        allow_tools=[tool.perm_key()],
    )
    await AgentRuntime(platform).run(
        session,
        AgentDefinition(
            type=AgentType.BUILDER, system_prompt="x", tools=[tool.name]
        ),
    )
    return [m for m in adapter.seen[-1] if getattr(m, "role", "") == "tool"]


def test_call_signature_ignores_key_order():
    """A model re-emitting the same object with its keys shuffled is repeating
    itself; a signature that disagreed would let the breaker be walked around
    by accident."""
    a = call_signature("extract_pdf", {"path": "x.pdf", "page_range": "1-2"})
    b = call_signature("extract_pdf", {"page_range": "1-2", "path": "x.pdf"})
    assert a == b
    assert a != call_signature("extract_pdf", {"path": "y.pdf"})
    assert a != call_signature("read_document", {"path": "x.pdf", "page_range": "1-2"})


def test_call_signature_survives_unserialisable_arguments():
    assert call_signature("t", {"o": object()})  # no raise, non-empty


async def test_the_second_identical_failure_warns_the_model(platform, tmp_path):
    tool = _QueueTool("qfail", [False, False])
    msgs = await _run_loop(platform, tool, [{"target": "a"}, {"target": "a"}])

    assert "[repeat" not in str(msgs[0].content)  # first failure says nothing new
    second = str(msgs[1].content)
    assert "[repeat" in second
    assert "qfail" in second
    assert str(_BREAKER_REFUSE_AT) in second  # it says what happens next
    # ...and the real diagnostic is still there. The note must ADD, never replace.
    assert "stderr: no such thing" in second


async def test_the_third_identical_call_is_refused_and_never_runs(
    platform, tmp_path
):
    tool = _QueueTool("qfail", [False, False, False])
    msgs = await _run_loop(
        platform, tool, [{"target": "a"}, {"target": "a"}, {"target": "a"}]
    )

    assert len(tool.calls) == _BREAKER_NOTE_AT, (
        "the third identical call must never reach the tool"
    )
    third = str(msgs[2].content)
    assert "repeated-failure breaker" in third
    assert "qfail" in third
    # Actionable, not just a wall: it names what to do instead.
    assert "different tool" in third or "Change the arguments" in third


async def test_the_refusal_is_written_to_the_execution_ledger(platform, tmp_path):
    """Routed through the registry's deny seam ON PURPOSE. `agents/outcome`
    derives what a run did from `ToolInvocation`, so a refusal that never
    reached the ledger would make the run's own history disagree with its
    transcript."""
    tool = _QueueTool("qfail", [False, False, False])
    await _run_loop(
        platform, tool, [{"target": "a"}, {"target": "a"}, {"target": "a"}]
    )
    outputs = [r.output for r in _rows(platform) if r.tool == "qfail"]
    assert len(outputs) == 3  # every attempt recorded, including the refused one
    assert any("repeated-failure breaker" in o for o in outputs)


async def test_the_breaker_is_scoped_to_the_CALL_not_the_TOOL(platform, tmp_path):
    """THE SAFETY PROPERTY. An agent legitimately probes several paths that turn
    out not to exist. A breaker that disabled `read_file` after three misses
    would end runs it was built to rescue — so a DIFFERENT argument set must
    still reach the tool."""
    tool = _QueueTool("qfail", [False, False, True])
    msgs = await _run_loop(
        platform, tool, [{"target": "a"}, {"target": "a"}, {"target": "b"}]
    )

    assert tool.calls[-1] == {"target": "b"}
    assert "worked" in str(msgs[2].content)
    assert "repeated-failure breaker" not in str(msgs[2].content)


async def test_a_success_clears_the_streak(platform, tmp_path):
    """Consecutive, not cumulative. A flaky call that eventually works must not
    carry its history into the next attempt."""
    tool = _QueueTool("qfail", [False, True, False])
    msgs = await _run_loop(
        platform, tool, [{"target": "a"}, {"target": "a"}, {"target": "a"}]
    )

    assert len(tool.calls) == 3  # nothing was refused
    assert "[repeat" not in str(msgs[2].content), (
        "the streak restarted at 1, so the third call is only failure #1"
    )


async def test_every_tool_call_still_gets_exactly_one_tool_result(
    platform, tmp_path
):
    """The assistant/tool-pair invariant (v1.152.0): a `tool_use` without its
    `tool_result` makes strict providers reject the ENTIRE conversation. A
    refusal that skipped the message would be worse than the loop it prevents."""
    tool = _QueueTool("qfail", [False, False, False, False])
    msgs = await _run_loop(
        platform,
        tool,
        [{"target": "a"}, {"target": "a"}, {"target": "a"}, {"target": "a"}],
    )
    assert len(msgs) == 4
    assert [m.tool_call_id for m in msgs] == ["c0", "c1", "c2", "c3"]
    assert all(str(m.content).strip() for m in msgs)


# =============================================================================
# 3. CONTRACT 3 — the read cache
# =============================================================================


class _CountingReadTool(Tool):
    """Stands in for a real read tool, and COUNTS how often it truly ran.

    Named after a real cacheable tool so it takes the same registry path and
    the same permission key. The count is the whole point: a cache that returns
    the right text while still paying for the extraction has fixed nothing.
    """

    description = "test double"
    reversibility = Reversibility.READONLY
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "page_range": {"type": "string"}},
        "required": ["path"],
    }

    def __init__(
        self,
        name: str,
        *,
        fail: bool = False,
        ocr: bool = False,
        text: str | None = None,
    ):
        self.name = name
        self.runs = 0
        self._fail = fail
        self._text = text
        # The SAME signal `documents/tools.py` hands to `ocr_if_unreadable`, and
        # therefore the same one the registry reads to decide whether this door
        # could have transcribed a scan. `None` = blind, exactly like the
        # `read_file` that `default_registry()` builds.
        self._router_resolver = (lambda: None) if ocr else None

    async def execute(self, args, ctx):  # noqa: D102
        self.runs += 1
        if self._fail:
            return ToolResult(ok=False, error="cannot read", output="broken pdf")
        if self._text is not None:
            return ToolResult(ok=True, output=self._text, data={"chars": 0})
        target = Path(args["path"])
        if not target.is_absolute():
            target = Path(ctx.workspace) / args["path"]
        return ToolResult(
            ok=True,
            output=f"TEXT#{self.runs}:{target.read_text(encoding='utf-8')}",
            data={"chars": 7},
        )


def test_the_cacheable_set_is_the_three_read_tools():
    assert CACHEABLE_READ_TOOLS == {"read_file", "read_document", "extract_pdf"}


async def test_a_second_read_of_an_unchanged_file_does_not_re_read_it(
    platform, tmp_path
):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "k1.pdf").write_text("K-1 income", encoding="utf-8")
    tool = _CountingReadTool("read_document")
    platform.registry.register(tool)
    ctx = _ctx(platform, ws)

    first = await platform.registry.invoke(
        "read_document", {"path": "k1.pdf"}, ctx, platform.permissions
    )
    second = await platform.registry.invoke(
        "read_document", {"path": "k1.pdf"}, ctx, platform.permissions
    )

    assert tool.runs == 1, "the file was extracted twice"
    assert "K-1 income" in second.output       # the text really came back
    assert "TEXT#1" in second.output           # ...and it is the FIRST read's text
    assert "already read" in second.output     # ...and it says so
    assert "unchanged since" in second.output
    assert second.data["cached"] is True
    assert first.data.get("cached") is None    # the first read is not a hit


async def test_the_cache_crosses_read_tools(platform, tmp_path):
    """THE EXACT WASTE IN THE EVIDENCE: three files read by `extract_pdf` at
    steps 9/11/12 and read AGAIN by `read_document` at steps 13-18. What
    identifies a read is the FILE, not the door used to open it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "1099.pdf").write_text("1099-NEC 2024", encoding="utf-8")
    pdf = _CountingReadTool("extract_pdf")
    doc = _CountingReadTool("read_document")
    platform.registry.register(pdf)
    platform.registry.register(doc)
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "extract_pdf", {"path": "1099.pdf"}, ctx, platform.permissions
    )
    res = await platform.registry.invoke(
        "read_document", {"path": "1099.pdf"}, ctx, platform.permissions
    )

    assert doc.runs == 0
    assert "1099-NEC 2024" in res.output
    assert "extract_pdf" in res.output, "the note must name which call produced it"
    assert res.data["cached_from"] == "extract_pdf"


async def test_a_cache_hit_is_still_recorded_in_the_ledger(platform, tmp_path):
    """The ledger is the run's source of truth. A hit that skipped it would
    make `agents/outcome` (and the compaction verifier) believe a file was
    never read at all."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "w2.pdf").write_text("W-2 wages", encoding="utf-8")
    platform.registry.register(_CountingReadTool("read_document"))
    ctx = _ctx(platform, ws)

    for _ in range(2):
        await platform.registry.invoke(
            "read_document", {"path": "w2.pdf"}, ctx, platform.permissions
        )

    rows = [r for r in _rows(platform) if r.tool == "read_document"]
    assert len(rows) == 2
    assert all(r.ok for r in rows)
    assert "already read" in rows[1].output   # marked as served from cache
    assert "already read" not in rows[0].output


async def test_the_note_quotes_the_step_the_file_was_read_on(platform, tmp_path):
    """`already read at step N` — the number must come from the run's own
    record, not be invented."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "a.pdf").write_text("body", encoding="utf-8")
    run = AgentRun(session_id="s1", steps=9)
    run_id = run.id  # read BEFORE the commit detaches the instance
    with session_scope(platform.engine) as db:
        db.add(run)
        db.commit()
    platform.registry.register(_CountingReadTool("extract_pdf"))
    ctx = _ctx(platform, ws, run_id=run_id)

    await platform.registry.invoke(
        "extract_pdf", {"path": "a.pdf"}, ctx, platform.permissions
    )
    res = await platform.registry.invoke(
        "extract_pdf", {"path": "a.pdf"}, ctx, platform.permissions
    )
    assert "already read at step 9 — unchanged since" in res.output
    assert res.data["cached_step"] == 9


async def test_a_changed_file_is_read_again(platform, tmp_path):
    """Staleness is the one thing a read cache may never get wrong."""
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "notes.txt"
    target.write_text("first", encoding="utf-8")
    tool = _CountingReadTool("read_file")
    platform.registry.register(tool)
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "read_file", {"path": "notes.txt"}, ctx, platform.permissions
    )
    target.write_text("second version, longer", encoding="utf-8")
    res = await platform.registry.invoke(
        "read_file", {"path": "notes.txt"}, ctx, platform.permissions
    )

    assert tool.runs == 2
    assert "second version" in res.output
    assert "already read" not in res.output


async def test_any_successful_write_in_the_scope_drops_the_cached_reads(
    platform, tmp_path
):
    """The belt to mtime+size's braces. Filesystem timestamp granularity is not
    universally sub-second, so a same-size rewrite inside one tick could be
    invisible to the key — and handing back the OLD text of a file the run just
    changed is the failure this feature exists to make less likely."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "doc.txt").write_text("original", encoding="utf-8")
    tool = _CountingReadTool("read_file")
    platform.registry.register(tool)
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "read_file", {"path": "doc.txt"}, ctx, platform.permissions
    )
    await platform.registry.invoke(
        "write_file", {"path": "other.txt", "content": "x"}, ctx, platform.permissions
    )
    await platform.registry.invoke(
        "read_file", {"path": "doc.txt"}, ctx, platform.permissions
    )
    assert tool.runs == 2


async def test_a_read_only_tool_does_not_drop_the_cache(platform, tmp_path):
    """...but the invalidation must not be so eager that the cache never
    survives a listing, which is the first thing every agent does."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "doc.txt").write_text("original", encoding="utf-8")
    tool = _CountingReadTool("read_file")
    platform.registry.register(tool)
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "read_file", {"path": "doc.txt"}, ctx, platform.permissions
    )
    await platform.registry.invoke("list_files", {}, ctx, platform.permissions)
    await platform.registry.invoke(
        "read_file", {"path": "doc.txt"}, ctx, platform.permissions
    )
    assert tool.runs == 1


async def test_failures_are_never_cached(platform, tmp_path):
    """A transient error must not become the answer for the rest of a session."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "bad.pdf").write_text("x", encoding="utf-8")
    failing = _CountingReadTool("extract_pdf", fail=True)
    platform.registry.register(failing)
    ctx = _ctx(platform, ws)

    for _ in range(2):
        res = await platform.registry.invoke(
            "extract_pdf", {"path": "bad.pdf"}, ctx, platform.permissions
        )
        assert res.ok is False
    assert failing.runs == 2


async def test_a_page_slice_is_not_the_whole_document(platform, tmp_path):
    """`page_range` changes what comes back, so it must change the identity —
    otherwise a 3-page slice would be served as the whole return."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "big.pdf").write_text("many pages", encoding="utf-8")
    tool = _CountingReadTool("read_document")
    platform.registry.register(tool)
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "read_document", {"path": "big.pdf"}, ctx, platform.permissions
    )
    await platform.registry.invoke(
        "read_document",
        {"path": "big.pdf", "page_range": "1-3"},
        ctx,
        platform.permissions,
    )
    assert tool.runs == 2


async def test_an_empty_page_range_is_the_same_call(platform, tmp_path):
    """...but `page_range=None` is the ABSENCE of a slice, which is what makes
    the cross-tool hit above possible at all."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "big.pdf").write_text("many pages", encoding="utf-8")
    tool = _CountingReadTool("read_document")
    platform.registry.register(tool)
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "read_document", {"path": "big.pdf"}, ctx, platform.permissions
    )
    await platform.registry.invoke(
        "read_document",
        {"path": "big.pdf", "page_range": None},
        ctx,
        platform.permissions,
    )
    assert tool.runs == 1


async def test_the_cache_does_not_cross_sessions(platform, tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "shared.txt").write_text("content", encoding="utf-8")
    tool = _CountingReadTool("read_file")
    platform.registry.register(tool)

    await platform.registry.invoke(
        "read_file", {"path": "shared.txt"}, _ctx(platform, ws, "sessionA"),
        platform.permissions,
    )
    await platform.registry.invoke(
        "read_file", {"path": "shared.txt"}, _ctx(platform, ws, "sessionB"),
        platform.permissions,
    )
    assert tool.runs == 2


async def test_the_cache_does_not_cross_workspaces_within_one_session(
    platform, tmp_path
):
    """THE CHAT CASE, and the reason the key is composite. Chat runs EVERY turn
    under the literal session id "chat" while its tool workspace follows the
    grounded project — so a key made of the session id alone would carry one
    client's file text into the next project."""
    a, b = tmp_path / "projA", tmp_path / "projB"
    a.mkdir()
    b.mkdir()
    (a / "return.txt").write_text("client A", encoding="utf-8")
    (b / "return.txt").write_text("client B", encoding="utf-8")
    tool = _CountingReadTool("read_file")
    platform.registry.register(tool)

    first = await platform.registry.invoke(
        "read_file", {"path": "return.txt"}, _ctx(platform, a, "chat"),
        platform.permissions,
    )
    second = await platform.registry.invoke(
        "read_file", {"path": "return.txt"}, _ctx(platform, b, "chat"),
        platform.permissions,
    )
    assert tool.runs == 2
    assert "client A" in first.output
    assert "client B" in second.output
    assert "client A" not in second.output


async def test_a_missing_file_is_never_cached_and_still_errors(platform, tmp_path):
    """No stat, no identity — the tool runs and reports its own honest error."""
    ws = tmp_path / "ws"
    ws.mkdir()
    res = await platform.registry.invoke(
        "read_file", {"path": "ghost.txt"}, _ctx(platform, ws), platform.permissions
    )
    assert res.ok is False
    assert "ghost.txt" in (res.error or "")


async def test_a_cache_hit_still_honours_store_as(platform, tmp_path):
    """`_store_as` (v1.159.0) keeps big payloads OUT of the context. A cached
    read is exactly the payload worth storing, so the two features must
    compose rather than shadow each other."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "big.txt").write_text("lots of text", encoding="utf-8")
    platform.registry.register(_CountingReadTool("read_file"))
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "read_file", {"path": "big.txt"}, ctx, platform.permissions
    )
    res = await platform.registry.invoke(
        "read_file", {"path": "big.txt", "_store_as": "doc"}, ctx,
        platform.permissions,
    )
    assert res.ok
    # Either the namespace took it (a receipt) or it degraded to the text —
    # never a crash, and never a silent loss of the payload.
    assert "doc" in res.output or "lots of text" in res.output


# =============================================================================
# 4. CONTRACT 4 (runtime half) — a session may carry its own step budget
# =============================================================================


def _cfg(default: int = 20):
    return SimpleNamespace(max_agent_steps=default)


def test_a_session_without_a_budget_gets_todays_number():
    """The additive guarantee. Every existing caller sets nothing, and must
    land on exactly the configured default."""
    assert resolve_max_steps(SimpleNamespace(), _cfg(20)) == 20
    assert resolve_max_steps(SimpleNamespace(max_steps=None), _cfg(17)) == 17


def test_a_session_budget_wins():
    assert resolve_max_steps(SimpleNamespace(max_steps=60), _cfg(20)) == 60
    assert resolve_max_steps(SimpleNamespace(max_steps=3), _cfg(20)) == 3


def test_zero_and_junk_mean_unset_never_zero_steps():
    """A run that can take no action at all is not a budget, it is a broken
    session — so 0/""/None all fall back rather than freezing the agent."""
    for junk in (0, "", "abc", -5, None, []):
        assert resolve_max_steps(SimpleNamespace(max_steps=junk), _cfg(20)) == 20


def test_a_numeric_string_is_accepted():
    assert resolve_max_steps(SimpleNamespace(max_steps="7"), _cfg(20)) == 7


async def test_the_runtime_actually_spends_the_session_budget(platform, tmp_path):
    """The value must reach the loop, not just the helper: a mutation deleting
    the call site has to fail something."""
    from iron_jarvis.agents.orchestrator import Orchestrator
    from iron_jarvis.agents.types import AgentDefinition

    seen: dict[str, int] = {}
    runtime = AgentRuntime(platform)

    async def _fake(*args, **kwargs):
        seen["max_steps"] = kwargs["max_steps"]
        return True, "done"

    runtime.perceive_act = _fake  # type: ignore[method-assign]
    session = await Orchestrator(platform).create_session(
        "t", AgentType.BUILDER, provider="mock"
    )
    session.max_steps = 4
    await runtime.run(
        session,
        AgentDefinition(type=AgentType.BUILDER, system_prompt="x", tools=[]),
    )
    assert seen["max_steps"] == 4


async def test_the_runtime_persists_the_step_before_running_tools(
    platform, tmp_path
):
    """The read cache quotes `AgentRun.steps` back to the model as "step N".
    The record used to be saved only at the END of a step, so throughout step N
    it still said N-1 — a note that quoted a step which had already passed."""
    tool = _QueueTool("qfail", [True])
    seen: list[int] = []

    original = platform.registry.invoke

    async def _spy(name, args, ctx, *a, **kw):
        with session_scope(platform.engine) as db:
            run = db.get(AgentRun, ctx.agent_run_id)
            seen.append(int(getattr(run, "steps", 0) or 0))
        return await original(name, args, ctx, *a, **kw)

    platform.registry.invoke = _spy  # type: ignore[method-assign]
    try:
        await _run_loop(platform, tool, [{"target": "a"}])
    finally:
        platform.registry.invoke = original  # type: ignore[method-assign]
    assert seen == [1], "the first step's tools must see steps == 1, not 0"


# =============================================================================
# 5. The wiring these three contracts share
# =============================================================================


def test_the_registry_composes_the_diagnostic_itself():
    """Contract 1 says COMPOSED ONCE, in the registry. If a future change moved
    it into the runtime, the chat lanes and the ledger would silently go back to
    bare "exit 1"."""
    import inspect

    from iron_jarvis.tools import registry as _reg

    body = inspect.getsource(_reg.ToolRegistry.invoke)
    assert "compose_failure_text" in body


def test_the_runtime_still_reads_error_on_failure():
    """...and the runtime keeps handing `error` to the model — which is only
    correct BECAUSE the registry enriched it. The two halves are a pair."""
    import inspect

    from iron_jarvis.agents import runtime as _rt

    body = inspect.getsource(_rt.AgentRuntime.perceive_act)
    assert "result.error" in body
    assert "call_signature" in body


def test_no_control_bytes_in_the_notes_this_module_emits():
    """Every string that reaches a transcript or the ledger is plain text."""
    from iron_jarvis.tools import registry as _reg

    entry = {"output": "body", "data": {}, "tool": "extract_pdf", "step": 3}
    text = _reg.ToolRegistry()._cached_result(entry).output
    text += compose_failure_text("exit 1", "x" * 5000)
    text += call_signature("t", {"a": 1})
    assert not any(ord(ch) < 32 and ch not in "\n\t" for ch in text)


def test_json_is_still_how_the_signature_is_built():
    """Guards the canonicalisation itself: two dicts equal as JSON are the same
    call, and the test above proves it for key order only."""
    sig = call_signature("t", {"path": "a", "n": 1})
    assert json.dumps({"n": 1, "path": "a"}, sort_keys=True) in sig


# =============================================================================
# 6. THE REVIEW ROUND — what the first cut of this wave got wrong
#
# Every test below reproduces a defect a reviewer measured against a REAL
# built platform, not a hypothetical. They are grouped here rather than folded
# into the sections above because the failures they pin all share one shape:
# the cache was built as if a file's identity were the whole story, when the
# door used to open it carries capability and authority of its own.
# =============================================================================


# --- 6a. A BLIND DOOR MUST NEVER ANSWER FOR A SIGHTED ONE --------------------


def test_ocr_capability_is_read_off_the_router_resolver(platform):
    """The honest signal, on the REAL registry. `ocr_if_unreadable` returns
    immediately when `router_resolver` is None, so a tool without one cannot
    recover a scan whatever its name promises."""
    assert platform.registry._ocr_capable("read_document") is True
    assert platform.registry._ocr_capable("extract_pdf") is True
    assert platform.registry._ocr_capable("nonexistent_tool") is False


async def test_a_blind_read_is_never_served_to_an_ocr_capable_tool(
    platform, tmp_path
):
    """THE ACCEPTANCE FOLDER, in one test. 11 of its 22 PDFs are image-only
    scans. `read_file` is in every roster and is what an agent reaches for
    first — and it carries no router, so it hands back an EMPTY extraction. If
    the cache let that answer the next `read_document` (which CAN transcribe),
    every scan opened with `read_file` first would be permanently unreadable
    for the rest of the scope, and the wave's own acceptance test would fail
    BECAUSE of the cache."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scan.pdf").write_text("%PDF-1.4 pretend", encoding="utf-8")
    blind = _CountingReadTool("read_file", text="")           # no text layer
    sighted = _CountingReadTool(
        "read_document", ocr=True, text="TRANSCRIBED: Form 1099-NEC"
    )
    platform.registry.register(blind)
    platform.registry.register(sighted)
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "read_file", {"path": "scan.pdf"}, ctx, platform.permissions
    )
    res = await platform.registry.invoke(
        "read_document", {"path": "scan.pdf"}, ctx, platform.permissions
    )

    assert sighted.runs == 1, "the OCR-capable tool must actually run"
    assert "TRANSCRIBED: Form 1099-NEC" in res.output
    assert "cached" not in (res.data or {})


async def test_an_ocr_capable_read_still_serves_the_blind_tool(platform, tmp_path):
    """...and the saving survives in the direction that is safe. Once the scan
    HAS been transcribed, `read_file` gets that text for free — which is the
    whole point of a cross-tool cache."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scan.pdf").write_text("%PDF-1.4 pretend", encoding="utf-8")
    sighted = _CountingReadTool(
        "read_document", ocr=True, text="TRANSCRIBED: W-2 wages 61,000"
    )
    blind = _CountingReadTool("read_file", text="")
    platform.registry.register(sighted)
    platform.registry.register(blind)
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "read_document", {"path": "scan.pdf"}, ctx, platform.permissions
    )
    res = await platform.registry.invoke(
        "read_file", {"path": "scan.pdf"}, ctx, platform.permissions
    )

    assert blind.runs == 0
    assert "TRANSCRIBED: W-2 wages 61,000" in res.output
    assert res.data["cached_from"] == "read_document"


async def test_the_capable_read_upgrades_the_entry_for_everyone(platform, tmp_path):
    """An entry only ever gets better: after the sighted tool has run, a THIRD
    read of the same file — by either door — costs nothing."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scan.pdf").write_text("%PDF-1.4 pretend", encoding="utf-8")
    blind = _CountingReadTool("read_file", text="")
    sighted = _CountingReadTool("read_document", ocr=True, text="TRANSCRIBED")
    platform.registry.register(blind)
    platform.registry.register(sighted)
    ctx = _ctx(platform, ws)

    for name in ("read_file", "read_document", "read_document", "read_file"):
        await platform.registry.invoke(
            name, {"path": "scan.pdf"}, ctx, platform.permissions
        )

    assert blind.runs == 1
    assert sighted.runs == 1


# --- 6b. THE CACHE MAY NOT JUMP A TOOL'S OWN PATH GATE -----------------------


async def test_the_cache_never_walks_a_file_into_the_workspace(platform, tmp_path):
    """MEASURED BYPASS. The PermissionEngine authorizes by tool NAME; the path
    authority is `safe_path` inside `read_file` (§17 workspace_only) and the
    cache sat in front of it. So: `read_file` on an outside file was refused;
    `read_document` (which may read anywhere) read it; and the IDENTICAL
    `read_file` call then returned ok=True with the whole file."""
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    target = outside / "client-ssn.txt"
    target.write_text("SSN 123-45-6789", encoding="utf-8")
    platform.registry.register(_CountingReadTool("read_document", ocr=True))
    ctx = _ctx(platform, ws)

    before = await platform.registry.invoke(
        "read_file", {"path": str(target)}, ctx, platform.permissions
    )
    assert before.ok is False  # the real ReadFileTool refuses it

    doc = await platform.registry.invoke(
        "read_document", {"path": str(target)}, ctx, platform.permissions
    )
    assert doc.ok is True and "123-45-6789" in doc.output  # legitimately allowed

    after = await platform.registry.invoke(
        "read_file", {"path": str(target)}, ctx, platform.permissions
    )
    assert after.ok is False, "the cache re-opened a workspace-confined tool"
    assert "123-45-6789" not in (after.output or "") + (after.error or "")


async def test_a_protected_path_gets_no_cache_identity_at_all(platform, tmp_path):
    """The same shape for the fs-policy gate: the app's own key material is
    never agent-readable, so it may not be remembered EITHER. No identity means
    nothing is served and nothing is stored — the tool runs and refuses."""
    ws = tmp_path / "ws"
    ws.mkdir()
    secrets = Path(platform.config.home) / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    key = secrets / "note.txt"
    key.write_text("fernet", encoding="utf-8")
    ordinary = ws / "ok.txt"
    ordinary.write_text("fine", encoding="utf-8")
    ctx = _ctx(platform, ws)

    assert await platform.registry._read_cache_key(
        "read_document", {"path": str(key)}, ctx
    ) is None
    assert await platform.registry._read_cache_key(
        "read_document", {"path": str(ordinary)}, ctx
    ) is not None


# --- 6c. "NOT READONLY" IS NOT "WROTE SOMETHING" -----------------------------


def test_the_mutating_set_is_writers_only():
    """`Reversibility` defaults to IRREVERSIBLE — the fail-safe answer for UNDO,
    which made 69 of 87 tools look like writers. Pure readers must not be in
    this set, or the cache is purged by the act of checking the worklist."""
    for writer in ("shell", "write_file", "edit_file", "write_document", "repl"):
        assert writer in _MUTATING_TOOLS
    for reader in (
        "worklist_status", "worklist_next", "memory_read", "blackboard_read",
        "recall", "recall_lessons", "tool_list", "list_agents", "web_search",
        "file_search", "skill_search", "secret_list", "image_info",
    ):
        assert reader not in _MUTATING_TOOLS


async def test_a_pure_reader_that_is_not_readonly_keeps_the_cache(
    platform, tmp_path
):
    """THE WORKFLOW THIS WAVE SHIPS. The supervisor loop is survey →
    `worklist_next` → read → rename → `worklist_done`, so a non-READONLY call
    sits between every pair of reads. `memory_read` stands in here: a REAL
    registered tool, allow-by-default, declared IRREVERSIBLE, and it reads."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "doc.txt").write_text("original", encoding="utf-8")
    tool = _CountingReadTool("read_file")
    platform.registry.register(tool)
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "read_file", {"path": "doc.txt"}, ctx, platform.permissions
    )
    reader = await platform.registry.invoke(
        "memory_read", {"layer": "session", "key": "anything"}, ctx,
        platform.permissions,
    )
    assert reader.ok is True  # it really ran; this is not a vacuous pass
    await platform.registry.invoke(
        "read_file", {"path": "doc.txt"}, ctx, platform.permissions
    )
    assert tool.runs == 1


async def test_a_real_shell_command_still_drops_the_cache(platform, tmp_path):
    """...and the belt still fastens: `shell` journals no undo and names no
    created path, so only the explicit list catches it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "doc.txt").write_text("original", encoding="utf-8")
    tool = _CountingReadTool("read_file")
    platform.registry.register(tool)
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "read_file", {"path": "doc.txt"}, ctx, platform.permissions
    )
    res = await platform.registry.invoke(
        "shell", {"command": "python -c \"print('hi')\""}, ctx, platform.permissions,
        session_allow={"shell"},
    )
    assert res.ok is True
    await platform.registry.invoke(
        "read_file", {"path": "doc.txt"}, ctx, platform.permissions
    )
    assert tool.runs == 2


class _CreatesFilesTool(Tool):
    """A tool nobody remembered to list, which really does create a file."""

    name = "unlisted_creator"
    permission_key = "read_file"
    description = "test double"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args, ctx):  # noqa: D102
        made = Path(ctx.workspace) / "made.txt"
        made.write_text("new", encoding="utf-8")
        return ToolResult(ok=True, output="made it", created_paths=[str(made)])


async def test_created_paths_invalidate_even_for_an_unlisted_tool(
    platform, tmp_path
):
    """The list is a maintenance cost, so it is BACKED by the objective signals
    the registry already holds. A tool that says it created a file drops the
    scope whether or not anyone remembered to name it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "doc.txt").write_text("original", encoding="utf-8")
    tool = _CountingReadTool("read_file")
    platform.registry.register(tool)
    platform.registry.register(_CreatesFilesTool())
    ctx = _ctx(platform, ws)

    await platform.registry.invoke(
        "read_file", {"path": "doc.txt"}, ctx, platform.permissions
    )
    await platform.registry.invoke(
        "unlisted_creator", {}, ctx, platform.permissions
    )
    await platform.registry.invoke(
        "read_file", {"path": "doc.txt"}, ctx, platform.permissions
    )
    assert tool.runs == 2


# --- 6d. THE BREAKER IS NOT THE PERMISSION SYSTEM ----------------------------


async def test_the_breaker_refusal_is_not_called_a_permission_denial(
    platform, tmp_path
):
    """It reached the model as `permission denied: repeated-failure breaker`,
    which the model relays as "I don't have permission" — and both chat lanes
    string-match that exact phrase to list a turn's user-refused tools. The app
    refusing its own repeated call is a different fact."""
    tool = _QueueTool("qfail", [False, False, False])
    msgs = await _run_loop(
        platform, tool, [{"target": "a"}, {"target": "a"}, {"target": "a"}]
    )
    third = str(msgs[2].content)
    assert "permission denied" not in third
    assert third.startswith("refused:")
    assert "repeated-failure breaker" in third


async def test_a_real_permission_denial_still_says_permission_denied(
    platform, tmp_path
):
    """The relabelling is scoped to a caller-supplied refusal. A decision the
    engine really made must keep the wording every surface already reads."""
    ws = tmp_path / "ws"
    ws.mkdir()
    res = await platform.registry.invoke(
        "shell", {"command": "echo hi"}, _ctx(platform, ws), platform.permissions
    )
    assert res.ok is False
    assert (res.error or "").startswith("permission denied:")


async def test_the_breaker_refusal_is_still_in_the_ledger_with_its_reason(
    platform, tmp_path
):
    """Recording it was always right; only the attribution was wrong."""
    tool = _QueueTool("qfail", [False, False, False])
    await _run_loop(
        platform, tool, [{"target": "a"}, {"target": "a"}, {"target": "a"}]
    )
    outputs = [r.output for r in _rows(platform) if r.tool == "qfail"]
    assert len(outputs) == 3
    assert any("repeated-failure breaker" in o for o in outputs)


# --- 6e. THE DIAGNOSTIC BOUND MAY ONLY EVER SHORTEN THE OUTPUT ---------------


def test_a_huge_error_is_never_truncated_by_a_little_output():
    """The feature exists to PRESERVE diagnostics. An error longer than the
    budget used to be clipped to 2000 chars purely because the tool had also
    captured a byte of stdout — the one half that was never lost before."""
    err = "E" * (_FAILURE_DIAGNOSTIC_CHARS + 500)
    assert compose_failure_text(err, "x") == err


def test_the_composed_text_never_exceeds_the_limit():
    """...and the bound still binds, including in the cramped middle where the
    'earlier output dropped' marker itself no longer fits."""
    for err_len in (0, 1, 1500, 1960, 1975, 1999, 2000, 2001):
        got = compose_failure_text("E" * err_len, "O" * 9000)
        assert len(got) <= max(_FAILURE_DIAGNOSTIC_CHARS, err_len)


def test_a_cramped_budget_returns_the_error_whole_not_a_marker_salad():
    err = "E" * (_FAILURE_DIAGNOSTIC_CHARS - 20)
    got = compose_failure_text(err, "O" * 9000)
    assert got == err  # no room for a tail AND the admission that it was cut


# --- 6f. read_file SAYS SO WHEN IT READ NOTHING ------------------------------


async def _read_with_stubbed_extractor(
    platform, tmp_path, monkeypatch, extracted: str, name: str = "scan.pdf"
):
    """Drive the REAL ReadFileTool with the extractor stubbed to *extracted*."""
    import iron_jarvis.documents as _documents

    ws = tmp_path / "ws"
    ws.mkdir(exist_ok=True)
    (ws / name).write_text("%PDF-1.4 pretend", encoding="utf-8")
    monkeypatch.setattr(_documents, "extract_text", lambda *a, **k: extracted)
    return await platform.registry.invoke(
        "read_file", {"path": name}, _ctx(platform, ws), platform.permissions
    )


async def test_read_file_says_so_when_a_document_yields_no_text(
    platform, tmp_path, monkeypatch
):
    """A HEADER OVER SILENCE IS A LIE. With no router wired (which is what
    `default_registry()` builds and nothing re-registers), a scanned PDF opened
    with `read_file` returned ok=True carrying ONLY '[extracted text from a PDF
    document — read-only view...]'. A model reads that as "this file is blank"
    and renames it accordingly."""
    res = await _read_with_stubbed_extractor(platform, tmp_path, monkeypatch, "")
    assert res.ok is True
    assert "NOTHING WAS READ" in res.output
    assert res.data["note"]


async def test_the_readers_own_sentinel_does_not_count_as_content(
    platform, tmp_path, monkeypatch
):
    """The reader's "[no extractable text ...]" sentence is the reader talking
    ABOUT the file, not text FROM it — counting it as content is what made an
    unreadable scan look like a successfully read document."""
    from iron_jarvis.documents.readers import SCANNED_PDF_SENTINEL

    res = await _read_with_stubbed_extractor(
        platform, tmp_path, monkeypatch, SCANNED_PDF_SENTINEL
    )
    assert "NOTHING WAS READ" in res.output


async def test_a_document_with_real_text_gets_no_such_note(
    platform, tmp_path, monkeypatch
):
    """The mutation guard: a note on every read would be noise, and noise is
    how an honesty mechanism stops being read."""
    res = await _read_with_stubbed_extractor(
        platform, tmp_path, monkeypatch, "FORM 1099-NEC\nNonemployee comp 42,000"
    )
    assert res.ok is True
    assert "NOTHING WAS READ" not in res.output
    assert "Nonemployee comp 42,000" in res.output
    assert (res.data or {}).get("note") is None


def test_the_shell_in_builtins_declares_that_it_is_superseded():
    """`platform.py` registers `SandboxedShellTool` under the same name, so a
    fix in `tools/builtins.ShellTool` is NOT a fix in the running app. The class
    says so, because the first cut of this wave reported one as the other."""
    from iron_jarvis.tools.builtins import ShellTool

    assert "superseded" in (ShellTool.__doc__ or "").lower()


async def test_the_registered_shell_is_the_sandboxed_one(platform):
    """...and here is the fact that docstring is about."""
    from iron_jarvis.tools.builtins import ShellTool

    assert not isinstance(platform.registry.get("shell"), ShellTool)
