"""What a Build pane started in the background stops when the pane closes (v1.333.0).

THE HOLE. Closing a ConPTY pane closes its pseudoconsole, which ends every
process still ATTACHED to that console (``backend.ConPtyBackend``). A process
that LEFT the console survived: ``Start-Process``, a detached or
``windowsHide`` node child, a dev server started hidden. Nothing could see it
again — no pane, no tail, no way to stop it from the app — and it outlived the
pane, the daemon, and days of uptime.

THE USER'S DECISION (2026-10-11, "Stop them"): closing a pane stops the HIDDEN
background processes it started, and NEVER a process with a visible window.
``code .``, a browser, an app the pane opened stays open.

THE MECHANISM. Each ConPTY shell is created INSIDE its own Job object
(``PROC_THREAD_ATTRIBUTE_JOB_LIST`` in the same attribute list as the
pseudoconsole, so there is no instant in which the shell runs outside it), and
everything it starts joins the job — including what leaves its console. The
job has NO kill-on-close: closing it must never take a window with it, and a
daemon crash must not kill anything by itself. ``JOB_OBJECT_LIMIT_BREAKAWAY_OK``
lets a program that EXPLICITLY asks to break away do so (without it, that
program's CreateProcess would fail inside a pane).

On pane close (and ``kill_all`` at shutdown) the job's members are listed and
:func:`hidden_ones` decides which to stop. A member OWNS a window when it owns
a visible top-level window, when its console's host does (a console program in
a visible console window — the window belongs to conhost.exe), when the
host's parent does (a console tab in Windows Terminal), or when its console is
one a terminal draws (:func:`console_windows`: Windows 11 hands every new
console window to Windows Terminal, so ``Start-Process cmd`` runs in a tab
whose program owns no visible window at all). Spared: every owner,
every member below one (a browser's or VS Code's helper processes own no
window of their own), and every member above one (a launcher: measured, a Tk
window started through the venv's ``pythonw.exe`` launcher died when the
windowless launcher was stopped). Everything else is stopped.

A DAEMON CRASH runs no close. The snapshot records each pane's background
members as ``(pid, start time)`` — only those NOT attached to the pane's own
console, which die with it — and the next boot stops the exact matches that
are still hidden (:func:`stop_leftovers`). A pid alone is never trusted:
Windows reuses them.

Windows-only; every function here is a no-op elsewhere and never raises.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

log = logging.getLogger(__name__)

_WINDOWS = sys.platform == "win32"

#: ``IRONJARVIS_PANE_JOB=off`` starts panes with no job (the v1.332.0
#: behaviour) — the escape hatch if a program refuses to run inside one.
PANE_JOB_ENV = "IRONJARVIS_PANE_JOB"

PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
_JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_JOB_OBJECT_BASIC_PROCESS_ID_LIST = 3
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_CONSOLE_HOST_PROCESS = 49  # PROCESSINFOCLASS

#: How many times a close re-lists the job: a member stopped mid-spawn can
#: leave a child that joined after the first list.
SWEEP_PASSES = 3

#: Start times closer than this are the same process (they come from the
#: same FILETIME; a reused pid starts measurably later).
_SAME_START_S = 0.001


@dataclass(frozen=True)
class Member:
    """One job member as the decision needs it."""

    pid: int
    ppid: int
    started: float
    #: The pid of the console host (conhost.exe / OpenConsole.exe) its
    #: console lives in, 0 when it has none (a GUI program).
    host: int = 0
    #: The host's own parent: Windows Terminal for a console tab in it.
    host_parent: int = 0


def jobs_wanted() -> bool:
    return _WINDOWS and os.environ.get(PANE_JOB_ENV, "").strip().lower() != "off"


def _parent(members: Mapping[int, Member], m: Member) -> Member | None:
    """``m``'s parent when it is a member too. Only a parent that started
    BEFORE the child counts — a dead parent's pid can be reused by a later
    member."""
    parent = members.get(m.ppid)
    if parent is None or parent.pid == m.pid or parent.started > m.started:
        return None
    return parent


def hidden_ones(
    members: Mapping[int, Member],
    visible: set[int],
    consoles: Mapping[int, bool] | None = None,
    own_host: int = 0,
) -> list[int]:
    """The members to STOP: every one that is not spared. Pure — the caller
    supplies the process facts.

    SPARED: a member that owns a visible window (or whose console's host or
    the host's parent does — a console window, a Windows Terminal tab, or a
    console :func:`console_windows` says is SHOWN); every member BELOW one (a
    browser's or VS Code's helpers own no window); and every member ABOVE
    one — a launcher (a venv's ``pythonw.exe``, a .exe shim) often holds its
    program in a kill-on-close job of its own, so stopping the windowless
    launcher closes the window (measured: a Tk window started through the
    venv launcher died with it). ``own_host`` is the pane's OWN console,
    which is never a window the user sees (its members die with it)."""
    consoles = consoles or {}

    def owns_window(m: Member) -> bool:
        if m.pid in visible:
            return True
        if not m.host or m.host == own_host:
            return False
        return bool(
            m.host in visible
            or (m.host_parent and m.host_parent in visible)
            or consoles.get(m.host)
        )

    def chain(m: Member):
        """``m``, then its member ancestors (bounded)."""
        node: Member | None = m
        for _ in range(64):
            if node is None:
                return
            yield node
            node = _parent(members, node)

    owners = {m.pid for m in members.values() if owns_window(m)}
    # An owner's launchers, up the chain. (Only the CHAIN: a launcher's other
    # children are as hidden as anything else.)
    launchers = {n.pid for pid in owners for n in chain(members[pid])}
    return sorted(
        pid
        for pid, m in members.items()
        if pid not in launchers and not any(n.pid in owners for n in chain(m))
    )


# --------------------------------------------------------------------------- #
# Windows calls                                                               #
# --------------------------------------------------------------------------- #

_API = None


def _api():
    global _API
    if _API is not None:
        return _API
    import ctypes
    from ctypes import wintypes
    from types import SimpleNamespace

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u = ctypes.WinDLL("user32", use_last_error=True)
    nt = ctypes.WinDLL("ntdll")
    vp = ctypes.c_void_p
    k.CreateJobObjectW.restype = vp
    k.CreateJobObjectW.argtypes = [vp, wintypes.LPCWSTR]
    k.SetInformationJobObject.restype = wintypes.BOOL
    k.SetInformationJobObject.argtypes = [vp, ctypes.c_int, vp, wintypes.DWORD]
    k.QueryInformationJobObject.restype = wintypes.BOOL
    k.QueryInformationJobObject.argtypes = [vp, ctypes.c_int, vp, wintypes.DWORD, vp]
    k.CloseHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = [vp]
    k.OpenProcess.restype = vp
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    nt.NtQueryInformationProcess.restype = ctypes.c_long
    nt.NtQueryInformationProcess.argtypes = [vp, ctypes.c_int, vp, wintypes.ULONG, vp]
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    u.EnumWindows.restype = wintypes.BOOL
    u.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    u.IsWindowVisible.restype = wintypes.BOOL
    u.IsWindowVisible.argtypes = [wintypes.HWND]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetClassNameW.restype = ctypes.c_int
    u.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

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

    class _EXTENDED(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _BASIC),
            ("IoInfo", ctypes.c_uint64 * 6),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    _API = SimpleNamespace(
        ctypes=ctypes, wintypes=wintypes, k32=k, user32=u, ntdll=nt,
        enum_proc=enum_proc, EXTENDED=_EXTENDED,
    )
    return _API


def create_job() -> int | None:
    """A new, unnamed job for one pane: breakaway allowed, nothing killed on
    close. None when jobs are off or unavailable — the pane then starts
    without one, exactly as before v1.333.0."""
    if not jobs_wanted():
        return None
    try:
        api = _api()
        job = api.k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = api.EXTENDED()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_BREAKAWAY_OK
        if not api.k32.SetInformationJobObject(
            job, _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            api.ctypes.byref(info), api.ctypes.sizeof(info),
        ):
            api.k32.CloseHandle(job)
            return None
        return int(job)
    except Exception:  # noqa: BLE001 — a pane never fails for want of a job
        log.debug("could not create a pane job", exc_info=True)
        return None


def close_job(job: int | None) -> None:
    """Close the handle. With no kill-on-close this kills nothing: a member
    the sweep spared (a window) keeps running."""
    if not job or not _WINDOWS:
        return
    try:
        _api().k32.CloseHandle(job)
    except Exception:  # noqa: BLE001
        pass


def job_pids(job: int | None) -> list[int]:
    """The processes in the job right now (empty on any failure)."""
    if not job or not _WINDOWS:
        return []
    try:
        api = _api()
        ctypes = api.ctypes
        size = 64
        while size <= 65536:
            class _LIST(ctypes.Structure):
                _fields_ = [
                    ("NumberOfAssignedProcesses", ctypes.c_uint32),
                    ("NumberOfProcessIdsInList", ctypes.c_uint32),
                    ("ProcessIdList", ctypes.c_size_t * size),
                ]

            buf = _LIST()
            ok = api.k32.QueryInformationJobObject(
                job, _JOB_OBJECT_BASIC_PROCESS_ID_LIST,
                ctypes.byref(buf), ctypes.sizeof(buf), None,
            )
            if ok or buf.NumberOfAssignedProcesses <= size:
                n = min(buf.NumberOfProcessIdsInList, size)
                return [int(buf.ProcessIdList[i]) for i in range(n)]
            size *= 4  # ERROR_MORE_DATA: a bigger list
    except Exception:  # noqa: BLE001
        log.debug("could not list a pane job", exc_info=True)
    return []


def visible_window_pids() -> set[int]:
    """Every process that owns a visible top-level window on this desktop."""
    if not _WINDOWS:
        return set()
    api = _api()
    out: set[int] = set()
    pid = api.wintypes.DWORD()

    def each(hwnd, _lparam):
        try:
            if api.user32.IsWindowVisible(hwnd):
                api.user32.GetWindowThreadProcessId(hwnd, api.ctypes.byref(pid))
                if pid.value:
                    out.add(int(pid.value))
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        api.user32.EnumWindows(api.enum_proc(each), 0)
    except Exception:  # noqa: BLE001
        log.debug("could not list visible windows", exc_info=True)
    return out


def console_host_pid(pid: int) -> int:
    """The console host (conhost.exe / OpenConsole.exe) of ``pid``'s console,
    or 0 (no console, or not ours to ask)."""
    if not _WINDOWS or not pid:
        return 0
    try:
        api = _api()
        handle = api.k32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return 0
        try:
            value = api.ctypes.c_size_t(0)
            status = api.ntdll.NtQueryInformationProcess(
                handle, _PROCESS_CONSOLE_HOST_PROCESS,
                api.ctypes.byref(value), api.ctypes.sizeof(value), None,
            )
            # The low two bits are flags, not part of the pid.
            return int(value.value) & ~3 if status == 0 else 0
        finally:
            api.k32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return 0


#: A console's own window (conhost) and the window a pseudoconsole keeps for
#: a console a TERMINAL draws (Windows Terminal).
_CONSOLE_WINDOW = "ConsoleWindowClass"
_TERMINAL_CONSOLE_WINDOW = "PseudoConsoleWindow"


def console_windows() -> dict[int, bool]:
    """For each console host with a console window: is that console SHOWN?

    WHY (review, measured on this PC): Windows 11 hands a new console window
    to Windows Terminal (the default terminal). ``Start-Process cmd`` from a
    pane then runs in a Terminal TAB: the program's console host is a
    conhost.exe with no visible window, the tab belongs to
    WindowsTerminal.exe (no relation of the program's), and the only window
    tied to the program is a ``PseudoConsoleWindow`` that is visible only
    while its tab is the one in front. Judged by visible windows alone the
    program was HIDDEN, and closing the pane stopped a window the user could
    see. So a console drawn by a terminal counts as shown, whatever tab is in
    front; a conhost window counts when it is visible (a minimized one is);
    a hidden conhost window (``Start-Process -WindowStyle Hidden``) and a
    console with no window at all (CREATE_NO_WINDOW) are hidden.

    Keyed by the console host's pid (and by the window's owner, which some
    Windows builds report as the host itself). Empty off Windows."""
    if not _WINDOWS:
        return {}
    api = _api()
    out: dict[int, bool] = {}
    hosts: dict[int, int] = {}
    pid = api.wintypes.DWORD()
    name = api.ctypes.create_unicode_buffer(64)

    def each(hwnd, _lparam):
        try:
            if not api.user32.GetClassNameW(hwnd, name, 64):
                return True
            cls = name.value
            if cls not in (_CONSOLE_WINDOW, _TERMINAL_CONSOLE_WINDOW):
                return True
            api.user32.GetWindowThreadProcessId(hwnd, api.ctypes.byref(pid))
            owner = int(pid.value)
            if not owner:
                return True
            shown = cls == _TERMINAL_CONSOLE_WINDOW or bool(api.user32.IsWindowVisible(hwnd))
            if owner not in hosts:
                hosts[owner] = console_host_pid(owner)
            for key in (owner, hosts[owner]):
                if key:
                    out[key] = out.get(key, False) or shown
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        api.user32.EnumWindows(api.enum_proc(each), 0)
    except Exception:  # noqa: BLE001
        log.debug("could not list console windows", exc_info=True)
    return out


def describe(pids: Iterable[int]) -> dict[int, Member]:
    """The facts :func:`hidden_ones` needs, for each pid still running."""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return {}
    out: dict[int, Member] = {}
    hosts: dict[int, int] = {}
    for pid in pids:
        try:
            p = psutil.Process(pid)
            host = console_host_pid(pid)
            if host and host not in hosts:
                try:
                    hosts[host] = psutil.Process(host).ppid()
                except psutil.Error:
                    hosts[host] = 0
            out[pid] = Member(
                pid=pid, ppid=p.ppid(), started=p.create_time(),
                host=host, host_parent=hosts.get(host, 0),
            )
        except Exception:  # noqa: BLE001 — gone, or not ours to read
            continue
    return out


def _stop(pids: Iterable[int], members: Mapping[int, Member]) -> list[int]:
    """Terminate each pid — through psutil, which refuses a pid that now
    belongs to a different process than the one described."""
    import psutil

    stopped: list[int] = []
    for pid in pids:
        try:
            p = psutil.Process(pid)
            if abs(p.create_time() - members[pid].started) > _SAME_START_S:
                continue  # the pid was reused since it was described
            p.kill()
            stopped.append(pid)
        except Exception:  # noqa: BLE001 — already gone, or access denied
            continue
    return stopped


def stop_hidden(job: int | None, *, own_host: int = 0) -> list[int]:
    """Stop every hidden member of ``job`` (re-listing it up to
    :data:`SWEEP_PASSES` times). ``own_host`` is the pane's own console host.
    Returns the pids stopped. Never raises."""
    if not job or not _WINDOWS:
        return []
    stopped: list[int] = []
    try:
        for _ in range(SWEEP_PASSES):
            members = describe(job_pids(job))
            if not members:
                break
            doomed = hidden_ones(
                members, visible_window_pids(), console_windows(), own_host=own_host
            )
            if not doomed:
                break
            stopped += _stop(doomed, members)
    except Exception:  # noqa: BLE001
        log.debug("pane job sweep failed", exc_info=True)
    if stopped:
        log.info("closed pane: stopped %d hidden background process(es)", len(stopped))
    return stopped


def background_members(job: int | None, *, own_host: int, shell_pid: int | None) -> list[list]:
    """What a crash would leave running: the job's members that are NOT
    attached to the pane's own console (those die with it) and are not the
    shell, as ``[pid, start time]`` pairs for the snapshot. Never raises."""
    if not job or not _WINDOWS:
        return []
    out: list[list] = []
    try:
        for m in describe(job_pids(job)).values():
            if m.pid == shell_pid or (own_host and m.host == own_host):
                continue
            out.append([m.pid, m.started])
    except Exception:  # noqa: BLE001
        return []
    return sorted(out)


def _descendants(pids: Iterable[int]) -> set[int]:
    """Every process now running below ``pids`` (not ``pids`` themselves)."""
    import psutil

    roots = set(pids)
    out: set[int] = set()
    for pid in roots:
        try:
            out.update(c.pid for c in psutil.Process(pid).children(recursive=True))
        except psutil.Error:
            continue
    return out - roots


def stop_leftovers(recorded: Iterable) -> list[int]:
    """On boot after a crash: stop each recorded ``[pid, start time]`` that is
    STILL that same process and is still hidden. Never raises."""
    if not _WINDOWS:
        return []
    want: dict[int, float] = {}
    for pair in recorded or ():
        try:
            pid, started = int(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if pid > 0:
            want[pid] = started
    if not want:
        return []
    try:
        matched = {
            pid: m
            for pid, m in describe(want).items()
            if abs(m.started - want[pid]) <= _SAME_START_S
        }
        if not matched:
            return []
        # Their CURRENT descendants take part in the decision but are never
        # stopped here (only a recorded process is): a window opened after
        # the last snapshot must still spare the launcher above it, exactly
        # as the close-time sweep would (review).
        members = {**describe(_descendants(matched)), **matched}
        doomed = [
            pid
            for pid in hidden_ones(members, visible_window_pids(), console_windows())
            if pid in matched
        ]
        stopped = _stop(doomed, members)
    except Exception:  # noqa: BLE001
        log.debug("leftover pane process sweep failed", exc_info=True)
        return []
    if stopped:
        log.info("stopped %d hidden process(es) left by panes before a crash", len(stopped))
    return stopped
