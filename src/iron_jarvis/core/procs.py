"""Child processes the daemon starts: which LANE they run in (v1.333.0).

THE PROBLEM THIS MODULE EXISTS FOR: Iron Jarvis is a daily driver that also
runs unattended work — schedules at 3am, goals, workflows, queued
assignments — for days at a time. Every child that work started (an agent
CLI, ffmpeg encoding on every core, a REPL cell, a build in a shell) ran at
Normal priority, exactly like the window the user is typing in, so a long
background job made the whole PC feel slow.

THE LANE: a run whose origin nobody is watching is BACKGROUND work. The
orchestrator marks it once, around the whole run, with
:func:`background_lane`, and every spawn site below it reads
:func:`in_background_lane` to decide the child's priority. A ContextVar is
the right carrier: asyncio copies the context into every task the run
creates and ``asyncio.to_thread`` copies it into the worker thread, so the
mark reaches a subprocess started three awaits and one thread hop away
without threading a flag through every signature. Work the user started and
is waiting on (chat, a job they pressed Run on, a Build pane they opened) is
never in the background lane and keeps Normal priority.

Below-normal, never idle: an idle-priority child starves outright while the
user works, and EcoQoS moves it onto efficiency cores, which turns a build
from minutes into much longer. Below-normal only yields when something the
user is doing needs the CPU.

THE DOOR (second half of this module): :func:`run`, :func:`run_async`,
:func:`spawn` and :func:`kill_tree` are how the daemon starts a child. Two
defects lived at every hand-rolled spawn site before this:

* ``subprocess.run(timeout=)`` on Windows kills only the DIRECT child, then
  drains the pipes with NO timeout. A grandchild still holding the pipe (an
  npm ``.cmd`` shim's node, a helper the command started) parked that worker
  thread for the grandchild's whole life, and the default executor has 28.
  Here a timeout kills the TREE and the drain after it is bounded.
* Nothing outlived nothing on purpose: after an abrupt daemon death every
  child (an agent CLI, ffmpeg, git) kept running with nobody to stop it.
  On Windows each child now sits in its own Job object created with
  KILL_ON_JOB_CLOSE, whose only handle the daemon holds — when the daemon
  dies, for any reason, the kernel closes that handle and the whole tree
  goes with it. The child is created SUSPENDED, assigned, then resumed, so a
  shell or ``.cmd`` shim cannot start a grandchild in the gap before the
  assignment.

Two kinds of child are deliberately different. Iron-Proxy's process must
survive a daemon crash (the next daemon re-adopts it), so it never comes
through here. And a child that can open WINDOWS for the user (the shell
tool's ``start notepad``, a script that opens a browser) gets a job WITHOUT
kill-on-close (``keep_windows=True``): the user's newly opened app is never
killed with the command that opened it. Such a call that TIMES OUT or is
CANCELLED stops, at once, every member that owns no visible window and is
not a window program. One that ENDS NORMALLY keeps what it left running (an
agent may start a dev server or a watcher on purpose and use it on its next
step): its job is handed to a small keeper, keyed by the session that ran the
command (:func:`owned_by`), and :func:`end_session` stops those windowless
members when that session's run ends (:func:`end_all` at shutdown; past
``KEPT_JOBS_MAX`` jobs the oldest is stopped first).

``BREAKAWAY_OK`` is set on every job on purpose. Without it a program that
asks to leave its job (``CREATE_BREAKAWAY_FROM_JOB`` — some launchers and
updaters do) fails to START with "Access is denied" (measured); with it, only
a process that asks in so many words leaves. Children that merely nest a job
of their own (Node/libuv puts its children in one with silent breakaway) stay
inside ours and still die with it (measured). ONE EXCEPTION (v1.386.0): Git
for Windows' bash (the IJ harness's Claude dialect) asks for every Windows
program it starts, so its Job is made WITHOUT ``BREAKAWAY_OK``
(``breakaway_ok=False``) — the MSYS2 runtime then asks nothing and every
member stays inside (measured).
"""

from __future__ import annotations

import asyncio
import contextlib
import locale
import os
import signal
import subprocess
import sys
import threading
import time
import weakref
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Iterator, Sequence

from . import progress as _progress

#: True while the current task is running unattended background work.
_BACKGROUND: ContextVar[bool] = ContextVar("ij_background_lane", default=False)

#: Windows priority class for background children (BELOW_NORMAL_PRIORITY_CLASS).
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000


@contextlib.contextmanager
def background_lane(on: bool = True) -> Iterator[None]:
    """Mark everything run inside this block (tasks and worker threads it
    creates included) as background work when *on* is true. ``on=False``
    leaves the current lane unchanged, so a caller can pass its own verdict
    without branching."""
    if not on:
        yield
        return
    token = _BACKGROUND.set(True)
    try:
        yield
    finally:
        _BACKGROUND.reset(token)


def in_background_lane() -> bool:
    """Whether the current task is running unattended background work."""
    return _BACKGROUND.get()


def priority_flags(background: bool | None = None) -> int:
    """The ``creationflags`` bits a child gets for its lane: below-normal
    priority on Windows for background work, nothing otherwise. *background*
    defaults to the current lane. Children inherit their parent's priority
    class, so marking the direct child covers the tree it starts."""
    if sys.platform != "win32":
        return 0
    if background is None:
        background = in_background_lane()
    return BELOW_NORMAL_PRIORITY_CLASS if background else 0


# --------------------------------------------------------------------------- #
# The door: run / run_async / spawn / kill_tree                               #
# --------------------------------------------------------------------------- #

