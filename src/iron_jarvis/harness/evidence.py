"""Before and after pictures under a coding reply (R2.15, phase 27-a of
``docs/next-leaps/07-coding-github-evidence-scoreboard-sealed-room.md``,
idea 27 "Show me it works").

When a Chat › Code reply changes a screen file (a ``.tsx``, ``.vue``,
``.html``, ``.css`` … — :func:`screen_file`) and the user has a page open in
the session's Browser, two pictures can sit under the reply: the page BEFORE
the change and AFTER it. Iron Jarvis takes them itself, from a real run of
the page, so they are evidence, never a claim.

WHEN (decision D-27.1, as the owner settled it: only when you ask). A turn
takes pictures by itself only when :func:`takes` says so: the user's message
asks ("show me it works", :func:`asks`), or the setting ``reply_pictures``
("Take before and after pictures by themselves") is on — and only for a turn
a PERSON started, with a page open. Otherwise a reply that changed a screen
file is only MARKED (``{"kind": "pictures", "screen": true, "items": []}``,
with ``beforeNote``: the true reason it took none, :func:`no_before_words`)
and the screen offers Show me under it, which takes the after picture then
(:func:`take_after_now`). Show me pictures the page AS IT IS NOW, so it is
offered only under the session's NEWEST reply, and never replaces an after
picture a reply already has (the route ``routes/build_evidence`` refuses
anything else in words). The setting starts not set (off) and Show me offers
it once (:func:`offer_due`), naming its cost: an invisible browser as each
reply with the page open begins, and again when it changed a screen file.

BEFORE. The picture of the page as it is starts as the reply begins
(:func:`start_before`, called by ``start_build_turn`` once the turn has
started: a real Edge picture can take longer than a write may be held, and
the first one after the app starts is the slowest). It is held in memory
only. The first write of a screen file in the turn waits for it
(:func:`before_write`, called by the tool registry in the one place a file
write is journaled, BEFORE the write lands, so both chat lanes and every
helper get it), at most :data:`BEFORE_WAIT_S`, and keeps it. Still not
there: it is ended (never kept: it could show the change), the write goes
ahead and the record says why. One that failed before the write (a page not
up yet as the reply began) is taken again then, within the same hold. Calls
in parallel wait on the turn's lock, so no screen write lands before the
picture. A reply that writes no screen file keeps nothing, and the turn's
end ends a picture still being taken (:func:`stop_before`).

AFTER. When the reply ends (both lanes' done frame, :func:`at_end`), the same
address, after the page settles.

KEPT. ``<home>/reply-evidence/<session>/<turn>/`` holds ``before.png``,
``after.png`` and ``evidence.json`` (the address as host and path only, never
its query; the times; each picture's hash). A session keeps the pictures of
its newest :data:`KEEP_REPLIES` replies (decision D-27.2); the folder goes to
the app's trash with the session (:func:`trash_session_evidence`). The
pictures are never pruned by the page screenshots' own count.

The pictures go to the USER only: they are never added to a model's context,
so Local only is untouched. They are taken through the agent browser's own
one-shot (``page_browser.AgentBrowsers.one_shot``): its address rules on
every navigation, frames included, the Local only proxy, the Job; a page
that moved away gives no picture. Nothing here blocks the event loop; every
failure is a plain sentence, never a made-up picture.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import re
import shutil
import uuid
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any

from ..core.ids import utcnow
from ..core.logging import get_logger

log = get_logger(__name__)

#: The folder (under the app's home) the pictures live in.
DIRNAME = "reply-evidence"
#: Decision D-27.2: the newest replies per session whose pictures are kept.
KEEP_REPLIES = 40
#: The before picture holds the first screen write at most this long.
BEFORE_WAIT_S = 8.0
#: The before picture started as the reply begins (:func:`start_before`),
#: at most this long (then it is given up; the first screen write tries once
#: more within its own hold).
EARLY_LIMIT_S = 30.0
#: The after picture at the reply's end, at most (it holds the done frame).
#: Review: a real Edge one-shot measured 4-15 s on this PC under load, most
#: of it starting the browser, and 2 of 4 afters missed 20 s; the press's 30.
AFTER_WAIT_S = 30.0
#: A Show me press, at most.
PRESS_WAIT_S = 30.0
#: How long the page may draw before its picture is taken.
SETTLE_MS = 1500
WIDTH, HEIGHT = 1280, 800
#: The two pictures of a reply, in the order they are shown.
ITEM_IDS: tuple[str, ...] = ("before", "after")
LABELS = {"before": "Before", "after": "After"}
#: The record's ``kind`` (27-b adds "steps").
KIND = "pictures"
#: The marker that the automatic setting was offered (once, ever).
OFFERED_MARK = ".offered"

#: The kinds of file that draw a screen. A fixed list, by suffix only.
SCREEN_SUFFIXES = frozenset({
    ".tsx", ".jsx", ".vue", ".svelte", ".astro", ".html", ".htm", ".css", ".scss",
    ".sass", ".less", ".styl", ".ejs", ".hbs", ".handlebars", ".njk", ".pug",
    ".liquid", ".erb", ".cshtml", ".razor",
})

#: The tools that write, move or edit a file (what the registry journals for
#: a coding turn). Their paths are read from their own arguments.
WRITERS = frozenset({
    "write_file", "edit_file", "rename_file", "multi_edit", "apply_patch", "notebook_edit",
})

# ---- the words (VOCABULARY.md: "before and after pictures", "Show me") ----
SAME_WORDS = "The page looks the same before and after."
NOT_ASKED_WORDS = (
    "No before picture: one is taken only when your message asks for it "
    "(\"show me it works\") or when pictures by themselves are on."
)
#: A turn that asked (or the setting is on) with no page open when it began.
NO_PAGE_WORDS = "No before picture: no page was open in the Browser when the reply began."
#: A turn Iron Jarvis started itself (the night, a "carry on" turn).
STARTED_WORDS = "No before picture: Iron Jarvis started this reply itself, not a message of yours."
#: A Show me press on a line that names no reason of its own (a copied line).
PRESS_ONLY_WORDS = "No before picture: Show me takes only the after picture."
#: A before picture the record names that is no longer on this PC.
BEFORE_GONE_WORDS = "No before picture: the one taken then is no longer kept."
BEFORE_SLOW_WORDS = "No before picture: the page did not answer in time."
UNSEEN_WORDS = (
    "No before picture: the screen file was changed by a step Iron Jarvis does not take "
    "a picture before."
)
NO_BROWSER_WORDS = "there is no Microsoft Edge or Google Chrome on this PC"
BRANCHED_WORDS = "This reply's pictures stay with the session it was branched from."
#: Show me on a reply that already has its after picture: the one taken then
#: is kept, never replaced by a picture of the page as it is now.
HAS_AFTER_WORDS = "This reply already has its after picture, kept as it was taken."

#: "show me it works", "show me that it works", "show me before and after",
#: "before and after pictures" — linear, bounded, case-insensitive. The last
#: one names the pictures, never a thing to build ("add a before-and-after
#: screenshots section" asks for a section, not for pictures of the page).
_ASK_RX = re.compile(
    r"\bshow\s+me\s+(?:that\s+)?it\s+works\b"
    r"|\bshow\s+me\s+(?:the\s+)?before\s+and\s+after\b"
    r"|\bbefore[\s-]+and[\s-]+after\s+(?:pictures?|screenshots?|shots?)\b"
    r"(?!\s+(?:section|slider|gallery|component|carousel|grid|block|view|page|panel|card|feature|module|widget)s?\b)",
    re.IGNORECASE,
)
#: How much of a message is read for the words.
ASK_SCAN_CHARS = 4000

_PATCH_PATH_RX = re.compile(r"^\*\*\*\s+(?:Add|Update|Delete)\s+File:\s*(.+?)\s*$|^\*\*\*\s+Move\s+to:\s*(.+?)\s*$",
                            re.MULTILINE)


class PictureProblem(Exception):
    """A picture that could not be taken, in words."""


class Refused(PictureProblem):
    """Show me refused, nothing kept; the words are a whole sentence, said as
    they are."""


# --------------------------------------------------------------------------- #
# pure rules
# --------------------------------------------------------------------------- #


def screen_file(path: Any) -> bool:
    """Is ``path`` a file that draws a screen (by its kind alone)?"""
    try:
        return PurePath(str(path or "").strip()).suffix.lower() in SCREEN_SUFFIXES
    except (TypeError, ValueError):
        return False


def asks(text: Any) -> bool:
    """Does the user's message ask for the pictures ("show me it works")?"""
    return bool(_ASK_RX.search(str(text or "")[:ASK_SCAN_CHARS]))


