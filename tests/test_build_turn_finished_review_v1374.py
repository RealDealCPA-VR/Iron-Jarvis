"""v1.374.0 review (IJ harness part 2, Phase 3): DECISION — a helper's own
long run raises no notice of its own.

A helper (delegate / spawn_agent) is ONE tool call of the Build turn that
started it: the turn's ``build.turn_finished`` already says the session
finished, so the helper's own ``session.completed`` (origin
``job:build-helper``, an attended origin) never rings the bell — whatever its
outcome — and "This PC" / the tray say nothing for it either. One outcome, one
notice (v1.360.0)."""

from __future__ import annotations

import pytest

from iron_jarvis.comm.notifier import bell_attention, bell_pings
from iron_jarvis.harness.helpers import HELPER_ORIGIN


@pytest.mark.parametrize("status, outcome", [
    ("failed", "failed"), ("completed", "needs_you"), ("completed", "completed_with_failures"),
    ("completed", "ok"), ("cancelled", "cancelled"),
])
def test_a_helpers_own_end_never_rings_the_turns_finish_does(status, outcome):
    ev = {"type": "session.completed", "session_id": "sess_helper1",
          "payload": {"origin": HELPER_ORIGIN, "status": status, "outcome": outcome}}
    assert bell_pings(ev) is False
    assert bell_attention(ev) is False
