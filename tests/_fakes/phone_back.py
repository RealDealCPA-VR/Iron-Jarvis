"""Shared fakes for the 10-b tests (finished files back to the phone, and the
buttons under a question): a Telegram that also takes uploads, button
answers and keyboard edits, a Slack Web API that records the three upload
steps, and a chat turn that makes a file. Nothing here touches a network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from iron_jarvis.comm.channels import SlackChannel
from tests._fakes.phone_files import BOT_TOKEN, FakeTelegram

#: A made-up Slack bot token (the tests look for it nowhere it must not be).
SLACK_TOKEN = "slack-bot-TEST-not-a-real-token"
SLACK_USER = "U0TESTUSER"
SLACK_DM = "D0TESTDM01"
SLACK_OWN = "C0TESTCHAN"


class FakeTelegramBack(FakeTelegram):
    """FakeTelegram plus the calls 10-b makes: sendMessage with a keyboard,
    sendDocument / sendPhoto (multipart), answerCallbackQuery and
    editMessageReplyMarkup. ``upload_answer`` is what an upload answers."""

    def __init__(self, updates: list[dict[str, Any]], files: dict[str, bytes] | None = None) -> None:
        super().__init__(updates, files)
        self.uploads: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.upload_answer: Any = {"ok": True, "result": {"message_id": 99}}
        self.upload_error: Exception | None = None
        #: File names whose upload fails (the others go).
        self.fail_names: set[str] = set()

    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        method = url.rsplit("/", 1)[-1]
        self.calls.append((method, dict(payload)))
        if method == "sendMessage":
            self.sent.append(dict(payload))
            return {"status_code": 200}
        if method in ("answerCallbackQuery", "editMessageReplyMarkup"):
            return {"ok": True}
        raise AssertionError(f"unexpected POST {url}")

    def post_file(self, url: str, fields: dict[str, Any], file: Any, headers: Any) -> Any:
        method = url.rsplit("/", 1)[-1]
        if self.upload_error is not None:
            raise self.upload_error
        field, name, data, mime = file
        if name in self.fail_names:
            raise RuntimeError("the line dropped")
        self.uploads.append({"method": method, "fields": dict(fields), "field": field,
                             "name": name, "data": bytes(data), "mime": mime, "url": url})
        return self.upload_answer

    @property
    def keyboards(self) -> list[list[dict[str, Any]]]:
        """The buttons of every message sent with some, in order."""
        out = []
        for p in self.sent:
            markup = p.get("reply_markup")
            if markup:
                out.append(markup["inline_keyboard"][0])
        return out

    def answered(self) -> list[str]:
        return [p.get("text", "") for m, p in self.calls if m == "answerCallbackQuery"]

    def edits(self) -> list[str]:
        return [p["reply_markup"]["inline_keyboard"][0][0]["text"]
                for m, p in self.calls if m == "editMessageReplyMarkup"]


def press(update_id: int, data: str, *, sender: int, chat: int | None = None,
          message_id: int = 55, text: str = "the question") -> dict[str, Any]:
    """A Telegram ``callback_query`` update."""
    return {"update_id": update_id, "callback_query": {
        "id": f"cq{update_id}", "from": {"id": sender, "is_bot": False, "first_name": "Val"},
        "message": {"message_id": message_id, "chat": {"id": chat if chat is not None else sender},
                    "text": text},
        "data": data,
    }}


class FakeSlackApi:
    """Slack's Web API from memory: ``post`` takes chat.postMessage and
    chat.update (JSON), ``post_file`` takes the form calls and the upload."""

    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.forms: list[tuple[str, dict[str, Any], dict[str, str] | None]] = []
        self.uploads: list[dict[str, Any]] = []
        self.upload_url = "https://files.slack.com/upload/v1/TESTUPLOAD"

    def post(self, url: str, payload: dict[str, Any], headers: dict[str, str] | None = None) -> dict:
        self.posts.append((url.rsplit("/", 1)[-1], dict(payload)))
        return {"ok": True, "ts": "111.222"}

    def post_file(self, url: str, fields: dict[str, Any], file: Any, headers: Any) -> dict:
        if file is not None:
            field, name, data, mime = file
            self.uploads.append({"url": url, "name": name, "data": bytes(data), "headers": headers})
            return {"status_code": 200}
        method = url.rsplit("/", 1)[-1]
        self.forms.append((method, dict(fields), headers))
        if method == "conversations.open":
            return {"ok": True, "channel": {"id": SLACK_DM}}
        if method == "files.getUploadURLExternal":
            return {"ok": True, "upload_url": self.upload_url, "file_id": "F0TESTFILE"}
        if method == "files.completeUploadExternal":
            return {"ok": True, "files": [{"id": "F0TESTFILE"}]}
        raise AssertionError(f"unexpected form call {url}")

    def methods(self) -> list[str]:
        return [m for m, _f, _h in self.forms]


def slack(api: FakeSlackApi, **over: Any) -> SlackChannel:
    return SlackChannel(
        {"token_secret": "slack_bot", "app_token_secret": "slack_app", "channel": SLACK_OWN,
         "inbound_enabled": True, "chat_enabled": True, "allowed_senders": [SLACK_USER], **over},
        http_post=api.post, http_post_file=api.post_file,
        secret_resolver=lambda n: SLACK_TOKEN if n == "slack_bot" else None,
    )


def making_turn(path: Path, data: bytes = b"PK-sheet", reply: str = "Here is your spreadsheet."):
    """A chat turn that WRITES ``path`` and reports it in ``documents``
    (absolute), the way both chat lanes report a file a tool made."""

    async def turn(platform, personas, body, **kw) -> dict[str, Any]:
        turn.calls.append(body)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {"reply": reply, "provider": "mock", "model": "m", "tools_used": [],
                "escalate": False, "escalate_reason": "", "documents": [str(path)]}

    turn.calls = []
    return turn


def making_turn_of(paths: list[Path], data: bytes = b"PK-sheet", reply: str = "Here are your spreadsheets."):
    """A chat turn that writes every one of ``paths`` and reports them all."""

    async def turn(platform, personas, body, **kw) -> dict[str, Any]:
        for p in paths:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return {"reply": reply, "provider": "mock", "model": "m", "tools_used": [],
                "escalate": False, "escalate_reason": "", "documents": [str(p) for p in paths]}

    return turn


def set_local_only(platform, thread_id: str) -> None:
    """Switch one saved chat to Local only (its ``setup_json``)."""
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord

    with session_scope(platform.engine) as db:
        row = db.get(ChatThreadRecord, thread_id)
        setup = json.loads(row.setup_json or "{}")
        setup["local_only"] = True
        row.setup_json = json.dumps(setup)
        db.add(row)
        db.commit()


def telegram_back(fake: FakeTelegramBack, **over: Any):
    from iron_jarvis.comm.channels import TelegramChannel
    from tests._fakes.phone_files import TG_CFG

    return TelegramChannel(
        {**TG_CFG, **over},
        http_post=fake.post, http_get=fake.get, http_get_bytes=fake.get_bytes,
        http_post_file=fake.post_file,
        secret_resolver=lambda n: BOT_TOKEN if n == "tg_token" else None,
    )
