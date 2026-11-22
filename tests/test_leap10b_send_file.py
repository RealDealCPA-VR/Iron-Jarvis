"""10-b (build-out plan R2.4): finished files come back to the phone.

The answer to "turn this into a spreadsheet", sent from Telegram, Slack or
email, arrives with the spreadsheet. Which files: what the work made, read
off its own record, inside the folders it was done in, at most five of
20 MB each (``agents/reply_files``, the reading a paired computer's answer
uses too). When: the FIRST time on each destination the phone is asked
("Send 1 spreadsheet (8 bytes...) to Telegram?"), and the answer is the
setting ``phone_send_files`` with Undo; a yes said on Telegram is not a yes
for Slack. Every service's calls are recorded here; nothing touches a
network, and the bot token is in no log, event or detail.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.agents import reply_files
from iron_jarvis.agents.reply_files import Caps, pick
from iron_jarvis.comm import files_back
from iron_jarvis.comm.base import InboundMessage
from iron_jarvis.comm.channels import EmailChannel
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import AgentRun, Session, SessionStatus, ToolInvocation, UndoJournal
from tests._fakes.phone_back import (
    SLACK_DM,
    SLACK_OWN,
    SLACK_TOKEN,
    SLACK_USER,
    FakeSlackApi,
    FakeTelegramBack,
    making_turn,
    slack,
    telegram_back,
)
from tests._fakes.phone_files import BOT_TOKEN, ME, message, poller, run

pytestmark = pytest.mark.usefixtures("no_network")


def _text(update_id: int, words: str) -> dict:
    return message(update_id, text=words)


def _uploads_dir(platform) -> Path:
    return Path(platform.config.home) / "uploads"


def _events(platform, etype: str) -> list[dict]:
    return [dict(e.payload) for e in platform.event_bus.history if e.type == etype]


# --------------------------------------------------------------------------- #
# the chat reply's file, on Telegram
# --------------------------------------------------------------------------- #
def test_a_yes_chosen_in_settings_sends_the_turns_spreadsheet_after_the_words(platform, caplog):
    platform.config.phone_send_files = "yes"  # chosen in Settings: no destination kept
    sheet = _uploads_dir(platform) / "march.xlsx"  # the turn's own folder (no chat folder)
    fake = FakeTelegramBack([_text(1, "turn this into a spreadsheet")])
    turn = making_turn(sheet)
    caplog.set_level(logging.DEBUG)

    res = run(poller(platform, telegram_back(fake), turn=turn))

    assert res[0]["status"] == "chat" and res[0]["files"] == "sent"
    assert fake.texts == ["Iron Jarvis: Here is your spreadsheet."]
    assert len(fake.uploads) == 1
    up = fake.uploads[0]
    assert up["method"] == "sendDocument" and up["field"] == "document"
    assert up["name"] == "march.xlsx" and up["data"] == b"PK-sheet"
    assert up["fields"]["chat_id"] == ME
    # The bot token rode the address only: no log line, no event, no detail.
    assert BOT_TOKEN not in caplog.text
    sent = _events(platform, EventType.COMM_FILE_SENT)
    assert sent == [{"channel": "tg", "kind": "spreadsheet", "bytes": 8}]
    assert BOT_TOKEN not in json.dumps(sent)


def test_a_picture_goes_by_send_photo_and_a_large_one_as_a_document(platform, tmp_path):
    fake = FakeTelegramBack([])
    ch = telegram_back(fake)
    small = tmp_path / "chart.png"
    small.write_bytes(b"\x89PNG" + b"0" * 10)
    big = tmp_path / "scan.png"
    big.write_bytes(b"\x89PNG" + b"0" * 20)
    ch.PHOTO_MAX_BYTES = 15  # the 10 MB line, shrunk for the test

    assert ch.send_file(str(small), chat_id=ME)["ok"]
    assert ch.send_file(str(big), chat_id=ME)["ok"]

    assert [(u["method"], u["field"]) for u in fake.uploads] == [
        ("sendPhoto", "photo"), ("sendDocument", "document")]


def test_a_failed_upload_says_so_in_words_and_never_names_the_token(platform, caplog):
    platform.config.phone_send_files = "yes"
    sheet = _uploads_dir(platform) / "march.xlsx"
    fake = FakeTelegramBack([_text(1, "make the sheet")])
    fake.upload_error = RuntimeError(f"connect failed for https://api.telegram.org/bot{BOT_TOKEN}/sendDocument")
    caplog.set_level(logging.DEBUG)

    run(poller(platform, telegram_back(fake), turn=making_turn(sheet)))

    assert fake.texts[0] == "Iron Jarvis: Here is your spreadsheet."
    said = fake.texts[1]
    assert said == "Iron Jarvis: I couldn't send march.xlsx to Telegram: the upload failed (RuntimeError)."
    assert BOT_TOKEN not in caplog.text and BOT_TOKEN not in said


# --------------------------------------------------------------------------- #
# the first time on a destination: the card, then the setting
# --------------------------------------------------------------------------- #
def test_the_first_time_on_a_destination_asks_and_send_sends_and_is_kept(platform):
    assert platform.config.phone_send_files == "ask"  # the default
    sheet = _uploads_dir(platform) / "march.xlsx"
    fake = FakeTelegramBack([_text(1, "turn this into a spreadsheet")])
    poll = poller(platform, telegram_back(fake), turn=making_turn(sheet))

    res = run(poll)

    assert res[0]["files"] == "asked"
    assert fake.uploads == []  # nothing left the PC before the yes
    question = fake.sent[-1]
    assert question["text"].startswith("Iron Jarvis: Send 1 spreadsheet (1 KB) to Telegram?")
    assert [b["text"] for b in question["reply_markup"]["inline_keyboard"][0]] == ["Send", "Keep it on the PC"]
    # The buttons AND the typed answer: a press that cannot come back (a
    # Slack app set up before interactivity) still leaves a way to answer.
    assert question["text"].endswith("Reply 1 to send or 2 to keep it on the PC.")

    # The answer by a typed "1" (the bare number answers the newest question).
    fake.updates = [_text(2, "1")]
    run(poll)

    assert [u["name"] for u in fake.uploads] == ["march.xlsx"]
    assert platform.config.phone_send_files == "yes"
    assert platform.config.phone_send_files_to == ["tg"]
    assert fake.texts[-1] == "Iron Jarvis: → Sent 1 spreadsheet (1 KB) to Telegram."

    # From now on: sent with the answer, never asked again on Telegram.
    fake.updates = [_text(3, "another one")]
    run(poll)
    assert len(fake.uploads) == 2
    assert not any("Send 1 spreadsheet" in t for t in fake.texts[-2:])


def test_a_reply_while_the_question_waits_reminds_how_to_answer(platform):
    from tests._fakes.phone_files import fake_turn

    sheet = _uploads_dir(platform) / "march.xlsx"
    fake = FakeTelegramBack([_text(1, "make the sheet")])
    poll = poller(platform, telegram_back(fake), turn=making_turn(sheet))
    run(poll)
    poll.chat_turn = fake_turn("You're welcome.")
    fake.updates = [_text(2, "thanks")]
    run(poll)
    assert fake.texts[-1].endswith(
        "(Still waiting for your answer: Send 1 spreadsheet (1 KB) to Telegram? I made it for "
        "your message. Your answer is kept for Telegram (Settings, Phone, Send finished files to "
        "your phone changes it). Reply 1 to send or 2 to keep it on the PC.)")
    assert fake.uploads == []


def test_keep_it_on_the_pc_keeps_it_and_the_next_answer_is_not_asked(platform):
    sheet = _uploads_dir(platform) / "march.xlsx"
    fake = FakeTelegramBack([_text(1, "make the sheet")])
    poll = poller(platform, telegram_back(fake), turn=making_turn(sheet))
    run(poll)

    fake.updates = [_text(2, "2")]
    run(poll)

    assert fake.uploads == []
    # Kept for Telegram, the destination it was asked on: no global "no".
    assert platform.config.phone_send_files_kept == ["tg"]
    assert platform.config.phone_send_files == "ask"
    assert fake.texts[-1] == ("Iron Jarvis: OK: finished files stay on this PC from now on when you "
                              "ask from Telegram (Settings, Phone, Send finished files to your phone "
                              "changes it).")
    fake.updates = [_text(3, "make another")]
    run(poll)
    assert fake.uploads == [] and "Send 1" not in fake.texts[-1]


def test_a_keep_said_on_slack_is_not_a_keep_for_telegram(platform):
    # The reviewer's probe: a Send on Telegram, then a Keep on Slack. The
    # question says "Your answer is kept for <destination>": both hold.
    from iron_jarvis.settings.writer import ConfigWriter

    platform.config_writer = ConfigWriter(SimpleNamespace(platform=platform))
    files_back.keep_choice(platform, "send", "tg")
    files_back.keep_choice(platform, "keep", "slack")
    cfg = platform.config
    assert files_back.consent(cfg, "tg") == "send"
    assert files_back.consent(cfg, "slack") == "keep"
    assert files_back.consent(cfg, "mail") == "ask"  # never asked: asks its own first time


def test_a_first_keep_on_slack_leaves_telegram_to_ask(platform):
    from iron_jarvis.settings.writer import ConfigWriter

    platform.config_writer = ConfigWriter(SimpleNamespace(platform=platform))
    files_back.keep_choice(platform, "keep", "slack")
    assert files_back.consent(platform.config, "slack") == "keep"
    assert files_back.consent(platform.config, "tg") == "ask"
    # A later Send on Telegram is Telegram's alone.
    files_back.keep_choice(platform, "send", "tg")
    assert files_back.consent(platform.config, "tg") == "send"
    assert files_back.consent(platform.config, "slack") == "keep"


def test_a_yes_said_on_telegram_is_not_a_yes_for_slack():
    cfg = SimpleNamespace(phone_send_files="yes", phone_send_files_to=["tg"])
    assert files_back.consent(cfg, "tg") == "send"
    assert files_back.consent(cfg, "slack") == "ask"
    # A yes chosen in Settings (no destination kept) is every destination.
    assert files_back.consent(SimpleNamespace(phone_send_files="yes", phone_send_files_to=[]), "slack") == "send"
    assert files_back.consent(SimpleNamespace(phone_send_files="no", phone_send_files_to=["tg"]), "tg") == "keep"
    assert files_back.consent(SimpleNamespace(phone_send_files="ask", phone_send_files_to=["tg"]), "tg") == "ask"
    assert files_back.consent(SimpleNamespace(), "tg") == "ask"


def test_no_reading_of_the_kept_answers_sends_where_nobody_said_send():
    def consent(setting, to, kept, where):
        return files_back.consent(SimpleNamespace(phone_send_files=setting, phone_send_files_to=to,
                                                  phone_send_files_kept=kept), where)

    # A Keep chosen in Settings (or in chat) keeps every destination's files.
    assert consent("no", ["tg"], ["slack"], "tg") == "keep"
    assert consent("no", [], ["slack"], "mail") == "keep"
    # Undo of the phone's first Send puts "ask" back: Telegram is asked again.
    assert consent("ask", ["tg"], [], "tg") == "ask"
    # A Keep on Slack after a Send on Telegram, then that Keep undone: the
    # yes that comes back is never wider than the phone's own answers.
    assert consent("yes", ["tg"], ["slack"], "tg") == "send"
    assert consent("yes", ["tg"], ["slack"], "slack") == "keep"
    assert consent("yes", ["tg"], ["slack"], "mail") == "ask"
    # A Send on Telegram, then a Keep on Telegram, then that Keep undone.
    assert consent("yes", [], ["tg"], "tg") == "keep"
    assert consent("yes", [], ["tg"], "mail") == "ask"
    # Only Keeps on record, Settings left at ask: the Keeps hold, the rest ask.
    assert consent("ask", [], ["slack"], "slack") == "keep"
    assert consent("ask", [], ["slack"], "tg") == "ask"


def test_undoing_the_phones_first_send_asks_again(platform):
    from iron_jarvis.settings.writer import ConfigWriter

    writer = ConfigWriter(SimpleNamespace(platform=platform))
    platform.config_writer = writer
    files_back.keep_choice(platform, "send", "tg")
    assert files_back.consent(platform.config, "tg") == "send"
    writer.restore({"phone_send_files": "ask"})  # what the ledger's Undo puts back
    assert files_back.consent(platform.config, "tg") == "ask"
    # The next answer (a Keep on Slack) starts the record again: the undone
    # Send on Telegram does not come back with it.
    files_back.keep_choice(platform, "keep", "slack")
    assert platform.config.phone_send_files_to == []
    assert platform.config.phone_send_files_kept == ["slack"]
    assert files_back.consent(platform.config, "tg") == "ask"
    assert files_back.consent(platform.config, "slack") == "keep"


def test_after_send_the_reply_names_only_the_files_that_went(platform):
    from tests._fakes.phone_back import making_turn_of

    a, b = _uploads_dir(platform) / "a.xlsx", _uploads_dir(platform) / "b.xlsx"
    fake = FakeTelegramBack([_text(1, "make the two sheets")])
    poll = poller(platform, telegram_back(fake), turn=making_turn_of([a, b]))
    run(poll)
    assert fake.uploads == []

    fake.fail_names = {"b.xlsx"}
    fake.updates = [_text(2, "1")]
    run(poll)

    assert [u["name"] for u in fake.uploads] == ["a.xlsx"]
    said = fake.texts[-1]
    assert said.startswith("Iron Jarvis: → Sent 1 spreadsheet (1 KB) to Telegram.")
    assert "2 spreadsheets" not in said
    assert "I couldn't send b.xlsx to Telegram: the upload failed (RuntimeError)." in said


def test_the_question_says_how_to_answer_by_typing_on_slack_too(platform):
    # Slack shows buttons whenever its app token is set, but an app set up
    # before interactivity was in the manifest never gets the press back.
    sheet = _uploads_dir(platform) / "march.xlsx"
    api = FakeSlackApi()
    ch = slack(api)
    poll = poller(platform, ch, name="slack", turn=making_turn(sheet))
    msg = InboundMessage(sender_id=SLACK_USER, text="make the sheet", reply_to=SLACK_USER)

    async def go():
        out = await poll._handle("slack", ch, msg)
        await poll.drain()
        return out

    res = asyncio.run(go())

    assert res["files"] == "asked"
    asked = [p for m, p in api.posts if m == "chat.postMessage" and p.get("blocks")]
    assert asked, "the question went without its buttons"
    assert asked[-1]["text"].startswith("Iron Jarvis: Send 1 spreadsheet (1 KB) to Slack?")
    assert asked[-1]["text"].endswith("Reply 1 to send or 2 to keep it on the PC.")


def test_the_files_question_is_looked_at_off_the_event_loop(platform, monkeypatch):
    # Whether the question still waits stats its files on disk (a project
    # folder can be a network share or a OneDrive folder not on this PC):
    # never on the event loop (CLAUDE.md, Daemon). Three doors reach it: a
    # chat reply's reminder, "/answer" and a bare number.
    import threading

    from tests._fakes.phone_files import fake_turn

    sheet = _uploads_dir(platform) / "march.xlsx"
    fake = FakeTelegramBack([_text(1, "make the sheet")])
    poll = poller(platform, telegram_back(fake), turn=making_turn(sheet))
    run(poll)
    loop_thread = threading.get_ident()  # run() drives the loop on this thread
    seen: list[int] = []
    real = files_back.prompt_fresh

    def spy(prompt):
        seen.append(threading.get_ident())
        return real(prompt)

    monkeypatch.setattr(files_back, "prompt_fresh", spy)
    poll.chat_turn = fake_turn("You're welcome.")
    for i, words in enumerate(("thanks", "/answer", "1"), start=2):
        fake.updates = [_text(i, words)]
        run(poll)

    assert len(seen) >= 3, seen
    assert loop_thread not in seen
    assert [u["name"] for u in fake.uploads] == ["march.xlsx"]  # the "1" still answered


def test_the_phones_answer_goes_through_the_settings_writer_with_undo(platform):
    from iron_jarvis.settings.writer import ConfigWriter

    platform.config_writer = ConfigWriter(SimpleNamespace(platform=platform))
    files_back.keep_choice(platform, "send", "tg")
    assert platform.config.phone_send_files == "yes"
    assert platform.config.phone_send_files_to == ["tg"]
    # The writer journalled it: the Activity page can undo it.
    from iron_jarvis.core.models import ToolInvocation as TI

    from sqlmodel import select

    with session_scope(platform.engine) as db:
        rows = [r for r in db.exec(select(TI)).all()
                if r.tool == "update_settings" and "phone_send_files" in (r.args_json or "")]
    assert rows, "the yes was not journalled by the settings writer"


# --------------------------------------------------------------------------- #
# which files: inside the folders, within the caps
# --------------------------------------------------------------------------- #
def test_a_file_outside_the_chats_folder_is_named_and_not_sent(platform, tmp_path):
    platform.config.phone_send_files = "yes"
    outside = tmp_path / "elsewhere" / "clients.xlsx"
    fake = FakeTelegramBack([_text(1, "edit that sheet")])

    run(poller(platform, telegram_back(fake), turn=making_turn(outside)))

    assert fake.uploads == []
    assert fake.texts[-1] == ("Iron Jarvis: Also made, kept on this PC and not sent: clients.xlsx "
                              "(it is not in the folder this work was done in).")


def test_the_caps_hold_five_files_and_the_size_of_each(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    paths = []
    for i in range(7):
        p = folder / f"f{i}.txt"
        p.write_bytes(b"x" * 10)
        paths.append(str(p))
    big = folder / "big.txt"
    big.write_bytes(b"x" * 50)

    got = pick([str(big)] + paths, [folder], Caps(count=5, each=40))

    assert [p.name for p in got.files] == ["f0.txt", "f1.txt", "f2.txt", "f3.txt", "f4.txt"]
    assert got.left[0][0] == "big.txt" and "the most sent is" in got.left[0][1]
    assert [n for n, _ in got.left[1:]] == ["f5.txt", "f6.txt"]
    assert all("at most 5 files" in why for _, why in got.left[1:])
    assert reply_files.PHONE_CAPS == Caps(count=5, each=20 * 1024 * 1024, total=None)


def test_a_link_out_of_the_folder_is_not_followed(tmp_path):
    folder = tmp_path / "work"
    folder.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("not for the phone", encoding="utf-8")
    link = folder / "shortcut.txt"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("this PC cannot make a link here")
    got = pick([str(link)], [folder], Caps())
    assert got.files == [] and got.left[0][0] == "shortcut.txt"


# --------------------------------------------------------------------------- #
# done when: a phone-started run that writes a spreadsheet sends it back on
# Telegram and on Slack
# --------------------------------------------------------------------------- #
class _RunThatWrites:
    """A run (the orchestrator's create + run) that writes a spreadsheet in
    its own folder and leaves the ledger rows a real write_file leaves."""

    def __init__(self, platform, ws: Path, name: str = "march.xlsx",
                 status: SessionStatus = SessionStatus.COMPLETED) -> None:
        self.platform, self.ws, self.name, self.status = platform, ws, name, status
        self.sid = "session_10b_run"

    async def create_session(self, task, agent_type, **kw):
        self.ws.mkdir(parents=True, exist_ok=True)
        with session_scope(self.platform.engine) as db:
            db.add(Session(id=self.sid, task=task, status=SessionStatus.ACTIVE,
                           workspace_path=str(self.ws), origin=kw.get("origin")))
            db.commit()
        return SimpleNamespace(id=self.sid, provider="")

    async def run_session(self, sid):
        path = self.ws / self.name
        path.write_bytes(b"PK-sheet-from-a-run")
        with session_scope(self.platform.engine) as db:
            db.add(AgentRun(id="run_10b", session_id=sid))
            db.add(ToolInvocation(id="tool_10b", session_id=sid, agent_run_id="run_10b",
                                  tool="write_file", ok=True, reversibility="reversible"))
            db.add(UndoJournal(action_id="tool_10b", session_id=sid, agent_run_id="run_10b",
                               tool="write_file", kind="file_delete",
                               pre_inline=json.dumps({"path": str(path)})))
            row = db.get(Session, sid)
            row.status = self.status
            row.summary = "Made the March spreadsheet."
            db.add(row)
            db.commit()
        return SimpleNamespace(summary="Made the March spreadsheet.", status=self.status)


def test_a_phone_started_run_that_writes_a_spreadsheet_sends_it_back_on_telegram(platform, tmp_path):
    platform.config.phone_send_files = "yes"
    fake = FakeTelegramBack([_text(1, "make the March spreadsheet")])
    orch = _RunThatWrites(platform, tmp_path / "run-folder")

    res = run(poller(platform, telegram_back(fake, chat_enabled=False), orchestrator=orch))

    assert res[0]["status"] == "handled"
    assert [u["name"] for u in fake.uploads] == ["march.xlsx"]
    assert fake.uploads[0]["data"] == b"PK-sheet-from-a-run"
    # The words first (the ack, then the summary naming the file), the file after.
    assert fake.texts[-1].startswith("Iron Jarvis: Made the March spreadsheet.")


def test_a_run_that_did_not_finish_sends_nothing(platform, tmp_path):
    platform.config.phone_send_files = "yes"
    fake = FakeTelegramBack([_text(1, "make the March spreadsheet")])
    orch = _RunThatWrites(platform, tmp_path / "run-folder", status=SessionStatus.FAILED)

    run(poller(platform, telegram_back(fake, chat_enabled=False), orchestrator=orch))

    assert fake.uploads == []
    # Its words still say which file it kept.
    assert "march.xlsx" in fake.texts[-1]


def test_a_second_send_answer_sends_the_files_once(platform):
    sheet = _uploads_dir(platform) / "march.xlsx"
    fake = FakeTelegramBack([_text(1, "make the sheet")])
    poll = poller(platform, telegram_back(fake), turn=making_turn(sheet))
    run(poll)
    from sqlmodel import select

    from iron_jarvis.comm.models import PendingPromptRecord as P

    with session_scope(platform.engine) as db:
        rec = db.exec(select(P).where(P.kind == "file_send")).first()

    async def both():
        # Two answers racing (a typed "/answer send" and a press): the
        # question is claimed first, so the files go once.
        msg = InboundMessage(sender_id=str(ME), text="", reply_to=ME)
        a = poll._resolve_file_prompt("tg", poll.notifier.get("tg"), msg, "", True, rec, "Send", "Send")
        b = poll._resolve_file_prompt("tg", poll.notifier.get("tg"), msg, "", True, rec, "Send", "Send")
        return await asyncio.gather(a, b)

    results = asyncio.run(both())
    assert sorted(r["status"] for r in results) == ["file_gone", "file_sent"]
    assert [u["name"] for u in fake.uploads] == ["march.xlsx"]


def test_a_phone_started_run_that_writes_a_spreadsheet_sends_it_back_on_slack(platform, tmp_path):
    platform.config.phone_send_files = "yes"
    api = FakeSlackApi()
    ch = slack(api, chat_enabled=False)
    orch = _RunThatWrites(platform, tmp_path / "run-folder")
    poll = poller(platform, ch, name="slack", orchestrator=orch)
    msg = InboundMessage(sender_id=SLACK_USER, text="make the March spreadsheet", reply_to=SLACK_USER)

    async def go():
        out = await poll._handle("slack", ch, msg)
        await poll.drain()
        return out

    res = asyncio.run(go())

    assert res["status"] == "handled"
    assert api.methods() == ["conversations.open", "files.getUploadURLExternal",
                             "files.completeUploadExternal"]
    opened = api.forms[0][1]
    assert opened == {"users": SLACK_USER}
    assert api.forms[0][2] == {"Authorization": f"Bearer {SLACK_TOKEN}"}
    asked = api.forms[1][1]
    assert asked["filename"] == "march.xlsx" and asked["length"] == len(b"PK-sheet-from-a-run")
    assert len(api.uploads) == 1 and api.uploads[0]["url"] == api.upload_url
    assert api.uploads[0]["data"] == b"PK-sheet-from-a-run"
    done = api.forms[2][1]
    assert done["channel_id"] == SLACK_DM and "thread_ts" not in done
    assert json.loads(done["files"]) == [{"id": "F0TESTFILE", "title": "march.xlsx"}]


def test_slack_puts_a_file_in_the_thread_of_its_own_channel(tmp_path):
    api = FakeSlackApi()
    ch = slack(api)
    f = tmp_path / "notes.md"
    f.write_text("notes", encoding="utf-8")

    assert ch.send_file(str(f), chat_id=f"{SLACK_OWN}|1700000000.0001")["ok"]

    assert api.methods() == ["files.getUploadURLExternal", "files.completeUploadExternal"]
    assert api.forms[1][1]["channel_id"] == SLACK_OWN
    assert api.forms[1][1]["thread_ts"] == "1700000000.0001"


def test_slack_never_sends_the_bytes_anywhere_but_its_own_file_host(tmp_path):
    api = FakeSlackApi()
    api.upload_url = "https://elsewhere.example/upload"
    ch = slack(api)
    f = tmp_path / "notes.md"
    f.write_text("notes", encoding="utf-8")

    res = ch.send_file(str(f), chat_id=SLACK_USER)

    assert not res["ok"] and res["detail"] == "Slack did not give an upload address"
    assert api.uploads == []


def test_slack_missing_files_write_is_said_in_words(tmp_path):
    api = FakeSlackApi()

    def refuse(url, fields, file, headers):
        return {"ok": False, "error": "missing_scope"}

    ch = slack(api)
    ch._http_post_file = refuse
    f = tmp_path / "notes.md"
    f.write_text("notes", encoding="utf-8")
    res = ch.send_file(str(f), chat_id=f"{SLACK_OWN}|1.2")
    assert not res["ok"] and "files:write" in res["detail"]


# --------------------------------------------------------------------------- #
# email: one message, every file attached, 20 MB in all
# --------------------------------------------------------------------------- #
class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def starttls(self):
        pass

    def login(self, u, p):
        pass

    def send_message(self, msg):
        _FakeSMTP.sent.append(msg)


def test_email_sends_one_message_with_every_file_and_20_mb_in_all(tmp_path, monkeypatch):
    import smtplib

    _FakeSMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _FakeSMTP)
    ch = EmailChannel({"host": "smtp.example.test", "from_addr": "jarvis@example.test",
                       "to_addr": "me@example.test", "username": "jarvis@example.test"})
    ch.ALL_FILES_MAX_BYTES = 30
    a, b, c = tmp_path / "a.csv", tmp_path / "b.pdf", tmp_path / "c.txt"
    a.write_bytes(b"x" * 10)
    b.write_bytes(b"y" * 15)
    c.write_bytes(b"z" * 10)  # past 30 in all

    res = ch.send_files([str(a), str(b), str(c)], chat_id="me@example.test")

    assert [r["ok"] for r in res] == [True, True, False]
    assert "in all" in res[2]["detail"]
    assert len(_FakeSMTP.sent) == 1
    names = [p.get_filename() for p in _FakeSMTP.sent[0].iter_attachments()]
    assert names == ["a.csv", "b.pdf"]
    assert _FakeSMTP.sent[0]["To"] == "me@example.test"


# --------------------------------------------------------------------------- #
# the paired computer's answer reads the same files the same way
# --------------------------------------------------------------------------- #
def test_the_task_door_picks_through_the_one_reading(platform, tmp_path, monkeypatch):
    from iron_jarvis.agents import task_door

    seen = []
    real = reply_files.files_made

    def spy(engine, sid, caps):
        seen.append(caps)
        return real(engine, sid, caps)

    monkeypatch.setattr(reply_files, "files_made", spy)
    task_door._reply_files(platform.engine, "no-such-session")
    assert seen == [Caps(count=task_door.REPLY_FILES_MAX, each=task_door.REPLY_FILE_BYTES,
                         total=task_door.REPLY_FILES_TOTAL_BYTES)]
