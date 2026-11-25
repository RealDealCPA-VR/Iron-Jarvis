"""Idea 33-a: the native speech code never runs in the daemon's process.

* Only the audio worker imports the decoder (PyAV) and, among the recording
  code, the recognizer (Vosk): a malformed recording can only crash the child,
  and a native wait cannot freeze the daemon (the WinRT rule, ``docs/rules/
  documents.md``). The plan's WinRT transcoder is not used: 10-a already ships
  PyAV, which decodes .m4a without ffmpeg, in the same child.
* The job starts it through ``core/procs`` in the BACKGROUND lane (below-normal
  priority), as the frozen exe's ``audio-worker`` fast path.
* A whole recording handed to a chat, heard by the real child, leaves the
  parent process without ``vosk`` or ``av`` loaded.
* The tool is allow tier and REVERSIBLE (new files, one Undo), arms by the
  file's type in both lanes, and stays out of a turn with Auto tools off.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.documents import recording_jobs as J
from tests._fakes.recordings import MEETING, fake_vosk_dir, wav

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iron_jarvis"

pytestmark = pytest.mark.timeout(240)


def _importers(name: str) -> list[str]:
    rx = re.compile(rf"^\s*(from|import)\s+{name}\b", re.M)
    return sorted(p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
                  if rx.search(p.read_text(encoding="utf-8")))


def test_only_the_audio_worker_imports_the_decoder_and_no_recording_code_imports_vosk():
    assert _importers("av") == ["audio_worker.py"]
    vosk = _importers("vosk")
    assert "audio_worker.py" in vosk
    assert not [p for p in vosk if p.startswith("documents/")], vosk


def test_the_job_starts_the_worker_through_procs_in_the_background_lane(tmp_path, monkeypatch):
    from iron_jarvis.comm import voice_note as VN
    from iron_jarvis.core import procs

    seen: list[dict] = []

    def fake_run(argv, *, timeout, input=None, on_spawn=None, **kw):
        seen.append({"argv": list(argv), "background": procs.in_background_lane(), "timeout": timeout})
        return SimpleNamespace(timed_out=False, returncode=0, stdout=b'{"lines": [], "seconds": 1}')

    monkeypatch.setattr(procs, "run", fake_run)
    job = J.Job(state={"id": "rec_0123456789abcdef"}, env=None)
    out = J._run_worker(job, {"op": "transcribe_timed"}, timeout=10)
    assert out == {"lines": [], "seconds": 1}
    assert seen and seen[0]["background"] is True
    assert seen[0]["argv"] == VN._worker_argv()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert J._worker_argv() == [sys.executable, "audio-worker"]


class _Workers:
    """A ``procs.run`` whose workers run until the test releases them; counts
    how many run at once."""

    def __init__(self) -> None:
        import threading

        self.release = threading.Event()
        self.lock = threading.Lock()
        self.running: list[str] = []
        self.started: list[str] = []
        self.most = 0

    def run(self, argv, *, timeout, input=None, on_spawn=None, **kw):
        with self.lock:
            self.running.append(input.decode("ascii"))
            self.started.append(input.decode("ascii"))
            self.most = max(self.most, len(self.running))
        assert self.release.wait(60), "the test never released the workers"
        with self.lock:
            self.running.pop()
        return SimpleNamespace(timed_out=False, returncode=0, stdout=b'{"lines": []}')


async def _until(cond, what: str, limit: float = 30.0) -> None:
    """Yield to the loop until ``cond()`` (never a sleep the test measures)."""
    import asyncio
    import time

    end = time.monotonic() + limit
    while not cond():
        assert time.monotonic() < end, f"never: {what}"
        await asyncio.sleep(0.01)


def test_at_most_two_recordings_are_worked_on_at_once_and_a_stopped_one_never_starts(monkeypatch):
    """Light on the PC: each worker holds the speech model in its own memory,
    so three recordings handed at once start two workers; the third waits its
    turn (and its stall clock waits with it), one stopped while it waits
    starts nothing, and the places go in the order the jobs asked."""
    import asyncio

    from iron_jarvis.core import procs

    w = _Workers()
    monkeypatch.setattr(procs, "run", w.run)
    monkeypatch.setattr(J, "WAIT_POLL_S", 0.05)
    jobs = [J.Job(state={"id": f"rec_{i:016x}"}, env=None) for i in range(5)]

    async def _go():
        tasks = [asyncio.ensure_future(J._work(jobs[i], {"n": i}, 10)) for i in range(3)]
        try:
            await _until(lambda: len(w.running) == 2 and jobs[2].waiting, "two running, the third waiting")
            # A fourth asks, then is stopped (its Undo) while it waits: it starts nothing.
            fourth = asyncio.ensure_future(J._work(jobs[3], {"n": 3}, 10))
            fifth = asyncio.ensure_future(J._work(jobs[4], {"n": 4}, 10))
            await _until(lambda: jobs[3].waiting and jobs[4].waiting, "the fourth and fifth waiting")
            jobs[3].stopped = "undo"
            assert await asyncio.wait_for(fourth, 10) is None
            assert not jobs[3].waiting
        finally:
            w.release.set()
        outs = await asyncio.wait_for(asyncio.gather(*tasks, fifth), 30)
        # One more, cancelled while it waits (the daemon stopping), behind two
        # held places: its place-to-be goes back too.
        held = [await J._take_place(J.Job(state={"id": f"rec_{9 + i:016x}"}, env=None)) for i in range(2)]
        late = asyncio.ensure_future(J._work(J.Job(state={"id": "rec_00000000000000aa"}, env=None), {"n": 9}, 10))
        await _until(lambda: not late.done() and len(J._places()._waiters or ()) == 1, "the late one waiting")
        late.cancel()
        await asyncio.gather(late, return_exceptions=True)
        for places in held:
            places.release()
        assert J._places()._value == J.MAX_WORKERS, "every place came back"
        return outs

    outs = asyncio.run(_go())
    assert w.most == 2, f"{w.most} workers ran at once"
    assert outs == [{"lines": []}] * 4, outs
    assert '{"n": 3}' not in w.started, "the stopped one started a worker"
    assert not any(j.waiting for j in jobs)


def test_recordings_waiting_for_a_worker_hold_no_thread_of_the_shared_pool(tmp_path, monkeypatch):
    """A recording waiting for a place waits ON THE LOOP: more recordings than
    the daemon's shared thread pool holds (``asyncio.to_thread``) leave that
    pool free for everything else (saves, reads, other routes)."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from iron_jarvis.core import procs
    from iron_jarvis.documents import recording as R

    w = _Workers()
    monkeypatch.setattr(procs, "run", w.run)
    monkeypatch.setattr(J, "WAIT_POLL_S", 0.05)
    pool = 4
    env = J.Env(engine=None, event_bus=None, config=SimpleNamespace(home=tmp_path / "home"))
    tr = R.Transcriber(R.THIS_PC, "the offline speech model on this PC", model="C:/fake")
    jobs = [J.Job(state={"id": f"rec_{i:016x}", "source": str(tmp_path / f"m{i}.m4a"),
                         "source_name": f"m{i}.m4a"}, env=env, transcriber=tr) for i in range(pool + 2)]

    async def _go():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=pool))
        # Started the way a job starts them (``_hear`` → ``_hear_here``).
        tasks = [asyncio.ensure_future(J._hear_here(j, tr)) for j in jobs]

        async def unrelated() -> str:
            try:
                return await asyncio.wait_for(asyncio.to_thread(lambda: "answered"), 5)
            except asyncio.TimeoutError:
                return "not answered within 5 s: the pool is full"

        try:
            await _until(lambda: len(w.running) == 2, "two workers running")
            first = await unrelated()
            assert first == "answered", "an unrelated to_thread call waited behind recordings waiting for a worker"
            await _until(lambda: sum(j.waiting for j in jobs) == len(jobs) - 2, "the rest waiting on the loop")
            again = await unrelated()
            assert again == "answered", "with every other recording waiting, the pool is still free"
        finally:
            w.release.set()
        return await asyncio.wait_for(asyncio.gather(*tasks), 60)

    outs = asyncio.run(_go())
    assert w.most == 2 and len(w.started) == len(jobs)
    assert all(o is not None for o in outs), outs


