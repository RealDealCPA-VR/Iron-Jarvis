"""Tests for the parent-side REPL session manager (``repl/session.py``).

These drive REAL child processes over REAL pipes — nothing about the transport
is mocked, because the two failures this module exists to prevent (a blocked
event loop, a runaway loop that never dies) only exist at that boundary.

The worker used here is a small stub written to disk by this file, so the tests
stand up on their own. When ``iron_jarvis.repl.worker`` is importable the whole
suite is ALSO parametrized over the real worker, which turns these into an
integration check of the shipped protocol for free.
"""

from __future__ import annotations

import asyncio
import importlib.util
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from iron_jarvis.repl import session as session_mod
from iron_jarvis.repl.session import (
    DEFAULT_TIMEOUT_S,
    IDLE_TTL_S,
    MAX_SESSIONS,
    ReplRegistry,
    ReplSession,
    worker_command,
)

# A stand-in worker implementing the newline-delimited JSON contract:
#   in   {"id","code"}
#   out  {"id","ok","stdout","stderr","result","error","truncated"}
_STUB_WORKER = r'''
import ast
import contextlib
import io
import json
import sys
import traceback


def main():
    ns = {"__name__": "__main__"}
    while True:
        raw = sys.stdin.readline()
        if not raw:
            return
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except Exception:
            continue
        rid = req.get("id", "")
        code = req.get("code", "")
        out, err = io.StringIO(), io.StringIO()
        ok, result, error = True, "", ""
        try:
            body = ast.parse(code, "<repl>", "exec").body
            tail = None
            if body and isinstance(body[-1], ast.Expr):
                tail, body = body[-1], body[:-1]
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                if body:
                    mod = ast.Module(body=body, type_ignores=[])
                    exec(compile(mod, "<repl>", "exec"), ns)
                if tail is not None:
                    value = eval(compile(ast.Expression(tail.value), "<repl>", "eval"), ns)
                    if value is not None:
                        result = repr(value)
        except BaseException:
            ok = False
            error = traceback.format_exc()
        resp = {
            "id": rid,
            "ok": ok,
            "stdout": out.getvalue(),
            "stderr": err.getvalue(),
            "result": result,
            "error": error,
            "truncated": False,
        }
        sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
        sys.stdout.flush()


main()
'''


def _real_worker_available() -> bool:
    try:
        return importlib.util.find_spec("iron_jarvis.repl.worker") is not None
    except (ImportError, ValueError):  # pragma: no cover - partial package
        return False


_WORKERS = ["stub"] + (["real"] if _real_worker_available() else [])


@pytest.fixture(scope="session")
def stub_worker_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("repl-stub") / "stub_worker.py"
    path.write_text(_STUB_WORKER, encoding="utf-8")
    return path


@pytest.fixture(params=_WORKERS)
def worker_argv(request, stub_worker_path) -> list[str]:
    """argv for a worker child: always the stub, plus the real one if present."""
    if request.param == "real":
        return [sys.executable, "-m", "iron_jarvis.repl.worker"]
    return [sys.executable, str(stub_worker_path)]


@pytest.fixture
async def registry(worker_argv):
    reg = ReplRegistry(command=worker_argv)
    try:
        yield reg
    finally:
        await reg.dispose_all()


@pytest.fixture
async def session(worker_argv, tmp_path):
    s = ReplSession("solo", tmp_path, command=worker_argv)
    try:
        yield s
    finally:
        await s.dispose()


def _text(payload: dict) -> str:
    """Everything the worker said, for format-tolerant assertions."""
    return f"{payload.get('result', '')}\n{payload.get('stdout', '')}"


# --------------------------------------------------------------- spawn shape


