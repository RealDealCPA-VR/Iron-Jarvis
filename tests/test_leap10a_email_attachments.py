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


def _rig(platform, tmp_path, monkeypatch, mails: list[EmailMessage]):
    root = chat_root(platform, tmp_path)
    fake = _IMAP([(10 + i, m.as_bytes()) for i, m in enumerate(mails)])
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda host, port: fake)
    _SMTP.sent = []
    monkeypatch.setattr(smtplib, "SMTP", _SMTP)
    ch = EmailChannel(dict(CFG), secret_resolver=lambda n: "pw-test" if n == "imap_pw" else None)
    turn = fake_turn("Filed under March.")
    poll = poller(platform, ch, name="mail", turn=turn)

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
