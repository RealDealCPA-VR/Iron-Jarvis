"""v1.544.0 (review) — "Try on several models" takes the message's words only.

Comments on changed code were not in that check: the attempts started without
them, no model saw them, the saved question had none, and the chips were gone.
The request is now refused in words, and nothing starts (the screen then puts
the comments back above the box).
"""

from __future__ import annotations

from iron_jarvis.daemon.routes import build_attempts as BA
from tests.test_build_attempts_review_v1397 import (  # noqa: F401 — fixtures
    MENU,
    World,
    app,
    asgi,
    enable,
    fake,
    messages_of,
    new_session,
    open_turn,
    repo,
    wire_models,
)

PICKS = [{"provider": "ollama", "model": "glm-local"}, {"provider": "ollama", "model": "qwen-local"}]
COMMENT = {"path": "app.py", "side": "new", "start": 1, "end": 1, "quote": "+x = 1", "words": "Rename x."}


async def test_try_on_with_comments_is_refused_and_starts_nothing(app, fake, repo, monkeypatch):  # noqa: F811
    await enable(app)
    menu = [*MENU, {"provider": "ollama", "model": "qwen-local", "available": True, "on_this_network": True}]
    world = World({"ollama": ["never"]})
    wire_models(app, monkeypatch, world, menu=menu)
    sid = (await new_session(app, repo, approval_mode="yolo"))["id"]
    status, raw = await open_turn(app, sid, {"text": "do it", "turn_id": "try-c", "try_on": PICKS,
                                             "comments": [COMMENT]})
    assert status == 400 and BA.WORDS_ONLY in raw and "comments" in BA.WORDS_ONLY
    assert messages_of(app, sid) == []
    status, view = await asgi(app, "GET", f"/build/sessions/{sid}")
    assert status == 200 and not view.get("attempts")
