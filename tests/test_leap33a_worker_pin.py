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
* A Stop of the reply that waits for a recording kills its worker and ends
  its record in words; a reply cut off with no Stop lets it carry on, and it
  comes home once; the wait ends before the call's own deadline.
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
    turn, one stopped while it waits starts nothing, and the places go in the
    order the jobs asked. (Its stall and deadline clocks waiting with it:
    :func:`test_a_recording_waiting_for_a_worker_is_never_stopped_as_a_stall`.)"""
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


class _HeldWorkers:
    """A ``procs.run`` whose worker for each recording runs until the test
    lets THAT one go (``free(rid)``)."""

    def __init__(self) -> None:
        import threading

        self.lock = threading.Lock()
        self.gates: dict[str, "threading.Event"] = {}
        self.running: set[str] = set()
        self.started: list[str] = []

    def gate(self, rid: str):
        import threading

        with self.lock:
            return self.gates.setdefault(rid, threading.Event())

    def free(self, rid: str) -> None:
        self.gate(rid).set()

    def run(self, argv, *, timeout, input=None, on_spawn=None, **kw):
        import json

        rid = Path(json.loads(input.decode("ascii"))["progress"]).name.split(".")[0]
        with self.lock:
            self.running.add(rid)
            self.started.append(rid)
        try:
            assert self.gate(rid).wait(60), "the test never let the worker go"
        finally:
            with self.lock:
                self.running.discard(rid)
        return SimpleNamespace(timed_out=False, returncode=0, stdout=b'{"lines": [], "seconds": 1, "duration": 1}')


def test_a_recording_waiting_for_a_worker_is_never_stopped_as_a_stall(tmp_path, monkeypatch):
    """A recording handed in while two are being heard waits its turn, and
    its stall and deadline clocks wait with it: however long the two take, it
    is not stopped as "stopped making progress" (nor "took too long") before
    its own worker has started, and once it starts both clocks count from
    then. The two being heard, whose progress moves, are not stopped either."""
    import asyncio
    import threading

    from iron_jarvis.core import procs
    from iron_jarvis.documents import recording as R

    w = _HeldWorkers()
    monkeypatch.setattr(procs, "run", w.run)
    monkeypatch.setattr(J, "WAIT_POLL_S", 0.01)
    monkeypatch.setattr(J, "POLL_S", 0.01)
    now = [1000.0]
    reads: dict = {}

    def clock() -> float:
        task = asyncio.current_task()
        reads[task] = reads.get(task, 0) + 1
        return now[0]

    monkeypatch.setattr(J, "_clock", clock)
    env = J.Env(engine=None, event_bus=None, config=SimpleNamespace(home=tmp_path / "home"))
    tr = R.Transcriber(R.THIS_PC, "the offline speech model on this PC", model="C:/fake")
    jobs = [J.Job(state={"id": f"rec_{i:016x}", "source": str(tmp_path / f"m{i}.m4a"),
                         "source_name": f"m{i}.m4a"}, env=env, transcriber=tr) for i in range(3)]
    third = jobs[2]
    # The progress each worker reports: the two being heard move on every
    # look; the third's worker, once it runs, is still loading its model (no
    # progress yet), as a real one does for up to WORKER_START_S.
    looks: dict[str, int] = {}
    looks_lock = threading.Lock()

    def progress(home, rid):
        with looks_lock:
            looks[rid] = looks.get(rid, 0) + 1
            if rid not in w.running or rid == third.id:
                return None
            return float(looks[rid]), 4.0 * R.MAX_AUDIO_S

    monkeypatch.setattr(J, "read_progress", progress)

    async def _go():
        tasks = [asyncio.ensure_future(J._hear_here(j, tr)) for j in jobs[:2]]
        try:
            await _until(lambda: len(w.running) == 2, "two workers running")
            tasks.append(asyncio.ensure_future(J._hear_here(third, tr)))
            t3 = tasks[2]
            await _until(lambda: third.waiting and reads.get(t3, 0) >= 3, "the third waiting, its watch looking")
            # The two take a long time: past the stall (no progress for
            # STALL_S after a start of WORKER_START_S) AND past the deadline
            # of a recording whose length is not known yet.
            now[0] += J.WORKER_START_S + R.MAX_AUDIO_S * J.RTF_CAP + J.DEADLINE_SLACK_S + 60.0
            seen = reads[t3]
            # A clock read after the step is checked before the next read;
            # a look in flight at the step is the first one counted, so the
            # third look is the one after a whole look on the new time.
            with looks_lock:
                looked = {j.id: looks.get(j.id, 0) for j in jobs[:2]}
            # Ends at once when the third is stopped, so the assert below names why.
            await _until(lambda: third.stopped or (
                reads[t3] >= seen + 2 and all(looks[k] >= n + 3 for k, n in looked.items())),
                "every watch looked after the long wait")
            assert third.waiting and third.stopped == "", f"stopped while waiting: {third.stopped!r}"
            assert jobs[0].stopped == "" and jobs[1].stopped == "", "the two being heard moved on"
            # A place frees: the third's own worker starts, and its clocks
            # count from then (its model is still loading: no progress yet).
            w.free(jobs[0].id)
            await _until(lambda: third.id in w.running and looks.get(third.id, 0) >= 2,
                         "the third's worker running, its watch looking")
            assert third.stopped == "", f"stopped as soon as its worker started: {third.stopped!r}"
        finally:
            for j in jobs:
                w.free(j.id)
        return await asyncio.wait_for(asyncio.gather(*tasks), 60)

    outs = asyncio.run(_go())
    assert all(o is not None for o in outs), [j.state.get("error") for j in jobs]
    assert third.stopped == "" and "error" not in third.state, third.state
    assert sorted(w.started[:2]) == [jobs[0].id, jobs[1].id] and w.started[2:] == [third.id], w.started


def test_a_place_handed_to_a_job_stopped_in_the_same_wait_goes_back(monkeypatch):
    """A job whose Undo lands while it waits, in the same wait in which a
    running worker ends and its place is handed to it, starts nothing and
    gives that place back: never a place lost for good (two such Undos would
    leave every later recording waiting until a restart)."""
    import asyncio

    from iron_jarvis.core import procs

    w = _Workers()
    monkeypatch.setattr(procs, "run", w.run)
    # One long wait: the stop is seen only when the place arrives in it.
    monkeypatch.setattr(J, "WAIT_POLL_S", 60.0)
    job = J.Job(state={"id": "rec_00000000000000bb"}, env=None)

    async def _go():
        held = [await J._take_place(J.Job(state={"id": f"rec_{20 + i:016x}"}, env=None)) for i in range(2)]
        task = asyncio.ensure_future(J._work(job, {"n": 7}, 10))
        await _until(lambda: job.waiting and len(J._places()._waiters or ()) == 1, "the job waiting for a place")
        job.stopped = "undo"
        held[0].release()  # a running worker ends: its place is handed to the waiting job
        out = await asyncio.wait_for(task, 10)
        held[1].release()
        return out, J._places()._value

    out, free = asyncio.run(_go())
    assert out is None, "a stopped job starts no worker"
    assert w.started == [], w.started
    assert free == J.MAX_WORKERS, f"{free} of {J.MAX_WORKERS} places came back"


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


# --------------------------------------------------------------------------- #
# the reply waiting for a recording is stopped, or cut off some other way
# --------------------------------------------------------------------------- #
#: What the worker hears (the real ``_hear_here`` reads it).
_HEARD = {
    "lines": [{"start": 0.5, "text": "good morning everyone"},
              {"start": 70.2, "text": "we decided to move the launch to march"},
              {"start": 95.0, "text": "i will send the revised plan on friday"},
              {"start": 110.0, "text": "maria will check the numbers"}],
    "seconds": 130.0, "duration": 130.0, "cut": False,
}


class _KillableWorker:
    """A ``procs.run`` whose worker hears until the test lets it end
    (``free``) or the job kills its tree (``procs.kill_tree``)."""

    def __init__(self) -> None:
        import threading

        self.proc = SimpleNamespace(pid=4242)
        self.spawned = threading.Event()
        self.end = threading.Event()
        self.killed = False
        #: A worker that does not end when its tree is killed (wedged).
        self.deaf = False

    def run(self, argv, *, timeout, input=None, on_spawn=None, **kw):
        import json

        if on_spawn is not None and on_spawn(self.proc):
            self.kill_tree(self.proc)
        self.spawned.set()
        assert self.end.wait(60), "the worker was never let go nor killed"
        if self.killed:
            return SimpleNamespace(timed_out=False, returncode=1, stdout=b"")
        return SimpleNamespace(timed_out=False, returncode=0, stdout=json.dumps(_HEARD).encode("ascii"))

    def kill_tree(self, proc) -> None:
        if proc is self.proc:
            self.killed = True
            if not self.deaf:
                self.end.set()

    def free(self) -> None:
        self.end.set()


class _HeldNotes:
    """The notes model: answers only when the test lets it (``hold``)."""

    def __init__(self) -> None:
        from tests._fakes.recordings import NotesModel

        self.model = NotesModel()
        self.hold = False
        self.asked = None
        self.answer = None

    async def complete(self, **kw):
        self.asked.set()
        if self.hold:
            await self.answer.wait()
        return await self.model.complete(**kw)


class _StopRig:
    """The real tool and the real hearing (``_hear_here``, ``_work``), the
    worker a stand-in; the turn ``turn-stop`` of the chat ``thr_a``."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        from iron_jarvis.agents import chat_delivery
        from iron_jarvis.core import procs
        from iron_jarvis.documents import recording as R
        from iron_jarvis.documents import recording_tools as RT
        from tests._fakes.recordings import LOCAL_CHOICE

        self.home, self.ws = tmp_path / "home", tmp_path / "work"
        self.ws.mkdir(parents=True)
        self.clip = self.ws / "meeting.m4a"
        self.clip.write_bytes(b"the bytes of a recording")
        self.worker = _KillableWorker()
        self.notes = _HeldNotes()
        self.sent: list = []
        monkeypatch.setattr(chat_delivery, "report_to_chat",
                            lambda platform, tid, report: self.sent.append((tid, report)) or {"ok": True})
        monkeypatch.setattr(procs, "run", self.worker.run)
        monkeypatch.setattr(procs, "kill_tree", self.worker.kill_tree)
        monkeypatch.setattr(RT, "_answers_the_phone", lambda: False)
        monkeypatch.setattr(J, "_answers_the_phone", lambda: False)
        monkeypatch.setattr(J, "_LIVE", {})
        monkeypatch.setattr(J, "POLL_S", 0.01)
        monkeypatch.setattr(J, "INLINE_WAIT_S", 120.0)  # the reply is still waiting when it is stopped
        monkeypatch.setattr(J, "notes_choice", lambda r, c, local_only, chat=None: dict(LOCAL_CHOICE))
        tr = R.Transcriber(R.THIS_PC, "the offline speech model on this PC", model="C:/fake")

        async def choose(env, *, local_only):
            return tr

        monkeypatch.setattr(J, "choose_now", choose)
        router = SimpleNamespace(default_provider="ollama", manager=None, complete=self.notes.complete)
        self.tool = RT.TranscribeRecordingTool(router_resolver=lambda: router)

    def call(self):
        import asyncio

        from iron_jarvis.tools.base import ToolContext

        self.notes.asked, self.notes.answer = asyncio.Event(), asyncio.Event()
        ctx = ToolContext(workspace=self.ws, session_id="chat", agent_run_id="chat",
                          config=SimpleNamespace(home=self.home), event_bus=None, engine=None,
                          thread_id="thr_a", turn_id="turn-stop")
        return asyncio.ensure_future(self.tool.execute({"path": str(self.clip)}, ctx))

    async def cut_off(self, call, *, stop: bool) -> "J.Job":
        """The reply is stopped (``stop``: the user pressed Stop, which marks
        the turn and cancels it, as ``chat_turn``'s runner does) or cut off
        with no Stop (the page went away): the call is cancelled."""
        import asyncio

        from iron_jarvis.core.turns import TURNS

        job = next(iter(J._LIVE.values()))
        if stop:
            assert TURNS.stop("turn-stop", by="you")
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        return job

    def files(self) -> list[str]:
        return sorted(p.name for p in self.ws.iterdir())


