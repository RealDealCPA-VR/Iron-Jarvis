"""v1.356.0 (G77) — the Status map's stars say what they lead to.

Everything > Status read "Ready: anthropic, claude-cli, codex-cli, custom.
Answers come from custom." and the Browser star said "Chrome" whatever
browser the user had paired. Now the Models star names providers in the model
menu's words (the daemon twin of the dashboard's ``providerHeading``) and the
Browser star names the PAIRED browser from the transport's own cached status
(no probe, no process: the v1.334.0 cost rules).

Driven through the real app factory and the real ``GET /ui/system-map``; the
provider rows the map reads (``d._visible_providers``) and the add-on's socket
are the only inputs set by hand.
"""

from __future__ import annotations

import subprocess

from fastapi.testclient import TestClient

from iron_jarvis.browser.extension_backend import ExtensionConnection
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import system_map as sm


class _Ws:
    """A socket stand-in: the transport only needs these two."""

    async def send_json(self, _payload):  # pragma: no cover - never called here
        return None

    async def close(self, code=1000):  # pragma: no cover - never called here
        return None


def _nodes(body):
    return {n["key"]: n for a in body["areas"] for n in a["nodes"]}


def _rows(*providers):
    return [{"provider": p, "available": True, "class": "local"} for p in providers]


def _map(app):
    return _nodes(TestClient(app).get("/ui/system-map").json())


def test_the_models_star_says_your_model_server_never_custom(tmp_path):
    app = create_app(str(tmp_path))
    d = app.state.d
    d._visible_providers = lambda: _rows("anthropic", "claude-cli", "codex-cli", "custom")
    d.platform.config.default_provider = "custom"
    detail = _map(app)["models"]["detail"]
    assert detail == (
        "Ready: Anthropic, Claude Code, Codex, Your model server. "
        "Answers come from your model server."
    )
    # No raw provider id the tables know reaches the star.
    for raw in ("custom", "claude-cli", "codex-cli", "anthropic"):
        assert raw not in detail


def test_a_brand_keeps_its_casing_and_auto_reads_as_a_sentence(tmp_path):
    app = create_app(str(tmp_path))
    d = app.state.d
    d._visible_providers = lambda: _rows("xai", "ollama")
    d.platform.config.default_provider = "xai"
    assert _map(app)["models"]["detail"] == "Ready: xAI, Ollama. Answers come from xAI."
    d.platform.config.default_provider = "auto"
    assert _map(app)["models"]["detail"].endswith("Auto picks a model for each message.")


def test_the_demo_line_is_unchanged(tmp_path):
    app = create_app(str(tmp_path))
    d = app.state.d
    d._visible_providers = lambda: _rows("claude-cli")
    d.platform.config.default_provider = "mock"
    assert _map(app)["models"]["detail"] == (
        "Ready: Claude Code. No model chosen for answers yet — replies are a demo."
    )


def test_an_unknown_id_is_shown_as_itself_never_re_cased():
    assert sm.provider_heading("my-odd-server") == "my-odd-server"
    assert sm.answers_from("my-odd-server") == "Answers come from my-odd-server."


def _connect(app, name: str) -> None:
    """Put a paired, open add-on socket on the real transport, as the
    ``browser.hello`` handler leaves it."""
    backend = app.state.d.platform.browser.backend
    conn = ExtensionConnection(_Ws(), paired=True)
    conn.browser_name = name
    backend._conn = conn


def test_the_browser_star_names_the_paired_browser(tmp_path):
    app = create_app(str(tmp_path))
    _connect(app, "Microsoft Edge")
    node = _map(app)["browser"]
    assert node["detail"] == "Your own Microsoft Edge, connected."
    assert node["live"] == 1
    assert "Chrome" not in node["detail"]


def test_a_browser_that_never_said_its_name_is_not_called_chrome(tmp_path):
    app = create_app(str(tmp_path))
    _connect(app, "")
    assert _map(app)["browser"]["detail"] == "Your own browser, connected."
    # And nothing connected or paired: no browser is named at all.
    fresh = create_app(str(tmp_path / "fresh"))
    detail = _map(fresh)["browser"]["detail"]
    assert detail == "Not paired with a browser yet."
    assert "Chrome" not in detail


def test_naming_the_browser_reads_the_cached_status_only(tmp_path, monkeypatch):
    """The v1.334.0 cost rules hold: the name comes from the transport's
    in-memory status view — no process, no probe of the add-on."""
    app = create_app(str(tmp_path))
    _connect(app, "Microsoft Edge")
    started: list[object] = []
    real_popen = subprocess.Popen

    def spy(*a, **kw):
        started.append(a)
        return real_popen(*a, **kw)

    monkeypatch.setattr(subprocess, "Popen", spy)
    backend = app.state.d.platform.browser.backend
    calls: list[str] = []

    def spy_on(method: str):
        # A plain function, so the CALL is recorded even if the result would
        # never be awaited (calling a coroutine function is already the probe).
        def no_probe(*_a, **_kw):  # pragma: no cover - must never run
            calls.append(method)
            raise AssertionError(f"the Browser star called backend.{method}")

        return no_probe

    # The transport's two doors to the add-on are `command` and `directive`
    # (browser/extension_backend.py); the star must not reach for either.
    # raising=True: a rename of either method fails here, not silently.
    for method in ("command", "directive"):
        monkeypatch.setattr(backend, method, spy_on(method), raising=True)
    node = _map(app)["browser"]
    assert node["detail"] == "Your own Microsoft Edge, connected."
    assert started == [] and calls == []


def test_a_transport_that_raises_falls_back_to_plain_words(tmp_path, monkeypatch):
    app = create_app(str(tmp_path))
    _connect(app, "Microsoft Edge")
    backend = app.state.d.platform.browser.backend

    def boom():
        raise RuntimeError("status view broke")

    monkeypatch.setattr(backend, "status", boom)
    assert sm.paired_browser_name(app.state.d) == ""
    assert sm.browser_detail(True, True, "") == "Your own browser, connected."