#: ``CREATE_NO_WINDOW``: a console child gets no console window of its own.
CREATE_NO_WINDOW = 0x08000000
#: ``CREATE_SUSPENDED``: the child's first thread waits until resumed.
CREATE_SUSPENDED = 0x00000004

#: How long the drain after a tree kill may take. Bounded on purpose: a
#: holder the kill could not reach must cost seconds, never the thread.
DRAIN_TIMEOUT_S = 15.0

_JOB_OBJECT_LIMIT_PRIORITY_CLASS = 0x00000020
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3  # JOBOBJECTINFOCLASS
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9  # JOBOBJECTINFOCLASS
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_IMAGE_SUBSYSTEM_WINDOWS_GUI = 2


@dataclass
class Result:
    """What :func:`run` hands back. Output is BYTES: each caller decodes it
    the way it always has (``sandbox.native._as_text`` for anything that
    reaches a model, :func:`text` for a text-mode pipe)."""

    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False
    #: True when :func:`run_async`'s await was cancelled before the child ran.
    cancelled: bool = False
    args: Any = None
    timeout: float | None = None

    def check_timeout(self) -> "Result":
        """Raise ``subprocess.TimeoutExpired`` when the run timed out, so a
        caller that used to catch it from ``subprocess.run`` keeps its words."""
        if self.timed_out:
            raise subprocess.TimeoutExpired(
                self.args, self.timeout or 0, output=self.stdout, stderr=self.stderr
            )
        return self


def text(data: bytes | None, encoding: str | None = None, errors: str = "replace") -> str:
    """Decode captured bytes the way a text-mode pipe would have: the locale
    encoding unless one is named, and universal newlines (``\\r\\n`` and a
    lone ``\\r`` become ``\\n``). ``errors`` defaults to "replace": a strict
    decode in ``communicate()``'s reader thread used to return ``''`` for the
    whole stream on one bad byte."""
    if not data:
        return ""
    enc = encoding or locale.getpreferredencoding(False)
    return data.decode(enc, errors).replace("\r\n", "\n").replace("\r", "\n")