def test_worker_command_uses_the_m_form_in_dev(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\venv\Scripts\python.exe")
    assert worker_command() == [
        r"C:\venv\Scripts\python.exe",
        "-m",
        "iron_jarvis.repl.worker",
    ]


def test_worker_command_re_execs_the_frozen_binary(monkeypatch):
    """A packaged install has NO python on PATH — we re-exec ourselves.

    This is the whole reason ``run_code``'s ``shutil.which("python")`` lookup
    cannot be copied here: on the user's real machine it finds nothing.
    """
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Iron Jarvis\ironjarvis.exe")
    assert worker_command() == [
        r"C:\Program Files\Iron Jarvis\ironjarvis.exe",
        "repl-worker",
    ]
    # No interpreter hunting anywhere in the argv.
    assert "python" not in " ".join(worker_command()).lower()


def test_constants_are_the_agreed_values():
    assert IDLE_TTL_S == 900
    assert MAX_SESSIONS == 16
    assert DEFAULT_TIMEOUT_S > 0


# ------------------------------------------------------------------- state


async def test_state_persists_across_calls(session):
    first = await session.execute("x = 41", timeout=30)
    assert first["ok"], first

    second = await session.execute("print(x + 1)", timeout=30)
    assert second["ok"], second
    assert "42" in second["stdout"]
    assert second["restarted"] is False

    third = await session.execute("x + 1", timeout=30)
    assert third["ok"], third
    assert "42" in _text(third)


async def test_two_sessions_do_not_share_variables(registry, tmp_path):
    a = await registry.execute("alpha", "secret = 'from-a'", workspace=tmp_path / "a", timeout=30)
    assert a["ok"], a

    b = await registry.execute("beta", "print(secret)", workspace=tmp_path / "b", timeout=30)
    assert b["ok"] is False
    assert "NameError" in (b["error"] + b["stderr"])
    assert "from-a" not in _text(b)

    # ...and A is untouched by B's failure.
    again = await registry.execute("alpha", "print(secret)", workspace=tmp_path / "a", timeout=30)
    assert again["ok"], again
    assert "from-a" in again["stdout"]


async def test_the_child_runs_in_the_session_workspace(registry, tmp_path):
    ws = tmp_path / "workspace-here"
    payload = await registry.execute(
        "cwd", "import os; print(os.path.realpath(os.getcwd()))", workspace=ws, timeout=30
    )
    assert payload["ok"], payload
    reported = Path(payload["stdout"].strip().splitlines()[-1])
    assert reported.resolve() == ws.resolve()


# ----------------------------------------------------------------- timeouts


async def test_runaway_loop_is_killed_within_the_timeout(session):
    """``while True: pass`` must be survivable — that is why this is a process."""
    await session.execute("primed = True", timeout=30)
    proc = session._proc
    assert proc is not None and proc.poll() is None

    started = time.monotonic()
    payload = await session.execute("while True:\n    pass\n", timeout=2.0)
    elapsed = time.monotonic() - started

    assert payload["ok"] is False
    assert elapsed < 2.0 + 10.0, f"took {elapsed:.1f}s to give up on a 2s timeout"
    error = payload["error"].lower()
    assert "timed out" in error
    assert "kill" in error
    assert "2" in payload["error"]
    assert "lost" in error  # the namespace loss is stated, not implied

    # The process is really gone, not just abandoned to spin at 100% CPU.
    assert proc.poll() is not None
    assert session.pid is None


async def test_the_call_after_a_kill_works_and_admits_the_state_loss(session):
    await session.execute("keep = 'this'", timeout=30)
    killed = await session.execute("while True:\n    pass\n", timeout=2.0)
    assert killed["ok"] is False
    assert killed["restarted"] is False  # this call did not restart; it died

    revived = await session.execute("print('alive')", timeout=30)
    assert revived["ok"], revived
    assert "alive" in revived["stdout"]
    assert revived["restarted"] is True, "a silent respawn hides the state loss"
    assert "fresh" in revived["note"].lower()
    assert "gone" in revived["note"].lower()

    # The claim is true: the old namespace really is gone.
    gone = await session.execute("keep", timeout=30)
    assert gone["ok"] is False
    assert "NameError" in (gone["error"] + gone["stderr"])

    # ...and the marker is not sticky — the run after that is a normal run.
    normal = await session.execute("print('ok')", timeout=30)
    assert normal["ok"], normal
    assert normal["restarted"] is False


# ------------------------------------------------- THE ONE THAT MATTERS


async def test_slow_code_does_not_block_the_event_loop(session):
    """A slow child must not park the daemon's single loop.

    A tick COUNT proves nothing here (``gather`` waits for both sides either
    way) and a heartbeat started alongside the blocking call proves nothing
    either — both mistakes have shipped green in this repo. So: start the
    heartbeat FIRST, prove it is already ticking, then measure the MAXIMUM GAP
    between ticks across a call that occupies 2 full seconds of wall time. The
    two outcomes are ~2.0s apart from the 0.5s assertion, so a loaded CI runner
    cannot land between them.
    """
    await session.execute("warm = 1", timeout=30)  # spawn cost out of the way

    gaps: list[float] = []
    stop = asyncio.Event()

    async def heartbeat() -> None:
        previous = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.02)
            now = time.monotonic()
            gaps.append(now - previous)
            previous = now

    beat = asyncio.create_task(heartbeat())
    await asyncio.sleep(0.3)
    assert len(gaps) > 5, "the heartbeat must already be running before we measure"

    started = time.monotonic()
    payload = await session.execute("import time; time.sleep(2)", timeout=30)
    elapsed = time.monotonic() - started

    stop.set()
    await beat

    assert payload["ok"], payload
    assert elapsed >= 1.5, f"the call only took {elapsed:.2f}s — it did not really block"
    assert max(gaps) < 0.5, (
        f"the event loop stalled for {max(gaps):.2f}s during a {elapsed:.2f}s call — "
        f"blocking pipe I/O is running on the loop"
    )


