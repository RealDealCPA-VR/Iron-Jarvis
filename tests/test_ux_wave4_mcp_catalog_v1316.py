"""v1.316.0 (UX wave 4, T5 connections & catalogs) — two additive daemon facts
the dashboard needs to stop crying wolf.

1. ``GET /mcp/catalog`` rows carry ``runtime_ready: bool``.

   The Tools page painted EVERY extension with an amber "Needs Node" /
   "Needs Python (uv)" warning, on a machine that runs Node, because ``needs``
   is a static catalog field. ``runtime_ready`` answers "would the launcher
   find this pack's command right now?" — and it must use the SAME resolution
   the MCP launcher uses (``mcp.tools.resolve_launcher`` → ``ai_clis._find``:
   real PATH, then the per-user bin dirs a GUI-launched daemon's PATH misses).
   A ``shutil.which``-only answer would say "not ready" for a Node that the
   launcher WILL find (and the reverse lie — "ready" for a pack that then fails
   to launch — is worse than the warning). Computed per request, never cached
   into the shared catalog list, never a 500.

2. ``GET /connections`` rows carry ``oauth_client_configured: bool``.

   The Dropbox / Drive / OneDrive cards promised "Log in with your account"
   and only AFTER a 400 said the user had to register their own OAuth app. The
   daemon never said whether a client id exists; the card cannot be honest
   without it. True only when an OAuth login could actually start (the
   registry's own ``oauth_app`` resolver or the spec's embedded public id);
   always a bool — never the id itself.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.connections.models  # noqa: F401  (register table before init_db)
from iron_jarvis.connections import ConnectionRegistry, ConnectionSpec
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcp.tools import resolve_launcher
from iron_jarvis.terminals import ai_clis


# --------------------------------------------------------------------------- #
#  /mcp/catalog — runtime_ready                                                 #
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_path):
    # Built BEFORE any resolution is patched, so boot is untouched.
    return TestClient(create_app(str(tmp_path / "home")))


@pytest.fixture
def bin_dir(tmp_path, monkeypatch):
    """A per-user bin dir the launcher searches, with the real PATH emptied.

    ``shutil.which`` answers None for everything — the GUI-launched daemon whose
    PATH lacks the user's Node — so ONLY the launcher's per-user-dir fallback
    can find a command dropped in here."""
    d = tmp_path / "userbin"
    d.mkdir()
    monkeypatch.setattr(ai_clis.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(ai_clis, "_extra_bin_dirs", lambda: [d])
    return d


def _drop(bin_dir, name: str) -> None:
    # Both spellings: ``_find`` tries ".cmd" on Windows and the bare name
    # everywhere else.
    (bin_dir / name).write_text("", encoding="utf-8")
    (bin_dir / f"{name}.cmd").write_text("", encoding="utf-8")


def _catalog(client) -> list[dict]:
    r = client.get("/mcp/catalog")
    assert r.status_code == 200, r.text
    return r.json()["catalog"]


def _launcher_finds(command: str) -> bool:
    try:
        resolve_launcher(command)
        return True
    except FileNotFoundError:
        return False


def test_every_row_says_runtime_ready_as_a_real_bool(client, bin_dir):
    cat = _catalog(client)
    assert len(cat) >= 5  # anti-vacuity: the real catalog, not an empty list
    for row in cat:
        assert "runtime_ready" in row, row["id"]
        assert type(row["runtime_ready"]) is bool, row["id"]


def test_node_found_only_in_a_per_user_dir_is_ready(client, bin_dir):
    """THE discriminating case: the real PATH has no npx (shutil.which → None),
    but the launcher's per-user dir does. The launcher WILL start these packs,
    so the catalog must not warn — a which-only answer would."""
    _drop(bin_dir, "npx")
    cat = _catalog(client)
    npx_rows = [r for r in cat if r["command"] == "npx"]
    uvx_rows = [r for r in cat if r["command"] == "uvx"]
    assert npx_rows and uvx_rows  # both runtimes are represented in the catalog
    assert all(r["runtime_ready"] is True for r in npx_rows), [
        r["id"] for r in npx_rows if r["runtime_ready"] is not True
    ]
    # uv is not installed here: those stay honestly NOT ready.
    assert all(r["runtime_ready"] is False for r in uvx_rows)


def test_nothing_installed_means_nothing_ready_and_needs_is_kept(client, bin_dir):
    cat = _catalog(client)
    assert all(r["runtime_ready"] is False for r in cat)
    # The requirement itself stays on every row (the UI keeps "Needs X" in
    # the chip's title) and nothing else about the row changed.
    by_id = {r["id"]: r for r in cat}
    assert by_id["filesystem"]["needs"] == "Node"
    assert by_id["fetch"]["needs"] == "Python (uv)"
    for r in cat:
        assert {"id", "name", "description", "command", "args", "category", "needs"} <= set(r)


def test_agrees_with_the_launcher_row_by_row(client, bin_dir):
    """Same environment, same answer as ``resolve_launcher`` — for every row."""
    _drop(bin_dir, "uvx")
    cat = _catalog(client)
    for r in cat:
        assert r["runtime_ready"] is _launcher_finds(r["command"]), r["id"]
    # And the answer is not constant (anti-vacuity for the loop above).
    assert {r["runtime_ready"] for r in cat} == {True, False}


def test_answer_is_fresh_per_request_not_cached(client, bin_dir):
    """A user installs uv and reloads Tools: the next read says so. Also
    catches writing the flag INTO the shared catalog list on first read."""
    first = {r["id"]: r["runtime_ready"] for r in _catalog(client)}
    assert first["fetch"] is False
    _drop(bin_dir, "uvx")
    second = {r["id"]: r["runtime_ready"] for r in _catalog(client)}
    assert second["fetch"] is True
    assert second["filesystem"] is False  # npx still absent


def test_a_resolution_fault_is_not_ready_never_a_500(client, monkeypatch):
    def boom(_cmd):
        raise OSError("permission denied reading a PATH dir")

    monkeypatch.setattr(ai_clis, "_find", boom)
    cat = _catalog(client)  # asserts 200
    assert cat and all(r["runtime_ready"] is False for r in cat)


# --------------------------------------------------------------------------- #
#  /connections — oauth_client_configured                                      #
# --------------------------------------------------------------------------- #


class FakeSecrets:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def set(self, name, value, kind="generic", description=""):
        self.store[name] = value
        return {"name": name, "kind": kind}

    def get(self, name):
        return self.store.get(name)

    def set_oauth(self, name, token, description=""):
        self.store[name] = json.dumps(token)
        return {"name": name, "kind": "oauth"}

    def get_oauth(self, name):
        raw = self.store.get(name)
        return json.loads(raw) if raw is not None else None

    def delete(self, name):
        return self.store.pop(name, None) is not None


def _registry(tmp_path, oauth_app=None) -> ConnectionRegistry:
    engine = make_engine(str(tmp_path / "c.db"))
    init_db(engine)
    return ConnectionRegistry(engine, FakeSecrets(), oauth_app=oauth_app)


def _rows(reg: ConnectionRegistry) -> dict[str, dict]:
    return {r["provider"]: r for r in reg.status()}


def test_drives_with_no_registered_app_say_not_configured(tmp_path):
    rows = _rows(_registry(tmp_path))
    for p in ("dropbox", "google_drive", "onedrive"):
        assert rows[p]["supports_oauth"] is True  # still an OAuth card
        assert rows[p]["oauth_client_configured"] is False, p


def test_a_resolved_client_id_says_configured(tmp_path):
    secret_id = "client-abc.apps.example"

    def resolver(provider):
        return {"client_id": secret_id, "client_secret": "shh"} if provider == "dropbox" else {}

    reg = _registry(tmp_path, resolver)
    rows = _rows(reg)
    assert rows["dropbox"]["oauth_client_configured"] is True
    assert rows["onedrive"]["oauth_client_configured"] is False
    # It is a FLAG — the id and the secret never ride the status payload.
    dumped = json.dumps(reg.status())
    assert secret_id not in dumped
    assert "shh" not in dumped
    # Agreement with the real start path: configured ⇒ start_oauth works,
    # not configured ⇒ it raises (the 400 the card used to discover late).
    assert reg.start_oauth("dropbox")["authorization_url"]
    with pytest.raises(ValueError):
        reg.start_oauth("onedrive")


def test_an_embedded_public_client_says_configured(tmp_path):
    reg = _registry(tmp_path)
    reg.register(
        ConnectionSpec(
            provider="publicapp",
            display_name="Public app",
            method="oauth",
            auth_url="https://auth.example/authorize",
            token_url="https://auth.example/token",
            oauth_client_id="embedded-public-id",
        )
    )
    row = _rows(reg)["publicapp"]
    assert row["oauth_client_configured"] is True
    assert "embedded-public-id" not in json.dumps(row)


def test_key_only_providers_say_false_even_with_oauth_help(tmp_path):
    """xAI carries oauth_help but is API-key only — the flag must not make the
    UI offer it an account-login setup."""
    rows = _rows(_registry(tmp_path))
    assert rows["xai"]["oauth_help"]  # the trap: help text present
    assert rows["xai"]["supports_oauth"] is False
    assert rows["xai"]["oauth_client_configured"] is False
    assert rows["openrouter"]["oauth_client_configured"] is False


def test_a_resolver_fault_reads_not_configured_never_blanks_the_page(tmp_path):
    def resolver(_provider):
        raise RuntimeError("vault locked")

    rows = _rows(_registry(tmp_path, resolver))  # must not raise
    assert rows["dropbox"]["oauth_client_configured"] is False
    assert len(rows) >= 10  # every card still listed


def test_the_real_route_carries_the_flag_and_flips_on_the_secret(tmp_path):
    """Through the REAL app factory + platform resolver (the vault secret the
    amber fallback note tells the user to set)."""
    c = TestClient(create_app(str(tmp_path / "home")))
    rows = {r["provider"]: r for r in c.get("/connections").json()["connections"]}
    assert rows["dropbox"]["oauth_client_configured"] is False
    c.app.state.platform.secrets.set("dropbox_oauth_client_id", "my-dropbox-app")
    rows = {r["provider"]: r for r in c.get("/connections").json()["connections"]}
    assert rows["dropbox"]["oauth_client_configured"] is True
    assert rows["google_drive"]["oauth_client_configured"] is False


def test_a_key_only_provider_stays_false_even_when_a_client_id_exists(tmp_path):
    """Coordinator (v1.316.0): with a resolver that hands EVERY provider a
    client id, a key-only provider (xAI, OpenRouter) still reads False — the
    `supports_oauth` guard, not the missing id, is what keeps the card from
    offering an account login it cannot do."""
    rows = _rows(_registry(tmp_path, lambda _p: {"client_id": "x"}))
    assert rows["xai"]["oauth_client_configured"] is False
    assert rows["openrouter"]["oauth_client_configured"] is False
    assert rows["dropbox"]["oauth_client_configured"] is True  # control
