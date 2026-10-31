"""v1.415.0 review, part 2 — the daily refresh never touches a running server's
files, and two writers of the kept answers never tear them.

1. The DAILY refresh of an ``npx -y`` pack (no version / ``@latest``) used to
   run npx again, which UPGRADES ``_npx/<hash>`` IN PLACE while a direct
   server runs from that folder all day; npx itself only ever upgrades at a
   start. The refresh is now a READ-ONLY check — ``npm pack --dry-run --json
   --prefer-online <spec>`` with the npm beside the pack's npx (measured: the
   same engine-aware pick as npx, 1.0.0 for server-everything on node 20 where
   ``npm view`` says 2.1.0; the same project/user config; ``_npx`` unchanged).
   A different version marks the answer ``outdated``: the NEXT start goes
   through npx (which upgrades at start time) and the ask after it keeps the
   new answer. A check that cannot run changes nothing and is retried after
   ``CHECK_RETRY_S``. uv is still asked (measured: a new resolution is a new
   environment; ``--refresh``/``--upgrade`` left the running one unchanged).
2. Two processes writing ``direct-launch.json`` shared one fixed ``.tmp`` name:
   one could truncate the other's half-written file and replace the answers
   with a torn one, or fail its replace. Each write now has its own temp name.
"""

from __future__ import annotations

import hashlib
import json
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
    _load,
    _tree,
    _whoami,
    make_npx_cache,
    make_uv_env,
    needs_node,
    npx_record,
    npx_spec,
    pack_cfg,
    state,  # noqa: F401 — the fixture
    uv_spec,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="direct starts are Windows-only")

DAY_OLD = dl.REFRESH_S + 60