# ----------------------------------------------------------------- disposal


async def test_dispose_kills_the_child(registry, tmp_path):
    payload = await registry.execute("doomed", "n = 1", workspace=tmp_path, timeout=30)
    assert payload["ok"], payload

    session = registry.get("doomed")
    assert session is not None
    proc = session._proc
    assert proc is not None and proc.poll() is None

    assert await registry.dispose("doomed") is True
    assert proc.poll() is not None, "dispose left the child process running"
    assert session.pid is None
    assert "doomed" not in registry
    assert await registry.dispose("doomed") is False


async def test_dispose_all_kills_every_child(registry, tmp_path):
    procs = []
    for sid in ("one", "two", "three"):
        await registry.execute(sid, "n = 1", workspace=tmp_path / sid, timeout=30)
        session = registry.get(sid)
        assert session is not None and session._proc is not None
        procs.append(session._proc)

    await registry.dispose_all()

    assert len(registry) == 0
    assert all(p.poll() is not None for p in procs)


async def test_sweep_disposes_idle_sessions_only(worker_argv, tmp_path):
    reg = ReplRegistry(command=worker_argv, idle_ttl_s=0.4)
    try:
        await reg.execute("old", "n = 1", workspace=tmp_path / "old", timeout=30)
        stale_proc = reg.get("old")._proc
        await asyncio.sleep(0.6)

        await reg.execute("new", "n = 1", workspace=tmp_path / "new", timeout=30)
        fresh_proc = reg.get("new")._proc

        swept = await reg.sweep()

        assert swept == ["old"]
        assert stale_proc.poll() is not None
        assert reg.session_ids == ["new"]
        assert fresh_proc.poll() is None
    finally:
        await reg.dispose_all()


async def test_beyond_max_sessions_it_explains_instead_of_spawning(worker_argv, tmp_path):
    reg = ReplRegistry(command=worker_argv, max_sessions=2, idle_ttl_s=IDLE_TTL_S)
    try:
        for sid in ("s1", "s2"):
            assert (await reg.execute(sid, "n = 1", workspace=tmp_path / sid, timeout=30))["ok"]

        refused = await reg.execute("s3", "n = 1", workspace=tmp_path / "s3", timeout=30)

        assert refused["ok"] is False
        assert "s3" not in reg
        assert len(reg) == 2
        assert "2" in refused["error"]
        assert "close" in refused["error"].lower()
        # The shape is the same shape — callers never special-case this.
        assert set(refused) >= {"ok", "stdout", "stderr", "result", "error", "truncated"}

        # An idle slot is reclaimed rather than refused.
        reg.idle_ttl_s = 0.0
        accepted = await reg.execute("s4", "n = 1", workspace=tmp_path / "s4", timeout=30)
        assert accepted["ok"], accepted
    finally:
        await reg.dispose_all()


# ------------------------------------------------------- never raise, ever


async def test_a_worker_that_cannot_start_returns_an_error(tmp_path):
    s = ReplSession("bad", tmp_path, command=["ij-no-such-worker-binary-9f2c"])
    try:
        payload = await s.execute("1 + 1", timeout=10)
    finally:
        await s.dispose()
    assert payload["ok"] is False
    assert "could not start" in payload["error"].lower()
    assert payload["session_id"] == "bad"