_PROBE = r"""
import asyncio, json, sys
from pathlib import Path
from types import SimpleNamespace
from iron_jarvis.documents import recording as R
from iron_jarvis.documents import recording_jobs as J

ws = Path(sys.argv[1])
clip = ws / "meeting.wav"
home = ws / "home"
t, n = R.target_paths(ws, clip.name)
tr = R.Transcriber(R.THIS_PC, "the offline speech model on this PC", model="C:/fake")
state = J.new_state(source=clip, sha256="x", workspace=ws, thread_id="", turn_id="t", local_only=True,
                    transcriber=tr, transcript=t, notes=n)
job = J.Job(state=state, env=J.Env(engine=None, event_bus=None, config=SimpleNamespace(home=home)),
            transcriber=tr)
asyncio.run(J._run(job))
print(json.dumps({"status": job.state["status"], "lines": job.state.get("lines"),
                  "vosk": "vosk" in sys.modules, "av": "av" in sys.modules}))
"""


def test_a_recording_is_heard_in_a_child_and_the_parent_never_loads_the_native_code(tmp_path):
    import json

    wav(tmp_path / "meeting.wav", 130.0)
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PYTHONPATH"] = os.pathsep.join([str(fake_vosk_dir(tmp_path)), str(SRC.parent)])
    env["IJ_FAKE_VOSK_WORDS"] = json.dumps([list(w) for w in MEETING])
    got = subprocess.run([sys.executable, "-c", _PROBE, str(tmp_path)], capture_output=True, text=True,
                         timeout=200, env=env, cwd=str(tmp_path))
    assert got.returncode == 0, got.stderr[-3000:]
    probe = json.loads(got.stdout.strip().splitlines()[-1])
    assert probe["status"] == J.STATUS_DONE and probe["lines"] == 4
    assert probe["vosk"] is False and probe["av"] is False
    assert (tmp_path / "meeting transcript.md").is_file()


