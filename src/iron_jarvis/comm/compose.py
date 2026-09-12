"""Compose a real email from a chat draft (C-06): save to Drafts, or send.

THE NEED: the chat drafts an email in a DraftCard and the only thing the card
could do was COPY it. The user then opened Outlook, pasted, and went hunting
for the file the chat had just made so they could attach it. This module is
the one path from that card to the user's own mailbox.

SUGGEST, DON'T ACT — the rules this module exists to keep:

* It is NOT a tool. Nothing here is registered in the tool registry; the only
  caller is ``POST /comm/email/compose``, which the card calls on a person's
  click after a confirm dialog. A model cannot reach it.
* Saving to the mailbox's **Drafts** folder (IMAP APPEND) is the default and
  sends nothing. **Send** (SMTP) is a separate mode the dialog must ask for.
* Attachments are full paths the dashboard offered from THIS conversation's
  files (the Files rail); every one must pass ``fs_policy.fs_read_ok`` here,
  so a protected root (the app's own secrets) can never ride out in an email.
* Every attempt, success or failure, is published as ``comm.email_composed``
  (persisted to the event ledger like every event). The payload carries the
  recipients, the subject line, attachment NAMES and the outcome — never the
  message body, never a credential.

Blocking work (reading attachments, IMAP, SMTP) runs in a worker thread under
a deadline chosen by the route; these functions are synchronous on purpose.
"""

from __future__ import annotations

import mimetypes
import re
import socket
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid
from pathlib import Path
from typing import Any

#: The ledger event every compose attempt publishes (persisted by the
#: event bus's persistence handler, like every other event).
EMAIL_COMPOSED = "comm.email_composed"

MODES = ("draft", "send")
MAX_RECIPIENTS = 25
MAX_ATTACHMENTS = 8
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 25 * 1024 * 1024
MAX_HTML_CHARS = 500_000
MAX_TEXT_CHARS = 200_000
MAX_SUBJECT_CHARS = 300
#: How long the route waits for the mail server before answering. A send that
#: times out may still have landed, so the words say "check before retrying".
COMPOSE_DEADLINE_S = 45.0

NO_EMAIL_CHANNEL = (
    "No email account is connected yet. Add your email in Channels "
    "(SMTP to send, IMAP to save drafts), then try again."
)

#: Folder names tried, in order, when the server advertises no ``\\Drafts``
#: special-use folder (RFC 6154) — plain, Gmail, Dovecot/Courier styles.
DRAFT_FOLDER_FALLBACKS = (
    "Drafts",
    "[Gmail]/Drafts",
    "INBOX.Drafts",
    "INBOX/Drafts",
    "Draft",
)

_ADDR = re.compile(r"^[^@\s<>\"',;()]+@[^@\s<>\"',;()]+\.[^@\s<>\"',;()]+$")
_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_LIST_LINE = re.compile(
    rb'^\((?P<flags>[^)]*)\)\s+(?:"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.+)$', re.I
)


class ComposeError(ValueError):
    """A request the user can fix (bad address, unreadable file, empty draft)."""


def transport_problem(exc: BaseException, *, doing: str) -> str:
    """A mail-server failure in PLAIN WORDS.

    Every other refusal in this module already reads like a sentence; the
    catch-all used to end in ``f"{type(exc).__name__}: {exc}"``, and the live
    check put exactly that in front of the user — "ConnectionRefusedError:
    [WinError 10061] No connection could be made because the target machine
    actively refused it". A class name and a WinError number name nothing the
    user can act on. The exception text is still appended, clipped, because a
    server's own words ("STARTTLS required") are often the useful half.
    """
    detail = str(exc).strip()
    if isinstance(exc, (ConnectionRefusedError, ConnectionResetError, socket.gaierror)):
        why = "could not be reached"
    elif isinstance(exc, (socket.timeout, TimeoutError)):
        why = "did not answer in time"
    elif isinstance(exc, ssl.SSLError):
        why = "refused the secure connection"
    elif isinstance(exc, OSError):
        why = "could not be reached"
    else:
        why = "could not be used"
    tail = f" ({detail[:160]})" if detail else ""
    return (
        f"the mail server {why}, so {doing} did not happen — check the server "
        f"and port for this email account in Channels{tail}"
    )


