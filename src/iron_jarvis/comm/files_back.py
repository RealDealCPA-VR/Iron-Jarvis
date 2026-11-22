"""Finished files go back to the phone (10-b, build-out plan R2.4).

The answer to "turn this into a spreadsheet", sent from Telegram, Slack or
email, arrives with the spreadsheet. What may go, and when:

* WHICH files: what the work made, read off its own record, inside the
  folders it was done in, at most five of 20 MB each
  (``agents/reply_files``: the ONE reading a paired computer's answer uses
  too). Anything left out is named with why.
* WHEN: a file leaves the PC through a messaging service, so it goes only
  after a yes (G10, offered once): the FIRST time on each destination the
  phone is asked ("Send 1 spreadsheet (48 KB) to Telegram?", Send / Keep it
  on the PC), and the answer is kept FOR THAT DESTINATION: a Send in
  ``phone_send_files_to``, a Keep in ``phone_send_files_kept`` (records
  beside the setting). Another destination is asked its own first time.
  The setting ``phone_send_files`` (ask, yes, no; Settings › Phone, with
  Undo) follows the phone's answers ("yes" once one said Send) through the
  settings writer, so the phone's first Send has an Undo, and the value it
  gave is kept beside them (``phone_send_files_answered``). The answers
  decide only while the row still reads that value: a choice of the row
  made in Settings or chat since (the writer clears the mark), or an Undo
  that put another value back, asks every destination again, so no Keep
  outlives a choice there and no Send is read wider than the destination
  that said it. "Keep them on this PC" keeps every destination's files. A
  yes chosen in Settings before any phone answer is every destination.
  :func:`consent` reads them; NO reading ever sends where nobody said Send.
* A LOCAL ONLY chat (decision D-10.3): nothing is sent until the user says
  Send, every time, and the question names only how many files, their kind
  and size, never a file's name (the title-only rule of Local only alerts,
  ``notifier.LOCAL_ONLY_DROPPED_KEYS``). Its answer changes no setting.

Words and small readers only; the pipeline (``comm/inbound``) sends.
Everything that touches the disk or a row is BLOCKING: call it through
``asyncio.to_thread``.
"""

from __future__ import annotations

import json
from pathlib import Path, PurePath
from typing import Any

from ..core.ids import utcnow
from ..core.logging import get_logger

log = get_logger("comm.files_back")

#: Where the setting lives, as the phone hears it.
SETTING_WHERE = "Settings, Phone, Send finished files to your phone"
#: How long the question waits for an answer.
ASK_TTL_S = 24 * 3600.0
#: The most files one question waits on (a later answer's files join it).
MAX_WAITING = 10

SEND_WORDS = frozenset({"1", "send", "send it", "send them", "yes", "y", "ok"})
KEEP_WORDS = frozenset({"2", "keep", "keep it", "keep them", "keep it on the pc",
                        "keep them on the pc", "no", "n"})

#: The kind of file a sentence names, by extension: (one, many).
_KINDS: dict[str, tuple[str, str]] = {}
for _ext in (".xlsx", ".xlsm", ".xls", ".ods", ".csv", ".tsv"):
    _KINDS[_ext] = ("spreadsheet", "spreadsheets")
for _ext in (".docx", ".doc", ".odt", ".rtf", ".md", ".txt"):
    _KINDS[_ext] = ("document", "documents")
for _ext in (".pptx", ".ppt", ".odp"):
    _KINDS[_ext] = ("presentation", "presentations")
for _ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".heic"):
    _KINDS[_ext] = ("picture", "pictures")
_KINDS[".pdf"] = ("PDF", "PDFs")


def kind_word(name: str) -> str:
    """"spreadsheet", "document", "PDF", "picture", "presentation" or
    "file", for the event and the sentences."""
    return _KINDS.get(PurePath(str(name or "")).suffix.lower(), ("file", "files"))[0]


def size_words(n: int) -> str:
    """"48 KB" / "1.2 MB" (never "0 KB")."""
    n = max(0, int(n or 0))
    if n >= 1024 * 1024:
        mb = n / (1024 * 1024)
        return f"{mb:.1f} MB" if mb < 10 else f"{mb:.0f} MB"
    return f"{max(1, round(n / 1024))} KB"


