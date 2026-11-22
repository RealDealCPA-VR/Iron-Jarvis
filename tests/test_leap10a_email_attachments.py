"""10-a (build-out plan R2.3): an email's attachments reach Jarvis.

R0.3 counted them and said they could not be used. Now an attachment from a
sender the mail provider vouched for AND who is on the allowlist is kept in
the chat's folder and handed to the chat turn. An unproven sender's or a
stranger's attachment is never decoded or kept. A signature, the key beside
it and a picture inside the email's text are still not files.

Only IMAP, SMTP and the chat turn are fakes.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage
from typing import Any

import pytest

from iron_jarvis.comm.channels import EmailChannel, _email_attachments
from tests._fakes.phone_files import chat_root, fake_turn, files_under, poller

pytestmark = pytest.mark.usefixtures("no_network")


def _mail(*, subject: str = "Invoice", body: str = "Please file this.",
          sender: str = "Alice <alice@example.com>", proven: bool = True,
          pdfs: int = 1) -> EmailMessage:
    msg = EmailMessage()
    if proven:
        domain = sender.rsplit("@", 1)[-1].rstrip(">")
        msg["Authentication-Results"] = f"mx.example.com; dkim=pass header.d={domain}"
    msg["From"] = sender
    msg["To"] = "jarvis@example.com"
    msg["Subject"] = subject
    msg.set_content(body)
    for i in range(pdfs):
        msg.add_attachment(b"%PDF-1.4 invoice " + bytes([48 + i]), maintype="application",
                           subtype="pdf", filename=f"invoice{i}.pdf")
    return msg


class _IMAP:
    def __init__(self, mails: list[tuple[int, bytes]]) -> None:
        self.mails = mails

    def login(self, user, password):  # noqa: ANN001
        return "OK", [b"ok"]

    def select(self, mailbox="INBOX"):  # noqa: ANN001
        return "OK", [b"1"]

    def uid(self, command, *args):  # noqa: ANN001
        if command.upper() == "SEARCH":
            return "OK", [b" ".join(str(u).encode() for u, _ in self.mails)]
        for u, raw in self.mails:
            if u == int(args[0]):
                return "OK", [(f"{u} (RFC822".encode(), raw), b")"]
        return "OK", [None]

    def logout(self):
        return "BYE", [b"bye"]


class _SMTP:
    sent: list[str] = []

    def __init__(self, *a: Any, **k: Any) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def starttls(self) -> None:
        pass

    def login(self, *a: Any) -> None:
        pass

    def send_message(self, msg: EmailMessage) -> None:
        _SMTP.sent.append(msg.get_content().strip())


CFG = {"host": "smtp.example.com", "imap_host": "imap.example.com", "username": "jarvis@example.com",
       "password_secret": "imap_pw", "to_addr": "alice@example.com", "inbound_enabled": True,
       "chat_enabled": True, "allowed_senders": ["alice@example.com"]}


def _rig(platform, tmp_path, monkeypatch, mails: list[EmailMessage], *, voice_deps: Any = None):
    root = chat_root(platform, tmp_path)
    fake = _IMAP([(10 + i, m.as_bytes()) for i, m in enumerate(mails)])
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda host, port: fake)
    _SMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    ch = EmailChannel(dict(CFG), secret_resolver=lambda n: "pw-test" if n == "imap_pw" else None)
    turn = fake_turn("Filed under March.")
    poll = poller(platform, ch, name="mail", turn=turn)
    poll.voice_deps = voice_deps

    async def go():
        res = await poll.poll_once()
        await poll.drain()
        return res

    return root, asyncio.run(go()), turn


def test_a_proven_senders_attachment_is_kept_and_read(platform, tmp_path, monkeypatch):
    root, results, turn = _rig(platform, tmp_path, monkeypatch, [_mail()])

    kept = files_under(root)
    assert [p.name for p in kept] == ["invoice0.pdf"]
    assert kept[0].read_bytes() == b"%PDF-1.4 invoice 0"
    assert turn.calls[0].attachments == [str(kept[0])]
    assert "Please file this." in turn.calls[0].messages[-1].content
    assert len(_SMTP.sent) == 2
    assert _SMTP.sent[0].startswith("Iron Jarvis: Saved invoice0.pdf in ")
    assert "Filed under March." in _SMTP.sent[1]


def test_an_unproven_senders_attachment_is_never_kept(platform, tmp_path, monkeypatch):
    root, results, turn = _rig(platform, tmp_path, monkeypatch, [_mail(proven=False)])

    assert files_under(root) == []
    assert not (platform.config.home / "uploads").exists() or not any(
        (platform.config.home / "uploads").iterdir())
    assert results[0]["status"] == "unauthorized"
    assert _SMTP.sent == [] and turn.calls == []


def test_a_strangers_attachment_is_never_kept(platform, tmp_path, monkeypatch):
    stranger = _mail(sender="Mallory <mallory@example.net>")
    root, results, turn = _rig(platform, tmp_path, monkeypatch, [stranger])

    assert files_under(root) == []
    assert _SMTP.sent == [] and turn.calls == []


def test_an_unproven_senders_parts_are_not_even_listed():
    from email import message_from_bytes

    em = message_from_bytes(_mail(proven=False).as_bytes())
    files = _email_attachments(em)
    assert [(f.kind, f.name, f.mime) for f in files] == [("document", "invoice0.pdf", "application/pdf")]
    # ...but the poll only builds them for a proven, allowed sender:
    ch = EmailChannel(dict(CFG))
    assert ch._files_for(em, "alice@example.com", verified=False) == []
    assert ch._files_for(em, "mallory@example.net", verified=True) == []
    assert len(ch._files_for(em, "alice@example.com", verified=True)) == 1


def test_the_size_before_the_decode_is_the_files_own_size():
    # The size is estimated from the encoded text, so a file over the cap is
    # refused before it is decoded. The line breaks of the encoding are not
    # the file: counting them made a file just under the cap look over it.
    from email import message_from_bytes

    data = bytes(range(256)) * 4000  # 1,024,000 bytes, base64 in 76-character lines
    msg = _mail(pdfs=0)
    msg.add_attachment(data, maintype="application", subtype="pdf", filename="big.pdf")
    files = _email_attachments(message_from_bytes(msg.as_bytes()))
    assert [f.name for f in files] == ["big.pdf"]
    assert files[0].size == len(data)
    # The same file is taken under a cap of its own size, and refused under
    # one byte less, both before anything is decoded.
    ch = EmailChannel(dict(CFG))
    assert ch.fetch_file(files[0], len(data)) == data
    from iron_jarvis.comm.base import FileTooLarge

    with pytest.raises(FileTooLarge):
        ch.fetch_file(files[0], len(data) - 4)


def test_an_attachment_over_the_cap_is_never_decoded():
    # The refusal comes from the size read off the encoded text, BEFORE the
    # decode: the part's decoded payload is never asked for.
    from email import message_from_bytes

    from iron_jarvis.comm.base import FileTooLarge

    data = bytes(range(256)) * 400
    msg = _mail(pdfs=0)
    msg.add_attachment(data, maintype="application", subtype="pdf", filename="big.pdf")
    f = _email_attachments(message_from_bytes(msg.as_bytes()))[0]
    decoded: list[bool] = []
    real = f.handle.get_payload

    def payload(*args, **kw):
        if kw.get("decode"):
            decoded.append(True)
        return real(*args, **kw)

    f.handle.get_payload = payload
    ch = EmailChannel(dict(CFG))
    with pytest.raises(FileTooLarge):
        ch.fetch_file(f, len(data) - 100)
    assert decoded == []
    assert ch.fetch_file(f, len(data)) == data and decoded == [True]


def test_a_forwarded_email_is_kept_as_a_file(platform, tmp_path, monkeypatch):
    # An e-mail attached to an e-mail is kept whole as .eml, a kind taken
    # (it was refused as "that isn't a kind of file taken").
    from tests._fakes.phone_files import fixed_time

    fixed_time(monkeypatch)
    inner = EmailMessage()
    inner["From"] = "Bob <bob@example.org>"
    inner["Subject"] = "Quote"
    inner.set_content("The quote is 400.")
    outer = _mail(pdfs=0, body="see the forwarded one")
    outer.add_attachment(inner)
    root, results, turn = _rig(platform, tmp_path, monkeypatch, [outer])

    kept = files_under(root)
    assert [p.name for p in kept] == ["forwarded-email-2026-11-20-101530.eml"]
    assert b"The quote is 400." in kept[0].read_bytes()
    assert turn.calls[0].attachments == [str(kept[0])]


def test_an_over_cap_attachment_in_the_poll_is_named_not_fetched(platform, tmp_path, monkeypatch):
    # Through the whole pipeline: the size check before the download refuses
    # it, so the channel's fetch is never reached.
    platform.config.phone_file_max_mb = 1
    big = _mail(pdfs=0, body="file the scan")
    big.add_attachment(b"x" * (2 * 1024 * 1024), maintype="application", subtype="pdf",
                       filename="scan.pdf")
    fetched: list[str] = []
    real = EmailChannel.fetch_file

    def spy(self, f, cap):  # noqa: ANN001
        fetched.append(f.name)
        return real(self, f, cap)

    monkeypatch.setattr(EmailChannel, "fetch_file", spy)
    root, results, turn = _rig(platform, tmp_path, monkeypatch, [big])

    assert fetched == []
    assert files_under(root) == []
    assert any("scan.pdf" in s and "1 MB" in s for s in _SMTP.sent)
    assert len(turn.calls) == 1 and turn.calls[0].attachments == []


class _OfflineModel:
    """The daemon's voice deps: an offline speech model on this PC, no speech
    service chosen."""

    _VOICE_MAX_BYTES = 25 * 1024 * 1024

    def __init__(self, platform) -> None:  # noqa: ANN001
        self.platform = platform

    def _vosk_model_path(self) -> str:
        return "vosk-model"

    def _voice_backend(self) -> None:
        return None


def _memo(*, subject: str, body: str) -> EmailMessage:
    memo = _mail(subject=subject, body=body, pdfs=0)
    memo.add_attachment(b"\x00\x00\x00\x18ftypM4A memo", maintype="audio", subtype="mp4",
                        filename="New Recording 3.m4a")
    return memo


def _offline_worker(monkeypatch) -> list[dict]:  # noqa: ANN001
    from iron_jarvis.comm import voice_note as VN

    asked: list[dict] = []

    def worker(request: dict, timeout: float) -> dict:
        asked.append(request)
        return {"text": "call the bank at nine", "seconds": 2.0}

    monkeypatch.setattr(VN, "_ask_worker", worker)
    return asked


#: What a mail app puts in the body of a shared voice memo by itself: no words
#: of the sender's own.
APP_ONLY_BODIES = [
    "",
    "Sent from my iPhone",
    "\n\nSent from my iPhone\n",
    "Get Outlook for iOS<https://aka.ms/o0ukef>",
    "Sent from my Samsung Galaxy smartphone.",
    "Von meinem iPhone gesendet",
    "-- \nAlice Example\nExample Ltd",
]


@pytest.mark.parametrize("body", APP_ONLY_BODIES)
@pytest.mark.parametrize("subject", ["New Recording 3", ""])
def test_a_voice_memo_by_email_is_heard_whatever_its_subject(subject, body, platform, tmp_path,
                                                             monkeypatch):
    # Almost every e-mail has a subject, and a shared voice memo's subject is
    # the recording's name. The e-mail's own words are its body: with nothing
    # in it the recording is heard, as a voice note on Telegram is ("I heard:
    # ..." first, then the work). With the subject counted as words it was
    # kept as a file and never heard. The line the mail app adds ("Sent from
    # my iPhone", the usual body of a memo shared from the phone) and a
    # signature under "-- " are not the sender's words either: with them
    # counted, the memo was kept as a file and never heard.
    asked = _offline_worker(monkeypatch)
    root, results, turn = _rig(platform, tmp_path, monkeypatch, [_memo(subject=subject, body=body)],
                               voice_deps=_OfflineModel(platform))

    kept = files_under(root)
    assert len(kept) == 1 and kept[0].suffix == ".m4a"
    assert [a["path"] for a in asked] == [str(kept[0])]
    assert "I heard: call the bank at nine" in _SMTP.sent[0]
    assert len(turn.calls) == 1
    assert "call the bank at nine" in turn.calls[0].messages[-1].content
    # Its words are the message; the recording itself is named, not read.
    assert turn.calls[0].attachments == []
    assert turn.calls[0].thread_files == [str(kept[0])]


@pytest.mark.parametrize("body", [
    "File this with the March notes.",
    "File this with the March notes.\n\nSent from my iPhone",
    "Sent from my iPhone\nFile this with the March notes.",
    "Alice",  # a name the sender typed is their words: never guessed at
])
def test_an_audio_file_with_words_in_the_body_is_a_file(body, platform, tmp_path, monkeypatch):
    # Words in the body are the sender's own: the recording is a file to work
    # on, as an audio file with a caption is on Telegram. Only the mail app's
    # own last line is left out, never a line the sender wrote.
    asked = _offline_worker(monkeypatch)
    root, results, turn = _rig(
        platform, tmp_path, monkeypatch,
        [_memo(subject="New Recording 3", body=body)],
        voice_deps=_OfflineModel(platform))

    kept = files_under(root)
    assert asked == []
    assert not any("I heard" in s for s in _SMTP.sent)
    assert turn.calls[0].attachments == [str(kept[0])]
    # The turn reads the whole e-mail as it came, sign-off and all.
    assert body.strip() in turn.calls[0].messages[-1].content


@pytest.mark.parametrize("body, mine", [
    ("Sent from my iPhone", ""),
    ("  sent from my ipad.  ", ""),
    ("Sent from Mail for Windows", ""),
    ("Sent from my Pixel 9 Pro", ""),
    ("Envoyé de mon iPhone", ""),
    ("Please file it.\n\nSent from my iPhone\n", "Please file it."),
    ("Please file it.\n-- \nAlice\nExample Ltd", "Please file it."),
    ("Please file it.\n--\nAlice", "Please file it."),
    ("Sent from my iPhone\nPlease file it.", "Sent from my iPhone\nPlease file it."),
    ("Sent from my office, please file it", "Sent from my office, please file it"),
    ("Sent from home", "Sent from home"),
    ("Alice", "Alice"),
])
def test_only_the_mail_apps_own_line_is_left_out(body, mine):
    from iron_jarvis.comm.base import without_signoff

    assert without_signoff(body) == mine


class _NothingSetUp:
    """No offline speech model and no speech service: a recording is not heard."""

    _VOICE_MAX_BYTES = 25 * 1024 * 1024

    def __init__(self, platform) -> None:  # noqa: ANN001
        self.platform = platform

    def _vosk_model_path(self) -> None:
        return None

    def _voice_backend(self) -> None:
        return None


@pytest.mark.parametrize("subject", ["New Recording 3", "New Recording 3.m4a", ""])
def test_a_memo_not_heard_whose_subject_is_its_name_waits(subject, platform, tmp_path, monkeypatch):
    # The subject of a shared memo is its name: no request. When it cannot be
    # heard the message ends with the receipt, as a voice note on Telegram
    # does, instead of a turn answering "New Recording 3". The second memo of
    # the same name is kept as "New_Recording_3-2.m4a": its subject is still
    # read against the name the phone sent.
    memo = _memo(subject=subject, body="Sent from my iPhone")
    root, results, turn = _rig(platform, tmp_path, monkeypatch, [memo, memo],
                               voice_deps=_NothingSetUp(platform))

    assert [r["status"] for r in results] == ["voice_not_heard", "voice_not_heard"]
    assert len(files_under(root)) == 2
    assert len(_SMTP.sent) == 2 and all("Saved New" in s for s in _SMTP.sent)
    assert turn.calls == []


def test_a_memo_not_heard_with_a_request_in_the_subject_still_runs_it(platform, tmp_path,
                                                                      monkeypatch):
    # Words in the subject that are not the recording's name are a request:
    # the recording could not be heard, and the words still run.
    root, results, turn = _rig(
        platform, tmp_path, monkeypatch,
        [_memo(subject="Call the bank at nine tomorrow", body="")],
        voice_deps=_NothingSetUp(platform))

    kept = files_under(root)
    assert [r["status"] for r in results] == ["chat"]
    assert len(turn.calls) == 1
    assert turn.calls[0].messages[-1].content.startswith("Call the bank at nine tomorrow")
    assert turn.calls[0].attachments == []
    assert turn.calls[0].thread_files == [str(kept[0])]


def _vcard(subject: str) -> EmailMessage:
    mail = _mail(subject=subject, body="", pdfs=0)
    mail.add_attachment(b"BEGIN:VCARD\nFN:Bank\nEND:VCARD\n", maintype="text", subtype="vcard",
                        filename="bank.vcf")
    return mail


def _big_scan(subject: str) -> EmailMessage:
    mail = _mail(subject=subject, body="", pdfs=0)
    mail.add_attachment(b"x" * (2 * 1024 * 1024), maintype="application", subtype="pdf",
                        filename="scan.pdf")
    return mail


@pytest.mark.parametrize("build, name", [(_vcard, "bank.vcf"), (_big_scan, "scan.pdf")])
def test_a_subject_only_email_with_a_file_not_taken_still_runs_its_words(build, name, platform,
                                                                         tmp_path, monkeypatch):
    # R0.3: "the words still run". All of this e-mail's words are in its
    # subject; its file is not taken. The receipt names the file, then the
    # subject is answered, told one file was not read. It ended after the
    # receipt, the subject never answered, while the body alone was read as
    # its words.
    from iron_jarvis.comm.inbound import files_not_read_note

    platform.config.phone_file_max_mb = 1
    root, results, turn = _rig(platform, tmp_path, monkeypatch,
                               [build("Call the bank at nine tomorrow")])

    assert files_under(root) == []
    assert [r["status"] for r in results] == ["chat"]
    assert len(_SMTP.sent) == 2 and f"I couldn't take {name}" in _SMTP.sent[0]
    assert len(turn.calls) == 1 and turn.calls[0].attachments == []
    assert turn.calls[0].messages[-1].content == (
        "Call the bank at nine tomorrow\n\n" + files_not_read_note(1))


def test_a_signature_and_a_body_picture_are_not_files():
    from email import message_from_bytes
    from email.mime.image import MIMEImage
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    related = MIMEMultipart("related")
    related.attach(MIMEText("<p>hi <img src='cid:logo'></p>", "html"))
    logo = MIMEImage(b"\x89PNG\r\n\x1a\n", "png")
    logo.add_header("Content-Disposition", "inline", filename="logo.png")
    related.attach(logo)
    signed = MIMEMultipart("signed", protocol="application/pkcs7-signature")
    signed.attach(related)
    sig = MIMEText("x")
    sig.replace_header("Content-Type", "application/pkcs7-signature; name=smime.p7s")
    sig.add_header("Content-Disposition", "attachment", filename="smime.p7s")
    signed.attach(sig)

    assert _email_attachments(message_from_bytes(signed.as_bytes())) == []
