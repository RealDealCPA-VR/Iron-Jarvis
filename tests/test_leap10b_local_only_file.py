"""10-b (build-out plan R2.4, decision D-10.3): a finished file in a Local
only chat.

Nothing leaves the PC until the user says Send, every time, whatever the
setting says (only "Keep them on this PC" stops the question). The question
names how many files, their kind and size, never a file's name: not in the
words, not on a button, not in the prompt kept for it. Send goes through the
same upload; Keep it on the PC sends nothing and changes no setting. A
phone-started run's own line for the files it wrote counts them there too.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.comm import files_back
from iron_jarvis.comm.prompts import FILE_SEND_KIND
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import AgentRun, Session, SessionStatus, ToolInvocation, UndoJournal
from tests._fakes.phone_back import FakeTelegramBack, making_turn, press, set_local_only, telegram_back
from tests._fakes.phone_files import ME, message, poller, run

pytestmark = pytest.mark.usefixtures("no_network")

SECRET_NAME = "family-budget.xlsx"


def _local_only_chat(platform, fake, **over):
    sheet = Path(platform.config.home) / "uploads" / SECRET_NAME
    poll = poller(platform, telegram_back(fake), turn=making_turn(sheet, data=b"x" * 2048, **over))
    tid = poll.thread_store.resolve("tg", str(ME), "Val").id
    set_local_only(platform, tid)
    return poll, tid


def _open_prompt(platform):
    from sqlmodel import select

    from iron_jarvis.comm.models import PendingPromptRecord as P

    with session_scope(platform.engine) as db:
        return db.exec(select(P).where(P.kind == FILE_SEND_KIND, P.status == "open")).first()


def _everything_sent(fake) -> str:
    """Every word that crossed to Telegram: texts, buttons, uploads' names."""
    words = list(fake.texts)
    for kb in fake.keyboards:
        words.extend(b["text"] for b in kb)
    words.extend(u["name"] for u in fake.uploads)
    return "\n".join(words)


@pytest.mark.parametrize("setting", ["ask", "yes"])
def test_nothing_leaves_until_send_and_the_ask_names_no_file(platform, setting):
    platform.config.phone_send_files = setting
    fake = FakeTelegramBack([message(1, text="turn the scan into a spreadsheet")])
    poll, tid = _local_only_chat(platform, fake)

    res = run(poll)

    assert res[0]["files"] == "asked"
    assert fake.uploads == []
    question = fake.sent[-1]["text"]
    # The buttons, and the typed answer with them (it always works).
    assert question == ("Iron Jarvis: This chat is Local only, so the 1 spreadsheet (2 KB) it made "
                        "was not sent. Send it through Telegram? Reply 1 to send or 2 to keep it "
                        "on the PC.")
    assert [b["text"] for b in fake.keyboards[-1]] == ["Send", "Keep it on the PC"]
    assert SECRET_NAME not in _everything_sent(fake)
    rec = _open_prompt(platform)
    assert SECRET_NAME not in rec.question
    paths, local_only, left = files_back.read_ref(rec.ref_id)
    assert local_only is True and left == [] and Path(paths[0]).name == SECRET_NAME

    # Send, by the button: the file goes now, through the same upload.
    fake.updates = [press(2, f"ij:p:{rec.id}:1", sender=ME)]
    pressed = run(poll)

    assert pressed[0]["pressed"] is True and pressed[0]["status"] == "file_sent"
    assert [u["name"] for u in fake.uploads] == [SECRET_NAME]
    assert fake.edits() == ["Sent"]
    # A Local only chat's answer changes no setting: it asks every time.
    assert platform.config.phone_send_files == setting
    assert platform.config.phone_send_files_to == []
    assert platform.config.phone_send_files_kept == []


def test_keep_it_on_the_pc_sends_nothing_and_changes_no_setting(platform):
    fake = FakeTelegramBack([message(1, text="make the sheet")])
    poll, _tid = _local_only_chat(platform, fake)
    run(poll)
    rec = _open_prompt(platform)

    fake.updates = [press(2, f"ij:p:{rec.id}:2", sender=ME)]
    res = run(poll)

    assert res[0]["status"] == "file_kept"
    assert fake.uploads == []
    assert fake.edits() == ["Kept on the PC"]
    assert fake.texts[-1] == "Iron Jarvis: OK: it stays on this PC."
    assert platform.config.phone_send_files == "ask"
    assert platform.config.phone_send_files_kept == []  # not kept for the destination either
    # The next file in this chat is asked about again.
    fake.updates = [message(3, text="and another")]
    again = run(poll)
    assert again[0]["files"] == "asked"


