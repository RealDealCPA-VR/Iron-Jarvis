"""v1.449.0 (readiness Wave C, item 25) — the chat's secure card tests a
model provider's key BEFORE it is stored, like the connect door's
``POST /connections/{p}/key?check=1`` (v1.445.0).

Until now the card stored the key, made it the default and said "saved
securely" whatever the key was; a wrong key then failed every answer. Now
``CredentialStore.store`` asks the connect door's own check first: a key
that fails is NOT stored (no vault row, no ledger row, no default change)
and the card gets the Test's words plus "Nothing was saved".
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import UndoJournal
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings.credentials import NOT_SAVED_TAIL, not_saved_words

KEY = "sk-wave-c-test-key-0123456789"


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _prober(ok: bool, calls: list):
    def prober(provider: str, cred: str):
        calls.append((provider, cred))
        if ok:
            return (True, f"{provider} reachable — credential accepted")
        return (False, f"{provider} rejected the credential (401) — the key/token is invalid, revoked, or expired; reconnect it")

    return prober


def _journal_rows(client) -> int:
    with session_scope(client.app.state.platform.engine) as db:
        return len(list(db.exec(select(UndoJournal).where(UndoJournal.kind == "secret_restore"))))


def test_a_key_that_fails_its_check_is_not_stored_and_the_card_is_told(client):
    """MUTATION: store before the check (the old order) -> the key is in the
    vault -> red. MUTATION: raise instead of answering -> a 400 with no
    `saved` -> red."""
    p = client.app.state.platform
    calls: list = []
    p.connections._prober = _prober(False, calls)
    r = client.post("/config/secret", json={"name": "connection.openai", "value": KEY})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "not_saved" and body["saved"] is False
    assert body["test"]["ok"] is False
    assert body["words"].endswith(NOT_SAVED_TAIL)
    assert body["words"].startswith("OpenAI")  # the Test's own headline, named
    assert KEY not in r.text  # the key never comes back
    assert calls == [("openai", KEY)]
    # Nothing landed: no vault row, no ledger row, the default untouched.
    assert not p.connections.has_credential("openai")
    assert _journal_rows(client) == 0
    assert client.get("/health").json()["default_provider"] == "mock"


def test_a_key_that_passes_is_stored_exactly_as_before(client):
    p = client.app.state.platform
    p.connections._prober = _prober(True, [])
    r = client.post("/config/secret", json={"name": "connection.openai", "value": KEY})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "stored"
    assert p.connections.has_credential("openai")
    assert _journal_rows(client) == 1


def test_with_nothing_to_check_the_store_is_unchanged(client):
    """No prober wired (an install without one): stored as before — the
    check never refuses what it cannot test."""
    p = client.app.state.platform
    p.connections._prober = None
    r = client.post("/config/secret", json={"name": "connection.anthropic", "value": "sk-ant-" + KEY})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "stored"
    assert p.connections.has_credential("anthropic")


def test_other_secrets_are_never_probed(client):
    """Only `connection.*` is a provider key the Test can judge."""
    p = client.app.state.platform
    calls: list = []
    p.connections._prober = _prober(False, calls)
    r = client.post("/config/secret", json={"name": "voice_transcribe_key", "value": KEY})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "stored"
    assert calls == []


def test_the_words_lead_with_the_tests_title_or_its_detail():
    worded = not_saved_words({"ok": False, "title": "OpenAI refused the key", "fix": "Check the key.", "detail": "x"})
    assert worded == f"OpenAI refused the key. Check the key. {NOT_SAVED_TAIL}"
    plain = not_saved_words({"ok": False, "detail": "openai probe returned HTTP 503"})
    assert plain == f"Not connected — openai probe returned HTTP 503. {NOT_SAVED_TAIL}"
    assert not_saved_words({"ok": False}) == f"Not connected. {NOT_SAVED_TAIL}"
    assert "`" not in json.dumps([worded, plain])