def no_before_words(setting: Any, text: Any, address: str, *, by_person: bool) -> str:
    """Why this turn takes no before picture by itself, as the reply says it
    ("" when it does take one: :func:`takes`). The true reason, in order: a
    turn Iron Jarvis started; a message that did not ask with the setting not
    on; no page open in the Browser when the reply began."""
    if not by_person:
        return STARTED_WORDS
    if not (setting is True or asks(text)):
        return NOT_ASKED_WORDS
    if not address:
        return NO_PAGE_WORDS
    return ""


def takes(setting: Any, text: Any, address: str, *, by_person: bool) -> bool:
    """Does this turn take its pictures by itself? Only with a page open, only
    for a turn a person started, and only when the message asks for them or
    the setting is ON (``True``; not set = off, decision D-27.1)."""
    return not no_before_words(setting, text, address, by_person=by_person)


def written_paths(name: str, args: Any) -> list[str]:
    """The paths a file-writing call names (its own arguments; for
    ``apply_patch`` the patch's file lines). Pure; never raises."""
    if name not in WRITERS or not isinstance(args, dict):
        return []
    out: list[str] = []
    for key in ("path", "new_path", "notebook_path", "file_path"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            out.append(value.strip())
    if name == "apply_patch":
        for key in ("patch", "input"):
            text = args.get(key)
            if isinstance(text, str):
                for m in _PATCH_PATH_RX.finditer(text[:200_000]):
                    p = (m.group(1) or m.group(2) or "").strip()
                    if p:
                        out.append(p)
    return out[:50]


def where_of(address: str) -> str:
    """The address as the user reads it: host[:port] and path, never the
    query or fragment (they can carry tokens)."""
    from .page_browser import where

    return where(address)


# --------------------------------------------------------------------------- #
# the turn's state
# --------------------------------------------------------------------------- #


@dataclass
class TurnEvidence:
    """One coding turn's pictures (armed by ``routes/build.start_build_turn``
    like the Browser's address; the turn's tasks and helpers copy it)."""

    home: Path
    thread_id: str
    turn_id: str
    address: str = ""
    take: bool = False
    #: Why the turn takes no before picture by itself (:func:`no_before_words`;
    #: "" when it takes one, or when the caller named no reason).
    why: str = ""
    screen: list[str] = field(default_factory=list)
    #: Each screen file a write named, as it was on disk BEFORE that first
    #: write (:func:`_fingerprint`): at the end, a file that is just as it was
    #: (the write failed) is no change, so the reply is not marked for it.
    seen: dict[str, Any] = field(default_factory=dict)
    before: "dict[str, Any] | None" = None
    before_note: str = ""
    tried_before: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    #: The before picture started as the reply began (:func:`start_before`):
    #: a task giving ``(png, frames left empty)``, held in memory only until
    #: the first screen write takes it.
    early: "asyncio.Task[tuple[bytes, list[str]]] | None" = None


TURN: "ContextVar[TurnEvidence | None]" = ContextVar("ij_reply_evidence", default=None)


def arm(home: Any, thread_id: str, turn_id: str, address: str, *, take: bool,
        why: str = "") -> "Token[TurnEvidence | None]":
    """Arm this turn's pictures (``address`` = the Browser's local address or
    "" for none; ``take`` = :func:`takes`; ``why`` = :func:`no_before_words`,
    the reason a turn that takes none says under Show me)."""
    take = bool(take and address)
    return TURN.set(TurnEvidence(
        home=Path(home), thread_id=str(thread_id or ""), turn_id=str(turn_id or ""),
        address=str(address or ""), take=take, why="" if take else str(why or "")[:300],
    ))


def reset(token: "Token[TurnEvidence | None]") -> None:
    try:
        TURN.reset(token)
    except (ValueError, LookupError):  # a token from another context
        TURN.set(None)


def _note_screens(st: TurnEvidence, paths: list[str]) -> None:
    for p in paths:
        if p not in st.screen and len(st.screen) < 20:
            st.screen.append(p)


#: A file whose state could not be read: counted as changed (never hides one).
_UNKNOWN = ("?",)


def _fingerprint(path: "Path | None") -> tuple[Any, ...]:
    """BLOCKING (one stat): a file's state — ``(True, size, mtime_ns)``,
    ``(False,)`` when there is none, :data:`_UNKNOWN` when it cannot be read
    or the write named no place it can be found."""
    if path is None:
        return _UNKNOWN
    try:
        s = path.stat()
        return (True, s.st_size, s.st_mtime_ns)
    except FileNotFoundError:
        return (False,)
    except (OSError, ValueError):
        return _UNKNOWN


def _state_before(raw: str, workspace: Any) -> tuple[str, "Path | None", tuple[Any, ...]]:
    """BLOCKING: ``(key, path, state)`` of a file a write names, BEFORE it
    lands. The path is the tools' own reading (``tools/base.safe_path``: the
    call's workspace, its refusals — another computer's path is never even
    opened); one it refuses, or no workspace, gives no path and an unknown
    state (counted as changed)."""
    from ..tools.base import safe_path

    if not workspace:
        return raw, None, _UNKNOWN
    try:
        path = safe_path(Path(workspace), raw)
    except Exception:  # noqa: BLE001 — the write itself says why it is refused
        return raw, None, _UNKNOWN
    return str(path), path, _fingerprint(path)


def _changed_screens(st: TurnEvidence, documents: list[str]) -> list[str]:
    """BLOCKING (a stat per file): the screen files the reply REALLY changed —
    the ones its own results reported (``documents``), and each one a write
    named whose state is no longer what it was before that write. A write
    that failed leaves its file as it was: no change, so no mark."""
    out = [d for d in documents if screen_file(d)]
    for key, (path, was) in list(st.seen.items()):
        if was == _UNKNOWN or _fingerprint(path) != was:
            out.append(key)
    return out


# --------------------------------------------------------------------------- #
# taking a picture
# --------------------------------------------------------------------------- #


def _edge() -> str:
    """The Edge or Chrome a picture is taken with ("" when there is none)."""
    from .page_shot import browser_executable

    return browser_executable()


async def _take(address: str, *, limit_s: float) -> tuple[bytes, list[str]]:
    """ONE picture of ``address`` through the agent browser's one-shot:
    ``(png, frames left empty)``. Raises :class:`PictureProblem` in words."""
    from . import page_browser as PB

    edge = await asyncio.to_thread(_edge)
    if not edge:
        raise PictureProblem(NO_BROWSER_WORDS)
    try:
        shot = await PB.BROWSERS.one_shot(
            address, edge=edge, preview=address, width=WIDTH, height=HEIGHT,
            wait_ms=SETTLE_MS, limit_s=limit_s,
        )
    except PB.BrowserProblem as exc:
        raise PictureProblem(str(exc).rstrip(".")) from None
    if not shot.picture:
        raise PictureProblem("the browser gave no picture")
    return shot.picture, list(shot.blocked)


def _plain(words: str) -> str:
    """A sentence from a reason: first letter low (it follows a colon), one
    full stop, bounded."""
    w = " ".join(str(words or "").split())[:300].rstrip(".")
    if w[:1].isupper() and not w[:2].isupper():
        w = w[:1].lower() + w[1:]
    return w + "." if w else "it could not be taken."


# --------------------------------------------------------------------------- #
# where the pictures are kept
# --------------------------------------------------------------------------- #


def _clean_id(raw: Any) -> str:
    """A turn id as a folder name: the turn route's own characters (letters,
    digits, ``-``, ``_``, ``.``), at most 80."""
    return "".join(c for c in str(raw or "") if (c.isascii() and c.isalnum()) or c in "-_.")[:80]


def session_folder(home: Any, thread_id: str) -> Path:
    """``<home>/reply-evidence/<session>`` (the page screenshots' own naming)."""
    from .page_shot import _session_dirname

    return Path(home) / DIRNAME / _session_dirname(thread_id)


def reply_folder(home: Any, thread_id: str, turn_id: str) -> "Path | None":
    """``<session folder>/<turn>``; None for a turn id that is not one."""
    turn = _clean_id(turn_id)
    # Never another folder: the id exactly as given, and no leading or
    # trailing dot (".", "..", and a name Windows would cut short).
    if not turn or turn != str(turn_id or "") or turn.startswith(".") or turn.endswith("."):
        return None
    return session_folder(home, thread_id) / turn


def picture_file(home: Any, thread_id: str, turn_id: str, item_id: str) -> "Path | None":
    """The kept picture ``item_id`` of a reply, or None (an unknown id, a
    picture no longer kept)."""
    if item_id not in ITEM_IDS:
        return None
    folder = reply_folder(home, thread_id, turn_id)
    if folder is None:
        return None
    path = folder / f"{item_id}.png"
    return path if path.is_file() else None


def _read_json(folder: Path) -> dict[str, Any]:
    try:
        data = json.loads((folder / "evidence.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(home: Any, thread_id: str, turn_id: str, item_id: str, png: bytes, address: str,
          blocked: "list[str] | None" = None) -> dict[str, Any]:
    """BLOCKING: keep one picture (replacing that item's older one), update
    ``evidence.json``, prune the session to its newest replies. Returns the
    item as the reply line keeps it."""
    folder = reply_folder(home, thread_id, turn_id)
    if folder is None:
        raise PictureProblem("the reply could not be named")
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"{item_id}.png"
    tmp = folder / f".{item_id}-{uuid.uuid4().hex[:6]}.tmp"
    tmp.write_bytes(png)
    os.replace(tmp, dest)
    digest = hashlib.sha256(png).hexdigest()
    at = utcnow().isoformat()
    item = {"id": item_id, "label": LABELS[item_id], "where": where_of(address), "at": at,
            "sha": digest[:16]}
    data = _read_json(folder)
    items = [i for i in (data.get("items") or []) if isinstance(i, dict) and i.get("id") != item_id]
    items.append({**item, "sha256": digest, "file": str(dest),
                  **({"blocked": [str(b)[:300] for b in blocked[:5]]} if blocked else {})})
    items.sort(key=lambda i: ITEM_IDS.index(i["id"]) if i.get("id") in ITEM_IDS else 9)
    data.update({"turn": turn_id, "where": where_of(address), "items": items, "saved_at": at,
                 "folder": str(folder)})
    (folder / "evidence.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    prune(home, thread_id)
    return item


def _write_note(home: Any, thread_id: str, turn_id: str, note: str) -> None:
    folder = reply_folder(home, thread_id, turn_id)
    if folder is None or not folder.is_dir():
        return
    data = _read_json(folder)
    data["note"] = note
    with contextlib.suppress(OSError):
        (folder / "evidence.json").write_text(json.dumps(data, indent=1), encoding="utf-8")


def _drop_reply(home: Any, thread_id: str, turn_id: str) -> None:
    """BLOCKING: remove THIS turn's own folder (a before picture it took a
    moment ago for a write that then failed: nothing names it, and it shows
    no change that happened). Never raises."""
    folder = reply_folder(home, thread_id, turn_id)
    if folder is not None and folder.is_dir():
        shutil.rmtree(folder, ignore_errors=True)


def prune(home: Any, thread_id: str, keep: int = KEEP_REPLIES) -> list[str]:
    """BLOCKING: keep the pictures of the session's newest ``keep`` replies
    (by when each was last saved); the older ones are removed. Never raises."""
    root = session_folder(home, thread_id)
    removed: list[str] = []
    try:
        rows = []
        for d in root.iterdir():
            if not d.is_dir():
                continue
            mark = d / "evidence.json"
            try:
                rows.append(((mark if mark.exists() else d).stat().st_mtime_ns, d.name, d))
            except OSError:
                continue
        rows.sort(reverse=True)
        for _t, name, d in rows[keep:]:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(name)
    except OSError:
        pass
    return removed


def trash_session_evidence(home: Any, thread_id: str) -> str:
    """BLOCKING: MOVE a deleted session's pictures (they can show another
    program's data) to ``<home>/trash/<stamp>/reply-evidence/<session>`` —
    never delete them. Returns where they went ("" when there were none or
    they could not be moved: said in the log). Never raises."""
    from ..maintenance import TRASH_DIRNAME, trash_stamp

    src = session_folder(home, thread_id)
    try:
        if not src.is_dir():
            return ""
        dest = Path(home) / TRASH_DIRNAME / trash_stamp() / DIRNAME / src.name
        if dest.exists():
            dest = dest.with_name(f"{src.name}-{uuid.uuid4().hex[:6]}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))
        return str(dest)
    except OSError:
        log.warning("a deleted coding session's before and after pictures could not be moved to the trash",
                    exc_info=True)
        return ""


# --------------------------------------------------------------------------- #
# the record on the reply line
# --------------------------------------------------------------------------- #


def _record(items: list[dict[str, Any]], *, before_note: str, folder: "Path | None",
            after_note: str = "") -> dict[str, Any]:
    by_id = {i["id"]: i for i in items}
    notes: list[str] = []
    if "before" not in by_id and before_note:
        notes.append(before_note)
    if after_note:
        notes.append(after_note)
    if "before" in by_id and "after" in by_id and by_id["before"].get("sha") == by_id["after"].get("sha"):
        notes.append(SAME_WORDS)
    out: dict[str, Any] = {
        "kind": KIND, "screen": True,
        "items": [{k: by_id[i][k] for k in ("id", "label", "where", "at", "sha")} for i in ITEM_IDS if i in by_id],
        "note": " ".join(notes),
    }
    if before_note:
        out["beforeNote"] = before_note
    if folder is not None and items:
        out["folder"] = str(folder)
    return out


def copied_lines(lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A BRANCHED session's copy of the conversation: the pictures stay in
    the folder of the session they were taken in, so a copied reply keeps
    only the Show me mark and says where its pictures are. Pure."""
    out = []
    for m in lines:
        rec = saved_record(m.get("evidence")) if isinstance(m, dict) else None
        if rec is not None and rec["items"]:
            m = {**m, "evidence": {"kind": KIND, "screen": True, "items": [], "note": BRANCHED_WORDS}}
        out.append(m)
    return out


def saved_record(raw: Any) -> "dict[str, Any] | None":
    """The reply line's ``evidence``, whitelisted (the lane's done frame or a
    press). None for anything else."""
    if not isinstance(raw, dict) or raw.get("kind") != KIND or raw.get("screen") is not True:
        return None
    items = []
    for it in raw.get("items") or []:
        if not isinstance(it, dict) or it.get("id") not in ITEM_IDS:
            continue
        items.append({
            "id": it["id"], "label": LABELS[it["id"]], "where": str(it.get("where") or "")[:200],
            "at": str(it.get("at") or "")[:40], "sha": str(it.get("sha") or "")[:16],
        })
    out: dict[str, Any] = {"kind": KIND, "screen": True, "items": items[:2],
                           "note": str(raw.get("note") or "")[:600]}
    if raw.get("beforeNote"):
        out["beforeNote"] = str(raw["beforeNote"])[:300]
    if raw.get("folder"):
        out["folder"] = str(raw["folder"])[:500]
    return out


# --------------------------------------------------------------------------- #
# the seams: the registry (before), the lanes' done frame (after), a press
# --------------------------------------------------------------------------- #


def _retrieved(task: "asyncio.Task[Any]") -> None:
    """A done callback: an early picture's failure is read (the write that
    takes it says it), so the loop never logs it as never retrieved."""
    if not task.cancelled():
        task.exception()


async def _early_take(address: str) -> tuple[bytes, list[str]]:
    return await asyncio.wait_for(_take(address, limit_s=EARLY_LIMIT_S), EARLY_LIMIT_S + 5)


def start_before(*, local_only: bool = True) -> bool:
    """Start this turn's before picture NOW, as the reply begins (review: a
    real Edge one-shot can take longer than :data:`BEFORE_WAIT_S`, the most a
    screen write is held). Held in memory only: the first screen write takes
    and keeps it (:func:`before_write`); a reply that writes no screen file
    keeps nothing. ``local_only``: the turn's Local only scope (the lane sets
    the turn's own only later, inside the turn). Call on the loop, in the
    armed turn's context, once the turn has started. Returns whether a
    picture was started."""
    st = TURN.get()
    if st is None or not (st.take and st.address) or st.early is not None or st.tried_before:
        return False
    from ..providers import egress as _egress

    with _egress.local_only_scope(local_only is True):
        st.early = asyncio.get_running_loop().create_task(
            _early_take(st.address), name=f"reply-before-picture-{st.turn_id}",
        )
    st.early.add_done_callback(_retrieved)
    return True


def stop_before(st: "TurnEvidence | None" = None) -> None:
    """The turn ended (its end, a Stop, a failure): a before picture still
    being taken that no screen write took is ended. Never raises."""
    st = st if st is not None else TURN.get()
    early = st.early if st is not None else None
    if early is not None and not early.done():
        early.cancel()


async def _before_picture(st: TurnEvidence) -> tuple[bytes, list[str]]:
    """The before picture for the turn's first screen write: the one started
    as the reply began, waited for at most :data:`BEFORE_WAIT_S` (then it is
    ended, never kept: it could show the change); else — none started, or it
    failed before this write (a page not up yet as the reply began) — taken
    now, within the same hold."""
    early = st.early
    if early is not None and not (early.done() and (early.cancelled() or early.exception() is not None)):
        try:
            return await asyncio.wait_for(early, BEFORE_WAIT_S)
        except asyncio.CancelledError:
            me = asyncio.current_task()
            if me is not None and me.cancelling():
                raise  # the write itself is stopped
            raise PictureProblem("it was stopped") from None  # only the picture was
    return await asyncio.wait_for(_take(st.address, limit_s=BEFORE_WAIT_S), BEFORE_WAIT_S)


async def before_write(name: str, args: Any, workspace: Any = None) -> None:
    """The tool registry's hook, BEFORE a file write lands: the first write
    of a screen file in an armed turn waits for the before picture (the one
    started as the reply began, :func:`_before_picture`; at most
    :data:`BEFORE_WAIT_S`; then the write goes ahead and the record says
    why). Each screen file's state before its first write is kept, so a
    write that then fails is no change (:func:`_changed_screens`).
    ``workspace``: the call's own folder (a relative path is under it).
    Cheap and silent when nothing is armed. Never raises (a Stop still
    cancels)."""
    st = TURN.get()
    if st is None or name not in WRITERS:
        return
    try:
        screens = [p for p in written_paths(name, args) if screen_file(p)]
    except Exception:  # noqa: BLE001 — a hook never fails a write
        return
    if not screens:
        return
    _note_screens(st, screens)
    try:
        for raw in screens:
            if len(st.seen) >= 50:  # past 50 files: counted as changed, unread
                st.seen.setdefault("*", (None, _UNKNOWN))
                break
            key, path, was = await asyncio.to_thread(_state_before, raw, workspace)
            st.seen.setdefault(key, (path, was))
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a hook never fails a write
        log.warning("a screen file's state before a write could not be read", exc_info=True)
    if not (st.take and st.address):
        return
    # Every screen write waits here while the before is taken, so none lands
    # first (calls run in parallel, a helper's too).
    async with st.lock:
        if st.tried_before:
            return
        st.tried_before = True
        try:
            png, blocked = await _before_picture(st)
            st.before = await asyncio.to_thread(
                _save, st.home, st.thread_id, st.turn_id, "before", png, st.address, blocked,
            )
        except (TimeoutError, asyncio.TimeoutError):
            st.before_note = BEFORE_SLOW_WORDS
        except PictureProblem as exc:
            st.before_note = f"No before picture: {_plain(str(exc))}"
        except Exception:  # noqa: BLE001 — said, never a made-up picture
            log.warning("a reply's before picture failed", exc_info=True)
            st.before_note = "No before picture: it could not be taken."


async def at_end(documents: Any = None) -> "dict[str, Any] | None":
    """The reply's record for the lanes' done frame (MIRROR NOTE: ``routes/
    chat.py``'s stream and ``chat_turn.py``'s POST answer put it on the same
    key, ``evidence``): None when nothing is armed or no screen file
    changed; the Show me mark when the turn does not take pictures by
    itself; else the after picture, taken now. Never raises (a Stop still
    cancels)."""
    st = TURN.get()
    if st is None:
        return None
    try:
        docs = [d for d in (documents or []) if isinstance(d, str)]
        async with st.lock:  # a before still being taken finishes first
            pass
        if not st.tried_before:
            # No screen write took the picture started as the reply began: it
            # is not kept (with no write seen, it could show a change made by
            # a command), and one still being taken is ended.
            stop_before(st)
        if not await asyncio.to_thread(_changed_screens, st, docs):
            # No screen file really changed (a write that failed leaves it as
            # it was): no mark, no after. A before taken for that write shows
            # nothing that happened, so it is not kept.
            if st.before is not None:
                await asyncio.to_thread(_drop_reply, st.home, st.thread_id, st.turn_id)
            return None
        if not (st.take and st.address):
            mark: dict[str, Any] = {"kind": KIND, "screen": True, "items": [], "note": ""}
            if st.why:
                # The true reason there is no before picture, for Show me.
                mark["beforeNote"] = st.why
            return mark
        if not st.tried_before:
            st.before_note = UNSEEN_WORDS
        items = [st.before] if st.before else []
        after_note = ""
        try:
            png, blocked = await asyncio.wait_for(_take(st.address, limit_s=AFTER_WAIT_S), AFTER_WAIT_S)
            items.append(await asyncio.to_thread(
                _save, st.home, st.thread_id, st.turn_id, "after", png, st.address, blocked,
            ))
        except (TimeoutError, asyncio.TimeoutError):
            after_note = "No after picture: the page did not answer in time."
        except PictureProblem as exc:
            after_note = f"No after picture: {_plain(str(exc))}"
        except Exception:  # noqa: BLE001
            log.warning("a reply's after picture failed", exc_info=True)
            after_note = "No after picture: it could not be taken."
        folder = reply_folder(st.home, st.thread_id, st.turn_id)
        rec = _record(items, before_note=st.before_note, folder=folder, after_note=after_note)
        if items:
            await asyncio.to_thread(_write_note, st.home, st.thread_id, st.turn_id, rec["note"])
        return rec
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — the reply ends; the pictures are only missing
        log.warning("a reply's pictures could not be recorded", exc_info=True)
        return None


def has_after(raw: Any) -> bool:
    """Does a reply line's ``evidence`` already hold its after picture?"""
    rec = saved_record(raw)
    return bool(rec and any(i["id"] == "after" for i in rec["items"]))


async def take_after_now(home: Any, thread_id: str, turn_id: str, address: str,
                         current: Any = None, *, still: Any = None) -> dict[str, Any]:
    """Show me: the after picture of ``address`` now, for reply ``turn_id``
    (its before, when one was taken, is kept). NEVER replaces an after
    picture the reply already has (:data:`HAS_AFTER_WORDS`): that one shows
    the page as the reply left it. ``still`` (optional, async, no
    arguments): asked once the picture is taken and BEFORE it is kept — a
    sentence back refuses it (:class:`Refused`) and nothing is kept (a reply
    that started meanwhile). Returns the reply line's new ``evidence``.
    Raises :class:`PictureProblem` in words (nothing changed)."""
    if reply_folder(home, thread_id, turn_id) is None:
        raise PictureProblem("that reply could not be named")
    if has_after(current):
        raise Refused(HAS_AFTER_WORDS)
    try:
        png, blocked = await asyncio.wait_for(_take(address, limit_s=PRESS_WAIT_S), PRESS_WAIT_S + 5)
    except (TimeoutError, asyncio.TimeoutError):
        raise PictureProblem("the page did not answer in time") from None
    if still is not None:
        words = await still()
        if words:
            raise Refused(str(words))
    after = await asyncio.to_thread(_save, home, thread_id, turn_id, "after", png, address, blocked)
    old = saved_record(current) or {}
    before = next((i for i in old.get("items") or [] if i.get("id") == "before"), None)
    before_note = ""
    if before is not None and await asyncio.to_thread(picture_file, home, thread_id, turn_id, "before") is None:
        before = None  # no longer kept: never shown as if it were
        before_note = BEFORE_GONE_WORDS
    if before is None and not before_note:
        before_note = str(old.get("beforeNote") or "") or PRESS_ONLY_WORDS
    items = [before, after] if before else [after]
    rec = _record(items, before_note=before_note, folder=reply_folder(home, thread_id, turn_id))
    await asyncio.to_thread(_write_note, home, thread_id, turn_id, rec["note"])
    return rec


# --------------------------------------------------------------------------- #
# the automatic setting, offered once
# --------------------------------------------------------------------------- #


def offer_due(config: Any) -> bool:
    """Is the automatic setting still to be offered? Only while it was never
    chosen, and only once ever (:func:`mark_offered`)."""
    if getattr(config, "reply_pictures", None) is not None:
        return False
    try:
        return not (Path(config.home) / DIRNAME / OFFERED_MARK).exists()
    except (OSError, AttributeError, TypeError):
        return False


def mark_offered(home: Any) -> None:
    """BLOCKING: the offer was made; it is never made again."""
    try:
        mark = Path(home) / DIRNAME / OFFERED_MARK
        mark.parent.mkdir(parents=True, exist_ok=True)
        mark.write_text(utcnow().isoformat(), encoding="utf-8")
    except OSError:
        log.warning("could not note that the pictures setting was offered", exc_info=True)
