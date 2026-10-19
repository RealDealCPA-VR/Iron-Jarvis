"""v1.354.0 independent re-review — chat knows the apps by name WITHOUT
reading an everyday request as one (G39).

Found by the re-review: the catalog matcher read "the email" and lower-case
"my drive" as Gmail and Google Drive. So "rewrite the email below to sound
more professional" (the everyday request of a new user, pasting a message)
put "Gmail: not connected … Tell the user plainly and offer to connect it"
into the system prompt and ask-armed ``app_connect`` — a real model then
ended the rewrite with "Gmail isn't connected yet" (checked on the local
model). "How much space is left on my drive" did the same with Google Drive.

Now "the email" is a message the user has in hand, "my email" points at text
in the message when "below"/"above"/"here"/":"/"draft" follows, lower-case
"my drive" is the disk (only Google's own "My Drive" names the app), and the
section tells the model to raise a missing app only when answering needs it.

Every pin drives the REAL matcher and the REAL app factory's chat lanes
(only the model is scripted — the 4A harness).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connectors.app_names import apps_named
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings.records import record_tools_for
from tests.test_chat_knows_apps_v13540 import SECTION, _scripted, _section


@pytest.mark.parametrize(
    "text",
    [
        "rewrite the email below to sound more professional: hey bob, need ur w2 asap",
        "summarize the email below",
        "make the email shorter",
        "translate the email into Spanish",
        "rewrite my email below so it sounds kinder",
        "improve my email: hi Bob, the return is ready",
        "fix the typos in my email draft",
        "how much space is left on my drive",
        "clean up my drive",
    ],
)
def test_an_everyday_request_about_text_or_the_disk_names_no_app(text):
    assert apps_named(text) == [], text
    assert "app_connect" not in record_tools_for(text), text


@pytest.mark.parametrize(
    "text, want",
    [
        ("check my email", ["gmail"]),
        ("did I get any new emails in my inbox", ["gmail"]),
        ("open my inbox", ["gmail"]),
        ("clear the inbox", ["gmail"]),
        ("reply to my email from Bob", ["gmail"]),
        ("check my Gmail", ["gmail"]),
        ("search My Drive for the lease", ["google_drive"]),
        ("search my Google Drive for the lease", ["google_drive"]),
    ],
)
def test_the_mailbox_and_googles_own_drive_name_still_name_the_app(text, want):
    assert apps_named(text) == want, text


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_a_pasted_email_rewrite_gets_no_apps_section_and_no_connect_tool(tmp_path, monkeypatch, lane):
    client = TestClient(create_app(str(tmp_path)))
    seen = _scripted(client.app.state.platform, monkeypatch, [])
    body = {
        "messages": [{
            "role": "user",
            "content": "rewrite the email below to sound more professional:\n\nhey bob, need ur w2 asap. thx",
        }],
        "auto_tools": True,
    }
    r = client.post("/chat" if lane == "post" else "/chat/stream", json=body)
    assert r.status_code == 200, r.text
    assert SECTION not in seen["systems"][0], "a pasted email is not a request for Gmail"
    assert "app_connect" not in seen["tools"][0]


@pytest.mark.parametrize("lane", ["post", "stream"])
def test_a_missing_app_is_raised_only_when_answering_needs_it(tmp_path, monkeypatch, lane):
    client = TestClient(create_app(str(tmp_path)))
    seen = _scripted(client.app.state.platform, monkeypatch, [])
    body = {"messages": [{"role": "user", "content": "search my Google Drive for the lease"}], "auto_tools": True}
    r = client.post("/chat" if lane == "post" else "/chat/stream", json=body)
    assert r.status_code == 200, r.text
    section = _section(seen["systems"][0])
    assert "- Google Drive: not connected." in section
    assert "If answering needs Google Drive, tell the user plainly" in section
    assert "If it does not, say nothing about it." in section
    # The acceptance words stay: the offer, and never the web in its place.
    assert "offer to connect it" in section and "Never search the web in its place." in section


# --------------------------------------------------------------------------- #
# G28 on the phone: a failed answer is the chat's sentence, not server text.
# --------------------------------------------------------------------------- #
OPENROUTER_401 = "openrouter API error 401: No auth credentials found"


async def test_the_phone_hears_the_chats_sentence_for_a_refused_key(platform, monkeypatch):
    """The REAL chat turn (run_chat_turn) behind the REAL phone poller: the
    model refuses the key; the phone's reply and the saved thread line say
    "OpenRouter refused your key. Update it in Settings › Connections." —
    the words the chat line and the run summary use — never the 401 text."""
    from iron_jarvis.daemon.chat_turn import run_chat_turn
    from iron_jarvis.providers.adapters.base import ProviderError
    from iron_jarvis.providers.failure_words import ACCOUNTS_PLACE
    from tests.test_comm_full_chat import CHAT_CFG, ChatMockChannel, _chat_poller, _msg

    async def refused(**kw):
        raise ProviderError(OPENROUTER_401, status_code=401)

    monkeypatch.setattr(platform.router, "complete", refused)
    ch = ChatMockChannel(dict(CHAT_CFG))
    poller, _orch, store = _chat_poller(platform, ch, run_chat_turn)

    res = await poller._handle("tg", ch, _msg("summarise my week"))

    want = f"I hit a problem: OpenRouter refused your key. Update it in {ACCOUNTS_PLACE}."
    assert res["status"] == "chat_error" and res["sent"] is True
    assert store.history_body(res["thread_id"])[-1] == {"role": "assistant", "content": want}
    assert want in ch.sent[-1]
    assert "No auth credentials found" not in " ".join(ch.sent)
    assert "401" not in ch.sent[-1]
