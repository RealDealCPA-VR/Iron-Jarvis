"""v1.342.0 ("the pack says why"), lane B-status: a probe reports the REAL
load error, a crash is classified as a crash, and a loaded pack whose calls
fail is "failing" with one ``mcp.pack_failed`` per episode.

Before this lane ``_load_one_server`` swallowed a probe's failure when
``record=False``, so ``POST /mcp/servers/{name}/test`` and the Directory's
Test answered "connected but the server advertised no tools" for EVERY
failure — a missing launcher, a child that died saying "BRAVE_API_KEY is not
set", a timeout — and ``classify_load_failure`` read "closed the connection"
as an unreachable server. Now ``mcp_tools(errors=)`` hands the failure back
(error, reason, fix, the child's masked stderr tail), the crash branch is
checked first, the load record carries ``stderr_tail`` + ``failures`` +
``state`` in {starting, ok, failed, failing}, and ``note_call_failure`` /
``note_call_success`` drive the episode.

Every dying child here is REAL (``tests/fixtures/stdio_stderr_server_v1342.py``,
lane A's fixture); only the server is fake. Secrets are PLANTED by it and
asserted absent from every answer.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventBus
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp import FakeTransport
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.client import MCPError
from iron_jarvis.mcp.tools import (
    EVENT_PACK_FAILED,
    MCPRemoteTool,
    PackLoadFailed,
    classify_load_failure,
    load_status,
    mcp_tools,
    note_call_failure,
    note_call_success,
)
from iron_jarvis.tools.base import ToolContext

FIXTURES = Path(__file__).parent / "fixtures"
SERVER = str(FIXTURES / "stdio_stderr_server_v1342.py")
#: A real, healthy stdio server (advertises `echo` + `add`).
ECHO_SERVER = str(FIXTURES / "echo_mcp_server.py")

#: Every secret VALUE the fixture plants; none may leave the loader.
_SECRET_VALUES = ("abc123", "sk-live-secretvalue")

#: A stdio command that cannot exist (the live "npx not found" shape).
NO_SUCH_COMMAND = "ij-no-such-mcp-launcher-v1342"

TOOLS_LIST = {
    "tools": [
        {"name": "search", "description": "Search.", "inputSchema": {"type": "object", "properties": {}}},
    ]
}


def _dying_cfg(name: str = "dying") -> dict:
    """A pack whose child reads the handshake, prints its planted lines to
    stderr and exits 1 without answering."""
    return {"name": name, "command": sys.executable, "args": [SERVER, "--mode", "die"]}


def _assert_no_secret(text: str) -> None:
    for value in _SECRET_VALUES:
        assert value not in (text or ""), f"secret value {value!r} leaked: {text!r}"


@pytest.fixture(autouse=True)
def _fresh_records():
    mcp_tools_mod._LOAD_STATUS.clear()
    mcp_tools_mod._ANNOUNCED.clear()
    mcp_tools_mod._LOAD_CFGS.clear()
    mcp_tools_mod.set_event_bus(None)
    yield
    mcp_tools_mod._LOAD_STATUS.clear()
    mcp_tools_mod._ANNOUNCED.clear()
    mcp_tools_mod._LOAD_CFGS.clear()
    mcp_tools_mod.set_event_bus(None)


class _Bus:
    """An async bus double that only records what was published."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def publish(self, type: str, payload: dict | None = None, session_id=None):
        self.events.append((type, dict(payload or {})))


def _pack_failed(bus: _Bus) -> list[dict]:
    return [p for t, p in bus.events if t == EVENT_PACK_FAILED]


# --------------------------------------------------------------------------- #
# B1(b): a crash is a crash, checked before the "connect" branch.
# --------------------------------------------------------------------------- #
CRASH_TEXT = (
    "MCPServerStopped: the MCP server closed the connection (exit code 1); "
    "its last output: Error: the pack needs a key (BRAVE_API_KEY is not set); "
    "it will be restarted on the next call."
)


def test_a_closed_connection_is_classified_as_a_crash_not_unreachable():
    verdict = classify_load_failure({"command": "npx"}, CRASH_TEXT)
    assert verdict["reason"] == "the pack's server exited (exit code 1)"
    assert verdict["fix"] == "read its last output below; a missing key or setting is the usual cause"
    assert "reached" not in verdict["reason"], "the connect branch misread the crash"


