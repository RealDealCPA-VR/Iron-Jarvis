"""v1.439.0 — OPT-IN: the REAL Antigravity (``agy``) driven by the real Code-view
service. Skipped unless ``IJ_LIVE_AGY`` names the binary
(``IJ_LIVE_AGY=C:\\path\\to\\agy.exe``).

Never against Google and never with a sign-in: the agy process gets a scratch
``USERPROFILE``/``HOME``, ``GEMINI_API_KEY`` is a fake, and every model call goes
to a LOCAL stand-in Gemini API (``tests/fixtures/agy_standin_v1439.py`` on
127.0.0.1, ``GOOGLE_GEMINI_BASE_URL``). The scratch home is given agy's OWN
"last sign-in method" file (``cache/acp-auth-state.json``) — what agy writes
itself after ``authenticate`` — so its silent sign-in uses that fake key; Iron
Jarvis never calls ``authenticate``. ``--disable-telemetry`` is in the product's
own argv.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.hosted import antigravity as A
from iron_jarvis.hosted import service as S
from tests._build_app_helpers_v1409 import client, frames, stop_all, until

AGY = os.environ.get("IJ_LIVE_AGY", "").strip()
STANDIN = Path(__file__).parent / "fixtures" / "agy_standin_v1439.py"

pytestmark = pytest.mark.skipif(not AGY or not Path(AGY).is_file(), reason="IJ_LIVE_AGY names no agy binary")

CMD = {"functionCall": {"name": "run_command", "args": {"CommandLine": "echo live-from-agy", "toolSummary": "echo",
                                                         "WaitMsBeforeAsync": 3000}}}
ASK = {"functionCall": {"name": "ask_question",
                        "args": {"questions": [{"question": "Which color?", "options": ["Blue", "Red"]}]}}}


def _port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture()
def live(tmp_path, monkeypatch):
    home = tmp_path / "userhome"
    agy_home = home / ".gemini" / "antigravity-cli"
    (agy_home / "cache").mkdir(parents=True)
    (agy_home / "cache" / "acp-auth-state.json").write_text('{"methodId":"gemini-api-key"}', encoding="utf-8")
    work = tmp_path / "work"
    work.mkdir()
    answers = tmp_path / "answers.json"
    answers.write_text(json.dumps([[{"text": "x"}]]), encoding="utf-8")
    port = _port()
    standin = subprocess.Popen([sys.executable, "-I", str(STANDIN), str(port), str(tmp_path / "req.log"),
                                str(answers)])
    for k in list(os.environ):
        if k.startswith(("GEMINI", "GOOGLE_", "ANTIGRAVITY")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-key-probe")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", f"http://127.0.0.1:{port}")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv(A.HOME_ENV, str(agy_home))
    monkeypatch.setattr(S, "binary_for", lambda harness: [AGY] if harness == A.HARNESS else None)
    time.sleep(0.5)
    try:
        yield SimpleNamespaceLive(work=work, answers=answers, req=tmp_path / "req.log", root=tmp_path)
    finally:
        standin.kill()


class SimpleNamespaceLive:
    def __init__(self, **kw):
        self.__dict__.update(kw)


async def _turn(c, svc, sid, text, answers=()):
    task = asyncio.ensure_future(c.post(f"/build/app/sessions/{sid}/turn", json={"text": text, "turn_id": text}))
    for decision, picked in answers:
        ask = await until(lambda: next(iter(svc.live[sid].asks), None) if sid in svc.live else None, 90)
        r = await c.post(f"/build/app/sessions/{sid}/answer",
                         json={"request_id": ask, "decision": decision, "answers": picked})
        assert r.status_code == 200, r.text
    return frames((await asyncio.wait_for(task, 120)).text)


async def test_the_real_antigravity_answers_asks_and_reopens_through_the_service(live):
    app = create_app(str(live.root / "ij"))
    svc = S.service_for(app.state.platform)
    try:
        async with client(app) as c:
            r = await c.post("/build/app/sessions", json={"harness": "antigravity", "folder": str(live.work)})
            assert r.status_code == 201, r.text
            sid = r.json()["id"]
            live.answers.write_text(json.dumps([[CMD], [{"text": "ran it"}]]), encoding="utf-8")
            evs = await _turn(c, svc, sid, "run it", [("allow", None)])
            card = next(e for e in evs if e["type"] == "approval")
            assert card["kind"] == "command" and card["once_only"] is True and card["command"] == "echo live-from-agy"
            done_rows = [e for e in evs if e["type"] == "tool" and e["status"] == "done"]
            assert done_rows and "live-from-agy" in done_rows[-1].get("output", "")
            assert [e["text"] for e in evs if e["type"] == "text_done"] == ["ran it"]
            assert evs[-1]["type"] == "done" and evs[-1]["stop"] == "completed"
            assert next(e for e in evs if e["type"] == "usage")["input"] > 0

            live.answers.write_text(json.dumps([[ASK], [{"text": "got it"}]]), encoding="utf-8")
            evs = await _turn(c, svc, sid, "ask me", [("allow", {"Which color?": "Red"})])
            assert next(e for e in evs if e["type"] == "question")["questions"][0]["question"] == "Which color?"
            assert evs[-1]["stop"] == "completed"
            assert "A1: Red" in live.req.read_text(encoding="utf-8"), "the model read the chosen answer"

            log_file = Path(app.state.platform.config.home) / "hosted" / sid / A.LOG_NAME
            assert log_file.is_file(), "its own log goes to the session's folder (--log-file)"
            hid = (await c.get(f"/build/app/sessions/{sid}")).json()["session"]["harness_session_id"]
            # One conversation, one row: the first row goes (its process with it).
            assert (await c.delete(f"/build/app/sessions/{sid}")).status_code in (200, 204)

            listed = S.disk_sessions("antigravity", S.default_home("antigravity"), str(live.work))
            assert hid in [s["id"] for s in listed], "its own saved conversation is listed for the folder"

            r = await c.post("/build/app/sessions", json={"harness": "antigravity", "folder": str(live.work),
                                                          "harness_session_id": hid})
            assert r.status_code == 201, r.text
            sid2 = r.json()["id"]
            live.answers.write_text(json.dumps([[{"text": "resumed"}]]), encoding="utf-8")
            evs = await _turn(c, svc, sid2, "again")
            assert [e["text"] for e in evs if e["type"] == "text_done"] == ["resumed"]
    finally:
        await stop_all(app)