def test_the_tool_is_allow_tier_reversible_and_untrusted():
    from iron_jarvis.core.config import default_permissions
    from iron_jarvis.documents.recording_tools import TranscribeRecordingTool
    from iron_jarvis.tools.base import Reversibility

    tool = TranscribeRecordingTool()
    assert default_permissions()["transcribe_recording"] == "allow"
    assert tool.reversibility is Reversibility.REVERSIBLE and tool.returns_untrusted_content is True


def _d(tmp_path):
    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path / "home"))
    return app.state.d


def _body(**kw):
    from iron_jarvis.daemon.schemas import ChatBody

    base = {"messages": [{"role": "user", "content": "here you go"}], "auto_tools": True}
    base.update(kw)
    return ChatBody(**base)


def test_a_recording_arms_the_tool_by_its_type_outside_the_cap(tmp_path, monkeypatch):
    from iron_jarvis.daemon.chat_turn import _resolve_armed_tools, recording_turn

    d = _d(tmp_path)
    memo = str(tmp_path / "memo.m4a")
    assert recording_turn(d, _body(attachments=[memo])) is True
    assert recording_turn(d, _body(attachments=[str(tmp_path / "call.mp4")])) is True, "a video's sound"
    assert recording_turn(d, _body(attachments=[str(tmp_path / "letter.pdf")])) is False
    assert recording_turn(d, _body(attachments=[memo], auto_tools=False)) is False
    six = ["read_document", "write_document", "list_files", "calculate", "web_search", "file_search"]
    sel = _resolve_armed_tools(d, _body(attachments=[memo], tools=six))
    assert "transcribe_recording" in list(sel[0]) + list(sel[1])
    monkeypatch.setitem(d.platform.permissions._base, "transcribe_recording", "deny")  # "Never run"
    assert recording_turn(d, _body(attachments=[memo])) is False, "Never run stays never"


def test_the_words_about_a_recording_name_no_tool_id():
    """G8: the model repeats these to the user, so they use the screen's words
    (the Auto tools switch), never the tool's id."""
    from iron_jarvis.daemon.chat_turn import RECORDING_NOT_ARMED_NOTE

    assert "transcribe_recording" not in RECORDING_NOT_ARMED_NOTE
    assert "Auto tools" in RECORDING_NOT_ARMED_NOTE
    assert "+ menu" not in RECORDING_NOT_ARMED_NOTE, "the + menu picks no tool"


def test_finished_jobs_are_let_go_unless_a_new_chat_may_still_bring_them_home(monkeypatch):
    """Nothing piles up in memory: a finished job is read from its state on
    disk, and only one a new chat's link may still report (detached, no chat
    yet) is kept, at most KEEP_FINISHED of them."""
    import asyncio

    monkeypatch.setattr(J, "_LIVE", {})
    monkeypatch.setattr(J, "KEEP_FINISHED", 2)

    async def _go():
        async def nothing():
            return None

        def job(i: int, *, detached: bool, thread_id: str = "", homed: bool = False) -> J.Job:
            j = J.Job(state={"id": f"rec_{i:016x}", "thread_id": thread_id, "homed": homed}, env=None)
            j.detached = detached
            j.task = asyncio.get_running_loop().create_task(nothing())
            await_list.append(j.task)
            return j

        await_list: list = []
        jobs = [job(0, detached=False), job(1, detached=True, thread_id="thr"), job(2, detached=True),
                job(3, detached=True), job(4, detached=True), job(5, detached=True, homed=True)]
        await asyncio.gather(*await_list)
        for j in jobs:
            J._LIVE[j.id] = j
        running = J.Job(state={"id": "rec_00000000000000ff"}, env=None)
        running.task = asyncio.get_running_loop().create_future()
        J._LIVE[running.id] = running
        new = J.Job(state={"id": "rec_00000000000000fe"}, env=None)
        J._track(new)
        running.task.cancel()
        return sorted(J._LIVE)

    kept = asyncio.run(_go())
    assert kept == ["rec_0000000000000003", "rec_0000000000000004", "rec_00000000000000fe",
                    "rec_00000000000000ff"], kept