def test_keep_them_on_this_pc_in_settings_means_no_question_either(platform):
    platform.config.phone_send_files = "no"
    fake = FakeTelegramBack([message(1, text="make the sheet")])
    poll, _tid = _local_only_chat(platform, fake)

    res = run(poll)

    assert res[0]["files"] == "kept"
    assert fake.uploads == [] and len(fake.sent) == 1  # the answer's words only


def test_a_doubt_about_local_only_keeps_the_files_here(platform, monkeypatch):
    from iron_jarvis.core import db as core_db

    def broken(*a, **k):
        raise RuntimeError("the database is away")

    monkeypatch.setattr(core_db, "session_scope", broken)

    assert files_back.work_is_local_only(platform.engine, "thread_x") is True
    assert files_back.work_is_local_only(platform.engine, "", "session_y") is True


# --------------------------------------------------------------------------- #
# a phone-started run in Local only work: its own files line counts too
# --------------------------------------------------------------------------- #
class _LocalOnlyRun:
    """A run created Local only (its own switch) that writes the file in its
    folder and leaves the ledger rows a real write_file leaves. A run that
    did not finish carries the orchestrator's named kept line, as a real
    failed run's summary does."""

    def __init__(self, platform, ws: Path, status: SessionStatus) -> None:
        self.platform, self.ws, self.status = platform, ws, status
        self.sid = "session_10b_local_only_run"

    async def create_session(self, task, agent_type, **kw):
        self.ws.mkdir(parents=True, exist_ok=True)
        with session_scope(self.platform.engine) as db:
            db.add(Session(id=self.sid, task=task, status=SessionStatus.ACTIVE,
                           workspace_path=str(self.ws), origin=kw.get("origin"),
                           options_json=json.dumps({"local_only": True})))
            db.commit()
        return SimpleNamespace(id=self.sid, provider="")

    async def run_session(self, sid):
        path = self.ws / SECRET_NAME
        path.write_bytes(b"x" * 2048)
        summary = ("Made the spreadsheet." if self.status is SessionStatus.COMPLETED
                   else f"The run failed: the model went away. The file it wrote is kept: {SECRET_NAME}.")
        with session_scope(self.platform.engine) as db:
            db.add(AgentRun(id="run_10b_lo", session_id=sid))
            db.add(ToolInvocation(id="tool_10b_lo", session_id=sid, agent_run_id="run_10b_lo",
                                  tool="write_file", ok=True, reversibility="reversible"))
            db.add(UndoJournal(action_id="tool_10b_lo", session_id=sid, agent_run_id="run_10b_lo",
                               tool="write_file", kind="file_delete",
                               pre_inline=json.dumps({"path": str(path)})))
            row = db.get(Session, sid)
            row.status = self.status
            row.summary = summary
            db.add(row)
            db.commit()
        return SimpleNamespace(summary=summary, status=self.status)


@pytest.mark.parametrize("status, line", [
    (SessionStatus.COMPLETED, "It wrote 1 file."),
    (SessionStatus.FAILED, "The file it wrote is kept on this PC."),
])
def test_a_local_only_runs_files_line_counts_and_names_none(platform, tmp_path, status, line):
    # 10-b review: the run's summary used to name the file it wrote ("Files
    # it wrote: <name>.") before the files question that names none.
    fake = FakeTelegramBack([message(1, text="make the spreadsheet")])
    orch = _LocalOnlyRun(platform, tmp_path / "run-folder", status)

    run(poller(platform, telegram_back(fake, chat_enabled=False), orchestrator=orch))

    assert fake.uploads == []
    assert any(t.endswith(line) for t in fake.texts), fake.texts
    assert SECRET_NAME not in _everything_sent(fake)
    if status is SessionStatus.COMPLETED:
        assert fake.texts[-1].startswith("Iron Jarvis: This chat is Local only, so the 1 spreadsheet (2 KB)")