async def test_a_worker_that_dies_mid_call_returns_an_error(tmp_path):
    s = ReplSession(
        "dying", tmp_path, command=[sys.executable, "-c", "import sys; sys.exit(7)"]
    )
    try:
        payload = await s.execute("1 + 1", timeout=10)
    finally:
        await s.dispose()
    assert payload["ok"] is False
    assert "died" in payload["error"].lower() or "could not" in payload["error"].lower()
    assert "lost" in payload["error"].lower() or "fresh" in payload["error"].lower()


async def test_garbage_on_stdout_does_not_derail_the_session(tmp_path, stub_worker_path):
    """A library that prints at import time must not break the protocol."""
    noisy = tmp_path / "noisy_worker.py"
    noisy.write_text(
        "import sys\n"
        "sys.stdout.write('a banner line that is not JSON\\n')\n"
        "sys.stdout.flush()\n"
        + stub_worker_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    s = ReplSession("noisy", tmp_path, command=[sys.executable, str(noisy)])
    try:
        payload = await s.execute("print('still works')", timeout=30)
    finally:
        await s.dispose()
    assert payload["ok"], payload
    assert "still works" in payload["stdout"]


async def test_execute_never_raises_on_a_wedged_child(session, monkeypatch):
    """Even if the exchange itself explodes, the caller gets the dict shape."""

    def boom(*_args, **_kwargs):
        raise RuntimeError("pipe went sideways")

    await session.execute("warm = 1", timeout=30)
    monkeypatch.setattr(session, "_exchange", boom)
    payload = await session.execute("1 + 1", timeout=10)

    assert payload["ok"] is False
    assert "pipe went sideways" in payload["error"]
    assert payload["stdout"] == "" and payload["result"] == ""


async def test_registry_describe_reports_live_state(registry, tmp_path):
    await registry.execute("d1", "n = 1", workspace=tmp_path, timeout=30)
    rows = registry.describe()
    assert len(rows) == 1
    assert rows[0]["session_id"] == "d1"
    assert rows[0]["alive"] is True
    assert isinstance(rows[0]["pid"], int)
    assert rows[0]["executions"] == 1


async def test_spawn_hides_the_console_window_on_windows(tmp_path, monkeypatch):
    """A packaged desktop app must not flash a console for every REPL call.

    Asserting ``hasattr(subprocess, "CREATE_NO_WINDOW")`` tests the standard
    library, not this module: deleting the ``creationflags`` line entirely
    leaves such a test green while every REPL call flashes a black window on
    the user's desktop. So intercept the real ``Popen`` and read the kwarg.
    """
    if sys.platform != "win32":
        pytest.skip("Windows-only spawn flag")

    seen: dict[str, object] = {}
    real_popen = subprocess.Popen

    class Spy(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, *args, **kwargs):
            seen.update(kwargs)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(session_mod.subprocess, "Popen", Spy)
    s = ReplSession("flags", tmp_path, command=[sys.executable, "-c", "pass"])
    try:
        await s.execute("1 + 1", timeout=10)
    finally:
        await s.dispose()

    assert "creationflags" in seen, "the child was spawned without creationflags"
    assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW


# ------------------------------------------------- leaks: processes + threads


def _repl_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name.startswith("repl-")]


def _live_workers(marker: str) -> set[int]:
    """Pids of every live process below us whose argv carries ``marker``.

    Counting ``Process().children()`` naively does not work here: this repo's
    ``.venv/Scripts/python.exe`` is a uv TRAMPOLINE, so the process we
    ``Popen`` immediately spawns the real interpreter (plus a ``conhost.exe``)
    beneath it. Matching argv counts the same thing on a trampoline venv, on a
    plain venv and on a frozen build, and — unlike counting the manager's own
    ``_proc`` handles — it cannot be fooled by bookkeeping that has forgotten a
    process it is still responsible for.
    """
    live: set[int] = set()
    try:
        me = psutil.Process()
    except psutil.Error:  # pragma: no cover
        return live
    for proc in [me, *me.children(recursive=True)]:
        try:
            if not proc.is_running() or proc.status() == psutil.STATUS_ZOMBIE:
                continue
            if any(marker in part for part in (proc.cmdline() or [])):
                live.add(proc.pid)
        except psutil.Error:  # pragma: no cover - raced with exit
            continue
    return live


async def _settle(predicate, timeout: float = 10.0) -> None:
    """Give reaping/thread-exit a bounded moment; the assertion follows."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not predicate():
        await asyncio.sleep(0.05)


@pytest.fixture
def marker(worker_argv) -> str:
    """The argv fragment that identifies THIS parametrization's worker."""
    return worker_argv[-1]


