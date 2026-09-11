"""C-06 — a chat draft becomes a real email: saved to Drafts, or sent.

No real server anywhere: the IMAP connect seam
(``comm.channels._imap_connect``) and the SMTP seam
(``comm.compose._smtp_connect``) are monkeypatched with fakes, so these tests
never touch the network and never send mail.

What matters here, in the order it matters:

* SUGGEST, DON'T ACT — ``draft`` is the default and puts the message in the
  mailbox's Drafts folder; ``send`` is a separate mode; and compose is NOT a
  tool, so no model can reach it.
* The message the mailbox gets is the real thing: plain text AND the card's
  HTML, with the conversation's file attached.
* Every attempt is ledgered as ``comm.email_composed`` with the recipients,
  the subject and the attachment NAMES — never the body, never a credential.
* Refusals are the user's words: a bad address, an unreadable attachment, a
  mailbox with no Drafts folder, no connected account at all.
"""

from __future__ import annotations

import email
import time
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.comm import compose as compose_mod
from iron_jarvis.comm.channels import EmailChannel
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import EventRecord
from iron_jarvis.daemon.app import create_app

PASSWORD = "hunter2"
BODY_SECRET = "Fee is $3,000 and the K-1 is attached."

CFG = {
    "host": "smtp.example.com",
    "port": 587,
    "imap_host": "imap.example.com",
    "imap_port": 993,
    "username": "jarvis@example.com",
    "from_addr": "Jarvis <jarvis@example.com>",
    "password_secret": "email_pw",
}


class FakeIMAP:
    """Enough IMAP4_SSL for APPEND: LIST, LOGIN, APPEND, LOGOUT."""

    def __init__(self, folders: list[bytes] | None = None, append_ok: bool = True) -> None:
        self.folders = folders if folders is not None else [
            b'(\\HasNoChildren \\Drafts) "/" "[Gmail]/Drafts"',
            b'(\\HasNoChildren) "/" "INBOX"',
        ]
        self.append_ok = append_ok
        self.appended: list[tuple[str, str, bytes]] = []
        self.logged_in: tuple[str, str] | None = None
        self.logged_out = False

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.logged_in = (user, password)
        return "OK", [b"ok"]

    def list(self, *a: Any, **k: Any) -> tuple[str, list[bytes]]:
        return "OK", list(self.folders)

    def append(self, folder: str, flags: str, date: str, message: bytes):
        self.appended.append((folder, flags, message))
        return ("OK", [b"[APPENDUID 1 2] done"]) if self.append_ok else ("NO", [b"over quota"])

    def logout(self) -> tuple[str, list[bytes]]:
        self.logged_out = True
        return "BYE", [b"bye"]


class FakeSMTP:
    def __init__(self) -> None:
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.sent: list[Any] = []
        self.refused: dict[str, Any] = {}

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def starttls(self) -> None:
        self.started_tls = True

    def login(self, user: str, password: str) -> None:
        self.logged_in = (user, password)

    def send_message(self, msg: Any) -> dict[str, Any]:
        self.sent.append(msg)
        return dict(self.refused)


def _client(tmp_path, monkeypatch, *, with_channel: bool = True) -> TestClient:
    client = TestClient(create_app(str(tmp_path)))
    if with_channel:
        client.app.state.platform.notifier.add_channel(
            "work-email",
            EmailChannel(dict(CFG), secret_resolver=lambda n: PASSWORD if n == "email_pw" else None),
        )
    return client


def _attachment(tmp_path) -> str:
    p = tmp_path / "HarborPoint_Q1.xlsx"
    p.write_bytes(b"PK\x03\x04 fictional workbook")
    return str(p)


def _body(**over: Any) -> dict[str, Any]:
    body = {
        "mode": "draft",
        "to": ["client@example.com"],
        "cc": [],
        "subject": "Q1 expenses",
        "text": BODY_SECRET,
        "html": f"<p style='margin:0'>{BODY_SECRET}</p>",
        "attachments": [],
    }
    body.update(over)
    return body


def _events(platform, etype: str) -> list[EventRecord]:
    with session_scope(platform.engine) as db:
        return [r for r in db.exec(select(EventRecord)) if r.type == etype]


# --------------------------------------------------------------------------- #
# Draft: the default, and it SENDS NOTHING.
# --------------------------------------------------------------------------- #
def test_draft_is_appended_to_the_special_use_drafts_folder(tmp_path, monkeypatch):
    fake = FakeIMAP()
    smtp = FakeSMTP()
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda h, p: fake)
    monkeypatch.setattr(compose_mod, "_smtp_connect", lambda h, p: smtp)
    client = _client(tmp_path, monkeypatch)

    r = client.post("/comm/email/compose", json=_body(attachments=[_attachment(tmp_path)]))

    assert r.status_code == 200, r.text
    assert r.json()["folder"] == "[Gmail]/Drafts"
    assert r.json()["mode"] == "draft"
    # Nothing was SENT — that is the whole point of the default.
    assert smtp.sent == []
    folder, flags, raw = fake.appended[0]
    assert folder == '"[Gmail]/Drafts"' and "\\Draft" in flags
    msg = email.message_from_bytes(raw)
    assert msg["To"] == "client@example.com"
    assert msg["Subject"] == "Q1 expenses"
    kinds = {part.get_content_type() for part in msg.walk()}
    assert "text/plain" in kinds and "text/html" in kinds
    names = [p.get_filename() for p in msg.walk() if p.get_filename()]
    assert names == ["HarborPoint_Q1.xlsx"]
    assert fake.logged_in == ("jarvis@example.com", PASSWORD)
    assert fake.logged_out


