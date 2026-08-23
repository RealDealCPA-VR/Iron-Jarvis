"""ChatApprovals is LOOP-AWARE (D3, v1.209.0).

The bug: a scheduled goal iteration runs inside ``asyncio.run`` on the
APScheduler worker thread (``platform._run_scheduled`` →
``scheduling/service.py::_fire``), so its ask-tier pause created the approval
future on that PRIVATE loop — while ``POST /chat/approvals/{id}`` and the
Telegram resolve call ``fut.set_result`` from the MAIN loop's thread. Setting
a result from a foreign thread never wakes the private loop's selector, so
the user's Allow applied only when the ``wait_for`` timeout fired (~300s
late, minting a ``timeout`` receipt instead of an approval), and RuntimeErrors
under debug mode.

Pinned here:

* a future created on a private loop in a worker thread and resolved from the
  main thread WAKES PROMPTLY — the wait returns the decision well under its
  timeout (a liveness bound, not a wall-clock performance assertion: before
  the fix this path structurally takes the FULL timeout);
* the same-loop path is byte-identical to the old behavior (resolve True,
  double-resolve False, popped id False, bad decision False);
* resolving after the creating loop died (``asyncio.run`` closed it) is an
  honest False — never an exception;
* the ``fut.done()`` guard inside the marshaled callable: two cross-loop
  answers may both be accepted, exactly one lands, and the awaiter sees ONE
  decision with no error on its loop.
"""

from __future__ import annotations

import asyncio
import threading
import time

from iron_jarvis.core.approvals import ChatApprovals

#: The awaiter's timeout in the wake test. The liveness bound asserts we wake
#: WELL under it — the broken behavior structurally rides the full timeout.
_WAIT_TIMEOUT_S = 30.0


def _run_worker(body) -> threading.Thread:
    """A worker thread running ``asyncio.run(body())`` — the exact shape of a
    scheduled fire (scheduling/service.py::_fire)."""
    thread = threading.Thread(target=lambda: asyncio.run(body()), daemon=True)
    thread.start()
    return thread


async def test_cross_loop_resolve_wakes_the_private_loop_promptly():
    approvals = ChatApprovals()
    box: dict = {}
    asked = threading.Event()

    async def scheduled_fire():
        approval_id, fut = approvals.request("web_search", {"q": "x"})
        box["id"] = approval_id
        asked.set()
        started = time.monotonic()
        try:
            box["decision"] = await asyncio.wait_for(fut, timeout=_WAIT_TIMEOUT_S)
        except asyncio.TimeoutError:
            box["decision"] = "timeout"
        finally:
            approvals.pop(approval_id)
        box["elapsed"] = time.monotonic() - started

    thread = _run_worker(scheduled_fire)
    assert await asyncio.to_thread(asked.wait, 10.0), "the worker never asked"

    # Answer from THIS (main) loop's thread — the POST /chat/approvals shape.
    assert approvals.resolve(box["id"], "once") is True

    await asyncio.to_thread(thread.join, 15.0)
    assert not thread.is_alive(), "the private loop never woke — D3 regressed"
    assert box["decision"] == "once"
    # Liveness bound, not a perf assertion: the broken path structurally takes
    # the FULL timeout, so waking in under half of it proves the marshal.
    assert box["elapsed"] < _WAIT_TIMEOUT_S / 2, box["elapsed"]


async def test_same_loop_path_is_unchanged():
    approvals = ChatApprovals()
    approval_id, fut = approvals.request("web_search", None)
    assert approvals.pending_ids() == [approval_id]

    assert approvals.resolve(approval_id, "not-a-decision") is False
    assert approvals.resolve(approval_id, "deny") is True
    assert await asyncio.wait_for(fut, timeout=1.0) == "deny"
    # Already answered: honest False, exactly as before.
    assert approvals.resolve(approval_id, "once") is False

    approvals.pop(approval_id)
    assert approvals.resolve(approval_id, "once") is False  # popped = unknown
    assert approvals.pending_count() == 0


async def test_resolve_after_the_creating_loop_died_is_an_honest_false():
    """The scheduled fire ended (or crashed) before anyone answered:
    ``asyncio.run`` closed its loop, so the asker is gone. A late Allow must
    read as "already over" — False, never a raise into the route."""
    approvals = ChatApprovals()
    box: dict = {}

    async def fire_that_ends_without_waiting():
        approval_id, _fut = approvals.request("web_search", None)
        box["id"] = approval_id  # never awaited: the run ends, the loop closes

    thread = _run_worker(fire_that_ends_without_waiting)
    await asyncio.to_thread(thread.join, 10.0)
    assert not thread.is_alive()

    assert approvals.resolve(box["id"], "once") is False
    approvals.pop(box["id"])  # the sweeper-less registry still cleans by pop


async def test_double_cross_loop_answers_land_exactly_one_decision():
    """The answered-already race MOVES WITH THE MARSHAL: both answers may be
    accepted for delivery, but the guard inside the marshaled callable lets
    exactly one land — the awaiter sees one decision and no error."""
    approvals = ChatApprovals()
    box: dict = {}
    asked = threading.Event()

    async def scheduled_fire():
        approval_id, fut = approvals.request("web_search", None)
        box["id"] = approval_id
        asked.set()
        try:
            box["decision"] = await asyncio.wait_for(fut, timeout=_WAIT_TIMEOUT_S)
        finally:
            approvals.pop(approval_id)

    thread = _run_worker(scheduled_fire)
    assert await asyncio.to_thread(asked.wait, 10.0)

    first = approvals.resolve(box["id"], "once")
    second = approvals.resolve(box["id"], "deny")
    assert first is True  # the first answer is always accepted

    await asyncio.to_thread(thread.join, 15.0)
    assert not thread.is_alive()
    # Exactly one landed; if the second was accepted pre-delivery it must have
    # lost silently (no InvalidStateError on the worker loop).
    assert box["decision"] in ("once", "deny")
    if second is False:
        assert box["decision"] == "once"
