"""v1.433.0 (Wave 1, group A) — the independent review's pins.

Each one was red on the change set as first committed (1b1f8ca4):

* ``pixio_status`` SAVES a file (the finished result, into the run's folder
  and the gallery), so it is not READONLY: a READONLY tool is replay-safe
  after a restart (``tools.base.replay_safe``), runs headless from a stored
  workflow without asking (``workflows/engine``), is left out of a rewind's
  "commands it cannot undo" list (``harness/rewind``) and its failure is
  never counted as work the job did not get (``agents/outcome``).
  ``pixio_models`` / ``pixio_params`` only read and stay READONLY.
* turning "Run app tools without asking" off says how many apps STILL RAN
  their tools unasked — only rows whose own flag really opened the shared key
  (``mcp.tools.pack_grants_auto``) count; a row holding ``auto_approve =
  false``, or a switched-off app's flag, ran nothing unasked. Every row's key
  is still taken away, as the Tools page always did.
* a Discord webhook address with an API version in its path
  (``/api/v10/webhooks/…``, the form Discord's own docs use) is masked in a
  log row wherever a model put it, like the unversioned one.
* "Keep activity history" needs no restart on the older Settings form either.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.base import Reversibility, ToolContext, replay_safe
from iron_jarvis.tools.pixio import PixioModelsTool, PixioParamsTool, PixioStatusTool
from tests.test_pixio_tools import FakeResponse, ScriptedHttp

_KEY = "pxio_live_test"
ROOT = Path(__file__).resolve().parent.parent


def _ctx(tmp_path):
    return ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id="r", config=None, event_bus=None, engine=None
    )


@pytest.mark.asyncio
async def test_pixio_status_writes_a_file_so_it_is_not_a_read_and_not_replay_safe(tmp_path):
    http = ScriptedHttp(
        {
            ("GET", "/api/v1/generations/gen_9"): FakeResponse(
                200, {"status": "succeeded", "outputUrl": "https://cdn.pixio.test/out/pic.png"}
            ),
            ("GET", "https://cdn.pixio.test/out/pic.png"): FakeResponse(200, content=b"img"),
        }
    )
    tool = PixioStatusTool(key_resolver=lambda: _KEY, http=http)
    res = await tool.execute({"generation_id": "gen_9"}, _ctx(tmp_path))
    assert res.ok and res.created_paths, "a check that succeeded saved a file"
    assert (tmp_path / "pixio" / "gen_9.png").is_file()
    # So it must not be declared a read.
    assert tool.reversibility != Reversibility.READONLY
    assert replay_safe(tool) is False, "a cut-off check is not re-run as if it changed nothing"
    # The two that only ask Pixio a question stay reads.
    for cls in (PixioModelsTool, PixioParamsTool):
        assert cls.reversibility == Reversibility.READONLY, cls.__name__


def test_the_apps_counted_are_the_ones_that_really_ran_unasked(tmp_path):
    app = create_app(str(tmp_path))
    p = app.state.platform
    p.config.mcp_auto_approve = False
    p.config.mcp_servers = [
        {"name": "brave", "command": "x", "args": [], "auto_approve": True},
        {"name": "files", "command": "x", "args": [], "auto_approve": False},
        {"name": "sleepy", "command": "x", "args": [], "auto_approve": True, "enabled": False},
        {"name": "notes", "command": "x", "args": []},
    ]
    out = p.config_writer.apply({"mcp_auto_approve": False}, actor="test")
    assert out.cleared_auto_approve == ["brave"], out.cleared_auto_approve
    # Every row's own key is still gone (absent == off), so nothing comes back on.
    assert all("auto_approve" not in s for s in p.config.mcp_servers), p.config.mcp_servers


@pytest.mark.parametrize(
    "url",
    [
        "https://discord.com/api/v10/webhooks/123456789012345678/AbCdEfGh-VERSIONED-NEVER-IN-LOG",
        "https://canary.discord.com/api/v9/webhooks/123456789012345678/AbCdEfGh-VERSIONED-NEVER-IN-LOG",
    ],
)
def test_a_versioned_discord_webhook_is_masked_wherever_a_model_put_it(tmp_path, url):
    app = create_app(str(tmp_path))
    tool = app.state.platform.registry.get("channel_connect")
    shown = tool.redact_args({"name": "team", "type": "discord", "channel": url, "chat_id": f"see {url}"})
    assert "VERSIONED-NEVER-IN-LOG" not in json.dumps(shown), shown
    assert shown["name"] == "team" and shown["type"] == "discord"


def test_the_older_settings_form_no_longer_marks_activity_history_restart():
    src = (ROOT / "dashboard/components/settings/pages/LegacySettingsForm.tsx").read_text(
        encoding="utf-8"
    ).replace("\r\n", "\n")
    start = src.index('key: "event_retention_days"')
    block = src[start: src.index("},", start)]
    assert "restart: true" not in block, block


def test_the_channel_credential_help_names_a_discord_webhook():
    from iron_jarvis.settings import schema

    sdef = next(s for s in schema.SECRETS if s.name == "channel.{name}")
    assert "Discord webhook" in sdef.help


@pytest.mark.asyncio
async def test_a_discord_destination_made_in_chat_works_end_to_end_and_its_url_is_never_recorded(
    tmp_path, monkeypatch, caplog
):
    """Made from chat, finished on the secure card, then the Notifications
    page's Test: the message goes to the pasted address, and no ledger row,
    event, log line, card or answer carries it."""
    import logging

    from fastapi.testclient import TestClient
    from sqlmodel import select

    import iron_jarvis.comm as comm
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import EventRecord, ToolInvocation, UndoJournal
    from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done

    tail = "Zz9-END-TO-END-NEVER-RECORDED_0123456789"
    url = f"https://discord.com/api/webhooks/123456789012345678/{tail}"
    posted: list[tuple[str, dict]] = []

    class _Resp:
        status_code = 204
        text = ""

        def json(self):
            return {}

    def fake_post(u, payload, headers=None):
        posted.append((u, payload))
        return _Resp()

    monkeypatch.setattr(comm, "httpx_post", fake_post)
    caplog.set_level(logging.DEBUG)
    app = create_app(str(tmp_path))
    p = app.state.platform
    p.router.stream = _calls_stream([("channel_connect", {"name": "team", "type": "discord"})])

    async def approve(data):
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_stream(app, _body("connect my discord channel"), approve)
    secret = next(c for c in _done(frames)["config_cards"] if c["kind"] == "secret")
    status, raw = await _asgi_post(app, "/config/secret", {"name": secret["name"], "value": url})
    assert status == 200, raw
    client = TestClient(app)
    r = client.post("/comm/channels/team/test")
    assert r.status_code == 200 and r.json()["ok"] is True, r.text
    assert posted and posted[-1][0] == url, "the test message went to the pasted address"
    with session_scope(p.engine) as db:
        seen = [json.dumps([i.args_json, i.output]) for i in db.exec(select(ToolInvocation))]
        seen += [e.payload_json for e in db.exec(select(EventRecord))]
        seen += [str(u.pre_inline) for u in db.exec(select(UndoJournal))]
    assert seen and all(tail not in s for s in seen)
    assert tail not in json.dumps(frames, default=str) + raw.decode("utf-8", "replace") + r.text
    assert tail not in caplog.text
    rows = client.get("/comm/channels").json()["channels"]
    assert tail not in json.dumps(rows)
