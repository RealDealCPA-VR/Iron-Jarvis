"""Review of Wave B lane B2 (v1.449.0): the defects the independent review
found in the builder's cut, each red first.

1. A remote that answers an error WITH THE TOKEN IT WAS SENT ("invalid token
   <the token>") put the user's own credential into ``run()``'s ``detail`` —
   shown raw on the card before this lane, folded under Details after it, and
   carried into the chat room, the phone and the log. The mask knows key
   SHAPES, not the word the user typed on the card: ``run()`` now takes the
   sent token out of the body before any wording (``remote._without_token``).
2. No word of a remote Test's title or fix is a retired word of the app's
   vocabulary ("Press Retry" where the press is Test, "daemon", …), read
   against the dashboard's own retired list, for EVERY kind and status.
"""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import remote
from iron_jarvis.daemon.app import create_app

ROOT = Path(__file__).resolve().parents[1]
RETIRED_TS = ROOT / "dashboard" / "__tests__" / "helpers" / "retiredWords.ts"

TOKEN = "hermes-secret-2024-xyz"


class _FakeResp:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text

    def json(self):
        raise ValueError("no json body")


def _record() -> remote.RemoteAgentRecord:
    return remote.RemoteAgentRecord(
        name="hermes", base_url="http://192.168.1.9:9000/run", kind="http-task",
        secret_name="remote_agent_hermes", timeout_s=30,
    )


def _echoing_401(monkeypatch):
    seen: dict[str, str] = {}

    async def fake_post(self, url, json=None, headers=None, **kw):
        seen["auth"] = (headers or {}).get("Authorization", "")
        return _FakeResp(401, '{"error": "invalid token ' + TOKEN + '"}')

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    return seen


# --------------------------------------------------------------------------
# 1. the echoed token
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_body_that_echoes_the_sent_token_never_reaches_detail(monkeypatch):
    seen = _echoing_401(monkeypatch)
    out = await remote.RemoteAgentRegistry.run(None, _record(), "x", lambda n: TOKEN)
    assert seen["auth"] == f"Bearer {TOKEN}", "the fixture did not send the token"
    assert out["ok"] is False and out["kind"] == "http" and out["status"] == 401
    assert TOKEN not in out["detail"], out["detail"]
    assert remote.TOKEN_HIDDEN in out["detail"]
    # Everything else about the detail is as before.
    assert out["detail"].startswith("remote returned HTTP 401: ")


@pytest.mark.asyncio
async def test_a_body_without_the_token_is_untouched(monkeypatch):
    async def fake_post(self, url, json=None, headers=None, **kw):
        return _FakeResp(503, "upstream down")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    out = await remote.RemoteAgentRegistry.run(None, _record(), "x", lambda n: TOKEN)
    assert out["detail"] == "remote returned HTTP 503: upstream down"


def test_a_short_token_is_left_alone_so_the_words_survive():
    # Replacing every "t" would wreck the text; the mask's Bearer rule still
    # covers the header form.
    assert remote._without_token("remote returned HTTP 401: t", "t") == "remote returned HTTP 401: t"
    assert remote._without_token("x " + TOKEN + " y", TOKEN) == f"x {remote.TOKEN_HIDDEN} y"
    assert remote._without_token("", TOKEN) == ""
    assert remote._without_token("text", "") == "text"


def test_the_test_route_folds_no_echoed_token_under_details(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    r = client.post(
        "/agents/remote",
        json={"name": "hermes", "base_url": "http://192.168.1.9:9000/run", "kind": "http-task", "token": TOKEN},
    )
    assert r.status_code == 200, r.text
    _echoing_401(monkeypatch)
    body = client.post("/agents/remote/hermes/test").json()
    assert body["ok"] is False
    assert body["title"] == "Hermes refused the token"
    for key in ("detail", "technical", "title", "fix"):
        assert TOKEN not in body[key], (key, body[key])
    assert remote.TOKEN_HIDDEN in body["technical"]


# --------------------------------------------------------------------------
# 2. no retired word in any Test sentence
# --------------------------------------------------------------------------


def _retired() -> list[tuple[str, re.Pattern[str]]]:
    src = RETIRED_TS.read_text(encoding="utf-8").replace("\r\n", "\n")
    rows = [
        (word, re.compile(rx, re.I))
        for word, rx in re.findall(r"\{ word: \"([^\"]+)\", re: /(.+?)/i, use:", src)
    ]
    assert len(rows) >= 10, "read too little of retiredWords.ts"
    return rows


def _every_test_sentence() -> list[tuple[str, str]]:
    probes = [
        {"ok": False, "detail": "hermes did not answer within 15s", "kind": "timeout"},
        {"ok": False, "detail": "request failed: boom", "kind": "unreachable"},
        {"ok": False, "detail": "remote returned a non-JSON body", "kind": "shape"},
        {"ok": False, "detail": "probe failed"},
        {"ok": True, "detail": "ok"},
    ] + [
        {"ok": False, "detail": f"remote returned HTTP {st}", "kind": "http", "status": st}
        for st in (400, 401, 403, 404, 408, 409, 422, 429, 500, 502, 503, 504)
    ]
    out: list[tuple[str, str]] = []
    for probe in probes:
        words = remote.remote_test_words("hermes", probe)
        assert words.get("title"), probe
        out.append((f"{probe.get('kind')}/{probe.get('status')} title", words["title"]))
        out.append((f"{probe.get('kind')}/{probe.get('status')} fix", words["fix"]))
    for kind, fix in remote.REMOTE_TEST_FIX.items():
        out.append((f"REMOTE_TEST_FIX[{kind}]", fix))
    return out


def test_no_test_sentence_uses_a_retired_word_or_the_wrong_press():
    retired = _retired()
    for where, text in _every_test_sentence():
        hits = [w for w, rx in retired if rx.search(text)]
        assert not hits, f"{where}: {text!r} uses a retired word {hits}"
        assert "retry" not in text.lower(), f"{where}: the press is Test, not Retry: {text!r}"
        assert "chat" not in text.lower(), f"{where}: a Test on a card is not a chat: {text!r}"
        assert "daemon" not in text.lower() and "probe" not in text.lower(), (where, text)
