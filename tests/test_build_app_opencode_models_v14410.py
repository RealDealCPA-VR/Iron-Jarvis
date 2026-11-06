"""v1.441.0: OpenCode's own model list, read in the background BEFORE any
OpenCode session ran (``hosted.opencode_catalog``).

MEASURED (opencode 1.18.18, a scratch home, a stand-in provider): ``opencode
models --verbose`` lists every model as ``<provider>/<model>`` + its JSON
(name, ``variants`` = the effort levels) in about two seconds, starts no
session, and with ``OPENCODE_DISABLE_MODELS_FETCH=1`` reads OpenCode's own
cache instead of fetching models.dev. The recorded answer is
``fixtures/build_app_v14410/opencode_models_verbose.txt``; the OpenCode here is
``fixtures/fake_opencode_models_v14410.py`` replaying it — never the real one.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import opencode as O
from iron_jarvis.hosted import opencode_catalog as OCC
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import client, until

FIX = Path(__file__).parent / "fixtures"
VERBOSE = FIX / "build_app_v14410" / "opencode_models_verbose.txt"
FAKE = FIX / "fake_opencode_models_v14410.py"


@pytest.fixture()
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(O, "LAST_MODELS", [])
    monkeypatch.setattr(O, "LAST_DEFAULT", [])
    monkeypatch.setattr(OCC, "_FAILED_AT", {})
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    log = tmp_path / "fake-oc.log"
    monkeypatch.setattr(S, "binary_for", lambda harness: [sys.executable, str(FAKE)])
    monkeypatch.setenv("IJ_FAKE_OC_MODELS", str(VERBOSE))
    monkeypatch.setenv("IJ_FAKE_LOG", str(log))
    monkeypatch.setenv("OPENCODE_ENABLE_QUESTION_TOOL", "1")
    return log


def _runs(log: Path) -> list[dict]:
    if not log.exists():
        return []
    return [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines() if x.strip()]


def _opencode(rows: list[dict]) -> dict:
    return {r["id"]: r for r in rows}["opencode"]


def test_the_recorded_answer_reads_as_rows_with_each_models_own_levels():
    rows = OCC.parse_verbose(VERBOSE.read_text(encoding="utf-8"))
    assert [r["id"] for r in rows] == [
        "opencode/big-pickle", "opencode/deepseek-v4-flash-free", "opencode/hy3-free", "opencode/laguna-s-2.1-free",
        "opencode/ling-3.0-tiny-free", "opencode/mimo-v2.5-free", "opencode/nemotron-3-ultra-free",
        "opencode/nemotron-3.5-lightning-free", "standin/probe-m", "standin/probe-n"]
    by = {r["id"]: r for r in rows}
    assert by["opencode/deepseek-v4-flash-free"] == {
        "id": "opencode/deepseek-v4-flash-free", "label": "DeepSeek V4 Flash Free", "efforts": ["low", "high", "max"]}
    assert by["standin/probe-m"]["efforts"] == [] and by["standin/probe-m"]["label"] == "Probe M"


def test_a_broken_block_is_left_out_and_nothing_else_is_lost():
    text = "a/one\n{\n  \"name\": \"One\"\n}\nb/two\n{\n  broken\n}\nc/three\n{\n  \"name\": \"Three\"\n}\n"
    assert [r["id"] for r in OCC.parse_verbose(text)] == ["a/one", "c/three"]
    assert OCC.parse_verbose("") == [] and OCC.parse_verbose("Error: no config") == []


async def test_a_fresh_install_lists_opencodes_models_from_one_background_read(tmp_path, monkeypatch, fresh):
    monkeypatch.setattr(OCC, "AUTO_PROBE", True)
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        first = _opencode((await c.get("/build/app/harnesses")).json()["harnesses"])
        assert [m["id"] for m in first["models"]] == [""], "nothing read yet: the default row alone"
        await until(lambda: not OCC.reading(svc._oc_read) and svc._oc_read.exists())
        oc = _opencode((await c.get("/build/app/harnesses")).json()["harnesses"])
    ids = [m["id"] for m in oc["models"]]
    assert ids[0] == "" and "opencode/big-pickle" in ids and "standin/probe-n" in ids
    deep = next(m for m in oc["models"] if m["id"] == "opencode/deepseek-v4-flash-free")
    assert deep["efforts"] == ["low", "high", "max"] and deep["group"] == "opencode"
    [run] = _runs(fresh)
    # The reader: OpenCode's own list, quietly — never models.dev, never a
    # permission posture, never the question tool (literal values).
    assert run["argv"] == ["models", "--verbose"]
    assert Path(run["cwd"]) == svc.home / "hosted" / "opencode-probe"
    assert run["env"] == {"OPENCODE_DISABLE_MODELS_FETCH": "1", "OPENCODE_PERMISSION": None,
                          "OPENCODE_ENABLE_QUESTION_TOOL": None}
    kept = json.loads(svc._oc_read.read_text(encoding="utf-8"))
    assert kept["fetched_at"] and kept["watch"] == OCC.watch_signature()


async def test_the_list_is_read_once_until_opencodes_own_files_change(tmp_path, monkeypatch, fresh):
    monkeypatch.setattr(OCC, "AUTO_PROBE", True)
    cache = tmp_path / "cache" / "opencode"
    app = create_app(str(tmp_path / "home"))
    svc = S.service_for(app.state.platform)
    async with client(app) as c:
        await c.get("/build/app/harnesses")
        await until(lambda: not OCC.reading(svc._oc_read) and svc._oc_read.exists())
        await c.get("/build/app/harnesses")
        await asyncio.sleep(0.2)
        assert len(_runs(fresh)) == 1, "a fresh list is not read again"
        cache.mkdir(parents=True)
        (cache / "models.json").write_text("{}", encoding="utf-8")  # OpenCode refreshed its cache
        await c.get("/build/app/harnesses")
        await until(lambda: len(_runs(fresh)) == 2 and not OCC.reading(svc._oc_read))


def test_the_reader_is_single_flight(tmp_path, monkeypatch, fresh):
    monkeypatch.setattr(OCC, "AUTO_PROBE", True)
    started: list = []
    gate = __import__("threading").Event()

    def slow(path, home, argv0):
        started.append(1)
        gate.wait(5)
        return True

    monkeypatch.setattr(OCC, "read_now", slow)
    path = tmp_path / "read.json"
    try:
        assert OCC.refresh_soon(path, tmp_path, ["x"]) is True
        assert OCC.refresh_soon(path, tmp_path, ["x"]) is True
        assert OCC.reading(path)
    finally:
        gate.set()
    deadline = time.time() + 5
    while OCC.reading(path) and time.time() < deadline:
        time.sleep(0.02)
    assert started == [1]


def test_an_opencode_that_does_not_answer_keeps_what_was_kept_and_waits_before_trying_again(
        tmp_path, monkeypatch, fresh):
    monkeypatch.setattr(OCC, "AUTO_PROBE", True)
    path = tmp_path / "read.json"
    OCC.save(path, [{"id": "a/kept", "label": "Kept"}], watch=[], now=1.0)
    monkeypatch.setenv("IJ_FAKE_OC_RC", "1")
    assert OCC.read_now(path, tmp_path, [sys.executable, str(FAKE)]) is False
    assert OCC.cached_rows(path) == [{"id": "a/kept", "label": "Kept"}]
    OCC._FAILED_AT[str(path)] = time.monotonic()
    assert OCC.refresh_soon(path, tmp_path, [sys.executable, str(FAKE)]) is False, "no retry at once"


def test_nothing_is_read_while_the_reader_is_off(tmp_path, monkeypatch, fresh):
    assert OCC.AUTO_PROBE is False, "the suite turns it off (conftest)"
    assert OCC.refresh_soon(tmp_path / "r.json", tmp_path, [sys.executable, str(FAKE)]) is False
    assert _runs(fresh) == []


def test_an_old_read_is_stale_and_a_fresh_one_is_not():
    now = 10_000.0
    assert OCC.is_stale({}, now=now)
    assert not OCC.is_stale({"fetched_at": now - 5, "watch": [["a", 1, 2]]}, now=now, watch=[["a", 1, 2]])
    assert OCC.is_stale({"fetched_at": now - OCC.CATALOG_TTL_S - 1}, now=now)
    assert OCC.is_stale({"fetched_at": now - 5, "watch": [["a", 1, 2]]}, now=now, watch=[["a", 1, 3]])


def test_a_sessions_own_list_lends_its_labels_and_adds_a_projects_own_models():
    read = [{"id": "opencode/big-pickle", "label": "Big Pickle", "efforts": []},
            {"id": "standin/probe-m", "label": "Probe M"}]
    listed = [{"id": "opencode/big-pickle", "label": "OpenCode Zen/Big Pickle", "efforts": ["high"]},
              {"id": "proj/only-here", "label": "Proj/Only here"},
              {"id": "standin/probe-m", "label": "Stand-in/Probe M", "efforts": ["low"]}]
    got = OCC.merged(read, listed)
    assert [r["id"] for r in got] == ["opencode/big-pickle", "standin/probe-m", "proj/only-here"]
    assert got[0] == {"id": "opencode/big-pickle", "label": "OpenCode Zen/Big Pickle", "efforts": []}
    assert got[1] == {"id": "standin/probe-m", "label": "Stand-in/Probe M", "efforts": ["low"]}
    assert OCC.merged([], listed) == listed


def test_the_reader_never_runs_on_the_event_loop():
    import inspect

    src = inspect.getsource(OCC.refresh_soon)
    assert "threading.Thread" in src and "read_now(" not in src.split("threading.Thread")[0]
    assert "procs.run" in inspect.getsource(OCC.probe)