def test_a_crash_whose_tail_names_a_missing_file_is_still_a_crash():
    # The tail is the CHILD's problem (its own ENOENT), not our launcher's.
    text = CRASH_TEXT.replace("the pack needs a key", "ENOENT: no such file or directory, open x.json")
    verdict = classify_load_failure({"command": "npx"}, text)
    assert verdict["reason"].startswith("the pack's server exited")


def test_a_crash_without_an_exit_code_still_says_exited():
    verdict = classify_load_failure(None, "MCPServerStopped: the MCP server closed the connection")
    assert verdict["reason"] == "the pack's server exited"
    assert verdict["fix"]


def test_a_missing_launcher_is_still_the_launcher_story():
    verdict = classify_load_failure({"command": NO_SUCH_COMMAND}, f"FileNotFoundError: launcher '{NO_SUCH_COMMAND}' was not found on PATH")
    assert NO_SUCH_COMMAND in verdict["reason"]
    assert "exited" not in verdict["reason"]


# --------------------------------------------------------------------------- #
# B1(a): the loader hands a probe's failure back through `errors`.
# --------------------------------------------------------------------------- #
def test_a_probe_of_a_dying_pack_reports_the_crash_with_its_masked_tail():
    errors: dict = {}
    assert mcp_tools([_dying_cfg()], record=False, errors=errors) == []
    assert load_status("dying") is None, "a probe must not write the load record"
    failure = errors["dying"]
    assert failure["error"].startswith("MCPServerStopped: pack 'dying': "), failure["error"]
    assert "the MCP server closed the connection (exit code 1)" in failure["error"]
    assert failure["reason"] == "the pack's server exited (exit code 1)"
    assert failure["fix"] == "read its last output below; a missing key or setting is the usual cause"
    assert "BRAVE_API_KEY=***" in failure["stderr_tail"], failure["stderr_tail"]
    assert "the pack needs a key" in failure["stderr_tail"]
    _assert_no_secret(failure["error"])
    _assert_no_secret(failure["stderr_tail"])


def test_a_recorded_load_of_a_dying_pack_keeps_the_tail_on_the_record():
    errors: dict = {}
    assert mcp_tools([_dying_cfg()], errors=errors) == []
    rec = load_status("dying")
    assert rec["state"] == "failed"
    assert rec["failures"] == 0
    assert rec["last_error"] == errors["dying"]["error"]
    assert rec["reason"] == errors["dying"]["reason"]
    assert "BRAVE_API_KEY=***" in rec["stderr_tail"]
    assert len(rec["stderr_tail"]) <= 2000
    _assert_no_secret(rec["stderr_tail"])
    _assert_no_secret(rec["last_error"])


def test_a_probe_of_a_missing_launcher_reports_the_launcher():
    errors: dict = {}
    assert mcp_tools([{"name": "brave", "command": NO_SUCH_COMMAND, "args": []}], record=False, errors=errors) == []
    failure = errors["brave"]
    assert "FileNotFoundError" in failure["error"]
    assert NO_SUCH_COMMAND in failure["reason"]
    assert failure["fix"]
    assert failure["stderr_tail"] == ""  # nothing ever ran


def test_a_probe_that_connects_writes_no_error_entry():
    errors: dict = {}
    tools = mcp_tools([{"name": "fine", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}], record=False, errors=errors)
    assert len(tools) == 1
    assert errors == {}


def test_connect_with_timeout_raises_pack_load_failed_carrying_the_cause_and_tail():
    with pytest.raises(PackLoadFailed) as exc:
        mcp_tools_mod._connect_with_timeout(_dying_cfg(), "dying", None, 30.0)
    assert type(exc.value.cause).__name__ == "MCPServerStopped"
    assert str(exc.value).startswith("MCPServerStopped: ")
    assert "BRAVE_API_KEY=***" in exc.value.stderr_tail
    _assert_no_secret(exc.value.stderr_tail)


def test_a_healthy_load_records_ok_with_an_empty_tail_and_zero_failures():
    mcp_tools([{"name": "fine", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}])
    rec = load_status("fine")
    assert rec["state"] == "ok"
    assert rec["failures"] == 0
    assert rec["stderr_tail"] == ""
    assert rec["last_error"] is None


def test_mark_starting_carries_the_new_fields():
    mcp_tools_mod.mark_starting([{"name": "p"}])
    rec = load_status("p")
    assert rec["state"] == "starting"
    assert rec["stderr_tail"] == "" and rec["failures"] == 0


