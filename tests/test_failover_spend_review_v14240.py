"""v1.424.0 (review of decision D1): the holes the independent review found
in "failover never spends money the user did not choose".

1. THE VISION NOTE MIRRORS THE MONEY RULE. ``chat_turn.
   _vision_unavailable_reason`` says "this image was NOT seen" only when
   neither the pick nor ANY connected provider can see it — it "mirrors
   ``_first_capable``'s filter". After D1 the router's vision reroute never
   moves a picture to a pay-per-use key for a pick that is not one, so a
   local (or plan) pick whose only seeing provider is an OpenAI key keeps the
   picture and the model never sees it. The note still counted the key as
   "a provider that can see", said nothing, and the Windows OCR fallback that
   runs when the note fires (C-05) never ran: the image went unseen in
   silence. The note now leaves out a pay-per-use provider exactly when the
   router does and says, in the one D1 sentence, that it was not sent there.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _vision_unavailable_reason

LOCAL = "http://127.0.0.1:11434/v1/chat/completions"


class _Ad:
    def __init__(self, provider, *, vision, endpoint="", model="m"):
        self.provider, self.model, self._endpoint = provider, model, endpoint
        self._vision = vision

    def capabilities(self):
        return {"tool_use": True, "vision": self._vision}


@pytest.fixture
def platform(tmp_path, monkeypatch):
    p = TestClient(create_app(str(tmp_path))).app.state.platform
    # No keyless inheritance here: an "openai" is a real key.
    monkeypatch.setattr(p.providers, "inherited_from", lambda name: None)
    return p


def _fleet(platform, monkeypatch, adapters):
    monkeypatch.setattr(platform.router, "_snapshot", lambda: set(adapters))
    monkeypatch.setattr(platform.providers, "get", lambda n, m=None: adapters[n])


def test_a_local_pick_whose_only_seeing_model_is_a_paid_key_is_told_the_image_was_not_seen(
    platform, monkeypatch,
):
    _fleet(platform, monkeypatch, {
        "ollama": _Ad("ollama", vision=False, endpoint=LOCAL),
        "openai": _Ad("openai", vision=True),
    })
    reason = _vision_unavailable_reason(SimpleNamespace(platform=platform), "ollama", "")
    assert "NOT seen" in reason, reason
    assert "didn't switch to OpenAI, which bills per use" in reason, reason


def test_a_subscription_that_can_see_still_silences_the_note(platform, monkeypatch):
    """The control: the router reroutes a local pick's picture to Claude Code
    (a plan), so no note — a warning there would be the lie."""
    _fleet(platform, monkeypatch, {
        "ollama": _Ad("ollama", vision=False, endpoint=LOCAL),
        "openai": _Ad("openai", vision=True),
        "claude-cli": _Ad("claude-cli", vision=True),
    })
    assert _vision_unavailable_reason(SimpleNamespace(platform=platform), "ollama", "") == ""


def test_a_paid_pick_may_still_send_the_picture_to_a_paid_key(platform, monkeypatch):
    """The control: a pay-per-use pick may move to another key, so the key
    that can see silences the note, exactly as before D1."""
    _fleet(platform, monkeypatch, {
        "xai": _Ad("xai", vision=False),
        "openai": _Ad("openai", vision=True),
    })
    assert _vision_unavailable_reason(SimpleNamespace(platform=platform), "xai", "") == ""


def test_nothing_left_out_keeps_the_old_words(platform, monkeypatch):
    _fleet(platform, monkeypatch, {"ollama": _Ad("ollama", vision=False, endpoint=LOCAL)})
    reason = _vision_unavailable_reason(SimpleNamespace(platform=platform), "ollama", "")
    assert reason.startswith(
        "the model answering this turn cannot accept images and no connected provider can (ollama)"
    ), reason
    assert "bills per use" not in reason
