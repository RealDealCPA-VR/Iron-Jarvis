"""Owner decision 6 after the third fix plan: the last two answers that put
programming words on screen.

* The app-wide answer to a malformed request (``daemon/app._input_error``)
  answered every ValueError, KeyError, TOML and JSON decode error that
  reached the app as ``"<ClassName>: <text>"`` ("ValueError: Send the tools
  as a set of roles.", "KeyError: \\"unknown session 'x'\\"",
  "JSONDecodeError: Expecting value: line 1 column 1 (char 0)"). Now a
  sentence the app wrote itself is shown as it is, a KeyError is one plain
  sentence, and a decode error says which kind of text could not be read and
  where. Still 400.
* A one-shot's provider failure that was not a rate limit
  (``daemon/app._provider_error_http``) answered 502 with the provider's own
  text. Now ``detail`` names the model that could not answer in plain words
  (the root rule: a model that is down is refused BY NAME) and the raw text
  rides beside it as ``technical``. Still 502.

Real app factory, real exception handlers, a TestClient; no network, no
provider, no sleeps.
"""

from __future__ import annotations

import json
import re
import tomllib

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app

#: Engine words an answer's main words must never carry.
_ENGINE = re.compile(
    r"\b[A-Za-z]*(?:Error|Exception)\b|Expecting value|char \d+|Invalid value|\(at line"
)


@pytest.fixture()
def app(tmp_path):
    app = create_app(str(tmp_path))

    # Routes added to the REAL app, so its own exception handlers answer.
    def _written_value_error():  # noqa: ANN202
        # A sentence the app's own code wrote for the user (raised inside
        # iron_jarvis, as a route's helper does).
        from iron_jarvis.ltm import mcp_roles

        mcp_roles.clean("not a set", [])

    def _library_value_error():  # noqa: ANN202
        from datetime import datetime

        datetime.fromisoformat("next tuesday")

    def _key_error():  # noqa: ANN202
        raise KeyError("unknown session 'abc123'")

    def _bad_json():  # noqa: ANN202
        json.loads('{"name": "weekly",\n  "steps": [1, 2,,]}')

    def _bad_toml():  # noqa: ANN202
        tomllib.loads('name = "weekly"\nsteps = [1, 2,,]\n')

    app.add_api_route("/__t/value", _written_value_error, methods=["POST"])
    app.add_api_route("/__t/library-value", _library_value_error, methods=["POST"])
    app.add_api_route("/__t/key", _key_error, methods=["POST"])
    app.add_api_route("/__t/json", _bad_json, methods=["POST"])
    app.add_api_route("/__t/toml", _bad_toml, methods=["POST"])
    return app


def _detail(client: TestClient, path: str) -> str:
    r = client.post(path)
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, str) and detail.strip(), r.text
    assert not _ENGINE.search(detail), detail
    return detail


def test_a_sentence_the_app_wrote_is_shown_without_the_class_name(app):
    with TestClient(app) as c:
        assert _detail(c, "/__t/value") == "Send the tools as a set of roles."


def test_a_library_value_error_is_one_plain_sentence(app):
    with TestClient(app) as c:
        detail = _detail(c, "/__t/library-value")
    assert "isoformat" not in detail and "next tuesday" not in detail, detail


def test_a_key_error_is_one_plain_sentence(app):
    with TestClient(app) as c:
        detail = _detail(c, "/__t/key")
    assert "abc123" not in detail and "unknown session" not in detail, detail


def test_a_json_decode_error_says_what_could_not_be_read_and_where(app):
    with TestClient(app) as c:
        detail = _detail(c, "/__t/json")
    assert "JSON" in detail and "line 2" in detail, detail


def test_a_toml_decode_error_says_what_could_not_be_read_and_where(app):
    with TestClient(app) as c:
        detail = _detail(c, "/__t/toml")
    assert "TOML" in detail and "line 2" in detail, detail


# --------------------------------------------------------------------------- #
# A one-shot's provider failure
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    [
        # The adapter's own shape: names its provider in the text.
        "anthropic API error 404: model 'claude-x' not found (request_id req_9f8e)",
        # A bare failure that names nobody: the one-shot knows who it asked.
        "model not found",
    ],
)
def test_a_provider_failure_names_the_model_and_keeps_the_raw_text_apart(tmp_path, monkeypatch, raw):
    app = create_app(str(tmp_path))
    platform = app.state.platform

    class _Down:
        provider, model = "anthropic", "claude-x"

        async def complete(self, *, system, messages, tools):  # noqa: ANN001, ANN202
            raise RuntimeError(raw)

    monkeypatch.setattr(platform.providers, "get", lambda p, m=None: _Down())
    monkeypatch.setattr(platform.providers, "available", lambda p: p == "anthropic")
    with TestClient(app) as c:
        r = c.post(
            "/workflows/generate",
            json={"description": "tidy the notes folder", "provider": "anthropic", "model": "claude-x"},
        )
    assert r.status_code == 502, r.text
    body = r.json()
    detail = body["detail"]
    assert "Anthropic" in detail, detail
    assert raw not in detail and "not found" not in detail and "RuntimeError" not in detail, detail
    assert not _ENGINE.search(detail), detail
    # The raw text is kept beside the plain words, never lost.
    assert raw.split(" (request_id")[0] in body.get("technical", ""), body