class _Kernel:
    """kernel32 / user32 / ntdll with the signatures this module calls.

    Private ``WinDLL`` instances, so setting ``argtypes`` here never changes a
    function another module calls through ``ctypes.windll``. HANDLE is
    pointer-sized: every handle is ``c_void_p`` (a default ``c_int`` return
    would truncate it)."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes as w

        self.ctypes = ctypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        u32 = ctypes.WinDLL("user32", use_last_error=True)
        nt = ctypes.WinDLL("ntdll")
        H = ctypes.c_void_p
        k32.CreateJobObjectW.restype = H
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
        k32.SetInformationJobObject.restype = w.BOOL
        k32.SetInformationJobObject.argtypes = [H, ctypes.c_int, ctypes.c_void_p, w.DWORD]
        k32.QueryInformationJobObject.restype = w.BOOL
        k32.QueryInformationJobObject.argtypes = [
            H, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.c_void_p,
        ]
        k32.AssignProcessToJobObject.restype = w.BOOL
        k32.AssignProcessToJobObject.argtypes = [H, H]
        k32.TerminateJobObject.restype = w.BOOL
        k32.TerminateJobObject.argtypes = [H, w.UINT]
        k32.CloseHandle.restype = w.BOOL
        k32.CloseHandle.argtypes = [H]
        k32.OpenProcess.restype = H
        k32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k32.TerminateProcess.restype = w.BOOL
        k32.TerminateProcess.argtypes = [H, w.UINT]
        k32.QueryFullProcessImageNameW.restype = w.BOOL
        k32.QueryFullProcessImageNameW.argtypes = [
            H, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD),
        ]
        nt.NtResumeProcess.restype = ctypes.c_long
        nt.NtResumeProcess.argtypes = [H]
        self.enum_proc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        u32.EnumWindows.restype = w.BOOL
        u32.EnumWindows.argtypes = [self.enum_proc, w.LPARAM]
        u32.IsWindowVisible.restype = w.BOOL
        u32.IsWindowVisible.argtypes = [w.HWND]
        u32.GetWindowThreadProcessId.restype = w.DWORD
        u32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
        self.k32, self.u32, self.nt, self.w = k32, u32, nt, w

        class _BASIC(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class _IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount",
            )]

        class _EXTENDED(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BASIC),
                ("IoInfo", _IO),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class _PIDS(ctypes.Structure):
            _fields_ = [
                ("NumberOfAssignedProcesses", ctypes.c_uint32),
                ("NumberOfProcessIdsInList", ctypes.c_uint32),
                ("ProcessIdList", ctypes.c_size_t * 1024),
            ]

        self.EXTENDED, self.PIDS = _EXTENDED, _PIDS


_KERNEL: _Kernel | None = None
_KERNEL_LOCK = threading.Lock()


def _kernel() -> _Kernel | None:
    """The Windows API, loaded once; ``None`` off Windows or when it cannot
    load (every caller then falls back to the plain tree kill)."""
    global _KERNEL
    if sys.platform != "win32":
        return None
    with _KERNEL_LOCK:
        if _KERNEL is None:
            try:
                _KERNEL = _Kernel()
            except Exception:  # noqa: BLE001 — degraded: no jobs, plain kills
                return None
        return _KERNEL


class _Job:
    """One child's Windows Job object. Thread-safe: a cancel on the loop's
    thread may terminate it while the worker thread is closing it."""

    def __init__(self, handle: int, kill_on_close: bool) -> None:
        self.handle = handle
        self.kill_on_close = kill_on_close
        self._lock = threading.Lock()
        self.closed = False
        #: Handed to the keeper (:func:`release`): the keeper alone closes it.
        self.kept = False

    @classmethod
    def create(cls, *, kill_on_close: bool, below_normal: bool, breakaway_ok: bool = True) -> "_Job | None":
        k = _kernel()
        if k is None:
            return None
        try:
            handle = k.k32.CreateJobObjectW(None, None)
            if not handle:
                return None
            info = k.EXTENDED()
            flags = _JOB_OBJECT_LIMIT_BREAKAWAY_OK if breakaway_ok else 0
            if kill_on_close:
                flags |= _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if below_normal:
                # The whole tree stays below normal: a member cannot raise
                # its own class while the job sets one.
                flags |= _JOB_OBJECT_LIMIT_PRIORITY_CLASS
                info.BasicLimitInformation.PriorityClass = BELOW_NORMAL_PRIORITY_CLASS
            info.BasicLimitInformation.LimitFlags = flags
            ok = k.k32.SetInformationJobObject(
                handle, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                k.ctypes.byref(info), k.ctypes.sizeof(info),
            )
            if not ok:
                k.k32.CloseHandle(handle)
                return None
            return cls(int(handle), kill_on_close)
        except Exception:  # noqa: BLE001 — no job: the caller still runs the child
            return None

    def assign(self, process_handle: int) -> bool:
        k = _kernel()
        with self._lock:
            if k is None or self.closed:
                return False
            try:
                return bool(k.k32.AssignProcessToJobObject(self.handle, process_handle))
            except Exception:  # noqa: BLE001
                return False

    def terminate(self) -> bool:
        """Kill every member at once. True when the kernel did it."""
        k = _kernel()
        with self._lock:
            if k is None or self.closed:
                return False
            try:
                return bool(k.k32.TerminateJobObject(self.handle, 1))
            except Exception:  # noqa: BLE001
                return False

    def member_pids(self) -> list[int]:
        k = _kernel()
        with self._lock:
            if k is None or self.closed:
                return []
            try:
                buf = k.PIDS()
                ok = k.k32.QueryInformationJobObject(
                    self.handle, _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                    k.ctypes.byref(buf), k.ctypes.sizeof(buf), None,
                )
                if not ok:
                    return []
                n = min(int(buf.NumberOfProcessIdsInList), len(buf.ProcessIdList))
                return [int(buf.ProcessIdList[i]) for i in range(n)]
            except Exception:  # noqa: BLE001
                return []

    def close(self) -> None:
        """Release the handle. With kill-on-close this IS the end of every
        member still running; without it the members are left alone."""
        k = _kernel()
        with self._lock:
            if self.closed:
                return
            self.closed = True
            if k is not None:
                try:
                    k.k32.CloseHandle(self.handle)
                except Exception:  # noqa: BLE001
                    pass


def _resume(process_handle: int) -> bool:
    """Resume a child created with CREATE_SUSPENDED (all its threads).

    True for every NTSTATUS in the SUCCESS class (``NT_SUCCESS``: >= 0), not
    only ``STATUS_SUCCESS``: a False here makes :func:`spawn` kill the child
    and start the command AGAIN, so an informational success read as failure
    would run a command with side effects twice."""
    k = _kernel()
    if k is None:
        return False
    try:
        return int(k.nt.NtResumeProcess(process_handle)) >= 0
    except Exception:  # noqa: BLE001
        return False


def _pids_with_visible_windows() -> set[int]:
    """Every process that owns a visible top-level window right now."""
    k = _kernel()
    if k is None:
        return set()
    owners: set[int] = set()
    pid = k.w.DWORD()

    def _each(hwnd: Any, _lparam: Any) -> bool:
        try:
            if k.u32.IsWindowVisible(hwnd):
                k.u32.GetWindowThreadProcessId(hwnd, k.ctypes.byref(pid))
                owners.add(int(pid.value))
        except Exception:  # noqa: BLE001 — one odd window never ends the walk
            pass
        return True

    try:
        k.u32.EnumWindows(k.enum_proc(_each), 0)
    except Exception:  # noqa: BLE001
        pass
    return owners


_GUI_IMAGE_CACHE: dict[str, bool] = {}


def _is_window_program(pid: int) -> bool:
    """Whether *pid* runs a Windows-GUI-subsystem program (notepad, a
    browser) rather than a console one (cmd, python, node, ping).

    This is what makes the sweep safe in the first moments after ``start
    notepad``: the program exists before its window does, and a sweep that
    judged by the window alone would kill it in that gap. Read from the
    image's PE header (no new dependency); unknown reads as a window program,
    because sparing a console helper costs less than killing a user's app."""
    k = _kernel()
    if k is None:
        return True
    try:
        h = k.k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return True
        try:
            size = k.w.DWORD(32768)
            buf = k.ctypes.create_unicode_buffer(size.value)
            if not k.k32.QueryFullProcessImageNameW(h, 0, buf, k.ctypes.byref(size)):
                return True
            path = buf.value
        finally:
            k.k32.CloseHandle(h)
    except Exception:  # noqa: BLE001
        return True
    cached = _GUI_IMAGE_CACHE.get(path)
    if cached is not None:
        return cached
    try:
        with open(path, "rb") as fh:
            head = fh.read(4096)
        pe = int.from_bytes(head[0x3C:0x40], "little")
        if head[pe:pe + 4] != b"PE\0\0":
            return True
        # Optional header starts 24 bytes after "PE\0\0"; Subsystem is at +68
        # in both the PE32 and PE32+ layouts.
        at = pe + 24 + 68
        gui = int.from_bytes(head[at:at + 2], "little") == _IMAGE_SUBSYSTEM_WINDOWS_GUI
    except Exception:  # noqa: BLE001
        return True
    if len(_GUI_IMAGE_CACHE) > 256:
        _GUI_IMAGE_CACHE.clear()
    _GUI_IMAGE_CACHE[path] = gui
    return gui


