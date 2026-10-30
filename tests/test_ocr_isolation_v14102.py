"""v1.410.2 — a stalled Windows OCR call can never freeze the daemon again.

THE USER BUG (2026-10-30, after an update): "Iron Jarvis isn't responding" on
every start. Each freeze trace (logs/daemon-freeze.txt) showed a Build
terminal's restore parked in Windows' CreateProcessW (the Store PowerShell)
and the OCR self-test that ``GET /onboarding`` runs parked at its first WinRT
object — and a WinRT call holds Python's GIL while it waits, so EVERY thread
stopped: the event loop, ``/health``, the freeze watcher. The desktop's
watchdog restarted the daemon and it froze again.

The fix: no WinRT in the daemon process. ``iron_jarvis/ocr_worker.py`` is the
one module that touches it, run as a short-lived child that is killed at its
deadline. These pins:
* only ``ocr_worker.py`` imports ``winrt``, and only the worker's own entry
  points import ``ocr_worker``;
* after a real self-test the daemon process has never loaded ``winrt``;
* a worker that holds ITS OWN GIL forever (exactly the stalled call) does
  not stop this process: a heartbeat keeps ticking, the self-test answers
  "did not answer" within its bound, and that answer is not cached;
* the frozen exe's fast path serves ``ocr-worker`` without the CLI import;
* chat asks ``available()`` off the event loop.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from iron_jarvis.documents import local_ocr

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iron_jarvis"


def test_only_the_worker_module_touches_winrt():
    offenders = []
    for p in SRC.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if re.search(r"^\s*(from|import)\s+winrt\b", text, re.M) and p.name != "ocr_worker.py":
            offenders.append(str(p.relative_to(ROOT)))
    assert offenders == [], f"WinRT outside the OCR worker process: {offenders}"


def test_only_the_workers_own_entry_points_import_it():
    importers = []
    for p in list(SRC.rglob("*.py")) + [ROOT / "packaging" / "ironjarvis_entry.py"]:
        text = p.read_text(encoding="utf-8", errors="replace")
        if re.search(r"(from\s+\S*ocr_worker\s+import|import\s+\S*ocr_worker)", text):
            importers.append(p.name)
    assert sorted(importers) == ["cli.py", "ironjarvis_entry.py"]


def _gil_holding_worker(tmp_path: Path) -> list[str]:
    """A worker that never answers AND holds its own GIL — the stalled WinRT
    call, reproduced (``PyDLL`` keeps the GIL across the foreign call)."""
    script = tmp_path / "stuck_worker.py"
    script.write_text(
        "import ctypes, sys\n"
        "sys.stdin.buffer.read()\n"
        "ctypes.PyDLL('kernel32').Sleep(600000) if sys.platform == 'win32' else __import__('time').sleep(600)\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def test_a_stuck_engine_costs_one_answer_never_the_app(tmp_path, monkeypatch):
    monkeypatch.setattr(local_ocr, "_FORCED_OFF", False)
    monkeypatch.setattr(local_ocr, "_SELF_TEST", None)
    monkeypatch.setattr(local_ocr, "_worker_argv", lambda: _gil_holding_worker(tmp_path))
    monkeypatch.setattr(local_ocr, "languages", lambda: ["en-US"])
    monkeypatch.setattr(local_ocr, "available", lambda: True)
    monkeypatch.setattr(local_ocr, "WORKER_START_S", 2.0)

    beats = 0
    stop = threading.Event()

    def heartbeat():
        nonlocal beats
        while not stop.is_set():
            beats += 1
            time.sleep(0.05)

    t = threading.Thread(target=heartbeat, daemon=True)
    t.start()
    t0 = time.perf_counter()
    try:
        result = local_ocr.self_test()
    finally:
        took = time.perf_counter() - t0
        stop.set()
        t.join(5)
    assert result["ok"] is False
    assert result["detail"] == "Windows OCR did not answer"
    assert took < local_ocr.WORKER_START_S + 12 + 15
    # This process kept running the whole time (a GIL held HERE would have
    # stopped the heartbeat at once).
    assert beats >= int(took / 0.05 * 0.5), (beats, took)
    # A stall is not "no OCR": the next ask tries again.
    assert local_ocr._SELF_TEST is None


def test_a_stuck_worker_is_killed_at_its_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(local_ocr, "_worker_argv", lambda: _gil_holding_worker(tmp_path))
    monkeypatch.setattr(local_ocr, "WORKER_START_S", 1.0)
    t0 = time.perf_counter()
    assert local_ocr.recognize_many([b"a", b"b"], deadline_s=0.5) == [None, None]
    assert time.perf_counter() - t0 < 20


@pytest.mark.skipif(sys.platform != "win32", reason="Windows OCR exists only on Windows")
def test_the_daemon_process_never_loads_winrt_even_after_a_real_self_test():
    code = (
        "import sys\n"
        "import iron_jarvis.daemon.app\n"
        "from iron_jarvis.documents import local_ocr\n"
        "local_ocr._FORCED_OFF = False\n"
        "r = local_ocr.self_test()\n"
        "import json\n"
        "print(json.dumps({'ok': r['ok'], 'langs': r['languages'],"
        " 'winrt': [m for m in sys.modules if m == 'winrt' or m.startswith('winrt.')]}))\n"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr[-2000:]
    data = json.loads(out.stdout.strip().splitlines()[-1])
    assert data["winrt"] == [], "the daemon process imported WinRT"
    if data["langs"]:
        assert data["ok"] is True, "the worker process must still read the test line"


def test_the_frozen_fast_path_serves_the_worker_without_the_cli(monkeypatch):
    spec = importlib.util.spec_from_file_location("ij_entry_v14102", ROOT / "packaging" / "ironjarvis_entry.py")
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)  # type: ignore[union-attr]
    import iron_jarvis.ocr_worker as worker

    called = []
    monkeypatch.setattr(worker, "main", lambda argv=None: called.append(1) or 0)
    assert entry.OCR_WORKER_SUBCOMMAND == local_ocr.WORKER_SUBCOMMAND == "ocr-worker"
    assert entry._fast_path(["ocr-worker"]) == 0
    assert called == [1]
    assert entry._fast_path(["ocr-worker", "--help"]) is None


def test_the_cli_subcommand_runs_the_same_worker():
    cli = (SRC / "daemon" / "cli.py").read_text(encoding="utf-8")
    assert '@app.command("ocr-worker", hidden=True)' in cli
    assert "from ..ocr_worker import main as ocr_main" in cli


def test_the_worker_answers_its_protocol():
    from iron_jarvis import ocr_worker

    assert ocr_worker.answer({"op": "nope"}) == {"error": "unknown request"}
    assert ocr_worker.answer({"op": "recognize", "pngs": ["!!not base64!!"]}) == {"texts": [None]}


def test_chat_asks_whether_ocr_is_there_off_the_event_loop():
    src = (SRC / "daemon" / "chat_turn.py").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "_local_ocr.local_enabled(cfg) and _local_ocr.available()" not in src
    assert "await asyncio.to_thread(\n                _local_ocr.available\n" in src
