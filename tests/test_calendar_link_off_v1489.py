"""Chat can take the saved calendar link OFF (v1.489.0).

Seen live, in the released version and the new one: "take the calendar link
off please i dont want you looking at that anymore" ended on "I can't remove
the calendar link from chat. You can remove it yourself on the Rules page."
Chat could save the link (the secure card) and never remove it.

The calendar is not an app (Iron Jarvis reads one by a saved private link),
but "disconnect my calendar" is how a person asks. So `app_disconnect` with
app "calendar" takes the link off:

* an ordinary approval card first (no card when no link is saved: nothing
  would change);
* the value is copied, encrypted, for the Undo before the row is deleted, and
  the ledger row holds names only;
* the Undo puts the link back, and refuses in words when a newer link was
  saved since (it never writes over newer work);
* a real app whose name holds "calendar" is still disconnected as an app;
* the words that ask for it arm the tool; a thing ON a calendar does not.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation, UndoJournal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import records_apps as RA
from iron_jarvis.settings.credentials import BACKUP_PREFIX, CredentialError, CredentialStore
from iron_jarvis.tools.base import ToolContext
from tests.test_settings_chat_tools_v1321 import _done
from tests.test_tool_details_v1489 import _app, _finished, _run

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

LINK = "https://calendar.example.com/private/abc123/basic.ics"
ASK = "take the calendar link off please i dont want you looking at that anymore"


def _ctx(p, tmp_path):
    return ToolContext(workspace=tmp_path, session_id="chat", agent_run_id="chat", config=p.config,
                       event_bus=p.event_bus, engine=p.engine)


@pytest.fixture()
def home(tmp_path):
    app = create_app(str(tmp_path / "ij"))
    p = app.state.platform
    p.secrets.set(RA.CALENDAR_SECRET, LINK, kind="password")
    return app, p


def _rows(p):
    with session_scope(p.engine) as db:
        return [(r.kind, json.loads(r.pre_inline or "{}")) for r in db.exec(select(UndoJournal)).all()]


# =========================================================================== #
# The tool
# =========================================================================== #
def test_the_link_is_taken_off_with_a_copy_kept_for_the_undo(home, tmp_path):
    _app_, p = home
    tool = p.registry.get("app_disconnect")
    out = asyncio.run(tool.execute({"app": "calendar"}, _ctx(p, tmp_path)))
    assert out.ok, out.error
    assert p.secrets.get(RA.CALENDAR_SECRET) is None
    assert "no longer reads that calendar" in out.output and "Undo" in out.output
    card = out.data["record_change"]
    assert card["title"] == "Calendar link removed" and card["old"] == "saved" and card["new"] is None
    rows = [d for kind, d in _rows(p) if kind == "secret_restore"]
    assert len(rows) == 1
    desc = rows[0]
    assert desc["secret"] == RA.CALENDAR_SECRET and desc["removed"] is True
    assert desc["change_id"] == card["change_id"]
    assert desc["backup"].startswith(BACKUP_PREFIX) and p.secrets.get(desc["backup"]) == LINK
    # NAMES ONLY: the link is in no ledger row and nowhere in the tool's answer.
    with session_scope(p.engine) as db:
        blob = " ".join(
            [str(r.args_json) + str(r.output) for r in db.exec(select(ToolInvocation)).all()]
            + [str(r.pre_inline) for r in db.exec(select(UndoJournal)).all()]
        )
    assert LINK not in blob and "abc123" not in blob
    assert LINK not in out.output and LINK not in json.dumps(out.data)


@pytest.mark.parametrize("app", ["calendar", "Google Calendar", "my calendar link", "ical", "outlook_calendar"])
def test_every_way_of_naming_the_calendar_reaches_the_link(home, tmp_path, app):
    _app_, p = home
    out = asyncio.run(p.registry.get("app_disconnect").execute({"app": app}, _ctx(p, tmp_path)))
    assert out.ok and p.secrets.get(RA.CALENDAR_SECRET) is None


def test_with_no_link_saved_nothing_changes_and_no_card_is_shown(tmp_path):
    app = create_app(str(tmp_path / "ij"))
    p = app.state.platform
    tool = p.registry.get("app_disconnect")
    check = asyncio.run(tool.spend_check({"app": "calendar"}, _ctx(p, tmp_path)))
    assert check is not None and check.ask is False, "a call that changes nothing shows no card"
    out = asyncio.run(tool.execute({"app": "calendar"}, _ctx(p, tmp_path)))
    assert out.ok and "nothing to take off" in out.output and not out.data
    assert [k for k, _ in _rows(p) if k == "secret_restore"] == []


def test_a_saved_link_gets_the_ordinary_card(home, tmp_path):
    _app_, p = home
    check = asyncio.run(p.registry.get("app_disconnect").spend_check({"app": "calendar"}, _ctx(p, tmp_path)))
    assert check is None, "the ordinary ask-tier card decides"


def test_another_unknown_app_is_still_refused_as_before(home, tmp_path):
    _app_, p = home
    out = asyncio.run(p.registry.get("app_disconnect").execute({"app": "trello"}, _ctx(p, tmp_path)))
    assert not out.ok and p.secrets.get(RA.CALENDAR_SECRET) == LINK


def test_only_a_plain_private_value_is_removed_this_way(home):
    _app_, p = home
    from types import SimpleNamespace

    store = CredentialStore(SimpleNamespace(platform=p))
    for name in ("connection.openai", "channel.telegram", "app.notion", "model_server.box"):
        with pytest.raises(CredentialError):
            store.remove(name)
    with pytest.raises(CredentialError):
        store.remove("no_such_secret")
    assert p.secrets.get(RA.CALENDAR_SECRET) == LINK


def test_a_copy_that_could_not_be_kept_removes_nothing(home, tmp_path, monkeypatch):
    # No Undo copy, no removal: the link must never be lost with no way back.
    _app_, p = home
    real = p.secrets.set

    def set_but_lose_the_copy(name, value, **kw):
        if str(name).startswith(BACKUP_PREFIX):
            return None
        return real(name, value, **kw)

    monkeypatch.setattr(p.secrets, "set", set_but_lose_the_copy)
    out = asyncio.run(p.registry.get("app_disconnect").execute({"app": "calendar"}, _ctx(p, tmp_path)))
    assert not out.ok and "nothing was removed" in out.error
    assert p.secrets.get(RA.CALENDAR_SECRET) == LINK
    assert [k for k, _ in _rows(p) if k == "secret_restore"] == []


# =========================================================================== #
# The Undo
# =========================================================================== #
def _undo(app, change_id):
    client = TestClient(app)
    look = client.get(f"/config/changes/{change_id}")
    assert look.status_code == 200, look.text
    return client.post(f"/undo/{look.json()['action_id']}")


def test_undo_puts_the_link_back(home, tmp_path):
    app, p = home
    out = asyncio.run(p.registry.get("app_disconnect").execute({"app": "calendar"}, _ctx(p, tmp_path)))
    r = _undo(app, out.data["record_change"]["change_id"])
    assert r.status_code == 200, r.text
    assert p.secrets.get(RA.CALENDAR_SECRET) == LINK
    assert LINK not in r.text


def test_undo_never_writes_over_a_newer_link(home, tmp_path):
    app, p = home
    out = asyncio.run(p.registry.get("app_disconnect").execute({"app": "calendar"}, _ctx(p, tmp_path)))
    newer = "https://calendar.example.com/private/newer/basic.ics"
    p.secrets.set(RA.CALENDAR_SECRET, newer, kind="password")
    r = _undo(app, out.data["record_change"]["change_id"])
    assert r.status_code == 409 and "newer value was saved" in r.text
    assert p.secrets.get(RA.CALENDAR_SECRET) == newer


# =========================================================================== #
# The words that ask for it
# =========================================================================== #
@pytest.mark.parametrize("text", [
    ASK,
    "Disconnect my Google calendar.",
    "please remove the calendar link",
    "unlink the calendar feed, then tell me it is done",
    "Get rid of my calendar connection please",
    "take my outlook calendar off",
])
def test_asking_for_the_link_to_go_arms_the_tool(text):
    RA.apps_tools_for.cache_clear()
    assert "app_disconnect" in RA.apps_tools_for(text), text


@pytest.mark.parametrize("text", [
    "remove the calendar invite from Friday",
    "delete the calendar event for the dentist",
    "take the calendar down to the print shop",
    "I dropped my calendar notes in the folder",
    "can you take a look at my calendar",
    "forget the calendar meeting on Tuesday",
    "the remove calendar button is broken on my phone",
    "take the bins off the list",
])
def test_a_thing_on_a_calendar_arms_nothing(text):
    RA.apps_tools_for.cache_clear()
    assert "app_disconnect" not in RA.apps_tools_for(text), text


def test_the_matcher_is_linear_on_a_hostile_message():
    import time

    RA.apps_tools_for.cache_clear()
    for text in ("take the " * 3000, "remove my calendar " * 2000 + "x", "take " + "the calendar link " * 2000):
        t0 = time.perf_counter()
        RA.apps_tools_for(text)
        assert time.perf_counter() - t0 < 2.0, text[:30]


# =========================================================================== #
# The real stream lane
# =========================================================================== #
@pytest.mark.asyncio
async def test_in_chat_the_link_goes_after_the_approval_card_and_the_reply_has_the_undo(tmp_path):
    app = _app(tmp_path, [("app_disconnect", {"app": "calendar"})])
    p = app.state.platform
    p.secrets.set(RA.CALENDAR_SECRET, LINK, kind="password")
    frames, order = await _run(app, ASK)
    assert [k for k, _ in order] == ["approval"], order
    assert order[0][1]["tool"] == "app_disconnect" and order[0][1]["args"] == {"app": "calendar"}
    assert p.secrets.get(RA.CALENDAR_SECRET) is None
    done = _finished(frames, "app_disconnect")
    assert len(done) == 1 and done[0]["ok"] is True
    cards = _done(frames)["config_cards"]
    assert len(cards) == 1 and cards[0]["title"] == "Calendar link removed" and cards[0]["change_id"]
    assert LINK not in json.dumps([d for _ev, d in frames])


@pytest.mark.asyncio
async def test_a_declined_card_keeps_the_link(tmp_path):
    app = _app(tmp_path, [("app_disconnect", {"app": "calendar"})])
    p = app.state.platform
    p.secrets.set(RA.CALENDAR_SECRET, LINK, kind="password")
    _frames, order = await _run(app, ASK, decision="deny")
    assert [k for k, _ in order] == ["approval"]
    assert p.secrets.get(RA.CALENDAR_SECRET) == LINK