def _terminate_pid(pid: int) -> None:
    k = _kernel()
    if k is None:
        return
    try:
        h = k.k32.OpenProcess(_PROCESS_TERMINATE, False, pid)
        if h:
            try:
                k.k32.TerminateProcess(h, 1)
            finally:
                k.k32.CloseHandle(h)
    except Exception:  # noqa: BLE001 — already gone
        pass


def _windowless_members(job: _Job) -> list[int]:
    """The members of a keep-windows job that show the user nothing: no
    visible top-level window AND not a window program."""
    members = job.member_pids()
    if not members:
        return []
    windowed = _pids_with_visible_windows()
    return [pid for pid in members if not (pid in windowed or _is_window_program(pid))]


def _sweep_windowless(job: _Job) -> list[int]:
    """Stop the members of a keep-windows job that show the user nothing.
    Returns the pids stopped (the pins count them)."""
    stopped = _windowless_members(job)
    for pid in stopped:
        _terminate_pid(pid)
    return stopped


# --------------------------------------------------------------------------- #
# The keeper: what a keep-windows call that ENDED NORMALLY left running        #
# --------------------------------------------------------------------------- #

#: The session whose tool call is starting children right now (v1.333.0):
#: the agent session id, "chat" for chat, "" when the caller named none.
#: Set by the tool around the call (:func:`owned_by`); a spawn records it.
_OWNER: ContextVar[str] = ContextVar("ij_proc_owner", default="")

#: How many kept jobs may wait for their session to end. Past it the OLDEST
#: is stopped (its windowless members) and closed, so a session that never
#: ends ("chat", a run whose end nobody reports) cannot grow this forever.
KEPT_JOBS_MAX = 64

_KEPT: list[tuple[str, _Job]] = []
_KEPT_LOCK = threading.Lock()


@contextlib.contextmanager
def owned_by(session_id: str | None) -> Iterator[None]:
    """Children started inside this block belong to ``session_id``: what a
    keep-windows call leaves running is stopped when :func:`end_session`
    is called for it. Reaches worker threads (``asyncio.to_thread`` copies
    the context)."""
    token = _OWNER.set(str(session_id or ""))
    try:
        yield
    finally:
        _OWNER.reset(token)


def _keep(owner: str, job: _Job) -> None:
    evicted: list[_Job] = []
    with _KEPT_LOCK:
        job.kept = True
        _KEPT.append((owner, job))
        while len(_KEPT) > KEPT_JOBS_MAX:
            evicted.append(_KEPT.pop(0)[1])
    for old in evicted:
        _end_kept(old)


def _end_kept(job: _Job) -> list[int]:
    try:
        return _sweep_windowless(job)
    finally:
        job.close()


def kept_jobs(session_id: str | None = None) -> int:
    """How many kept jobs wait (for one session, or in all)."""
    with _KEPT_LOCK:
        return sum(1 for owner, _ in _KEPT if session_id is None or owner == session_id)


#: Callbacks run by :func:`end_session` (with the session id) and
#: :func:`end_all` (with ``None`` = every session), v1.364.0. A module that
#: keeps its OWN long-lived children per session (the IJ harness's
#: ``process_start``, ``harness/process.py``) registers one, so the run's end
#: stops them through the one door the runtime already calls — never a second
#: "run ended" hook in the runtime. Each returns the pids it stopped.
_END_HOOKS: list[Callable[[str | None], list[int]]] = []
_END_HOOKS_LOCK = threading.Lock()


def add_end_hook(hook: Callable[[str | None], list[int]]) -> None:
    """Run ``hook(session_id)`` from :func:`end_session` and ``hook(None)``
    from :func:`end_all`. Registering the same callable twice is a no-op."""
    with _END_HOOKS_LOCK:
        if hook not in _END_HOOKS:
            _END_HOOKS.append(hook)


def _run_end_hooks(session_id: str | None) -> list[int]:
    with _END_HOOKS_LOCK:
        hooks = list(_END_HOOKS)
    stopped: list[int] = []
    for hook in hooks:
        try:
            stopped += list(hook(session_id) or [])
        except Exception:  # noqa: BLE001 — one hook never stops the rest
            pass
    return stopped


def end_session(session_id: str) -> list[int]:
    """The session's run is over: stop the windowless members of every job
    its keep-windows calls left running (a window the user now has is
    spared), and close those jobs. Returns the pids stopped. Cheap when the
    session kept nothing; never raises."""
    with _KEPT_LOCK:
        mine = [job for owner, job in _KEPT if owner == session_id]
        _KEPT[:] = [(owner, job) for owner, job in _KEPT if owner != session_id]
    stopped: list[int] = []
    for job in mine:
        try:
            stopped += _end_kept(job)
        except Exception:  # noqa: BLE001 — one job never stops the rest
            pass
    stopped += _run_end_hooks(session_id)
    return stopped