def _stop_run(rig: _StopRig, body):
    import asyncio

    from iron_jarvis.core.turns import TURNS

    async def _go():
        handle = TURNS.register("turn-stop")
        try:
            return await body()
        finally:
            TURNS.release("turn-stop", handle)
            rig.worker.free()
            if rig.notes.answer is not None:
                rig.notes.answer.set()

    return asyncio.run(_go())


def test_a_stop_of_the_reply_stops_the_recording_and_kills_its_worker(tmp_path, monkeypatch):
    """Stop pressed while the reply waits for a recording being heard: the
    recording stops with it. Its worker's tree is killed, nothing is written,
    its record ends in words (never still working: the next boot does not
    start it again), and nothing more comes to the chat (the Stop is the
    answer)."""
    import asyncio

    rig = _StopRig(tmp_path, monkeypatch)

    async def body():
        call = rig.call()
        await _until(lambda: rig.worker.spawned.is_set() and J._LIVE, "the worker hearing")
        job = await rig.cut_off(call, stop=True)
        await _until(lambda: rig.worker.killed, "the worker killed by the Stop", limit=10)
        await asyncio.wait_for(job.task, 30)
        await _until(lambda: not J._BACKGROUND, "the stop settled")
        return job

    job = _stop_run(rig, body)
    assert job.stopped == J.STOP_REPLY
    disk = J.load_state(rig.home, job.id)
    assert disk["status"] == J.STATUS_FAILED, disk["status"]
    assert "stopped with the reply" in disk["error"] and "Nothing was saved" in disk["error"]
    assert job.id not in [s["id"] for s in J._interrupted(rig.home)]
    assert rig.files() == ["meeting.m4a"]
    assert rig.sent == [], "nothing comes to the chat after the user's own Stop"


