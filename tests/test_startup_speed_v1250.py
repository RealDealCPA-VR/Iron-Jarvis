"""v1.250.0 (S-01) — the app opens in a few seconds instead of 6-15.

Two halves, measured on this machine before anything was changed:

* DESKTOP. The boot gates ran one after the other — ``await waitForDaemon``,
  then ``await waitForDashboard`` — and the dashboard answers in ~0.1 s while
  the daemon takes seconds. So the splash waited for the daemon and only THEN
  began probing a dashboard that had been ready the whole time. They run
  together now, and the poll interval dropped from 500 ms to ``GATE_POLL_MS``.
* DAEMON. ``available("opencode-cli")`` resolves its allowlist by shelling out
  to ``opencode models`` — ~1.4 s cold on this machine — and the first caller
  is the first ``GET /health``, which is the desktop's own startup gate.
  ``warm_opencode`` fills that cache on a thread during boot, and
  ``_opencode_allowed`` is SINGLE-FLIGHT so the gate WAITS on that warm
  instead of racing it.

  Frozen build, empty state home, INTERLEAVED and counterbalanced (5 pairs,
  one discarded warm-up per exe) — because cross-session variance on this box
  reaches 2x and swamps the effect, so before/after must alternate in one
  session:

    before          first healthy /health 3.84 s  (gap after startup 1.66 s)
    warm-up ALONE   3.90 s — 0.08 s SLOWER: the gate re-resolved in parallel,
                    two concurrent ``opencode models`` subprocesses
    warm + lock     3.44 s  (gap 1.24 s), paired median -0.42 s, 5/5 pairs

  The isolating measurement: waiting 4 s after "Application startup complete"
  before asking, the first /health costs 46 ms; asking immediately costs
  1,415 ms. So the cost is real and the warm does fill the cache — it just
  cannot finish before the gate arrives, which is what makes the lock, not the
  thread, the part that buys the time.

House idiom (``test_desktop_reliability_v1249.py``): main.js source is lifted
and run under node where it can execute, and the startup wiring — which cannot
run here — is pinned against the source instead.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "desktop" / "main.js"

requires_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")


def _src() -> str:
    # CRLF on the CI runner, LF here (v1.232.1): normalise at the READER.
    return _MAIN.read_text(encoding="utf-8").replace("\r\n", "\n")


def _lift(start_marker: str, end_after: str) -> str:
    src = _src()
    start = src.index(start_marker)
    tail = src.index(end_after, start)
    end = src.index("\n}\n", tail) + 3
    return src[start:end]


def _decl(name: str) -> str:
    m = re.search(rf"^(?:const|let) {name} = [^\n]*;", _src(), re.M)
    assert m, f"main.js has no top-level decl {name}"
    return m.group(0)


def _startup_block() -> str:
    """The boot's gate section: from the health-gate comment to the line that
    marks a clean boot."""
    src = _src()
    start = src.index("// 3) Health-gate the DAEMON")
    end = src.index("clearUpdatePending();", start)
    return src[start:end]


# ==========================================================================
# DESKTOP — the two gates run at the same time
# ==========================================================================


def test_the_two_boot_gates_run_at_the_same_time():
    """Serial gates cost the whole daemon boot before the dashboard is even
    probed. One `allSettled` over both, each at the shipped poll interval."""
    block = _startup_block()
    assert "Promise.allSettled([" in block
    assert re.search(
        r"Promise\.allSettled\(\[\s*waitForDaemon\(STARTUP_TIMEOUT_MS, GATE_POLL_MS\),"
        r"\s*waitForDashboard\(STARTUP_TIMEOUT_MS, GATE_POLL_MS\),?\s*\]\)",
        block,
    ), block


def test_neither_gate_is_awaited_a_second_time():
    """THE MUTATION THIS CATCHES: re-adding `await waitForDashboard(...)` in
    the boot path silently reinstates the serial wait while the allSettled
    above still looks right."""
    block = _startup_block()
    assert "await waitForDashboard(" not in block
    assert "await waitForDaemon(" not in block
    # Both verdicts must still be consumed — an unused gate is a gate that
    # never fails the boot.
    assert "daemonGate.status" in block
    assert "dashboardGate.status" in block


def test_the_daemon_verdict_is_reported_before_the_dashboards():
    """When both fail, the daemon's failure is the one that explains the
    other, and it carries the classifier's evidence (v1.249.0, R-06)."""
    block = _startup_block()
    assert block.index("daemonGate.status") < block.index("dashboardGate.status")
    assert block.index('label: "daemon"') < block.index('label: "dashboard"')


