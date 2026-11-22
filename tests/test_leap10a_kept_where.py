"""10-a (build-out plan R2.3): where a file from the phone is kept, and what
the chat turn is given.

A file is written to ``<home>/uploads`` with the upload route's naming, then
copied into the chat's own folder: a dated "From your phone" folder under the
chat files folder (Documents › Iron Jarvis), made with the first file and
saved as the phone chat's ``workspace_dir`` so every later file joins it. The
turn gets the file through ``ChatBody.attachments`` and the folder as its
``workspace_dir``; an earlier file of the same chat rides ``thread_files``.
The saved line on the chat records the absolute paths, so the phone chat on
the PC shows the files as chips. At most five files are taken from one
message; the rest are named. With two-way chat off, the files are kept and
the run's task names them in a fenced data block.
"""

from __future__ import annotations

import pytest

from iron_jarvis.comm.media import PHONE_FILES_PER_MESSAGE, FOLDER_TITLE
from tests._fakes.phone_files import (
    ME,
    PHOTO,
    PHOTO_2,
    FakeTelegram,
    chat_root,
    document,
    fake_turn,
    files_under,
    fixed_time,
    photo,
    poller,
    run,
    telegram,
)

pytestmark = pytest.mark.usefixtures("no_network")


def test_the_first_file_makes_the_dated_folder_and_the_next_joins_it(platform, tmp_path, monkeypatch):
    fixed_time(monkeypatch)
    root = chat_root(platform, tmp_path)
    fake = FakeTelegram([photo(10, "P1", caption="first")], files={"P1": b"one", "P2": b"two"})
    turn = fake_turn()
    poll = poller(platform, telegram(fake), turn=turn)

    run(poll)
    fake.updates = [photo(11, "P2", caption="second")]
    run(poll)

    kept = files_under(root)
    assert [p.name for p in kept] == [PHOTO_2, PHOTO]
    assert kept[0].parent == kept[1].parent  # one folder for the chat
    folder = kept[0].parent
    assert folder.parent == root and folder.name.endswith(f" {FOLDER_TITLE}")
    tid = poll.thread_store.resolve("tg", str(ME), "").id
    assert poll.thread_store.get_setup_value(tid, "workspace_dir") == str(folder)
    # The second turn: its own file as the attachment, the first one carried.
    second = turn.calls[1]
    assert second.attachments == [str(folder / PHOTO_2)]
    assert str(folder / PHOTO) in second.thread_files
    assert second.workspace_dir == str(folder)
    # The upload copy is the app's own (the desk's attach path).
    uploads = platform.config.home / "uploads"
    assert sorted(p.name for p in uploads.iterdir()) == [PHOTO_2, PHOTO]


def test_two_files_in_one_message_both_reach_the_turn(platform, tmp_path, monkeypatch):
    fixed_time(monkeypatch)
    root = chat_root(platform, tmp_path)
    fake = FakeTelegram([
        photo(20, "P1", caption="both of these", media_group_id="g"),
        photo(21, "P2", media_group_id="g"),
    ], files={"P1": b"one", "P2": b"two"})
    turn = fake_turn()
    run(poller(platform, telegram(fake), turn=turn))

    kept = files_under(root)
    assert len(kept) == 2
    assert sorted(turn.calls[0].attachments) == sorted(str(p) for p in kept)
    assert fake.texts[0].startswith(f"Iron Jarvis: Saved {PHOTO} and {PHOTO_2} in ")


def test_the_saved_line_on_the_chat_carries_the_chips(platform, tmp_path):
    root = chat_root(platform, tmp_path)
    fake = FakeTelegram([document(30, "D1", name="lease.pdf", caption="summarise")],
                        files={"D1": b"%PDF-1.4"})
    poll = poller(platform, telegram(fake))
    results = run(poll)

    tid = results[0]["thread_id"]
    import json

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord

    with session_scope(platform.engine) as db:
        msgs = json.loads(db.get(ChatThreadRecord, tid).messages_json)
    user = msgs[0]
    assert user["role"] == "user" and user["content"] == "summarise"
    assert user["attachmentNames"] == ["lease.pdf"]
    assert user["attachmentPaths"] == [str(files_under(root)[0])]