def test_a_stopped_recording_that_does_not_end_is_cancelled_and_still_ends_in_words(tmp_path, monkeypatch):
    """The worker does not end when its tree is killed (wedged): the stop
    cancels the job after STOP_WAIT_S, and its record still ends in words,
    never "hearing" for the next boot to start again."""
    rig = _StopRig(tmp_path, monkeypatch)
    rig.worker.deaf = True
    monkeypatch.setattr(J, "STOP_WAIT_S", 0.05)

    async def body():
        call = rig.call()
        await _until(lambda: rig.worker.spawned.is_set() and J._LIVE, "the worker hearing")
        job = await rig.cut_off(call, stop=True)
        await _until(lambda: job.done.is_set() and not J._BACKGROUND, "the stop settled", limit=10)
        return job

    job = _stop_run(rig, body)
    assert rig.worker.killed
    disk = J.load_state(rig.home, job.id)
    assert disk["status"] == J.STATUS_FAILED, disk["status"]
    assert "stopped with the reply" in disk["error"]
    assert job.id not in [s["id"] for s in J._interrupted(rig.home)]
    assert rig.files() == ["meeting.m4a"] and rig.sent == []


def test_a_stop_while_the_notes_are_written_keeps_the_transcript_and_says_so_once(tmp_path, monkeypatch):
    """Stop pressed while the notes are being written (the model slow to
    answer): the notes are stopped, the transcript already written stays, and
    the chat is told so in ONE report; the record is not left "notes" for the
    next boot to start again."""
    import asyncio

    rig = _StopRig(tmp_path, monkeypatch)
    rig.notes.hold = True
    monkeypatch.setattr(J, "STOP_WAIT_S", 0.05)

    async def body():
        call = rig.call()
        await _until(lambda: rig.worker.spawned.is_set(), "the worker hearing")
        rig.worker.free()
        await asyncio.wait_for(rig.notes.asked.wait(), 30)
        job = await rig.cut_off(call, stop=True)
        await _until(lambda: job.done.is_set(), "the notes stopped", limit=10)
        await _until(lambda: not J._BACKGROUND, "the stop settled")
        return job

    job = _stop_run(rig, body)
    assert rig.files() == ["meeting transcript.md", "meeting.m4a"]
    disk = J.load_state(rig.home, job.id)
    assert disk["status"] == J.STATUS_DONE and disk["transcript_written"] is True and not disk["notes_written"]
    assert disk["notes_why"] == "The notes were not written: the reply was stopped. The transcript is saved."
    assert job.id not in [s["id"] for s in J._interrupted(rig.home)]
    assert len(rig.sent) == 1 and rig.sent[0][0] == "thr_a", rig.sent
    words = rig.sent[0][1]["words"]
    assert words.startswith("The transcript is ready") and "the reply was stopped" in words


