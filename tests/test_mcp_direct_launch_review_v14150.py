"""v1.415.0 review — defects in the direct start of ``npx -y`` / ``uvx`` packs.

Each pin below was red on a2d74dcd and goes green with the fix beside it.

1. ``npx`` on Windows is ``npx.cmd``, so a launcher start runs through
   ``cmd.exe``: ``%NAME%`` in an argument is EXPANDED and a ``^`` is REMOVED
   before npx ever sees it (measured: ``%USERPROFILE%\\Desktop`` arrives as
   ``C:\\Users\\VR\\Desktop``, ``pkg@^1.2.3`` as ``pkg@1.2.3``). A direct start
   hands the same text to ``node.exe`` untouched, so the server got DIFFERENT
   arguments — and still answered ``initialize``, so nothing fell back. A
   ``.cmd`` launcher line carrying cmd's special characters is the launcher's
   to run.
2. A kept answer whose npm cache sits behind a junction (or a ``subst``
   drive) never started directly: the bin target was resolved, the package
   folder was not, so ``_inside`` said "missing" — a STALE answer, which
   re-asks npx after EVERY start (one 80 MB npm process and a registry round
   trip each time, forever).
3. ``uvx pkg@1.2`` runs ``pkg==1.2``, which uv satisfies with ``1.2.0``; the
   string compare called that a mismatch — stale, so again a uv ask after
   every start and never a direct one.
4. A healthy server that is merely SLOW to answer (10-15 s: a cold Python
   pack at boot) was killed at ``DIRECT_INIT_TIMEOUT_S`` and restarted through
   the launcher, which needs MORE time than the direct start did — so it
   could no longer fit the connect bound and a pack that loaded before the
   change failed. Now, when the caller bounds the start (the boot connect
   probe), a direct start is given the same time the launcher would have had;
   a direct start that is still silent when that bound runs out is held on
   its launcher and the load is retried ONCE through it.
5. ``build_platform`` armed background asks in EVERY process that built a
   platform — every CLI one-shot too, contrary to the module's own contract
   ("a CLI one-shot … never asks"): an npm/uv install started there is killed
   half way when the command exits. Only the daemon (``defer_mcp``) asks now;
   everyone still READS the kept answers.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from iron_jarvis.mcp import direct_launch as dl
from iron_jarvis.mcp import tools as mcp_tools_mod
from tests.test_mcp_direct_launch_v14150 import (  # the author's harness, reused
    FIX,
    NODE,
    _load,
    _whoami,
    make_npx_cache,
    make_uv_env,
    needs_node,
    npx_record,
    pack_cfg,
    state,  # noqa: F401 — the fixture
    uv_spec,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="direct starts are Windows-only")


def _cleanup(tools):
    mcp_tools_mod.close_tools_clients(tools)
    mcp_tools_mod.forget_load_record("directpack")
    mcp_tools_mod.forget_live_client("directpack")


# --------------------------------------------------------------------------- #
# 1. cmd.exe rewrites a .cmd launcher's arguments
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("args", [
    ["-y", "pkg", "%USERPROFILE%\\Desktop"],
    ["-y", "pkg", "a^b"],
    ["-y", "pkg@^1.2.3"],
    ["-y", "pkg", "a&b"],
    ["-y", "pkg", 'say "hi"'],
    ["-y", "pkg", "x|y"],
    ["-y", "pkg", "line1\nline2"],
])
def test_a_cmd_launcher_line_cmd_would_rewrite_keeps_the_launcher(args):
    assert dl.parse("C:/n/npx.cmd", args) is None


def test_the_same_text_is_fine_where_no_cmd_runs():
    # uvx.exe / an npx.exe (volta) start without cmd.exe: the text arrives as is.
    assert dl.parse("C:/u/uvx.exe", ["pkg", "%APPDATA%\\x"]) is not None
    assert dl.parse("C:/n/npx.exe", ["-y", "pkg@^1.2.3"]) is not None
    assert dl.parse("C:/n/npx.cmd", ["-y", "pkg", "C:\\a b\\c", "--k=v,w;z"]) is not None


@needs_node
def test_a_percent_variable_argument_reaches_the_server_as_the_launcher_gives_it(
    tmp_path, state
):
    c = make_npx_cache(tmp_path, "pkg", "pkg")
    want = str(tmp_path / "chosen-folder")  # unquoted after expansion: no space
    cfg = pack_cfg(tmp_path, c, "pkg", args=("%IJ_REVIEW_DIR%",))
    cfg["env"]["IJ_REVIEW_DIR"] = want
    # an answer exists for the line as written (what a2d74dcd would use)
    spec = dl.LaunchSpec(
        kind="npx", launcher=cfg["command"], launcher_args=(), spec="pkg", name="pkg",
        version="", server_args=("%IJ_REVIEW_DIR%",), cwd=cfg["cwd"],
        key=dl._key_for("npx", cfg["command"], (), "pkg", cfg["cwd"],
                        {**os.environ, **cfg["env"]}),
    )
    dl.put_record(spec, npx_record(c))
    tools, client = _load(cfg)
    try:
        assert _whoami(client)["argv"] == [want]
    finally:
        _cleanup(tools)


# --------------------------------------------------------------------------- #
# 2. a cache reached through a junction
# --------------------------------------------------------------------------- #
def _junction(link: Path, target: Path) -> None:
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"no junction here: {r.stdout} {r.stderr}")


def test_a_cache_behind_a_junction_starts_directly(tmp_path, state):
    real = tmp_path / "real"
    real.mkdir()
    c = make_npx_cache(real, "pkg", "pkg")
    _junction(tmp_path / "via", real)
    via_cache = tmp_path / "via" / "npm-cache"
    s = dl.parse(str(tmp_path / "npx.cmd"), ["-y", "pkg"])
    rec = npx_record(c)
    rec["cache"] = str(via_cache)
    rec["path_prepend"] = [str(via_cache / "_npx" / dl.npx_hash("pkg") / "node_modules" / ".bin")]
    dl.put_record(s, rec)
    plan, why = dl.plan(s)
    assert plan is not None, why


# --------------------------------------------------------------------------- #
# 3. uv's own version equality
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spec,installed,ok", [
    ("mcp-server-fetch@1.2", "1.2.0", True),
    ("mcp-server-fetch==1.2", "1.2.0", True),
    ("mcp-server-fetch==1.02.0", "1.2.0", True),
    ("mcp-server-fetch@1.2.0", "1.2.0", True),
    ("mcp-server-fetch@1.2", "1.2.1", False),
    ("mcp-server-fetch@2.0", "1.2.0", False),
])
def test_a_pinned_uv_version_is_compared_the_way_uv_compares_it(
    tmp_path, state, spec, installed, ok
):
    prefix = make_uv_env(tmp_path, versions=(installed,))
    s = uv_spec(spec)
    dl.put_record(s, {"kind": "uvx", "prefix": str(prefix), "path_prepend": [],
                      "asked_at": time.time()})
    plan, why = dl.plan(s)
    assert (plan is not None) is ok, why
    if not ok:
        assert why.stale


# --------------------------------------------------------------------------- #
# 4. a slow (healthy) direct start inside the connect bound
# --------------------------------------------------------------------------- #
SLOW = (
    "setTimeout(() => {{ require({server}); }}, {ms});\n"
)


def _slow_cache(tmp_path, ms: int):
    server = json_str(str(FIX / "server.js"))
    return make_npx_cache(tmp_path, "pkg", "pkg",
                          sources={"dist/index.js": SLOW.format(server=server, ms=ms)})


def json_str(text: str) -> str:
    import json

    return json.dumps(text)


@needs_node
def test_a_slow_direct_start_still_loads_inside_the_connect_bound(tmp_path, state, monkeypatch):
    """Direct start answers in ~4 s; the connect bound is 6 s. The launcher
    start (which runs the same slow files plus its own overhead) also fits —
    so the pack loaded before the change. Killing the direct start at the cap
    and starting the launcher after it does NOT fit."""
    monkeypatch.setattr(dl, "DIRECT_INIT_TIMEOUT_S", 2.5)
    monkeypatch.setenv("IRONJARVIS_MCP_CONNECT_TIMEOUT", "6")
    c = _slow_cache(tmp_path, 4000)
    slow_entry = c["pkg"] / "dist" / "index.js"
    cfg = pack_cfg(tmp_path, c, "pkg", server=slow_entry)
    spec = mcp_tools_mod._build_transport(cfg, None).direct
    dl.put_record(spec, npx_record(c))
    tools, client = _load(cfg)
    try:
        assert tools, mcp_tools_mod.load_status("directpack")
        launch = mcp_tools_mod.load_status("directpack")["launch"]
        assert launch["direct"] is True, launch
        assert client.transport.spawn_count == 1
        assert not (tmp_path / "npx.log").exists()
    finally:
        _cleanup(tools)


HANGS = "process.stdin.resume(); setInterval(() => {}, 1000);\n"


@needs_node
def test_a_direct_start_silent_for_the_whole_bound_is_retried_once_through_npx(
    tmp_path, state, monkeypatch
):
    monkeypatch.setattr(dl, "DIRECT_INIT_TIMEOUT_S", 1.0)
    monkeypatch.setenv("IRONJARVIS_MCP_CONNECT_TIMEOUT", "3")
    c = make_npx_cache(tmp_path, "pkg", "pkg", sources={"dist/index.js": HANGS})
    cfg = pack_cfg(tmp_path, c, "pkg")  # the launcher runs the GOOD server
    spec = mcp_tools_mod._build_transport(cfg, None).direct
    dl.put_record(spec, npx_record(c))
    tools, client = _load(cfg)
    try:
        assert sorted(t.name.split("__")[-1] for t in tools) == ["ping_tool", "whoami"]
        launch = mcp_tools_mod.load_status("directpack")["launch"]
        assert launch["direct"] is False and launch["how"] == "npx"
        assert "did not answer within 3 s" in launch["note"]
        assert "through npx instead" in launch["note"]
        assert (tmp_path / "npx.log").read_text().split()[0] == "start"
        plan, why = dl.plan(spec)
        assert plan is None and "failed recently" in why
    finally:
        _cleanup(tools)


@needs_node
def test_a_hang_on_a_respawn_with_no_caller_bound_still_falls_back_at_the_cap(
    tmp_path, state, monkeypatch
):
    """Outside the connect probe (a respawn inside a tool call), nothing else
    bounds the start, so the cap still decides — unchanged."""
    monkeypatch.setattr(dl, "DIRECT_INIT_TIMEOUT_S", 1.5)
    c = make_npx_cache(tmp_path, "pkg", "pkg", sources={"dist/index.js": HANGS})
    cfg = pack_cfg(tmp_path, c, "pkg")
    transport = mcp_tools_mod._build_transport(cfg, None)
    dl.put_record(transport.direct, npx_record(c))
    try:
        res = transport.request("tools/list", {})
        assert [t["name"] for t in res["tools"]] == ["whoami", "ping_tool"]
        assert transport.spawn_count == 2
        assert "did not answer within 1.5 s" in transport.launch_info["note"]
    finally:
        transport.shutdown()


# --------------------------------------------------------------------------- #
# 5. only the daemon asks
# --------------------------------------------------------------------------- #
def test_a_platform_built_without_the_daemon_reads_answers_but_never_asks(
    tmp_path, monkeypatch
):
    from iron_jarvis.platform import build_platform

    monkeypatch.setattr(dl, "ask_runner", lambda *a, **k: pytest.fail("a CLI one-shot asked"))
    try:
        build_platform(tmp_path / "home")
        assert dl._STATE_DIR is not None  # answers are still read
        s = dl.parse("C:/n/npx.cmd", ["-y", "pkg"])
        assert dl.note_started(s) is False
    finally:
        dl.set_state_dir(None)


def test_the_daemon_build_asks():
    src = (Path(__file__).parents[1] / "src" / "iron_jarvis" / "platform.py").read_text(
        encoding="utf-8")
    assert "_mcp_direct_state(config.home, ask=defer_mcp)" in src
    assert sys.platform  # keep the import used on non-Windows collection
