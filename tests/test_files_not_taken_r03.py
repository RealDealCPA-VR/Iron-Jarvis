"""R0.3 (build-out plan, Stage 0): Slack and e-mail say when they could not
take a file, the way Telegram already does, until 10-a reads files.

Before: the Slack pump dropped every message that has a subtype,
``file_share`` included, so a file sent to Jarvis in Slack got no word at all
(``slack_socket.SlackSocketMode.process_envelope``). An e-mail's attachments
were skipped by ``channels._email_body`` and nothing said so. Telegram already
answers a photo with one sentence (``TelegramChannel.MEDIA_NOTICE``).

Now, for an allowlisted sender in their own chat (in Slack also the
destination's own channel, answered in the thread):

* a message that is only a file gets one plain sentence that the file could
  not be used, and nothing else runs;
* a message with words and a file: the sentence first, then the words are
  handled exactly as before;
* strangers, bots, unproven e-mail senders and other Slack channels hear
  nothing, and Slack's other subtypes (edits, joins) are still dropped;
* a picture inside an e-mail's body (a signature logo) is not a file;
* the sentence is said at most once a minute per sender, so a burst of files
  is told once (the Telegram notice is once per sender per poll).

Everything runs through the real ``InboundPoller`` and the real channels; only
the network (Slack's Web API, IMAP, SMTP) and the chat turn are fakes.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage, Message
from email.mime.application import MIMEApplication
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import pytest

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.comm import Notifier
from iron_jarvis.comm import slack_socket as S
from iron_jarvis.comm.channels import EmailChannel, SlackChannel, _email_body, _email_file_count
from iron_jarvis.comm.inbound import FILES_NOT_READ_EVERY_S, InboundPoller, files_not_read_note
from iron_jarvis.comm.threads import CommThreadStore

OWN = "C0OWN0001"


@pytest.fixture(autouse=True)
def _fresh_listening():
    S.LISTENING.clear()
    yield
    S.LISTENING.clear()


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _turn(reply: str = "The total is 42."):
    async def turn(platform, personas, body) -> dict[str, Any]:
        turn.calls.append(body)
        return {
            "reply": reply, "provider": "mock", "model": "m", "tools_used": [],
            "escalate": False, "escalate_reason": "", "documents": [],
        }

    turn.calls = []
    return turn


def _poller(platform, name: str, ch: Any, turn: Any, clock: _Clock) -> InboundPoller:
    notifier = Notifier()
    notifier.add_channel(name, ch)
    return InboundPoller(
        notifier, Orchestrator(platform), platform.engine,
        event_bus=platform.event_bus, thread_store=CommThreadStore(platform.engine),
        chat_turn=turn, personas={}, platform=platform, clock=clock,
    )


def _last_user_words(body: Any) -> str:
    return str(body.messages[-1].content)


# --------------------------------------------------------------------------- #
# Slack
# --------------------------------------------------------------------------- #
def _slack(sent: list) -> SlackChannel:
    def http_post(url, payload, headers=None):  # noqa: ANN001
        sent.append(dict(payload))
        return {"ok": True}

    cfg = {
        "token_secret": "tok",
        "channel": OWN,
        "inbound_enabled": True,
        "chat_enabled": True,
        "allowed_senders": ["U1"],
    }
    return SlackChannel(cfg, http_post=http_post, secret_resolver=lambda n: "xoxb-test")


def _sock(poller: InboundPoller, ch: SlackChannel) -> S.SlackSocketMode:
    cfg = {"channels": {"hq": {"type": "slack", **ch.config}}}
    sock = S.SlackSocketMode(
        poller, poller.notifier, lambda n: "xoxb-test", lambda: cfg,
        slack_api=lambda method, token, params: {"ok": True, "user_id": "UBOT"},
    )
    sock._own["hq"] = OWN
    sock._bot_user["hq"] = "UBOT"
    return sock


def _env(**event: Any) -> dict:
    return {"type": "events_api", "envelope_id": "e1", "payload": {"event": event}}


def _files(n: int) -> list[dict]:
    return [{"id": f"F{i}", "name": f"scan{i}.pdf", "mimetype": "application/pdf"} for i in range(n)]


def _go(sock: S.SlackSocketMode, poller: InboundPoller, env: dict) -> Any:
    async def run():
        res = await sock.process_envelope("hq", env)
        await poller.drain()
        return res

    return asyncio.run(run())


def _slack_rig(platform):
    sent: list = []
    ch = _slack(sent)
    turn = _turn()
    clock = _Clock()
    poller = _poller(platform, "hq", ch, turn, clock)
    return sent, _sock(poller, ch), poller, turn, clock


def test_a_slack_file_alone_in_the_dm_is_answered_in_words(platform):
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    env = _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
               user="U1", text="", ts="1700.1", files=_files(1))

    res = _go(sock, poller, env)

    assert res is not None, "a Slack file message was dropped without a word"
    assert res["status"] == "files_not_read" and res["sent"] is True
    assert len(sent) == 1 and sent[0]["channel"] == "U1"
    words = sent[0]["text"]
    assert "can't take files" in words and "the file you sent" in words
    assert "Send your request as text" in words and "desktop app" in words
    assert turn.calls == []  # nothing else ran on a message with no words


def test_a_slack_file_with_words_says_so_first_then_answers_the_words(platform):
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    env = _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
               user="U1", text="what's the total on these?", ts="1700.2", files=_files(2))

    res = _go(sock, poller, env)

    assert len(turn.calls) == 1, "the words of a message with a file were thrown away"
    assert _last_user_words(turn.calls[0]).startswith("what's the total on these?")
    assert [p["channel"] for p in sent] == ["U1", "U1"]
    assert "can't take files" in sent[0]["text"]
    assert "the 2 files you sent" in sent[0]["text"]
    assert "read your message" in sent[0]["text"]
    assert "The total is 42." in sent[1]["text"]
    # The saved chat shows the files came, so the record is true later too.
    saved = poller.thread_store.history_body(res["thread_id"])
    assert saved[0]["role"] == "user"
    assert saved[0]["content"] == "what's the total on these?\n\n" + files_not_read_note(2)


def test_the_turn_is_told_the_files_were_not_read(platform):
    # Review finding: the model got the bare words, "what's the total on
    # these?", with nothing saying files came and were never seen, so it could
    # invent a total. It is now told, in plain words, after the sender's words.
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    env = _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
               user="U1", text="what's the total on these?", ts="1700.21", files=_files(2))

    _go(sock, poller, env)

    said = _last_user_words(turn.calls[0])
    assert said == "what's the total on these?\n\n" + files_not_read_note(2)
    assert "2 files" in said and "not read" in said
    assert "a file" in files_not_read_note(1) and "it was not read" in files_not_read_note(1)


def test_a_message_with_no_files_reaches_the_turn_unchanged(platform):
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    env = _env(type="message", channel="D0DM00001", channel_type="im",
               user="U1", text="what's on today?", ts="1700.22")

    _go(sock, poller, env)

    assert _last_user_words(turn.calls[0]) == "what's on today?"


class _Orch:
    """The one-shot door's orchestrator seam: records the run's task."""

    def __init__(self) -> None:
        self.tasks: list[str] = []

    async def create_session(self, task: str, agent_type: str, **kw: Any) -> None:
        self.tasks.append(task)
        return None


def test_a_one_shot_run_is_told_the_files_were_not_read(platform):
    # With two-way chat off, the words start a run; its task says the files
    # came and were not read, the same as a chat turn.
    sent: list = []
    ch = _slack(sent)
    ch.config["chat_enabled"] = False
    notifier = Notifier()
    notifier.add_channel("hq", ch)
    orch = _Orch()
    poller = InboundPoller(notifier, orch, platform.engine, event_bus=platform.event_bus,
                           platform=platform, clock=_Clock())
    sock = _sock(poller, ch)
    env = _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
               user="U1", text="file this for me", ts="1700.23", files=_files(1))

    _go(sock, poller, env)

    assert orch.tasks == ["file this for me\n\n" + files_not_read_note(1)]


def test_an_escalated_run_is_told_the_files_were_not_read(platform):
    # A chat turn that hands the work to a run: the run's task carries the
    # same line. The run is refused at its start (Emergency stop) once its
    # task is recorded, so nothing runs.
    from iron_jarvis.agents.orchestrator import EmergencyStopOn

    class _StoppedOrch(_Orch):
        async def create_session(self, task: str, agent_type: str, **kw: Any) -> None:
            self.tasks.append(task)
            raise EmergencyStopOn("Emergency stop is on.")

    sent: list = []
    ch = _slack(sent)
    notifier = Notifier()
    notifier.add_channel("hq", ch)
    orch = _StoppedOrch()

    async def turn(platform, personas, body) -> dict[str, Any]:
        return {"reply": "", "escalate": True, "escalate_reason": "work", "provider": "mock",
                "model": "m", "tools_used": [], "documents": []}

    poller = InboundPoller(notifier, orch, platform.engine, event_bus=platform.event_bus,
                           thread_store=CommThreadStore(platform.engine), chat_turn=turn,
                           personas={}, platform=platform, clock=_Clock())
    sock = _sock(poller, ch)
    env = _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
               user="U1", text="sort these receipts", ts="1700.24", files=_files(3))

    res = _go(sock, poller, env)

    assert res["status"] == "chat_refused"
    assert len(orch.tasks) == 1
    assert orch.tasks[0].endswith("sort these receipts\n\n" + files_not_read_note(3))


def test_a_slack_file_in_the_own_channel_is_answered_in_its_thread(platform):
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    env = _env(type="message", subtype="file_share", channel=OWN, channel_type="channel",
               user="U1", text="", ts="1700.3", files=_files(1))

    res = _go(sock, poller, env)

    assert res is not None and res["status"] == "files_not_read"
    assert len(sent) == 1
    assert sent[0]["channel"] == OWN and sent[0]["thread_ts"] == "1700.3"
    assert "can't take files" in sent[0]["text"]


def test_a_mention_elsewhere_with_a_file_is_told_in_the_dm(platform):
    # An app_mention carries its files with no subtype: the words always ran,
    # and the file vanished without a word.
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    env = _env(type="app_mention", channel="C0OTHER01", user="U1",
               text="<@UBOT> read this", ts="1700.4", files=_files(1))

    _go(sock, poller, env)

    assert [p["channel"] for p in sent] == ["U1", "U1", "U1"]
    assert sent[0]["text"] == S.MENTION_ELSEWHERE_NOTE
    assert "can't take files" in sent[1]["text"]
    assert "The total is 42." in sent[2]["text"]
    assert _last_user_words(turn.calls[0]) == "read this\n\n" + files_not_read_note(1)


@pytest.mark.parametrize("bot_known", [True, False], ids=["bot-id-known", "bot-id-unknown"])
@pytest.mark.parametrize("message_first", [True, False], ids=["message-first", "mention-first"])
def test_a_file_mention_elsewhere_is_answered_whichever_copy_comes_first(
    platform, message_first, bot_known
):
    # Review finding (blocking): Slack sends a mention in a channel Jarvis is
    # in twice, as ``message`` (here ``file_share``) and ``app_mention``, same
    # ts, in no fixed order. The file_share copy used to reach the duplicate
    # check first and use up the key, so the mention was dropped and never
    # answered. Whichever comes first, it is answered once, in the DM, with
    # the file sentence, and the channel's rules hear it once.
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    poller.reflex_router = rules = _Rules()
    if not bot_known:
        sock._bot_user["hq"] = ""  # auth.test failed at connect
    base = dict(channel="C0OTHER01", user="U1", text="<@UBOT> read this",
                ts="1700.41", files=_files(1))
    copy = _env(type="message", subtype="file_share", channel_type="channel", **base)
    mention = _env(type="app_mention", **base)

    for env in ([copy, mention] if message_first else [mention, copy]):
        _go(sock, poller, env)

    assert [p["channel"] for p in sent] == ["U1", "U1", "U1"], sent
    assert sent[0]["text"] == S.MENTION_ELSEWHERE_NOTE
    assert "can't take files" in sent[1]["text"]
    assert "The total is 42." in sent[2]["text"]
    assert len(turn.calls) == 1
    assert rules.slack == ["<@UBOT> read this"]


@pytest.mark.parametrize("text", ["<@UBOT> status?", "<@UBOT|iron jarvis> status?"])
@pytest.mark.parametrize("message_first", [True, False], ids=["message-first", "mention-first"])
def test_a_plain_mention_elsewhere_is_answered_whichever_copy_comes_first(
    platform, message_first, text
):
    # The same race without a file: the ``message`` copy must never use up the
    # key the ``app_mention`` copy answers with.
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    poller.reflex_router = rules = _Rules()
    base = dict(channel="C0OTHER01", user="U1", text=text, ts="1700.42")
    copy = _env(type="message", channel_type="channel", **base)
    mention = _env(type="app_mention", **base)

    for env in ([copy, mention] if message_first else [mention, copy]):
        _go(sock, poller, env)

    assert [p["channel"] for p in sent] == ["U1", "U1"], sent
    assert sent[0]["text"] == S.MENTION_ELSEWHERE_NOTE
    assert "The total is 42." in sent[1]["text"]
    assert len(turn.calls) == 1
    assert rules.slack == [text]


def test_a_message_naming_someone_else_elsewhere_still_fires_the_rules(platform):
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    poller.reflex_router = rules = _Rules()
    env = _env(type="message", channel="C0OTHER01", channel_type="channel", user="U1",
               text="<@U2> lunch?", ts="1700.43")

    assert _go(sock, poller, env) == {"channel": "hq", "status": "reflex", "sender": "U1"}
    assert rules.slack == ["<@U2> lunch?"]
    assert sent == [] and turn.calls == []


def test_strangers_bots_and_other_channels_hear_nothing(platform):
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    envs = [
        # a stranger's file in the DM
        _env(type="message", subtype="file_share", channel="D0DM00002", channel_type="im",
             user="U999", text="", ts="1701.1", files=_files(1)),
        # a bot's file (Jarvis's own upload included)
        _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
             user="U1", bot_id="B1", text="", ts="1701.2", files=_files(1)),
        _env(type="message", subtype="file_share", channel=OWN, channel_type="channel",
             user="UBOT", text="", ts="1701.3", files=_files(1)),
        # a file in a shared channel that is not the destination's own
        _env(type="message", subtype="file_share", channel="C0OTHER01", channel_type="channel",
             user="U1", text="", ts="1701.4", files=_files(1)),
    ]
    for env in envs:
        _go(sock, poller, env)

    assert sent == []
    assert turn.calls == []


class _Rules:
    """The reflex router seam: records what the Slack rules heard."""

    def __init__(self) -> None:
        self.slack: list[str] = []

    async def on_slack(self, *, text: str, channel: str = "", sender: str = "") -> list:
        self.slack.append(text)
        return []

    async def on_signal(self, *args: Any, **kwargs: Any) -> list:
        return []


def test_a_file_in_another_channel_is_left_alone_as_before(platform):
    # A file in a shared channel that is not the destination's own is not for
    # Jarvis: as before R0.3 it is dropped before the rules and before the
    # duplicate check (a mention's files ride on its app_mention copy), and
    # nothing is posted in a shared channel.
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    poller.reflex_router = rules = _Rules()
    env = _env(type="message", subtype="file_share", channel="C0OTHER01", channel_type="channel",
               user="U1", text="invoice for March", ts="1701.9", files=_files(1))

    assert _go(sock, poller, env) is None
    assert rules.slack == []
    assert sent == [] and turn.calls == []


def test_a_file_share_without_a_files_list_still_counts_one(platform):
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    env = _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
               user="U1", text="", ts="1701.8")

    res = _go(sock, poller, env)

    assert res is not None and res["status"] == "files_not_read"
    assert len(sent) == 1 and "the file you sent" in sent[0]["text"]


def test_the_sentence_failing_never_stops_the_words(platform, monkeypatch):
    from iron_jarvis.comm import inbound

    def boom(count: int, *, with_words: bool) -> str:
        raise RuntimeError("wording broke")

    monkeypatch.setattr(inbound, "files_not_read_reply", boom)
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    env = _env(type="message", subtype="file_share", channel="D0DM00001", channel_type="im",
               user="U1", text="still answer me", ts="1701.7", files=_files(1))

    res = _go(sock, poller, env)

    assert res["status"] != "error"
    assert len(turn.calls) == 1
    assert _last_user_words(turn.calls[0]).startswith("still answer me")
    assert len(sent) == 1 and "The total is 42." in sent[0]["text"]


@pytest.mark.parametrize("subtype", ["message_changed", "channel_join", "bot_message", "thread_broadcast"])
def test_other_slack_subtypes_are_still_dropped(platform, subtype):
    sent, sock, poller, turn, _clock = _slack_rig(platform)
    env = _env(type="message", subtype=subtype, channel="D0DM00001", channel_type="im",
               user="U1", text="hello", ts="1702.1", files=_files(1))

    assert _go(sock, poller, env) is None
    assert sent == [] and turn.calls == []


def test_a_burst_of_slack_files_is_told_once_a_minute(platform):
    sent, sock, poller, turn, clock = _slack_rig(platform)

    def file_dm(ts: str) -> dict:
        return _env(type="message", subtype="file_share", channel="D0DM00001",
                    channel_type="im", user="U1", text="", ts=ts, files=_files(1))

    _go(sock, poller, file_dm("1703.1"))
    clock.now += 10
    second = _go(sock, poller, file_dm("1703.2"))
    assert len(sent) == 1
    assert second["status"] == "files_not_read" and second["sent"] is False
    clock.now += FILES_NOT_READ_EVERY_S
    _go(sock, poller, file_dm("1703.3"))
    assert len(sent) == 2 and "can't take files" in sent[1]["text"]


# --------------------------------------------------------------------------- #
# E-mail
# --------------------------------------------------------------------------- #
def _proof(msg: EmailMessage, sender: str) -> None:
    domain = sender.rsplit("@", 1)[-1].rstrip(">")
    msg["Authentication-Results"] = (
        f"mx.example.com; dkim=pass header.d={domain}; spf=pass smtp.mailfrom={domain}"
    )


def _mail(
    subject: str = "Invoice",
    body: str = "Please file this.",
    *,
    sender: str = "Alice <alice@example.com>",
    proven: bool = True,
    pdfs: int = 1,
) -> EmailMessage:
    msg = EmailMessage()
    if proven:
        _proof(msg, sender)
    msg["From"] = sender
    msg["To"] = "jarvis@example.com"
    msg["Subject"] = subject
    msg.set_content(body)
    for i in range(pdfs):
        msg.add_attachment(b"%PDF-1.4 test", maintype="application", subtype="pdf",
                           filename=f"invoice{i}.pdf")
    return msg


class _FakeIMAP:
    def __init__(self, mails: list[tuple[int, bytes]]) -> None:
        self.mails = list(mails)

    def login(self, user: str, password: str):
        return "OK", [b"ok"]

    def select(self, mailbox: str = "INBOX"):
        return "OK", [b"1"]

    def uid(self, command: str, *args: Any):
        if command.upper() == "SEARCH":
            lo = int(str(args[-1]).split()[1].split(":")[0])
            ids = [u for u, _ in self.mails if u >= lo]
            return "OK", [b" ".join(str(u).encode() for u in ids)]
        uid = int(args[0])
        for u, raw in self.mails:
            if u == uid:
                return "OK", [(f"{uid} (RFC822".encode(), raw), b")"]
        return "OK", [None]

    def logout(self):
        return "BYE", [b"bye"]


class _FakeSMTP:
    """Stands in for ``smtplib.SMTP``: every sent body lands in ``sent``."""

    sent: list[str] = []

    def __init__(self, host: str, port: int, timeout: float = 0) -> None:
        pass

    def __enter__(self) -> "_FakeSMTP":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def starttls(self) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        pass

    def send_message(self, msg: EmailMessage) -> None:
        _FakeSMTP.sent.append(msg.get_content().strip())


_EMAIL_CFG = {
    "host": "smtp.example.com",
    "imap_host": "imap.example.com",
    "username": "jarvis@example.com",
    "password_secret": "imap_pw",
    "to_addr": "alice@example.com",
    "inbound_enabled": True,
    "chat_enabled": True,
    "allowed_senders": ["alice@example.com"],
}


def _email_rig(platform, monkeypatch, mails: list[EmailMessage]):
    fake = _FakeIMAP([(10 + i, m.as_bytes()) for i, m in enumerate(mails)])
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda host, port: fake)
    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    ch = EmailChannel(dict(_EMAIL_CFG), secret_resolver=lambda n: "pw-test" if n == "imap_pw" else None)
    turn = _turn()
    clock = _Clock()
    poller = _poller(platform, "mail", ch, turn, clock)

    async def run():
        res = await poller.poll_once()
        await poller.drain()
        return res

    return asyncio.run(run()), turn


def test_an_email_attachment_is_said_and_the_words_still_run(platform, monkeypatch):
    results, turn = _email_rig(platform, monkeypatch, [_mail()])

    assert len(_FakeSMTP.sent) == 2, _FakeSMTP.sent
    note, answer = _FakeSMTP.sent
    assert "can't take files" in note and "the file you sent" in note
    assert "read your message" in note
    assert "The total is 42." in answer
    assert len(turn.calls) == 1
    said = _last_user_words(turn.calls[0])
    assert "Please file this." in said
    assert said.endswith("\n\n" + files_not_read_note(1))


def test_an_email_that_is_only_a_file_is_answered_in_words(platform, monkeypatch):
    results, turn = _email_rig(platform, monkeypatch, [_mail(subject="", body="", pdfs=2)])

    assert [r["status"] for r in results] == ["files_not_read"]
    assert len(_FakeSMTP.sent) == 1
    assert "the 2 files you sent" in _FakeSMTP.sent[0]
    assert "Send your request as text" in _FakeSMTP.sent[0]
    assert turn.calls == []


def test_an_unproven_senders_attachment_gets_no_answer(platform, monkeypatch):
    results, turn = _email_rig(platform, monkeypatch, [_mail(proven=False)])

    assert [r["status"] for r in results] == ["unauthorized"]
    assert _FakeSMTP.sent == [] and turn.calls == []


def test_a_strangers_attachment_gets_no_answer(platform, monkeypatch):
    stranger = _mail(sender="Mallory <mallory@example.org>")
    results, turn = _email_rig(platform, monkeypatch, [stranger])

    assert [r["status"] for r in results] == ["unauthorized"]
    assert _FakeSMTP.sent == [] and turn.calls == []


def test_two_emails_with_files_in_one_poll_are_told_once(platform, monkeypatch):
    results, turn = _email_rig(
        platform, monkeypatch, [_mail(body="First."), _mail(body="Second.")]
    )

    notes = [s for s in _FakeSMTP.sent if "can't take files" in s]
    assert len(notes) == 1
    assert len(turn.calls) == 2  # both e-mails' words were answered


def test_an_email_without_files_gets_no_note(platform, monkeypatch):
    results, turn = _email_rig(platform, monkeypatch, [_mail(pdfs=0)])

    assert len(_FakeSMTP.sent) == 1 and "can't take files" not in _FakeSMTP.sent[0]
    assert len(turn.calls) == 1


# --------------------------------------------------------------------------- #
# What counts as a file in an e-mail
# --------------------------------------------------------------------------- #
def test_attachments_are_counted():
    assert _email_file_count(_mail(pdfs=0)) == 0
    assert _email_file_count(_mail(pdfs=1)) == 1
    assert _email_file_count(_mail(pdfs=3)) == 3


def test_a_picture_inside_the_body_is_not_a_file():
    # A signature logo: an inline picture the HTML body refers to by cid.
    msg = EmailMessage()
    msg["Subject"] = "Hello"
    msg.set_content("Hello there.")
    msg.add_alternative('<p>Hello there.</p><img src="cid:logo">', subtype="html")
    html = msg.get_payload()[1]
    html.add_related(b"\x89PNG test", maintype="image", subtype="png", cid="<logo>",
                     filename="image001.png", disposition="inline")
    assert _email_file_count(msg) == 0


def test_a_named_inline_file_outside_the_body_is_a_file():
    # Apple Mail sends an attached PDF as "inline" with a file name.
    msg = EmailMessage()
    msg["Subject"] = "Scan"
    msg.set_content("See the scan.")
    msg.add_attachment(b"%PDF-1.4 test", maintype="application", subtype="pdf",
                       filename="scan.pdf", disposition="inline")
    assert _email_file_count(msg) == 1


def test_an_attached_email_is_one_file_whatever_it_carries():
    inner = _mail(pdfs=2)
    outer = EmailMessage()
    outer["Subject"] = "Fwd: Invoice"
    outer.set_content("Forwarding this.")
    outer.add_attachment(inner)
    assert _email_file_count(outer) == 1


def test_an_email_that_is_one_file_is_not_read_as_words():
    msg = EmailMessage()
    msg["Subject"] = "Scan"
    msg.set_content(b"%PDF-1.4 \x00\x01 binary", maintype="application", subtype="pdf",
                    filename="scan.pdf")
    assert _email_file_count(msg) == 1
    assert _email_body(msg) == ""  # never the file's bytes as the sender's words


def _signed(inner: Message, *, kind: str = "pkcs7-signature", name: str = "smime.p7s") -> Message:
    """A signed e-mail as Outlook (S/MIME) or Thunderbird (OpenPGP) sends it:
    ``multipart/signed`` = the message itself + the signature, attached."""
    outer = MIMEMultipart("signed", protocol=f"application/{kind}", micalg="sha-256")
    outer.attach(inner)
    sig = MIMEApplication(b"0\x82 not a real signature", _subtype=kind, name=name)
    sig.add_header("Content-Disposition", "attachment", filename=name)
    outer.attach(sig)
    return outer


def _with_key(words: str) -> Message:
    """Thunderbird's OpenPGP message with the sender's public key attached."""
    inner = MIMEMultipart("mixed")
    inner.attach(MIMEText(words))
    key = MIMEApplication(b"-----BEGIN PGP PUBLIC KEY BLOCK-----", _subtype="pgp-keys",
                          name="OpenPGP_0x0123456789ABCDEF.asc")
    key.add_header("Content-Disposition", "attachment", filename="OpenPGP_0x0123456789ABCDEF.asc")
    inner.attach(key)
    return inner


def test_a_signature_is_not_a_file():
    # Review finding: every signed e-mail was told "I read your message but
    # not the file you sent", because the signature is an attached part.
    smime = _signed(MIMEText("What is on my calendar today?"))
    pgp = _signed(_with_key("Hi"), kind="pgp-signature", name="OpenPGP_signature.asc")
    old_smime = _signed(MIMEText("Hi"), kind="x-pkcs7-signature")
    assert _email_file_count(smime) == 0
    assert _email_file_count(pgp) == 0
    assert _email_file_count(old_smime) == 0


def test_a_file_in_a_signed_email_is_still_a_file():
    inner = MIMEMultipart("mixed")
    inner.attach(MIMEText("The scan is attached."))
    pdf = MIMEApplication(b"%PDF-1.4 test", _subtype="pdf", name="scan.pdf")
    pdf.add_header("Content-Disposition", "attachment", filename="scan.pdf")
    inner.attach(pdf)
    assert _email_file_count(_signed(inner)) == 1


def test_an_encrypted_email_is_not_called_a_file():
    # Encrypted mail is a message Iron Jarvis cannot open, not a file the
    # person sent: "I couldn't use the file you sent" would be false.
    smime = MIMEApplication(b"0\x82 sealed", _subtype="pkcs7-mime", name="smime.p7m")
    smime.set_param("smime-type", "enveloped-data")
    smime.add_header("Content-Disposition", "attachment", filename="smime.p7m")
    pgp = MIMEMultipart("encrypted", protocol="application/pgp-encrypted")
    version = MIMEBase("application", "pgp-encrypted")
    version.set_payload("Version: 1")
    pgp.attach(version)
    sealed = MIMEApplication(b"-----BEGIN PGP MESSAGE-----", _subtype="octet-stream",
                             name="encrypted.asc")
    sealed.add_header("Content-Disposition", "inline", filename="encrypted.asc")
    pgp.attach(sealed)
    assert _email_file_count(smime) == 0
    assert _email_file_count(pgp) == 0


def test_a_signed_email_with_no_file_gets_no_file_sentence(platform, monkeypatch):
    signed = _signed(MIMEText("What is on my calendar today?"))
    _proof(signed, "Alice <alice@example.com>")  # type: ignore[arg-type]
    signed["From"] = "Alice <alice@example.com>"
    signed["To"] = "jarvis@example.com"
    signed["Subject"] = "Calendar"

    results, turn = _email_rig(platform, monkeypatch, [signed])  # type: ignore[list-item]

    assert len(_FakeSMTP.sent) == 1, _FakeSMTP.sent
    assert "can't take files" not in _FakeSMTP.sent[0]
    assert "The total is 42." in _FakeSMTP.sent[0]
    assert "file" not in _last_user_words(turn.calls[0])


def test_the_poll_carries_the_count(monkeypatch):
    fake = _FakeIMAP([(5, _mail(pdfs=2).as_bytes()), (6, _mail(pdfs=0).as_bytes())])
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda host, port: fake)
    ch = EmailChannel(dict(_EMAIL_CFG), secret_resolver=lambda n: "pw-test")
    messages, _offset = ch.poll(0)
    assert [m.files_not_read for m in messages] == [2, 0]