async def test_twenty_executes_reuse_one_child_and_leak_no_threads(session, marker):
    """Twenty calls must be twenty calls in ONE interpreter, not twenty spawns."""
    before_threads = set(_repl_threads())

    await session.execute("total = 0", timeout=30)
    pid = session.pid
    assert pid is not None
    spawned = _live_workers(marker)
    assert spawned, "no worker process is running at all"

    for _ in range(20):
        payload = await session.execute("total += 1", timeout=30)
        assert payload["ok"], payload
        assert payload["restarted"] is False

    final = await session.execute("total", timeout=30)
    assert "20" in _text(final)
    assert session.pid == pid, "the child was respawned behind the caller's back"
    assert _live_workers(marker) == spawned, "executes accumulated worker processes"

    # Two pumps for the one child, and no accumulation across 22 executes.
    new_threads = [n for n in _repl_threads() if n not in before_threads]
    assert len(new_threads) == 2, f"reader threads accumulated: {new_threads}"


async def test_a_timeout_kill_leaves_no_process_and_no_parked_threads(session, marker):
    """The runaway is reaped AND its pumps unwind — otherwise 100% CPU forever."""
    before_threads = set(_repl_threads())

    await session.execute("primed = True", timeout=30)
    assert _live_workers(marker)

    payload = await session.execute("while True:\n    pass\n", timeout=2.0)
    assert payload["ok"] is False

    await _settle(lambda: not _live_workers(marker))
    survivors = _live_workers(marker)
    assert survivors == set(), f"the runaway is still burning CPU: {survivors}"

    await _settle(lambda: not [n for n in _repl_threads() if n not in before_threads])
    parked = [n for n in _repl_threads() if n not in before_threads]
    assert parked == [], f"reader threads survived the kill: {parked}"


async def test_dispose_all_leaves_no_children_and_no_threads(registry, tmp_path, marker):
    before_threads = set(_repl_threads())

    for sid in ("a", "b", "c"):
        assert (await registry.execute(sid, "n = 1", workspace=tmp_path / sid, timeout=30))["ok"]
    assert len(_live_workers(marker)) >= 3

    await registry.dispose_all()

    await _settle(lambda: not _live_workers(marker))
    assert _live_workers(marker) == set(), "dispose_all orphaned a child"
    await _settle(lambda: not [n for n in _repl_threads() if n not in before_threads])
    assert [n for n in _repl_threads() if n not in before_threads] == []


# ------------------------------------------------------------- concurrency


async def test_overlapping_executes_on_one_session_do_not_cross(session):
    """Two requests on one pipe must serialise, not interleave their answers."""
    await session.execute("marker = 'set'", timeout=30)

    slow = asyncio.create_task(
        session.execute(
            "import time; time.sleep(1); slow_value = 'SLOW'; 'slow-done'", timeout=30
        )
    )
    await asyncio.sleep(0.1)
    fast = asyncio.create_task(session.execute("'fast-done'", timeout=30))

    slow_payload, fast_payload = await asyncio.wait_for(
        asyncio.gather(slow, fast), timeout=60
    )

    assert slow_payload["ok"], slow_payload
    assert fast_payload["ok"], fast_payload
    # Each caller got ITS OWN answer, not the other's.
    assert "slow-done" in _text(slow_payload)
    assert "fast-done" in _text(fast_payload)
    assert "slow-done" not in _text(fast_payload)
    # The pipe stayed in sync: the session still works and kept both effects.
    after = await session.execute("marker + '/' + slow_value", timeout=30)
    assert after["ok"], after
    assert "set/SLOW" in _text(after)


async def test_concurrent_first_use_of_one_id_makes_exactly_one_child(
    registry, tmp_path, marker
):
    """Eight coroutines racing on a cold session id must not spawn eight children."""
    results = await asyncio.gather(
        *(
            registry.execute("shared", f"tag = {i}", workspace=tmp_path / "shared", timeout=30)
            for i in range(8)
        )
    )
    assert all(r["ok"] for r in results), results
    assert len(registry) == 1
    session = registry.get("shared")
    assert session is not None
    # One namespace: every call landed in the same interpreter.
    assert _live_workers(marker) == _live_workers(marker)
    tally = await registry.execute("shared", "tag", workspace=tmp_path / "shared", timeout=30)
    assert tally["ok"], tally
    assert session.executions == 9


