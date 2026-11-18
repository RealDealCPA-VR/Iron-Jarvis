"""Fourth review, group 24, MS1: "Running now" never tells another app a Local
only or kept-home coding session.

``work_search`` starts with ``running.running_block``. Each running row
carries ``held``: a Chat › Code session is held when its work-record row is
Local only or kept home, OR when its chat, asked again right now
(``collect.live_state``), is Local only (failing closed); a Build › Code
session is held when its tool is not one that sends to its own vendor anyway
(the record's own rule, ``collect.hosted_candidates``). A row is told only
when it is not held AND the outside wall allows it. And every line it hands
out is masked (``block._safe``): a Build › Code title comes from memory,
never from a stored row.

Through ``WorkSearchTool`` and the real ``running._rows``: only the source of
WHAT runs is replaced (``durable_turns.running_replies``; for Build › Code,
``hosted.news.running_rows`` and the live session, as
``test_running_elsewhere_v1530``'s control does). Every value is made up.
"""

# ruff: noqa: F811 — the scene fixture is imported from test_running_elsewhere_v1530
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import BUILD_SURFACE, ChatThreadRecord
from iron_jarvis.daemon import durable_turns
from iron_jarvis.hosted import news
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.workmap import running
from iron_jarvis.workmap.connector_tools import WorkSearchTool
from iron_jarvis.workmap.index import work_map
from tests.test_running_elsewhere_v1530 import scene  # noqa: F401 — fixture by name

TITLE = "Speed up the import"


def _thread(scene, *, local_only: bool = False) -> str:
    setup = {"workspace_dir": str(scene.folder)}
    if local_only:
        setup["local_only"] = True
    messages = [{"role": "user", "content": "speed it up"}, {"role": "assistant", "content": "On it."}]
    with session_scope(scene.engine) as db:
        rec = ChatThreadRecord(title=TITLE, surface=BUILD_SURFACE, setup_json=json.dumps(setup),
                               messages_json=json.dumps(messages))
        db.add(rec)
        db.commit()
        return rec.id


def _running(monkeypatch, tid: str) -> None:
    monkeypatch.setattr(durable_turns, "running_replies", lambda *a, **k: [
        {"turn_id": "t1", "thread_id": tid, "surface": "build", "started_at": "", "waiting": False}])


def _ask(scene) -> str:
    ctx = ToolContext(workspace=scene.tmp, session_id="mcp:app:codex", agent_run_id="",
                      config=scene.platform.config, event_bus=scene.platform.event_bus, engine=scene.engine)
    got = asyncio.run(WorkSearchTool(scene.platform).execute({}, ctx))
    assert got.ok is True
    return got.output


def _wm(scene):
    return work_map(scene.engine, scene.platform.config.home)


@pytest.mark.parametrize("local_only", [True, False])
def test_a_local_only_chat_code_session_is_not_told_to_another_app(scene, monkeypatch, local_only):
    tid = _thread(scene, local_only=local_only)
    _wm(scene).refresh(force=True)
    _running(monkeypatch, tid)
    text = _ask(scene)
    if local_only:
        assert TITLE not in text and str(scene.folder) not in text, text
        assert running.NOTHING in text
        assert running.NOTHING == "Nothing Iron Jarvis may share with this app is running right now."
    else:  # the control: the same session, not Local only, is told
        assert f"- Chat › Code: {TITLE}, in {scene.folder}" in text, text


def test_a_session_switched_to_local_only_after_its_row_was_built_is_not_told(scene, monkeypatch):
    tid = _thread(scene)
    wm = _wm(scene)
    wm.refresh(force=True)
    with session_scope(scene.engine) as db:
        rec = db.get(ChatThreadRecord, tid)
        rec.setup_json = json.dumps({"workspace_dir": str(scene.folder), "local_only": True})
        db.add(rec)
        db.commit()
    _running(monkeypatch, tid)
    text = _ask(scene)
    # The row was not rebuilt (the record's refresh is throttled) ...
    (row,) = [r for r in wm._rows() if r["ref"] == tid]
    assert row["local_only"] is False
    # ... and the session, asked again, is not told.
    assert TITLE not in text and running.NOTHING in text, text


def test_a_session_whose_source_cannot_be_asked_is_not_told(scene, monkeypatch):
    from iron_jarvis.workmap import collect

    tid = _thread(scene)
    _wm(scene).refresh(force=True)
    _running(monkeypatch, tid)

    def broken(db, rows):
        raise RuntimeError("the chats cannot be read")

    monkeypatch.setattr(collect, "live_state", broken)
    text = _ask(scene)
    assert TITLE not in text and running.NOTHING in text, text


def _build_code(scene, monkeypatch, harness: str, title: str = "Fix the parser") -> None:
    live = SimpleNamespace(folder=str(scene.folder))
    scene.platform.hosted = SimpleNamespace(live={"hs_1": live}, clock=time.monotonic)
    monkeypatch.setattr(news, "running_rows", lambda platform: [
        {"thread_id": "hs_1", "harness": harness, "title": title, "waiting": False}])


@pytest.mark.parametrize("harness,told", [("pi", False), ("opencode", False), ("codex", True),
                                          ("claude-code", True)])
def test_a_build_code_session_on_a_tool_that_may_stay_local_is_not_told(scene, monkeypatch, harness, told):
    _build_code(scene, monkeypatch, harness)
    text = _ask(scene)
    if told:
        assert f": Fix the parser, in {scene.folder}" in text, text
    else:
        assert "Fix the parser" not in text and running.NOTHING in text, text


def test_a_build_code_session_in_a_folder_under_clients_is_not_told(scene, monkeypatch):
    smith = scene.tmp / "Clients" / "Smith"  # no client row: provisional
    smith.mkdir(parents=True)
    _build_code(scene, monkeypatch, "codex")
    scene.platform.hosted.live["hs_1"].folder = str(smith)
    text = _ask(scene)
    assert "Fix the parser" not in text and running.NOTHING in text, text


def test_a_secret_in_a_build_code_title_is_masked_before_it_is_told(scene, monkeypatch):
    fake = "sk-" + "test-" + "x" * 30
    _build_code(scene, monkeypatch, "codex", title=f"Rotate the key {fake} in the config")
    text = _ask(scene)
    assert "Rotate the key" in text, text
    assert fake not in text and "***" in text, text


def test_a_row_with_no_held_fact_is_held(scene, monkeypatch):
    monkeypatch.setattr(running, "_rows", lambda platform: [
        {"where": "Chat › Code", "title": TITLE, "folder": str(scene.folder), "waiting": False}])
    assert running.running_block(scene.platform) == running.NOTHING
