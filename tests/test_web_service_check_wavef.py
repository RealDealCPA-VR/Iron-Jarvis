"""Readiness 0j.5 item 18 (a) — ``web_service_add`` runs the service's OWN
Test and says "Added and answering", or why not.

Before, chat's add said "Added the web service … Jarvis can call it with
rest_call now" whether or not anything answered at that address. Now the
saved service's own Test (``integrations.test``, the Connections page's Test
button: one GET, the same SSRF guard, its key as a bearer token) runs once it
is saved — and, for a keyed service, once the secure card saves its key — and
its verdict is said in words (never the address's raw status line).

The HTTP GET is a stand-in (``integrations.builtin._httpx_get``): nothing
leaves this PC.
"""

from __future__ import annotations

import json

import pytest

from iron_jarvis.integrations import builtin as B
from iron_jarvis.settings import records_tools as RT
from tests.test_records_tools_v14480 import (
    SENTINELS,
    _app,
    _approve_all,
    _asgi_post,
    _body,
    _drive_stream,
    _finished,
    _text,
)


@pytest.fixture
def checks_on(monkeypatch):
    monkeypatch.setattr(RT, "AUTO_CHECK", True)
    seen: list = []

    def answer(status: int):
        def get(url, headers=None):
            seen.append({"url": url, "headers": dict(headers or {})})
            return {"ok": 200 <= status < 300, "status_code": status, "text": ""}

        monkeypatch.setattr(B, "_httpx_get", get)

    return answer, seen


@pytest.fixture(autouse=True)
def _fresh():
    with RT._PENDING_LOCK:
        RT._PENDING.clear()
    RT.tools_tools_for.cache_clear()
    yield


async def _add(tmp_path, args, text):
    app = _app(tmp_path, [("web_service_add", args)])
    seen: list = []
    frames = await _drive_stream(app, _body(text), await _approve_all(app, seen))
    return app, frames


@pytest.mark.asyncio
async def test_a_service_that_answers_is_added_and_answering(tmp_path, checks_on):
    answer, gets = checks_on
    answer(200)
    _app_, frames = await _add(
        tmp_path,
        {"name": "Pricing", "address": "https://api.example.com/v1"},
        "add a web service called Pricing at https://api.example.com/v1",
    )
    out = _text(_finished(frames, "web_service_add"))
    assert "Added and answering: “Pricing” replied to a test request." in out
    assert [g["url"] for g in gets] == ["https://api.example.com/v1"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status, why",
    [
        (401, "it refused the test request"),
        (404, "nothing answered at that address (404)"),
        (503, "it had a problem of its own (503)"),
    ],
)
async def test_a_service_that_does_not_answer_says_why(tmp_path, checks_on, status, why):
    answer, _ = checks_on
    answer(status)
    _a, frames = await _add(
        tmp_path,
        {"name": "Pricing", "address": "https://api.example.com/v1"},
        "add a web service called Pricing at https://api.example.com/v1",
    )
    out = _text(_finished(frames, "web_service_add"))
    assert "Added, but it is not answering: " + why in out
    assert "Added and answering" not in out
    assert f"-> {status}" not in out, "the raw status line is never the words"


@pytest.mark.asyncio
async def test_a_keyed_service_is_checked_once_its_key_is_saved(tmp_path, checks_on):
    answer, gets = checks_on
    answer(200)
    app, frames = await _add(
        tmp_path,
        {"name": "Pricing", "address": "https://api.example.com/v1", "needs_key": True},
        "add a web service called Pricing at https://api.example.com/v1 with a bearer key",
    )
    out = _text(_finished(frames, "web_service_add"))
    assert RT.SERVICE_CHECK_LATER in out
    assert gets == [], "nothing is sent before the key is saved"
    status, raw = await _asgi_post(
        app, "/config/secret", {"name": "web_service.pricing", "value": SENTINELS["service"]}
    )
    assert status == 200, raw
    raw = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
    check = json.loads(raw)["check"]
    assert check["answering"] is True
    assert check["words"].startswith("Added and answering")
    assert SENTINELS["service"] not in raw
    assert gets and gets[-1]["headers"].get("Authorization") == f"Bearer {SENTINELS['service']}"


def test_the_reason_words_never_carry_the_address():
    assert RT._check_reason("GET https://secret.example.com/x -> 401").startswith("it refused")
    assert "secret.example.com" not in RT._check_reason("ConnectError: secret.example.com")
    assert RT._check_reason("refused unsafe base_url: x") == "Iron Jarvis would not send a request to that address"
