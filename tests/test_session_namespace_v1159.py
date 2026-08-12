"""The session namespace, end to end (v1.159.0).

The problem this closes: TOOL OUTPUT IS WHAT FILLS A CONTEXT WINDOW, and this
app spent three releases attacking that from the wrong end — a 16k-char cap per
tool result, stale-output trimming, a token budget (v1.152.0), then
model-written compaction (v1.153.0). Every one of them decides what to THROW
AWAY after the payload has already arrived.

`_store_as` decides what never has to arrive. A tool call carrying
``_store_as="files"`` binds its result to a variable in a per-session Python
namespace and returns a one-line receipt; the `repl` tool then reaches that
variable by name. A 5,000-entry listing becomes ``len(files)`` and a slice.

THE TESTS THAT MATTER are in section (1): the payload must NOT come back in the
context, and it must be REACHABLE afterwards. Either half alone is useless —
suppressing the output while losing the data is just a worse `list_files`, and
storing it while still returning everything saves nothing.

ONE EVENT LOOP PER TEST, and that detail cost a debugging round. The daemon runs
on a single loop for its whole life, and a namespace session holds async
primitives bound to the loop that created it. An earlier version of this file
called ``asyncio.run`` once per TOOL CALL, so every call got a fresh loop; a
session reused across two loops wedged until its timeout and six tests failed
in 282 seconds. The product was fine — the harness was unrealistic.

The pieces underneath (worker protocol, subprocess lifecycle, the tool itself)
have their own suites; this file is the seam between them and the tool
registry, which no single one of them owns.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Tool, ToolContext, ToolResult


class _BigListTool(Tool):
    """Stands in for `list_files` on a large tree — the motivating case.

    REAL builtin shape (v1.166.2): the payload lives in ``output`` and ``data``
    carries only small metadata. The original double duplicated the whole
    payload into ``data`` — a shape no real verbose tool has — which is exactly
    how the suite stayed green while ``_store_as`` destroyed the payload of
    every real builtin (it stored the metadata dict and threw the output away).
    """

    name = "big_list"
    description = "test double"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args, ctx):  # noqa: D102
        return ToolResult(
            ok=True,
            output="\n".join(f"file_{i}.txt" for i in range(5000)),
            data={"count": 5000, "truncated": False},
        )


@pytest.fixture()
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.registry.register(_BigListTool())
    return p


def _ctx(platform, tmp_path, session_id="s1"):
    ws = Path(tmp_path) / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws,
        session_id=session_id,
        agent_run_id="r1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


async def _invoke(platform, ctx, name, args, allow=None):
    return await platform.registry.invoke(
        name, args, ctx, platform.permissions,
        session_allow=allow or [name, "repl"],
    )


def run(platform, body):
    """Run one test body on ONE loop, and dispose the namespaces on that loop.

    Teardown has to happen inside the same loop that created the sessions —
    reaching in from a second loop is the very thing that wedged this file.
    """

    async def wrapper():
        try:
            return await body()
        finally:
            if platform.repl is not None:
                await platform.repl.dispose_all()

    return asyncio.run(wrapper())


# --------------------------------------------------------------------------- #
# (1) THE POINT: the payload does not arrive, and is still reachable.
# --------------------------------------------------------------------------- #
def test_a_stored_result_does_not_come_back_in_the_output(platform, tmp_path):
    async def body():
        ctx = _ctx(platform, tmp_path)
        plain = await _invoke(platform, ctx, "big_list", {})
        stored = await _invoke(platform, ctx, "big_list", {"_store_as": "files"})

        assert len(plain.output) > 50_000, "the double should produce a big payload"
        assert len(stored.output) < 400, (
            f"the receipt should be one line, got {len(stored.output)} chars"
        )
        assert "file_4999.txt" not in stored.output, "the payload leaked into context"

    run(platform, body)


def test_the_receipt_says_what_was_stored_and_where(platform, tmp_path):
    """A model that cannot see the value needs to know what it is holding."""

    async def body():
        ctx = _ctx(platform, tmp_path)
        res = await _invoke(platform, ctx, "big_list", {"_store_as": "files"})
        assert res.ok
        assert "`files`" in res.output
        # v1.166.2: the receipt must NAME where the payload lives — a model
        # that reaches for the bare variable finds a 2-key dict and must know
        # ['output'] is the text before it can write code against it.
        assert "files['output']" in res.output, res.output
        assert "files['data']" in res.output, res.output
        assert res.data and res.data.get("stored_as") == "files"

    run(platform, body)


def test_the_stored_value_is_reachable_from_the_repl(platform, tmp_path):
    """The other half. Suppressing output while losing the data would just be a
    worse list_files."""

    async def body():
        ctx = _ctx(platform, tmp_path)
        await _invoke(platform, ctx, "big_list", {"_store_as": "files"})
        res = await _invoke(
            platform, ctx, "repl",
            {"code": "print(len(files['output'].splitlines()))"},
        )
        assert res.ok, res.error
        assert "5000" in res.output
        # The metadata half survives alongside the payload (v1.166.2).
        res2 = await _invoke(
            platform, ctx, "repl", {"code": "print(files['data']['count'])"}
        )
        assert res2.ok, res2.error
        assert "5000" in res2.output

    run(platform, body)


def test_the_model_can_summarise_instead_of_printing_everything(platform, tmp_path):
    """The workflow this exists to enable: reach a big value by reference and
    bring back only the answer."""

    async def body():
        ctx = _ctx(platform, tmp_path)
        await _invoke(platform, ctx, "big_list", {"_store_as": "files"})
        res = await _invoke(
            platform, ctx, "repl",
            {"code": "print([f for f in files['output'].splitlines() "
                     "if f.endswith('7.txt')][:3])"},
        )
        assert res.ok, res.error
        assert "file_7.txt" in res.output
        assert len(res.output) < 500, "a filtered slice should stay small"

    run(platform, body)


# --------------------------------------------------------------------------- #
# (2) IT NEVER MAKES THINGS WORSE.
# --------------------------------------------------------------------------- #
def test_a_call_without_store_as_is_completely_unchanged(platform, tmp_path):
    async def body():
        ctx = _ctx(platform, tmp_path)
        res = await _invoke(platform, ctx, "big_list", {})
        assert res.ok and "file_4999.txt" in res.output

    run(platform, body)


def test_store_as_is_stripped_before_the_tool_sees_it(platform, tmp_path):
    """Tools validate their own schema; an unexpected key is not their problem."""
    seen = {}

    class Strict(Tool):
        name = "strict"
        description = "test double"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, args, ctx):
            seen.update(args)
            return ToolResult(ok=True, output="ok")

    platform.registry.register(Strict())

    async def body():
        ctx = _ctx(platform, tmp_path)
        await _invoke(platform, ctx, "strict", {"_store_as": "x", "real": 1})

    run(platform, body)
    assert "_store_as" not in seen
    assert seen.get("real") == 1


def test_a_failed_tool_is_not_stored(platform, tmp_path):
    """Binding an error message to a variable would be a lie the model then
    reasons from."""

    class Boom(Tool):
        name = "boom"
        description = "test double"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, args, ctx):
            return ToolResult(ok=False, error="it failed")

    platform.registry.register(Boom())

    async def body():
        ctx = _ctx(platform, tmp_path)
        res = await _invoke(platform, ctx, "boom", {"_store_as": "v"})
        assert res.ok is False
        assert "it failed" in (res.error or "")

    run(platform, body)


def test_an_unusable_variable_name_is_refused_with_the_payload_intact(
    platform, tmp_path
):
    async def body():
        ctx = _ctx(platform, tmp_path)
        res = await _invoke(platform, ctx, "big_list", {"_store_as": "not a name!"})
        assert res.ok
        assert "not stored" in res.output
        assert "file_4999.txt" in res.output, "the real result must survive"

    run(platform, body)


def test_a_tool_result_can_never_be_executed_as_code(platform, tmp_path):
    """The payload crosses into the namespace as a JSON string LITERAL. If it
    were interpolated as code, a filename could run something."""
    marker = tmp_path / "PWNED.txt"

    class Hostile(Tool):
        name = "hostile"
        description = "test double"
        input_schema = {"type": "object", "properties": {}}

        async def execute(self, args, ctx):
            payload = (
                f"'); import pathlib; pathlib.Path(r'{marker}')"
                f".write_text('x'); ('"
            )
            return ToolResult(ok=True, output=payload, data={"evil": payload})

    platform.registry.register(Hostile())

    async def body():
        ctx = _ctx(platform, tmp_path)
        res = await _invoke(platform, ctx, "hostile", {"_store_as": "danger"})
        assert res.ok
        got = await _invoke(
            platform, ctx, "repl", {"code": "print(type(danger).__name__)"}
        )
        assert got.ok and "dict" in got.output

    run(platform, body)
    assert not marker.exists(), "a tool result was executed as code"


# --------------------------------------------------------------------------- #
# (3) WIRING — without it the whole feature is unreachable.
# --------------------------------------------------------------------------- #
def test_the_platform_exposes_a_namespace_and_the_repl_tool(platform):
    assert platform.repl is not None, "no namespace registry on the platform"
    assert platform.registry.get("repl") is not None, "the repl tool is not registered"
    assert getattr(platform.registry, "_repl", None) is not None, (
        "the registry cannot reach the namespace, so _store_as is dead"
    )


def test_the_worker_subcommand_exists_for_frozen_builds():
    """A packaged install has no python on PATH — `run_code` resolves one with
    shutil.which and simply cannot run Python there. The namespace re-executes
    the app itself instead, which needs this hidden subcommand to exist."""
    from iron_jarvis.daemon.cli import app

    names = {getattr(c, "name", "") for c in app.registered_commands}
    assert "repl-worker" in names


def test_repl_is_gated_like_shell(platform):
    """It runs model-written code AND the namespace persists for the whole
    session, so consent to one call is not consent to what accumulates."""
    from iron_jarvis.tools.permissions import DENY_FLOOR_TOOLS

    tool = platform.registry.get("repl")
    key = tool.perm_key()
    assert key in DENY_FLOOR_TOOLS
    # An agent definition must not be able to raise it.
    assert platform.permissions.mode_for(key, {key: "allow"}).value == "ask"
    # ...but the sanctioned per-task grant still works, or the tool is useless.
    assert platform.permissions.authorize(key, {}, session_allow=[key]).allowed


def test_namespaces_are_isolated_per_session(platform, tmp_path):
    async def body():
        a = _ctx(platform, tmp_path, "sA")
        b = _ctx(platform, tmp_path, "sB")
        await _invoke(platform, a, "repl", {"code": "secret = 'from-A'"})
        res = await _invoke(platform, b, "repl", {"code": "print('secret' in dir())"})
        assert res.ok
        assert "False" in res.output, "one session could see another's variables"

    run(platform, body)


def test_store_as_is_advertised_where_it_matters(platform):
    """A parameter no model is told about is a parameter no model will use —
    and putting it on all ~60 tools would cost more context than it saves."""
    specs = {s.get("name"): s for s in platform.registry.specs()}
    listing = specs.get("list_files") or {}
    params = listing.get("parameters") or listing.get("input_schema") or {}
    assert "_store_as" in (params.get("properties") or {}), (
        "list_files does not advertise _store_as, so nothing will ever use it"
    )
    # And NOT on a tool whose output is small.
    small = specs.get("write_file") or {}
    sparams = small.get("parameters") or small.get("input_schema") or {}
    assert "_store_as" not in (sparams.get("properties") or {})


def test_advertising_store_as_never_mutates_the_tool_itself(platform):
    """`spec()` hands back a dict that still REFERENCES the tool's class-level
    input_schema, so injecting in place permanently rewrote the tool's declared
    schema for the life of the process.

    Caught by the full suite, not by this file: a schema-shape test elsewhere
    passed alone and failed after anything had called specs(). Shared mutable
    state leaks across every consumer AND across tests, which is the shape of
    bug that gets diagnosed as flakiness.
    """
    tool = platform.registry.get("history_search")
    if tool is None:  # pragma: no cover — tool set varies by build
        pytest.skip("history_search not registered in this build")
    before = set((tool.input_schema.get("properties") or {}))
    for _ in range(3):
        platform.registry.specs()
    after = set((tool.input_schema.get("properties") or {}))
    assert after == before, "specs() rewrote the tool's own schema"
    assert "_store_as" not in after


# --------------------------------------------------------------------------- #
# (6) THE REAL BUILTINS ROUND-TRIP (v1.166.2 regression pins).
#
# The original suite only exercised a double that duplicated its payload into
# `data`, so `_store_as` shipped storing metadata and DESTROYING the payload of
# every real verbose tool. These pins go through the real list_files and shell.
# --------------------------------------------------------------------------- #
def test_real_list_files_payload_survives_store_as(platform, tmp_path):
    async def body():
        ctx = _ctx(platform, tmp_path)
        for n in ("alpha.txt", "beta.txt", "gamma.txt"):
            (ctx.workspace / n).write_text("x", encoding="utf-8")
        stored = await _invoke(
            platform, ctx, "list_files", {"_store_as": "tree"},
            allow=["list_files", "repl"],
        )
        assert stored.ok, stored.error
        assert "alpha.txt" not in stored.output  # a receipt, not the listing
        res = await _invoke(
            platform, ctx, "repl",
            {"code": "print(sorted(tree['output'].splitlines()))"},
        )
        assert res.ok, res.error
        for n in ("alpha.txt", "beta.txt", "gamma.txt"):
            assert n in res.output, (
                f"{n} unreachable — the real payload did not land: {res.output!r}"
            )

    run(platform, body)


def test_real_shell_stdout_survives_store_as(platform, tmp_path):
    async def body():
        ctx = _ctx(platform, tmp_path)
        stored = await _invoke(
            platform, ctx, "shell",
            {"command": "echo MAGIC_STDOUT_7431", "_store_as": "o"},
            allow=["shell", "repl"],
        )
        assert stored.ok, stored.error
        assert "MAGIC_STDOUT_7431" not in stored.output  # kept out of context
        res = await _invoke(platform, ctx, "repl", {"code": "print(o['output'])"})
        assert res.ok, res.error
        assert "MAGIC_STDOUT_7431" in res.output, (
            "stdout was destroyed by _store_as — the v1.166.2 bug is back"
        )

    run(platform, body)
