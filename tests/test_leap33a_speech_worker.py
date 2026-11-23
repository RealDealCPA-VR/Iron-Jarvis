"""Idea 33-a: the speech worker hears a recording with the time of each line.

The REAL child (``python -m iron_jarvis.audio_worker``, op
``transcribe_timed``) through the REAL ``core/procs`` door, in the background
lane, on a generated WAV. CI has no Vosk model, so the recognizer is a
stand-in that hears known words at known times (``tests/_fakes/recordings``);
the decode, the feeding, the lines, the bound and the progress file are the
real code. Then the job's own guards: a worker that stops making progress, or
keeps going past its deadline, is killed as a tree and the job ends in words.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis import audio_worker as W
from iron_jarvis.documents import recording as R
from iron_jarvis.documents import recording_jobs as J
from tests._fakes.recordings import FAKE_VOSK, MEETING, use_fake_vosk, wav

pytestmark = pytest.mark.timeout(240)


def _job(tmp_path: Path) -> J.Job:
    state = {"id": R.new_recording_id(), "source_name": "meeting.wav", "source": "", "status": J.STATUS_HEARING}
    env = J.Env(engine=None, event_bus=None, config=SimpleNamespace(home=tmp_path / "home"))
    return J.Job(state=state, env=env)


def _request(clip: Path, progress: Path, *, max_s: float = R.MAX_AUDIO_S) -> dict:
    return {"op": "transcribe_timed", "path": str(clip), "model": "C:/fake/vosk-model",
            "max_audio_s": max_s, "piece_s": 60, "progress": str(progress)}


def test_the_real_worker_hears_a_recording_with_the_time_of_each_line(tmp_path, monkeypatch):
    use_fake_vosk(monkeypatch, tmp_path, MEETING)
    clip = wav(tmp_path / "meeting.wav", 130.0)
    progress = tmp_path / "progress.json"
    out = J._run_worker(_job(tmp_path), _request(clip, progress), timeout=180)
    assert out is not None and not out.get("error"), out
    got = [(line["start"], line["text"]) for line in out["lines"]]
    # Each line keeps the RECORDING's own time (one recognizer over the whole
    # file: a word at 1:10 is at 1:10, not at 0:10 of its minute).
    assert got == [
        (0.5, "good morning everyone"),
        (70.2, "we decided to move the launch to march"),
        (95.0, "i will send the revised plan on friday"),
        (110.0, "maria will check the numbers"),
    ]
    assert abs(out["seconds"] - 130.0) < 0.1 and abs(out["duration"] - 130.0) < 0.1
    assert out["cut"] is False
    done = json.loads(progress.read_text(encoding="ascii"))
    assert done["done"] is True and abs(done["heard_s"] - 130.0) < 0.2 and abs(done["total_s"] - 130.0) < 0.2


def test_the_worker_reads_no_more_than_its_bound_and_says_it_was_cut(tmp_path, monkeypatch):
    use_fake_vosk(monkeypatch, tmp_path, MEETING)
    clip = wav(tmp_path / "meeting.wav", 130.0)
    out = J._run_worker(_job(tmp_path), _request(clip, tmp_path / "p.json", max_s=60), timeout=180)
    assert out is not None and not out.get("error"), out
    assert [line["text"] for line in out["lines"]] == ["good morning everyone"]
    assert abs(out["seconds"] - 60.0) < 0.1
    assert out["cut"] is True and abs(out["duration"] - 130.0) < 0.1


def _fake_vosk_module(monkeypatch, words) -> None:
    monkeypatch.setenv("IJ_FAKE_VOSK_WORDS", json.dumps([list(w) for w in words]))
    mod = types.ModuleType("vosk")
    exec(compile(FAKE_VOSK, "fake_vosk", "exec"), mod.__dict__)  # noqa: S102 — the test's own stand-in
    monkeypatch.setitem(sys.modules, "vosk", mod)


def test_progress_is_written_after_each_minute_of_audio(tmp_path, monkeypatch):
    # In this process (the same code the child runs): the card's "12 of 40
    # minutes" comes from these writes, one after each piece of PIECE_S.
    _fake_vosk_module(monkeypatch, MEETING)
    clip = wav(tmp_path / "meeting.wav", 130.0, rate=16000)
    seen: list[tuple[float, float, bool]] = []
    monkeypatch.setattr(W, "_write_progress", lambda target, heard, total, done=False: seen.append(
        (round(heard), round(total), done)))
    out = W._transcribe_timed(_request(clip, tmp_path / "p.json"))
    assert [s for s in seen if not s[2]] == [(0, 130, False), (60, 130, False), (120, 130, False)]
    assert seen[-1] == (130, 130, True)
    assert len(out["lines"]) == 4


def test_a_long_wav_is_read_a_block_at_a_time(tmp_path):
    # A two-hour 44.1 kHz stereo WAV is 1.3 GB: it is read WAVE_BLOCK_S at a
    # time, never whole, and comes out as 16 kHz mono.
    clip = wav(tmp_path / "long.wav", 25.0, rate=44100, channels=2)
    chunks = list(W._pcm_wave(str(clip), 600))
    assert len(chunks) == 3, "25 s in blocks of 10 s"
    assert max(len(c) for c in chunks) <= W.WAVE_BLOCK_S * W.RATE * 2
    assert abs(sum(len(c) for c in chunks) / 2 / W.RATE - 25.0) < 0.01


def test_the_pieces_op_cuts_the_recording_into_minute_wavs(tmp_path):
    clip = wav(tmp_path / "meeting.wav", 130.0, rate=16000)
    out_dir = tmp_path / "pieces"
    out_dir.mkdir()
    out = W._pieces({"path": str(clip), "out_dir": str(out_dir), "max_audio_s": R.MAX_AUDIO_S, "piece_s": 60})
    assert [(p["start"], p["seconds"]) for p in out["pieces"]] == [(0.0, 60.0), (60.0, 60.0), (120.0, 10.0)]
    for p in out["pieces"]:
        with wave.open(p["path"], "rb") as w:
            assert (w.getframerate(), w.getnchannels(), w.getsampwidth()) == (16000, 1, 2)
    assert out["cut"] is False


class _Clock:
    def __init__(self, step: float) -> None:
        self.now = 1000.0
        self.step = step

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def _hear(job: J.Job) -> None:
    job.transcriber = R.Transcriber(R.THIS_PC, "the offline speech model on this PC", model="C:/fake")
    asyncio.run(J._run(job))


def test_a_worker_that_stops_making_progress_is_killed_and_the_job_ends_in_words(tmp_path, monkeypatch):
    # A child that never answers and never writes progress: with no stall
    # guard it would run until its 20 s sleep ends (and the job would wait
    # for its hard bound on a real recording).
    hang = "import sys, time; sys.stdin.read(); time.sleep(20)"
    monkeypatch.setattr(J, "_worker_argv", lambda: [sys.executable, "-c", hang])
    monkeypatch.setattr(J, "POLL_S", 0.05)
    monkeypatch.setattr(J, "_clock", _Clock(step=30.0))
    job = _job(tmp_path)
    job.state["source"] = str(wav(tmp_path / "meeting.wav", 2.0))
    started = time.monotonic()
    _hear(job)
    assert time.monotonic() - started < 15, "the stalled worker was not stopped"
    assert job.state["status"] == J.STATUS_FAILED
    assert "stopped making progress" in job.state["error"] and "Nothing was saved" in job.state["error"]
    assert job.proc is None
    assert not (tmp_path / "meeting transcript.md").exists()


def test_a_worker_past_its_deadline_is_killed_even_while_it_reports_progress(tmp_path, monkeypatch):
    # Progress keeps moving, but the recording is 1 s long: the deadline is
    # its length times RTF_CAP, plus the start and the slack.
    busy = (
        "import sys, json, time, os\n"
        "r = json.loads(sys.stdin.read()); p = r['progress']\n"
        "for i in range(400):\n"
        "    open(p + '.t', 'w').write(json.dumps({'heard_s': i * 0.01, 'total_s': 1.0}))\n"
        "    os.replace(p + '.t', p)\n"
        "    time.sleep(0.05)\n"
    )
    monkeypatch.setattr(J, "_worker_argv", lambda: [sys.executable, "-c", busy])
    monkeypatch.setattr(J, "POLL_S", 0.05)
    monkeypatch.setattr(J, "WORKER_START_S", 1.0)
    monkeypatch.setattr(J, "DEADLINE_SLACK_S", 1.0)
    monkeypatch.setattr(J, "_clock", _Clock(step=0.5))
    job = _job(tmp_path)
    job.state["source"] = str(wav(tmp_path / "meeting.wav", 1.0))
    started = time.monotonic()
    _hear(job)
    assert time.monotonic() - started < 15, "the worker ran past its deadline"
    assert job.state["status"] == J.STATUS_FAILED
    assert "took too long" in job.state["error"]


def test_a_worker_that_ends_with_no_answer_is_said_in_words(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_worker_argv", lambda: [sys.executable, "-c", "import sys; sys.exit(3)"])
    job = _job(tmp_path)
    job.state["source"] = str(wav(tmp_path / "meeting.wav", 1.0))
    _hear(job)
    assert job.state["status"] == J.STATUS_FAILED
    assert "ended without an answer" in job.state["error"]


def test_a_decoder_error_is_said_with_the_one_step_that_helps(tmp_path, monkeypatch):
    monkeypatch.setattr(J, "_run_worker", lambda job, req, timeout: {
        "error": "the audio decoder (PyAV) is missing from this install"})
    job = _job(tmp_path)
    job.state["source"] = str(tmp_path / "memo.m4a")
    job.state["source_name"] = "memo.m4a"
    _hear(job)
    assert "uv sync --extra voice" in job.state["error"] and "Nothing was sent anywhere" in job.state["error"]