def end_all() -> list[int]:
    """Daemon shutdown: :func:`end_session` for every session at once."""
    with _KEPT_LOCK:
        jobs = [job for _, job in _KEPT]
        _KEPT.clear()
    stopped: list[int] = []
    for job in jobs:
        try:
            stopped += _end_kept(job)
        except Exception:  # noqa: BLE001
            pass
    stopped += _run_end_hooks(None)
    return stopped


def _job_of(proc: Any) -> "_Job | None":
    return getattr(proc, "_ij_job", None)


#: OpenProcess rights :func:`adopt` needs to put a process in a job.
_PROCESS_SET_QUOTA = 0x0100


def adopt(pid: int, *, background: bool | None = None) -> "_Job | None":
    """Put a child that a LIBRARY started (not this door) in its own
    kill-on-close Job, and hand the job back (v1.376.0, the IJ harness's
    agent browser). ``None`` off Windows or when the job cannot be made or
    the process cannot be assigned — the caller then keeps its own kill path.

    Only safe for a child that has not started anything yet: a process
    assigned AFTER it ran keeps outside the job whatever it had already
    started (assignment never adopts existing children — the v1.346.1
    lesson). Playwright's driver is such a child: it starts nothing until it
    is asked to launch the browser, and the caller adopts it before asking.
    What it starts afterwards is born inside the job (measured with Edge:
    the driver, the browser and its gpu, network, storage, renderer and
    crash-reporter processes all members), so ``terminate()`` — or the
    daemon dying for any reason, which closes the job's only handle — ends
    them. A process that something ELSE starts on a member's behalf is not
    (Edge's ``identity_helper.exe``, measured in the v1.376.0 review): the
    caller keeps its own list for those. The caller owns the job:
    ``terminate()`` then ``close()``."""
    k = _kernel()
    if k is None or not pid:
        return None
    bg = in_background_lane() if background is None else bool(background)
    job = _Job.create(kill_on_close=True, below_normal=bg)
    if job is None:
        return None
    try:
        handle = k.k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, int(pid))
    except Exception:  # noqa: BLE001
        handle = None
    if not handle:
        job.close()
        return None
    try:
        assigned = job.assign(int(handle))
    finally:
        try:
            k.k32.CloseHandle(handle)
        except Exception:  # noqa: BLE001
            pass
    if not assigned:
        job.close()
        return None
    return job


def spawn(
    argv: Sequence[str] | str,
    *,
    background: bool | None = None,
    no_window: bool = True,
    keep_windows: bool = False,
    breakaway_ok: bool = True,
    **popen_kw: Any,
) -> subprocess.Popen:
    """Start a child for a caller that streams its pipes itself.

    Same contract as ``subprocess.Popen(argv, **popen_kw)`` plus, on Windows:
    ``CREATE_NO_WINDOW`` (unless ``no_window=False`` — the shell and script
    tools keep the console behaviour they always had), below-normal priority
    in the background lane, and its own Job (see the module docstring). POSIX:
    its own session, so :func:`kill_tree`'s ``killpg`` reaches the tree. The
    caller owns the handle and ends it with :func:`kill_tree` and
    :func:`release` (an unreleased job is released when the Popen object is
    collected).

    ``breakaway_ok`` (v1.386.0): False makes a Job that lets NO member leave.
    Git for Windows' bash needs it: the MSYS2 runtime starts every Windows
    program (``python``, ``node``) with ``CREATE_BREAKAWAY_FROM_JOB`` whenever
    the job allows it — measured: in a ``BREAKAWAY_OK`` job a bash command's
    ``python`` was not a member and outlived the tree kill; without it MSYS
    adds no such flag and every member dies with the job. The cost: a program
    a bash command starts that ASKS to leave its job fails to start."""
    bg = in_background_lane() if background is None else bool(background)
    windows = sys.platform == "win32"
    if not windows:
        popen_kw.setdefault("start_new_session", True)
        return subprocess.Popen(argv, **popen_kw)  # noqa: S603 — callers own argv
    flags = int(popen_kw.pop("creationflags", 0) or 0) | priority_flags(bg)
    if no_window:
        flags |= CREATE_NO_WINDOW
    job = _Job.create(kill_on_close=not keep_windows, below_normal=bg, breakaway_ok=breakaway_ok)
    try:
        proc = subprocess.Popen(  # noqa: S603 — callers own argv
            argv, creationflags=flags | (CREATE_SUSPENDED if job else 0), **popen_kw
        )
    except BaseException:
        if job is not None:
            job.close()
        raise
    if job is None:
        return proc
    handle = getattr(proc, "_handle", None)
    if handle is None:  # a test double: nothing was created suspended
        job.close()
        return proc
    assigned = job.assign(int(handle))
    if not _resume(int(handle)):
        # It never ran an instruction, so stopping it is free; start it plainly
        # (no job) rather than leave a suspended child behind.
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            pass
        job.close()
        return subprocess.Popen(argv, creationflags=flags, **popen_kw)  # noqa: S603
    if not assigned:
        job.close()
        return proc
    proc._ij_job = job  # type: ignore[attr-defined]
    proc._ij_owner = _OWNER.get()  # type: ignore[attr-defined]
    proc._ij_job_release = weakref.finalize(proc, job.close)  # type: ignore[attr-defined]
    return proc


