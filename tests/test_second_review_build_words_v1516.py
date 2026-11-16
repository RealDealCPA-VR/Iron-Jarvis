"""v1.516.0 — second review, group 16 part E: exception names in sentences a
person reads on the coding surfaces (decision E8: a plain sentence on screen,
the raw text in the log).

H11  a coding tool that broke while it started read "Codex did not start:
     KeyError" / "...: TimeoutError" as the reply's error title
     (``hosted/service.py``, the handshake after the spawn).
D10  a helper that crashed read "KeyError: 'path'" on its Timeline row
     (``harness/helpers.invoke_helper``); an attempt read the lane's raw
     exception text, or "The attempt stopped on an internal error (KeyError)."
     on the attempts card (``harness/attempts._run_one``).

A sentence Iron Jarvis (or the coding tool, through its own refusal class)
wrote for a person is kept as it was. NO REAL CODING TOOL EVER STARTS HERE:
the service's process class and driver are stand-ins and the program path does
not exist. Waits wait for the thing asserted.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from iron_jarvis.daemon import chat_stream as CS
from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import attempts as A
from iron_jarvis.harness import helpers as H
from iron_jarvis.hosted import service as S
from iron_jarvis.hosted import store
from iron_jarvis.hosted.driver import ControlRefused, Driver
from tests._build_app_helpers_v1409 import until

_ENGINE = ("KeyError", "TimeoutError", "Error:", "'thread'", "'path'")


def _plain(text: str) -> None:
    for word in _ENGINE:
        assert word not in text, f"engine text on screen: {text!r}"


# --------------------------------------------------------------------------- #
# H11: a coding tool that broke while it started
# --------------------------------------------------------------------------- #
@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    return folder


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


class _BrokenDriver(Driver):
    """A coding tool's driver whose handshake raises ``boom``."""

    harness = "codex"

    def __init__(self, boom: BaseException) -> None:
        async def _write(obj: Any) -> None:
            return None

        super().__init__(write=_write, emit=lambda ev: None, ask=lambda a: None,
                         on_session_id=lambda s: None, mode="ask")
        self.boom = boom

    def argv(self, *, session_id: str = "", resume: bool = False) -> list[str]:
        return []

    async def open(self, *, session_id: str = "", resume: bool = False) -> None:
        raise self.boom

    async def send_user(self, text: str, files: Any) -> None:
        return None

    async def interrupt(self) -> None:
        return None

    async def answer(self, *a: Any, **k: Any) -> None:
        return None

    def on_exit(self) -> None:
        return None

    def on_message(self, obj: Any) -> None:
        return None


def _rig(svc: S.HostedService, monkeypatch, tmp_path: Path, boom: BaseException) -> list[Any]:
    procs: list[Any] = []

    class _Proc:
        def __init__(self, argv: Any, env: Any = None, cwd: Any = None, name: str = "",
                     ends_left_behind: bool = False) -> None:
            self.alive = False
            self.stopped = False
            self.lock = threading.Lock()
            procs.append(self)

        def start(self, loop: Any, on_message: Any, on_exit: Any) -> None:
            self.alive = True

        def stop(self) -> None:
            self.alive = False
            self.stopped = True

        def write(self, obj: Any) -> None:
            return None

        def stderr_text(self) -> str:
            return ""

    monkeypatch.setattr(S, "HarnessProcess", _Proc)
    # A path that does not exist: never a real program (tests/_real_tool_guard.py).
    monkeypatch.setattr(S, "binary_for", lambda harness: [str(tmp_path / "no-such-coding-tool.exe")])
    monkeypatch.setattr(svc, "_new_driver", lambda live, row, write, **kw: _BrokenDriver(boom))
    return procs


async def _start_error(app, work: Path, tmp_path: Path, monkeypatch, boom: BaseException) -> dict[str, Any]:
    svc = S.service_for(app.state.platform)
    _rig(svc, monkeypatch, tmp_path, boom)
    row = store.create(app.state.platform.engine, harness="codex", folder=str(work))
    try:
        turn = await svc.start_turn(row.id, "write the report", "t1")
        await until(lambda: not turn.running)
        errors = [e for e in turn.events if e.get("type") == "error"]
        assert turn.status == "failed" and errors, turn.events
        return errors[-1]
    finally:
        await svc.shutdown()


