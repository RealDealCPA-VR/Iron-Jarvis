"""Idea 33-a: who turns a recording into text is the user's own pick, local
first, and a pick that cannot be used is refused BY NAME, never switched.

* "Same as voice" (the default): the speech server chosen for voice when it is
  on this network, else the offline model on this PC. OpenAI, chosen for
  dictation, is never sent a whole meeting on that choice alone (the reasoning
  of D-10.1): with no offline model the refusal names OpenAI, the one step
  that turns it on for recordings, and its cost.
* "The speech service chosen for voice": that service wherever it is; a Local
  only chat refuses one off the network by name, and nothing is heard on this
  PC instead (no silent switch).
* The speech server is sent the recording a minute at a time, through the
  real ``transcribe_clip``; each piece goes only while the service chosen for
  voice is still the one the job started with, and only while the chat is not
  Local only (the real refusal, ``SpeechKeptHere``, stops the rest).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.documents import recording as R
from iron_jarvis.documents import recording_jobs as J
from tests._fakes.recordings import Deps, wav

pytestmark = pytest.mark.timeout(240)

LAN = ("stt", "http://192.168.1.20:9000/v1/audio/transcriptions", None)
CLOUD_STT = ("stt", "https://speech.example.com/v1/audio/transcriptions", "test-key")
OPENAI = ("openai", "https://api.openai.com/v1/audio/transcriptions", "test-key")
CUSTOM = ("custom", "http://127.0.0.1:11434/v1/audio/transcriptions", None)
MODEL = "C:/fake/vosk-model"


def test_same_as_voice_uses_the_speech_server_on_this_network():
    tr = R.choose_transcriber("same_as_voice", LAN, MODEL, local_only=True)
    assert tr.kind == R.SPEECH_SERVER and tr.who == "the speech server at 192.168.1.20:9000"
    assert tr.note == ""


def test_same_as_voice_never_sends_a_recording_off_the_network_and_says_so():
    for backend, who in ((CLOUD_STT, "speech.example.com"), (OPENAI, "OpenAI")):
        tr = R.choose_transcriber("same_as_voice", backend, MODEL, local_only=False)
        assert tr.kind == R.THIS_PC and tr.model == MODEL
        assert who in tr.note and "off your network" in tr.note and "on this PC instead" in tr.note
        assert "The speech service chosen for voice" in tr.note, "the one step that sends it there"


def test_without_the_offline_model_openai_is_refused_by_name_with_its_cost():
    with pytest.raises(R.RecordingRefused) as exc:
        R.choose_transcriber("same_as_voice", OPENAI, None, local_only=False)
    words = str(exc.value)
    assert "OpenAI is chosen for voice" in words and "bills your account per minute" in words
    assert "The speech service chosen for voice" in words and "Nothing was sent anywhere" in words


def test_nothing_set_up_is_said():
    with pytest.raises(R.RecordingRefused) as exc:
        R.choose_transcriber("same_as_voice", None, None, local_only=False)
    assert "offline speech model is not on this PC" in str(exc.value)


def test_the_chat_model_server_is_not_a_speech_service_the_user_chose():
    tr = R.choose_transcriber("same_as_voice", CUSTOM, MODEL, local_only=False)
    assert tr.kind == R.THIS_PC and tr.note == ""
    with pytest.raises(R.RecordingRefused) as exc:
        R.choose_transcriber("voice_service", CUSTOM, MODEL, local_only=False)
    assert "no speech service is chosen for voice" in str(exc.value)


def test_this_pc_only_is_only_this_pc():
    assert R.choose_transcriber("this_pc", LAN, MODEL, local_only=False).kind == R.THIS_PC
    with pytest.raises(R.RecordingRefused) as exc:
        R.choose_transcriber("this_pc", LAN, None, local_only=False)
    assert "only on this PC" in str(exc.value) and "Nothing was sent anywhere" in str(exc.value)


def test_local_only_refuses_a_cloud_pick_by_name_and_never_switches():
    # The offline model IS here: a silent switch to it would be easy, and it
    # is exactly what must not happen. The refusal names the service.
    with pytest.raises(R.RecordingRefused) as exc:
        R.choose_transcriber("voice_service", OPENAI, MODEL, local_only=True)
    words = str(exc.value)
    assert "Local only" in words and "nothing was sent to OpenAI" in words
    with pytest.raises(R.RecordingRefused) as exc:
        R.choose_transcriber("voice_service", CLOUD_STT, MODEL, local_only=True)
    assert "speech.example.com" in str(exc.value)
    # Off Local only, the pick is honoured; on the network, Local only too.
    assert R.choose_transcriber("voice_service", OPENAI, MODEL, local_only=False).kind == R.VOICE_SERVICE
    assert R.choose_transcriber("voice_service", LAN, MODEL, local_only=True).kind == R.SPEECH_SERVER


def test_the_setting_reads_junk_as_the_default(tmp_path):
    from iron_jarvis.core.config import Config

    assert R.clean_pick("Elsewhere") == R.PICK_SAME_AS_VOICE
    base = {"home": tmp_path, "project_root": tmp_path}
    assert Config(**base).recording_transcriber == "same_as_voice"
    assert Config(**base, recording_transcriber="this_pc").recording_transcriber == "this_pc"
    assert Config(**base, recording_transcriber="cloud").recording_transcriber == "same_as_voice"


def test_the_setting_is_a_row_in_settings_with_its_three_choices():
    from iron_jarvis.settings.schema import BY_KEY

    row = BY_KEY["recording_transcriber"]
    assert [v for v, _ in row.options] == list(R.PICKS)
    assert row.tier == "ask" and row.section == "Voice"
    assert "OpenAI bills per minute" in row.help and "Local only" in row.help


# --------------------------------------------------------------------------- #
# through the tool: the refusal reaches the model, and nothing is made
# --------------------------------------------------------------------------- #
@pytest.fixture()
def desk(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path / "home")))
    p = client.app.state.platform
    ws = tmp_path / "work"
    ws.mkdir()
    yield p, client, ws
    client.close()


def _invoke(p, ws: Path, path: Path, *, local_only: bool):
    from iron_jarvis.providers.egress import local_only_scope
    from iron_jarvis.tools.base import ToolContext

    ctx = ToolContext(workspace=ws, session_id="chat", agent_run_id="chat", config=p.config,
                      event_bus=p.event_bus, engine=p.engine, thread_id="thr_rec")

    async def _go():
        with local_only_scope(local_only):
            return await p.registry.invoke(
                "transcribe_recording", {"path": str(path)}, ctx, p.permissions, None,
                allowed_names={"transcribe_recording"},
            )

    return asyncio.run(_go())


def test_the_tool_refuses_a_cloud_pick_in_a_local_only_chat_and_makes_nothing(desk, monkeypatch):
    p, client, ws = desk
    monkeypatch.setattr(J, "_DEPS", Deps(p, model=MODEL, backend=OPENAI))
    monkeypatch.setattr(p.config, "recording_transcriber", "voice_service")
    clip = wav(ws / "call.wav", 2.0)
    r = _invoke(p, ws, clip, local_only=True)
    assert not r.ok
    assert "Local only" in r.error and "OpenAI" in r.error
    assert sorted(x.name for x in ws.iterdir()) == ["call.wav"]
    assert not list(J.jobs_dir(p.config.home).glob("rec_*.json")), "no job was started"


# --------------------------------------------------------------------------- #
# the speech server, a minute at a time
# --------------------------------------------------------------------------- #
def _service_job(tmp_path: Path, monkeypatch, deps: Deps, backend, *, thread_id: str = "thr_rec") -> J.Job:
    monkeypatch.setattr(J, "_DEPS", deps)
    clip = wav(tmp_path / "call.wav", 150.0, rate=16000)
    ws = tmp_path / "work"
    ws.mkdir(exist_ok=True)
    t, n = R.target_paths(ws, clip.name)
    tr = R.choose_transcriber("voice_service", backend, None, local_only=False)
    env = J.Env(engine=None, event_bus=None, config=SimpleNamespace(home=tmp_path / "home"))
    state = J.new_state(source=clip, sha256="x", workspace=ws, thread_id=thread_id, turn_id="turn1",
                        local_only=False, transcriber=tr, transcript=t, notes=n)
    job = J.Job(state=state, env=env, transcriber=tr)
    return job


def test_a_speech_server_is_sent_the_recording_a_minute_at_a_time(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import voice as voice_routes

    deps = Deps(None, model=None, backend=LAN)
    job = _service_job(tmp_path, monkeypatch, deps, LAN)
    sent: list[dict] = []

    async def fake_clip(d, *, data=None, mime="", local_only=False, **kw):
        sent.append({"bytes": len(data or b""), "mime": mime, "local_only": local_only})
        return {"text": f"piece {len(sent)} words here"}

    monkeypatch.setattr(voice_routes, "transcribe_clip", fake_clip)
    asyncio.run(J._run(job))
    assert job.state["status"] == J.STATUS_DONE, job.state.get("error")
    assert [s["mime"] for s in sent] == ["audio/wav"] * 3
    assert sent[0]["bytes"] > 60 * 16000 * 2 - 100, "a minute of 16 kHz mono"
    text = Path(job.state["transcript"]).read_text(encoding="utf-8")
    assert "[00:00] piece 1 words here" in text and "[01:00] piece 2" in text and "[02:00] piece 3" in text
    assert "the speech server at 192.168.1.20:9000" in text
    assert not any((J.jobs_dir(tmp_path / "home") / "pieces").iterdir()), "the pieces are removed"


def test_a_change_of_the_voice_service_midway_sends_nothing_more(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import voice as voice_routes

    deps = Deps(None, model=None, backend=LAN)
    job = _service_job(tmp_path, monkeypatch, deps, LAN)
    sent: list[int] = []

    async def fake_clip(d, *, data=None, **kw):
        sent.append(len(data or b""))
        # The user picks another service in Settings after the first piece.
        deps.backend = ("stt", "http://192.168.1.99:9000/v1/audio/transcriptions", None)
        return {"text": "first piece"}

    monkeypatch.setattr(voice_routes, "transcribe_clip", fake_clip)
    asyncio.run(J._run(job))
    assert len(sent) == 1, "nothing went to the service chosen since"
    assert job.state["status"] == J.STATUS_FAILED
    assert "changed" in job.state["error"] and "nothing more was sent" in job.state["error"]
    assert not Path(job.state["transcript"]).exists()


def test_a_chat_made_local_only_midway_is_refused_by_the_real_transcribe_clip(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import voice as voice_routes
    from iron_jarvis.providers import egress

    deps = Deps(SimpleNamespace(config=SimpleNamespace(voice_transcribe_model="")), model=None, backend=OPENAI)
    job = _service_job(tmp_path, monkeypatch, deps, OPENAI)
    real = voice_routes.transcribe_clip
    calls: list[bool] = []
    local = {"on": False}

    async def first_then_real(d, **kw):
        calls.append(bool(kw.get("local_only")))
        if len(calls) == 1:
            local["on"] = True  # the user turns Local only on for the chat
            return {"text": "first piece"}
        return await real(d, **kw)  # refuses OpenAI under Local only, sending nothing

    monkeypatch.setattr(voice_routes, "transcribe_clip", first_then_real)
    monkeypatch.setattr(egress, "thread_local_only", lambda engine, tid: local["on"])
    asyncio.run(J._run(job))
    assert calls == [False, True]
    assert job.state["status"] == J.STATUS_FAILED
    assert "local only" in job.state["error"].lower() and "OpenAI" in job.state["error"]
    assert "Nothing more was sent" in job.state["error"]


def test_the_job_state_never_holds_a_key():
    tr = R.choose_transcriber("voice_service", OPENAI, None, local_only=False)
    assert "test-key" not in json.dumps(tr.as_dict())
