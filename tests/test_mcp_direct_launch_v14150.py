"""v1.415.0 — an ``npx -y`` / ``uvx`` pack's server is started DIRECTLY.

Measured on the user's PC: an ``npx -y <pkg>`` pack ran as cmd.exe -> cmd.exe
-> node npx-cli.js (~81 MB, idle forever) -> node <server>; a ``uvx`` pack as
uvx.exe -> uv.exe (~158 MB, idle forever) -> trampoline -> python. The
launcher is now ASKED (in the background, after a start) which files it runs,
and later starts run them directly. These pins drive the resolver over fake
npx and uv caches (found / not found / ambiguous / version mismatch / non-JS
bin), a REAL direct start of a tiny Node MCP server placed in a fake npx cache
(no cmd.exe, no launcher in the tree, the tools listed, the same cwd/args/env),
the whole ask-then-direct flow through a stand-in npx, and the fallback to the
launcher when a direct start does not answer.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

from iron_jarvis.mcp import direct_launch as dl
from iron_jarvis.mcp import tools as mcp_tools_mod
from iron_jarvis.mcp.client import MCPClient

pytestmark = pytest.mark.skipif(os.name != "nt", reason="direct starts are Windows-only")

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="no node on PATH")
FIX = Path(__file__).parent / "fixtures" / "direct_launch_v14150"

SHIM = (
    "@ECHO off\nGOTO start\n:find_dp0\nSET dp0=%~dp0\nEXIT /b\n:start\nSETLOCAL\n"
    "CALL :find_dp0\n\nIF EXIST \"%dp0%\\node.exe\" (\n  SET \"_prog=%dp0%\\node.exe\"\n"
    ") ELSE (\n  SET \"_prog=node\"\n  SET PATHEXT=%PATHEXT:;.JS;=;%\n)\n\n"
    "endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & \"%_prog%\" {extra} "
    "\"%dp0%\\..\\{rel}\" %*\n"
)


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
def state(tmp_path, monkeypatch):
    """A state home for kept answers; the switch on; nothing left behind."""
    monkeypatch.delenv(dl.ENV_SWITCH, raising=False)
    home = tmp_path / "home"
    dl.set_state_dir(home)
    yield home
    dl.drain(60)
    dl.set_state_dir(None)


def make_npx_cache(root: Path, spec: str, name: str, version: str = "1.0.0", *,
                   bins: dict[str, str] | None = None, sources: dict[str, str] | None = None,
                   extra: str = "", shim_rel: str | None = None) -> dict:
    """npx's own layout for ONE package: ``<cache>/_npx/<hash>/package.json``
    + ``node_modules/<name>/`` + ``node_modules/.bin/<bin>.cmd``."""
    cache = root / "npm-cache"
    install = cache / "_npx" / dl.npx_hash(spec)
    pkg = install / "node_modules" / Path(*name.split("/"))
    bindir = install / "node_modules" / ".bin"
    pkg.mkdir(parents=True)
    bindir.mkdir(parents=True)
    (install / "package.json").write_text(json.dumps({"dependencies": {name: f"^{version}"}}))
    bins = bins if bins is not None else {name.split("/")[-1]: "dist/index.js"}
    (pkg / "package.json").write_text(json.dumps({"name": name, "version": version, "bin": bins}))
    server = (FIX / "server.js").read_text(encoding="utf-8")
    for rel in set(bins.values()):
        f = pkg / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text((sources or {}).get(rel, server), encoding="utf-8")
    for bname, rel in bins.items():
        target = f"{name.replace('/', chr(92))}\\{(shim_rel or rel).replace('/', chr(92))}"
        (bindir / f"{bname}.cmd").write_text(SHIM.format(extra=extra, rel=target))
    gp = root / "global"
    lp = root / "work"
    gp.mkdir(exist_ok=True)
    lp.mkdir(exist_ok=True)
    return {"cache": cache, "install": install, "pkg": pkg, "bindir": bindir, "gp": gp, "lp": lp}


def npx_spec(root: Path, spec: str, args: list[str] | None = None, *, cwd: Path | None = None,
             env: dict | None = None, flags: list[str] | None = None,
             launcher_name: str = "npx.cmd") -> dl.LaunchSpec:
    launcher = root / launcher_name
    s = dl.parse(str(launcher), [*(flags or ["-y"]), spec, *(args or [])],
                 cwd=str(cwd) if cwd else None, env=env)
    assert s is not None
    return s


def npx_record(c: dict, node: str | None = None) -> dict:
    return {
        "kind": "npx", "node": node or NODE or sys.executable, "cache": str(c["cache"]),
        "gp": str(c["gp"]), "lp": str(c["lp"]), "path_prepend": [str(c["bindir"])],
        "asked_at": time.time(),
    }


# --------------------------------------------------------------------------- #
# what is parsed
# --------------------------------------------------------------------------- #
def test_npx_cache_folder_name_is_npms_own():
    """Measured with node's crypto against the user's real npx cache folders."""
    assert dl.npx_hash("@modelcontextprotocol/server-everything") == "5b2dd62b9d0bddd4"
    assert dl.npx_hash("@modelcontextprotocol/server-sequential-thinking") == "de2bd410102f5eda"
    assert dl.npx_hash("@brave/brave-search-mcp-server") == "c34b0aca2a0f246a"


@pytest.mark.parametrize("command,args,kind,spec,rest", [
    ("C:/n/npx.CMD", ["-y", "@a/b"], "npx", "@a/b", ()),
    ("C:/n/npx.cmd", ["--yes", "pkg@1.2.3", "--x", "1"], "npx", "pkg@1.2.3", ("--x", "1")),
    ("C:/n/npx", ["-y", "-y", "pkg@latest"], "npx", "pkg@latest", ()),
    ("C:/u/uvx.exe", ["mcp-server-fetch"], "uvx", "mcp-server-fetch", ()),
    ("C:/u/uvx.exe", ["tool==1.0", "--a"], "uvx", "tool==1.0", ("--a",)),
    ("C:/u/uv.exe", ["tool", "run", "pkg[x]@2.0"], "uvx", "pkg[x]@2.0", ()),
])
def test_launcher_lines_this_module_understands(command, args, kind, spec, rest):
    s = dl.parse(command, args)
    assert s is not None and (s.kind, s.spec, s.server_args) == (kind, spec, rest)


@pytest.mark.parametrize("command,args", [
    ("C:/n/npx.cmd", ["pkg"]),                       # no -y: npx may prompt
    ("C:/n/npx.cmd", ["-y", "-p", "pkg", "cmd"]),     # any other option
    ("C:/n/npx.cmd", ["-y", "--registry=x", "pkg"]),
    ("C:/n/npx.cmd", ["-y", "github:a/b"]),           # not a registry spec
    ("C:/n/npx.cmd", ["-y", "./local"]),
    ("C:/n/npx.cmd", ["-y", "pkg@>=1.0.0"]),          # a range form not handled
    ("C:/n/npx.cmd", ["-y", "pkg@next"]),             # a tag other than latest
    ("C:/u/uvx.exe", ["--from", "x", "y"]),           # any uvx option
    ("C:/u/uvx.exe", ["git+https://x/y"]),
    ("C:/u/uv.exe", ["run", "x"]),
    ("C:/p/python.exe", ["-y", "pkg"]),
    ("C:/n/npx.ps1", ["-y", "pkg"]),
])
def test_anything_else_starts_through_the_launcher(command, args):
    assert dl.parse(command, args) is None


# --------------------------------------------------------------------------- #
# the npx resolver over a fake cache
# --------------------------------------------------------------------------- #
def test_a_cached_package_with_a_kept_answer_starts_directly(tmp_path, state):
    c = make_npx_cache(tmp_path, "@s/srv", "@s/srv")
    s = npx_spec(tmp_path, "@s/srv", ["--flag"])
    assert dl.plan(s) == (None, "npx has not been asked yet")
    dl.put_record(s, npx_record(c))
    plan, why = dl.plan(s)
    assert why == "" and plan is not None
    target = c["pkg"] / "dist" / "index.js"
    assert plan.argv == (NODE or sys.executable, str(target), "--flag")
    env = plan.env_for({"PATH": "C:\\x"})
    assert env["PATH"] == f"{c['bindir']};C:\\x"


def test_no_copy_in_the_cache_is_not_a_guess(tmp_path, state):
    c = make_npx_cache(tmp_path, "pkg", "pkg")
    s = npx_spec(tmp_path, "pkg")
    dl.put_record(s, npx_record(c))
    shutil.rmtree(c["install"])
    plan, why = dl.plan(s)
    assert plan is None and why.stale and "no copy" in why


@pytest.mark.parametrize("where", ["global", "global-cmd", "local", "local-bin", "local-self",
                                   "local-nested", "local-unlisted"])
def test_a_copy_npx_would_prefer_keeps_the_launcher(tmp_path, state, where):
    """npx runs a globally installed copy, or one in the working folder's own
    project, before its cache: either one means the launcher decides."""
    c = make_npx_cache(tmp_path, "pkg", "pkg")
    s = npx_spec(tmp_path, "pkg")
    dl.put_record(s, npx_record(c))
    assert dl.plan(s)[0] is not None
    nm = c["lp"] / "node_modules"
    if where == "global":
        (c["gp"] / "node_modules" / "pkg").mkdir(parents=True)
    elif where == "global-cmd":
        (c["gp"] / "pkg.cmd").write_text("x")
    elif where == "local":
        (nm / "pkg").mkdir(parents=True)
    elif where == "local-bin":
        (nm / ".bin").mkdir(parents=True)
        (nm / ".bin" / "pkg.cmd").write_text("x")
    elif where == "local-self":
        (c["lp"] / "package.json").write_text(json.dumps({"name": "pkg"}))
    elif where == "local-nested":
        nm.mkdir()
        (nm / ".package-lock.json").write_text(json.dumps(
            {"packages": {"node_modules/other/node_modules/pkg": {"version": "1.0.0"}}}))
    elif where == "local-unlisted":
        nm.mkdir()  # packages and no list of them: cannot tell
    plan, why = dl.plan(s)
    assert plan is None and not why.stale, why


def test_a_project_folder_that_does_not_hold_the_package_is_fine(tmp_path, state):
    c = make_npx_cache(tmp_path, "pkg", "pkg")
    s = npx_spec(tmp_path, "pkg")
    dl.put_record(s, npx_record(c))
    (c["lp"] / "node_modules").mkdir()
    (c["lp"] / "node_modules" / ".package-lock.json").write_text(json.dumps(
        {"packages": {"node_modules/other": {"version": "1.0.0"}}}))
    (c["lp"] / "package.json").write_text(json.dumps({"name": "app"}))
    assert dl.plan(s)[0] is not None


@pytest.mark.parametrize("spec,installed,ok", [
    ("pkg", "9.9.9", True),
    ("pkg@latest", "9.9.9", True),
    ("pkg@1.2.3", "1.2.3", True),
    ("pkg@2.0.0", "1.0.0", False),
    ("pkg@^1.0.0", "1.4.2", True),
    ("pkg@^1.0.0", "2.0.0", False),
    ("pkg@~1.2.0", "1.2.9", True),
    ("pkg@~1.2.0", "1.3.0", False),
    ("pkg@^0.6.2", "0.7.0", False),
    ("pkg@^1.0.0", "1.1.0-beta.1", False),
])
def test_a_version_the_cached_copy_does_not_satisfy_keeps_the_launcher(
    tmp_path, state, spec, installed, ok
):
    c = make_npx_cache(tmp_path, spec, "pkg", installed)
    # review v1.415.0: npm's npx.cmd runs through cmd.exe, which strips the
    # `^` before npx sees it, so a `^` range is only ever direct for an npx
    # with no cmd layer (volta's npx.exe); the ranges are pinned there.
    s = npx_spec(tmp_path, spec, launcher_name="npx.exe" if "^" in spec else "npx.cmd")
    dl.put_record(s, npx_record(c))
    plan, why = dl.plan(s)
    assert (plan is not None) is ok, why


def test_npm_picks_the_bin_the_same_way(tmp_path):
    assert dl.bin_name({"name": "@s/a", "bin": "cli.js"}) == ("a", "cli.js")
    assert dl.bin_name({"name": "a", "bin": {"x": "c.js", "y": "c.js"}}) == ("x", "c.js")
    assert dl.bin_name({"name": "@s/a", "bin": {"x": "x.js", "a": "a.js"}}) == ("a", "a.js")
    assert dl.bin_name({"name": "a", "bin": {"x": "x.js", "y": "y.js"}}) is None
    assert dl.bin_name({"name": "a"}) is None


@pytest.mark.parametrize("case", ["two-bins", "not-js", "shim-args", "shim-elsewhere",
                                  "own-node", "no-shim"])
def test_a_bin_that_is_not_plain_node_keeps_the_launcher(tmp_path, state, case):
    kw: dict = {}
    if case == "two-bins":
        kw["bins"] = {"x": "x.js", "y": "y.js"}
    elif case == "not-js":
        kw["bins"] = {"pkg": "bin/cli.sh"}
    elif case == "shim-args":
        kw["extra"] = "--experimental-x"
    elif case == "shim-elsewhere":
        kw["shim_rel"] = "dist/other.js"
    c = make_npx_cache(tmp_path, "pkg", "pkg", **kw)
    if case == "own-node":
        (c["bindir"] / "node.exe").write_text("x")
    if case == "no-shim":
        (c["bindir"] / "pkg.cmd").unlink()
    s = npx_spec(tmp_path, "pkg")
    dl.put_record(s, npx_record(c))
    plan, why = dl.plan(s)
    assert plan is None, case
    assert why


def test_a_failed_direct_start_holds_the_launcher_for_a_day(tmp_path, state):
    c = make_npx_cache(tmp_path, "pkg", "pkg")
    s = npx_spec(tmp_path, "pkg")
    dl.put_record(s, npx_record(c))
    dl.note_direct_failed(s, "boom", now=1_000.0)
    assert dl.plan(s, now=1_000.0 + 3600)[0] is None
    assert dl.plan(s, now=1_000.0 + dl.FAILED_HOLD_S + 1)[0] is not None


def test_the_switch_turns_it_off(tmp_path, state, monkeypatch):
    c = make_npx_cache(tmp_path, "pkg", "pkg")
    s = npx_spec(tmp_path, "pkg")
    dl.put_record(s, npx_record(c))
    monkeypatch.setenv(dl.ENV_SWITCH, "off")
    assert dl.plan(s)[0] is None


def test_answers_are_kept_on_disk_and_read_back(tmp_path, state):
    c = make_npx_cache(tmp_path, "pkg", "pkg")
    s = npx_spec(tmp_path, "pkg")
    dl.put_record(s, npx_record(c))
    dl.set_state_dir(state)  # a restart: memory forgotten, the file stays
    assert dl.plan(s)[0] is not None
    raw = json.loads((state / "mcp" / dl.RECORDS_NAME).read_text())
    assert raw[s.key]["cache"] == str(c["cache"])


def test_the_key_never_carries_the_environment(tmp_path):
    s = dl.parse("C:/n/npx.cmd", ["-y", "pkg"], env={"PATH": "x", "NPM_CONFIG_TOKEN": "s3cr3t"})
    s2 = dl.parse("C:/n/npx.cmd", ["-y", "pkg"], env={"PATH": "x", "NPM_CONFIG_TOKEN": "other"})
    assert "s3cr3t" not in s.key and s.key != s2.key


# --------------------------------------------------------------------------- #
# the uv resolver over a fake cache
# --------------------------------------------------------------------------- #
def make_uv_env(root: Path, name: str = "mcp-server-fetch", versions=("2026.10.28",)) -> Path:
    prefix = root / "uvcache" / "archive-v0" / "Abc"
    (prefix / "Scripts").mkdir(parents=True)
    (prefix / "pyvenv.cfg").write_text("home = C:\\py\n")
    (prefix / "Scripts" / f"{name}.exe").write_bytes(b"MZ")
    for v in versions:
        (prefix / "Lib" / "site-packages" / f"{name.replace('-', '_')}-{v}.dist-info").mkdir(
            parents=True)
    return prefix


def uv_spec(spec: str = "mcp-server-fetch", env: dict | None = None) -> dl.LaunchSpec:
    s = dl.parse("C:/u/uvx.exe", [spec, "--arg"], env=env or {"PATH": "C:\\x", "APPDATA": "C:\\nope"})
    assert s is not None
    return s


def test_a_uv_environment_with_a_kept_answer_starts_directly(tmp_path, state):
    prefix = make_uv_env(tmp_path)
    s = uv_spec()
    dl.put_record(s, {"kind": "uvx", "prefix": str(prefix), "uv": "C:\\u\\uv.exe",
                      "path_prepend": [str(prefix / "Scripts")], "asked_at": time.time()})
    plan, why = dl.plan(s)
    assert why == "" and plan.argv == (str(prefix / "Scripts" / "mcp-server-fetch.exe"), "--arg")
    env = plan.env_for({"PATH": "C:\\x"})
    assert env["PATH"].startswith(str(prefix / "Scripts")) and env["UV"] == "C:\\u\\uv.exe"


@pytest.mark.parametrize("case", ["gone", "no-exe", "two-versions", "pinned-other", "tool"])
def test_a_uv_environment_that_is_not_certain_keeps_the_launcher(tmp_path, state, case):
    prefix = make_uv_env(tmp_path, versions=("1.0", "2.0") if case == "two-versions" else ("1.0",))
    env = {"PATH": "C:\\x", "APPDATA": str(tmp_path / "appdata")}
    s = uv_spec("mcp-server-fetch==3.0" if case == "pinned-other" else "mcp-server-fetch", env)
    dl.put_record(s, {"kind": "uvx", "prefix": str(prefix), "path_prepend": [],
                      "asked_at": time.time()})
    if case == "gone":
        shutil.rmtree(prefix)
    elif case == "no-exe":
        (prefix / "Scripts" / "mcp-server-fetch.exe").unlink()
    elif case == "tool":
        (tmp_path / "appdata" / "uv" / "tools" / "mcp-server-fetch").mkdir(parents=True)
    plan, why = dl.plan(s)
    assert plan is None and why, case


# --------------------------------------------------------------------------- #
# reading the launcher's answer
# --------------------------------------------------------------------------- #
def test_the_npx_answer_is_read_off_its_one_line(tmp_path):
    c = make_npx_cache(tmp_path, "pkg", "pkg")
    s = npx_spec(tmp_path, "pkg", env={"PATH": "C:\\a;C:\\b"})
    line = "IJASK" + json.dumps({"node": "C:\\n\\node.exe",
                                 "path": f"{c['bindir']};C:\\w\\node_modules\\.bin;C:\\a;C:\\b",
                                 "cache": str(c["cache"]), "gp": "C:\\g", "lp": "C:\\w"})
    rec = dl.answer_from(s, "npm warn x\n" + line + "\n", now=5.0)
    assert rec["path_prepend"] == [str(c["bindir"]), "C:\\w\\node_modules\\.bin"]
    assert (rec["node"], rec["lp"], rec["asked_at"]) == ("C:\\n\\node.exe", "C:\\w", 5.0)
    # npx ran a copy from elsewhere (its install folder is not first): no answer
    other = line.replace(json.dumps(str(c["bindir"]))[1:-1], "C:\\\\elsewhere")
    assert dl.answer_from(s, other) is None
    # a PATH that does not end with ours: what was prepended is not known
    assert dl.answer_from(s, line.replace("C:\\\\a;C:\\\\b", "C:\\\\a")) is None
    assert dl.answer_from(s, "no marker here") is None
    assert dl.answer_from(s, "IJASK{not json") is None


def test_the_uv_answer_is_read_off_its_one_line():
    s = uv_spec(env={"PATH": "C:\\a"})
    out = "IJASK" + json.dumps({"prefix": "C:\\env", "path": "C:\\env\\Scripts;C:\\a",
                                "uv": "C:\\uv.exe"})
    rec = dl.answer_from(s, out)
    assert rec["prefix"] == "C:\\env" and rec["path_prepend"] == ["C:\\env\\Scripts"]


def test_only_a_fresh_spec_is_asked_again_and_only_once_a_day(tmp_path, state, monkeypatch):
    calls: list[list[str]] = []

    def runner(argv, *, env, cwd, timeout):
        calls.append(list(argv))
        return 1, ""

    monkeypatch.setattr(dl, "ask_runner", runner)
    c = make_npx_cache(tmp_path, "pkg", "pkg")
    fresh = npx_spec(tmp_path, "pkg")
    pinned = npx_spec(tmp_path, "pkg@1.0.0")
    dl.put_record(fresh, {**npx_record(c), "asked_at": time.time()})
    dl.put_record(pinned, {**npx_record(c), "asked_at": 0.0})
    assert not dl.note_started(fresh) and not dl.note_started(pinned)
    dl.put_record(fresh, {**npx_record(c), "asked_at": time.time() - dl.REFRESH_S - 1})
    assert dl.note_started(fresh)
    assert not dl.note_started(fresh)  # already queued: one ask at a time
    assert dl.drain(30)
    assert len(calls) == 1 and "--package=pkg" in calls[0]


def test_no_state_home_means_no_ask(tmp_path, monkeypatch):
    dl.set_state_dir(None)
    monkeypatch.setattr(dl, "ask_runner", lambda *a, **k: pytest.fail("asked"))
    assert not dl.note_started(npx_spec(tmp_path, "pkg"))


# --------------------------------------------------------------------------- #
# REAL starts: a tiny Node MCP server in a fake npx cache
# --------------------------------------------------------------------------- #
def write_fake_npx(root: Path) -> Path:
    cmd = root / "npx.cmd"
    cmd.write_text(f'@"{sys.executable}" "{FIX / "fake_npx.py"}" %*\n')
    return cmd


def pack_cfg(root: Path, c: dict, spec: str, *, args=("--flag", "x"), server=None) -> dict:
    write_fake_npx(root)
    return {
        "name": "directpack",
        "command": str(root / "npx.cmd"),
        "args": ["-y", spec, *args],
        "cwd": str(c["lp"]),
        "env": {
            "IJ_DIRECT_MARKER": "same-env",
            "FAKE_NPX_NODE": NODE or "",
            "FAKE_NPX_SERVER": str(server or (FIX / "server.js")),
            "FAKE_NPX_CACHE": str(c["cache"]),
            "FAKE_NPX_GP": str(c["gp"]),
            "FAKE_NPX_LP": str(c["lp"]),
            "FAKE_NPX_BIN": str(c["bindir"]),
            "FAKE_NPX_LOG": str(root / "npx.log"),
        },
    }


def _tree(pid: int) -> list[str]:
    import psutil

    p = psutil.Process(pid)
    return [p.name().lower()] + [ch.name().lower() for ch in p.children(recursive=True)]


def _whoami(client: MCPClient) -> dict:
    import asyncio

    res = asyncio.run(client.call_tool("whoami", {}))
    return json.loads(res["content"][0]["text"])


def _load(cfg: dict):
    tools = mcp_tools_mod.mcp_tools([cfg], record=True)
    client = mcp_tools_mod.live_client(cfg["name"])
    return tools, client


@needs_node
def test_a_real_direct_start_has_no_launcher_in_its_tree(tmp_path, state):
    c = make_npx_cache(tmp_path, "@s/srv", "@s/srv")
    cfg = pack_cfg(tmp_path, c, "@s/srv")
    spec = mcp_tools_mod._build_transport(cfg, None).direct
    dl.put_record(spec, npx_record(c))
    tools, client = _load(cfg)
    try:
        assert sorted(t.name.split("__")[-1] for t in tools) == ["ping_tool", "whoami"]
        transport = client.transport
        tree = _tree(transport.pid)
        assert tree == ["node.exe"], tree  # no cmd.exe, no npx node, no fake npx
        import psutil

        assert psutil.Process(transport.pid).ppid() == os.getpid()
        rec = mcp_tools_mod.load_status("directpack")
        assert rec["launch"]["direct"] is True and "index.js" in rec["launch"]["how"]
        info = _whoami(client)
        assert info["argv"] == ["--flag", "x"]
        assert Path(info["cwd"]) == c["lp"]
        assert info["marker"] == "same-env"
        assert Path(info["path0"]) == c["bindir"]  # npx's own .bin in front, as npx does
        assert not (tmp_path / "npx.log").exists()  # the launcher never ran
    finally:
        pid = client.transport.pid
        mcp_tools_mod.close_tools_clients(tools)
        mcp_tools_mod.forget_load_record("directpack")
        mcp_tools_mod.forget_live_client("directpack")
    import psutil

    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == "zombie"


@needs_node
def test_first_start_goes_through_npx_then_it_is_asked_and_the_next_start_is_direct(
    tmp_path, state
):
    c = make_npx_cache(tmp_path, "pkg", "pkg")
    cfg = pack_cfg(tmp_path, c, "pkg")
    tools, client = _load(cfg)
    try:
        tree = _tree(client.transport.pid)
        assert tree[0] == "cmd.exe" and "python.exe" in tree and "node.exe" in tree, tree
        rec = mcp_tools_mod.load_status("directpack")
        assert rec["launch"] == {
            "direct": False, "how": "npx", "note": "Started through npx: npx has not been asked yet.",
        }
        assert dl.drain(60)
        assert (tmp_path / "npx.log").read_text().split() == ["start", "ask"]
    finally:
        mcp_tools_mod.close_tools_clients(tools)
    tools, client = _load(cfg)
    try:
        assert _tree(client.transport.pid) == ["node.exe"]
        assert mcp_tools_mod.load_status("directpack")["launch"]["direct"] is True
        assert _whoami(client)["argv"] == ["--flag", "x"]
        assert dl.drain(30)
        # fresh answer: no second ask
        assert (tmp_path / "npx.log").read_text().split() == ["start", "ask"]
    finally:
        mcp_tools_mod.close_tools_clients(tools)
        mcp_tools_mod.forget_load_record("directpack")
        mcp_tools_mod.forget_live_client("directpack")


EXITS = "process.stderr.write('direct copy is broken\\n'); process.exit(3);\n"
HANGS = "process.stdin.resume(); setInterval(() => {}, 1000);\n"


@needs_node
@pytest.mark.parametrize("broken", ["exits", "hangs"])
def test_a_direct_start_that_does_not_answer_falls_back_to_npx_once(
    tmp_path, state, monkeypatch, broken
):
    monkeypatch.setattr(dl, "DIRECT_INIT_TIMEOUT_S", 2.0)
    # review v1.415.0: at boot the connect probe bounds the start, so a direct
    # start that hangs is given that whole bound (not the 2 s cap) and the load
    # is then tried ONCE more through npx on a fresh transport.
    monkeypatch.setenv("IRONJARVIS_MCP_CONNECT_TIMEOUT", "4")
    c = make_npx_cache(tmp_path, "pkg", "pkg",
                       sources={"dist/index.js": EXITS if broken == "exits" else HANGS})
    cfg = pack_cfg(tmp_path, c, "pkg")  # the launcher runs the GOOD server
    spec = mcp_tools_mod._build_transport(cfg, None).direct
    dl.put_record(spec, npx_record(c))
    t0 = time.monotonic()
    tools, client = _load(cfg)
    try:
        assert time.monotonic() - t0 < 14  # inside the 15 s connect bound
        assert sorted(t.name.split("__")[-1] for t in tools) == ["ping_tool", "whoami"]
        assert client.transport.spawn_count == (2 if broken == "exits" else 1)
        launch = mcp_tools_mod.load_status("directpack")["launch"]
        assert launch["direct"] is False and launch["how"] == "npx"
        assert "direct start" in launch["note"] and "through npx instead" in launch["note"]
        if broken == "exits":
            assert "exit code 3" in launch["note"] and "direct copy is broken" in launch["note"]
        else:
            assert "did not answer within 4 s" in launch["note"]
        assert (tmp_path / "npx.log").read_text().split()[0] == "start"
        plan, why = dl.plan(spec)
        assert plan is None and "failed recently" in why
    finally:
        mcp_tools_mod.close_tools_clients(tools)
        mcp_tools_mod.forget_load_record("directpack")
        mcp_tools_mod.forget_live_client("directpack")


@needs_node
def test_a_start_shut_down_during_the_direct_handshake_starts_nothing_else(
    tmp_path, state, monkeypatch
):
    import psutil

    monkeypatch.setattr(dl, "DIRECT_INIT_TIMEOUT_S", 60.0)
    c = make_npx_cache(tmp_path, "pkg", "pkg", sources={"dist/index.js": HANGS})
    cfg = pack_cfg(tmp_path, c, "pkg")
    transport = mcp_tools_mod._build_transport(cfg, None)
    dl.put_record(transport.direct, npx_record(c))
    box: dict = {}

    def call():
        try:
            transport.request("tools/list", {})
        except Exception as exc:  # noqa: BLE001
            box["err"] = exc

    th = threading.Thread(target=call)
    th.start()
    deadline = time.monotonic() + 20
    while transport.pid is None and time.monotonic() < deadline:
        time.sleep(0.05)
    pid = transport.pid
    assert pid is not None
    time.sleep(0.5)
    transport.shutdown()
    th.join(20)
    assert not th.is_alive() and "err" in box
    assert transport.spawn_count == 1  # the launcher was never started
    assert not (tmp_path / "npx.log").exists()
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == "zombie"


def test_the_connect_timeout_shuts_the_transport_down(monkeypatch):
    seen: list[str] = []

    class Slow:
        def request(self, *_a, **_k):
            time.sleep(5)

        def shutdown(self):
            seen.append("shutdown")

        def close(self):
            seen.append("close")

    cfg = {"name": "slow", "transport_obj": Slow()}
    with pytest.raises(mcp_tools_mod.PackLoadFailed):
        mcp_tools_mod._connect_with_timeout(cfg, "slow", None, 0.3)
    assert seen == ["shutdown"]


def test_a_pack_that_is_not_npx_or_uvx_has_no_launch_key(tmp_path, state):
    server = Path(__file__).parent / "fixtures" / "stdio_mcp_server_v1291.py"
    cfg = {"name": "plainpack", "command": sys.executable, "args": [str(server)]}
    tools = mcp_tools_mod.mcp_tools([cfg], record=True)
    try:
        assert tools
        assert "launch" not in mcp_tools_mod.load_status("plainpack")
    finally:
        mcp_tools_mod.close_tools_clients(tools)
        mcp_tools_mod.forget_load_record("plainpack")
        mcp_tools_mod.forget_live_client("plainpack")


def test_the_platform_keeps_answers_in_its_home():
    src = (Path(__file__).parents[1] / "src" / "iron_jarvis" / "platform.py").read_text(
        encoding="utf-8")
    # review v1.415.0: only the daemon (defer_mcp) asks; every build reads.
    assert "_mcp_direct_state(config.home, ask=defer_mcp)" in src