@dataclass
class ComposePlan:
    mode: str
    to: list[str]
    cc: list[str]
    subject: str
    text: str
    html: str
    attachments: list[Path] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Validation (BLOCKING: attachment checks stat the disk — call off the loop)
# --------------------------------------------------------------------------- #
def parse_addresses(values: Any, label: str) -> list[str]:
    """``["Ann <ann@x.com>", "bob@y.com; cy@z.com"]`` → validated, de-duplicated
    header values. A line break anywhere is refused (header injection)."""
    raw = [v for v in (values or []) if isinstance(v, str)]
    if any("\r" in v or "\n" in v for v in raw):
        raise ComposeError(f"{label}: line breaks are not allowed in an address")
    parts = [p for v in raw for p in v.split(";")]
    out: list[str] = []
    seen: set[str] = set()
    for name, addr in getaddresses(parts):
        addr = (addr or "").strip()
        if not addr:
            continue
        if not _ADDR.match(addr):
            raise ComposeError(f"{label}: {addr!r} is not an email address")
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(formataddr((name.strip(), addr)) if name and name.strip() else addr)
    return out


def check_attachments(paths: Any) -> list[Path]:
    """Every attachment must be a full path to a readable file the file policy
    allows, within the size caps. Refusals name the file and the reason."""
    from ..core.fs_policy import fs_read_ok

    items = [str(p) for p in (paths or []) if isinstance(p, str) and p.strip()]
    if len(items) > MAX_ATTACHMENTS:
        raise ComposeError(f"At most {MAX_ATTACHMENTS} attachments per email.")
    out: list[Path] = []
    total = 0
    for raw in items:
        p = Path(raw)
        if not p.is_absolute():
            raise ComposeError(f"{p.name}: an attachment must be a full path")
        ok, why = fs_read_ok(p)
        if not ok:
            raise ComposeError(f"{p.name} can't be attached: {why}")
        if not p.is_file():
            raise ComposeError(f"{p.name}: file not found")
        size = p.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            raise ComposeError(
                f"{p.name} is {size // (1024 * 1024)} MB — attachments are "
                f"limited to {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB each."
            )
        total += size
        if total > MAX_TOTAL_BYTES:
            raise ComposeError(
                f"Attachments add up to more than "
                f"{MAX_TOTAL_BYTES // (1024 * 1024)} MB — most mail servers refuse that."
            )
        if p not in out:
            out.append(p)
    return out


def prepare(body: dict[str, Any]) -> ComposePlan:
    """Validate a compose request into a plan. Raises :class:`ComposeError`."""
    body = body if isinstance(body, dict) else {}
    mode = str(body.get("mode") or "draft").strip().lower()
    if mode not in MODES:
        raise ComposeError("mode must be 'draft' or 'send'")
    to = parse_addresses(body.get("to"), "To")
    cc = parse_addresses(body.get("cc"), "Cc")
    if mode == "send" and not to:
        raise ComposeError("Sending needs at least one address in To.")
    if len(to) + len(cc) > MAX_RECIPIENTS:
        raise ComposeError(f"At most {MAX_RECIPIENTS} recipients per email.")
    subject = " ".join(str(body.get("subject") or "").split())[:MAX_SUBJECT_CHARS]
    text = str(body.get("text") or "")
    html = str(body.get("html") or "")
    if len(html) > MAX_HTML_CHARS or len(text) > MAX_TEXT_CHARS:
        raise ComposeError("This draft is too long to send as one email.")
    html = _SCRIPT.sub("", html)
    if not text.strip() and not html.strip():
        raise ComposeError("The draft is empty.")
    return ComposePlan(
        mode=mode,
        to=to,
        cc=cc,
        subject=subject,
        text=text,
        html=html,
        attachments=check_attachments(body.get("attachments")),
    )


# --------------------------------------------------------------------------- #
# The message
# --------------------------------------------------------------------------- #
def _html_document(fragment: str) -> str:
    # The fragment already carries the card's inline, point-based spacing
    # (EMAIL_STYLES) — Outlook renders through Word, which ignores stylesheets.
    return (
        '<!DOCTYPE html><html><head><meta charset="utf-8"></head><body>'
        f"{fragment}</body></html>"
    )


