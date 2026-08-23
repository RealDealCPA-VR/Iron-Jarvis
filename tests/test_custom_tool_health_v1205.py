"""v1.205.0 — custom tools whose PROGRAM does not exist (the dead-`mv` failure).

THE MEASURED FAILURE, from a live task (organizing tax documents, 39 errors):
a past session's agent used `tool_create` to author `rename_real_file` around
POSIX `mv`. Nothing validated that `mv` exists on a Windows install, so the
dead tool was persisted, advertised to every future run, and failed 22/22
times with "command not found: 'mv'" — while the built-in rename_file
succeeded 15/15 alongside it.

Three properties pinned here, each with a silent failure mode:

* CREATE-TIME: both doors (the `tool_create` agent tool AND POST /tools/custom)
  REFUSE a command whose argv[0] does not resolve (`shutil.which`, so Windows
  PATHEXT applies), with the SAME honest error naming the missing program and
  the built-in that already covers the job — and NOTHING is persisted;
* ADVERTISE-TIME: a PERSISTED dead tool (created before the check, or whose
  program was uninstalled later) is not offered to models (`registry.specs`,
  the one seam every model-facing catalog builds through) but STAYS in the
  management listing (GET /tools/custom — the Tools page source) so the user
  can see and delete it. Never deleted automatically;
* INVOKE-TIME: calling it anyway fails with the honest error + the pointer to
  the Tools page — never a bare "command not found", and never a misleading
  "missing required: ..." on a tool that can never run.
"""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient

import iron_jarvis.tools.dynamic as dynamic
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.dynamic import missing_program_error

#: A program that provably does not exist on any machine running this suite.
_DEAD = "ij_v1205_definitely_not_installed"


def _ctx(p, workspace):
    return ToolContext(
        workspace=workspace,
        session_id="s1",
        agent_run_id="r1",
        config=p.config,
        event_bus=p.event_bus,
        engine=p.engine,
    )


# --------------------------------------------------------------------------- #
# 1. CREATE-TIME REFUSAL — the agent door (tool_create).
# --------------------------------------------------------------------------- #


async def test_tool_create_refuses_a_program_that_is_not_installed(platform, tmp_path):
    create = platform.registry.get("tool_create")
    res = await create.execute(
        {
            "name": "dead_tool",
            "description": "runs a program this machine does not have",
            "command": [_DEAD, "{a}"],
            "parameters": [{"name": "a", "type": "string", "required": True}],
        },
        _ctx(platform, tmp_path),
    )
    assert not res.ok
    err = res.error or ""
    assert _DEAD in err, "the refusal must NAME the missing program"
    assert "is not installed on this machine" in err
    assert "custom tools run real programs" in err
    # NOTHING persisted, NOTHING registered — a refusal that still writes the
    # record is the original bug with a nicer message.
    assert platform.tools_registry.get("dead_tool") is None
    assert platform.registry.get("dead_tool") is None


async def test_the_mv_refusal_points_at_rename_file(platform, tmp_path, monkeypatch):
    """The live shape exactly: `mv` on a machine where it isn't installed.

    The dev box HAS Git's mv.EXE on PATH, so the resolver seam stands in for
    the user's packaged install (where the daemon saw no Git bin dir)."""
    real = dynamic._which
    monkeypatch.setattr(
        dynamic,
        "_which",
        lambda prog: None if str(prog).strip().lower() == "mv" else real(prog),
    )
    create = platform.registry.get("tool_create")
    res = await create.execute(
        {
            "name": "rename_real_file",
            "command": ["mv", "{src}", "{dst}"],
            "parameters": [
                {"name": "src", "required": True},
                {"name": "dst", "required": True},
            ],
        },
        _ctx(platform, tmp_path),
    )
    assert not res.ok
    err = res.error or ""
    assert "'mv' is not installed on this machine" in err
    # The reason the refusal exists: the agent should pick the built-in.
    assert "rename_file" in err


async def test_tool_create_accepts_a_real_program(platform, tmp_path):
    create = platform.registry.get("tool_create")
    res = await create.execute(
        {
            "name": "alive_tool",
            "command": [sys.executable, "-c", "print('alive')"],
            "parameters": [],
        },
        _ctx(platform, tmp_path),
    )
    assert res.ok, res.error
    assert platform.tools_registry.get("alive_tool") is not None
    run = await platform.registry.get("alive_tool").execute({}, _ctx(platform, tmp_path))
    assert run.ok and "alive" in run.output


# --------------------------------------------------------------------------- #
# 2. CREATE-TIME REFUSAL — the route door, with the IDENTICAL message.
# --------------------------------------------------------------------------- #


def test_route_refuses_with_the_same_error_as_the_agent_door(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/tools/custom",
        json={"name": "dead_tool", "description": "d", "command": [_DEAD, "{a}"]},
    )
    assert r.status_code == 400
    # Both doors speak through missing_program_error — assert the route relays
    # it VERBATIM, so the two can never drift.
    assert r.json()["detail"] == missing_program_error([_DEAD, "{a}"])
    assert _DEAD in r.json()["detail"]
    # nothing persisted
    assert client.get("/tools/custom").json()["tools"] == []
    # a real program is accepted through the same door
    ok = client.post(
        "/tools/custom",
        json={"name": "alive_tool", "command": [sys.executable, "-c", "print(1)"]},
    )
    assert ok.status_code == 200 and ok.json()["name"] == "alive_tool"


