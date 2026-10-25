"""Readiness 12B (0e.4): a workflow parked on a question tells the phone ONCE.

THE DUPLICATE (measured on v1.386.2 through the real app with a two-way
Telegram destination): a scheduled workflow whose step asks the user sent the
SAME phone chat three lines —

1. ``Iron Jarvis: Workflow 'gated' is waiting: Send it? — reply with /answer
   <your answer>.`` (the answerable question, ``comm/prompts``),
2. ``Workflow “gated” needs you: Send it? — answer it in chat or on the
   Workflows page.`` (the engine's park line), and
3. ``Schedule “fire-gated”: needs you — …`` (the schedule's own line).

An unscheduled park sent 1 and 2. Now a destination the ANSWERABLE question
reaches — at the very address its own lines go to — gets that one line only;
every other destination keeps the lines it had (a one-way destination still
hears the park and the schedule). The answer still works: the phone replies
``/answer yes`` through the REAL poller and the run resumes.

Every pin drives the real app factory, the real Telegram channel (only its
HTTP transport is a recorder), the real workflow engine, the real scheduler
fire (Run now) and the real inbound poller.
"""

from __future__ import annotations

import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.comm import MockChannel, TelegramChannel
from iron_jarvis.comm.channels import EmailChannel, SlackChannel
from iron_jarvis.comm.notifier import Notifier
from iron_jarvis.comm.prompts import question_destinations
from iron_jarvis.comm.threads import CommThreadStore
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.daemon.app import create_app

SENDER = 4242
QUESTION_LINE = "Iron Jarvis: Workflow 'gated' is waiting: Send it? — reply with /answer <your answer>."
PARK_LINE = "Workflow “gated” needs you: Send it? — answer it in chat or on the Workflows page."
SCHEDULE_HEAD = "Schedule “fire-gated”: needs you"


