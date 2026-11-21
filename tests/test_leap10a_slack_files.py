"""10-a (build-out plan R2.3): a file sent to Jarvis in Slack reaches it.

R0.3 let a ``file_share`` message through the pump only to say the file could
not be used. Now its files come from ``event.files`` and are downloaded from
Slack's own file host with the bot token in the header (never to any other
address), kept in the chat's folder and handed to the chat turn. A Slack app
made from the older manifest has no ``files:read``: Slack then answers the
download with its sign-in page, and the sender hears in words what to update.
The Events API webhook (``/comm/slack/events/{name}``) takes a file in a DM
too; R0.3 left it dropping one without a word.

Only Slack itself (the download, chat.postMessage) and the chat turn are
fakes.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
import time
from typing import Any

import pytest

from iron_jarvis.comm import slack_socket as S
from iron_jarvis.comm.channels import SlackChannel
from iron_jarvis.comm.inbound import InboundPoller
from tests._fakes.phone_files import chat_root, fake_turn, files_under, poller

pytestmark = pytest.mark.usefixtures("no_network")

OWN = "C0OWN0001"
URL = "https://files.slack.com/files-pri/T1-F1/download/scan.pdf"


@pytest.fixture(autouse=True)
def _fresh_listening():
    S.LISTENING.clear()
    yield
    S.LISTENING.clear()


class _Slack:
    """chat.postMessage and the file host, recorded."""

    def __init__(self, files: dict[str, bytes] | None = None, html: bool = False) -> None:
        self.files = dict(files or {URL: b"%PDF-1.4 slack"})
        self.html = html
        self.sent: list[dict[str, Any]] = []
        self.downloads: list[tuple[str, dict]] = []

    def post(self, url, payload, headers=None):  # noqa: ANN001
        self.sent.append(dict(payload))
        return {"ok": True}

    def get_bytes(self, url, headers, max_bytes):  # noqa: ANN001
        self.downloads.append((url, dict(headers or {})))
        if self.html:
            return b"<!DOCTYPE html><html><head><title>Slack</title></head></html>"
        return self.files[url]

    @property
    def texts(self) -> list[str]:
        return [str(p.get("text") or "") for p in self.sent]


def _channel(fake: _Slack, **over: Any) -> SlackChannel:
    cfg = {"token_secret": "tok", "channel": OWN, "inbound_enabled": True,
           "chat_enabled": True, "allowed_senders": ["U1"], **over}
    return SlackChannel(cfg, http_post=fake.post, http_get_bytes=fake.get_bytes,
                        secret_resolver=lambda n: "xoxb-test")


def _sock(poll: InboundPoller, ch: SlackChannel) -> S.SlackSocketMode:
    cfg = {"channels": {"hq": {"type": "slack", **ch.config}}}
    sock = S.SlackSocketMode(poll, poll.notifier, lambda n: "xoxb-test", lambda: cfg,
                             slack_api=lambda m, t, p: {"ok": True, "user_id": "UBOT"})
    sock._own["hq"] = OWN
    sock._bot_user["hq"] = "UBOT"
    return sock


def _env(**event: Any) -> dict:
    return {"type": "events_api", "envelope_id": "e1", "payload": {"event": event}}


def _file(url: str = URL, name: str = "scan.pdf", mime: str = "application/pdf", **extra: Any) -> dict:
    return {"id": "F1", "name": name, "mimetype": mime, "size": 14, "url_private_download": url, **extra}


def _go(sock: S.SlackSocketMode, poll: InboundPoller, env: dict) -> Any:
    async def go():
        res = await sock.process_envelope("hq", env)
        await poll.drain()
        return res

    return asyncio.run(go())


def _rig(platform, tmp_path, fake: _Slack | None = None):
    root = chat_root(platform, tmp_path)
    fake = fake or _Slack()
    ch = _channel(fake)
    turn = fake_turn("Filed.")
    poll = poller(platform, ch, name="hq", turn=turn)
    return root, fake, _sock(poll, ch), poll, turn


def test_a_file_share_dm_is_read(platform, tmp_path):
    root, fake, sock, poll, turn = _rig(platform, tmp_path)
    env = _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
               user="U1", text="file this", ts="1700.1", files=[_file()])

    res = _go(sock, poll, env)

    assert fake.downloads == [(URL, {"Authorization": "Bearer xoxb-test"})]
    kept = files_under(root)
    assert [p.name for p in kept] == ["scan.pdf"] and kept[0].read_bytes() == b"%PDF-1.4 slack"
    assert fake.texts[0].startswith("Saved scan.pdf in ") or fake.texts[0].startswith("Iron Jarvis: Saved scan.pdf in ")
    assert turn.calls[0].attachments == [str(kept[0])]
    assert res["status"] == "chat"


def test_a_file_in_the_own_channel_is_read_and_answered_in_its_thread(platform, tmp_path):
    root, fake, sock, poll, turn = _rig(platform, tmp_path)
    env = _env(type="message", subtype="file_share", channel=OWN, channel_type="channel",
               user="U1", text="", ts="1700.2", files=[_file()])

    _go(sock, poll, env)

    assert len(files_under(root)) == 1
    assert {p["channel"] for p in fake.sent} == {OWN}
    assert all(p.get("thread_ts") == "1700.2" for p in fake.sent)
    assert len(turn.calls) == 1


def test_a_file_url_off_slacks_file_host_is_never_fetched_with_the_token(platform, tmp_path):
    evil = "https://attacker.example/steal"
    root, fake, sock, poll, turn = _rig(platform, tmp_path, _Slack(files={evil: b"x"}))
    env = _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
               user="U1", text="look", ts="1700.3", files=[_file(url=evil)])

    _go(sock, poll, env)

    assert fake.downloads == []
    assert files_under(root) == []
    assert "scan.pdf" in fake.texts[0]


def test_missing_files_permission_answers_in_words(platform, tmp_path):
    root, fake, sock, poll, turn = _rig(platform, tmp_path, _Slack(html=True))
    env = _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
               user="U1", text="", ts="1700.4", files=[_file()])

    res = _go(sock, poll, env)

    assert files_under(root) == []
    words = fake.texts[0]
    assert "scan.pdf" in words and "files:read" in words and "manifest" in words
    assert res["status"] == "files_not_taken"
    assert turn.calls == []


@pytest.mark.parametrize("subtype", ["message_changed", "channel_join", "bot_message", "thread_broadcast"])
def test_other_subtypes_are_still_dropped(platform, tmp_path, subtype):
    root, fake, sock, poll, turn = _rig(platform, tmp_path)
    env = _env(type="message", subtype=subtype, channel="D0DM00001", channel_type="im",
               user="U1", text="x", ts="1700.5", files=[_file()])

    assert _go(sock, poll, env) is None
    assert fake.downloads == [] and fake.sent == [] and files_under(root) == []


def test_a_strangers_file_is_never_fetched(platform, tmp_path):
    root, fake, sock, poll, turn = _rig(platform, tmp_path)
    env = _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
               user="U9", text="", ts="1700.6", files=[_file()])

    _go(sock, poll, env)

    assert fake.downloads == [] and fake.sent == [] and files_under(root) == []


def test_the_manifest_asks_for_files_and_buttons(tmp_path):
    from iron_jarvis.daemon.app import create_app

    d = create_app(str(tmp_path / "home")).state.d
    manifest = json.loads(d._CHANNEL_MANIFESTS["slack"])
    scopes = manifest["oauth_config"]["scopes"]["bot"]
    assert "files:read" in scopes and "files:write" in scopes
    assert manifest["settings"]["interactivity"]["is_enabled"] is True
    assert manifest["settings"]["socket_mode_enabled"] is True


def test_the_row_says_when_the_app_has_no_files_permission():
    from iron_jarvis.daemon.routes.comm import listening_status

    S.LISTENING["hq"] = {"connected_at": "t", "last_event_at": "t", "own_channel": OWN,
                         "channel_note": "", "scopes": "chat:write,im:history"}
    st = listening_status("hq", "slack", {"inbound_enabled": True, "channel": "#iron-jarvis"})
    assert st["tone"] == "warn" and "files:read" in st["words"]
    assert "manifest" in st["words"]
    S.LISTENING["hq"]["scopes"] = "chat:write,files:read,files:write"
    st = listening_status("hq", "slack", {"inbound_enabled": True, "channel": "#iron-jarvis"})
    assert st["tone"] == "quiet"


# --------------------------------------------------------------------------- #
# the Events API webhook (needs a tunnel; R0.3 left it dropping files)
# --------------------------------------------------------------------------- #
SIGNING = "8f742231b10e8888abcd99yyyzzz85a5"


def _signed(body: bytes) -> dict:
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(SIGNING.encode(), f"v0:{ts}:".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig}


def test_the_events_api_takes_a_file_in_a_dm(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path / "home")))
    r = client.post("/comm/channels", json={"type": "slack", "name": "team", "config": {
        "token": "xoxb-test", "channel": "#general", "signing_secret": SIGNING,
        "inbound_enabled": "true", "allowed_senders": "U1"}})
    assert r.status_code == 200, r.text
    ran = threading.Event()
    seen: dict[str, Any] = {}

    async def _fake_handle(self, name, ch, msg):  # noqa: ANN001
        seen["msg"] = msg
        ran.set()
        return {"channel": name, "status": "handled"}

    monkeypatch.setattr(InboundPoller, "_handle", _fake_handle)
    body = json.dumps({"type": "event_callback", "event_id": "Ev1", "event": {
        "type": "message", "subtype": "file_share", "channel_type": "im", "channel": "D9",
        "user": "U1", "text": "", "files": [_file()]}}).encode()
    r = client.post("/comm/slack/events/team", content=body, headers=_signed(body))

    assert r.status_code == 200
    assert ran.wait(timeout=5), "a file in a DM through the Events API was dropped"
    msg = seen["msg"]
    assert [(f.name, f.ref) for f in msg.files] == [("scan.pdf", URL)]
    assert msg.reply_to == "U1"