def test_a_mailbox_without_the_flag_falls_back_to_a_known_drafts_name(tmp_path, monkeypatch):
    fake = FakeIMAP(folders=[b'(\\HasNoChildren) "." "INBOX"', b'(\\HasNoChildren) "." "INBOX.Drafts"'])
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda h, p: fake)
    client = _client(tmp_path, monkeypatch)

    r = client.post("/comm/email/compose", json=_body())

    assert r.status_code == 200, r.text
    assert r.json()["folder"] == "INBOX.Drafts"


def test_a_mailbox_with_no_drafts_folder_says_so_and_lists_what_it_has(tmp_path, monkeypatch):
    fake = FakeIMAP(folders=[b'(\\HasNoChildren) "." "INBOX"', b'(\\HasNoChildren) "." "Sent"'])
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda h, p: fake)
    client = _client(tmp_path, monkeypatch)

    r = client.post("/comm/email/compose", json=_body())

    assert r.status_code == 502
    detail = r.json()["detail"]
    assert "no Drafts folder" in detail and "INBOX" in detail
    assert fake.appended == []


# --------------------------------------------------------------------------- #
# Send: only when asked for, over SMTP, with the attachment.
# --------------------------------------------------------------------------- #
def test_send_goes_over_smtp_with_the_attachment(tmp_path, monkeypatch):
    fake = FakeIMAP()
    smtp = FakeSMTP()
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda h, p: fake)
    monkeypatch.setattr(compose_mod, "_smtp_connect", lambda h, p: smtp)
    client = _client(tmp_path, monkeypatch)

    r = client.post(
        "/comm/email/compose",
        json=_body(mode="send", cc=["partner@example.com"], attachments=[_attachment(tmp_path)]),
    )

    assert r.status_code == 200, r.text
    assert r.json()["detail"] == "sent"
    assert fake.appended == []  # a send does not also leave a draft
    assert smtp.started_tls and smtp.logged_in == ("jarvis@example.com", PASSWORD)
    msg = smtp.sent[0]
    assert msg["Cc"] == "partner@example.com"
    assert [p.get_filename() for p in msg.walk() if p.get_filename()] == ["HarborPoint_Q1.xlsx"]


def test_send_needs_an_address_but_a_draft_does_not(tmp_path, monkeypatch):
    fake = FakeIMAP()
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda h, p: fake)
    client = _client(tmp_path, monkeypatch)

    refused = client.post("/comm/email/compose", json=_body(mode="send", to=[]))
    assert refused.status_code == 400 and "To" in refused.json()["detail"]

    saved = client.post("/comm/email/compose", json=_body(to=[]))
    assert saved.status_code == 200, saved.text


# --------------------------------------------------------------------------- #
# Refusals in the user's words.
# --------------------------------------------------------------------------- #
def test_no_email_account_explains_how_to_connect_one(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, with_channel=False)

    r = client.post("/comm/email/compose", json=_body())

    assert r.status_code == 409
    assert r.json()["detail"] == compose_mod.NO_EMAIL_CHANNEL
    assert "Channels" in r.json()["detail"]


def test_a_bad_address_and_a_header_injection_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda h, p: FakeIMAP())
    client = _client(tmp_path, monkeypatch)

    bad = client.post("/comm/email/compose", json=_body(to=["not-an-address"]))
    assert bad.status_code == 400 and "not an email address" in bad.json()["detail"]

    injected = client.post(
        "/comm/email/compose",
        json=_body(to=["ok@example.com\nBcc: sneaky@example.com"]),
    )
    assert injected.status_code == 400 and "line breaks" in injected.json()["detail"]


def test_an_attachment_the_file_policy_refuses_names_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda h, p: FakeIMAP())
    monkeypatch.setattr(
        "iron_jarvis.core.fs_policy.fs_read_ok", lambda p: (False, "it is in a protected folder")
    )
    client = _client(tmp_path, monkeypatch)

    r = client.post("/comm/email/compose", json=_body(attachments=[_attachment(tmp_path)]))

    assert r.status_code == 400
    assert "HarborPoint_Q1.xlsx" in r.json()["detail"]
    assert "protected folder" in r.json()["detail"]


def test_a_missing_attachment_is_refused_before_anything_is_saved(tmp_path, monkeypatch):
    fake = FakeIMAP()
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda h, p: fake)
    client = _client(tmp_path, monkeypatch)

    r = client.post(
        "/comm/email/compose", json=_body(attachments=[str(tmp_path / "gone.xlsx")])
    )

    assert r.status_code == 400 and "file not found" in r.json()["detail"]
    assert fake.appended == []


