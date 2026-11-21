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
    FakeTelegram,
    chat_root,
    document,
    fake_turn,
    files_under,
    photo,
    poller,
    run,
    telegram,
)

pytestmark = pytest.mark.usefixtures("no_network")


def test_the_first_file_makes_the_dated_folder_and_the_next_joins_it(platform, tmp_path):
    root = chat_root(platform, tmp_path)
    fake = FakeTelegram([photo(10, "P1", caption="first")], files={"P1": b"one", "P2": b"two"})
    turn = fake_turn()
    poll = poller(platform, telegram(fake), turn=turn)

    run(poll)
    fake.updates = [photo(11, "P2", caption="second")]
    run(poll)

    kept = files_under(root)
    assert [p.name for p in kept] == ["photo-2.jpg", "photo.jpg"]
    assert kept[0].parent == kept[1].parent  # one folder for the chat
    folder = kept[0].parent
    assert folder.parent == root and folder.name.endswith(f" {FOLDER_TITLE}")
    tid = poll.thread_store.resolve("tg", str(ME), "").id
    assert poll.thread_store.get_setup_value(tid, "workspace_dir") == str(folder)
    # The second turn: its own file as the attachment, the first one carried.
    second = turn.calls[1]
    assert second.attachments == [str(folder / "photo-2.jpg")]
    assert str(folder / "photo.jpg") in second.thread_files
    assert second.workspace_dir == str(folder)
    # The upload copy is the app's own (the desk's attach path).
    uploads = platform.config.home / "uploads"
    assert sorted(p.name for p in uploads.iterdir()) == ["photo-2.jpg", "photo.jpg"]


def test_two_files_in_one_message_both_reach_the_turn(platform, tmp_path):
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
    assert fake.texts[0].startswith("Iron Jarvis: Saved photo.jpg and photo-2.jpg in ")


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


def test_a_file_with_no_words_still_reaches_the_turn(platform, tmp_path):
    chat_root(platform, tmp_path)
    fake = FakeTelegram([photo(40)], files={"P1": b"img"})
    turn = fake_turn("A receipt from the corner shop.")
    results = run(poller(platform, telegram(fake), turn=turn))

    assert len(turn.calls) == 1
    said = turn.calls[0].messages[-1].content
    assert "photo.jpg" in said and said.startswith("(")
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
