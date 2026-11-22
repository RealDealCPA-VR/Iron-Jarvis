"""What the machine was doing when a real-Edge test failed (Build › Code
follow-up, item 1).

Release run 44745306821 (job 136319029882) failed two real-Edge pins four
times in a row ("Opening 127.0.0.1:<port> did not finish within 45 s.") while
every test of that shard ran four to eight times slower than the same tests
on the rerun's runner, and the log named nothing that would say why.
``tests/_real_edge_report.py`` (registered by ``tests/conftest.py``) adds this
text to the report of any ``test_real_edge*`` test that fails, and prints it
at the end of the run when the test passed on its retry, so the next one
names the busiest programs, the processors,
memory and disk, how many Edge, node and python processes were running, and
how many Edge and node processes had no living parent (and how many of those
started before the failing test did).

WHAT A LEFTOVER LOOKS LIKE (FU-1 fourth review). The counts are no clue by
themselves: CI runs each shard with ``-n auto``, so the Edge of every
real-Edge test running on another worker at that moment is in them (about 11
processes each), and one of those can be among the busiest programs. A live
test's Edge always has a living parent (its browser's is Playwright's driver,
whose own is the test's worker; measured on this PC: all of a live Edge's
processes had one, and once its driver was killed its browser process had
none). So an ``msedge.exe`` or ``node.exe`` with NO living parent, started
since this run began and still there at the end of the sample, is what an
Edge or a driver a test left running looks like; on a PC someone uses, an
Edge they opened during the run counts too. One that started BEFORE the
failing test (its setup) was left by an earlier test; one that started during
it outlived the failing test's own close. A driver a test never stopped is
still held by its living worker, so it is no orphan: a zero does not rule a
leftover out. On Linux and macOS an
orphan is handed to pid 1 (or to a "subreaper", which this does not see).
Edge and node are counted by their EXACT names: ``msedgewebview2.exe`` and
``msedge_proxy.exe`` are other programs.

Names only: this repository's CI logs are public, and a command line can
carry a secret, so no argument of a command line is ever printed. (On Linux
and macOS psutil's own ``name()`` reads a program's command line when the
kernel cut its name at 15 characters, and keeps only the file name of its
first word; this file reads none.) psutil + stdlib.
"""

from __future__ import annotations

import os
import sys
import time
from typing import Any

#: How long the processors and programs are sampled, in seconds.
SAMPLE_S = 1.0
#: How many of the busiest programs are listed.
TOP = 12
#: Programs counted by name (Edge's processes, Playwright's driver, the workers).
COUNTED = ("msedge.exe", "node.exe", "python.exe")
#: The programs a test can leave running (Edge and its driver), by their exact
#: names on Windows and on Linux (no ``.exe`` there).
LEFT_BEHIND = {"msedge.exe": ("msedge.exe", "msedge"), "node.exe": ("node.exe", "node")}
_WINDOWS = sys.platform == "win32"


def _counted(name: str) -> str:
    """Which of :data:`COUNTED` a program is ("" for none): Edge and node by
    their exact names, python by its first letters (python3, python3.12)."""
    low = name.lower()
    for counted, names in LEFT_BEHIND.items():
        if low in names:
            return counted
    return "python.exe" if low.startswith("python") else ""


def _run_began(proc: Any) -> float:
    """When this test run began: the start of the oldest python in this
    process's line of parents (an xdist worker's controller, and the venv's
    launcher above it on Windows)."""
    began = float(proc.create_time())
    try:
        for parent in proc.parents():
            if _counted(parent.name()) != "python.exe":
                break
            began = min(began, float(parent.create_time()))
    except Exception:  # noqa: BLE001 — gone, or not ours to read: what was read
        pass
    return began


def _left_behind(proc: Any, since: float) -> bool:
    """Whether a process started at or after ``since`` has no living parent
    (psutil's ``parent()`` is None once the parent has gone, also when its
    pid was reused). On Linux and macOS an orphan's parent becomes pid 1."""
    try:
        if float(proc.create_time()) < since:
            return False
        parent = proc.parent()
    except Exception:  # noqa: BLE001 — gone, or not ours to read
        return False
    if parent is None:
        return True
    return not _WINDOWS and parent.pid == 1


#: Windows' priority classes (psutil's ``nice()`` there), in words.
_PRIORITY = {64: "idle", 16384: "below normal", 32: "normal", 32768: "above normal", 128: "high", 256: "realtime"}


def _priority(proc: Any) -> str:
    """A program's priority in words ("?" when it cannot be read). CI starts
    pytest BELOW NORMAL (v1.333.1): a Normal program outranks every test
    process and every Edge a test starts."""
    try:
        nice = int(proc.nice())
    except Exception:  # noqa: BLE001
        return "?"
    if sys.platform == "win32":
        return _PRIORITY.get(nice, str(nice))
    return f"nice {nice}"