def what_words(paths: list[str]) -> str:
    """"1 spreadsheet (48 KB)", "2 PDFs (1.2 MB)", "3 files (2.0 MB)": how
    many, their kind when they share one, and their size in all. Never a
    name. BLOCKING (it reads the sizes)."""
    total = 0
    kinds: set[tuple[str, str]] = set()
    for p in paths:
        kinds.add(_KINDS.get(PurePath(str(p)).suffix.lower(), ("file", "files")))
        try:
            total += Path(p).stat().st_size
        except OSError:
            pass
    n = len(paths)
    one, many = kinds.pop() if len(kinds) == 1 else ("file", "files")
    return f"{n} {one if n == 1 else many} ({size_words(total)})"


def destination_words(ch: Any, name: str) -> str:
    """The destination as a sentence names it: "Telegram", "Slack",
    "email", else its own name."""
    return str(getattr(ch, "display_name", "") or "") or str(name)


def _answers(config: Any) -> tuple[list[str], list[str]]:
    """The destinations whose phone answer was Send, and those whose answer
    was Keep it on the PC (each destination in one list at most)."""
    sent = [str(x) for x in (getattr(config, "phone_send_files_to", None) or [])]
    kept = [str(x) for x in (getattr(config, "phone_send_files_kept", None) or []) if str(x) not in sent]
    return sent, kept


def _follows(sent: list[str], kept: list[str]) -> str | None:
    """What ``phone_send_files`` reads while the phone's answers stand:
    "yes" once one destination said Send, "ask" while only Keeps are on
    record (each other destination is still asked), ``None`` for no answer."""
    if sent:
        return "yes"
    return "ask" if kept else None


def _setting(config: Any) -> str:
    return str(getattr(config, "phone_send_files", "ask") or "ask").strip().lower()


def _answers_stand(config: Any) -> bool:
    """Do the phone's answers decide? Only while the row reads the value the
    phone's answers last gave it (``phone_send_files_answered``). Not after
    a choice of the row in Settings or chat (the writer clears the mark),
    not after an Undo put another value back, and never under a Keep chosen
    there (every destination keeps)."""
    raw = _setting(config)
    said = str(getattr(config, "phone_send_files_answered", "") or "").strip().lower()
    return raw != "no" and bool(said) and raw == said


def consent(config: Any, destination: str) -> str:
    """"send", "keep" or "ask" for files back to ``destination`` (its
    registration name). With no phone answer on record, the setting
    ``phone_send_files`` decides for every destination (ask, yes = send,
    no = keep). Once the phone has answered, "no" keeps every destination;
    while the answers stand (:func:`_answers_stand`) each destination's own
    answer holds and one that never answered is asked; otherwise every
    destination is asked again. "send" only where the phone said Send, or
    under a yes chosen before any phone answer. Never raises (a doubt
    asks)."""
    try:
        raw = _setting(config)
        sent, kept = _answers(config)
        if raw == "no":
            return "keep"
        if _follows(sent, kept) is None:
            return "send" if raw == "yes" else "ask"
        if not _answers_stand(config):
            return "ask"
        where = str(destination)
        if where in kept:
            return "keep"
        return "send" if where in sent else "ask"
    except Exception:  # noqa: BLE001 — a doubt asks
        return "ask"


def one_or_them(paths: list[str]) -> str:
    return "it" if len(paths) == 1 else "them"


def first_time_question(what: str, where: str, n: int) -> str:
    """The first time on a destination: the card that names what goes back."""
    pronoun = "it" if n == 1 else "them"
    return (f"Send {what} to {where}? I made {pronoun} for your message. Your answer is kept "
            f"for {where} ({SETTING_WHERE} changes it).")


def local_only_question(what: str, where: str, n: int) -> str:
    """D-10.3: a Local only chat's files are not sent until the user says so;
    the question names how many, their kind and size, never a name."""
    verb = "it made was" if n == 1 else "it made were"
    pronoun = "it" if n == 1 else "them"
    return f"This chat is Local only, so the {what} {verb} not sent. Send {pronoun} through {where}?"


def answer_hint(n: int) -> str:
    """How to answer without buttons."""
    return f"Reply 1 to send or 2 to keep {'it' if n == 1 else 'them'} on the PC."


