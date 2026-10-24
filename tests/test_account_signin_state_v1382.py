"""v1.382.0 — an account Iron-Proxy says is READY is never shown as "needs to
sign in again" because of an old note.

The user's report: the IJ harness's model menu and a Build terminal's account
chip said "Needs to sign in again" for two Claude accounts the user had signed
in to the night before, while `claude` worked in that very terminal. Read off
the live app (read-only): Iron-Proxy's state for both was ``status: "ready"``
with a LEFTOVER ``parkedReason: {kind: "auth-expired", …}`` ("Invalid bearer
token", "Not logged in · Please run /login"). Iron-Proxy's own rule
(the vendored bundle): an ``auth-expired`` signal sets ``status:
"unauthenticated"``; its refresh sets ``"ready"`` again after a sign-in and
does NOT delete the note (only a sign-in it runs itself, or an unpark, does);
and its ``availability`` hands out a ``ready`` account. Three readers here
(the harness account rows, the pane's chip, the check before a pane starts on
an account) and the shared ``_account_kind`` treated the note alone as "needs
sign-in" — forever, because nothing ever cleared it. ONE rule now:
:func:`iron_jarvis.iron_proxy.accounts.needs_sign_in`.
"""
from __future__ import annotations

import pytest

from iron_jarvis.harness import build_accounts as BA
from iron_jarvis.iron_proxy import accounts as A
from iron_jarvis.terminals import pane_accounts as PA

STALE = {"kind": "auth-expired", "message": "Invalid bearer token"}
PROFILE = {"id": "prof_tsi", "title": "TSI", "provider": "anthropic", "lane": "cli",
           "enabled": True, "cli": {"home": "C:/Users/x/.claude-tsi"}}


# --------------------------------------------------------------------------- #
# the one rule
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"status": "unauthenticated", "parkedReason": STALE}, True),
        ({"status": "unauthenticated"}, True),
        # Iron-Proxy re-checked and found it signed in: the old note is history.
        ({"status": "ready", "parkedReason": STALE}, False),
        ({"status": "active", "parkedReason": STALE}, False),
        ({"status": "ready"}, False),
        # A bare signal (no status yet) still reads as a sign-in problem — the
        # pinned-pick and parked-answer paths build exactly this shape.
        ({"parkedReason": {"kind": "auth-expired"}}, True),
        # Defensive: a parked account whose reason is an expired sign-in.
        ({"status": "parked", "parkedReason": {"kind": "auth-expired"}}, True),
        ({"status": "parked", "parkedReason": {"kind": "rate-limit"}}, False),
        ({}, False),
    ],
)
def test_needs_sign_in_follows_iron_proxys_status(state, expected):
    assert A.needs_sign_in(state) is expected


def test_the_shared_kind_reads_a_ready_account_with_an_old_note_as_not_signed_out():
    assert A._account_kind({"status": "ready", "parkedReason": STALE}) != "signin"
    assert A._account_kind({"status": "unauthenticated"}) == "signin"
    # The signal-only shape keeps its old answer.
    assert A._account_kind({"parkedReason": {"kind": "auth-expired"}}) == "signin"


# --------------------------------------------------------------------------- #
# the readers
# --------------------------------------------------------------------------- #
def test_the_harness_account_row_is_ready():
    row = BA.row_of(PROFILE, {"status": "ready", "parkedReason": STALE}, vendor="claude")
    assert row["status"] == BA.READY, row
    row = BA.row_of(PROFILE, {"status": "unauthenticated", "parkedReason": STALE}, vendor="claude")
    assert row["status"] == BA.NEEDS_SIGN_IN, row


def test_the_terminal_chip_is_not_needs_sign_in():
    cache = ([PROFILE], {"prof_tsi": {"status": "ready", "parkedReason": STALE}})
    state = PA._state_of({"id": "prof_tsi", "source": "pick"}, cache)
    assert state["state"] != "needs-sign-in", state
    cache = ([PROFILE], {"prof_tsi": {"status": "unauthenticated", "parkedReason": STALE}})
    assert PA._state_of({"id": "prof_tsi", "source": "pick"}, cache)["state"] == "needs-sign-in"


class _AskedClient:
    """Iron-Proxy's pick was reached (the account was not refused first)."""

    class Reached(Exception):
        pass

    def pick(self, *a, **k):
        raise self.Reached()


def test_a_terminal_can_start_on_a_ready_account_with_an_old_note():
    spec = PA.SPECS["anthropic"]
    states = {"prof_tsi": {"status": "ready", "parkedReason": STALE}}
    # The pick is reached — no "needs to sign in again" refusal before it.
    with pytest.raises((_AskedClient.Reached, PA.PaneAccountRefused)) as got:
        PA._pick_explicit(_AskedClient(), spec, "prof_tsi", [PROFILE], states)
    assert not isinstance(got.value, PA.PaneAccountRefused) or "sign in" not in got.value.sentence
    # A signed-out account is still refused, in words, before any pick.
    states = {"prof_tsi": {"status": "unauthenticated", "parkedReason": STALE}}
    with pytest.raises(PA.PaneAccountRefused) as refused:
        PA._pick_explicit(_AskedClient(), spec, "prof_tsi", [PROFILE], states)
    assert "needs to sign in again" in refused.value.sentence
