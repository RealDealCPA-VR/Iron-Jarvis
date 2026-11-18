"""Fourth review, group 29 (MS5), the route side: the answer to a Connect press
carries that app's OWN after-Connect sentence (``apps.AFTER_CONNECT``), through
the real ``POST /work-record/other-apps/{name}/connect``; Claude Code is never
told about Codex. An unknown app's refusal names all three apps.

The other apps' settings live under a temp folder (``apps.TEST_ROOT``,
``CODEX_HOME``); Claude Code's own command is a stand-in that writes its own
file, as the real one does. No daemon port is opened.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.browser.panetokens import INSTALL_TOKEN_ENV
from iron_jarvis.daemon.app import create_app
from iron_jarvis.mcpserver import apps
from iron_jarvis.workmap import outside
from tests.test_running_elsewhere_v1530 import FakeClaude

INSTALL = "install-bearer-for-the-whole-app"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv(INSTALL_TOKEN_ENV, INSTALL)
    monkeypatch.setattr(apps, "TEST_ROOT", str(tmp_path / "other-apps"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-home"))
    monkeypatch.delenv(apps.BRIDGE_DIR_ENV, raising=False)
    monkeypatch.setattr(outside, "READS_DISK", False)
    monkeypatch.setattr(apps, "_sleep", lambda s: None)
    # Claude Code: installed, and its own command writes its own file.
    monkeypatch.setattr(apps, "claude_cli", lambda: ["claude"])
    apps.claude_code_config_path().parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(apps, "run_cli", FakeClaude(apps.claude_code_config_path()))
    apps._cc_cache.clear()
    # Claude desktop and Codex: installed (their settings folders exist).
    for app in (apps.APP_CLAUDE, apps.APP_CODEX):
        Path(apps.config_path(app)).parent.mkdir(parents=True, exist_ok=True)
    app = create_app(str(tmp_path / "home"))
    yield TestClient(app, headers={"Authorization": f"Bearer {INSTALL}"})
    apps._cc_cache.clear()


@pytest.mark.parametrize("name", list(apps.APPS))
def test_connect_answers_with_that_apps_own_sentence(client, name):
    r = client.post(f"/work-record/other-apps/{name}/connect")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True
    label = apps.LABELS[name]
    assert body["words"] == f"{label} is connected. {apps.AFTER_CONNECT[name]}"
    for other in apps.LABELS.values():
        if other != label and other not in label:
            assert other not in body["words"], f"{label}'s answer names {other}"


def test_claude_code_is_told_about_claude_code_sessions(client):
    r = client.post("/work-record/other-apps/claude-code/connect")
    assert r.status_code == 200, r.text
    assert r.json()["words"] == (
        "Claude Code is connected. New Claude Code sessions use it; one that is open now needs a restart.")


@pytest.mark.parametrize("press", ["connect", "disconnect"])
def test_an_unknown_app_is_refused_naming_all_three(client, press):
    r = client.post(f"/work-record/other-apps/someone-else/{press}")
    assert r.status_code == 404
    detail = r.json()["detail"]
    assert detail == apps.UNKNOWN_APP
    for label in apps.LABELS.values():
        assert label in detail
