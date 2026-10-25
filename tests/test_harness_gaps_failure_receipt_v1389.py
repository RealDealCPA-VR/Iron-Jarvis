"""Harness gaps, fix 2 and fix 3: a Build coding session's reply line keeps
what the chat's failure line and receipt show.

Fix 2 (the v1.388.0 re-audit, G.3 / G.8 / H3): a failed Build reply said
"Press Retry" with no Retry, kept only ``title. fix`` on its line (no
technical text for a Details fold after a reload) and offered no Sign in. The
line now keeps, beside ``error`` (unchanged, ``title. fix``):

* ``errorTitle`` — the headline alone (the screen reads just it beside a
  press, the chat's rule);
* ``errorDetail`` — the server's own words, MASKED (a key in the error text
  never reaches the line) and bounded;
* ``errorKind`` "sign_in" / "key" + ``errorProvider`` — the ONE press that
  fixes it (the chat's ``fix_action``); "refusal" keeps its old meaning.

Fix 3 (A22): the line already saved ``approved`` and ``refit`` (the screen
never passed them on — a dashboard pin covers that); it now also keeps the
other receipt facts the chat page passes — ``adapted``, ``remembered``,
``threadRefs`` — and the line whitelist (``harness/sessions.LINE_KEYS``)
carries every one of them.

These drive a REAL Build turn (the real app factory, a scripted model that
fails) and the real line writer.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as B
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ProviderError
from tests.test_build_sessions_v1367 import (
    Script,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    wire,
)

#: A key-shaped value in the provider's error text — it must never reach the
#: reply line (the frame's technical text is masked; the line masks again).
SECRET = "sk-ant-api03-ZZZZZZZZZZZZZZZZZZZZ"


class _Failing(Script):
    def __init__(self, exc):
        super().__init__([])
        self.exc = exc

    async def stream(self, **kw):
        await self._next(kw.get("system", ""), kw.get("messages", ()), kw.get("tools"))
        raise self.exc
        yield  # pragma: no cover — an async generator

    async def complete(self, **kw):
        await self._next(kw.get("system", ""), kw.get("messages", ()), kw.get("tools"))
        raise self.exc


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _reply(app, sid: str) -> dict:
    return [m for m in messages_of(app, sid) if m.get("role") == "assistant"][-1]


async def test_a_signed_out_codex_reply_keeps_the_press_the_headline_and_masked_details(app, work):
    sid = new_session(app, work)["id"]
    wire(app, _Failing(ProviderError(f"codex-cli API error 401: unauthorized (token {SECRET})", status_code=401)))
    status, raw = await open_turn(app, sid, {"text": "Add a cart", "turn_id": "hg-signin"})
    assert status == 200, raw
    [err] = [d for ev, d in frames_of(raw) if ev == "error"]
    assert err["fix_kind"] == "sign_in" and err["fix_provider"] == "codex-cli"

    line = _reply(app, sid)
    # Unchanged: the chat's one sentence.
    assert line["error"] == f"{err['title']}. {err['fix']}"
    # New: the press, its provider, the headline alone.
    assert line["errorKind"] == "sign_in"
    assert line["errorProvider"] == "codex-cli"
    assert line["errorTitle"] == err["title"]
    assert not line["errorTitle"].endswith("."), "the headline as the daemon said it"
    # New: the server's own words for the Details fold — masked, bounded.
    assert "401" in line["errorDetail"]
    assert SECRET not in line["errorDetail"] and SECRET not in json.dumps(line)
    assert len(line["errorDetail"]) <= B.FAILURE_DETAIL_CHARS


async def test_a_refused_key_keeps_the_key_press(app, work):
    sid = new_session(app, work)["id"]
    wire(app, _Failing(ProviderError("openrouter API error 401: invalid key", status_code=401)))
    status, raw = await open_turn(app, sid, {"text": "Add a cart", "turn_id": "hg-key"})
    assert status == 200, raw
    line = _reply(app, sid)
    assert (line["errorKind"], line["errorProvider"]) == ("key", "openrouter")


async def test_a_failure_no_press_fixes_keeps_its_details_and_no_press(app, work):
    sid = new_session(app, work)["id"]
    wire(app, _Failing(ProviderError("openrouter API error 503: overloaded right now", status_code=503)))
    status, raw = await open_turn(app, sid, {"text": "Add a cart", "turn_id": "hg-503"})
    assert status == 200, raw
    line = _reply(app, sid)
    assert "errorKind" not in line and "errorProvider" not in line
    assert line["errorTitle"] and "overloaded" in line["errorDetail"]


def test_the_details_are_masked_again_and_bounded_on_the_line():
    """The line's own guard (the frame's text is masked already, but a line
    is kept for good): a key-shaped value never survives, and it is cut."""
    out = B.failure_detail(f"401 from the gateway (Authorization: Bearer {SECRET})")
    assert SECRET not in out and "401" in out
    assert len(B.failure_detail("x" * 10_000)) == B.FAILURE_DETAIL_CHARS
    assert B.failure_detail(None) == "" and B.failure_detail({"no": "text"}) == ""


def test_the_line_keeps_every_receipt_fact_the_chat_page_passes():
    """Fix 3: the done frame's receipt facts reach the reply line — the ones
    it already kept (approved, refit, folderRules) and the ones it dropped
    (adapted, remembered, threadRefs)."""
    w = B._ReplyWatch()
    done = {
        "reply": "Added the cart.",
        "route": {"provider": "claude-cli", "model": "opus", "reason": "explicit"},
        "approved": [{"tool": "shell", "decision": "conversation"}, {"tool": "process_start", "decision": "conversation"}],
        "refit": {"dropped": 4},
        "folder_rules": ["AGENTS.md"],
        "adapted": {"model": "opus", "changes": ["tool_cap:6"]},
        "remembered": ["Keep answers short"],
        "thread_refs": [{"id": "t1", "title": "Earlier", "chars": 120, "ok": True}],
    }
    w.see("done", json.dumps(done))
    [line] = [m for m in w.lines("t1", "done") if m["role"] == "assistant"]
    assert line["approved"] == done["approved"]
    assert line["refit"] == {"dropped": 4}
    assert line["folderRules"] == ["AGENTS.md"]
    assert line["adapted"] == done["adapted"]
    assert line["remembered"] == ["Keep answers short"]
    assert line["threadRefs"] == done["thread_refs"]
    # Nothing to say: nothing kept (a turn with no adaptation, no preference).
    w2 = B._ReplyWatch()
    w2.see("done", json.dumps({"reply": "ok", "adapted": None, "remembered": [], "thread_refs": []}))
    [plain] = [m for m in w2.lines("t2", "done") if m["role"] == "assistant"]
    assert "adapted" not in plain and "remembered" not in plain and "threadRefs" not in plain


def test_the_line_whitelist_carries_every_new_key(tmp_path, work):
    """The session's line store keeps only ``LINE_KEYS``: every key the
    writer now adds must be on it, or it is dropped on the way to disk."""
    app = create_app(str(tmp_path / "home2"))
    sid = new_session(app, work)["id"]
    line = {
        "role": "assistant", "content": "", "turnId": "t9", "error": "X. Y.",
        "errorKind": "sign_in", "errorProvider": "claude-cli", "errorTitle": "X", "errorDetail": "raw words",
        "adapted": {"changes": ["tool_cap:6"]}, "remembered": ["short"], "threadRefs": [{"id": "a"}],
        "approved": [{"tool": "shell", "decision": "once"}], "refit": {"dropped": 1},
    }
    assert S.append_lines(app.state.platform.engine, sid, [line])["ok"]
    saved = _reply(app, sid)
    for key in ("errorKind", "errorProvider", "errorTitle", "errorDetail", "adapted", "remembered",
                "threadRefs", "approved", "refit"):
        assert saved.get(key) == line[key], key
