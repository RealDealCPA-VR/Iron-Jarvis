"""Waiting for a set of background tasks, without spinning (v1.388.2).

``while tasks: await asyncio.gather(*list(tasks))`` looks like a wait and is a
busy loop in one case. A tracked task leaves its set through a done callback,
which the event loop runs with ``call_soon`` after the task finishes. When
EVERY task handed to ``gather`` has already finished, Python 3.12's ``gather``
completes eagerly, and awaiting a finished future returns without yielding to
the event loop — so the loop never gets the turn it needs to run the removal
callback, the set never empties, and the ``while`` spins at full CPU forever.
An ``asyncio.wait_for(..., timeout)`` around it cannot help: a timeout fires
only when the loop runs.

That window is real: the phone poller's ``drain()`` (called by the daemon's
shutdown) entered it when a delivery task finished just before the drain
began — the Tests gate hung on it once, and a loop of 120 runs under load hung
5 times, every one spinning in ``drain()``.

:func:`drain_tasks` waits only for the tasks that are still running, and gives
the loop one turn at the end so the removal callbacks of finished tasks run
before it returns.
"""

from __future__ import annotations

import asyncio
from typing import Iterable


async def drain_tasks(tasks: Iterable[asyncio.Future]) -> None:
    """Return once every task in ``tasks`` (a live set the caller keeps adding
    to and removing from) has finished. Tasks added while it waits are waited
    for too. Never raises for a task's own failure."""
    while True:
        pending = [t for t in list(tasks) if not t.done()]
        if not pending:
            break
        await asyncio.gather(*pending, return_exceptions=True)
    # One turn of the loop: the done callbacks of the tasks that finished
    # (the ones that remove them from the caller's set) run now.
    await asyncio.sleep(0)