def _disk() -> "tuple[float, float] | None":
    import psutil

    try:
        io = psutil.disk_io_counters()
    except Exception:  # noqa: BLE001
        return None
    if io is None:
        return None
    return float(io.read_bytes), float(io.write_bytes)


def _earlier(proc: Any, test_began: "float | None") -> bool:
    """Whether a process started before the failing test did."""
    if test_began is None:
        return False
    try:
        return float(proc.create_time()) < test_began
    except Exception:  # noqa: BLE001 — gone, or not ours to read
        return False


def runner_state(test_began: "float | None" = None) -> str:
    """BLOCKING (about :data:`SAMPLE_S`): the machine's state, as text.
    ``test_began`` (seconds since the epoch, the failing attempt's setup):
    the leftovers are also counted by whether they started before it."""
    import psutil

    me = psutil.Process(os.getpid())
    since = _run_began(me)
    # Each program's CPU time is read twice; its share is over ITS OWN window
    # (reading a few hundred programs takes a while on a busy machine).
    first: dict[int, tuple[Any, str, float, float, bool, bool]] = {}
    for proc in psutil.process_iter(["name"]):
        if proc.pid == 0:
            continue  # Windows' "System Idle Process": the idle time, not a program
        try:
            name = str(proc.info.get("name") or "?")
            t = proc.cpu_times()
            left = _counted(name) in LEFT_BEHIND and _left_behind(proc, since)
            earlier = left and _earlier(proc, test_began)
            first[proc.pid] = (proc, name, float(t.user + t.system), time.monotonic(), left, earlier)
        except Exception:  # noqa: BLE001 — gone, or not ours to read
            continue
    disk0 = _disk()
    psutil.cpu_percent(percpu=True)  # opens the per-processor window
    began = time.monotonic()
    time.sleep(SAMPLE_S)
    per_cpu = psutil.cpu_percent(percpu=True)
    spent = max(time.monotonic() - began, 1e-3)
    disk1 = _disk()

    rows: list[tuple[float, str, int, int, str]] = []
    counts = {name: 0 for name in COUNTED}
    # A process whose parent went a moment ago (another worker's test closing
    # its Edge) is usually gone by the end of the sample: only one still there
    # counts.
    left_counts = {name: 0 for name in LEFT_BEHIND}
    earlier_counts = {name: 0 for name in LEFT_BEHIND}
    for pid, (proc, name, before, at, left, earlier) in first.items():
        if _counted(name):
            counts[_counted(name)] += 1
        try:
            t = proc.cpu_times()
            rss = int(proc.memory_info().rss)
        except Exception:  # noqa: BLE001
            continue
        if left:
            left_counts[_counted(name)] += 1
        if earlier:
            earlier_counts[_counted(name)] += 1
        window = max(time.monotonic() - at, 1e-3)
        share = 100.0 * (float(t.user + t.system) - before) / window
        rows.append((share, name, pid, rss, ""))
    rows.sort(key=lambda r: r[0], reverse=True)
    rows = [
        (*r[:4], _priority(first[r[2]][0]) + (", no living parent" if first[r[2]][4] else ""))
        for r in rows[:TOP]
    ]

    mem = psutil.virtual_memory()
    lines = [f"the machine when the test failed (sampled over {spent:.1f} s):"]
    avg = sum(per_cpu) / len(per_cpu) if per_cpu else 0.0
    lines.append(
        f"  processors: {len(per_cpu)}, {avg:.0f}% busy (each: {' '.join(f'{p:.0f}' for p in per_cpu)})"
        f"; up {(time.time() - psutil.boot_time()) / 60:.0f} min"
    )
    lines.append(f"  memory: {mem.used / 2**30:.1f} of {mem.total / 2**30:.1f} GB used ({mem.percent:.0f}%)")
    if disk0 is not None and disk1 is not None:
        lines.append(
            f"  disk: read {(disk1[0] - disk0[0]) / spent / 2**20:.1f} MB/s, "
            f"write {(disk1[1] - disk0[1]) / spent / 2**20:.1f} MB/s"
        )
    lines.append("  running: " + ", ".join(f"{name} {n}" for name, n in counts.items()))
    orphans = "  with no living parent, started since this run began: " + ", ".join(
        f"{name} {n}" for name, n in left_counts.items()
    )
    if test_began is not None:
        orphans += "; of them, started before this test: " + ", ".join(
            f"{name} {n}" for name, n in earlier_counts.items()
        )
    lines.append(orphans)
    lines.append(f"  this test's process: {_priority(me)} priority")
    lines.append("  busiest programs (% of one processor, memory, priority):")
    for share, name, pid, rss, prio in rows:
        lines.append(f"    {name} (pid {pid}) {share:.0f}% {rss / 2**20:.0f} MB {prio}")
    return "\n".join(lines)
