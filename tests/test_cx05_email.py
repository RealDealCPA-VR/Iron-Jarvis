"""CX-05 — EMAIL inbound trigger (offline).

No real server: the IMAP connect seam (``channels._imap_connect``) is
monkeypatched with a fake that serves canned RFC822 messages honouring the
``UID {offset+1}:*`` search contract. Covers the receive leg (poll parses
sender/subject/body, advances the durable UID offset, and dedupes on a second
poll) plus the fail-closed From-address allowlist that guards it.
"""

from __future__ import annotations

import re
from email.message import EmailMessage
from typing import Any

from iron_jarvis.comm.base import InboundMessage
from iron_jarvis.comm.channels import EmailChannel


# --------------------------------------------------------------------------- #
# Fakes — an IMAP4_SSL stand-in that honours the UID SEARCH offset semantics.
# --------------------------------------------------------------------------- #
def _raw_email(sender: str, subject: str, body: str) -> bytes:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "jarvis@example.com"
    msg["Subject"] = subject
    msg.set_content(body)
    return msg.as_bytes()


class FakeIMAP:
    """Minimal ``imaplib.IMAP4_SSL`` stand-in over a list of ``(uid, raw)`` mails.

    ``uid("SEARCH", None, "UID lo:*")`` returns every UID >= lo, and — like a real
    server — clamps to the single highest UID when lo exceeds it (so the channel's
    ``> offset`` filter is exercised). ``uid("FETCH", uid, "(RFC822)")`` returns the
    imaplib-shaped nested tuple ``[(header, raw_bytes), b")"]``.
    """

    def __init__(self, mails: list[tuple[int, bytes]]) -> None:
        self.mails = list(mails)
        self.logged_in: tuple[str, str] | None = None
        self.selected: str | None = None
        self.logged_out = False
        self.search_calls: list[str] = []

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.logged_in = (user, password)
        return "OK", [b"LOGIN completed"]

    def select(self, mailbox: str = "INBOX") -> tuple[str, list[bytes]]:
        self.selected = mailbox
        return "OK", [str(len(self.mails)).encode()]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        cmd = command.upper()
        if cmd == "SEARCH":
            criteria = str(args[-1])
            self.search_calls.append(criteria)
            m = re.search(r"UID (\d+):", criteria)
            lo = int(m.group(1)) if m else 1
            ids = [uid for uid, _ in self.mails if uid >= lo]
            if not ids and self.mails:  # IMAP `n:*` clamp to the highest UID
                ids = [max(uid for uid, _ in self.mails)]
            return "OK", [b" ".join(str(u).encode() for u in ids)]
        if cmd == "FETCH":
            uid = int(args[0])
            for u, raw in self.mails:
                if u == uid:
                    header = f"{uid} (UID {uid} RFC822 {{{len(raw)}}}".encode()
                    return "OK", [(header, raw), b")"]
            return "OK", [None]
        return "OK", [b""]

    def logout(self) -> tuple[str, list[bytes]]:
        self.logged_out = True
        return "BYE", [b"logout"]


def _email_channel(config: dict[str, Any], *, password: str | None = "hunter2") -> EmailChannel:
    return EmailChannel(
        config,
        secret_resolver=lambda name: password if name == "imap_pw" else None,
    )


_INBOUND_CFG = {
    "imap_host": "imap.example.com",
    "username": "jarvis@example.com",
    "password_secret": "imap_pw",
    "mailbox": "INBOX",
    "inbound_enabled": True,
    "allowed_senders": ["alice@example.com"],
}


# --------------------------------------------------------------------------- #
# poll() parses canned mail and advances a durable, deduping UID offset.
# --------------------------------------------------------------------------- #
def test_poll_returns_parsed_messages_and_advances_offset(monkeypatch):
    fake = FakeIMAP(
        [
            (11, _raw_email("Alice <alice@example.com>", "Deploy failed", "The nightly deploy failed on staging.")),
            (12, _raw_email("Bob <bob@example.com>", "Invoice #42", "Please review the attached invoice.")),
        ]
    )
    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", lambda host, port: fake)

    ch = _email_channel(_INBOUND_CFG)
    messages, next_offset = ch.poll(0)

    # Two InboundMessages, sorted by UID ascending.
    assert len(messages) == 2
    assert all(isinstance(m, InboundMessage) for m in messages)
    assert [m.update_id for m in messages] == [11, 12]

    first, second = messages
    # sender = the bare From address (display name stripped); reply_to mirrors it
    # so the poller's private-chat guard (reply_to == sender_id) passes.
    assert first.sender_id == "alice@example.com"
    assert first.reply_to == "alice@example.com"
    assert first.is_bot is False
    assert first.raw == {"subject": "Deploy failed", "from": "alice@example.com"}
    # text = subject + "\n" + body.
    assert "Deploy failed" in first.text
    assert "nightly deploy failed on staging" in first.text
    assert second.sender_id == "bob@example.com"
    assert "Invoice #42" in second.text and "review the attached invoice" in second.text

    # next_offset is strictly greater than the max UID (so it dedupes next time).
    assert next_offset == 13
    assert next_offset > max(m.update_id for m in messages)

    # The channel actually authenticated + selected the mailbox via the seam.
    assert fake.logged_in == ("jarvis@example.com", "hunter2")
    assert fake.selected == "INBOX"
    assert fake.logged_out is True

    # A second poll from the advanced offset returns NOTHING (the `> offset`
    # filter drops the server's clamped high-UID echo — no re-fire on restart).
    again, again_offset = ch.poll(next_offset)
    assert again == []
    assert again_offset == next_offset


def test_poll_without_password_is_a_noop(monkeypatch):
    """An inbound email channel whose password does not resolve never connects."""
    called = False

    def _boom(host, port):
        nonlocal called
        called = True
        raise AssertionError("must not connect without credentials")

    monkeypatch.setattr("iron_jarvis.comm.channels._imap_connect", _boom)
    ch = _email_channel(_INBOUND_CFG, password=None)  # secret does not resolve

    assert ch.has_credentials() is False
    assert ch.poll(0) == ([], 0)
    assert called is False


# --------------------------------------------------------------------------- #
# Security — off by default, credentialed, FAIL-CLOSED allowlist.
# --------------------------------------------------------------------------- #
def test_has_credentials_tracks_password_secret():
    assert _email_channel(_INBOUND_CFG).has_credentials() is True
    assert _email_channel(_INBOUND_CFG, password=None).has_credentials() is False


def test_inbound_off_by_default():
    cfg = {**_INBOUND_CFG}
    cfg.pop("inbound_enabled")
    ch = _email_channel(cfg)
    assert ch.supports_inbound is True
    assert ch.inbound_enabled() is False  # opt-in required
    assert _email_channel(_INBOUND_CFG).inbound_enabled() is True


def test_reflex_source_is_email():
    assert EmailChannel.reflex_source == "email"


def test_is_authorized_fail_closed():
    # Empty / missing allowlist authorizes NOBODY.
    empty = _email_channel({**_INBOUND_CFG, "allowed_senders": []})
    assert empty.is_authorized("alice@example.com") is False
    assert empty.allowed_senders() == set()

    # Only an explicitly listed From-address is accepted.
    ch = _email_channel(_INBOUND_CFG)  # allowlist = alice@example.com
    assert ch.is_authorized("alice@example.com") is True
    assert ch.is_authorized("mallory@evil.com") is False