async def test_h11_a_tool_that_broke_while_starting_is_said_in_plain_words(app, work, tmp_path, monkeypatch):
    err = await _start_error(app, work, tmp_path, monkeypatch, KeyError("thread"))
    _plain(err["title"])
    _plain(err["fix"])
    assert err["title"] == "Codex did not start.", err


async def test_h11_a_handshake_that_ran_out_of_time_says_so(app, work, tmp_path, monkeypatch):
    err = await _start_error(app, work, tmp_path, monkeypatch, TimeoutError())
    _plain(err["title"])
    assert err["title"] == "Codex did not answer in time while it started.", err


async def test_h11_the_tools_own_refusal_is_kept(app, work, tmp_path, monkeypatch):
    err = await _start_error(app, work, tmp_path, monkeypatch, ControlRefused("Codex did not start a thread"))
    assert err["title"] == "Codex did not start: Codex did not start a thread", err


# --------------------------------------------------------------------------- #
# D10: a helper's row
# --------------------------------------------------------------------------- #
async def _helper_rows(boom: BaseException) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    parent = H.HelperParent(session_id="build:s1", folder="", provider="anthropic", model="m")

    async def _invoke() -> Any:
        raise boom

    with pytest.raises(type(boom)):
        await H.invoke_helper(parent, "call1", "delegate", {"agent": "builder", "task": "write the report"},
                              _invoke, emit=lambda kind, frame: frames.append(dict(frame)))
    return frames


async def test_d10_a_helper_that_crashed_is_said_in_plain_words():
    frames = await _helper_rows(KeyError("path"))
    last = frames[-1]
    assert last.get("state") == "failed", last
    _plain(str(last.get("result") or ""))
    assert last["result"] == "The helper stopped on an internal error.", last


async def test_d10_a_helpers_own_sentence_is_kept():
    frames = await _helper_rows(A.AttemptsRefused("The folder this helper works in is gone."))
    assert frames[-1]["result"] == "The folder this helper works in is gone.", frames[-1]


# --------------------------------------------------------------------------- #
# D10: an attempt's card
# --------------------------------------------------------------------------- #
def _attempt() -> A.Attempt:
    return A.Attempt(id="try1", target=A.Target(label="Model one", provider="ollama", model="local-model"),
                     pin=None, git={}, folder="", sid="build:x~try1", turn_id="t.try1")


async def _attempt_error(monkeypatch, stream: Any) -> str:
    async def _no_end(sid: str) -> None:
        return None

    monkeypatch.setattr(A, "_body", lambda plan, a: SimpleNamespace())
    monkeypatch.setattr(A, "attempt_cap", lambda plan, a: None)
    monkeypatch.setattr(A, "_end_programs", _no_end)
    monkeypatch.setattr(CS, "stream_chat_turn", stream)
    plan = SimpleNamespace(rounds=None, ceiling=None)
    queue: asyncio.Queue = asyncio.Queue()
    a = _attempt()
    await A._run_one(None, {}, plan, a, queue, lambda: False)
    got = []
    while not queue.empty():
        got.append(queue.get_nowait())
    errors = [json.loads(item[3]) for item in got if item[0] == "frame" and item[2] == "error"]
    assert errors, got
    detail = str(errors[-1].get("detail") or "")
    a.fold("error", json.dumps(errors[-1]))
    return detail


async def test_d10_an_attempt_the_lane_refused_on_a_crash_is_said_in_plain_words(monkeypatch):
    async def _stream(*a: Any, **k: Any) -> Any:
        raise KeyError("path")

    detail = await _attempt_error(monkeypatch, _stream)
    _plain(detail)
    assert detail == "The attempt could not start because of an internal error.", detail


async def test_d10_the_lanes_own_refusal_is_kept(monkeypatch):
    async def _stream(*a: Any, **k: Any) -> Any:
        raise HTTPException(status_code=404, detail="no such skill: report-writer")

    assert await _attempt_error(monkeypatch, _stream) == "no such skill: report-writer"


async def test_d10_an_attempt_that_broke_outside_its_stream_is_said_in_plain_words(monkeypatch):
    async def _frames():
        raise KeyError("path")
        yield ""  # pragma: no cover — an async generator

    async def _stream(*a: Any, **k: Any) -> Any:
        return _frames()

    detail = await _attempt_error(monkeypatch, _stream)
    _plain(detail)
    assert detail == "The attempt stopped on an internal error.", detail