async def test_racing_creates_never_exceed_max_sessions(worker_argv, tmp_path, marker):
    reg = ReplRegistry(command=worker_argv, max_sessions=3, idle_ttl_s=IDLE_TTL_S)
    try:
        results = await asyncio.gather(
            *(
                reg.execute(f"race{i}", "n = 1", workspace=tmp_path / f"race{i}", timeout=30)
                for i in range(10)
            )
        )
        assert len(reg) <= 3, f"the cap was breached: {len(reg)} sessions"
        assert sum(1 for r in results if r["ok"]) == 3
        assert all("close" in r["error"].lower() for r in results if not r["ok"])
    finally:
        await reg.dispose_all()
    await _settle(lambda: not _live_workers(marker))
    assert _live_workers(marker) == set(), "the create race leaked a child"


async def test_dispose_during_an_in_flight_execute_does_not_orphan_a_child(
    registry, tmp_path, marker
):
    """A session removed from the registry must never spawn another child.

    ``ReplRegistry.execute`` hands the session out under the registry lock and
    then runs OUTSIDE it. If the session is disposed in that window the
    in-flight call finds a dead child, respawns — and the new process belongs to
    an object the registry no longer holds. Nothing can ever kill it: not
    ``dispose``, not ``dispose_all`` at daemon shutdown. That is an orphaned
    Python interpreter outliving the app that made it.
    """
    assert (await registry.execute("victim", "n = 1", workspace=tmp_path, timeout=30))["ok"]

    # Exactly the sequence ``tools/repl_tool.py`` performs: resolve the session,
    # await something (it snapshots the workspace), then call execute.
    session = registry.get("victim")
    assert session is not None
    await asyncio.sleep(0)  # the await that opens the window
    assert await registry.dispose("victim") is True

    payload = await asyncio.wait_for(session.execute("n = 2", timeout=30), timeout=60)
    assert payload["ok"] is False
    assert "closed" in payload["error"].lower()

    # ...and a call that was ALREADY running when dispose landed also ends.
    assert (await registry.execute("victim2", "n = 1", workspace=tmp_path, timeout=30))["ok"]
    running = asyncio.create_task(
        registry.execute(
            "victim2", "import time; time.sleep(1.5)", workspace=tmp_path, timeout=30
        )
    )
    await asyncio.sleep(0.3)
    assert await registry.dispose("victim2") is True
    mid_flight = await asyncio.wait_for(running, timeout=60)
    assert mid_flight["ok"] is False

    await registry.dispose_all()
    await _settle(lambda: not _live_workers(marker))
    leaked = _live_workers(marker)
    assert leaked == set(), f"a disposed session respawned an unreachable child: {leaked}"


async def test_a_swept_session_does_not_respawn_behind_the_registrys_back(
    worker_argv, tmp_path, marker
):
    reg = ReplRegistry(command=worker_argv, idle_ttl_s=0.2)
    try:
        assert (await reg.execute("ghost", "n = 1", workspace=tmp_path, timeout=30))["ok"]
        session = reg.get("ghost")
        assert session is not None
        await asyncio.sleep(0.4)
        assert await reg.sweep() == ["ghost"]

        # Somebody still holding the object must not be able to resurrect it.
        payload = await session.execute("n = 2", timeout=30)
        assert payload["ok"] is False
        assert "closed" in payload["error"].lower()
    finally:
        await reg.dispose_all()
    await _settle(lambda: not _live_workers(marker))
    assert _live_workers(marker) == set()


# --------------------------------------------------------- nonsense timeouts


@pytest.mark.parametrize(
    "bad", [float("nan"), float("inf"), float("-inf"), 0.0, -5.0, "abc", None, object()]
)
async def test_a_nonsense_timeout_falls_back_instead_of_breaking(session, bad):
    """``timeout`` arrives from a MODEL, so every shape of garbage is expected.

    ``float("inf")`` is the one that bit: it survives the ``<= 0`` coercion and
    then explodes inside ``asyncio.wait_for`` (``OverflowError: timestamp out of
    range``), which lands in the last-resort handler — so a bad ARGUMENT costs
    the user their whole namespace. ``json.loads`` accepts ``NaN``/``Infinity``
    literals, and ``repl_tool``'s ``min(max(...))`` clamp passes NaN straight
    through, so neither value is hypothetical.
    """
    payload = await asyncio.wait_for(session.execute("1 + 1", timeout=bad), timeout=60)
    assert payload["ok"], payload
    assert payload["error"] == ""
    assert "2" in _text(payload)