def test_a_mail_server_that_never_answers_is_bounded_and_says_what_to_check(tmp_path, monkeypatch):
    def _slow(host: str, port: int):
        time.sleep(2.0)
        return FakeIMAP()

    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", _slow)
    monkeypatch.setattr(compose_mod, "COMPOSE_DEADLINE_S", 0.1)
    client = _client(tmp_path, monkeypatch)

    r = client.post("/comm/email/compose", json=_body())

    assert r.status_code == 502
    assert "did not answer" in r.json()["detail"]
    assert "Drafts" in r.json()["detail"]  # what to check before retrying
    rows = _events(client.app.state.platform, compose_mod.EMAIL_COMPOSED)
    assert rows and '"timed_out": true' in rows[-1].payload_json.lower().replace(" ", " ")


# --------------------------------------------------------------------------- #
# The ledger, and the rule that no model can reach this.
# --------------------------------------------------------------------------- #
def test_a_dead_mail_server_is_explained_in_plain_words_not_a_python_error(tmp_path, monkeypatch):
    """The live check on the isolated daemon showed the user this, verbatim:
    "ConnectionRefusedError: [WinError 10061] No connection could be made
    because the target machine actively refused it". A class name and a WinError
    number name nothing the user can act on, and every other refusal in this
    module already reads like a sentence."""
    def refuse(host, port):
        raise ConnectionRefusedError(
            10061, "No connection could be made because the target machine actively refused it"
        )

    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", refuse)
    monkeypatch.setattr(compose_mod, "_smtp_connect", refuse)
    client = _client(tmp_path, monkeypatch)

    for mode, doing in (("draft", "saving the draft"), ("send", "sending")):
        r = client.post("/comm/email/compose", json=_body(mode=mode))
        assert r.status_code == 502, r.text
        detail = r.json()["detail"]
        assert "the mail server could not be reached" in detail
        assert doing in detail
        assert "Channels" in detail
        # The exception's own words ride along, clipped and in parentheses —
        # "[Errno 10061] ..." included, because a server's own text is often the
        # useful half ("STARTTLS required"). What must never lead the message is
        # the CLASS NAME: that was the whole of the old detail and named nothing
        # the user could act on.
        assert "ConnectionRefusedError" not in detail
        assert not detail.startswith("ConnectionRefusedError")


def test_a_timeout_and_a_tls_refusal_each_say_which_one_it_was(tmp_path, monkeypatch):
    """Two failures the user fixes DIFFERENTLY (wrong port vs wrong security
    setting) must not collapse into one sentence."""
    import socket as _socket
    import ssl as _ssl

    monkeypatch.setattr(
        "iron_jarvis.comm.channels._imap_connect",
        lambda h, p: (_ for _ in ()).throw(_socket.timeout("timed out")),
    )
    client = _client(tmp_path, monkeypatch)
    timed = client.post("/comm/email/compose", json=_body()).json()["detail"]
    assert "did not answer in time" in timed

    monkeypatch.setattr(
        "iron_jarvis.comm.channels._imap_connect",
        lambda h, p: (_ for _ in ()).throw(_ssl.SSLError("WRONG_VERSION_NUMBER")),
    )
    client2 = _client(tmp_path, monkeypatch)
    tls = client2.post("/comm/email/compose", json=_body()).json()["detail"]
    assert "refused the secure connection" in tls
    assert "SSLError" not in tls


def test_every_attempt_is_ledgered_without_the_body_or_the_password(tmp_path, monkeypatch):
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda h, p: FakeIMAP())
    client = _client(tmp_path, monkeypatch)
    platform = client.app.state.platform

    ok = client.post("/comm/email/compose", json=_body(attachments=[_attachment(tmp_path)]))
    assert ok.status_code == 200, ok.text

    rows = _events(platform, compose_mod.EMAIL_COMPOSED)
    assert len(rows) == 1
    payload = rows[0].payload_json
    assert "client@example.com" in payload  # who it went to
    assert "Q1 expenses" in payload  # which email
    assert "HarborPoint_Q1.xlsx" in payload  # what rode along
    assert BODY_SECRET not in payload  # never the body
    assert PASSWORD not in payload  # never a credential


def test_a_refusal_is_ledgered_too(tmp_path, monkeypatch):
    fake = FakeIMAP(append_ok=False)
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda h, p: fake)
    client = _client(tmp_path, monkeypatch)

    r = client.post("/comm/email/compose", json=_body())

    assert r.status_code == 502 and "over quota" in r.json()["detail"]
    rows = _events(client.app.state.platform, compose_mod.EMAIL_COMPOSED)
    assert len(rows) == 1 and '"ok": false' in rows[0].payload_json.lower()


def test_compose_is_not_a_tool_no_model_can_reach_it(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    names = set(client.app.state.platform.registry.names())

    assert not [n for n in names if "compose" in n]
    assert not [n for n in names if "email" in n and "send" in n]
