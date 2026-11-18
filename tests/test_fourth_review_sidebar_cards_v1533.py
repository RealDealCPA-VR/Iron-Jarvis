"""Fourth review BS1 and C-BS-1: in a Local only sidebar reply, the sidebar's
Auto-allow switch answered the card that only the card itself may answer.

THE DEFECT. Under Local only every page action asks first (v1.332.0): its card
is "Allow once" / "Deny" only, and no grant may skip or answer it. The chat
lane knew the card's kind, but filed it in the approval registry with no kind,
and the sidebar's card dropped it, so turning Auto-allow on while the card was
up answered it as "once" with the switch (actor "grant"): a page action ran
that nobody allowed one at a time. "Allow all page actions" was also offered
on that card, and accepted.

WHAT IS PINNED, through the REAL app (``create_app``, a paired add-on socket,
the real panel, the real stream lane and the real approval registry; only the
model and the tool runner are doubles):

1. A Local only sidebar reply's card, then the switch turned on: the switch is
   on, the card still waits, the step has not run; the card carries
   ``floor: true`` and its one line; the person's own Allow once runs it.
2. The registry itself refuses an answer by a grant on a once-only or priced
   ask (the ask keeps waiting), ``resolve_where`` skips them, and a person's
   answer still lands. ``once_kind`` names the kind.
3. "Allow all page actions" on such a card is refused in words: nothing ran,
   the switch is unchanged, the card still waits. An older add-on's version
   (a plain Allow, then the switch) runs the one action and is refused the
   switch.
4. The control: an ORDINARY sidebar card is still answered by the switch,
   through the real registry.
5. The add-on's switch says why a Local only card still asks.

Every wait is a bounded budget that fails by name; nothing asserts a duration.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.browser import panel as panel_mod
from iron_jarvis.browser import protocol as P
from iron_jarvis.core.approvals import ChatApprovals
from iron_jarvis.core.turns import TURNS
from iron_jarvis.daemon.routes.chat import _approvals
from tests._fakes.panel_harness import RealApp, _wait_for
from tests.test_browser_agent_v1262 import _NEUTRAL, _tool_names
from tests.test_browser_tab_grant_v1266 import _cards, _click, _look_at, _script, _states

ROOT = Path(__file__).resolve().parents[1]
PANEL_HTML = ROOT / "extensions" / "chrome" / "src" / "sidepanel" / "sidepanel.html"

#: C-BS-1: the line under a Local only card, word for word.
LOCAL_ONLY_LINE = (
    "Local only is on, so every page action asks first. Auto-allow does not cover it."
)

WINDOW = "win-bs1"


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _registry(app: RealApp):
    return _approvals(SimpleNamespace(platform=app.platform))


def _kind(app: RealApp, approval_id: str) -> str:
    return _registry(app).once_kind(approval_id)


def _local_only_card(app: RealApp, *, local_only: bool = True) -> tuple[dict, list[dict]]:
    """Pair, look at a tab, and Send a question whose first step is a click.
    Returns the card the sidebar was shown and the record of tool runs."""
    app.platform.config.chat_new_local_only = local_only
    app.pair()
    _look_at(app, 7)
    calls = app.stub_tools()
    _script(app, [[_click("c1", 1)], "Done."])
    app.panel.send(P.PANEL_ACTION_SEND, text=_NEUTRAL, window=WINDOW)
    card = app.panel.wait_for(P.PANEL_EVENT_APPROVAL, "the click never raised a card")
    assert card["tool"] == "browser_click", card
    return card, calls


# --------------------------------------------------------------------------- #
# 1. The switch leaves a Local only card up
# --------------------------------------------------------------------------- #


def test_bs1_the_switch_leaves_a_local_only_card_waiting(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        card, calls = _local_only_card(app)
        before = len(_states(app))
        app.panel.send(P.PANEL_ACTION_AUTO_ALLOW, on=True, window=WINDOW)
        app.panel.barrier("the switch press was never handled")
        states = _states(app)[before:]
        assert states and states[-1]["auto_allow"] is True, "the switch did not turn on"
        assert app.platform.browser.auto_allowed() is True
        # The card is still the person's: the registry still waits on it, and
        # the click has not run.
        assert card["id"] in _registry(app).pending_ids(), (
            "turning Auto-allow on answered a Local only card"
        )
        assert _tool_names(calls) == [], "the click ran on the switch, with no Allow once"
        assert not [p for e, p in app.panel.events() if e == P.PANEL_EVENT_DONE], (
            "the reply ended although its card was never answered"
        )
        # What the card is: answered one action at a time, and it says why.
        assert card.get("floor") is True, card
        assert card.get("note") == LOCAL_ONLY_LINE, card
        # The lane filed the card WITH its kind; the registry is the one field.
        assert _kind(app, card["id"]) == "local_only"
        # The person's own Allow once runs it, and the reply ends.
        app.panel.send(P.PANEL_ACTION_APPROVE, id=card["id"], window=WINDOW)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the reply never finished after Allow once")
        assert _tool_names(calls) == ["browser_click"]
        assert not any(c["kw"].get("deny_reason") for c in calls), "the allowed click was refused"
        # (The barrier's `open` shows the waiting card again: the same id.)
        assert {c["id"] for c in _cards(app)} == {card["id"]}, "a second card appeared"


# --------------------------------------------------------------------------- #
# 2. The registry: a grant never answers a once-only or priced ask
# --------------------------------------------------------------------------- #


def test_bs1_the_registry_refuses_a_grants_answer_on_a_once_only_or_priced_ask():
    async def drive() -> None:
        reg = ChatApprovals()
        once_id, once_fut = reg.request(
            "browser_click", {}, "sess-1", once_only=True, once_reason="local_only",
        )
        priced_id, priced_fut = reg.request(
            "make_picture", {}, "sess-1", cost_note="Make 1 picture for 4 credits",
        )
        plain_id, plain_fut = reg.request("browser_click", {}, "sess-1")

        assert reg.resolve(once_id, "once", actor="grant") is False
        assert not once_fut.done(), "a grant answered a once-only ask"
        assert reg.resolve(priced_id, "once", actor="grant") is False
        assert not priced_fut.done(), "a grant answered a priced ask"
        assert reg.actor_of(once_id) == "" and reg.actor_of(priced_id) == ""
        # The sibling wake-up skips them too, and still answers the plain one.
        delivered = reg.resolve_where("sess-1", "browser_click", "once", actor="grant")
        assert delivered == 1, delivered
        assert not once_fut.done(), "resolve_where answered a once-only ask"
        assert plain_fut.done() and plain_fut.result() == "once"
        assert reg.resolve_where("sess-1", "make_picture", "conversation", actor="grant") == 0
        assert not priced_fut.done()

        # The kind, read-only.
        assert reg.once_kind(once_id) == "local_only"
        assert reg.once_kind(priced_id) == "once"
        assert reg.once_kind(plain_id) == ""
        assert reg.once_kind("apr_unknown") == ""

        # A person's answer still lands, on both.
        assert reg.resolve(once_id, "once") is True
        assert once_fut.result() == "once"
        assert reg.resolve(priced_id, "deny", actor="phone") is True
        assert priced_fut.result() == "deny"

    asyncio.run(drive())


# --------------------------------------------------------------------------- #
# 3. "Allow all page actions" on such a card is refused in words
# --------------------------------------------------------------------------- #


def test_bs1_allow_all_on_a_local_only_card_is_refused_in_words(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        card, calls = _local_only_card(app)
        app.panel.send(P.PANEL_ACTION_APPROVE, id=card["id"], scope="all", window=WINDOW)
        error = app.panel.wait_for(P.PANEL_EVENT_ERROR, "allow-all on a Local only card was not refused")
        assert error["text"] == panel_mod.LOCAL_ONLY_CARD_ALL_REFUSED, error
        assert error["text"] == (
            "Local only is on, so each page action is allowed one at a time. Nothing"
            " was done and Allow all page actions was not turned on. Choose Allow"
            " once or Deny."
        )
        # The reason every add-on keeps the card up on.
        assert error.get("reason") == "risk_card_all", error
        app.panel.barrier("the refused answer was never handled")
        assert _tool_names(calls) == [], "allow-all on a Local only card ran the click"
        assert app.platform.browser.auto_allowed() is False, "allow-all turned the switch on"
        assert card["id"] in _registry(app).pending_ids(), "the card was answered"

        # An OLDER add-on's allow-all: a plain Allow (no window), then the switch.
        app.panel.send(P.PANEL_ACTION_APPROVE, id=card["id"])
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the reply never finished after the Allow")
        assert _tool_names(calls) == ["browser_click"]
        app.panel.send(P.PANEL_ACTION_AUTO_ALLOW, on=True)
        older = app.panel.wait_for(
            P.PANEL_EVENT_ERROR, "an older add-on's switch after a Local only Allow was not refused", nth=2,
        )
        assert older["text"] == panel_mod.LOCAL_ONLY_CARD_ALL_OLDER, older
        app.panel.barrier("the older switch press was never handled")
        assert app.platform.browser.auto_allowed() is False


# --------------------------------------------------------------------------- #
# 4. The control: an ordinary sidebar card is still answered by the switch
# --------------------------------------------------------------------------- #


def test_bs1_an_ordinary_sidebar_card_is_still_answered_by_the_switch(tmp_path, monkeypatch):
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        card, calls = _local_only_card(app, local_only=False)
        assert _kind(app, card["id"]) == "", "an ordinary card was filed as once-only"
        assert card.get("floor") is not True and "note" not in card, card
        app.panel.send(P.PANEL_ACTION_AUTO_ALLOW, on=True, window=WINDOW)
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the reply never finished after the switch went on")
        assert _tool_names(calls) == ["browser_click"], "the switch did not answer an ordinary card"
        assert not any(c["kw"].get("deny_reason") for c in calls)
        _wait_for(
            lambda: card["id"] not in _registry(app).pending_ids(),
            "the ordinary card still waits after the switch answered it",
        )


# --------------------------------------------------------------------------- #
# 5. The add-on's switch says why a Local only card still asks
# --------------------------------------------------------------------------- #


def test_c_bs_1_the_switch_says_local_only_still_asks():
    html = PANEL_HTML.read_text(encoding="utf-8").replace("\r\n", "\n")
    start = html.index('id="auto"')
    tag = html[start: html.index(">", start)]
    assert "In a Local only chat every page action still asks." in tag, tag
    # The card has a line for the note, empty and hidden until a card brings one.
    assert '<p id="approval-note"' in html
