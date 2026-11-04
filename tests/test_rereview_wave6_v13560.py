"""v1.356.0 re-review (G60): Microsoft 365 connected FROM CHAT says what is
still to do — the sign-in.

The Directory card shows "It works once you sign in…" and the "Sign in to
Microsoft" press right after Connect. The chat's own door (``settings/
records.AppConnectTool``, armed by "connect my Outlook") answered only
"Connected Microsoft 365 (24 tools)." — so the model told the user it was
done, and the next "what's on my calendar tomorrow?" failed on a pack nobody
had signed in to. Now the connect (and an "already connected" answer) carries
one sentence naming the press and where it is; an app that does not sign in
on this PC carries none.

v1.428.0 (moved on purpose): the chat no longer connects Microsoft 365 (it is
LOCKED DOWN and set up only from its card), so a chat connect says WHERE it is
set up instead — and once the card saved it, asking again from chat still
names the sign-in press and where it is.

Reuses lane 6D's harness (the stand-in server listing the real tool names,
the recording terminal backend) from ``tests/test_microsoft_365_v13560``.
"""

from __future__ import annotations

import asyncio

from iron_jarvis.connectors import catalog
from iron_jarvis.connectors.app_names import APPS_PLACE
from iron_jarvis.settings import records
from tests import test_microsoft_365_v13560 as ms
from tests.test_microsoft_365_v13560 import CARD_ID, _connect, _platform, _saved, _values

# Lane 6D's fixtures, bound here so pytest finds them in this module.
_fresh_records = ms._fresh_records
spawned = ms.spawned
client = ms.client


def _chat_connect(c, values: dict | None = None):
    tool = records.AppConnectTool(_platform(c))
    args: dict = {"app": CARD_ID}
    if values is not None:
        args["values"] = values
    return asyncio.run(tool.execute(args, None))


def test_a_chat_connect_of_microsoft_365_says_where_it_is_set_up(client):
    """v1.428.0 (moved on purpose; was "says to sign in and where"): the chat
    answers where the card is — its place in the app, in plain words — and
    saves nothing; never a command line."""
    out = _chat_connect(client, _values(catalog.MS365_WORK))
    assert out.ok is False
    text = out.error or ""
    assert text.startswith("Microsoft 365"), text
    assert "set up only from its card" in text and APPS_PLACE in text, text
    assert "read and draft only" in text and "your own models only" in text, text
    # The sentence is the card's own words, never a command line.
    assert "--login" not in text and "npx" not in text and "node" not in text
    assert _saved(client) is None


def test_asking_again_once_connected_still_names_the_sign_in(client):
    """v1.428.0 (moved on purpose): connected from its card; asking from chat
    (no values) answers "already connected" with the sign-in sentence."""
    assert _connect(client, catalog.MS365_PERSONAL).status_code == 200
    again = _chat_connect(client)
    assert again.ok is True, again.error
    assert (again.data or {}).get("already_connected") is True
    assert '"Sign in to Microsoft"' in (again.output or "")
    assert APPS_PLACE in (again.output or "")


def test_an_app_that_does_not_sign_in_here_gets_no_sign_in_sentence():
    for cid in ("notion", "github", "slack", "brave_search"):
        conn = catalog.get_connector(cid)
        assert conn is not None, cid
        assert records.signin_hint(conn) == "", cid
    ms365 = catalog.get_connector(CARD_ID)
    assert records.signin_hint(ms365).startswith(" It works once the user signs in")