def release(proc: Any, *, stop_windowless: bool = False) -> None:
    """The child's call is over. Kill-on-close job: closing it ends every
    member still running (nothing the call started outlives the call).

    Keep-windows job: with ``stop_windowless`` (the call timed out, was
    cancelled or raised) the members that show nothing are stopped now and
    the job closed. A call that ENDED NORMALLY keeps what it left running:
    when a windowless member is still alive the job goes to the keeper
    under the session that ran it (:func:`end_session`); else it closes."""
    job = _job_of(proc)
    if job is None or job.closed or getattr(job, "kept", False):
        return  # a second release of a kept job must not keep it twice
    fin = getattr(proc, "_ij_job_release", None)
    if not job.kill_on_close:
        if stop_windowless:
            _sweep_windowless(job)
        elif _windowless_members(job):
            # The keeper owns the handle now: the Popen object is collected
            # long before the session ends, and its finalizer must not close
            # the job under the keeper.
            if fin is not None:
                fin.detach()
            _keep(getattr(proc, "_ij_owner", ""), job)
            return
    if fin is not None:
        fin()
    else:
        job.close()


def _plain_tree_kill(proc: Any) -> None:
    """The pre-job tree kill (v1.228.0, RT3): ``taskkill /T /F`` walks the
    PID tree on Windows; POSIX children were started in their own session,
    so the whole group is one ``killpg`` away. Then ``proc.kill()`` so the
    drain after it can never hang on a live direct child."""
    try:
        if os.name == "nt":
            windir = os.environ.get("SystemRoot", r"C:\Windows")
            taskkill = os.path.join(windir, "System32", "taskkill.exe")
            if not os.path.exists(taskkill):
                taskkill = "taskkill"
            subprocess.run(
                [taskkill, "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=15,
                creationflags=CREATE_NO_WINDOW,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:  # noqa: BLE001 — fall through to the plain kill
        pass
    try:
        proc.kill()
    except Exception:  # noqa: BLE001 — already gone
        pass


def kill_tree(proc: Any) -> None:
    """Kill ``proc`` AND everything it started. Never raises.

    A child from :func:`spawn` with a kill-on-close job: ``TerminateJobObject``
    — every member at once, including one that re-parented itself away from
    the PID tree. A keep-windows job: only the members that show the user
    nothing (see :func:`_sweep_windowless`). Anything else (no job, a job that
    could not be made, Iron-Proxy's child): the plain tree kill."""
    job = _job_of(proc)
    if job is not None and not job.closed:
        if not job.kill_on_close:
            _sweep_windowless(job)
            return
        if job.terminate():
            try:
                proc.kill()
            except Exception:  # noqa: BLE001 — the job already ended it
                pass
            return
    if job is not None and job.closed:
        try:
            if proc.poll() is not None:
                return  # finished and released: its pid may be someone else's
        except Exception:  # noqa: BLE001
            pass
    _plain_tree_kill(proc)


def run(
    argv: Sequence[str] | str,
    *,
    timeout: float | None,
    input: bytes | None = None,
    cwd: Any = None,
    env: dict[str, str] | None = None,
    shell: bool = False,
    background: bool | None = None,
    no_window: bool = True,
    keep_windows: bool = False,
    drain_timeout: float = DRAIN_TIMEOUT_S,
    on_spawn: Callable[[Any], bool] | None = None,
    cancelled: Callable[[], bool] | None = None,
    startupinfo: Any = None,
    breakaway_ok: bool = True,
) -> Result:
    """Blocking: run a child to completion with a REAL bound, output as bytes.

    ``stdin`` is ``input`` when given, else DEVNULL (the packaged daemon's
    own stdin is a pipe nobody writes; an inherited one parked every
    prompting child for its whole timeout). On a timeout the TREE is killed
    and the pipes drained for at most ``drain_timeout``; the result says
    ``timed_out`` (``Result.check_timeout`` re-raises it as
    ``TimeoutExpired`` for callers that caught that). ``OSError`` from the
    spawn itself (a missing program) propagates. ``on_spawn(proc)`` and
    ``cancelled()`` are the :func:`run_async` seams: the first publishes the
    handle and answers whether the caller was already cancelled, the second
    tells a call the await gave up on (its child ended by that kill, not on
    its own) from one that finished, so a keep-windows job is swept rather
    than kept. ``startupinfo`` (v1.386.0): handed to ``Popen`` only when
    given (Git's bash gets the hidden console ``shell=True`` gives cmd.exe).
    ``breakaway_ok``: :func:`spawn`'s (False for Git's bash)."""
    proc = spawn(
        argv,
        background=background,
        no_window=no_window,
        keep_windows=keep_windows,
        **({} if breakaway_ok else {"breakaway_ok": False}),
        shell=shell,
        cwd=None if cwd is None else str(cwd),
        env=env,
        stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **({"startupinfo": startupinfo} if startupinfo is not None else {}),
    )
    ended_early = True  # a timeout, a cancel or an error: stop what it left
    try:
        if on_spawn is not None and on_spawn(proc):
            kill_tree(proc)
            _bounded_drain(proc, drain_timeout)
            return Result(-1, b"", b"", cancelled=True, args=argv, timeout=timeout)
        # v1.392.0: inside a coding session's turn the output is read piece
        # by piece, each piece a progress mark (core/progress — a command that
        # keeps printing is never a stall). Nothing listens anywhere else, and
        # there the call is exactly what it was.
        moving = _progress.current() if input is None else None
        try:
            if moving is not None:
                out, err, timed_out = _communicate_moving(proc, timeout, drain_timeout, moving)
                if timed_out:
                    code = proc.returncode if proc.returncode is not None else -1
                    return Result(code, out, err, timed_out=True, args=argv, timeout=timeout)
            else:
                out, err = proc.communicate(input, timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_tree(proc)
            out, err = _bounded_drain(proc, drain_timeout)
            code = proc.returncode if proc.returncode is not None else -1
            return Result(code, out, err, timed_out=True, args=argv, timeout=timeout)
        except BaseException:
            kill_tree(proc)
            raise
        ended_early = bool(cancelled is not None and cancelled())
        return Result(proc.returncode, out or b"", err or b"", args=argv, timeout=timeout)
    finally:
        release(proc, stop_windowless=ended_early)


#: v1.392.0: how often a command writing to FILES is looked at for new output
#: (``run_to_files`` inside a watched turn).
MOVING_LOOK_S = 1.0


def _communicate_moving(
    proc: Any, timeout: float | None, drain_timeout: float, fn: Callable[[], None],
) -> tuple[bytes, bytes, bool]:
    """``proc.communicate(timeout=timeout)`` for a child with no stdin to
    feed, reading each pipe PIECE BY PIECE on a thread of its own so every
    piece calls ``fn`` (v1.392.0, ``core/progress``). The same bound as
    ``communicate``: the child AND its pipes must be done within ``timeout``
    (a background job still holding a pipe counts, as it always did), else
    the tree is killed and the pipes drained for at most ``drain_timeout``.
    ``(out, err, timed_out)``."""
    mark = _progress.safe(fn)
    sinks: dict[str, list[bytes]] = {"out": [], "err": []}

    def _read(fh: Any, sink: list[bytes]) -> None:
        if fh is None:
            return
        read1 = getattr(fh, "read1", None)
        try:
            while True:
                data = read1(65536) if read1 is not None else fh.read(4096)
                if not data:
                    return
                sink.append(data)
                mark()
        except (OSError, ValueError):
            return

    readers = [
        threading.Thread(target=_read, args=(proc.stdout, sinks["out"]), name="ij-proc-out", daemon=True),
        threading.Thread(target=_read, args=(proc.stderr, sinks["err"]), name="ij-proc-err", daemon=True),
    ]
    for t in readers:
        t.start()
    deadline = None if timeout is None else time.monotonic() + float(timeout)
    timed_out = False
    try:
        proc.wait(timeout=timeout)
        for t in readers:
            t.join(None if deadline is None else max(0.0, deadline - time.monotonic()))
            if t.is_alive():
                raise subprocess.TimeoutExpired(getattr(proc, "args", ""), timeout or 0)
    except subprocess.TimeoutExpired:
        timed_out = True
        kill_tree(proc)
        _bounded_wait(proc, drain_timeout)
        end = time.monotonic() + drain_timeout
        for t in readers:
            t.join(max(0.0, end - time.monotonic()))
    if not any(t.is_alive() for t in readers):
        for fh in (proc.stdout, proc.stderr):
            with contextlib.suppress(Exception):
                if fh is not None:
                    fh.close()
    return b"".join(sinks["out"]), b"".join(sinks["err"]), timed_out


def _wait_moving(proc: Any, timeout: float | None, fds: Sequence[int], fn: Callable[[], None]) -> None:
    """``proc.wait(timeout=timeout)`` for a child writing to FILES, looking
    at their sizes every :data:`MOVING_LOOK_S`: a file that grew calls ``fn``
    (v1.392.0, ``core/progress``). Raises ``TimeoutExpired`` at the deadline
    like ``wait``."""
    mark = _progress.safe(fn)

    def _sizes() -> list[int]:
        out = []
        for fd in fds:
            try:
                out.append(os.fstat(fd).st_size)
            except OSError:
                out.append(-1)
        return out

    seen = _sizes()
    deadline = None if timeout is None else time.monotonic() + float(timeout)
    while True:
        step = MOVING_LOOK_S
        if deadline is not None:
            left = deadline - time.monotonic()
            if left <= 0:
                raise subprocess.TimeoutExpired(getattr(proc, "args", ""), timeout or 0)
            step = min(step, left)
        try:
            proc.wait(timeout=step)
            return
        except subprocess.TimeoutExpired:
            now = _sizes()
            if now != seen:
                seen = now
                mark()


def _capture_file(kind: str) -> tuple[int, int]:
    """``(write fd, read fd)`` of a new PRIVATE temporary file in the system
    temp folder (never the user's project, never the app's home, which a
    backup walks). On Windows it is opened delete-on-close
    (``O_TEMPORARY``): the file is gone the moment the last handle to it
    closes — ours after the read, a background job's when that job ends — so
    nothing is ever left behind, whoever closes last. The two fds are
    separate opens (separate positions): reading never moves where a child
    still writing writes. Elsewhere the file is unlinked at once (open fds
    keep it)."""
    import secrets
    import tempfile

    name = os.path.join(
        tempfile.gettempdir(), f"ij-out-{os.getpid()}-{secrets.token_hex(8)}.{kind}"
    )
    flags = getattr(os, "O_BINARY", 0) | getattr(os, "O_NOINHERIT", 0) | getattr(os, "O_TEMPORARY", 0)
    w = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | flags, 0o600)
    try:
        r = os.open(name, os.O_RDONLY | flags)
    except BaseException:
        os.close(w)
        raise
    if not getattr(os, "O_TEMPORARY", 0):
        try:
            os.unlink(name)
        except OSError:
            pass
    return w, r


def _read_fd(fd: int) -> bytes:
    """Everything written to the file so far (from the read fd's own start)."""
    chunks: list[bytes] = []
    while True:
        try:
            data = os.read(fd, 1 << 20)
        except OSError:
            break
        if not data:
            break
        chunks.append(data)
    return b"".join(chunks)


def run_to_files(
    argv: Sequence[str],
    *,
    timeout: float | None,
    cwd: Any = None,
    env: dict[str, str] | None = None,
    background: bool | None = None,
    no_window: bool = True,
    keep_windows: bool = False,
    drain_timeout: float = DRAIN_TIMEOUT_S,
    on_spawn: Callable[[Any], bool] | None = None,
    cancelled: Callable[[], bool] | None = None,
    startupinfo: Any = None,
    breakaway_ok: bool = True,
) -> Result:
    """:func:`run`, with the child's output written to private temporary
    FILES and the call waiting for the CHILD to exit — not for its output to
    close (v1.386.0 review, Git for Windows' bash for the IJ harness's Claude
    dialect: Claude Code itself runs its Bash tool so).

    Why: a shell command that leaves a job running in the background with
    its output not redirected (``python server.py &`` — what a model trained
    on Claude Code writes, where it returns at once) keeps a PIPE open, so
    :func:`run`'s ``communicate`` waited until the timeout and then the tree
    kill ended the server the model meant to keep (measured). With files the
    call returns when the shell exits, the job keeps running — owned by the
    session like any job a keep-windows call leaves (``keep_windows``:
    :func:`release` hands it to the keeper, :func:`end_session` /
    :func:`end_all` stop it) — and whatever it prints later goes to a file
    nobody reads, deleted when it ends.

    The same contract as :func:`run` otherwise: stdin is DEVNULL, a timeout
    kills the TREE (the child is then waited for at most ``drain_timeout``)
    and returns what was written with ``timed_out``, ``on_spawn`` /
    ``cancelled`` are :func:`run_async`'s seams, output is BYTES. Every other
    caller keeps :func:`run`."""
    out_w, out_r = _capture_file("out")
    try:
        err_w, err_r = _capture_file("err")
    except BaseException:
        os.close(out_w)
        os.close(out_r)
        raise
    try:
        try:
            proc = spawn(
                argv,
                background=background,
                no_window=no_window,
                keep_windows=keep_windows,
                **({} if breakaway_ok else {"breakaway_ok": False}),
                cwd=None if cwd is None else str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out_w,
                stderr=err_w,
                **({"startupinfo": startupinfo} if startupinfo is not None else {}),
            )
        finally:
            # The child holds its own copies; ours would only keep the files
            # alive past the last writer.
            os.close(out_w)
            os.close(err_w)
        ended_early = True  # a timeout, a cancel or an error: stop what it left
        try:
            if on_spawn is not None and on_spawn(proc):
                kill_tree(proc)
                _bounded_wait(proc, drain_timeout)
                return Result(-1, b"", b"", cancelled=True, args=argv, timeout=timeout)
            try:
                # v1.392.0: a watched turn's command is looked at as it runs
                # (its output files growing are progress — core/progress).
                moving = _progress.current()
                if moving is not None:
                    _wait_moving(proc, timeout, (out_r, err_r), moving)
                else:
                    proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_tree(proc)
                _bounded_wait(proc, drain_timeout)
                code = proc.returncode if proc.returncode is not None else -1
                return Result(code, _read_fd(out_r), _read_fd(err_r), timed_out=True, args=argv,
                              timeout=timeout)
            except BaseException:
                kill_tree(proc)
                raise
            ended_early = bool(cancelled is not None and cancelled())
            return Result(proc.returncode, _read_fd(out_r), _read_fd(err_r), args=argv, timeout=timeout)
        finally:
            release(proc, stop_windowless=ended_early)
    finally:
        os.close(out_r)
        os.close(err_r)


def _bounded_wait(proc: Any, drain_timeout: float) -> None:
    """Wait for a killed child, never unbounded."""
    try:
        proc.wait(timeout=drain_timeout)
    except Exception:  # noqa: BLE001 — a wedged child still returns
        pass


def _bounded_drain(proc: Any, drain_timeout: float) -> tuple[bytes, bytes]:
    """The second ``communicate`` after a kill — never an unbounded wait."""
    try:
        out, err = proc.communicate(timeout=drain_timeout)
        return out or b"", err or b""
    except Exception:  # noqa: BLE001 — a wedged drain still returns
        return b"", b""


async def run_async(argv: Sequence[str] | str, **kw: Any) -> Result:
    """:func:`run` off the loop — and the child dies with the await.

    A cancelled await does not cancel a thread (the v1.228.0 trap): Stop, a
    closed tab or a deadline used to leave the child running to its own
    timeout. The spawn happens in the worker thread; the handle is published
    under a lock, so a cancel that lands before the spawn finishes is
    honoured by the thread itself, and one that lands after kills the tree
    from here (shielded, so a second cancel cannot abandon it half-way). The
    CancelledError is always re-raised."""
    lock = threading.Lock()
    box: dict[str, Any] = {"proc": None, "cancelled": False}

    def _published(proc: Any) -> bool:
        with lock:
            box["proc"] = proc
            return bool(box["cancelled"])

    def _cancelled() -> bool:
        with lock:
            return bool(box["cancelled"])

    try:
        return await asyncio.to_thread(
            run, argv, on_spawn=_published, cancelled=_cancelled, **kw
        )
    except asyncio.CancelledError:
        with lock:
            box["cancelled"] = True
            proc = box["proc"]
        if proc is not None:
            try:
                await asyncio.shield(asyncio.to_thread(kill_tree, proc))
            except asyncio.CancelledError:
                pass
        raise
