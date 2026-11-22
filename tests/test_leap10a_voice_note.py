"""10-a (build-out plan R2.3, settled choice D-10.1): a voice note from the
phone becomes the user's words.

* The offline speech model on this PC first, through a short-lived child
  (``iron_jarvis.audio_worker``, started through ``core/procs``). Its words
  are echoed first ("I heard: ..."), so a mishearing shows, then they are the
  message.
* Only when the offline model is missing or heard nothing: the speech service
  chosen in Settings, and only after the user said yes ONCE ("Use OpenAI for
  phone voice notes too?"). The answer is a setting with Undo
  (``phone_voice_service``). The reply names the service.
* A Local only chat never sends a voice note off the network: the real
  ``transcribe_clip`` refuses it (``SpeechKeptHere``).
* A worker that stalls is killed at its bound and the user hears words.
* Nothing set up: the user hears that, and where the recording is kept.

The speech service is a fake HTTP client; Telegram and the chat turn are
fakes. The worker's own child is real in two tests.
"""

from __future__ import annotations

import json
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.comm import voice_note as VN
from iron_jarvis.core import http as core_http
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ChatThreadRecord, UndoJournal
from iron_jarvis.settings.writer import ConfigWriter
from tests._fakes.phone_files import (
    ME,
    VOICE,
    FakeTelegram,
    chat_root,
    fake_turn,
    files_under,
    fixed_time,
    message,
    poller,
    run,
    telegram,
    voice,
)

pytestmark = pytest.mark.usefixtures("no_network")

OGG = b"OggS\x00voice"
OPENAI = ("openai", "https://api.openai.com/v1/audio/transcriptions", "test-key")


class _Deps:
    _VOICE_MAX_BYTES = 25 * 1024 * 1024

    def __init__(self, platform, *, model: str | None = None, backend: Any = None) -> None:
        self.platform = platform
        self.model = model
        self.backend = backend

    def _vosk_model_path(self) -> str | None:
        return self.model

    def _voice_backend(self):
        return self.backend


class _Speech:
    """The speech service's HTTP client, recorded."""

    calls: list[dict] = []

    def __init__(self, **kw: Any) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False

    async def post(self, url, headers=None, data=None, files=None):  # noqa: ANN001
        _Speech.calls.append({"url": url, "model": (data or {}).get("model")})
        return SimpleNamespace(status_code=200, json=lambda: {"text": "buy milk on the way home"},
                               text="")


@pytest.fixture()
def speech(monkeypatch):
    _Speech.calls = []
    monkeypatch.setattr(core_http, "async_client", _Speech)
    return _Speech


def _rig(platform, tmp_path, *, model=None, backend=None, worker: Any = None, monkeypatch=None):
    root = chat_root(platform, tmp_path)
    platform.config_writer = ConfigWriter(SimpleNamespace(platform=platform))
    if worker is not None:
        monkeypatch.setattr(VN, "_ask_worker", worker)
    fake = FakeTelegram([voice(10)], files={"V1": OGG})
    turn = fake_turn("Added milk.")
    poll = poller(platform, telegram(fake), turn=turn)
    poll.voice_deps = _Deps(platform, model=model, backend=backend)
    return root, fake, poll, turn


def test_offline_first_echoes_what_it_heard_then_does_the_work(platform, tmp_path, monkeypatch, speech):
    asked: list[dict] = []

    def worker(request: dict, timeout: float) -> dict:
        asked.append(request)
        return {"text": "add milk to the list", "seconds": 2.0}

    fixed_time(monkeypatch)
    root, fake, poll, turn = _rig(platform, tmp_path, model=str(tmp_path / "vosk"),
                                  backend=OPENAI, worker=worker, monkeypatch=monkeypatch)
    run(poll)

    kept = files_under(root)
    assert [p.name for p in kept] == [VOICE]
    assert asked and asked[0]["op"] == "transcribe" and asked[0]["path"] == str(kept[0])
    assert asked[0]["model"] == str(tmp_path / "vosk")
    assert f"Saved {VOICE} in " in fake.texts[0]
    assert "I heard: add milk to the list" in fake.texts[0]
    assert turn.calls[0].messages[-1].content == "add milk to the list"
    # The recording's WORDS are the message; the recording itself is named
    # (its path, for "file this voice note"), never extracted as a document.
    assert turn.calls[0].attachments == []
    assert turn.calls[0].thread_files == [str(kept[0])]
    assert speech.calls == []  # the service was never reached