# --------------------------------------------------------------------------- #
# B2: the Test route answers the real error; "advertised no tools" only when true.
# --------------------------------------------------------------------------- #
def test_test_route_answers_the_real_crash_not_advertised_no_tools(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [_dying_cfg()]
    r = client.post("/mcp/servers/dying/test").json()
    assert r["ok"] is False and r["tools"] == []
    assert "advertised no tools" not in (r["error"] or "")
    assert r["error"].startswith("MCPServerStopped: pack 'dying': "), r["error"]
    assert "the MCP server closed the connection (exit code 1)" in r["error"]
    assert r["reason"] == "the pack's server exited (exit code 1)"
    assert r["fix"] == "read its last output below; a missing key or setting is the usual cause"
    assert "BRAVE_API_KEY=***" in r["stderr_tail"]
    _assert_no_secret(r["error"])
    _assert_no_secret(r["stderr_tail"])
    assert load_status("dying") is None, "the Test route is a probe: no record"


def test_test_route_names_a_missing_launcher(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [{"name": "brave", "command": NO_SUCH_COMMAND, "args": []}]
    r = client.post("/mcp/servers/brave/test").json()
    assert r["ok"] is False
    assert "FileNotFoundError" in r["error"]
    assert NO_SUCH_COMMAND in r["reason"]
    assert "advertised no tools" not in r["error"]


def test_test_route_says_advertised_no_tools_only_when_it_really_connected(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [
        {"name": "empty", "transport_obj": FakeTransport({"tools/list": {"tools": []}})}
    ]
    r = client.post("/mcp/servers/empty/test").json()
    assert r["ok"] is False and r["tools"] == []
    assert r["error"] == "connected but the server advertised no tools"
    assert r["reason"] == "" and r["fix"] == "" and r["stderr_tail"] == ""


def test_test_route_green_answer_carries_the_new_keys(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [{"name": "fine", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}]
    r = client.post("/mcp/servers/fine/test").json()
    assert r["ok"] is True and r["tools"] == ["search"] and r["error"] is None
    assert r["stderr_tail"] == ""


# --------------------------------------------------------------------------- #
# B3: the Directory's Test uses the same mechanism.
# --------------------------------------------------------------------------- #
def test_directory_test_reports_the_real_error(tmp_path):
    from iron_jarvis.connectors import service as connectors

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [_dying_cfg()]
    r = connectors.test(platform, "dying")
    assert r["ok"] is False and r["tools"] == []
    assert "advertised no tools" not in (r["error"] or "")
    assert r["error"].startswith("MCPServerStopped: pack 'dying': "), r["error"]
    assert "closed the connection (exit code 1)" in r["error"]
    assert r["reason"] == "the pack's server exited (exit code 1)"
    assert "BRAVE_API_KEY=***" in r["stderr_tail"]
    _assert_no_secret(r["stderr_tail"])
    assert load_status("dying") is None


def test_directory_test_still_says_no_tools_when_it_connected_with_none(tmp_path):
    from iron_jarvis.connectors import service as connectors

    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [{"name": "empty", "transport_obj": FakeTransport({"tools/list": {"tools": []}})}]
    r = connectors.test(platform, "empty")
    assert r["ok"] is False and r["error"] == "connected but advertised no tools"


# --------------------------------------------------------------------------- #
# B1(c): note_call_failure / note_call_success and the one-per-episode event.
# --------------------------------------------------------------------------- #
def test_call_failures_flip_the_record_to_failing_and_announce_once_per_episode():
    mcp_tools([{"name": "srv", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}])
    bus = _Bus()
    mcp_tools_mod.set_event_bus(bus)

    first = note_call_failure("srv", "MCPServerStopped: the MCP server closed the connection (exit code 2)")
    rec = load_status("srv")
    assert rec["state"] == "failing" and rec["failures"] == 1
    assert rec["last_error"].startswith("MCPServerStopped:")
    assert rec["reason"] == "the pack's server exited (exit code 2)"
    assert first == {
        "name": "srv",
        "error": "MCPServerStopped: the MCP server closed the connection (exit code 2)",
        "reason": "the pack's server exited (exit code 2)",
        "fix": "read its last output below; a missing key or setting is the usual cause",
    }
    assert _pack_failed(bus) == [first]

    second = note_call_failure("srv", "MCPError: boom")
    assert second is None
    rec = load_status("srv")
    assert rec["failures"] == 2 and rec["state"] == "failing"
    assert rec["last_error"] == "MCPError: boom"
    assert len(_pack_failed(bus)) == 1, "the second failure of an episode is quiet"

    note_call_success("srv")
    rec = load_status("srv")
    assert rec["state"] == "ok" and rec["failures"] == 0 and rec["last_error"] is None
    assert rec["reason"] == "" and rec["fix"] == ""

    third = note_call_failure("srv", "MCPError: again")
    assert third is not None
    assert len(_pack_failed(bus)) == 2, "a success ended the episode; the next failure speaks"


def test_a_success_on_a_healthy_record_changes_nothing():
    mcp_tools([{"name": "srv", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}])
    before = load_status("srv")
    note_call_success("srv")
    assert load_status("srv") == before
    note_call_success("never-loaded")  # no record: a no-op, never a crash
    assert load_status("never-loaded") is None


def test_the_announced_error_is_masked():
    mcp_tools([{"name": "srv", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}])
    bus = _Bus()
    payload = note_call_failure(
        "srv", "MCPError: refused Authorization: Bearer abc123 for key=sk-live-secretvalue", event_bus=bus
    )
    _assert_no_secret(payload["error"])
    _assert_no_secret(load_status("srv")["last_error"])
    _assert_no_secret(_pack_failed(bus)[0]["error"])


def test_a_failed_load_announces_once_and_a_retry_opens_a_new_episode():
    bus = _Bus()
    mcp_tools_mod.set_event_bus(bus)
    mcp_tools([{"name": "bad", "transport_obj": FakeTransport(raise_on=True)}])
    assert [p["name"] for p in _pack_failed(bus)] == ["bad"]
    assert _pack_failed(bus)[0]["error"].startswith("MCPError: ")
    assert set(_pack_failed(bus)[0]) == {"name", "error", "reason", "fix"}
    # A call-time note after a failed load is the SAME episode: quiet.
    assert note_call_failure("bad", "MCPError: still") is None
    assert len(_pack_failed(bus)) == 1
    # A Retry (a new load record) is a new episode, announced again when it fails.
    mcp_tools([{"name": "bad", "transport_obj": FakeTransport(raise_on=True)}])
    assert len(_pack_failed(bus)) == 2
    # A Retry that SUCCEEDS ends it quietly.
    mcp_tools([{"name": "bad", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}])
    assert len(_pack_failed(bus)) == 2
    assert load_status("bad")["state"] == "ok"


def test_a_failed_load_with_no_bus_wired_records_and_announces_nothing():
    mcp_tools([{"name": "bad", "transport_obj": FakeTransport(raise_on=True)}])
    assert load_status("bad")["state"] == "failed"


def test_a_probe_failure_announces_nothing():
    bus = _Bus()
    mcp_tools_mod.set_event_bus(bus)
    mcp_tools([{"name": "bad", "transport_obj": FakeTransport(raise_on=True)}], record=False, errors={})
    assert _pack_failed(bus) == []


# --------------------------------------------------------------------------- #
# MCPRemoteTool.execute drives the notes on the call's own bus.
# --------------------------------------------------------------------------- #
def _ctx(bus) -> ToolContext:
    return ToolContext(
        workspace=Path("."),
        session_id="s1",
        agent_run_id="r1",
        config=SimpleNamespace(),
        event_bus=bus,
        engine=None,
    )


def test_execute_notes_a_failure_on_the_context_bus_and_a_success_after():
    mcp_tools([{"name": "srv", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}])
    bus = EventBus()
    failing = FakeTransport(raise_on="tools/call", error=MCPError("the MCP server closed the connection (exit code 1)"))
    tool = MCPRemoteTool(mcp_tools_mod.MCPClient(failing, name="srv"), "srv", "search")

    async def run():
        r1 = await tool.execute({}, _ctx(bus))
        r2 = await tool.execute({}, _ctx(bus))
        return r1, r2

    r1, r2 = asyncio.run(run())
    assert r1.ok is False and r1.error.startswith("MCPError: the MCP server closed the connection")
    assert r2.ok is False
    rec = load_status("srv")
    assert rec["state"] == "failing" and rec["failures"] == 2
    announced = [e for e in bus.history if e.type == EVENT_PACK_FAILED]
    assert len(announced) == 1, "one announcement per episode, awaited before the result"
    assert announced[0].payload["name"] == "srv"
    assert announced[0].payload["reason"] == "the pack's server exited (exit code 1)"

    answering = FakeTransport({"tools/call": {"content": [{"type": "text", "text": "hi"}]}})
    tool.client = mcp_tools_mod.MCPClient(answering, name="srv")
    r3 = asyncio.run(tool.execute({}, _ctx(bus)))
    assert r3.ok is True
    rec = load_status("srv")
    assert rec["state"] == "ok" and rec["failures"] == 0 and rec["last_error"] is None


def test_execute_masks_the_error_the_model_sees():
    mcp_tools([{"name": "srv", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}])
    failing = FakeTransport(raise_on="tools/call", error=MCPError("401 for Authorization: Bearer abc123"))
    tool = MCPRemoteTool(mcp_tools_mod.MCPClient(failing, name="srv"), "srv", "search")
    r = asyncio.run(tool.execute({}, _ctx(EventBus())))
    assert r.ok is False
    _assert_no_secret(r.error)
    assert "Authorization: Bearer ***" in r.error


def test_a_tool_level_is_error_counts_as_an_answer_not_a_pack_failure():
    mcp_tools([{"name": "srv", "transport_obj": FakeTransport({"tools/list": TOOLS_LIST})}])
    note_call_failure("srv", "MCPError: earlier")
    saying_no = FakeTransport({"tools/call": {"isError": True, "content": [{"type": "text", "text": "bad args"}]}})
    tool = MCPRemoteTool(mcp_tools_mod.MCPClient(saying_no, name="srv"), "srv", "search")
    bus = EventBus()
    r = asyncio.run(tool.execute({}, _ctx(bus)))
    assert r.ok is False and r.error == "bad args"
    assert load_status("srv")["state"] == "ok", "the pack answered; the episode is over"
    assert not [e for e in bus.history if e.type == EVENT_PACK_FAILED]


# --------------------------------------------------------------------------- #
# B2: the GET row and the POST add note.
# --------------------------------------------------------------------------- #
def test_mcp_servers_row_carries_state_failures_and_stderr_tail(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    # Real commands only: a `transport_obj` row cannot be serialised by the GET.
    platform.config.mcp_servers = [
        _dying_cfg(),
        {"name": "fine", "command": sys.executable, "args": [ECHO_SERVER]},
    ]
    for tool in mcp_tools(platform.config.mcp_servers, secret_resolver=platform.secrets.get):
        platform.registry.register(tool, mcp=True)
    rows = {r["name"]: r for r in client.get("/mcp/servers").json()["servers"]}
    dying = rows["dying"]
    assert dying["state"] == "failed" and dying["failures"] == 0
    assert dying["reason"] == "the pack's server exited (exit code 1)"
    assert "BRAVE_API_KEY=***" in dying["stderr_tail"]
    _assert_no_secret(dying["stderr_tail"])
    _assert_no_secret(dying["last_error"])
    fine = rows["fine"]
    assert fine["state"] == "ok" and fine["failures"] == 0 and fine["tools_loaded"] == 2
    assert isinstance(fine["stderr_tail"], str)
    # A loaded pack whose calls fail reads "failing" with the count.
    note_call_failure("fine", "MCPError: boom")
    note_call_failure("fine", "MCPError: boom")
    fine = {r["name"]: r for r in client.get("/mcp/servers").json()["servers"]}["fine"]
    assert fine["state"] == "failing" and fine["failures"] == 2
    assert fine["last_error"] == "MCPError: boom"


def test_mcp_servers_row_for_a_pack_never_attempted(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    platform.config.mcp_servers = [{"name": "quiet", "command": "uvx"}]
    row = client.get("/mcp/servers").json()["servers"][0]
    assert row["state"] is None and row["failures"] == 0 and row["stderr_tail"] == ""


def test_add_route_note_says_why_the_pack_did_not_start(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post("/mcp/servers", json={"name": "brave", "command": NO_SUCH_COMMAND, "args": []}).json()
    assert r["added"] is True and r["tools_loaded"] == 0
    assert "did not start:" in r["note"]
    assert NO_SUCH_COMMAND in r["note"]
    assert "see its row for the fix" in r["note"]
    assert "restart the daemon" not in r["note"]


def test_add_route_note_for_a_crashing_pack_names_the_crash(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/mcp/servers", json={"name": "dying", "command": sys.executable, "args": [SERVER, "--mode", "die"]}
    ).json()
    assert r["tools_loaded"] == 0
    assert "did not start: the pack's server exited (exit code 1)" in r["note"]
    row = client.get("/mcp/servers").json()["servers"][0]
    assert row["state"] == "failed" and "BRAVE_API_KEY=***" in row["stderr_tail"]
