"""The drive sign-in boxes, driven end to end (v1.449.0, readiness Wave C
item 22 review).

The dashboard's form (``components/connections/OAuthAppForm.tsx``) saves
``<provider>_oauth_client_id`` and ``<provider>_oauth_client_secret`` through
``POST /secrets`` — the names ``platform._oauth_app`` reads. This file drives
the REAL daemon with exactly those two posts and checks what the form's
words promise: the card's ``oauth_client_configured`` flips, the sign-in
starts and its authorize URL carries the id and the redirect address the
form showed, and the secret is echoed by nothing — not the save, not the
status rows, not the URL. A secret saved alone configures nothing (the id
is what the vendor uses to know which app is asking).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

REPO = Path(__file__).resolve().parents[1]
FORM = REPO / "dashboard" / "components" / "connections" / "OAuthAppForm.tsx"
SECRET = "GOCSPX-not-a-real-secret-value"
CLIENT = "123456-abc.apps.googleusercontent.com"


@pytest.fixture
def client(tmp_path) -> TestClient:
    app = create_app(str(tmp_path))
    c = TestClient(app)
    c.app = app  # type: ignore[attr-defined]
    return c


def _platform(client: TestClient):
    return client.app.state.platform  # type: ignore[attr-defined]


def _drive_row(client: TestClient) -> dict:
    return next(r for r in _platform(client).connections.status() if r["provider"] == "google_drive")


def _form_posts(client: TestClient, provider: str, *, client_id: str | None, secret: str | None) -> list[dict]:
    """The two POSTs the form makes (its own bodies: name, value, kind, description)."""
    out = []
    if client_id is not None:
        out.append(
            client.post(
                "/secrets",
                json={"name": f"{provider}_oauth_client_id", "value": client_id, "kind": "oauth_client",
                      "description": "Google Drive app ID (for its sign-in)"},
            ).json()
        )
    if secret is not None:
        out.append(
            client.post(
                "/secrets",
                json={"name": f"{provider}_oauth_client_secret", "value": secret, "kind": "oauth_client",
                      "description": "Google Drive app secret (for its sign-in)"},
            ).json()
        )
    return out


def test_the_saved_names_make_the_drives_sign_in_start_with_the_shown_redirect(client):
    assert _drive_row(client)["oauth_client_configured"] is False
    answers = _form_posts(client, "google_drive", client_id=CLIENT, secret=SECRET)
    # The save answers names only — never the value.
    assert all(set(a) == {"name", "kind"} for a in answers), answers
    assert _drive_row(client)["oauth_client_configured"] is True
    r = client.get("/oauth/google_drive/start")
    assert r.status_code == 200, r.text
    url = r.json()["authorization_url"]
    q = parse_qs(urlparse(url).query)
    assert q["client_id"] == [CLIENT]
    # The redirect the form told the user to allow is the one the daemon sends.
    assert q["redirect_uri"] == ["http://localhost:8787/oauth/google_drive/callback"]
    assert SECRET not in url


def test_the_secret_is_echoed_by_nothing(client):
    _form_posts(client, "google_drive", client_id=CLIENT, secret=SECRET)
    everywhere = [
        json.dumps(_platform(client).connections.status()),
        client.get("/secrets").text,
        client.get("/connections").text,
        client.get("/connectors").text,
        client.get("/oauth/google_drive/start").text,
    ]
    assert not any(SECRET in text for text in everywhere)


def test_a_secret_alone_configures_nothing_and_the_sign_in_cannot_start(client):
    """The form refuses this before any post (OAUTH_APP_NEEDS_ID); the daemon
    agrees: with no id saved the card still says 'needs the app' and the
    start is refused in words, never a half-configured sign-in."""
    _form_posts(client, "google_drive", client_id=None, secret=SECRET)
    assert _drive_row(client)["oauth_client_configured"] is False
    r = client.get("/oauth/google_drive/start")
    assert r.status_code >= 400
    assert "client" in r.text.lower()


def test_every_drive_the_form_serves_reaches_the_same_door(client):
    for provider in ("google_drive", "onedrive", "dropbox"):
        _form_posts(client, provider, client_id=f"{provider}-app-id", secret=None)
        r = client.get(f"/oauth/{provider}/start")
        assert r.status_code == 200, (provider, r.text)
        q = parse_qs(urlparse(r.json()["authorization_url"]).query)
        assert q["client_id"] == [f"{provider}-app-id"]
        assert q["redirect_uri"] == [f"http://localhost:8787/oauth/{provider}/callback"]


def test_the_callback_the_redirect_names_is_a_route_that_needs_no_bearer():
    """The vendor's browser redirect carries no Authorization header."""
    from iron_jarvis.daemon import auth

    assert auth._is_exempt("/oauth/google_drive/callback") is True  # noqa: SLF001
    assert auth._is_exempt("/oauth/google_drive/start") is False  # noqa: SLF001


def test_the_form_refuses_a_secret_alone_in_words_and_posts_the_id_first():
    src = FORM.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert 'OAUTH_APP_NEEDS_ID = "Paste the app’s ID first' in src
    body = src[src.index("async function save") :]
    # The id is checked, and posted, before the secret is ever sent.
    assert body.index("if (!id)") < body.index("oauthClientIdName(provider)") < body.index("oauthClientSecretName(provider)")
    # The secret box never shows its value back: a password field, cleared after Save.
    assert re.search(r'type="password"[\s\S]{0,200}value=\{secret\}', src)
    assert 'setSecret("")' in src
