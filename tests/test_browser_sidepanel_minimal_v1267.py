"""v1.267.0 — a minimal sidebar, and the model is the user's to pick.

THE REPORT: "I want the browser extension to be extremely good, not too much in
the way of instruction when the extension is opened. Super clean and minimal. I
should also be able to select the model I want from this browser extension."

WHAT IS PINNED.

* **Minimal by measurement.** The idle panel's visible words — comments, CSS,
  script, the approval card and the access-off line removed — stay under a
  budget. Every explanation lives in a tooltip (``title=``): the Stop promise,
  the keyboard contract, the access mode. No ``<h1>``, no hint paragraphs.
* **The model list is THE catalog.** ``open`` emits ``models`` built by the same
  function ``GET /models`` answers with, projected to a few keys; ``default``
  names the app's default. Proven by substituting that one function and reading
  the panel's frame.
* **A pick reaches the turn** as ``ChatBody.provider``/``model`` — the chat page's
  own fields — and nothing else changes about the turn.
* **A bad pick is refused before anything is spent**: a model not on the list, or
  one the list says is not connected, gets one sentence and no turn.
* **The route is disclosed only when it is not what was asked**: a failover or a
  mock answer prints one muted line; an ordinary answer prints nothing extra.
* **The add-on's half** at the source: the select, the stored pick, the pick on
  every Send, the models branch, and the generated protocol.

Against the REAL app where a turn is involved (``create_app``, a paired add-on
socket, the real panel); the turn itself is a recorder standing in for the chat
lane at its one seam (``chat_stream.stream_chat_turn``), because the property
under test is what the panel HANDS the lane, not what the lane does with it —
that is ``test_browser_agent_v1262.py``'s subject.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser.panel import PanelTurns
from iron_jarvis.core.turns import TURNS
from tests._fakes.panel_harness import RealApp

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "extensions" / "chrome" / "src"
PANEL_HTML = ADDON / "sidepanel" / "sidepanel.html"
PANEL_TS = ADDON / "sidepanel" / "sidepanel.ts"
PROTOCOL_TS = ADDON / "protocol.ts"

#: The idle panel's visible-word budget. Measured at 22 on the v1.267.0 page;
#: the budget leaves room for a word or two, not for a paragraph.
IDLE_WORD_BUDGET = 40

CATALOG = [
    {"provider": "anthropic", "model": "claude-sonnet-4-6", "available": True, "kind": "cloud", "base_url": "x"},
    {"provider": "ollama", "model": "qwen3", "name": "Qwen 3", "available": False, "kind": "local", "exec_path": "y"},
]


@pytest.fixture(autouse=True)
def _clean_registry():
    for tid in TURNS.running_ids():
        TURNS.release(tid)
    yield
    for tid in TURNS.running_ids():
        TURNS.release(tid)


def _src(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def _visible_words(html: str) -> list[str]:
    """Words a user reads on the idle panel: no comments, CSS, script, card or off-line."""
    text = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    text = re.sub(r"<style.*?</style>", "", text, flags=re.S)
    text = re.sub(r"<script.*?</script>", "", text, flags=re.S)
    text = re.sub(r'<div id="approval".*?</div>\s*</div>', "", text, flags=re.S)
    text = re.sub(r'<div id="empty">.*?</div>', "", text, flags=re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return [w for w in re.split(r"\s+", text) if w and w not in ("·", "—", "…")]


def _catalog_stub(monkeypatch) -> None:
    from iron_jarvis.daemon.routes import connections

    monkeypatch.setattr(connections, "selectable_models", lambda d: [dict(r) for r in CATALOG])


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _record_turns(monkeypatch, *, done: dict[str, Any] | None = None) -> list[Any]:
    """Stand in for the chat lane at its one seam; keep every body handed to it."""
    from iron_jarvis.daemon import chat_stream

    bodies: list[Any] = []

    async def fake_stream_chat_turn(platform, personas, body, **kw):
        bodies.append(body)

        async def gen():
            yield _sse("token", {"text": "ok"})
            yield _sse("done", done or {"text": "ok"})

        return gen()

    monkeypatch.setattr(chat_stream, "stream_chat_turn", fake_stream_chat_turn)
    return bodies


# --------------------------------------------------------------------------- #
# 1. Minimal, measured
# --------------------------------------------------------------------------- #


def test_the_idle_panel_prints_fewer_words_than_the_budget():
    words = _visible_words(_src(PANEL_HTML))
    assert len(words) <= IDLE_WORD_BUDGET, (
        f"{len(words)} visible words on the idle panel (budget {IDLE_WORD_BUDGET}): {words}"
    )


def test_no_instruction_paragraphs_and_no_title_bar():
    html = _src(PANEL_HTML)
    assert "<h1" not in html, "the side panel already has Chrome's own title bar"
    for gone in ("mode-hint", "running-hint", "keys-hint"):
        assert f'id="{gone}"' not in html, f"#{gone} is an instruction paragraph; explanations are tooltips now"


def test_every_explanation_lives_in_a_tooltip():
    html = _src(PANEL_HTML)
    stop = re.search(r'<button[^>]*id="stop"[^>]*>', html)
    assert stop and "stop prevents the next step" in stop.group(0).lower() and "finishes" in stop.group(0).lower()
    ask = re.search(r'<textarea[^>]*id="ask"[^>]*>', html)
    assert ask and "Enter sends" in ask.group(0) and "Shift+Enter" in ask.group(0)
    assert re.search(r'<span[^>]*id="access"[^>]*title=', html), "the access pill carries no tooltip slot"
    ts = _src(PANEL_TS)
    assert "el.access.title = modeHint(status.access)" in ts


def test_the_off_state_is_one_line_that_still_says_nothing_runs():
    html = _src(PANEL_HTML)
    empty = re.search(r'<div id="empty">(.*?)</div>', html, flags=re.S)
    assert empty
    text = re.sub(r"<[^>]+>", "", empty.group(1)).lower()
    assert "browser access is off" in text and "can run nothing" in text
    assert len(text.split()) <= 24, text


# --------------------------------------------------------------------------- #
# 2. The model list is THE catalog
# --------------------------------------------------------------------------- #


def test_open_emits_the_catalog_projected_and_the_default(tmp_path, monkeypatch):
    _catalog_stub(monkeypatch)
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        app.panel.send(P.PANEL_ACTION_OPEN)
        frame = app.panel.wait_for(P.PANEL_EVENT_MODELS, "open never emitted the model list")
    rows = frame["models"]
    assert [(r["provider"], r["model"]) for r in rows] == [("anthropic", "claude-sonnet-4-6"), ("ollama", "qwen3")]
    assert rows[1]["name"] == "Qwen 3" and rows[1]["available"] is False
    for row in rows:
        assert set(row) <= set(PanelTurns.MODEL_ROW_KEYS), f"a key the panel has no business with: {row}"
    assert "base_url" not in json.dumps(rows) and "exec_path" not in json.dumps(rows)
    assert set(frame["default"]) == {"provider", "model"}


def test_the_list_is_cached_across_tab_switches_and_refreshed_after_the_ttl(tmp_path, monkeypatch):
    from iron_jarvis.daemon.routes import connections

    calls = {"n": 0}

    def counting(d):
        calls["n"] += 1
        return [dict(r) for r in CATALOG]

    monkeypatch.setattr(connections, "selectable_models", counting)
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        app.panel.send(P.PANEL_ACTION_OPEN)
        app.panel.wait_for(P.PANEL_EVENT_MODELS, "first open")
        app.panel.send(P.PANEL_ACTION_OPEN)  # a tab switch re-posts open
        app.panel.wait_for(P.PANEL_EVENT_MODELS, "second open", nth=2)
        assert calls["n"] == 1, "a tab switch must not re-probe every provider"
        panel = app.platform.browser.backend.panel_handler.__self__  # the PanelTurns
        panel.models_ttl_s = 0.0
        app.panel.send(P.PANEL_ACTION_OPEN)
        app.panel.wait_for(P.PANEL_EVENT_MODELS, "third open", nth=3)
        assert calls["n"] == 2, "past the TTL the list is read again"


# --------------------------------------------------------------------------- #
# 3. A pick reaches the turn; a bad pick never starts one
# --------------------------------------------------------------------------- #


def test_a_pick_rides_the_chat_body_and_default_stays_empty(tmp_path, monkeypatch):
    _catalog_stub(monkeypatch)
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        bodies = _record_turns(monkeypatch)
        app.panel.send(P.PANEL_ACTION_SEND, text="hi", provider="anthropic", model="claude-sonnet-4-6")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the picked turn never finished")
        app.panel.send(P.PANEL_ACTION_SEND, text="again")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the default turn never finished", nth=2)
    assert [(b.provider, b.model) for b in bodies] == [("anthropic", "claude-sonnet-4-6"), ("", "")]
    assert bodies[0].messages[0].content == "hi" and bodies[0].auto_tools is True


def test_an_unavailable_pick_is_refused_before_a_turn_starts(tmp_path, monkeypatch):
    _catalog_stub(monkeypatch)
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        bodies = _record_turns(monkeypatch)
        app.panel.send(P.PANEL_ACTION_SEND, text="hi", provider="ollama", model="qwen3")
        err = app.panel.wait_for(P.PANEL_EVENT_ERROR, "no refusal for a model that is not connected")
    assert "qwen3" in err["text"] and "not connected" in err["text"] and err["reason"] == "model_unavailable"
    assert bodies == [], "a refused pick must not start a turn"
    assert not any(e == P.PANEL_EVENT_DONE for e, _ in app.panel.events())


def test_an_unknown_pick_is_refused_by_name(tmp_path, monkeypatch):
    _catalog_stub(monkeypatch)
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        bodies = _record_turns(monkeypatch)
        app.panel.send(P.PANEL_ACTION_SEND, text="hi", provider="openai", model="gpt-ghost")
        err = app.panel.wait_for(P.PANEL_EVENT_ERROR, "no refusal for a model not on the list")
    assert "gpt-ghost" in err["text"] and err["reason"] == "model_unknown"
    assert bodies == []


# --------------------------------------------------------------------------- #
# 4. The route is disclosed only when it is not what was asked
# --------------------------------------------------------------------------- #


def test_a_failover_or_a_mock_answer_is_said_once_and_an_ordinary_one_is_not(tmp_path, monkeypatch):
    _catalog_stub(monkeypatch)
    with RealApp(tmp_path, monkeypatch, access="interactive") as app:
        app.pair()
        _record_turns(monkeypatch, done={
            "text": "ok",
            "route": {"requested": "anthropic", "provider": "openai", "model": "gpt-4o", "reason": "failover", "from": "anthropic", "why": "unreachable"},
        })
        app.panel.send(P.PANEL_ACTION_SEND, text="hi")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the failover turn never finished")
        lines = [p["text"] for e, p in app.panel.events() if e == P.PANEL_EVENT_TOOL and p.get("name") == "route"]
        assert lines == ["Answered by openai/gpt-4o — anthropic was unreachable (unreachable)."], lines

        _record_turns(monkeypatch, done={"text": "ok", "route": {"provider": "mock", "model": "mock-1", "reason": "mock"}})
        app.panel.send(P.PANEL_ACTION_SEND, text="hi")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the mock turn never finished", nth=2)
        lines = [p["text"] for e, p in app.panel.events() if e == P.PANEL_EVENT_TOOL and p.get("name") == "route"]
        assert len(lines) == 2 and "mock model" in lines[1]

        _record_turns(monkeypatch, done={"text": "ok", "route": {"requested": "", "provider": "anthropic", "model": "claude-sonnet-4-6", "reason": "default"}})
        app.panel.send(P.PANEL_ACTION_SEND, text="hi")
        app.panel.wait_for(P.PANEL_EVENT_DONE, "the ordinary turn never finished", nth=3)
        lines = [p["text"] for e, p in app.panel.events() if e == P.PANEL_EVENT_TOOL and p.get("name") == "route"]
        assert len(lines) == 2, "an ordinary route must print nothing"


def test_route_notice_words():
    assert PanelTurns._route_notice(None) == ""
    assert PanelTurns._route_notice({"reason": "explicit", "provider": "a", "model": "b"}) == ""
    assert PanelTurns._route_notice({"reason": "failover", "provider": "a", "model": "b", "requested": "c"}) == (
        "Answered by a/b — c was unreachable."
    )
    assert "mock" in PanelTurns._route_notice({"reason": "mock"})


# --------------------------------------------------------------------------- #
# 5. The add-on's half, at the source
# --------------------------------------------------------------------------- #


def test_the_panel_has_a_model_select_wired_to_the_daemon_and_to_send():
    html = _src(PANEL_HTML)
    assert re.search(r'<select[^>]*id="model"', html), "no model select"
    ts = _src(PANEL_TS)
    assert "case PANEL_EVENT_MODELS:" in ts and "paintModels(payload)" in ts
    assert 'STORAGE_MODEL_KEY = "ij.panel.model"' in ts
    assert re.search(r'post\(PANEL_ACTION_SEND, \{ text, \.\.\.\(pick \? pick : \{\}\) \}\)', ts), (
        "the pick does not ride the Send"
    )
    assert "option.disabled = true" in ts, "an unavailable model must be greyed, not hidden"
    generated = _src(PROTOCOL_TS)
    assert 'export const PANEL_EVENT_MODELS = "models";' in generated, "protocol.ts was not regenerated"
    assert '"models"' in generated.split("ALL_PANEL_EVENTS")[1].split(";")[0]


def test_the_visible_copy_never_calls_the_addon_an_extension():
    text = re.sub(r"<!--.*?-->|<style.*?</style>|<script.*?</script>", "", _src(PANEL_HTML), flags=re.S)
    assert "extension" not in text.lower()
