"""Shared fakes for the 10-a tests (photos, files and voice notes from the
phone): a Telegram that answers ``getUpdates``, ``getFile``, the file
download and ``sendMessage`` from memory, a chat turn that records its body,
and a poller wired the way the daemon wires it. Nothing here touches a
network; every chat folder lands under the test's own ``tmp_path``.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm import Notifier
from iron_jarvis.comm.base import FileTooLarge
from iron_jarvis.comm.channels import TelegramChannel
from iron_jarvis.comm.inbound import InboundPoller
from iron_jarvis.comm.prompts import PendingPromptStore
from iron_jarvis.comm.threads import CommThreadStore

#: A made-up bot token: the tests look for it in logs, events and replies.
BOT_TOKEN = "123456:TEST-not-a-real-bot-token"
ME = 777

#: The time a test's files are kept at (``fixed_time``), and the names a
#: Telegram photo and voice note are kept under then.
KEPT_AT = datetime(2026, 11, 20, 10, 15, 30)
PHOTO = "photo-2026-11-20-101530.jpg"
PHOTO_2 = "photo-2026-11-20-101530-2.jpg"
VOICE = "voice-note-2026-11-20-101530.ogg"


def fixed_time(monkeypatch: Any, when: datetime = KEPT_AT) -> None:
    """Keep every file of the test at ``when`` (a plain name gets it)."""
    from iron_jarvis.comm import media

    monkeypatch.setattr(media, "_now", lambda: when)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeTelegram:
    """``getUpdates`` honours the offset; ``getFile`` and the download serve
    ``files`` (file_id -> bytes); every call is recorded."""

    def __init__(self, updates: list[dict[str, Any]], files: dict[str, bytes] | None = None) -> None:
        self.updates = list(updates)
        self.files = dict(files or {})
        self.sent: list[dict[str, Any]] = []
        self.get_files: list[str] = []
        self.downloads: list[str] = []
        self.download_error: Exception | None = None

    def get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        if url.endswith("/getUpdates"):
            offset = int(params.get("offset", 0) or 0)
            if offset:
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
            return {"ok": True, "result": list(self.updates)}
        if url.endswith("/getFile"):
            fid = str(params.get("file_id"))
            self.get_files.append(fid)
            if fid not in self.files:
                return {"ok": False, "description": "file not found"}
            return {"ok": True, "result": {"file_id": fid, "file_path": f"documents/{fid}.bin",
                                           "file_size": len(self.files[fid])}}
        raise AssertionError(f"unexpected GET {url}")

    def get_bytes(self, url: str, headers: dict[str, str] | None, max_bytes: int) -> bytes:
        self.downloads.append(url)
        if self.download_error is not None:
            raise self.download_error
        fid = url.rsplit("/", 1)[-1].removesuffix(".bin")
        data = self.files[fid]
        if len(data) > max_bytes:
            raise FileTooLarge("too large")
        return data

    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        assert url.endswith("/sendMessage"), url
        self.sent.append(dict(payload))
        return {"status_code": 200}

    @property
    def texts(self) -> list[str]:
        return [str(p.get("text") or "") for p in self.sent]


TG_CFG = {
    "token_secret": "tg_token",
    "inbound_enabled": True,
    "chat_enabled": True,
    "allowed_senders": [str(ME)],
}


def telegram(fake: FakeTelegram, **over: Any) -> TelegramChannel:
    return TelegramChannel(
        {**TG_CFG, **over},
        http_post=fake.post,
        http_get=fake.get,
        http_get_bytes=fake.get_bytes,
        secret_resolver=lambda n: BOT_TOKEN if n == "tg_token" else None,
    )


def message(update_id: int, sender: int = ME, *, chat: int | None = None, **fields: Any) -> dict:
    msg = {"from": {"id": sender, "is_bot": False, "first_name": "Val"},
           "chat": {"id": chat if chat is not None else sender}, **fields}
    return {"update_id": update_id, "message": msg}


def photo(update_id: int, file_id: str = "P1", *, caption: str = "", sender: int = ME,
          size: int | None = 12, **extra: Any) -> dict:
    sizes = [{"file_id": file_id + "-small", "width": 90, "height": 60, "file_size": 4},
             {"file_id": file_id, "width": 1280, "height": 960,
              **({"file_size": size} if size is not None else {})}]
    return message(update_id, sender, photo=sizes, **({"caption": caption} if caption else {}), **extra)


def document(update_id: int, file_id: str = "D1", *, name: str = "scan.pdf",
             mime: str = "application/pdf", size: int | None = 20, caption: str = "",
             sender: int = ME) -> dict:
    doc = {"file_id": file_id, "file_name": name, "mime_type": mime,
           **({"file_size": size} if size is not None else {})}
    return message(update_id, sender, document=doc, **({"caption": caption} if caption else {}))


def voice(update_id: int, file_id: str = "V1", *, sender: int = ME) -> dict:
    return message(update_id, sender, voice={"file_id": file_id, "mime_type": "audio/ogg",
                                             "duration": 3, "file_size": 30})


def fake_turn(reply: str = "Added to the March list."):
    async def turn(platform, personas, body, **kw) -> dict[str, Any]:
        turn.calls.append(body)
        return {"reply": reply, "provider": "mock", "model": "m", "tools_used": [],
                "escalate": False, "escalate_reason": "", "documents": []}

    turn.calls = []
    return turn


def chat_root(platform, tmp_path: Path) -> Path:
    """Point the chat files folder at the test's own folder (never the
    owner's Documents)."""
    root = tmp_path / "Documents" / "Iron Jarvis"
    platform.config.chat_files_root = str(root)
    return root


def poller(platform, ch: Any, *, name: str = "tg", turn: Any = None, clock: Clock | None = None,
           orchestrator: Any = None, thread_store: bool = True) -> InboundPoller:
    notifier = Notifier()
    notifier.add_channel(name, ch)
    return InboundPoller(
        notifier, orchestrator if orchestrator is not None else Orchestrator(platform),
        platform.engine, event_bus=platform.event_bus,
        thread_store=CommThreadStore(platform.engine) if thread_store else None,
        chat_turn=turn if turn is not None else fake_turn(), personas={}, platform=platform,
        clock=clock or Clock(), prompt_store=PendingPromptStore(platform.engine),
    )


def run(poll: InboundPoller) -> list[dict[str, Any]]:
    async def go():
        res = await poll.poll_once()
        await poll.drain()
        return res

    return asyncio.run(go())


def files_under(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []
