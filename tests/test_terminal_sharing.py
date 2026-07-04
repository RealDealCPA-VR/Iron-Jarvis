"""Cross-terminal context sharing: one pane's work visible to another's model."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.terminals.backend import FakeBackend


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "iron_jarvis.terminals.session.default_backend", lambda: FakeBackend()
    )
    return TestClient(create_app(str(tmp_path)))


def _spy_system_user(client, monkeypatch, captured):
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools):
            captured["user"] = messages[0].content
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = spy
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy_get)


def test_include_terminals_shares_other_pane_output(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    a = client.post("/terminals", json={}).json()
    b = client.post("/terminals", json={}).json()
    # Produce output in A (FakeBackend echoes completed lines via the session).
    platform = client.app.state.platform
    sa = platform.terminals.get(a["id"])
    sa.write("SECRET-FROM-A\n")
    sa.read()  # pump the echo into the tail

    captured = {}
    _spy_system_user(client, monkeypatch, captured)
    r = client.post(
        f"/terminals/{b['id']}/ai",
        json={"prompt": "what happened over there?", "include_terminals": [a["id"]]},
    )
    assert r.status_code == 200
    assert "SECRET-FROM-A" in captured["user"]
    assert "ANOTHER terminal" in captured["user"]


def test_unknown_and_self_ids_are_ignored(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    b = client.post("/terminals", json={}).json()
    captured = {}
    _spy_system_user(client, monkeypatch, captured)
    r = client.post(
        f"/terminals/{b['id']}/ai",
        json={"prompt": "x", "include_terminals": [b["id"], "term_ghost"]},
    )
    assert r.status_code == 200
    assert "ANOTHER terminal" not in captured["user"]


def test_context_endpoint_clean_text(tmp_path, monkeypatch):
    client = _app(tmp_path, monkeypatch)
    a = client.post("/terminals", json={}).json()
    platform = client.app.state.platform
    sa = platform.terminals.get(a["id"])
    sa.write("hello ctx\n")
    sa.read()
    r = client.get(f"/terminals/{a['id']}/context")
    assert r.status_code == 200
    assert "hello ctx" in r.json()["text"]
    assert "Iron Jarvis terminal" in r.json()["text"]
    assert client.get("/terminals/term_nope/context").status_code == 404
