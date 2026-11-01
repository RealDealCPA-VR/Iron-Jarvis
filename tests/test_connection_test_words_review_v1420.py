"""Review of lane P's plain-words connection Test (v1.420.0, C6.6).

Two defects, each red on the lane's first cut:

1. A Google AI Studio key that Google REFUSES answers 400 ("API key not
   valid"), which ``google_key.probe_google_key`` already words as
   "google rejected the key (400)". ``kind_of`` read it as ``refused_key``
   with status 400, and ``probe_test_words`` then asked the chat classifier
   about a 400 — "Google Gemini answered with an error. Retry, or pick another
   model for this chat." A refused key is a refused key, whatever status
   carried it, and a Test is not a chat.
2. A provider that ECHOES the key it was sent in an error body put the key in
   ``detail`` and in the folded ``technical`` text when the key has no shape
   ``redact.mask`` knows (the v1.382.0 rule: every credential a request carried
   comes out of the error text, by VALUE).
"""

from __future__ import annotations

import httpx

from iron_jarvis.connections import probe
from iron_jarvis.daemon.routes.connections import probe_test_words

ODD_KEY = "zq7-PLAINKEYWITHNOKNOWNSHAPE-0123456789abcdef"


class _Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


def _words(provider: str, ok: bool, detail: str) -> dict:
    facts = probe.kind_of(provider, ok, detail)
    result = {"ok": ok, "detail": detail, "kind": facts.kind}
    if facts.status is not None:
        result["status"] = facts.status
    return probe_test_words(result, provider)


def test_a_google_key_refused_with_a_400_reads_as_a_refused_key():
    w = _words(
        "google",
        False,
        "google rejected the key (400) — the key is invalid, revoked, or expired; reconnect it",
    )
    assert "key" in w["title"].lower(), w
    assert "answered with an error" not in w["title"], w
    assert "this chat" not in w["fix"] and "Retry" not in w["fix"], w


def test_a_refused_key_keeps_its_real_status_on_the_result():
    facts = probe.kind_of("google", False, "google rejected the key (400) — x")
    assert facts.kind == "refused_key" and facts.status == 400


def test_a_key_echoed_in_an_error_body_is_scrubbed_from_detail_and_technical(monkeypatch):
    monkeypatch.setattr(
        httpx, "get", lambda *a, **kw: _Resp(400, f'{{"error": "bad key {ODD_KEY} sent"}}')
    )
    res = probe.live_probe("openai", ODD_KEY)
    ok, detail = res
    assert ok is False and res.kind == "http"
    assert ODD_KEY not in detail, detail
    w = probe_test_words({"ok": False, "detail": detail, "kind": res.kind, "status": res.status}, "openai")
    assert ODD_KEY not in w["technical"], w


def test_a_key_in_a_transport_error_is_scrubbed(monkeypatch):
    def boom(*a, **kw):
        raise httpx.ConnectError(f"failed for https://x.test/?access_token={ODD_KEY}")

    monkeypatch.setattr(httpx, "get", boom)
    res = probe.live_probe("openai", ODD_KEY)
    assert res.kind == "unreachable"
    assert ODD_KEY not in res[1], res[1]
