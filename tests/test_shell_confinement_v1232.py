"""A shell that ran UNCONFINED says so where the user looks (T5).

v1.232.0 (audit Wave 6, finding T5). When Docker is unavailable the sandboxed
shell falls back to the native runtime and the workspace/host/network limits
the policy asked for become advisory - a command CAN write outside the
workspace. The only trace of that was a paragraph inside the FIRST shell
result of a session (``_NO_CONFINEMENT_WARNING``): nothing on the session
page, no event field, no ledger column, and nothing at all for the second
call onward. The user's own install has no Docker, so this is the everyday
case, not the exotic one.

Now the tool answers one word - ``sandbox`` / ``native-unconfined`` /
``native`` - and ``registry.invoke`` carries it onto BOTH the ``ToolInvocation``
row and the ``tool.executed`` event, so the session page can fold the rows into
ONE chip ("Shell ran unconfined (Docker unavailable)") that is still true for a
finished run, long after the events stopped streaming.

Converted from the native-shell characterisation in
``tests/_audit_20260904/test_q2_workspace_binding.py``, deleted with this change.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.sandbox.docker_runtime import DockerSandbox
from iron_jarvis.sandbox.shell_tool import (
    CONFINEMENT_NATIVE_UNCONFINED,
    CONFINEMENT_SANDBOX,
)
from iron_jarvis.tools.base import ToolContext


def _ctx(platform, workspace, session_id="conf-s1") -> ToolContext:
    return ToolContext(
        workspace=workspace,
        session_id=session_id,
        agent_run_id="conf-r1",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _spy_events(platform, monkeypatch) -> list[dict]:
    """Every tool.executed payload, in order. The spy takes *args/**kw - a
    narrower signature has bitten this suite twice."""
    seen: list[dict] = []
    real = platform.event_bus.publish

    async def spy(etype, payload=None, *args, **kw):
        if etype == EventType.TOOL_EXECUTED:
            seen.append(dict(payload or {}))
        return await real(etype, payload, *args, **kw)

    monkeypatch.setattr(platform.event_bus, "publish", spy)
    return seen


@pytest.fixture
def no_docker(monkeypatch):
    """The live install's shape: Docker is not reachable."""
    monkeypatch.setattr(DockerSandbox, "available", lambda self: False)


async def test_native_fallback_is_named_native_unconfined(platform, tmp_path, no_docker):
    ws = tmp_path / "ws"
    ws.mkdir()
    res = await platform.registry.get("shell").execute(
        {"command": "echo hi"}, _ctx(platform, ws)
    )
    assert res.ok, res
    # ONE word a surface can read - not prose it would have to parse.
    assert res.data["confinement"] == CONFINEMENT_NATIVE_UNCONFINED
    assert CONFINEMENT_NATIVE_UNCONFINED != CONFINEMENT_SANDBOX
    # The advisory paragraph is still there for the model; the field is for the UI.
    assert "confinement_warning" in res.data


async def test_the_confinement_rides_the_ledger_row_and_the_event(
    platform, tmp_path, no_docker, monkeypatch
):
    ws = tmp_path / "ws"
    ws.mkdir()
    seen = _spy_events(platform, monkeypatch)
    ctx = _ctx(platform, ws, session_id="conf-ledger")
    res = await platform.registry.invoke(
        # `shell` is ask-tier; a session grant is the everyday shape (the user
        # approved it once) and keeps this test about the confinement field.
        "shell", {"command": "echo hi"}, ctx, platform.permissions,
        session_allow={"shell"},
    )
    assert res.ok, res

    assert seen and seen[-1]["tool"] == "shell"
    assert seen[-1]["confinement"] == CONFINEMENT_NATIVE_UNCONFINED

    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(select(ToolInvocation).where(ToolInvocation.session_id == "conf-ledger"))
        )
    assert [r.confinement for r in rows] == [CONFINEMENT_NATIVE_UNCONFINED]


async def test_a_tool_with_no_runtime_to_report_adds_no_field(platform, tmp_path, monkeypatch):
    """Absent, not "unknown": a field on every row would read as a claim."""
    seen = _spy_events(platform, monkeypatch)
    ctx = _ctx(platform, tmp_path, session_id="conf-none")
    await platform.registry.invoke(
        "worklist_add", {"items": "one\ntwo"}, ctx, platform.permissions
    )
    assert seen and "confinement" not in seen[-1]
    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(select(ToolInvocation).where(ToolInvocation.session_id == "conf-none"))
        )
    assert rows and all(r.confinement is None for r in rows)