def snapshot(root: Path) -> str:
    """Every file under ``root`` by name, size and mtime: a write shows."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        st = p.stat()
        h.update(f"{p.relative_to(root)}|{st.st_size}|{st.st_mtime_ns}".encode())
    return h.hexdigest()


def pack_json(name: str, version: str) -> str:
    return json.dumps([{"id": f"{name}@{version}", "name": name, "version": version,
                        "files": [{"path": "package.json"}]}], indent=2)


def write_fake_npm(root: Path) -> Path:
    cmd = root / "npm.cmd"
    cmd.write_text(f'@"{sys.executable}" "{FIX / "fake_npm.py"}" %*\n')
    return cmd


class Runner:
    """A runner double that records what it was asked to run."""

    def __init__(self, answer=(0, "")):
        self.calls: list[list[str]] = []
        self.answer = answer

    def __call__(self, argv, *, env, cwd, timeout):
        self.calls.append(list(argv))
        return self.answer(argv) if callable(self.answer) else self.answer


@pytest.fixture
def day_old(tmp_path, state, monkeypatch):  # noqa: F811 — the fixture
    """A direct-ready npx pack whose answer is a day old, an npm beside npx."""
    c = make_npx_cache(tmp_path, "pkg", "pkg", "1.0.0")
    (tmp_path / "npm.cmd").write_text("@echo off\n")
    s = npx_spec(tmp_path, "pkg")
    dl.put_record(s, {**npx_record(c), "asked_at": time.time() - DAY_OLD})
    ask = Runner(lambda argv: pytest.fail(f"npx was run for a daily refresh: {argv}"))
    monkeypatch.setattr(dl, "ask_runner", ask)
    monkeypatch.setattr(dl, "_CHECK_FAILED", {})
    return c, s


# --------------------------------------------------------------------------- #
# 1. the daily refresh of an npx pack is READ-ONLY
# --------------------------------------------------------------------------- #
def test_a_day_old_npx_answer_is_checked_with_npm_pack_dry_run_never_npx(day_old, monkeypatch):
    c, s = day_old
    check = Runner((0, pack_json("pkg", "1.0.0")))
    monkeypatch.setattr(dl, "check_runner", check)
    before = snapshot(c["install"])
    assert dl.plan(s)[0] is not None
    assert dl.note_started(s)
    assert dl.drain(30)
    assert len(check.calls) == 1
    assert check.calls[0] == [str(Path(s.launcher).with_name("npm.cmd")), "pack", "--dry-run",
                              "--json", "--prefer-online", "pkg"]
    rec = dl.record_for(s)
    assert time.time() - rec["asked_at"] < 60 and "outdated" not in rec  # fresh another day
    assert snapshot(c["install"]) == before
    assert dl.plan(s)[0] is not None
    assert not dl.note_started(s)  # checked: nothing more today


def test_a_newer_version_sends_the_next_start_through_npx_and_touches_nothing(
    day_old, monkeypatch
):
    c, s = day_old
    monkeypatch.setattr(dl, "check_runner", Runner((0, pack_json("pkg", "1.1.0"))))
    before = snapshot(c["install"])
    assert dl.note_started(s)
    assert dl.drain(30)
    assert snapshot(c["install"]) == before  # the running server's folder is untouched
    rec = dl.record_for(s)
    assert rec["outdated"] == "1.1.0"
    plan, why = dl.plan(s)
    assert plan is None and why.stale and "1.1.0" in why
    # The next start went through npx (which upgrades at start time); the ask
    # after it records the new answer, without the mark.
    out = "IJASK" + json.dumps({"node": rec["node"], "path": str(c["bindir"]) + os.pathsep +
                                os.environ.get("PATH", ""), "cache": rec["cache"],
                                "gp": rec["gp"], "lp": rec["lp"]})
    asked = Runner((0, out))
    monkeypatch.setattr(dl, "ask_runner", asked)
    assert dl.note_started(s, plan_failed=bool(why.stale))
    assert dl.drain(30)
    assert len(asked.calls) == 1 and "--package=pkg" in asked.calls[0]
    assert "outdated" not in dl.record_for(s)
    assert dl.plan(s)[0] is not None


@pytest.mark.parametrize("answer", [
    (1, ""),                                   # npm failed (offline, refused)
    (-1, ""),                                  # timed out
    (0, "not json"),
    (0, pack_json("other-package", "1.1.0")),  # not the package asked about
    (0, "[]"),
    "no-npm",                                  # no npm beside npx
    "raises",
])
def test_a_check_that_cannot_answer_changes_nothing_and_waits_an_hour(day_old, monkeypatch, answer):
    c, s = day_old
    if answer == "no-npm":
        (Path(s.launcher).with_name("npm.cmd")).unlink()
        check = Runner((0, pack_json("pkg", "1.1.0")))
    elif answer == "raises":
        def boom(argv):
            raise OSError("npm vanished")
        check = Runner(boom)
    else:
        check = Runner(answer)
    monkeypatch.setattr(dl, "check_runner", check)
    rec_before = dl.record_for(s)
    file_before = (dl._records_path()).read_bytes()
    now = [time.time()]
    monkeypatch.setattr(dl, "_clock", lambda: now[0])
    assert dl.note_started(s)
    assert dl.drain(30)
    assert dl.record_for(s) == rec_before
    assert dl._records_path().read_bytes() == file_before
    assert dl.plan(s)[0] is not None  # the start stays direct
    assert not dl.note_started(s)  # not again within the hour
    now[0] += dl.CHECK_RETRY_S + 1
    assert dl.note_started(s)  # tried again later
    assert dl.drain(30)


def test_an_answer_that_changed_during_the_check_is_left_alone(day_old, monkeypatch):
    c, s = day_old
    pkg_json = c["pkg"] / "package.json"

    def upgraded_meanwhile(argv):
        data = json.loads(pkg_json.read_text())
        data["version"] = "1.1.0"  # npx (a launcher start) upgraded it meanwhile
        pkg_json.write_text(json.dumps(data))
        return 0, pack_json("pkg", "1.1.0")

    monkeypatch.setattr(dl, "check_runner", Runner(upgraded_meanwhile))
    before = dl.record_for(s)
    assert dl.note_started(s)
    assert dl.drain(30)
    assert dl.record_for(s) == before


def test_a_pinned_npx_pack_is_never_checked(tmp_path, state, monkeypatch):  # noqa: F811
    c = make_npx_cache(tmp_path, "pkg@1.0.0", "pkg", "1.0.0")
    (tmp_path / "npm.cmd").write_text("@echo off\n")
    s = npx_spec(tmp_path, "pkg@1.0.0")
    dl.put_record(s, {**npx_record(c), "asked_at": 0.0})
    monkeypatch.setattr(dl, "check_runner", Runner(lambda a: pytest.fail("checked")))
    monkeypatch.setattr(dl, "ask_runner", Runner(lambda a: pytest.fail("asked")))
    assert not dl.note_started(s)


def test_a_day_old_uv_answer_is_still_asked(tmp_path, state, monkeypatch):  # noqa: F811
    """Measured: uv never changes an environment a server uses (a new
    resolution is a new ``archive-v0`` folder), so asking uv stays."""
    prefix = make_uv_env(tmp_path)
    s = uv_spec()
    dl.put_record(s, {"kind": "uvx", "prefix": str(prefix), "path_prepend": [],
                      "asked_at": time.time() - DAY_OLD})
    asked = Runner((1, ""))
    monkeypatch.setattr(dl, "ask_runner", asked)
    monkeypatch.setattr(dl, "check_runner", Runner(lambda a: pytest.fail("checked uv")))
    assert dl.note_started(s)
    assert dl.drain(30)
    assert len(asked.calls) == 1 and "--from" in asked.calls[0]


def test_the_real_check_runs_the_npm_beside_npx_with_the_packs_env_and_folder(
    tmp_path, state, monkeypatch  # noqa: F811
):
    """Through the real runner (``core/procs``) and a stand-in ``npm.cmd``."""
    c = make_npx_cache(tmp_path, "pkg", "pkg", "1.0.0")
    write_fake_npm(tmp_path)
    log = tmp_path / "npm.log"
    env = {**os.environ, "FAKE_NPM_LOG": str(log), "FAKE_NPM_MARK": "pack-env",
           "FAKE_NPM_NAME": "pkg", "FAKE_NPM_VERSION": "1.0.0"}
    s = npx_spec(tmp_path, "pkg", cwd=c["lp"], env=env)
    dl.put_record(s, {**npx_record(c), "asked_at": time.time() - DAY_OLD})
    monkeypatch.setattr(dl, "_CHECK_FAILED", {})
    assert dl.check(s) is True
    line = json.loads(log.read_text().splitlines()[0])
    assert line["argv"] == ["pack", "--dry-run", "--json", "--prefer-online", "pkg"]
    assert Path(line["cwd"]) == c["lp"] and line["mark"] == "pack-env"
    env["FAKE_NPM_VERSION"] = "2.0.0"
    assert dl.check(s) is False and dl.record_for(s)["outdated"] == "2.0.0"
    env["FAKE_NPM_FAIL"] = "1"
    dl.put_record(s, {**npx_record(c), "asked_at": time.time() - DAY_OLD})
    assert dl.check(s) is None and "outdated" not in dl.record_for(s)


@needs_node
def test_a_running_direct_server_keeps_its_files_and_answers_through_a_daily_check(
    tmp_path, state, monkeypatch  # noqa: F811
):
    """End to end: a REAL direct server from the fake cache; the day-old check
    says a newer version is out; the server's folder is untouched and it still
    answers; the next start goes through npx, the ask after it makes the one
    after that direct again."""
    import psutil

    c = make_npx_cache(tmp_path, "pkg", "pkg", "1.0.0")
    cfg = pack_cfg(tmp_path, c, "pkg")
    write_fake_npm(tmp_path)
    cfg["env"].update({"FAKE_NPM_NAME": "pkg", "FAKE_NPM_VERSION": "1.1.0",
                       "FAKE_NPM_LOG": str(tmp_path / "npm.log")})
    spec = mcp_tools_mod._build_transport(cfg, None).direct
    dl.put_record(spec, {**npx_record(c), "asked_at": time.time() - DAY_OLD})
    monkeypatch.setattr(dl, "_CHECK_FAILED", {})
    before = snapshot(c["install"])
    tools, client = _load(cfg)
    try:
        assert _tree(client.transport.pid) == ["node.exe"]
        assert dl.drain(60)
        assert dl.record_for(spec)["outdated"] == "1.1.0"
        assert snapshot(c["install"]) == before
        assert not (tmp_path / "npx.log").exists()  # npx never ran
        assert _whoami(client)["argv"] == ["--flag", "x"]
        pid = client.transport.pid
    finally:
        mcp_tools_mod.close_tools_clients(tools)
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == "zombie"
    tools, client = _load(cfg)  # the next start: through npx
    try:
        assert _tree(client.transport.pid)[0] == "cmd.exe"
        assert dl.drain(60)
        assert (tmp_path / "npx.log").read_text().split() == ["start", "ask"]
        assert "outdated" not in dl.record_for(spec)
    finally:
        mcp_tools_mod.close_tools_clients(tools)
    tools, client = _load(cfg)
    try:
        assert _tree(client.transport.pid) == ["node.exe"]
    finally:
        mcp_tools_mod.close_tools_clients(tools)
        mcp_tools_mod.forget_load_record("directpack")
        mcp_tools_mod.forget_live_client("directpack")


# --------------------------------------------------------------------------- #
# 2. two writers never share a temp file
# --------------------------------------------------------------------------- #
def test_every_write_has_its_own_temp_file_and_leaves_none_behind(tmp_path, state, monkeypatch):  # noqa: F811
    s = npx_spec(tmp_path, "pkg")
    seen: list[str] = []
    real = os.replace

    def spy(src, dst):
        seen.append(str(src))
        return real(src, dst)

    monkeypatch.setattr(dl.os, "replace", spy)
    dl.put_record(s, {"kind": "npx", "n": 1})
    dl.put_record(s, {"kind": "npx", "n": 2})
    path = dl._records_path()
    assert len(seen) == 2 and len(set(seen)) == 2
    assert all(Path(p).parent == path.parent and Path(p).name != f"{path.stem}.tmp" for p in seen)
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_a_busy_answers_file_is_retried_and_a_failed_write_leaves_no_temp(
    tmp_path, state, monkeypatch  # noqa: F811
):
    s = npx_spec(tmp_path, "pkg")
    real = os.replace
    busy = [2]

    def flaky(src, dst):
        if busy[0]:
            busy[0] -= 1
            raise PermissionError(13, "in use by another process")
        return real(src, dst)

    monkeypatch.setattr(dl.os, "replace", flaky)
    dl.put_record(s, {"kind": "npx", "n": 1})
    assert json.loads(dl._records_path().read_text())[s.key]["n"] == 1
    busy[0] = 99  # never free: the write is given up, the temp removed
    dl.put_record(s, {"kind": "npx", "n": 2})
    assert json.loads(dl._records_path().read_text())[s.key]["n"] == 1
    assert [p.name for p in dl._records_path().parent.iterdir()] == [dl.RECORDS_NAME]
    assert dl.record_for(s)["n"] == 2  # memory still has it


WRITER = r"""
import sys
from iron_jarvis.mcp import direct_launch as dl
failures = []
dl.log.warning = lambda *a, **k: failures.append(
    type(a[1]).__name__ if len(a) > 1 and isinstance(a[1], BaseException) else "other")