def _text_from_html(html: str) -> str:
    text = re.sub(r"<(br|/p|/li|/h[1-6]|/div)\b[^>]*>", "\n", html, flags=re.I)
    text = _TAG.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def build_message(plan: ComposePlan, *, from_addr: str) -> EmailMessage:
    """A multipart message: plain text + the card's HTML, then attachments."""
    msg = EmailMessage()
    if plan.subject:
        msg["Subject"] = plan.subject
    msg["From"] = from_addr
    if plan.to:
        msg["To"] = ", ".join(plan.to)
    if plan.cc:
        msg["Cc"] = ", ".join(plan.cc)
    msg["Date"] = formatdate(localtime=True)
    domain = from_addr.rsplit("@", 1)[-1].strip(">") if "@" in from_addr else None
    msg["Message-ID"] = make_msgid(domain=domain or None)
    msg.set_content(plan.text.strip() or _text_from_html(plan.html) or " ")
    if plan.html.strip():
        msg.add_alternative(_html_document(plan.html), subtype="html")
    for p in plan.attachments:
        ctype, encoding = mimetypes.guess_type(p.name)
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(
            p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name
        )
    return msg


# --------------------------------------------------------------------------- #
# The mailbox (IMAP APPEND) and the server (SMTP)
# --------------------------------------------------------------------------- #
def _smtp_connect(host: str, port: int) -> Any:
    """Open an SMTP connection — the SEND seam (tests replace it). Port 465 is
    implicit TLS; anything else starts plain and upgrades with STARTTLS."""
    import smtplib

    if port == 465:
        import ssl

        return smtplib.SMTP_SSL(
            host, port, timeout=20, context=ssl.create_default_context()
        )
    return smtplib.SMTP(host, port, timeout=20)


def _imap_folders(conn: Any) -> list[tuple[str, set[str]]]:
    """``LIST "" "*"`` → ``[(name, {flags})]`` (flags lower-cased)."""
    try:
        typ, data = conn.list()
    except Exception:  # noqa: BLE001 — no listing = fall back to known names
        return []
    if typ != "OK":
        return []
    out: list[tuple[str, set[str]]] = []
    for line in data or []:
        if isinstance(line, tuple):  # a literal: (b'(...) "/" {n}', b'name')
            line = b" ".join(x for x in line if isinstance(x, (bytes, bytearray)))
        if not isinstance(line, (bytes, bytearray)):
            continue
        m = _LIST_LINE.match(bytes(line).strip())
        if not m:
            continue
        name = m.group("name").strip()
        if name.startswith(b'"') and name.endswith(b'"'):
            name = name[1:-1].replace(b'\\"', b'"').replace(b"\\\\", b"\\")
        flags = {f.lower() for f in m.group("flags").decode("ascii", "replace").split()}
        out.append((name.decode("utf-8", "replace"), flags))
    return out


def drafts_folder(conn: Any) -> str | None:
    """The mailbox's Drafts folder: the ``\\Drafts`` special-use flag first,
    then the common names. ``None`` when neither exists."""
    folders = _imap_folders(conn)
    for name, flags in folders:
        if "\\drafts" in flags:
            return name
    names = {n.lower(): n for n, _ in folders}
    for candidate in DRAFT_FOLDER_FALLBACKS:
        if candidate.lower() in names:
            return names[candidate.lower()]
    return None