# --------------------------------------------------------------------------- #
# 3. ADVERTISE-TIME HEALTH — persisted dead tool: hidden from models,
#    visible + deletable in management, never auto-deleted.
# --------------------------------------------------------------------------- #


def _register_persisted(platform, name: str, argv: list[str]):
    """A tool the way a PRE-v1.205.0 install holds it: straight into the
    dynamic-tool registry (no door validation), rebuilt into the live
    registry exactly as build_platform does at boot."""
    rec = platform.tools_registry.register(name, f"custom tool {name}", [], argv)
    tool = platform.tools_registry.build_tool(rec)
    platform.registry.register(tool, custom=True)
    return tool


def test_persisted_dead_tool_is_not_advertised_but_still_managed(platform):
    _register_persisted(platform, "dead_tool", [_DEAD, "x"])
    _register_persisted(platform, "alive_tool", [sys.executable, "-c", "print(1)"])

    # (b) NOT advertised to models — neither in the full catalog nor through
    # the "custom:*" allowlist sentinel agents use.
    for allowed in (None, ["custom:*"]):
        names = {s["name"] for s in platform.registry.specs(allowed)}
        assert "dead_tool" not in names, f"dead tool advertised (allowed={allowed})"
        # the gate hides THE DEAD ONE, not custom tools wholesale
        assert "alive_tool" in names, f"healthy custom tool hidden (allowed={allowed})"

    # (a) still in the management listing (the Tools page reads this) and
    # still resolvable by name — the user's data is never deleted for them.
    assert any(r.name == "dead_tool" for r in platform.tools_registry.list())
    assert platform.registry.get("dead_tool") is not None


def test_tools_page_still_lists_and_deletes_a_dead_tool(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    p = client.app.state.platform
    _register_persisted(p, "dead_tool", [_DEAD, "x"])

    listed = [t["name"] for t in client.get("/tools/custom").json()["tools"]]
    assert "dead_tool" in listed, "the Tools page must still show it"
    advertised = [t["name"] for t in client.get("/tools").json()["tools"]]
    assert "dead_tool" not in advertised
    assert client.delete("/tools/custom/dead_tool").json()["removed"] is True
    assert client.get("/tools/custom").json()["tools"] == []


def test_a_tool_whose_program_disappears_stops_being_advertised(
    platform, monkeypatch
):
    """The UNINSTALL path: created healthy, program removed later. The probe
    is TTL-cached, so the cache is aged out the way a later specs() refresh
    would see it."""
    tool = _register_persisted(
        platform, "flaky_tool", [sys.executable, "-c", "print(1)"]
    )
    assert "flaky_tool" in {s["name"] for s in platform.registry.specs()}

    monkeypatch.setattr(dynamic, "_which", lambda prog: None)
    tool._health_at = 0.0  # age the TTL cache out
    assert "flaky_tool" not in {s["name"] for s in platform.registry.specs()}
    # still the user's to manage
    assert any(r.name == "flaky_tool" for r in platform.tools_registry.list())


def test_templated_argv0_is_never_flagged():
    # ``["{prog}", "{arg}"]`` names no program — nothing to check at create or
    # advertise time (the capability deny-floor screens that hole separately).
    assert missing_program_error(["{prog}", "{arg}"]) == ""
    assert missing_program_error([]) == ""
    assert missing_program_error([sys.executable, "-c", "x"]) == ""
    assert missing_program_error([_DEAD]) != ""


# --------------------------------------------------------------------------- #
# 4. INVOKE-TIME HONESTY — calling a dead tool anyway.
# --------------------------------------------------------------------------- #


async def test_invoking_a_dead_tool_fails_honestly(platform, tmp_path):
    rec = platform.tools_registry.register(
        "dead_tool",
        "dead",
        [{"name": "a", "type": "string", "required": True}],
        [_DEAD, "{a}"],
    )
    tool = platform.tools_registry.build_tool(rec)
    # No args at all: the DEAD PROGRAM must answer, not "missing required" —
    # an argument complaint on a tool that can never run sends the model
    # fixing the wrong thing (mutation guard on the check ordering).
    res = await tool.execute({}, _ctx(platform, tmp_path))
    assert not res.ok
    err = res.error or ""
    assert "is not installed on this machine" in err
    assert "see the Tools page" in err
    assert "missing required" not in err


async def test_tool_list_marks_a_dead_tool_unavailable(platform, tmp_path):
    platform.tools_registry.register("dead_tool", "dead", [], [_DEAD])
    platform.tools_registry.register(
        "alive_tool", "alive", [], [sys.executable, "-c", "print(1)"]
    )
    res = await platform.registry.get("tool_list").execute({}, _ctx(platform, tmp_path))
    assert res.ok
    lines = {ln.split(":")[0]: ln for ln in res.output.splitlines()}
    assert "[unavailable" in lines["dead_tool"]
    assert "[unavailable" not in lines["alive_tool"]
