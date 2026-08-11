"""Editing a registered remote agent (v1.164.0).

REPORTED: "when connecting a remote agent, after i have it it only have an
option to test the agent or delete it, there should also be an edit option so i
dont need to start from scratch on a remote agent that may have had the wrong
config."

THE TRAP THIS ENDPOINT EXISTS TO AVOID. `POST /agents/remote` already upserts by
name, so "edit" looks like it needs no backend at all — just re-post the form.
It does not work, and the way it fails is silent: `RemoteAgentRegistry.upsert`
assigns EVERY column, including `row.secret_name = secret_name`, and the bearer
token is stored encrypted and NEVER returned, so no UI can prefill it. A re-post
therefore carries an empty token, clears `secret_name`, and leaves a remote that
authenticated a minute ago failing — with the credential gone from the vault and
the user unable to retype what they no longer have. That is strictly worse than
the "start from scratch" the request is trying to avoid.

So the credential has THREE distinct intents and this file exists to keep them
apart: replace it (send a token), keep it (send nothing), remove it
(`clear_token`). Conflating any two of them loses a secret.

`name` is intentionally not editable — it is the identity panels and threads
refer to (`participantKey("remote", name)`), and renaming would orphan those
references without saying so.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


def _client(tmp_path):
    return TestClient(create_app(str(tmp_path)))


def _add(client, **over):
    body = {
        "name": "my-hermes",
        "base_url": "http://192.168.1.20:8080",
        "kind": "http-task",
        "token": "super-secret-bearer",
        "enabled": True,
    }
    body.update(over)
    r = client.post("/agents/remote", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _get(client, name="my-hermes"):
    listing = client.get("/agents/remote").json()["agents"]
    return next(a for a in listing if a["name"] == name)


# --------------------------------------------------------------------------- #
# (1) THE POINT: fix one field without re-entering the rest.
# --------------------------------------------------------------------------- #
def test_a_mistyped_base_url_can_be_corrected(tmp_path):
    with _client(tmp_path) as client:
        _add(client)
        r = client.patch(
            "/agents/remote/my-hermes", json={"base_url": "http://192.168.1.21:9090"}
        )
        assert r.status_code == 200, r.text
        assert _get(client)["base_url"] == "http://192.168.1.21:9090"


def test_everything_else_is_left_exactly_as_it_was(tmp_path):
    """A patch that names one field must not quietly reset the others to their
    schema defaults — the failure mode of reusing the create body."""
    with _client(tmp_path) as client:
        _add(client, kind="openai-chat", model="llama3", timeout_s=45)
        before = _get(client)
        client.patch("/agents/remote/my-hermes", json={"base_url": "http://new:1"})
        after = _get(client)
        assert after["kind"] == before["kind"] == "openai-chat"
        assert after["model"] == before["model"] == "llama3"
        assert after["timeout_s"] == before["timeout_s"] == 45
        assert after["enabled"] == before["enabled"] is True


# --------------------------------------------------------------------------- #
# (2) THE CREDENTIAL — three intents, and losing one is unrecoverable.
# --------------------------------------------------------------------------- #
def test_an_edit_that_does_not_mention_the_token_KEEPS_it(tmp_path):
    """The headline. The UI cannot prefill the token, so every ordinary edit
    sends none — and must not be read as "remove the credential"."""
    with _client(tmp_path) as client:
        _add(client)
        assert _get(client)["has_credential"] is True
        client.patch("/agents/remote/my-hermes", json={"base_url": "http://new:1"})
        assert _get(client)["has_credential"] is True, (
            "the stored bearer token was dropped by an unrelated edit"
        )


def test_the_secret_itself_survives_in_the_vault(tmp_path):
    """`has_credential` is only a flag; assert the actual VALUE is still there,
    or a broken edit could leave the flag true and the vault empty."""
    with _client(tmp_path) as client:
        _add(client)
        secrets = client.app.state.platform.secrets
        assert secrets.get("remote_agent_my-hermes") == "super-secret-bearer"
        client.patch("/agents/remote/my-hermes", json={"kind": "openai-chat"})
        assert secrets.get("remote_agent_my-hermes") == "super-secret-bearer"


def test_a_new_token_replaces_the_old_one(tmp_path):
    with _client(tmp_path) as client:
        _add(client)
        client.patch("/agents/remote/my-hermes", json={"token": "rotated-bearer"})
        secrets = client.app.state.platform.secrets
        assert secrets.get("remote_agent_my-hermes") == "rotated-bearer"
        assert _get(client)["has_credential"] is True


def test_clearing_the_token_takes_an_EXPLICIT_flag(tmp_path):
    """Removal is possible but never accidental: an empty string in the token
    box means "I did not type one", not "delete my credential"."""
    with _client(tmp_path) as client:
        _add(client)
        secrets = client.app.state.platform.secrets
        client.patch("/agents/remote/my-hermes", json={"token": ""})
        assert _get(client)["has_credential"] is True, "an empty box cleared the secret"
        # The FLAG is not enough. Treating "" as a new token leaves secret_name
        # set — so has_credential stays true — while overwriting the vault entry
        # with an empty string: the credential is destroyed and the UI still
        # reports one. Caught by the mutation sweep; assert the VALUE.
        assert secrets.get("remote_agent_my-hermes") == "super-secret-bearer", (
            "an empty token box overwrote the stored credential"
        )

        client.patch("/agents/remote/my-hermes", json={"clear_token": True})
        assert _get(client)["has_credential"] is False
        assert client.app.state.platform.secrets.get("remote_agent_my-hermes") in (
            None, "",
        )


# --------------------------------------------------------------------------- #
# (3) VALIDATION AND EDGES.
# --------------------------------------------------------------------------- #
def test_an_unknown_agent_is_404_not_a_silent_create(tmp_path):
    """PATCH must never conjure a record — a typo'd name would otherwise leave
    a half-configured remote nobody meant to make."""
    with _client(tmp_path) as client:
        r = client.patch("/agents/remote/nope", json={"base_url": "http://x:1"})
        assert r.status_code == 404

        # An EMPTY patch reaches the "nothing asked for" early return, which
        # never touches the registry — so only the up-front existence check can
        # answer it. Without that check this path renders a None record and
        # 500s. The mutation sweep found the gap: the first assertion alone
        # passed with the check deleted, because the registry's own miss
        # produced the 404.
        r = client.patch("/agents/remote/nope", json={})
        assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text[:200]}"

        # And a credential-clearing patch must not blow up on a missing record.
        r = client.patch("/agents/remote/nope", json={"clear_token": True})
        assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text[:200]}"


def test_a_bad_kind_is_refused_and_changes_nothing(tmp_path):
    with _client(tmp_path) as client:
        _add(client)
        r = client.patch("/agents/remote/my-hermes", json={"kind": "carrier-pigeon"})
        assert r.status_code == 400
        assert _get(client)["kind"] == "http-task"


def test_an_empty_base_url_is_refused(tmp_path):
    """Blanking the URL would produce a remote that can never be reached, and
    the failure would surface later as a confusing connection error."""
    with _client(tmp_path) as client:
        _add(client)
        r = client.patch("/agents/remote/my-hermes", json={"base_url": "   "})
        assert r.status_code == 400
        assert _get(client)["base_url"] == "http://192.168.1.20:8080"


def test_an_empty_patch_is_a_no_op_not_an_error(tmp_path):
    with _client(tmp_path) as client:
        before = _add(client)
        r = client.patch("/agents/remote/my-hermes", json={})
        assert r.status_code == 200
        assert r.json()["base_url"] == before["base_url"]


def test_disabling_and_re_enabling_round_trips(tmp_path):
    with _client(tmp_path) as client:
        _add(client)
        client.patch("/agents/remote/my-hermes", json={"enabled": False})
        assert _get(client)["enabled"] is False
        client.patch("/agents/remote/my-hermes", json={"enabled": True})
        assert _get(client)["enabled"] is True


def test_the_response_never_leaks_the_token(tmp_path):
    """Same contract as every other remote-agent response."""
    with _client(tmp_path) as client:
        _add(client)
        r = client.patch("/agents/remote/my-hermes", json={"token": "rotated-bearer"})
        assert "rotated-bearer" not in r.text
        assert "token" not in r.json()


def test_an_unknown_field_name_is_refused_by_the_registry(tmp_path):
    """`update` takes **fields, so a typo'd key would silently no-op and the
    edit would appear to succeed while changing nothing."""
    import pytest

    from iron_jarvis.agents.remote import RemoteAgentRegistry

    with _client(tmp_path) as client:
        _add(client)
        reg = RemoteAgentRegistry(client.app.state.platform.engine)
        with pytest.raises(ValueError) as excinfo:
            reg.update("my-hermes", base_yrl="http://typo:1")
        # NAMING the field is the point. Pydantic's own ValidationError is also
        # a ValueError, so a bare `raises(ValueError)` passed with this guard
        # deleted (mutation sweep) — but it fires later, after the row is
        # loaded, and says "Object has no attribute" rather than which key was
        # wrong. Assert our message so the guard is actually pinned.
        assert "base_yrl" in str(excinfo.value)
        assert "unknown remote-agent field" in str(excinfo.value)