def test_the_tray_only_login_boot_still_skips_the_window():
    """S-01 must not change what a `--hidden` login boot does."""
    src = _src()
    after_gates = src[src.index("clearUpdatePending();") :]
    assert "if (START_HIDDEN && !showWindowWhenReady)" in after_gates
    assert "createMainWindow();" in after_gates


def test_the_gates_poll_faster_than_they_used_to():
    decl = _decl("GATE_POLL_MS")
    value = int(re.search(r"=\s*(\d+)", decl).group(1))
    assert 0 < value <= 200, decl
    assert "500" not in _startup_block(), "a 500 ms gate interval is back"


_POLL_HARNESS = """
const http = {
  get(url, optsOrCb, maybeCb) {
    const req = { on(){ return req; }, setTimeout(){ return req; } };
    // Answer nothing, ever: the gate must keep retrying at its interval.
    setImmediate(() => req._err && req._err());
    const onErr = [];
    req.on = (ev, fn) => { if (ev === "error") { onErr.push(fn); setImmediate(fn); } return req; };
    return req;
  },
};
const DASHBOARD_PROBE_URL = "http://127.0.0.1:1/";
__FN__
const timeoutMs = Number(process.argv[2]);
const intervalMs = Number(process.argv[3]);
let attempts = 0;
const realGet = http.get;
http.get = (...a) => { attempts += 1; return realGet(...a); };
const t0 = Date.now();
waitForDashboard(timeoutMs, intervalMs).then(
  () => { console.log(JSON.stringify({ ok: true, attempts })); },
  (err) => {
    console.log(JSON.stringify({
      ok: false, attempts, elapsed: Date.now() - t0, message: String(err && err.message),
    }));
  }
);
"""