def ref_for(paths: list[str], *, local_only: bool, left: list[tuple[str, str]] | None = None) -> str:
    """The prompt's ``ref_id``: the files' absolute paths, whether the chat
    is Local only, and (never for Local only) what was left out and why."""
    return json.dumps({
        "paths": [str(p) for p in paths][:MAX_WAITING],
        "local_only": bool(local_only),
        "left": [] if local_only else [[str(a), str(b)] for a, b in (left or [])][:MAX_WAITING],
    })


def read_ref(ref: Any) -> tuple[list[str], bool, list[tuple[str, str]]]:
    """``(paths, local_only, left)`` out of a prompt's ``ref_id``. A ref
    that cannot be read is Local only with no files (nothing is sent)."""
    try:
        data = json.loads(str(ref or ""))
        paths = [str(p) for p in (data.get("paths") or []) if str(p or "").strip()]
        left = [(str(a), str(b)) for a, b in (data.get("left") or [])]
        return paths, data.get("local_only") is not False, left
    except Exception:  # noqa: BLE001
        return [], True, []


def prompt_fresh(prompt: Any) -> bool:
    """The question still waits: under a day old and one of its files still
    on this PC. BLOCKING (it stats the files, which may sit on a network
    share): the poller reaches it only through ``asyncio.to_thread``. Never
    raises (a doubt reads as stale)."""
    try:
        paths, _local, _left = read_ref(getattr(prompt, "ref_id", ""))
        if not any(Path(p).is_file() for p in paths):
            return False
        created = getattr(prompt, "created_at", None)
        if created is None:
            return True
        now = utcnow()
        if created.tzinfo is None:
            created = created.replace(tzinfo=now.tzinfo)
        return (now - created).total_seconds() <= ASK_TTL_S
    except Exception:  # noqa: BLE001
        return False


def decision_of(answer: Any) -> str:
    """"send", "keep" or "" for what the user answered."""
    said = " ".join(str(answer or "").split()).strip().lower().rstrip(".!")
    if said in SEND_WORDS or said.startswith("send"):
        return "send"
    if said in KEEP_WORDS or said.startswith("keep"):
        return "keep"
    return ""


def not_sent_line(left: list[tuple[str, str]], failed: list[tuple[str, str]], where: str,
                  *, local_only: bool = False) -> str:
    """What did not go, in words: failures by name and why, then files left
    out. In a Local only chat by count only (no names cross the service)."""
    if local_only:
        n = len(left) + len(failed)
        if not n:
            return ""
        return (f"{'1 file' if n == 1 else f'{n} files'} could not be sent to {where}; "
                f"{'it is' if n == 1 else 'they are'} on this PC.")
    lines = [f"I couldn't send {name} to {where}: {why}." for name, why in failed]
    if left:
        named = "; ".join(f"{name} ({why})" for name, why in left[:MAX_WAITING])
        lines.append(f"Also made, kept on this PC and not sent: {named}.")
    return "\n".join(lines)


#: The row's choices as Settings shows them (``settings/schema``).
_ASK_LABEL = "Ask each destination first"
_SEND_LABEL = "Send them"


def kept_reply(local_only: bool, n: int, where: str = "", config: Any = None) -> str:
    """The reply to Keep it on the PC. On the first-time question it says
    how to be asked again: the row's other choice than the one it shows now
    (any new choice there, but Keep them on this PC, asks every destination
    again, :func:`_answers_stand`)."""
    if local_only:
        return f"OK: {'it stays' if n == 1 else 'they stay'} on this PC."
    asked = f" when you ask from {where}" if where else ""
    pick = _ASK_LABEL if config is not None and _setting(config) == "yes" else _SEND_LABEL
    return (f"OK: finished files stay on this PC from now on{asked}. To be asked again, "
            f"choose {pick} in {SETTING_WHERE}.")