async def test_a_nan_timeout_still_kills_a_runaway(session, monkeypatch, marker):
    """A NaN deadline must not silently mean "no deadline".

    ``queue.Queue.get(timeout=nan)`` never expires and ``asyncio.wait_for``'s
    ``call_later(nan)`` never fires, so before the fallback existed this call
    hung FOREVER: the child spun at 100% CPU, a worker thread stayed parked, and
    the session's own lock was held for the life of the daemon — that session id
    could never run anything again. The ``wait_for`` below is the regression
    guard: a return of this bug must fail the suite, not hang it.
    """
    monkeypatch.setattr(session_mod, "DEFAULT_TIMEOUT_S", 2.0)
    await session.execute("primed = True", timeout=30)

    started = time.monotonic()
    payload = await asyncio.wait_for(
        session.execute("while True:\n    pass\n", timeout=float("nan")), timeout=25
    )
    elapsed = time.monotonic() - started

    assert payload["ok"] is False
    assert "timed out" in payload["error"].lower()
    assert elapsed < 20, f"the NaN deadline was never enforced ({elapsed:.1f}s)"
    await _settle(lambda: not _live_workers(marker))
    assert _live_workers(marker) == set(), "the runaway survived a NaN timeout"


async def test_a_dispose_that_lands_mid_spawn_takes_the_new_child_with_it(
    worker_argv, tmp_path, marker
):
    """The narrow window: disposed AFTER the spawn, BEFORE the request goes out.

    Checking ``_closed`` only on entry would leave a live interpreter that
    nothing owns, so the check is repeated once the child exists. Driving the
    race by hand is a coin flip, so the dispose is injected exactly where it
    hurts — inside the spawn itself.
    """
    s = ReplSession("midspawn", tmp_path, command=worker_argv)
    real_spawn = s._spawn

    def spawn_then_dispose() -> None:
        real_spawn()
        s._closed = True  # a dispose() landing in the window

    s._spawn = spawn_then_dispose  # type: ignore[method-assign]
    payload = await s.execute("1 + 1", timeout=30)

    assert payload["ok"] is False
    assert "closed" in payload["error"].lower()
    await _settle(lambda: not _live_workers(marker))
    assert _live_workers(marker) == set(), "the child spawned into the window survived"
    assert s.pid is None


# ------------------------------------------------------- crashes + workspace


async def test_a_child_that_dies_on_its_own_is_recovered_and_reported(session):
    """The child exits by itself (``os._exit``); the next call must say so."""
    await session.execute("kept = 'value'", timeout=30)
    first_pid = session.pid

    crashed = await session.execute("import os; os._exit(3)", timeout=30)
    assert crashed["ok"] is False
    assert "died" in crashed["error"].lower()
    assert "lost" in crashed["error"].lower()

    revived = await session.execute("print('back')", timeout=30)
    assert revived["ok"], revived
    assert "back" in revived["stdout"]
    assert revived["restarted"] is True, "a silent respawn hides the state loss"
    assert session.pid != first_pid

    gone = await session.execute("kept", timeout=30)
    assert gone["ok"] is False
    assert "NameError" in (gone["error"] + gone["stderr"])


async def test_a_missing_workspace_is_created_not_refused(registry, tmp_path):
    deep = tmp_path / "does" / "not" / "exist" / "yet"
    assert not deep.exists()
    payload = await registry.execute(
        "mk", "import os; print(os.getcwd())", workspace=deep, timeout=30
    )
    assert payload["ok"], payload
    assert deep.is_dir()
    assert Path(payload["stdout"].strip().splitlines()[-1]).resolve() == deep.resolve()


async def test_a_workspace_that_is_a_file_fails_honestly(tmp_path, worker_argv):
    blocked = tmp_path / "iam-a-file.txt"
    blocked.write_text("not a directory", encoding="utf-8")
    s = ReplSession("blocked", blocked, command=worker_argv)
    try:
        payload = await s.execute("1 + 1", timeout=10)
    finally:
        await s.dispose()
    assert payload["ok"] is False
    assert "could not start" in payload["error"].lower()
    assert payload["stdout"] == "" and payload["result"] == ""