def _imap_quote(name: str) -> str:
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save_draft(channel: Any, msg: EmailMessage) -> dict[str, Any]:
    """Put ``msg`` in the mailbox's Drafts folder (IMAP APPEND, ``\\Draft``
    flag). Nothing is sent. Returns ``{"ok", "detail", "folder"?}``."""
    from . import channels as _channels  # the module attribute is the seam

    cfg = channel.config
    username = cfg.get("username")
    password = channel._resolve_secret(cfg.get("password_secret"))
    host = cfg.get("imap_host") or cfg.get("host")
    if not username or not password or not host:
        return {
            "ok": False,
            "detail": "saving a draft needs this email account's IMAP server, "
            "username and password (Channels → your email account)",
        }
    conn = None
    try:
        import imaplib
        import time

        port = int(cfg.get("imap_port") or 993)
        conn = _channels._imap_connect(host, port)
        try:
            conn.login(username, password)
        except Exception as exc:  # noqa: BLE001 — imaplib.IMAP4.error mostly
            return {
                "ok": False,
                "detail": f"the mail server refused the login for {username} at "
                f"{host}: {str(exc)[:200] or type(exc).__name__}",
            }
        folder = drafts_folder(conn)
        if not folder:
            listed = ", ".join(n for n, _ in _imap_folders(conn)[:12]) or "none listed"
            return {
                "ok": False,
                "detail": f"this mailbox has no Drafts folder (folders: {listed})",
            }
        typ, data = conn.append(
            _imap_quote(folder),
            "(\\Draft)",
            imaplib.Time2Internaldate(time.time()),
            msg.as_bytes(),
        )
        if typ != "OK":
            why = b" ".join(
                x for x in (data or []) if isinstance(x, (bytes, bytearray))
            ).decode("utf-8", "replace")[:200]
            return {
                "ok": False,
                "detail": f"the mail server refused to save the draft in {folder}: "
                f"{why or typ}",
            }
        return {"ok": True, "detail": f"saved to {folder}", "folder": folder}
    except Exception as exc:  # noqa: BLE001 — a transport error is a result
        return {"ok": False, "detail": transport_problem(exc, doing="saving the draft")}
    finally:
        if conn is not None:
            try:
                conn.logout()
            except Exception:  # noqa: BLE001 — best-effort close
                pass


def send_mail(channel: Any, msg: EmailMessage) -> dict[str, Any]:
    """Send ``msg`` over the account's SMTP server. A partial refusal is
    reported by address; a total refusal raises inside smtplib → failure."""
    cfg = channel.config
    host = cfg.get("host")
    if not host:
        return {
            "ok": False,
            "detail": "sending needs this email account's SMTP server "
            "(Channels → your email account)",
        }
    password = channel._resolve_secret(cfg.get("password_secret"))
    try:
        port = int(cfg.get("port") or 587)
        with _smtp_connect(host, port) as smtp:
            if cfg.get("use_tls", True) and port != 465:
                smtp.starttls()
            if cfg.get("username") and password:
                smtp.login(cfg["username"], password)
            refused = smtp.send_message(msg) or {}
        if refused:
            names = sorted(str(k) for k in refused)
            return {
                "ok": True,
                "detail": f"sent, but the server refused: {', '.join(names)}",
                "refused": names,
            }
        return {"ok": True, "detail": "sent"}
    except Exception as exc:  # noqa: BLE001 — a transport error is a result
        return {"ok": False, "detail": transport_problem(exc, doing="sending")}


def run_compose(channel: Any, plan: ComposePlan, *, from_addr: str) -> dict[str, Any]:
    """BLOCKING: build the message (reads attachments) and hand it over."""
    msg = build_message(plan, from_addr=from_addr)
    if plan.mode == "draft":
        return save_draft(channel, msg)
    return send_mail(channel, msg)


def find_email_channel(notifier: Any) -> tuple[str, Any] | None:
    """The first connected email account, as ``(name, channel)``, or None."""
    from .channels import EmailChannel

    for name in notifier.channels():
        ch = notifier.get(name)
        if isinstance(ch, EmailChannel):
            return name, ch
    return None


def ledger_payload(name: str, plan: ComposePlan, result: dict[str, Any]) -> dict[str, Any]:
    """What the ledger keeps: who, what subject, which files, what happened.
    Never the body, never a credential."""
    return {
        "channel": name,
        "mode": plan.mode,
        "ok": bool(result.get("ok")),
        "detail": str(result.get("detail") or "")[:300],
        "folder": result.get("folder"),
        "to": list(plan.to),
        "cc": list(plan.cc),
        "subject": plan.subject[:120],
        "attachments": [p.name for p in plan.attachments],
        "timed_out": bool(result.get("timed_out")),
    }
