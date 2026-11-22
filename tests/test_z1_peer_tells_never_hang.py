"""Z-1: waiting for the tells to a paired computer never hangs.

The Linux release run of v1.555.0 failed on one test that timed out at 300 s,
``test_next_leaps_41a_peer_switches::test_turning_send_me_tasks_off_stops_
the_running_task_and_refuses_the_next``, on its last line ``await
R.drain_tells()``. The dump showed the main thread STEPPING inside
``asyncio.gather`` (``futures._get_loop``) over a tell that had already
finished, not waiting on the network. ``drain_tells`` was ``while _TELLS: await
asyncio.gather(*list(_TELLS))``, the shape ``docs/rules/daemon.md`` forbids
since v1.388.2: a tell leaves its list through a done callback the loop runs
with ``call_soon``; when a drain starts in the turn after a tell finished and
before that callback ran, ``gather`` over finished tasks completes at once,
awaiting it never yields, the callback never runs, and the ``while`` spins
forever. And the list was ONE module-level set for every app in the process,
so a drain on one event loop gathered another loop's tells.

Each case runs on its own event loop in a thread with a deadline, so the old
shape fails here in seconds instead of hanging pytest (a thread still spinning
is a daemon thread; it ends with the test process). Through the real route
(``PATCH /peers/{name}/allows`` -> ``change_allows`` -> the tell); the one
stand-in is the other computer (``link.tell_allows``).

  Z1 the CI hang, made on purpose: a tell finishes and the drain starts before
     its removal ran. The drain returns, and the app's list is empty.
  Z2 two apps on two event loops in one process, each with a tell on its way:
     a drain waits only for its own loop's tells.
  Z3 a tell that never ends: the drain fails loudly, in words, at its bound.
  Z4 turning "Send me tasks" off never waits on the tell.
  Z5 the shape itself: no ``while <tasks>: await asyncio.gather(...)`` anywhere
     in the app's code (``drain_tells`` was written with it in v1.552.0, though
     the rule against it dates from v1.388.2).
"""

from __future__ import annotations

import asyncio
import re
import threading
from pathlib import Path
from typing import Any

import httpx

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import peers as R
from iron_jarvis.peers import link as L
from iron_jarvis.peers import store as S

#: How long a case's own loop may take before the case counts it as hung.
DEADLINE_S = 30.0


def _app_with_a_peer(root) -> Any:
    """One computer with one pairing ("desk"), every switch off."""
    app = create_app(str(root))
    S.store_for(app.state.platform).add(
        name="desk",
        their_name="desk",
        address="http://10.0.0.2:8787",
        in_token="made-here-for-the-desk",
        out_token="made-there-for-this-one",
        my_address="http://10.0.0.1:8787",
    )
    return app


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver",
                             timeout=30.0)


async def _switch(app, on: bool) -> httpx.Response:
    async with _client(app) as c:
        return await c.patch("/peers/desk/allows", json={"may_send_tasks": on})


def _start(main, name: str) -> tuple[threading.Event, dict[str, Any]]:
    """Run ``main()`` on a fresh event loop in a daemon thread. The dict gets
    ``value`` or ``error``; the event is set when it ended."""
    out: dict[str, Any] = {}
    ended = threading.Event()

    def run() -> None:
        try:
            out["value"] = asyncio.run(main())
        except BaseException as exc:  # noqa: BLE001 — handed to the test thread
            out["error"] = exc
        finally:
            ended.set()

    threading.Thread(target=run, daemon=True, name=name).start()
    return ended, out


def _run(main, name: str = "z1-loop") -> Any:
    ended, out = _start(main, name)
    assert ended.wait(DEADLINE_S), "the loop never ended: the drain spun or hung"
    if "error" in out:
        raise out["error"]
    return out["value"]


# --------------------------------------------------------------------------- #
# Z1 — the CI hang, on purpose
# --------------------------------------------------------------------------- #


def test_a_drain_that_starts_just_after_a_tell_finished_returns(tmp_path, monkeypatch):
    app = _app_with_a_peer(tmp_path / "laptop")

    async def main() -> set:
        entered, gate, told = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def tell(address, token, allows):
            entered.set()
            await gate.wait()
            told.set()  # wakes main BEFORE this tell's own removal is queued
            return True

        monkeypatch.setattr(L, "tell_allows", tell)
        r = await _switch(app, True)
        assert r.status_code == 200, r.text
        await entered.wait()
        gate.set()
        await told.wait()
        # The tell has finished; the done callback that takes it off its list
        # has not run yet: the state the Linux run hung in.
        await R.drain_tells()
        return set(getattr(app.state.d, "peer_tells", {"no list on the app"}))

    left = _run(main)
    assert left == set(), "a finished tell was still listed after the drain"


# --------------------------------------------------------------------------- #
# Z2 — two apps, two event loops, one process
# --------------------------------------------------------------------------- #


