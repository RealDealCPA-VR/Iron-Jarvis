"""Editable personas: the store, the /chat/personas CRUD, resolve_prompt, and
the chat workspace_dir guard — all offline.

Built-ins (assistant/developer/accountant/writer/researcher) ship in-memory in
the daemon (``_PERSONAS``); the store (``PersonaRecord`` table) holds the user's
OVERRIDES + CREATIONS. ``merged`` is the effective catalog the picker shows,
``resolve_prompt`` is what the chat handler turns a persona name into. Every
persona is fully editable (carries its prompt), a built-in name → an override,
and deleting reverts a built-in / removes a custom one. Restart-survival is
proven by re-opening a second app on the same root. The plain /chat turn runs
against the offline mock provider (200 + a reply); workspace_dir only affects the
ARMED-tools path, so a plain turn accepts-and-ignores it either way.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.personas import PersonaStore, resolve_prompt, slugify


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _by_name(personas: list[dict], name: str) -> dict | None:
    return next((p for p in personas if p["name"] == name), None)


# --------------------------------------------------------------------------- #
# (1) slugify: a stable, filesystem-free id; empty/symbol-only → "persona".
# --------------------------------------------------------------------------- #
def test_slugify():
    assert slugify("Tax Ninja!") == "tax-ninja"
    assert slugify("") == "persona"
    assert slugify("@#$%") == "persona"
    # Consecutive separators collapse and edges are trimmed.
    assert slugify("  My   Dev  ") == "my-dev"


# --------------------------------------------------------------------------- #
# (2) GET returns the built-ins, fully editable (prompt + title carried).
# --------------------------------------------------------------------------- #
def test_get_returns_editable_builtins(tmp_path):
    client = _client(tmp_path)
    r = client.get("/chat/personas")
    assert r.status_code == 200
    personas = r.json()["personas"]
    names = {p["name"] for p in personas}
    assert {"assistant", "developer", "accountant", "writer", "researcher"} <= names
    assert len(personas) >= 5

    dev = _by_name(personas, "developer")
    assert dev is not None
    assert dev["builtin"] is True
    assert dev["overridden"] is False
    assert dev["prompt"].strip()   # non-empty system prompt
    assert dev["title"].strip()    # non-empty display title
    # Every entry carries the full, editable shape.
    assert set(dev) >= {"name", "title", "description", "prompt", "builtin", "overridden"}


# --------------------------------------------------------------------------- #
# (3) PUT overrides a built-in (blank prompt → 400).
# --------------------------------------------------------------------------- #
def test_put_overrides_builtin(tmp_path):
    client = _client(tmp_path)
    r = client.put(
        "/chat/personas/developer",
        json={"title": "My Dev", "description": "d", "prompt": "You are MY dev."},
    )
    assert r.status_code == 200

    dev = _by_name(client.get("/chat/personas").json()["personas"], "developer")
    assert dev is not None
    assert dev["title"] == "My Dev"
    assert dev["overridden"] is True
    assert dev["builtin"] is True
    assert dev["prompt"] == "You are MY dev."

    # A blank prompt is rejected — a persona without a prompt is meaningless.
    assert client.put(
        "/chat/personas/developer", json={"title": "x", "prompt": "   "}
    ).status_code == 400


# --------------------------------------------------------------------------- #
# (4) resolve_prompt: override wins → built-in → free-text verbatim → default.
# --------------------------------------------------------------------------- #
def test_resolve_prompt_precedence(tmp_path):
    client = _client(tmp_path)
    store = PersonaStore(client.app.state.platform.engine)
    store.upsert("developer", title="My Dev", description="d", prompt="You are MY dev.")

    builtins = {"developer": {"prompt": "builtin", "description": ""}}
    # A user override for a built-in name wins over the built-in prompt.
    assert resolve_prompt(store, builtins, "developer") == "You are MY dev."
    # An unknown name that is neither saved nor built-in is free-text, verbatim.
    assert resolve_prompt(store, builtins, "unknown free text") == "unknown free text"
    # An empty want falls back to the default assistant prompt.
    assert resolve_prompt(store, {"assistant": {"prompt": "A"}}, "") == "A"


# --------------------------------------------------------------------------- #
# (5) POST creates a NEW persona (slug from title), listed as non-builtin.
# --------------------------------------------------------------------------- #
def test_post_creates_persona(tmp_path):
    client = _client(tmp_path)
    r = client.post("/chat/personas", json={"title": "Tax Ninja", "prompt": "You are a CPA."})
    assert r.status_code == 200
    assert r.json()["created"] == "tax-ninja"

    ninja = _by_name(client.get("/chat/personas").json()["personas"], "tax-ninja")
    assert ninja is not None
    assert ninja["builtin"] is False
    assert ninja["prompt"] == "You are a CPA."


# --------------------------------------------------------------------------- #
# (6) DELETE reverts a built-in / deletes a custom one; 404 for neither.
# --------------------------------------------------------------------------- #
def test_delete_reverts_and_deletes(tmp_path):
    client = _client(tmp_path)
    # Override then revert a built-in.
    client.put(
        "/chat/personas/developer",
        json={"title": "My Dev", "description": "d", "prompt": "You are MY dev."},
    )
    d = client.delete("/chat/personas/developer")
    assert d.status_code == 200
    assert d.json()["reverted_to_builtin"] is True

    dev = _by_name(client.get("/chat/personas").json()["personas"], "developer")
    assert dev is not None
    assert dev["overridden"] is False
    assert dev["title"] == "Developer"   # back to the built-in default title

    # Delete a CUSTOM persona → not a built-in, and it's gone.
    client.post("/chat/personas", json={"title": "Tax Ninja", "prompt": "You are a CPA."})
    d2 = client.delete("/chat/personas/tax-ninja")
    assert d2.status_code == 200
    assert d2.json()["reverted_to_builtin"] is False
    assert _by_name(client.get("/chat/personas").json()["personas"], "tax-ninja") is None

    # Neither saved nor a built-in → 404.
    assert client.delete("/chat/personas/ghost-persona-zzz").status_code == 404


# --------------------------------------------------------------------------- #
# (7) The chat surface resolves the persona (before AND after an override).
# --------------------------------------------------------------------------- #
def test_chat_uses_resolved_persona(tmp_path):
    client = _client(tmp_path)
    r = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "persona": "developer"},
    )
    assert r.status_code == 200
    assert r.json().get("reply")

    # After overriding the built-in, the same persona still resolves + replies.
    client.put(
        "/chat/personas/developer",
        json={"title": "My Dev", "description": "d", "prompt": "You are MY dev."},
    )
    r2 = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hi"}], "persona": "developer"},
    )
    assert r2.status_code == 200
    assert r2.json().get("reply")


# --------------------------------------------------------------------------- #
# (8) Restart survival: a persona override persists across a fresh app.
# --------------------------------------------------------------------------- #
def test_override_survives_restart(tmp_path):
    root = str(tmp_path)
    with TestClient(create_app(root)) as client:
        client.put(
            "/chat/personas/developer",
            json={"title": "My Dev", "description": "d", "prompt": "You are MY dev."},
        )
        dev = _by_name(client.get("/chat/personas").json()["personas"], "developer")
        assert dev["overridden"] is True and dev["title"] == "My Dev"

    # A second app on the SAME root re-reads the persisted override.
    with TestClient(create_app(root)) as client2:
        dev2 = _by_name(client2.get("/chat/personas").json()["personas"], "developer")
        assert dev2 is not None
        assert dev2["overridden"] is True
        assert dev2["title"] == "My Dev"
        assert dev2["prompt"] == "You are MY dev."


# --------------------------------------------------------------------------- #
# (9) workspace_dir: accepted on a plain (no-tools) turn either way.
# --------------------------------------------------------------------------- #
def test_workspace_dir_accepted(tmp_path):
    client = _client(tmp_path)
    ws = tmp_path / "workspace"
    ws.mkdir()

    # An existing dir, no tools armed → the body is accepted (200); the
    # workspace only matters on the armed-tools path, which isn't taken here.
    r = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hi"}],
              "workspace_dir": str(ws), "tools": []},
    )
    assert r.status_code == 200

    # A nonexistent / malformed workspace_dir is guarded — still 200.
    r2 = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hi"}],
              "workspace_dir": str(tmp_path / "does-not-exist"), "tools": []},
    )
    assert r2.status_code == 200
    r3 = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "hi"}],
              "workspace_dir": "not/absolute/./garbage", "tools": []},
    )
    assert r3.status_code == 200