def _run_node(script: str, tmp_path: Path, name: str, *argv: str) -> dict:
    f = tmp_path / f"{name}.js"
    f.write_text(script, encoding="utf-8")
    proc = subprocess.run(
        ["node", str(f), *argv],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        env={**os.environ},
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@requires_node
def test_the_poll_interval_is_really_the_retry_spacing(tmp_path):
    """GATE_POLL_MS is only worth anything if it IS the retry spacing: the
    shipped `waitForDashboard`, lifted verbatim, retries more often at a
    smaller interval over the same window."""
    script = _POLL_HARNESS.replace(
        "__FN__", _lift("function waitForDashboard(", "function waitForDashboard(")
    )
    slow = _run_node(script, tmp_path, "poll_slow", "600", "200")
    fast = _run_node(script, tmp_path, "poll_fast", "600", "50")
    assert slow["ok"] is False and fast["ok"] is False  # nothing ever answers
    assert fast["attempts"] > slow["attempts"], (fast, slow)
    assert "did not answer" in fast["message"]


# ==========================================================================
# DAEMON — the opencode allowlist is warmed off the boot path
# ==========================================================================


def test_warm_opencode_resolves_on_a_thread_and_never_raises():
    from iron_jarvis.providers.manager import ProviderManager

    started = threading.Event()
    where: list[str] = []

    def _resolver() -> list[str]:
        where.append(threading.current_thread().name)
        started.set()
        raise RuntimeError("opencode is not installed")

    mgr = ProviderManager(opencode_allowed=_resolver)
    mgr.warm_opencode()  # must not raise, must not block
    assert started.wait(10), "the warm-up never ran"
    assert where and where[0] != threading.main_thread().name
    # A failure is cached exactly as the lazy path would cache it.
    assert mgr._opencode_allowed() == []


def test_the_warm_up_is_what_makes_the_first_answer_cheap():
    """After the warm, the first real caller does not re-resolve."""
    from iron_jarvis.providers.manager import ProviderManager

    calls: list[int] = []
    done = threading.Event()

    def _resolver() -> list[str]:
        calls.append(1)
        done.set()
        return ["local-model"]

    mgr = ProviderManager(opencode_allowed=_resolver)
    mgr.warm_opencode()
    assert done.wait(10)
    assert mgr._opencode_allowed() == ["local-model"]
    assert len(calls) == 1, "the first caller paid for it again"
    # The user's own setting still re-resolves on demand.
    mgr.refresh_opencode()
    assert mgr._opencode_allowed() == ["local-model"]
    assert len(calls) == 2


def test_a_caller_arriving_mid_warm_waits_instead_of_resolving_again():
    """THE DEFECT THIS CATCHES — it shipped in this very change and only an
    interleaved frozen A/B found it. The cache is written when the shell-out
    RETURNS, so the desktop's first /health, landing while the warm is still
    running, started a SECOND ``opencode models``: measured 1,415 ms racing
    vs 46 ms once warm, and the whole warm-up came out 0.08 s SLOWER than no
    warm-up at all (two concurrent subprocesses).

    Every other warm test here waits for the warm to FINISH before calling,
    so none of them can see this. This one holds the resolver open and calls
    mid-flight.
    """
    from iron_jarvis.providers.manager import ProviderManager

    calls: list[int] = []
    in_flight = threading.Event()
    release = threading.Event()

    def _resolver() -> list[str]:
        calls.append(1)
        in_flight.set()
        assert release.wait(10), "the resolver was never released"
        return ["local-model"]

    mgr = ProviderManager(opencode_allowed=_resolver)
    mgr.warm_opencode()
    assert in_flight.wait(10), "the warm never started resolving"

    # The startup gate arrives while the warm is still shelling out.
    done = threading.Event()
    got: list[list[str]] = []

    def _gate() -> None:
        got.append(mgr._opencode_allowed())
        done.set()

    threading.Thread(target=_gate, daemon=True).start()
    # It must be WAITING on the warm — not running a second resolution. (Both
    # versions block here, so the call count is what actually discriminates.)
    assert not done.wait(0.5)
    assert len(calls) == 1, f"the resolver ran {len(calls)}x — the warm was duplicated"

    release.set()
    assert done.wait(10), "the mid-flight caller was never released"
    assert got == [["local-model"]]
    assert len(calls) == 1, "the waiting caller resolved again after being released"


def test_boot_warms_the_allowlist(tmp_path, monkeypatch):
    """The lifespan must actually call it — a warm nobody starts is a warm
    that never happens."""
    from iron_jarvis.providers.manager import ProviderManager

    called: list[int] = []
    monkeypatch.setattr(
        ProviderManager, "warm_opencode", lambda self: called.append(1), raising=True
    )

    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))

    async def _boot() -> None:
        async with app.router.lifespan_context(app):
            pass

    asyncio.run(_boot())
    assert called, "boot never warmed the opencode allowlist"


def test_a_failing_warm_never_breaks_boot(tmp_path, monkeypatch):
    from iron_jarvis.providers.manager import ProviderManager

    def _boom(self) -> None:
        raise RuntimeError("no opencode here")

    monkeypatch.setattr(ProviderManager, "warm_opencode", _boom, raising=True)

    from iron_jarvis.daemon.app import create_app

    app = create_app(str(tmp_path))

    async def _boot() -> bool:
        async with app.router.lifespan_context(app):
            return True

    assert asyncio.run(_boot()) is True