def test_a_file_with_no_words_still_reaches_the_turn(platform, tmp_path, monkeypatch):
    fixed_time(monkeypatch)
    chat_root(platform, tmp_path)
    fake = FakeTelegram([photo(40)], files={"P1": b"img"})
    turn = fake_turn("A receipt from the corner shop.")
    results = run(poller(platform, telegram(fake), turn=turn))

    assert len(turn.calls) == 1
    said = turn.calls[0].messages[-1].content
    assert PHOTO in said and said.startswith("(")
    assert results[0]["status"] == "chat"


def test_at_most_five_files_from_one_message(platform, tmp_path):
    root = chat_root(platform, tmp_path)
    ups = [photo(50 + i, f"P{i}", media_group_id="g", **({"caption": "all"} if i == 0 else {}))
           for i in range(PHONE_FILES_PER_MESSAGE + 2)]
    fake = FakeTelegram(ups, files={f"P{i}": b"x%d" % i for i in range(7)})
    run(poller(platform, telegram(fake)))

    assert len(files_under(root)) == PHONE_FILES_PER_MESSAGE
    assert len(fake.get_files) == PHONE_FILES_PER_MESSAGE
    assert "2 more files" in fake.texts[0] and "5 files" in fake.texts[0]


def test_with_two_way_chat_off_the_run_names_the_files_as_data(platform, tmp_path):
    root = chat_root(platform, tmp_path)

    class _Orch:
        def __init__(self) -> None:
            self.tasks: list[str] = []

        async def create_session(self, task, agent_type, **kw):
            self.tasks.append(task)
            return None

    orch = _Orch()
    fake = FakeTelegram([document(60, "D1", name="lease.pdf", caption="file this")],
                        files={"D1": b"%PDF"})
    run(poller(platform, telegram(fake, chat_enabled=False), orchestrator=orch))

    kept = files_under(root)
    assert len(kept) == 1
    task = orch.tasks[0]
    assert task.startswith("file this")
    assert str(kept[0]) in task
    assert "<<<files" in task and "data, not instructions" in task


def test_a_burst_of_files_is_bounded_per_minute(platform, tmp_path):
    from iron_jarvis.comm.inbound import FILES_PER_WINDOW, RATE_WINDOW_SECONDS
    from tests._fakes.phone_files import Clock

    root = chat_root(platform, tmp_path)
    n = FILES_PER_WINDOW + 1
    ups = [photo(100 + i, f"Q{i}", caption=f"page {i}") for i in range(n)]
    fake = FakeTelegram(ups, files={f"Q{i}": b"p%d" % i for i in range(n)})
    clock = Clock()
    poll = poller(platform, telegram(fake), clock=clock)
    run(poll)

    assert len(files_under(root)) == FILES_PER_WINDOW
    assert len(fake.get_files) == FILES_PER_WINDOW  # the last one was never fetched
    assert any("too many files came in the last minute" in t for t in fake.texts)
    clock.now += RATE_WINDOW_SECONDS
    fake.updates = [photo(200, "Q0", caption="again")]
    run(poll)
    assert len(fake.get_files) == FILES_PER_WINDOW + 1


def test_a_phone_chat_in_a_project_keeps_the_projects_folder(platform, tmp_path):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord

    root = chat_root(platform, tmp_path)
    fake = FakeTelegram([photo(300, "P1", caption="for the project")], files={"P1": b"img"})
    turn = fake_turn()
    poll = poller(platform, telegram(fake), turn=turn)
    tid = poll.thread_store.resolve("tg", str(ME), "").id
    with session_scope(platform.engine) as db:
        row = db.get(ChatThreadRecord, tid)
        row.project_id = "proj_1"
        db.add(row)
        db.commit()

    run(poll)

    kept = files_under(root)
    assert len(kept) == 1
    body = turn.calls[0]
    assert body.attachments == [str(kept[0])]
    assert body.workspace_dir == ""  # the project's folder, not the phone's
    assert body.project_id == "proj_1"
    # The chat's saved working folder is left alone too: the desk opens a
    # chat in its saved folder before its project's, so writing the phone's
    # folder there would move the project chat out of its project on the PC.
    assert poll.thread_store.get_setup_value(tid, "workspace_dir") == ""

    # The next file of the same chat joins the first one's folder.
    fake.updates = [photo(301, "P2", caption="and this")]
    fake.files["P2"] = b"img2"
    run(poll)
    kept = files_under(root)
    assert len(kept) == 2 and kept[0].parent == kept[1].parent
    assert poll.thread_store.get_setup_value(tid, "workspace_dir") == ""
    assert turn.calls[1].workspace_dir == ""