def test_a_stop_while_the_notes_model_is_chosen_asks_nothing(tmp_path, monkeypatch):
    """Stop pressed while the job looks who would write the notes (a model in
    the cloud, with no yes in this chat yet): it does not go on to wait for
    the card's yes (no card shows: the reply was stopped); it ends with the
    transcript saved, and says so once."""
    import asyncio

    from tests._fakes.recordings import CLOUD_CHOICE

    rig = _StopRig(tmp_path, monkeypatch)
    gate: dict = {}

    async def outlook(env, state):
        gate["looking"].set()
        await gate["go"].wait()
        return dict(CLOUD_CHOICE), False, False

    monkeypatch.setattr(J, "notes_outlook", outlook)

    async def body():
        gate["looking"], gate["go"] = asyncio.Event(), asyncio.Event()
        call = rig.call()
        await _until(lambda: rig.worker.spawned.is_set(), "the worker hearing")
        rig.worker.free()
        await asyncio.wait_for(gate["looking"].wait(), 30)
        job = await rig.cut_off(call, stop=True)
        gate["go"].set()
        await asyncio.wait_for(job.task, 30)
        await _until(lambda: not J._BACKGROUND, "the stop settled")
        return job

    job = _stop_run(rig, body)
    disk = J.load_state(rig.home, job.id)
    assert disk["status"] == J.STATUS_DONE and not disk.get("offer"), (disk["status"], disk.get("offer"))
    assert disk["notes_why"] == "The notes were not written: the reply was stopped. The transcript is saved."
    assert rig.notes.model.calls == [], "nothing was sent for the notes"
    assert len(rig.sent) == 1 and "card above" not in rig.sent[0][1]["words"], rig.sent