def test_the_service_is_used_only_after_a_yes_once_and_is_named(platform, tmp_path, monkeypatch, speech):
    root, fake, poll, turn = _rig(platform, tmp_path, model=None, backend=OPENAI)
    run(poll)

    assert speech.calls == []
    assert turn.calls == []
    ask = fake.texts[0]
    assert "Use OpenAI for phone voice notes too?" in ask and "1" in ask and "2" in ask
    assert platform.config.phone_voice_service == "ask"

    fake.updates = [message(11, text="1")]
    run(poll)

    assert platform.config.phone_voice_service == "yes"
    assert len(speech.calls) == 1 and speech.calls[0]["url"] == OPENAI[1]
    heard = [t for t in fake.texts if "I heard (by OpenAI, which you chose for voice): " in t]
    assert heard and "buy milk on the way home" in heard[0]
    assert turn.calls[-1].messages[-1].content == "buy milk on the way home"
    # The yes is a setting change with an Undo.
    with session_scope(platform.engine) as db:
        rows = [r for r in db.exec(__import__("sqlmodel").select(UndoJournal)).all()
                if r.kind == "setting_restore"]
    assert rows and "phone_voice_service" in rows[-1].pre_inline

    # A second voice note goes straight to the service, no question.
    fake.updates = [voice(12, "V2")]
    fake.files["V2"] = OGG
    n = len(fake.texts)
    run(poll)
    assert len(speech.calls) == 2
    assert not any("phone voice notes too?" in t for t in fake.texts[n:])


def test_a_no_keeps_voice_notes_on_this_pc(platform, tmp_path, monkeypatch, speech):
    root, fake, poll, turn = _rig(platform, tmp_path, model=None, backend=OPENAI)
    run(poll)
    fake.updates = [message(11, text="2")]
    run(poll)

    assert platform.config.phone_voice_service == "no"
    assert speech.calls == [] and turn.calls == []
    assert "stay on this PC" in fake.texts[-1]
    assert len(files_under(root)) == 1  # the recording is kept


def test_a_local_only_chat_refuses_an_off_network_service(platform, tmp_path, monkeypatch, speech):
    root, fake, poll, turn = _rig(platform, tmp_path, model=None, backend=OPENAI)
    platform.config.phone_voice_service = "yes"
    tid = poll.thread_store.resolve("tg", str(ME), "").id
    with session_scope(platform.engine) as db:
        row = db.get(ChatThreadRecord, tid)
        row.setup_json = json.dumps({"local_only": True})
        db.add(row)
        db.commit()

    seen: list[bool] = []
    from iron_jarvis.daemon.routes import voice as voice_routes

    real = voice_routes.transcribe_clip

    async def spy(d, **kw):
        seen.append(kw.get("local_only"))
        return await real(d, **kw)

    monkeypatch.setattr(voice_routes, "transcribe_clip", spy)
    run(poll)

    assert seen == [True]  # went through the real transcribe_clip, as Local only
    assert speech.calls == []
    assert any("local only" in t.lower() and "OpenAI" in t for t in fake.texts)
    assert turn.calls == []
    assert len(files_under(root)) == 1


SPEECH = ("stt", "https://speech.example.com/v1/audio/transcriptions", None)