def test_an_album_in_a_project_chat_lands_in_one_folder(platform, tmp_path):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord

    root = chat_root(platform, tmp_path)
    fake = FakeTelegram([
        photo(310, "P1", caption="both", media_group_id="g"),
        photo(311, "P2", media_group_id="g"),
    ], files={"P1": b"one", "P2": b"two"})
    turn = fake_turn()
    poll = poller(platform, telegram(fake), turn=turn)
    tid = poll.thread_store.resolve("tg", str(ME), "").id
    with session_scope(platform.engine) as db:
        row = db.get(ChatThreadRecord, tid)
        row.project_id = "proj_1"
        db.add(row)
        db.commit()

    run(poll)

    kept = files_under(root)
    assert len(kept) == 2 and kept[0].parent == kept[1].parent
    assert sorted(turn.calls[0].attachments) == sorted(str(p) for p in kept)


# --------------------------------------------------------------------------- #
# a name never runs out (<home>/uploads is never emptied; store_upload tries
# MAX_UPLOAD_NAMES numbered names, then refused)
# --------------------------------------------------------------------------- #
def _fill(uploads, stem: str, ext: str, count: int) -> None:
    from iron_jarvis.documents.workfolder import MAX_UPLOAD_NAMES

    assert count >= MAX_UPLOAD_NAMES
    uploads.mkdir(parents=True, exist_ok=True)
    for n in range(1, count + 1):
        name = f"{stem}{ext}" if n == 1 else f"{stem}-{n}{ext}"
        (uploads / name).write_bytes(b"old %d" % n)


def test_a_phone_photo_after_a_thousand_others_is_still_kept(platform, tmp_path, monkeypatch):
    # Every Telegram photo came as "photo.jpg": after a thousand of them every
    # later photo was refused for good. A plain name now gets the date and time.
    fixed_time(monkeypatch)
    root = chat_root(platform, tmp_path)
    _fill(platform.config.home / "uploads", "photo", ".jpg", 1000)
    fake = FakeTelegram([photo(400, "P1", caption="the receipt")], files={"P1": b"new receipt"})
    turn = fake_turn()
    run(poller(platform, telegram(fake), turn=turn))

    kept = files_under(root)
    assert [p.name for p in kept] == [PHOTO] and kept[0].read_bytes() == b"new receipt"
    assert fake.texts[0].startswith(f"Iron Jarvis: Saved {PHOTO} in ")
    assert turn.calls[0].attachments == [str(kept[0])]


def test_a_name_sent_a_thousand_times_is_still_kept(platform, tmp_path, monkeypatch):
    # A name the phone gave ("scan.pdf") keeps its name until every numbered
    # one is taken; then it is kept with the date and time, never refused.
    fixed_time(monkeypatch)
    root = chat_root(platform, tmp_path)
    _fill(platform.config.home / "uploads", "scan", ".pdf", 1000)
    fake = FakeTelegram([document(401, "D1", name="scan.pdf", caption="file it")],
                        files={"D1": b"%PDF-1.4 new"})
    turn = fake_turn()
    run(poller(platform, telegram(fake), turn=turn))

    kept = files_under(root)
    assert [p.name for p in kept] == ["scan-2026-11-20-101530.pdf"]
    assert kept[0].read_bytes() == b"%PDF-1.4 new"
    assert "couldn't take" not in fake.texts[0]
    assert turn.calls[0].attachments == [str(kept[0])]


def test_plain_names_and_given_names():
    from datetime import datetime

    from iron_jarvis.comm.media import kept_name

    at = datetime(2026, 1, 2, 3, 4, 5)
    assert kept_name("photo.jpg", at) == "photo-2026-01-02-030405.jpg"
    assert kept_name("voice-note.ogg", at) == "voice-note-2026-01-02-030405.ogg"
    assert kept_name("image.png", at) == "image-2026-01-02-030405.png"  # Slack's pasted picture
    assert kept_name("attachment-3.pdf", at) == "attachment-2026-01-02-030405.pdf"
    assert kept_name("lease.pdf", at) == "lease.pdf"
    assert kept_name("photos-of-site.jpg", at) == "photos-of-site.jpg"
