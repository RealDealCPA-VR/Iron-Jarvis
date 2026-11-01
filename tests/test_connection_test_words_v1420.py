"""A connection Test speaks plainly (v1.420.0, C6.6).

The key door and Connections printed the probe's raw text ("could not reach
anthropic: ConnectError: [Errno 11001] getaddrinfo failed", "openai probe
returned HTTP 500: {json}", "served through the logged-in claude CLI"). Now
the probe answers TYPED facts (``connections.probe.ProbeResult``: ``kind``,
``status``), the registry passes them on, and ``POST /connections/{p}/test``
adds ``title`` / ``fix`` (a refused key and an error status through
``failure_words.friendly_error``) and the masked ``technical`` text, keeping
``detail`` as it was for a headless caller.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from iron_jarvis.connections import probe
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes.connections import probe_test_words

KEY = "sk-ant-api03-ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"


class _Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text


# --------------------------------------------------------------------------- #
# The probe answers typed facts (the four shapes the plan names)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "answer, kind, status, ok",
    [
        (httpx.ConnectError("[Errno 11001] getaddrinfo failed"), "unreachable", None, False),
        (_Resp(401), "refused_key", 401, False),
        (_Resp(500, '{"error": {"type": "api_error", "message": "boom"}}'), "http", 500, False),
        (_Resp(200), "ok", None, True),
    ],
)
def test_the_probe_answers_typed_facts(monkeypatch, answer, kind, status, ok):
    def fake_get(*a, **kw):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(httpx, "get", fake_get)
    res = probe.live_probe("anthropic", KEY)
    assert res.kind == kind and res.status == status and res[0] is ok
    # still the (ok, detail) pair every caller unpacks
    got_ok, detail = res
    assert got_ok is ok and isinstance(detail, str)


def test_a_plain_pair_is_read_off_the_probes_own_words_only():
    assert probe.kind_of("pixio", False, "pixio probe returned HTTP 502: x").kind == "http"
    assert probe.kind_of("pixio", False, "could not reach pixio: ConnectError").kind == "unreachable"
    assert probe.kind_of("google", False, "google rejected the key (403) — …").status == 403
    assert probe.kind_of("x", False, "something nobody wrote").kind == ""


# --------------------------------------------------------------------------- #
# The words (table over the four shapes + the inherited one)
# --------------------------------------------------------------------------- #
WORDS = [
    (
        {"ok": False, "kind": "unreachable", "detail": "could not reach anthropic: ConnectError: [Errno 11001] x"},
        "Couldn't reach Anthropic to check the key",
        "Check your internet connection, then press Test again.",
    ),
    (
        {"ok": False, "kind": "refused_key", "status": 401, "detail": "anthropic rejected the credential (401)"},
        "Anthropic refused your key",
        "Update it in Settings › Connections.",
    ),
    (
        {"ok": False, "kind": "http", "status": 500, "detail": 'anthropic probe returned HTTP 500: {"error":1}'},
        "Anthropic is busy",
        "That says nothing about the key. Press Test again in a moment.",
    ),
    (
        {"ok": True, "kind": "inherited", "via": "claude-cli",
         "detail": "Anthropic is served through the logged-in claude CLI — no API key is stored"},
        "Anthropic works through your Claude Code sign-in",
        "No API key is needed.",
    ),
]


@pytest.mark.parametrize("result, title, fix", WORDS)
def test_each_shape_reads_in_plain_words(result, title, fix):
    w = probe_test_words(result, "anthropic")
    assert w["title"] == title and w["fix"] == fix
    joined = w["title"] + " " + w["fix"]
    for raw in ("Errno", "ConnectError", "HTTP 500", "logged-in", " CLI", "probe", "{"):
        assert raw not in joined, raw
    # the technical text is kept, folded, only for a failure
    if result["ok"]:
        assert w["technical"] == ""
    else:
        assert w["technical"] == result["detail"][:400]


def test_the_technical_text_is_masked():
    w = probe_test_words(
        {"ok": False, "kind": "http", "status": 502, "detail": f"x probe returned HTTP 502: key {KEY}"},
        "anthropic",
    )
    assert KEY not in w["technical"]


# --------------------------------------------------------------------------- #
# Through the route
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def test_the_test_route_adds_the_words_and_keeps_detail(client):
    p = client.app.state.platform
    assert client.post("/connections/anthropic/key", json={"key": KEY}).status_code == 200
    p.connections._prober = lambda provider, cred: probe.ProbeResult(
        False,
        "could not reach anthropic: ConnectError: [Errno 11001] getaddrinfo failed",
        kind="unreachable",
        provider=provider,
    )
    body = client.post("/connections/anthropic/test").json()
    assert body["ok"] is False
    assert body["detail"].startswith("could not reach anthropic")  # unchanged for headless callers
    assert body["kind"] == "unreachable"
    assert body["title"] == "Couldn't reach Anthropic to check the key"
    assert body["fix"] == "Check your internet connection, then press Test again."
    assert "Errno 11001" in body["technical"]


def test_a_plain_pair_prober_still_gets_words(client):
    p = client.app.state.platform
    assert client.post("/connections/anthropic/key", json={"key": KEY}).status_code == 200
    p.connections._prober = lambda provider, cred: (False, "anthropic probe returned HTTP 503: down")
    body = client.post("/connections/anthropic/test").json()
    assert body["kind"] == "http" and body["status"] == 503
    assert body["title"] == "Anthropic is busy"