def test_a_reply_cut_off_with_no_stop_lets_the_recording_carry_on_and_come_home_once(tmp_path, monkeypatch):
    """The page went away (no Stop pressed) while the reply waited: no reply
    carries the recording now, so it carries on and its end comes home to the
    chat as ONE report, never silently."""
    import asyncio

    rig = _StopRig(tmp_path, monkeypatch)

    async def body():
        call = rig.call()
        await _until(lambda: rig.worker.spawned.is_set() and J._LIVE, "the worker hearing")
        job = await rig.cut_off(call, stop=False)
        assert job.detached and not rig.worker.killed and not job.stopped
        rig.worker.free()
        await asyncio.wait_for(job.task, 30)
        return job

    job = _stop_run(rig, body)
    assert job.state["status"] == J.STATUS_DONE and job.state["notes_written"] is True
    assert rig.files() == ["meeting notes.md", "meeting transcript.md", "meeting.m4a"]
    assert len(rig.sent) == 1 and rig.sent[0][0] == "thr_a", rig.sent
    assert rig.sent[0][1]["words"].startswith("The transcript is ready")


def test_the_reply_waits_only_until_just_before_the_calls_deadline_which_is_a_stop(tmp_path):
    """The wait for a recording ends DEADLINE_MARGIN_S before the call's own
    deadline (the phone's 8 minutes too), so the deadline does not cut a call
    that waits; if it does cut one, its record says the call was stopped, so
    the recording is stopped with it."""
    import asyncio

    from iron_jarvis.documents import recording_tools as RT
    from iron_jarvis.tools import registry
    from iron_jarvis.tools.base import ToolContext

    ctx = ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat",
                      config=SimpleNamespace(home=tmp_path), event_bus=None, engine=None, turn_id="")

    async def _go():
        async with asyncio.timeout(100.0) as cm:
            token = registry._DEADLINE_SCOPE.set(cm)
            try:
                waits = RT._wait_s(phone=True), RT._wait_s(phone=False)
            finally:
                registry._DEADLINE_SCOPE.reset(token)
        pressed = None
        try:
            async with asyncio.timeout(0.01) as cm:
                token = registry._DEADLINE_SCOPE.set(cm)
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    pressed = RT._stop_pressed(ctx)
                    raise
                finally:
                    registry._DEADLINE_SCOPE.reset(token)
        except TimeoutError:
            pass
        return waits, pressed, RT._wait_s(phone=True), RT._stop_pressed(ctx)

    (phone, here), pressed, unbounded, no_cut = asyncio.run(_go())
    assert 100.0 - RT.DEADLINE_MARGIN_S - 5 < phone <= 100.0 - RT.DEADLINE_MARGIN_S, phone
    assert here == J.INLINE_WAIT_S
    assert pressed is True, "the call's own deadline is a stop"
    assert unbounded == RT.PHONE_WAIT_S and no_cut is False