def _thread(platform, poll) -> list[dict]:
    tid = poll.thread_store.resolve("tg", str(ME), "").id
    with session_scope(platform.engine) as db:
        return json.loads(db.get(ChatThreadRecord, tid).messages_json or "[]")


def test_a_yes_for_one_service_is_not_a_yes_for_another(platform, tmp_path, monkeypatch, speech):
    # D-10.1: the phone asked "Use OpenAI for phone voice notes too?". When
    # Settings later names another speech service, that yes is not consent
    # for it: the phone asks again, naming it, before anything is sent there.
    root, fake, poll, turn = _rig(platform, tmp_path, model=None, backend=OPENAI)
    run(poll)
    fake.updates = [message(11, text="1")]
    run(poll)
    assert platform.config.phone_voice_service == "yes"
    assert platform.config.phone_voice_service_for == "openai"
    assert len(speech.calls) == 1

    poll.voice_deps.backend = SPEECH  # the dictation service was changed
    fake.updates = [voice(12, "V2")]
    fake.files["V2"] = b"OggS\x00second"
    n = len(fake.texts)
    run(poll)

    assert len(speech.calls) == 1  # nothing went to the new service
    asked = " ".join(fake.texts[n:])
    assert "Use the speech server at speech.example.com for phone voice notes too?" in asked
    fake.updates = [message(13, text="1")]
    run(poll)
    assert [c["url"] for c in speech.calls] == [OPENAI[1], SPEECH[1]]
    assert platform.config.phone_voice_service_for == "stt:speech.example.com"


def test_a_yes_after_the_service_changed_asks_again(platform, tmp_path, monkeypatch, speech):
    # The question named OpenAI; Settings named another service before the
    # "1". The yes was for OpenAI: nothing is sent, nothing is kept, and the
    # phone is asked about the service chosen now.
    root, fake, poll, turn = _rig(platform, tmp_path, model=None, backend=OPENAI)
    run(poll)
    poll.voice_deps.backend = SPEECH
    fake.updates = [message(11, text="1")]
    run(poll)

    assert speech.calls == [] and turn.calls == []
    assert platform.config.phone_voice_service == "ask"
    words = fake.texts[-1]
    assert "changed since I asked" in words
    assert "Use the speech server at speech.example.com for phone voice notes too?" in words
    fake.updates = [message(12, text="1")]
    run(poll)
    assert [c["url"] for c in speech.calls] == [SPEECH[1]]


def test_a_voice_note_not_heard_yet_is_on_the_phone_chat(platform, tmp_path, monkeypatch, speech):
    # Probe P1: the question was asked and the message ended there. The chat
    # on the PC showed nothing: no chip for the recording, no question, so a
    # later "1" and "OK: ..." had nothing above them.
    root, fake, poll, turn = _rig(platform, tmp_path, model=None, backend=OPENAI)
    res = run(poll)

    assert res[0]["status"] == "voice_asked"
    kept = files_under(root)
    lines = _thread(platform, poll)
    assert [m["role"] for m in lines] == ["user", "assistant"]
    assert lines[0]["attachmentNames"] == [kept[0].name]
    assert lines[0]["attachmentPaths"] == [str(kept[0])]
    assert "Use OpenAI for phone voice notes too?" in lines[1]["content"]
    assert res[0]["thread_id"] == poll.thread_store.resolve("tg", str(ME), "").id

    fake.updates = [message(11, text="1")]
    run(poll)
    # The recording is the chat's file from now on: the turn names it.
    assert turn.calls and str(kept[0]) in turn.calls[-1].thread_files


def test_nothing_set_up_is_on_the_phone_chat_too(platform, tmp_path, monkeypatch, speech):
    root, fake, poll, turn = _rig(platform, tmp_path, model=None, backend=None)
    res = run(poll)

    assert res[0]["status"] == "voice_not_heard"
    lines = _thread(platform, poll)
    assert [m["role"] for m in lines] == ["user", "assistant"]
    assert lines[0]["attachmentPaths"] == [str(files_under(root)[0])]
    assert "no speech service is chosen in Settings" in lines[1]["content"]


