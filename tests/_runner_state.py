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
memory and disk, and how many Edge, node and python processes were running.

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


def _counted(name: str) -> str:
    """Which of :data:`COUNTED` a program is ("" for none); Linux names
    have no ``.exe``."""
    low = name.lower()
    for counted in COUNTED:
        if low.startswith(counted.removesuffix(".exe")):
            return counted
    return ""


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


def runner_state() -> str:
    """BLOCKING (about :data:`SAMPLE_S`): the machine's state, as text."""
    import psutil

    # Each program's CPU time is read twice; its share is over ITS OWN window
    # (reading a few hundred programs takes a while on a busy machine).
    first: dict[int, tuple[Any, str, float, float]] = {}
    for proc in psutil.process_iter(["name"]):
        if proc.pid == 0:
            continue  # Windows' "System Idle Process": the idle time, not a program
        try:
            t = proc.cpu_times()
            first[proc.pid] = (proc, str(proc.info.get("name") or "?"), float(t.user + t.system), time.monotonic())
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
    for pid, (proc, name, before, at) in first.items():
        if _counted(name):
            counts[_counted(name)] += 1
        try:
            t = proc.cpu_times()
            rss = int(proc.memory_info().rss)
        except Exception:  # noqa: BLE001
            continue
        window = max(time.monotonic() - at, 1e-3)
        share = 100.0 * (float(t.user + t.system) - before) / window
        rows.append((share, name, pid, rss, ""))
    rows.sort(key=lambda r: r[0], reverse=True)
    rows = [(*r[:4], _priority(first[r[2]][0])) for r in rows[:TOP]]

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
    lines.append(f"  this test's process: {_priority(psutil.Process(os.getpid()))} priority")
    lines.append("  busiest programs (% of one processor, memory, priority):")
    for share, name, pid, rss, prio in rows:
        lines.append(f"    {name} (pid {pid}) {share:.0f}% {rss / 2**20:.0f} MB {prio}")
    return "\n".join(lines)