def keep_choice(platform: Any, decision: str, destination: str) -> None:
    """Keep the phone's answer to the first-time question FOR ITS
    DESTINATION (BLOCKING): Send in ``phone_send_files_to``, Keep in
    ``phone_send_files_kept``, written first (a Keep on Slack never stops a
    Telegram that said Send; a Send never covers a destination it did not
    name). Under a Keep chosen in Settings nothing is kept (the answer was
    to a question asked before it). Answers no longer in force (a choice in
    Settings or chat since, or an Undo: :func:`_answers_stand`) are not
    built on: this answer starts the record again. Then
    ``phone_send_files`` follows the answers (:func:`_follows`) through the
    settings writer (a ledger row with Undo when it changes) when the daemon
    has one, else on the config, and the value is kept beside the records
    (``phone_send_files_answered``, written with them, first). Never
    raises."""
    try:
        cfg = getattr(platform, "config", None)
        if cfg is None:
            return
        raw = _setting(cfg)
        if raw == "no":
            # A Keep chosen in Settings since this question was asked: the
            # answer covers its own files only and changes no setting.
            return
        sent, kept = _answers(cfg)
        if not _answers_stand(cfg):
            sent, kept = [], []
        where = str(destination or "")
        sent = [x for x in sent if x != where]
        kept = [x for x in kept if x != where]
        if where:
            (sent if decision == "send" else kept).append(where)
        value = _follows(sent, kept) or raw
        cfg.phone_send_files_to = sent
        cfg.phone_send_files_kept = kept
        cfg.phone_send_files_answered = value if (sent or kept) else ""
        home = getattr(cfg, "home", None)
        if home is not None:
            from ..core.config import persist_config_values

            persist_config_values(home, {"phone_send_files_to": sent, "phone_send_files_kept": kept,
                                         "phone_send_files_answered": cfg.phone_send_files_answered})
        writer = getattr(platform, "config_writer", None)
        if writer is not None:
            writer.apply({"phone_send_files": value}, actor="phone")
            return
        cfg.phone_send_files = value
    except Exception:  # noqa: BLE001 — the answer still stands for these files
        log.warning("files back: the phone's answer could not be kept", exc_info=True)


def turn_folder(platform: Any, workspace_dir: str, project_id: str) -> list[Path]:
    """The folder a phone chat turn's tools worked in (BLOCKING), read the
    way the turn picks it (``chat_turn._resolve_tool_workspace``): the
    chat's own folder when it has one it may use, else its project's, else
    the chat's scratch folder (``<home>/uploads``). Only a file made inside
    it goes back. Never raises (a doubt: the scratch folder)."""
    uploads = Path(platform.config.home) / "uploads"
    try:
        ws = str(workspace_dir or "").strip()
        if ws:
            from ..core.fs_policy import usable_workspace_root

            return [Path(ws)] if usable_workspace_root(ws) else [uploads]
        pid = str(project_id or "").strip()
        if pid:
            from ..core.db import session_scope
            from ..core.models import Project

            with session_scope(platform.engine) as db:
                row = db.get(Project, pid)
                root = str(getattr(row, "root", "") or "").strip() if row is not None else ""
            if root and Path(root).is_dir():
                return [Path(root)]
    except Exception:  # noqa: BLE001
        log.warning("files back: the chat's folder could not be read", exc_info=True)
    return [uploads]


def work_is_local_only(engine: Any, thread_id: str, session_id: str = "") -> bool:
    """Is this work Local only (BLOCKING)? The saved chat's switch
    (``egress.setup_says_local_only``, an app's hold included) or the run
    created Local only (``options.local_only``): the readings Local only
    alerts use (``core/events.event_is_local_only``), read STRICTLY here: a
    read that fails is Local only, so nothing leaves on a doubt (the alert's
    reading says False then, which is right for a title line and wrong for a
    file). Never raises."""
    tid = str(thread_id or "").strip()[:80]
    sid = str(session_id or "").strip()
    if engine is None:
        return bool(tid or sid)
    try:
        from sqlmodel import select

        from ..core.db import session_scope
        from ..core.models import ChatThreadRecord, Session
        from ..providers.egress import setup_says_local_only

        with session_scope(engine) as db:
            if tid:
                setup = db.exec(select(ChatThreadRecord.setup_json).where(ChatThreadRecord.id == tid)).first()
                if setup_says_local_only(setup):
                    return True
            if sid:
                raw = db.exec(select(Session.options_json).where(Session.id == sid)).first()
                opts = json.loads(raw) if isinstance(raw, str) and raw else {}
                if isinstance(opts, dict) and opts.get("local_only") is True:
                    return True
        return False
    except Exception:  # noqa: BLE001 — a doubt keeps the files here
        log.warning("files back: whether the work is Local only could not be read", exc_info=True)
        return True
