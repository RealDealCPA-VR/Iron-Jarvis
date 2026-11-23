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
