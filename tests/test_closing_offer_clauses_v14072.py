"""v1.407.2 — a closing offer is the USER'S call when ANY clause of it pushes,
deploys, deletes, sends, pays, signs, revokes or migrates shared data.

The user's decision (2026-10-29), found by v1.407.0's review:
``harness/closing_offer.user_owns`` read only the START of an offered action,
so "run the tests and then push", "git push --force", "rm -rf the build
folder", "transfer the funds", "cancel the subscription", "run the deploy
script" … read as plain work and the daemon's CLOSING_OFFER_NUDGE told the
model to just go ahead. The next-step box patched it with a list of its own
(``nextStep.asksFirst``); that knowledge now lives in the daemon's reading
(``_owned`` / ``_clauses``) and reaches the box through the GENERATED twin.

The table rows themselves (both ways, Python and the TS twin from one
fixture) are in ``tests/test_closing_offer_v1402.py`` (USER_OWNED / OFFERS,
the v1.407.2 blocks) and ``dashboard/__tests__/closing-offer-twin-v1406``.
Here: the nudge is NOT sent for them, in both chat lanes and the agent
runtime, while an ordinary offer of the same shape still is.
"""

from __future__ import annotations

import pytest

from iron_jarvis.harness import closing_offer as CO
from tests._fakes.harness_lane import body, no_finish_check, wire
from tests.test_closing_offer_v1402 import (  # noqa: F401 — `env` is a fixture
    NOT_OFFERS,
    OFFERS,
    USER_OWNED,
    _agent,
    _lane,
    _nudges,
    env,
)

#: The new user-owned rows the lanes are driven with (one per family).
USER_NEW = [
    "Want me to run the tests and then push?",
    "Want me to git push --force?",
    "Want me to rm -rf the build folder?",
    "Shall I transfer the funds?",
    "Shall I cancel the subscription?",
    "Want me to go ahead and run the deploy script?",
    "Shall I tag v2 and publish the release?",
    "Want me to run the migration on staging?",
    "Shall I sign the contract?",
    "Want me to revoke the token?",
]
#: Ordinary work offers of the same shape (the controls).
WORK_NEW = [
    "Want me to add a delete button?",
    "Want me to run the linter and fix what it finds?",
    "Want me to add tests and then run them?",
]


def test_the_reading_is_clause_by_clause_and_one_definition():
    for text in USER_NEW:
        assert CO.offer_kind(text) == "user", text
        assert CO.user_owns(CO.last_sentence(text)), text
        assert not CO.ends_on_offer(text), text
    for text in WORK_NEW:
        assert CO.offer_kind(text) == "work", text
        assert not CO.user_owns(CO.last_sentence(text)), text
    # The action words the box fills come from the same reading.
    assert CO.offered_action("Want me to run the tests and then push?") == "run the tests and then push"
    assert CO.offered_action("Shall I transfer the funds?") == "transfer the funds"


def test_the_v1402_verdicts_for_ordinary_work_still_hold():
    for text in OFFERS:
        assert CO.offer_kind(text) == "work", text
    for text in NOT_OFFERS:
        assert CO.offer_kind(text) == "", text
    for text in USER_OWNED:
        assert CO.offer_kind(text) in ("user", ""), text


@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize("text", USER_NEW)
async def test_the_nudge_is_not_sent_for_a_user_owned_offer(env, lane, text, monkeypatch):  # noqa: F811
    platform, ws, events = env
    no_finish_check(ws)
    model = wire(platform, monkeypatch, [text, "never reached"])
    done, _ = await _lane(lane, platform, body(ws, "what does app.py do?"))
    assert _nudges(model) == 0, text
    assert len(model.calls) == 1, text
    assert done["reply"] == text
    assert events == []


@pytest.mark.parametrize("lane", ["stream", "post"])
@pytest.mark.parametrize("text", WORK_NEW)
async def test_control_an_ordinary_offer_of_the_same_shape_is_still_nudged(env, lane, text, monkeypatch):  # noqa: F811
    platform, ws, events = env
    no_finish_check(ws)
    model = wire(platform, monkeypatch, [text, "Did it."])
    await _lane(lane, platform, body(ws, "what does app.py do?"))
    assert _nudges(model) == 1, text
    assert len(events) == 1


@pytest.mark.parametrize("text", USER_NEW[:5])
def test_the_runtime_does_not_nudge_a_user_owned_offer(tmp_path, text):
    s, calls, events = _agent(tmp_path, [text, "never reached"])
    assert len(calls) == 1 and not events
    assert s.summary == text


def test_control_the_runtime_still_nudges_ordinary_work(tmp_path):
    s, calls, events = _agent(tmp_path, ["Want me to add tests and then run them?", "Added them."])
    assert len(events) == 1 and len(calls) >= 2
