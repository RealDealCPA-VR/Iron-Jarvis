"""10-a (build-out plan R2.3): a photo, a file or a voice note sent to Jarvis
on Telegram reaches it.

Before: ``TelegramChannel.poll`` dropped every message without text and sent
"I can't read photos, files, or voice messages here yet". Now a photo, a
document, a voice note, an audio file or a video becomes a file on the
message, with the caption as its words; the pipeline downloads it only after
the allowlist and the private-chat guard, keeps it in the chat's folder and
hands it to the chat turn. The notice is left for what is still not taken
(stickers, animations, contacts, locations).

The download URL holds the bot token, so it is in no log line, no event and
no stored or sent error.

Everything runs through the real ``InboundPoller`` and ``TelegramChannel``;
only Telegram itself (``getUpdates``, ``getFile``, the download) and the chat
turn are fakes.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from iron_jarvis.comm.base import InboundFile
from iron_jarvis.comm.channels import TelegramChannel
from tests._fakes.phone_files import (
    BOT_TOKEN,
    ME,
    FakeTelegram,
    chat_root,
    document,
    fake_turn,
    files_under,
    message,
    photo,
    poller,
    run,
    telegram,
    voice,
)

pytestmark = pytest.mark.usefixtures("no_network")


# --------------------------------------------------------------------------- #
# the poll: what becomes a file
# --------------------------------------------------------------------------- #
def test_photo_document_and_voice_become_files_with_the_caption_as_words():
    fake = FakeTelegram([
        photo(10, caption="add this to the March list"),
        document(11, name="lease.pdf"),
        voice(12),
        message(13, audio={"file_id": "A1", "file_name": "memo.m4a", "mime_type": "audio/mp4"}),
        message(14, video={"file_id": "M1", "mime_type": "video/mp4", "file_size": 99}),
    ])
    ch = telegram(fake)

    msgs, offset = ch.poll(0)

    assert offset == 15
    assert [m.text for m in msgs] == ["add this to the March list", "", "", "", ""]
    kinds = [[(f.kind, f.name) for f in m.files] for m in msgs]
    assert kinds == [
        [("photo", "photo.jpg")],
        [("document", "lease.pdf")],
        [("voice", "voice-note.ogg")],
        [("audio", "memo.m4a")],
        [("video", "video.mp4")],
    ]
    # The largest photo is taken (the 1280 px one), by its own handle.
    first = msgs[0].files[0]
    assert isinstance(first, InboundFile) and first.ref == "P1" and first.size == 12
    assert first.mime == "image/jpeg"
    assert fake.sent == []  # nothing is said at poll time any more


def test_an_album_is_one_message_with_its_caption():
    fake = FakeTelegram([
        photo(20, "P1", caption="receipts for March", media_group_id="g1"),
        photo(21, "P2", media_group_id="g1"),
        photo(22, "P3", media_group_id="g1"),
    ])
    msgs, offset = telegram(fake).poll(0)

    assert offset == 23
    assert len(msgs) == 1
    assert msgs[0].text == "receipts for March"
    assert [f.ref for f in msgs[0].files] == ["P1", "P2", "P3"]
    assert msgs[0].update_id == 22


def test_the_notice_is_left_for_what_is_still_not_taken():
    fake = FakeTelegram([
        message(30, sticker={"file_id": "S1"}),
        message(31, location={"latitude": 1.0, "longitude": 2.0}),
        message(32, animation={"file_id": "G1"}, document={"file_id": "G1", "mime_type": "video/mp4"}),
    ])
    msgs, offset = telegram(fake).poll(0)

    assert msgs == [] and offset == 33
    assert len(fake.sent) == 1  # once per sender per batch
    words = fake.sent[0]["text"]
    assert words == TelegramChannel.MEDIA_NOTICE
    assert "stickers" in words and "a photo, a file or a voice note" in words
    assert "can't read photos" not in words


# --------------------------------------------------------------------------- #
# the pipeline: fetched only for the allowed sender, in their own chat
# --------------------------------------------------------------------------- #
def test_a_photo_with_words_is_kept_and_reaches_the_chat_turn(platform, tmp_path, monkeypatch):
    from iron_jarvis.core import userdirs

    monkeypatch.setattr(userdirs, "documents_dir", lambda: tmp_path / "Documents")
    root = chat_root(platform, tmp_path)
    fake = FakeTelegram([photo(40, caption="add this to the March list")], files={"P1": b"\xff\xd8 jpeg"})
    turn = fake_turn()
    poll = poller(platform, telegram(fake), turn=turn)

    results = run(poll)

    assert fake.get_files == ["P1"] and len(fake.downloads) == 1
    kept = files_under(root)
    assert len(kept) == 1 and kept[0].name == "photo.jpg"
    assert kept[0].read_bytes() == b"\xff\xd8 jpeg"
    assert kept[0].parent.name.endswith(" From your phone")
    # The saved line comes first, naming the file and the folder from the
    # chat files folder down, never the whole path.
    where = str(Path("Documents") / "Iron Jarvis" / kept[0].parent.name)
    assert fake.texts[0] == f"Iron Jarvis: Saved photo.jpg in {where}."
    assert str(tmp_path) not in fake.texts[0]
    assert "Added to the March list." in fake.texts[1]
    # The turn got the file through the attachment seam, in the chat's folder.
    body = turn.calls[0]
    assert body.attachments == [str(kept[0])]
    assert body.workspace_dir == str(kept[0].parent)
    assert body.messages[-1].content == "add this to the March list"
    assert results[0]["status"] == "chat"


def test_a_strangers_photo_is_never_fetched(platform, tmp_path):
    root = chat_root(platform, tmp_path)
    fake = FakeTelegram([photo(50, sender=999)], files={"P1": b"x"})
    poll = poller(platform, telegram(fake))

    results = run(poll)

    assert fake.get_files == [] and fake.downloads == []
    assert files_under(root) == []
    assert fake.sent == []
    assert results[0]["status"] == "unauthorized"


def test_a_photo_in_a_group_is_never_fetched(platform, tmp_path):
    root = chat_root(platform, tmp_path)
    fake = FakeTelegram([photo(51, chat=-4242)], files={"P1": b"x"})
    run(poller(platform, telegram(fake)))

    assert fake.get_files == [] and fake.downloads == []
    assert files_under(root) == [] and fake.sent == []


def test_a_file_over_the_cap_is_refused_before_the_download(platform, tmp_path):
    root = chat_root(platform, tmp_path)
    platform.config.phone_file_max_mb = 1
    big = 3 * 1024 * 1024
    fake = FakeTelegram([document(60, "D1", name="big.pdf", size=big)], files={"D1": b"x"})
    poll = poller(platform, telegram(fake))

    results = run(poll)

    assert fake.get_files == [] and fake.downloads == []  # refused on the size Telegram gave
    assert files_under(root) == []
    assert "big.pdf" in fake.texts[0] and "3 MB" in fake.texts[0] and "1 MB" in fake.texts[0]
    assert results[0]["status"] == "files_not_taken"


def test_a_file_that_turns_out_too_big_is_stopped_at_the_cap(platform, tmp_path):
    root = chat_root(platform, tmp_path)
    platform.config.phone_file_max_mb = 1
    fake = FakeTelegram([document(61, "D2", name="sneaky.pdf", size=None, caption="file it")],
                        files={"D2": b"x" * (2 * 1024 * 1024)})
    turn = fake_turn()
    run(poller(platform, telegram(fake), turn=turn))

    assert files_under(root) == []
    assert "sneaky.pdf" in fake.texts[0] and "1 MB" in fake.texts[0]
    # The words still run, told that the file was not read.
    assert len(turn.calls) == 1 and turn.calls[0].attachments == []


def test_a_kind_that_is_not_taken_is_named_not_downloaded(platform, tmp_path):
    root = chat_root(platform, tmp_path)
    fake = FakeTelegram([document(62, "D3", name="setup.exe", mime="application/x-msdownload")],
                        files={"D3": b"MZ"})
    run(poller(platform, telegram(fake)))

    assert fake.get_files == [] and files_under(root) == []
    assert "setup.exe" in fake.texts[0] and "isn't a kind of file" in fake.texts[0]


def test_the_bot_token_is_in_no_log_event_reply_or_saved_line(platform, tmp_path, caplog):
    chat_root(platform, tmp_path)
    fake = FakeTelegram([document(70, "D4", name="notes.txt", mime="text/plain", caption="read it")],
                        files={"D4": b"hello"})
    # The download fails with an error that carries the URL, as httpx's do.
    fake.download_error = RuntimeError(
        f"Client error '404 Not Found' for url 'https://api.telegram.org/file/bot{BOT_TOKEN}/documents/D4.bin'"
    )
    turn = fake_turn()
    poll = poller(platform, telegram(fake), turn=turn)

    with caplog.at_level(logging.DEBUG):
        run(poll)

    assert fake.downloads and BOT_TOKEN in fake.downloads[0]  # it really was in the URL
    assert BOT_TOKEN not in caplog.text
    for rec in caplog.records:
        assert BOT_TOKEN not in str(rec.exc_info or "")
    for event in platform.event_bus.history:
        assert BOT_TOKEN not in str(event.payload)
    assert all(BOT_TOKEN not in t for t in fake.texts)
    assert "notes.txt" in fake.texts[0] and "couldn't take" in fake.texts[0]
    tid = poll.thread_store.resolve("tg", str(ME), "").id
    assert all(BOT_TOKEN not in m["content"] for m in poll.thread_store.history_body(tid))


def test_a_kept_file_is_recorded_as_an_event_without_its_name(platform, tmp_path):
    chat_root(platform, tmp_path)
    fake = FakeTelegram([photo(80, caption="look")], files={"P1": b"\xff\xd8 j"})
    run(poller(platform, telegram(fake)))

    got = [e for e in platform.event_bus.history if e.type == "comm.file_received"]
    assert len(got) == 1
    assert got[0].payload == {"channel": "tg", "kind": "photo", "bytes": 4}


def test_a_photo_too_large_at_full_size_is_taken_at_a_smaller_size(platform, tmp_path):
    root = chat_root(platform, tmp_path)
    platform.config.phone_file_max_mb = 1
    fake = FakeTelegram([photo(90, "P9", caption="the receipt", size=3 * 1024 * 1024)],
                        files={"P9": b"x" * (3 * 1024 * 1024), "P9-small": b"tiny"})
    run(poller(platform, telegram(fake)))

    assert fake.get_files == ["P9-small"]
    kept = files_under(root)
    assert len(kept) == 1 and kept[0].read_bytes() == b"tiny"