def test_a_chat_made_local_only_before_the_yes_never_sends_it(platform, tmp_path, monkeypatch, speech):
    # The chat was not Local only when the question was asked, and was made
    # Local only before the "1": the answer path reads the switch again.
    root, fake, poll, turn = _rig(platform, tmp_path, model=None, backend=OPENAI)
    run(poll)
    tid = poll.thread_store.resolve("tg", str(ME), "").id
    with session_scope(platform.engine) as db:
        row = db.get(ChatThreadRecord, tid)
        row.setup_json = json.dumps({**json.loads(row.setup_json or "{}"), "local_only": True})
        db.add(row)
        db.commit()

    fake.updates = [message(11, text="1")]
    run(poll)

    assert speech.calls == []
    assert any("local only" in t.lower() and "OpenAI" in t for t in fake.texts[1:])
    assert turn.calls == []


def test_two_voice_notes_before_the_answer_are_both_heard(platform, tmp_path, monkeypatch, speech):
    # A second voice note sent while the question waits replaces the
    # question, not the first recording: the yes hears both, oldest first.
    root, fake, poll, turn = _rig(platform, tmp_path, model=None, backend=OPENAI)
    run(poll)
    fake.updates = [voice(11, "V2")]
    fake.files["V2"] = b"OggS\x00second"
    run(poll)
    assert "Your 2 voice notes wait on this one answer." in fake.texts[-1]

    fake.updates = [message(12, text="1")]
    run(poll)

    assert len(speech.calls) == 2
    heard = [t for t in fake.texts if "I heard (by OpenAI" in t]
    assert heard and heard[-1].count("I heard (by OpenAI") == 2
    assert turn.calls[-1].messages[-1].content == "buy milk on the way home\n\nbuy milk on the way home"


def test_two_servers_on_one_pc_are_two_names():
    # The question names the service, and a yes is checked against the name
    # it asked about: two speech servers on one PC differ by their port.
    a = VN.service_name("stt", "http://127.0.0.1:9000/v1/audio/transcriptions")
    b = VN.service_name("stt", "http://127.0.0.1:9001/v1/audio/transcriptions")
    assert a == "the speech server at 127.0.0.1:9000" and a != b
    assert VN.service_name("stt", SPEECH[1]) == "the speech server at speech.example.com"
    assert VN.service_key("stt", SPEECH[1]) == "stt:speech.example.com"
    assert VN.service_key("openai", OPENAI[1]) == "openai"


def test_the_chat_server_fallback_is_not_called_a_dictation_choice():
    words = VN.ask_words("the speech server at 127.0.0.1", "custom")
    assert "model server you set up for chat" in words
    assert "you chose for dictation" not in words
    assert "you chose for dictation" in VN.ask_words("OpenAI", "openai")


def test_nothing_set_up_says_so_and_keeps_the_recording(platform, tmp_path, monkeypatch, speech):
    root, fake, poll, turn = _rig(platform, tmp_path, model=None, backend=None)
    run(poll)

    words = " ".join(fake.texts)
    assert "no offline speech model on this PC" in words
    assert "no speech service is chosen in Settings" in words
    assert "The recording is kept in" in words
    assert len(files_under(root)) == 1 and turn.calls == []


def test_a_stalled_worker_is_killed_and_ends_in_words(platform, tmp_path, monkeypatch, speech):
    # A child that answers only after its bound: killed at the bound, so its
    # late words are never used (with no bound they would be: delete the
    # timeout and this goes red).
    late = "import sys, time; time.sleep(20); sys.stdout.write('{\"text\": \"late words\"}')"
    monkeypatch.setattr(VN, "_worker_argv", lambda: [sys.executable, "-c", late])
    monkeypatch.setattr(VN, "WORK_TIMEOUT_S", 1.0)
    monkeypatch.setattr(VN, "WORKER_START_S", 0.5)
    root, fake, poll, turn = _rig(platform, tmp_path, model=str(tmp_path / "vosk"), backend=None)

    run(poll)

    words = " ".join(fake.texts)
    assert "late words" not in words
    assert "could not turn your voice note into words" in words
    assert "The recording is kept in" in words
    assert turn.calls == []