class FakeTelegram:
    """Telegram's HTTP API, recorded: ``sent`` holds every sendMessage body."""

    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.sent: list[dict[str, Any]] = []

    def get(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        offset = int(params.get("offset", 0) or 0)
        return {"ok": True, "result": [u for u in self.updates if u["update_id"] >= offset]}

    def post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent.append(dict(payload))
        return {"status_code": 200}

    def texts(self, chat: Any = SENDER) -> list[str]:
        return [str(s.get("text")) for s in self.sent if str(s.get("chat_id")) == str(chat)]


def _tg(fake: FakeTelegram, *, chat_id: Any = SENDER, chat: bool = True) -> TelegramChannel:
    cfg: dict[str, Any] = {
        "token_secret": "tg",
        "inbound_enabled": True,
        "allowed_senders": [SENDER],
        "chat_id": chat_id,
    }
    if chat:
        cfg["chat_enabled"] = True
    return TelegramChannel(
        cfg, http_post=fake.post, http_get=fake.get,
        secret_resolver=lambda n: "BOT" if n == "tg" else None,
    )


def _update(uid: int, text: str) -> dict[str, Any]:
    return {
        "update_id": uid,
        "message": {
            "text": text,
            "from": {"id": SENDER, "is_bot": False, "first_name": "V"},
            "chat": {"id": SENDER},
        },
    }


def _wait(pred, timeout: float = 20.0, what: str = "") -> None:
    deadline = time.monotonic() + timeout
    while not pred():
        if time.monotonic() > deadline:
            raise AssertionError(f"gave up waiting for {what or pred}")
        time.sleep(0.05)


def _mock(app) -> MockChannel:
    for ch in app.state.platform.notifier._channels.values():
        if isinstance(ch, MockChannel):
            return ch
    raise AssertionError("no mock channel")


def _phone(client: TestClient, fake: FakeTelegram, **kw: Any) -> None:
    """A two-way phone destination with an ESTABLISHED conversation (the
    identity row the answerable question is sent to)."""
    client.app.state.platform.notifier.add_channel("tg", _tg(fake, **kw))
    client.app.state.comm_thread_store.resolve("tg", str(SENDER), "V")


def _gated_workflow(client: TestClient) -> None:
    r = client.post(
        "/workflows",
        json={"name": "gated", "steps": [{"name": "Approve", "kind": "ask", "message": "Send it?"}]},
    )
    assert r.status_code < 300, r.text


def _schedule_row(client: TestClient, name: str) -> dict:
    return next(r for r in client.get("/schedules").json()["schedules"] if r["name"] == name)


# --------------------------------------------------------------------------- #
# The acceptance: one line on the phone, and it is the answerable one.
# --------------------------------------------------------------------------- #
@pytest.mark.timeout(180)
def test_a_parked_scheduled_workflow_tells_the_phone_once_and_the_answer_resumes_it(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        fake = FakeTelegram()
        _phone(client, fake)
        mock = _mock(client.app)
        before_mock = len(mock.sent)
        _gated_workflow(client)
        r = client.post(
            "/schedules",
            json={"name": "fire-gated", "cron": "0 8 * * *", "kind": "workflow", "payload": {"workflow": "gated"}},
        )
        assert r.status_code == 200, r.text
        body = client.post("/schedules/fire-gated/run").json()
        assert body["last_status"] == "needs_you", body

        # The park line and the schedule's line are sent before Run now
        # answers; the answerable question rides the event bus — wait for it.
        _wait(lambda: QUESTION_LINE in fake.texts(), what="the answerable question")
        time.sleep(0.3)  # nothing else is on its way (anti-vacuity below)
        assert fake.texts() == [QUESTION_LINE], fake.texts()

        # A one-way destination keeps both lines it always had: the park and
        # the schedule's needs-you (nothing else reaches it there).
        heard = mock.sent[before_mock:]
        assert PARK_LINE in heard, heard
        assert any(m.startswith(SCHEDULE_HEAD) for m in heard), heard

        # The ONE line is answerable: the phone replies through the real
        # poller and the run resumes; the row settles.
        poller = client.app.state.inbound_poller
        fake.updates.append(_update(1, "/answer yes"))
        client.portal.call(poller.poll_once)
        _wait(lambda: _schedule_row(client, "fire-gated")["last_status"] == "ok", what="the row to settle")
        runs = client.get("/workflows/runs").json()["runs"]
        assert any(r["status"] == "completed" for r in runs), runs


@pytest.mark.timeout(120)
def test_an_unscheduled_park_tells_the_phone_once_too(tmp_path):
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        fake = FakeTelegram()
        _phone(client, fake)
        mock = _mock(client.app)
        before_mock = len(mock.sent)
        _gated_workflow(client)
        r = client.post("/workflows/run", json={"name": "gated"})
        assert r.status_code < 300, r.text
        _wait(lambda: QUESTION_LINE in fake.texts(), what="the answerable question")
        _wait(lambda: PARK_LINE in mock.sent[before_mock:], what="the park line elsewhere")
        time.sleep(0.3)
        assert fake.texts() == [QUESTION_LINE], fake.texts()


@pytest.mark.timeout(120)
def test_a_phone_whose_own_lines_go_to_another_chat_keeps_them(tmp_path):
    """The question goes to the identity's own chat; this destination's
    default chat is a DIFFERENT one (a group) — it keeps the park and the
    schedule's line there. Nothing is dropped that the question did not
    replace."""
    group = -100777
    with TestClient(create_app(str(tmp_path / "home"))) as client:
        fake = FakeTelegram()
        _phone(client, fake, chat_id=group)
        _gated_workflow(client)
        client.post(
            "/schedules",
            json={"name": "fire-gated", "cron": "0 8 * * *", "kind": "workflow", "payload": {"workflow": "gated"}},
        )
        assert client.post("/schedules/fire-gated/run").json()["last_status"] == "needs_you"
        _wait(lambda: QUESTION_LINE in fake.texts(SENDER), what="the answerable question")
        group_lines = fake.texts(group)
        assert PARK_LINE in group_lines, group_lines
        assert any(t.startswith(SCHEDULE_HEAD) for t in group_lines), group_lines


# --------------------------------------------------------------------------- #
# The rule, read off the same gates the question uses.
# --------------------------------------------------------------------------- #
def _store(tmp_path) -> CommThreadStore:
    engine = make_engine(tmp_path / "db.sqlite")
    init_db(engine)
    return CommThreadStore(engine)


def test_question_destinations_is_the_questions_own_reach(tmp_path):
    store = _store(tmp_path)
    engine = store.engine
    n = Notifier()
    fake = FakeTelegram()
    n.add_channel("phone", _tg(fake))  # two-way, its own chat is the identity's
    n.add_channel("oneway", _tg(FakeTelegram(), chat=False))  # never asked
    n.add_channel("group", _tg(FakeTelegram(), chat_id=-1))  # asked, elsewhere
    n.add_channel(
        "nokey",
        TelegramChannel(
            {"token_secret": "missing", "inbound_enabled": True, "chat_enabled": True,
             "allowed_senders": [SENDER], "chat_id": SENDER},
            secret_resolver=lambda _n: None,
        ),
    )
    n.add_channel(
        "mail",
        EmailChannel(
            {"host": "smtp.x", "from_addr": "me@x", "to_addr": "Boss@Acme.com",
             "password_secret": "pw", "inbound_enabled": True, "chat_enabled": True,
             "allowed_senders": ["boss@acme.com"]},
            secret_resolver=lambda _n: "pw",
        ),
    )
    n.add_channel(
        "hook",
        SlackChannel(
            {"webhook_url": "https://hooks.example/x", "token_secret": "sl", "channel": "U1",
             "inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["U1"]},
            secret_resolver=lambda _n: "xoxb",
        ),
    )
    n.add_channel(
        "dm",
        SlackChannel(
            {"token_secret": "sl", "channel": "U1", "inbound_enabled": True,
             "chat_enabled": True, "allowed_senders": ["U1"]},
            secret_resolver=lambda _n: "xoxb",
        ),
    )
    for name, sender in [
        ("phone", SENDER), ("oneway", SENDER), ("group", SENDER), ("nokey", SENDER),
        ("mail", "boss@acme.com"), ("hook", "U1"), ("dm", "U1"),
    ]:
        store.resolve(name, str(sender), "V")
    assert question_destinations(n, engine) == {"phone", "mail", "dm"}

    # The allowlist shrinking after the conversation existed: never asked.
    n.get("phone").config["allowed_senders"] = [999]
    assert "phone" not in question_destinations(n, engine)
    # No conversation yet (no identity row): never asked either.
    assert question_destinations(Notifier(), engine) == set()


def test_question_destinations_never_raises(tmp_path):
    class Broken:
        def channels(self):
            raise RuntimeError("boom")

    assert question_destinations(Broken(), make_engine(tmp_path / "db.sqlite")) == set()