def test_a_drain_waits_only_for_the_tells_of_its_own_event_loop(tmp_path, monkeypatch):
    first = _app_with_a_peer(tmp_path / "one")
    second = _app_with_a_peer(tmp_path / "two")
    held: dict[str, Any] = {}
    one_has_a_tell = threading.Event()

    async def tell(address, token, allows):
        if asyncio.get_running_loop() is held.get("loop"):
            await held["gate"].wait()  # the first loop's tell stays on its way
        return True

    monkeypatch.setattr(L, "tell_allows", tell)

    async def one() -> str:
        held["loop"], held["gate"] = asyncio.get_running_loop(), asyncio.Event()
        r = await _switch(first, True)
        assert r.status_code == 200, r.text
        one_has_a_tell.set()
        await R.drain_tells()  # its own tell, let go from the test thread
        return "one drained"

    async def two() -> str:
        r = await _switch(second, True)
        assert r.status_code == 200, r.text
        await R.drain_tells()
        return "two drained"

    one_ended, one_out = _start(one, "z1-loop-one")
    try:
        assert one_has_a_tell.wait(DEADLINE_S), one_out
        # Loop one's tell is on its way and stays there: loop two's drain
        # neither waits for it nor touches it.
        assert _run(two, "z1-loop-two") == "two drained"
    finally:
        if "loop" in held:
            held["loop"].call_soon_threadsafe(held["gate"].set)
    assert one_ended.wait(DEADLINE_S), "loop one's drain never ended"
    assert one_out == {"value": "one drained"}, one_out
    for app in (first, second):
        assert set(app.state.d.peer_tells) == set(), "each app's own list is empty"


# --------------------------------------------------------------------------- #
# Z3 — bounded, and loud
# --------------------------------------------------------------------------- #


def test_a_tell_that_never_ends_makes_the_drain_fail_loudly_in_words(tmp_path, monkeypatch):
    app = _app_with_a_peer(tmp_path / "laptop")

    async def main() -> BaseException | None:
        gate = asyncio.Event()

        async def stuck(address, token, allows):
            await gate.wait()
            return True

        monkeypatch.setattr(L, "tell_allows", stuck)
        assert (await _switch(app, True)).status_code == 200
        caught: BaseException | None = None
        try:
            # The outer bound only catches a drain that never gives up; the
            # drain's own bound is the one asked for.
            await asyncio.wait_for(R.drain_tells(timeout=0.2), DEADLINE_S / 2)
        except BaseException as exc:  # noqa: BLE001
            caught = exc
        gate.set()
        await R.drain_tells()
        return caught

    caught = _run(main)
    assert isinstance(caught, TimeoutError), repr(caught)
    assert str(caught) == R.tells_still_going_words(1, 0.2), "the drain's own words, not wait_for's"


# --------------------------------------------------------------------------- #
# Z4 — turning "Send me tasks" off never waits on the tell
# --------------------------------------------------------------------------- #


def test_turning_send_me_tasks_off_never_waits_on_the_tell(tmp_path, monkeypatch):
    app = _app_with_a_peer(tmp_path / "laptop")

    async def main() -> tuple[int, int, dict]:
        gate = asyncio.Event()

        async def stuck(address, token, allows):
            await gate.wait()
            return True

        monkeypatch.setattr(L, "tell_allows", stuck)
        on = await asyncio.wait_for(_switch(app, True), DEADLINE_S / 3)
        # Both tells are stuck on the way to the other computer; the switch
        # answers anyway.
        off = await asyncio.wait_for(_switch(app, False), DEADLINE_S / 3)
        gate.set()
        await R.drain_tells()
        return on.status_code, off.status_code, off.json()

    on_status, off_status, off = _run(main)
    assert (on_status, off_status) == (200, 200), off
    assert off["after"]["may_send_tasks"] is False
    (peer,) = S.store_for(app.state.platform).list()
    assert peer.may_send_tasks is False, "the switch is off whatever the tell did"


# --------------------------------------------------------------------------- #
# Z5 — the shape that spins, nowhere in the code
# --------------------------------------------------------------------------- #

#: ``while <something>:`` whose very next statement awaits ``asyncio.gather``:
#: the drain that spins once every task it lists has finished.
_SPINNING_DRAIN = re.compile(r"^[ \t]*while\b[^\n]*:[ \t]*(?:#[^\n]*)?\n[ \t]*await[ \t]+asyncio\.gather\(",
                             re.MULTILINE)


def test_no_drain_in_the_code_has_the_shape_that_spins():
    src = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"
    found = []
    for path in sorted(src.rglob("*.py")):
        # CI's Windows runners check out with CRLF: normalize at the reader.
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
        for m in _SPINNING_DRAIN.finditer(text):
            found.append(f"{path.relative_to(src)}:{text.count(chr(10), 0, m.start()) + 1}")
    assert found == [], (
        "`while tasks: await asyncio.gather(*tasks)` spins forever once every task has finished "
        "(docs/rules/daemon.md, v1.388.2): use core/tasks.drain_tasks or wait only for tasks not done(). "
        f"Found at: {found}"
    )