dl.set_state_dir(sys.argv[1], ask=False)
spec = dl.parse(r"C:\n\npx.cmd", ["-y", "pkg-" + sys.argv[2]])
for i in range(int(sys.argv[3])):
    dl.put_record(spec, {"kind": "npx", "i": i, "pad": "x" * 4000})
import json
print(json.dumps(failures))
"""


def test_two_processes_writing_the_answers_never_tear_or_lose_a_write(tmp_path):
    home = tmp_path / "home"
    (home / "mcp").mkdir(parents=True)
    script = tmp_path / "writer.py"
    script.write_text(WRITER)
    procs = [subprocess.Popen([sys.executable, str(script), str(home), str(n), "150"],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
             for n in (1, 2)]
    torn = 0
    path = home / "mcp" / dl.RECORDS_NAME
    while any(p.poll() is None for p in procs):
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            pass
        except PermissionError:
            pass  # Windows: the file is being replaced this instant
        except (json.JSONDecodeError, UnicodeDecodeError):
            torn += 1
        time.sleep(0.002)  # a reader, not a lock-holder
    outs = [p.communicate(timeout=120) for p in procs]
    assert all(p.returncode == 0 for p in procs), outs
    failures = [f for o in outs for f in json.loads(o[0].strip().splitlines()[-1])]
    # A shared temp name made one writer replace the file with the OTHER's
    # half-written temp, or find its own temp already moved away
    # (FileNotFoundError: 7-12 lost writes per process here, measured).
    # Windows refusing a replace while the file is busy for longer than the
    # bounded retries (PermissionError) can still happen on a loaded PC: that
    # loses one write, which only means a launcher start and a new ask.
    assert all(f == "PermissionError" for f in failures), failures
    assert len(failures) <= 3, failures
    assert torn == 0
    final = json.loads(path.read_text(encoding="utf-8"))
    assert final  # the last writer's answers, whole
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_a_lost_answers_file_only_means_the_launcher(tmp_path, state):  # noqa: F811
    c = make_npx_cache(tmp_path, "pkg", "pkg")
    s = npx_spec(tmp_path, "pkg")
    dl.put_record(s, npx_record(c))
    dl._records_path().write_text("{ torn")
    dl.set_state_dir(state)  # a restart reading what another process left
    plan, why = dl.plan(s)
    assert plan is None and "not been asked" in why
