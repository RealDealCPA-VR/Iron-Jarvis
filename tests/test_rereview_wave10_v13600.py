"""v1.360.0 re-review — the Connect door, the last everyday sentences.

Lane 10B flipped the door's default to NOT own data (the final re-audit's G41
regression), but one rule still read an app NAME used as a modifier as the
user's own data: an acting verb with the app as its object
(``app_names._acts_on_app``) never looked at what FOLLOWED the app. So a
coding or design sentence offered "Connect <App> in Settings" under the
reply, on both chat lanes:

* "remove the GitHub link from the footer"   -> Connect GitHub
* "update the Slack logo in our app"         -> Connect Slack
* "review the Postgres migration in this PR" -> Connect Postgres
* "share our GitHub launch post on Twitter"  -> Connect GitHub (the
  re-audit's own "our GitHub launch" family)

Now the app is the verb's object only when the end, punctuation or a joining
word follows it ("search github for issues", "open Notion") or a thing kept
in it does ("show me the Slack messages") — the possessive rule's own test.
"dashboard", "notifications", "mentions" and "db" joined the things an app
keeps, so "check my Stripe dashboard", "show me my GitHub notifications" and
"query the postgres db" keep their door.

Pinned through the real matcher (both directions) and through BOTH real chat
lanes (the model is the only double).
"""

from __future__ import annotations

import pytest

from iron_jarvis.connectors.app_names import apps_named, connect_offers, own_data_apps
from iron_jarvis.daemon.app import create_app
import iron_jarvis.daemon.routes.chat as chat_routes
from tests.test_chat_matches_meant_v13600 import _post_turn, _stream_turn

#: An app's name that only qualifies another word — a link, a logo, a
#: migration, a post. Each offered its Connect door before this fix.
MODIFIER_NOT_OWN = [
    "remove the GitHub link from the footer",
    "update the Slack logo in our app",
    "add the Stripe button to the checkout page",
    "make the Dropbox icon bigger",
    "share our GitHub launch post on Twitter",
    "move the GitHub link to the header",
    "put the GitHub badge in the readme",
    "review the Postgres migration in this PR",
    "update the postgres version in docker-compose",
]

#: The same verbs with the app itself (or a thing kept in it) as the object:
#: the door stays.
ACTS_ON_THE_APP = [
    ("search github for issues about login", ["github"]),
    ("open Notion", ["notion"]),
    ("show me the Slack messages from today", ["slack"]),
    ("list my github repos", ["github"]),
    ("check Gmail for the receipt", ["gmail"]),
    ("summarize today's Slack", ["slack"]),
    ("check my Stripe dashboard", ["stripe"]),
    ("show me my GitHub notifications", ["github"]),
    ("query the postgres db", ["postgres"]),
    ("star the GitHub repo", ["github"]),
    ("check the github issues", ["github"]),
    ("pull up Sentry", ["sentry"]),
]


@pytest.mark.parametrize("text", MODIFIER_NOT_OWN)
def test_an_app_name_that_only_qualifies_another_word_offers_no_door(text):
    """MUTATION: drop the after-the-app check from ``_acts_on_app`` -> every
    row here is red."""
    assert apps_named(text), f"anti-vacuity: {text!r} names a catalog app"
    assert own_data_apps(text) == [], text
    rows = [{"id": cid, "name": cid, "state": "not_connected"} for cid in apps_named(text)]
    assert connect_offers(text, rows) == [], text


@pytest.mark.parametrize("text, ids", ACTS_ON_THE_APP)
def test_the_app_itself_as_the_verbs_object_keeps_its_door(text, ids):
    """MUTATION: drop "dashboards?|notifications?" / "dbs?" from the kept
    things -> the matching rows are red."""
    assert own_data_apps(text) == ids, text
    rows = [{"id": cid, "name": cid, "state": "not_connected"} for cid in ids]
    assert [o["id"] for o in connect_offers(text, rows)] == ids, text


@pytest.fixture()
def app(tmp_path, monkeypatch):
    app = create_app(str(tmp_path / "fresh"))
    monkeypatch.setattr(chat_routes, "_ASK_POLL_S", 0.05)
    return app


def test_a_coding_sentence_gets_no_connect_door_in_either_lane(app):
    """As a new user meets it: a fresh install, nothing connected, a model
    that answers in words — no Connect button under the reply on the stream
    lane AND the POST lane; the anti-vacuity half (the app itself as the
    object) still gets its door on both."""
    platform = app.state.platform
    text = "remove the GitHub link from the footer"
    done, _ = _stream_turn(platform, text)
    assert done["doors"] == [], done["doors"]
    res, _ = _post_turn(platform, text)
    assert res["doors"] == [], res["doors"]

    want = [{"href": "/settings?section=connections-apps", "label": "Connect GitHub in Settings"}]
    text = "search github for issues about login"
    done, _ = _stream_turn(platform, text)
    assert done["doors"] == want, done["doors"]
    res, _ = _post_turn(platform, text)
    assert res["doors"] == want, res["doors"]