def _wav(path: Path, seconds: float = 1.0, rate: int = 8000) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x01" * int(rate * seconds))
    return path


def test_the_real_worker_decodes_a_clip_in_its_own_process(tmp_path):
    clip = _wav(tmp_path / "one-second.wav")
    out = VN._ask_worker({"op": "decode", "path": str(clip)}, timeout=60)
    assert out is not None, "the audio worker did not answer"
    assert abs(float(out["seconds"]) - 1.0) < 0.05


def test_the_worker_bounds_the_audio_it_reads(tmp_path):
    clip = _wav(tmp_path / "long.wav", seconds=3.0)
    out = VN._ask_worker({"op": "decode", "path": str(clip), "max_audio_s": 1}, timeout=60)
    assert out is not None and abs(float(out["seconds"]) - 1.0) < 0.05


# A stand-in for PyAV in the child: ten frames of 1600 samples (one second at
# 16 kHz), each plane padded past its samples the way a real one may be. Its
# ``to_ndarray`` imports numpy, as PyAV's does.
_FAKE_AV = '''
class _Plane:
    def __init__(self, data):
        self._data = data
    def __bytes__(self):
        return self._data

class _Frame:
    def __init__(self, n):
        self.samples = n
        self.planes = [_Plane(b"\\x01\\x00" * n + b"\\x00" * 64)]
    def to_ndarray(self):
        import numpy
        return numpy.frombuffer(bytes(self.planes[0]), dtype="<i2")

class _Stream:
    type = "audio"

class _Container:
    streams = [_Stream()]
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False
    def decode(self, stream):
        return [object() for _ in range(10)]

def open(path):
    return _Container()

class AudioResampler:
    def __init__(self, **kw):
        pass
    def resample(self, frame):
        return [] if frame is None else [_Frame(1600)]
'''

_PROBE = r"""
import json, sys
from iron_jarvis import audio_worker
out = audio_worker.answer({"op": "decode", "path": sys.argv[1], "max_audio_s": 60})
print(json.dumps({"out": out, "numpy": "numpy" in sys.modules}))
"""


