"""An UNAUTHENTICATED socket must not be able to grow the daemon (v1.235.0).

`/browser/ws?pairing=1` deliberately needs no credential — that is the whole point of
the pairing handshake, since the add-on has nothing to authenticate with until the user
presses Pair. Everything else about that socket is therefore attacker-reachable by any
local process, and two of its costs were unbounded when Ship 1 first landed:

* every connection added a `PendingPairing` to an in-memory registry with no ceiling;
* every entry ALSO became a Pair button that `GET /browser/status` would offer the user.

The second is the worse half. A flood does not just consume memory — it buries the
user's real browser's request under a pile of identical offers, and pressing Pair then
mints the browser pairing credential for whichever socket happens to be holding that id.

`PairingStore.MAX_PENDING_PAIRINGS` bounds it, and the refusal is deliberately NOT an
exception: a flood must not be able to make the socket route raise. `open_request`
returns `None`, the route closes that one socket with 1008, and every pairing already in
flight survives — including the legitimate one.

What each assertion catches, and why nothing else would:

* THE CEILING HOLDS. Without it the registry grows until the process dies, and no test
  in the browser suite looks at the registry's size.
* THE REFUSAL IS A RETURN, NOT A RAISE. If a later refactor raises here instead, the
  socket handler's task dies with an unhandled exception inside the daemon's one event
  loop — a strictly worse outcome than the flood.
* EARLIER REQUESTS SURVIVE. A ceiling that evicted the OLDEST entry would let a flood
  push out the user's genuine request, which is the attack the ceiling exists to stop,
  wearing a fix's clothing.
* EXPIRY STILL FREES SPACE. A ceiling with no drain is a permanent lockout: once eight
  abandoned sockets have been seen, no browser could ever pair again.
"""

from __future__ import annotations

from datetime import timedelta

from iron_jarvis.browser.pairing import MAX_PENDING_PAIRINGS, PairingStore
from iron_jarvis.core.db import open_db
from iron_jarvis.core.ids import utcnow


def _store(tmp_path, **kw) -> PairingStore:
    return PairingStore(open_db(str(tmp_path / "t.db")), **kw)


def test_the_pending_registry_has_a_ceiling(tmp_path):
    store = _store(tmp_path)
    opened = [store.open_request(extension_id="x") for _ in range(MAX_PENDING_PAIRINGS)]
    assert all(r is not None for r in opened), "the ceiling must not bite before it is reached"
    assert store.open_request(extension_id="x") is None, (
        "an unauthenticated socket can grow the pending registry without limit"
    )


def test_the_refusal_is_a_return_and_never_a_raise(tmp_path):
    """A raise here would kill the socket task inside the daemon's single loop."""
    store = _store(tmp_path)
    for _ in range(MAX_PENDING_PAIRINGS):
        store.open_request()
    for _ in range(20):
        # No pytest.raises: the point is that this loop simply completes.
        assert store.open_request() is None


def test_a_flood_cannot_evict_the_request_the_user_is_about_to_approve(tmp_path):
    """The genuine request must still be there after the flood."""
    store = _store(tmp_path)
    real = store.open_request(extension_id="lgihfomaieifpnemakmpadmggjnoojmm")
    assert real is not None
    for _ in range(MAX_PENDING_PAIRINGS * 3):
        store.open_request(extension_id="")
    assert store.pending(real.request_id) is not None, (
        "the flood pushed out the user's own pairing request — the ceiling would then "
        "be the attack rather than the defence"
    )


def test_expiry_frees_the_ceiling_again(tmp_path):
    """Otherwise eight abandoned sockets lock the feature out permanently.

    Time moves by injecting a clock, never by sleeping: no assertion in this repo may
    depend on a wall clock.
    """
    now = utcnow()
    store = _store(tmp_path, deadline_s=60.0, clock=lambda: now)
    for _ in range(MAX_PENDING_PAIRINGS):
        assert store.open_request() is not None
    assert store.open_request() is None
    # Every pending request is now past its deadline.
    now = now + timedelta(seconds=120)
    assert store.open_request() is not None, (
        "expired requests did not free the ceiling, so no browser could pair again"
    )
