"""Daily-driver audit fixes: adapters fail loud on HTTP errors + settings are atomic."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.openai import OpenAIAdapter


class _ErrResp:
    status_code = 401

    def json(self):
        return {"error": {"message": "invalid api key"}}

    text = '{"error":{"message":"invalid api key"}}'


class _ErrHttp:
    async def post(self, *a, **k):
        return _ErrResp()


async def test_openai_xai_adapter_raises_on_http_error_not_blank():
    # A wrong key / bad model / rate-limit must RAISE (so the router falls back +
    # surfaces it), never parse into a blank successful reply. xAI routes through
    # this same adapter, so this covers Grok too.
    a = OpenAIAdapter(api_key="sk-bad", http=_ErrHttp())
    with pytest.raises(RuntimeError) as ei:
        await a.complete(system="s", messages=[], tools=[])
    assert "401" in str(ei.value) and "invalid api key" in str(ei.value)


def test_settings_put_is_atomic(tmp_path):
    # One valid + one invalid key in the same request -> 400 AND nothing applied
    # (no partial mutation that could brick the running config).
    client = TestClient(create_app(str(tmp_path)))
    before = client.get("/settings").json()["settings"]
    r = client.put(
        "/settings",
        json={"values": {"default_model": "claude-sonnet-4-6", "autonomy_level": "BOGUS"}},
    )
    assert r.status_code == 400
    after = client.get("/settings").json()["settings"]
    assert after["default_model"] == before["default_model"]  # valid key NOT applied


def test_briefing_get_is_read_only_post_pushes(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert client.get("/autonomy/briefing").status_code == 200  # read-only
    assert client.post("/autonomy/briefing").status_code == 200  # push path is POST
    # the side-effecting notify is no longer reachable by a bare GET query param
    assert "notify" not in client.get("/autonomy/briefing").json()


def test_diagnostics_exposes_background_loops(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    assert "background_loops" in client.get("/diagnostics").json()