def test_the_decoder_path_reads_frames_without_numpy(tmp_path):
    # numpy comes into the daemon's code through ONE door (core/_numpy, one
    # OpenBLAS thread). PyAV's to_ndarray() would import it behind that door
    # in the child; the frames are read from their plane instead, cut to their
    # own samples (the padding is not sound).
    import os
    import subprocess

    fake = tmp_path / "fakeav" / "av"
    fake.mkdir(parents=True)
    (fake / "__init__.py").write_text(_FAKE_AV, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = os.pathsep.join([str(fake.parent), str(SRC.parent)])
    got = subprocess.run(
        [sys.executable, "-c", _PROBE, str(tmp_path / "note.ogg")],
        capture_output=True, text=True, timeout=120, env=env, cwd=str(tmp_path),
    )
    assert got.returncode == 0, got.stderr[-2000:]
    probe = json.loads(got.stdout.strip().splitlines()[-1])
    assert probe["out"] == {"seconds": 1.0, "decoder": "pyav"}
    assert probe["numpy"] is False


# --------------------------------------------------------------------------- #
# the worker is the only place the native decoder runs
# --------------------------------------------------------------------------- #
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iron_jarvis"


def test_only_the_audio_worker_imports_the_decoder():
    import re

    importers = [
        p.relative_to(SRC).as_posix()
        for p in SRC.rglob("*.py")
        if re.search(r"^\s*(from|import)\s+av\b", p.read_text(encoding="utf-8"), re.M)
    ]
    assert importers == ["audio_worker.py"]


def test_the_frozen_exe_serves_the_audio_worker_without_the_cli(monkeypatch):
    entry = (ROOT / "packaging" / "ironjarvis_entry.py").read_text(encoding="utf-8")
    assert 'AUDIO_WORKER_SUBCOMMAND = "audio-worker"' in entry
    assert "from iron_jarvis.audio_worker import main" in entry
    cli = (SRC / "daemon" / "cli.py").read_text(encoding="utf-8")
    assert '@app.command("audio-worker", hidden=True)' in cli
    assert VN.WORKER_SUBCOMMAND == "audio-worker"
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert VN._worker_argv() == [sys.executable, "audio-worker"]
    spec = (ROOT / "packaging" / "ironjarvis.spec").read_text(encoding="utf-8")
    assert '_collect("av")' in spec


def test_the_bundled_clip_ships_with_the_package():
    from iron_jarvis import audio_worker

    clip = audio_worker.CHECK_CLIP
    assert clip.is_file() and clip.read_bytes()[:4] == b"OggS"
    assert clip.stat().st_size < 10_000


def test_the_doctor_runs_the_worker_and_says_what_is_missing(monkeypatch):
    import importlib

    from iron_jarvis.onboarding.readiness import CHECK_LABELS

    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")

    asked: list[dict] = []
    monkeypatch.setattr(doctor, "_VOICE_CHECK", {})
    monkeypatch.setattr(VN, "_ask_worker", lambda req, timeout: asked.append(req) or
                        {"error": "the audio decoder (PyAV) is missing from this install"})
    row = doctor.check_phone_voice_notes()
    assert asked == [{"op": "check"}]
    assert row["ok"] is False and row["level"] == "recommended"
    assert "can't be decoded" in row["detail"] and "PyAV" in row["detail"]
    assert "uv sync --extra voice" in row["fix"]
    assert CHECK_LABELS["phone_voice_notes"] == "Voice notes from your phone"
    assert doctor.check_phone_voice_notes in doctor.CHECKS

    monkeypatch.setattr(doctor, "_VOICE_CHECK", {})
    monkeypatch.setattr(VN, "_ask_worker", lambda req, timeout: {"seconds": 1.0, "decoder": "pyav"})
    row = doctor.check_phone_voice_notes()
    assert row["ok"] is True and "can be decoded" in row["detail"]


def test_a_check_that_did_not_finish_is_not_kept_as_the_answer(monkeypatch):
    # On a busy PC the worker's start can outlast the check's minute (the
    # frozen exe took 31-35 s to answer while other work ran). That is not
    # "can't be decoded": it says the check did not finish, and the next
    # look asks again instead of keeping the miss for the daemon's life.
    import importlib

    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
    now = [1000.0]
    monkeypatch.setattr(doctor, "_voice_clock", lambda: now[0])
    monkeypatch.setattr(doctor, "_VOICE_CHECK", {})
    monkeypatch.setattr(VN, "_ask_worker", lambda req, timeout: None)
    row = doctor.check_phone_voice_notes()
    assert row["ok"] is False and row["level"] == "recommended"
    assert "can't be decoded" not in row["detail"]
    assert "did not finish" in row["detail"]

    # The doctor runs on every Overview open: for a few minutes the miss is
    # said again without starting (and waiting on) another child.
    asked: list[dict] = []
    monkeypatch.setattr(VN, "_ask_worker", lambda req, timeout: asked.append(req) or
                        {"seconds": 1.0, "decoder": "pyav"})
    now[0] += doctor._VOICE_MISS_KEEP_S - 1
    row = doctor.check_phone_voice_notes()
    assert asked == [] and "did not finish" in row["detail"]

    now[0] += 2
    row = doctor.check_phone_voice_notes()
    assert asked == [{"op": "check"}]
    assert row["ok"] is True and "can be decoded" in row["detail"]
