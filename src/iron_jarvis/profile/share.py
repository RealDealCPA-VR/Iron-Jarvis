"""Share my profile with Build (v1.306.0; idea from agent-personalizer, MIT).

A Build pane runs a vendor CLI (Claude Code, Codex) that never sees Iron
Jarvis's prompt seams, so the profile the user set up stops at the pane's
edge. This module keeps ONE generated block between markers in that CLI's own
user-level instruction file, so every session the CLI starts reads it:

* Claude Code 2.1.290: ``<home>/CLAUDE.md``, home = ``CLAUDE_CONFIG_DIR`` or
  ``~/.claude`` (measured here: a throwaway ``CLAUDE_CONFIG_DIR`` and a
  throwaway ``USERPROFILE`` each made the file load as a "User" instructions
  attachment; HTML-comment marker lines are dropped from what the model reads).
* Codex 0.157.0: ``<home>/AGENTS.md``, home = ``CODEX_HOME`` or ``~/.codex``
  — but ``AGENTS.override.md`` in the same folder WINS when it holds anything
  but whitespace (measured with ``codex debug prompt-input``), so the block
  goes into the file Codex actually reads.

Rules (pinned by ``tests/test_profile_share_v1306.py``):

* OFF by default, one switch per CLI found on this PC; the switch is the
  consent. Content = ``profile.render`` + CONFIRMED preferences only (source
  ``preference``) — never reflections, feedback notes, project knowledge,
  memory or anything else; promptguard-scanned, secrets masked, paths on this
  PC replaced, capped at :data:`MAX_SHARE_CHARS`.
* Text OUTSIDE the markers is byte-identical after every write and after
  removal: the block (markers + one trailing newline) is inserted right after
  any BOM at the top of the file, replaced in place, or cut out — nothing else
  is touched. A missing file is created with only the block; turning off
  removes the block and deletes a file WE created that now holds nothing else.
* Before the FIRST write to a file that already existed, a copy goes to
  ``<IJ home>/trash/<stamp>/profile-share/<path>``. Writes are temp +
  ``os.replace``, and the file is read again right before the replace — a
  save that landed meanwhile wins (the write is abandoned). A file that is a
  symlink or has another hard link is left alone: ``os.replace`` would turn
  the link into a plain file.
* A block that differs from what we last wrote (sha256 kept in
  ``<IJ home>/profile-share.json``) — or that the user removed — is DRIFT and
  is never overwritten; only the user's Overwrite press (or Keep yours, which
  freezes the file) settles it.
* The default home AND every Iron-Proxy account home for that CLI; an account
  whose home IS the default (adopted) is the same file — written once.
  Iron-Proxy off = the default home only (blocks in account homes are taken
  back); Iron-Proxy on but not read yet = account files left as they are.
* Re-rendered on a profile / preference change and at boot, DEBOUNCED on a
  timer thread — never on the event loop, never holding up boot.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The whole generated block's content is capped here (characters).
MAX_SHARE_CHARS = 4000
#: One preference line's cap (the v1.305.0 sentence cap).
PREF_CHARS = 280
#: Seconds a burst of changes is folded into one write.
DEBOUNCE_S = 1.5

START = b"<!-- iron-jarvis:profile:start"
END = b"<!-- iron-jarvis:profile:end -->"
START_LINE = (
    "<!-- iron-jarvis:profile:start - written by Iron Jarvis from your profile; "
    "change it in Iron Jarvis (an edit here stops Iron Jarvis updating this block) -->"
)
END_LINE = END.decode("ascii")
BOM = b"\xef\xbb\xbf"

LEAD = "The person you are working with keeps this profile in Iron Jarvis, their own assistant app."
PREFS_HEADER = "# Preferences they approved"

STATE_FILE = "profile-share.json"


@dataclass(frozen=True)
class CliSpec:
    id: str
    label: str
    vendor: str
    binary: str
    home_var: str
    default_dir: str
    #: Instruction files in the home, in the CLI's own precedence order (the
    #: first one that holds anything but whitespace is read; the last is the
    #: one created when none does).
    files: tuple[str, ...]
    proxy_provider: str
    config_key: str


CLIS: dict[str, CliSpec] = {
    "claude-code": CliSpec(
        "claude-code", "Claude Code", "Anthropic", "claude", "CLAUDE_CONFIG_DIR", ".claude",
        ("CLAUDE.md",), "anthropic", "profile_share_claude_code",
    ),
    "codex": CliSpec(
        "codex", "Codex", "OpenAI", "codex", "CODEX_HOME", ".codex",
        ("AGENTS.override.md", "AGENTS.md"), "openai", "profile_share_codex",
    ),
}

_current: "ProfileShare | None" = None


def current() -> "ProfileShare | None":
    return _current


def notify_changed() -> None:
    """The profile or a preference changed: re-render (debounced, off the
    loop). Cheap and never raises — the data layer calls it after a commit."""
    svc = _current
    if svc is None:
        return
    try:
        svc.poke()
    except Exception:  # noqa: BLE001 — a share never breaks a save
        log.debug("profile share poke failed", exc_info=True)


# --------------------------------------------------------------------------- #
# content
# --------------------------------------------------------------------------- #

_WIN_PATH = re.compile(r"(?<![\w/])[A-Za-z]:[\\/][^\s\"'<>|]*")
_UNC_PATH = re.compile(r"(?<!\S)\\\\[^\s\\]+\\[^\s\"'<>|]*")
_HOME_PATH = re.compile(r"(?<![\w.])(?:~[\\/]|/(?:Users|home|root|mnt|var|opt|srv)/)[^\s\"'<>|]*")
PATH_WORDS = "(a path on this PC)"


def scrub_paths(text: str) -> str:
    """Replace absolute paths on this PC with a neutral phrase."""
    text = _UNC_PATH.sub(PATH_WORDS, text)
    text = _WIN_PATH.sub(PATH_WORDS, text)
    return _HOME_PATH.sub(PATH_WORDS, text)


def _clean(text: str, source: str) -> tuple[str, bool]:
    """``(text, blocked)`` — promptguard-scanned, secrets masked, paths out."""
    from ..core.promptguard import scan_context
    from ..detections.redact import mask

    result = scan_context(text, source=source, cap=None)
    return scrub_paths(mask(result.text) or ""), bool(result.blocked)


def confirmed_preferences(engine) -> list[str]:
    """The sentences of CONFIRMED preferences (said, or a suggestion the user
    kept): ``source == "preference"``, user scope, status confirmed or NULL.
    Feedback notes, distilled and reflection lessons are NOT preferences."""
    from sqlmodel import select

    from ..core.db import session_scope
    from ..learning.engine import confirmed_clause
    from ..learning.models import LessonRecord

    with session_scope(engine) as db:
        rows = list(
            db.exec(
                select(LessonRecord.text)
                .where(LessonRecord.scope == "user")
                .where(LessonRecord.source == "preference")
                .where(confirmed_clause())
                .order_by(LessonRecord.weight.desc(), LessonRecord.created_at.desc())
                .limit(200)
            )
        )
    return [str(t or "") for t in rows]


def render_content(engine) -> dict[str, Any]:
    """``{text, chars, omitted, blocked}`` — the block's inner text ("" when
    there is nothing to share). LF newlines."""
    from .block import render
    from .store import ProfileStore

    blocked = 0
    profile = render(ProfileStore(engine).get())
    if profile:
        profile, hit = _clean(profile, "profile-share")
        blocked += int(hit)
    lines: list[str] = []
    for raw in confirmed_preferences(engine):
        one = " ".join(raw.split())
        if not one:
            continue
        if len(one) > PREF_CHARS:
            one = one[: PREF_CHARS - 1].rstrip() + "…"
        cleaned, hit = _clean(one, "profile-share")
        if hit:
            blocked += 1
            continue  # a flagged preference is dropped, never placeholder'd
        if cleaned.strip():
            lines.append(f"- {cleaned.strip()}")
    head = [LEAD]
    if profile.strip():
        head.append(profile.strip())
    body = "\n\n".join(head)
    if len(body) > MAX_SHARE_CHARS:  # the profile alone is ~2,400 at most
        body = body[: MAX_SHARE_CHARS - 1].rstrip() + "…"
    omitted = 0
    if lines:
        kept: list[str] = []
        room = MAX_SHARE_CHARS - len(body) - len("\n\n" + PREFS_HEADER)
        for line in lines:
            if len(line) + 1 <= room:
                kept.append(line)
                room -= len(line) + 1
            else:
                omitted += 1
        if kept:
            body += "\n\n" + PREFS_HEADER + "\n" + "\n".join(kept)
    if not profile.strip() and not lines:
        return {"text": "", "chars": 0, "omitted": 0, "blocked": blocked}
    # A marker typed into the profile must never end (or start) our block.
    body = _MARKER_WORDS.sub("iron-jarvis profile ", body)
    # Nor may an HTML comment opener typed into the profile hide the rest of
    # the block from the CLI, which drops comments before the model reads it.
    body = body.replace("<!--", "< !--").replace("-->", "-- >")
    return {"text": body, "chars": len(body), "omitted": omitted, "blocked": blocked}


_MARKER_WORDS = re.compile(r"iron-jarvis:profile:", re.IGNORECASE)


def block_bytes(content: str, newline: str) -> bytes:
    """The whole managed region: markers + content + ONE trailing newline."""
    text = "\n".join([START_LINE, content, END_LINE]) + "\n"
    if newline != "\n":
        text = text.replace("\n", newline)
    return text.encode("utf-8")


def region_hash(region: bytes) -> str:
    norm = region.replace(b"\r\n", b"\n").rstrip(b"\n")
    return hashlib.sha256(norm).hexdigest()


def find_region(data: bytes) -> tuple[int, int] | str | None:
    """``(start, end)`` of the managed region (end includes ONE newline after
    the end marker), ``None`` when there are no markers, ``"broken"`` when
    they are damaged (an end without a start, a start without an end, two
    starts)."""
    s = data.find(START)
    if s < 0:
        return "broken" if END in data else None
    e0 = data.find(END, s)
    if e0 < 0:
        return "broken"
    if data.find(START, s + len(START), e0) >= 0 or data.find(START, e0) >= 0:
        return "broken"
    e = e0 + len(END)
    if data[e:e + 2] == b"\r\n":
        e += 2
    elif data[e:e + 1] == b"\n":
        e += 1
    return (s, e)


def _decodes(data: bytes) -> bool:
    try:
        (data[len(BOM):] if data.startswith(BOM) else data).decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _blank(data: bytes) -> bool:
    return not (data[len(BOM):] if data.startswith(BOM) else data).strip()


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.expanduser(str(path))))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FileChanged(OSError):
    """The file changed between Jarvis's read and its write (another program,
    or the user, saved it) — the write is abandoned, the user's bytes win."""


#: ``_atomic_write(expect=...)`` default: no re-check (Jarvis's own state file).
_UNCHECKED = object()

LINK_ERROR = "it is a link to another file, so it was left alone"


def _is_link(path: Path) -> bool:
    """A symlink, or a file with more than one hard link. ``os.replace`` would
    swap the LINK for a plain file — the user's dotfiles link (and every other
    name for those bytes) would silently stop being the file the CLI reads."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return False
    import stat as _stat

    return _stat.S_ISLNK(st.st_mode) or st.st_nlink > 1


def _atomic_write(path: Path, data: bytes, *, expect: Any = _UNCHECKED) -> None:
    """Temp + ``os.replace``. With ``expect`` (the bytes read before deciding,
    ``None`` = the file did not exist), the file is read again right before the
    replace and :class:`FileChanged` is raised when it differs — an edit saved
    while Jarvis was writing is never overwritten."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.ij-{uuid.uuid4().hex[:8]}.tmp")
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if expect is not _UNCHECKED and _read(path) != expect:
            raise FileChanged("it changed while Jarvis was writing it")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def default_home(spec: CliSpec) -> Path:
    """The CLI's home as the CLI resolves it: its variable, else ``~/.<cli>``."""
    override = (os.environ.get(spec.home_var) or "").strip()
    if override:
        return Path(os.path.expanduser(override))
    return Path(os.path.expanduser("~")) / spec.default_dir


def target_file(spec: CliSpec, home: Path) -> Path:
    """The file the CLI READS in ``home``: the first candidate that holds
    anything but whitespace, else the last one (the one to create)."""
    for name in spec.files[:-1]:
        p = home / name
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if not _blank(data):
            return p
    return home / spec.files[-1]


def _cli_found(spec: CliSpec) -> bool:
    """Is the CLI installed on this PC (the Launch menu's resolver)? A seam."""
    try:
        from ..terminals.ai_clis import _find

        return _find(spec.binary) is not None
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# the service
# --------------------------------------------------------------------------- #


class ProfileShare:
    """Keeps the generated block in each switched-on CLI's instruction files."""

    def __init__(self, config: Any, engine: Any, iron_proxy: Any = None, *, register: bool = True) -> None:
        global _current
        self._config = config
        self._engine = engine
        self._iron_proxy = iron_proxy
        self._lock = threading.RLock()
        self._timer_lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._closed = False
        self._last_content: dict[str, Any] = {}
        if iron_proxy is not None and hasattr(iron_proxy, "add_accounts_listener"):
            iron_proxy.add_accounts_listener(self.poke)
        if register:
            _current = self

    # ------------------------------------------------------------ settings
    @property
    def home(self) -> Path:
        return Path(self._config.home)

    def is_on(self, cli: str) -> bool:
        spec = CLIS.get(cli)
        return bool(spec and getattr(self._config, spec.config_key, False))

    def available(self, cli: str) -> bool:
        spec = CLIS.get(cli)
        return bool(spec) and _cli_found(spec)

    # --------------------------------------------------------------- state
    def _state_path(self) -> Path:
        return self.home / STATE_FILE

    def _load(self) -> dict[str, Any]:
        try:
            doc = json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"files": {}}
        if not isinstance(doc, dict) or not isinstance(doc.get("files"), dict):
            return {"files": {}}
        return doc

    def _save(self, doc: dict[str, Any]) -> None:
        _atomic_write(self._state_path(), json.dumps(doc, indent=2, sort_keys=True).encode("utf-8"))

    # --------------------------------------------------------------- homes
    def homes(self, cli: str) -> tuple[list[dict[str, Any]], bool]:
        """``([{home, account, account_id}], accounts_known)`` — the default
        home first, then each Iron-Proxy account home for this CLI (cached
        snapshot, no network). An account at the default home is skipped (the
        same file). ``accounts_known`` is False while Iron-Proxy is on but has
        not been read (account files are then left as they are)."""
        from ..iron_proxy.accounts import is_default_home

        spec = CLIS[cli]
        base = default_home(spec)
        out = [{"home": base, "account": None, "account_id": None}]
        seen = {_key(base)}
        ip = self._iron_proxy
        if ip is None or not getattr(ip, "enabled", False):
            return out, True
        try:
            cached = ip.cached_accounts()
        except Exception:  # noqa: BLE001
            cached = None
        if cached is None:
            return out, False
        profiles, _states = cached
        for p in profiles:
            if not isinstance(p, dict) or (p.get("lane") or "cli") != "cli":
                continue
            if p.get("provider") != spec.proxy_provider:
                continue
            home = str((p.get("cli") or {}).get("home") or "").strip()
            if not home or is_default_home(spec.home_var, home):
                continue
            k = _key(home)
            if k in seen:
                continue
            seen.add(k)
            out.append({
                "home": Path(home),
                "account": str(p.get("title") or p.get("id") or ""),
                "account_id": str(p.get("id") or ""),
            })
        return out, True

    # ------------------------------------------------------------- inspect
    def _inspect(self, path: Path, rec: dict[str, Any] | None) -> dict[str, Any]:
        """Read-only look at one file: ``{exists, drift, error}``."""
        rec = rec or {}
        try:
            data = _read(path)
        except OSError as exc:
            return {"exists": True, "drift": None, "error": f"could not read it ({exc.__class__.__name__})"}
        if data is None:
            return {"exists": False, "drift": "removed" if rec.get("hash") else None, "error": None}
        if not _decodes(data):
            return {"exists": True, "drift": None, "error": "it is not UTF-8 text, so it was left alone"}
        region = find_region(data)
        if region == "broken":
            return {"exists": True, "drift": "broken", "error": None}
        if region is None:
            return {"exists": True, "drift": "removed" if rec.get("hash") else None, "error": None}
        s, e = region
        h = region_hash(data[s:e])
        if rec.get("hash") and h == rec["hash"]:
            return {"exists": True, "drift": None, "error": None}
        return {"exists": True, "drift": "edited", "error": None, "found_hash": h}

    # --------------------------------------------------------------- write
    def _backup(self, path: Path, data: bytes, stamp: str) -> None:
        drive, rest = os.path.splitdrive(os.path.abspath(str(path)))
        parts = [drive.strip(":\\/").replace(":", "") or "root"] + [
            p for p in re.split(r"[\\/]+", rest) if p
        ]
        dest = self.home / "trash" / stamp / "profile-share" / Path(*parts)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def _write_one(
        self, cli: str, path: Path, content: str, rec: dict[str, Any], *, force: bool, stamp: str
    ) -> dict[str, Any]:
        """Put ``content`` in ``path``'s block. Returns the updated record."""
        rec = dict(rec)
        rec.update({"cli": cli, "path": str(path)})
        rec.pop("error", None)
        if rec.get("held") and not force:
            return rec
        if _is_link(path):
            rec["error"] = LINK_ERROR
            return rec
        try:
            data = _read(path)
        except OSError as exc:
            rec["error"] = f"could not read it ({exc.__class__.__name__})"
            return rec
        if data is not None and not _decodes(data):
            rec["error"] = "it is not UTF-8 text, so it was left alone"
            return rec
        region = find_region(data) if data is not None else None
        if region == "broken":
            rec["drift"] = "broken"
            if force:
                rec["error"] = "its Iron Jarvis markers are damaged — fix or delete them by hand first"
            return rec
        newline = "\r\n" if data is not None and b"\r\n" in data else "\n"
        new_block = block_bytes(content, newline)
        new_hash = region_hash(new_block)
        if region is not None:
            s, e = region
            cur = region_hash(data[s:e])
            if cur == new_hash:
                rec.update({"hash": new_hash, "drift": None, "held": False})
                return rec
            if (not rec.get("hash") or cur != rec["hash"]) and not force:
                rec["drift"] = "edited"
                return rec
            out = data[:s] + new_block + data[e:]
        elif data is not None:
            if rec.get("hash") and not force:
                rec["drift"] = "removed"
                return rec
            bom = BOM if data.startswith(BOM) else b""
            out = bom + new_block + data[len(bom):]
        else:
            if rec.get("hash") and not force:
                rec["drift"] = "removed"
                return rec
            out = new_block
            rec["created"] = True
        try:
            if data is not None and not rec.get("backed_up"):
                self._backup(path, data, stamp)
                rec["backed_up"] = True
            _atomic_write(path, out, expect=data)
        except FileChanged as exc:
            rec["error"] = f"{exc}, so it was left alone — the next change tries again"
            return rec
        except OSError as exc:
            rec["error"] = f"could not write it ({exc.__class__.__name__})"
            return rec
        rec.update({"hash": new_hash, "drift": None, "held": False, "written_at": _now()})
        return rec

    def _remove_one(self, rec: dict[str, Any], *, edited_ok: bool, stamp: str) -> dict[str, Any] | None:
        """Take our block out of ``rec``'s file. ``None`` = done (forget the
        record); a record back = left in place (edited and not ``edited_ok``,
        or an error)."""
        path = Path(rec.get("path") or "")
        if _is_link(path):
            return {**rec, "error": LINK_ERROR}
        try:
            data = _read(path)
        except OSError as exc:
            return {**rec, "error": f"could not read it ({exc.__class__.__name__})"}
        if data is None:
            return None
        if not _decodes(data):
            return None
        region = find_region(data)
        if not isinstance(region, tuple):
            return None if region is None else {**rec, "drift": "broken"}
        s, e = region
        edited = region_hash(data[s:e]) != rec.get("hash")
        if edited and not edited_ok:
            return {**rec, "drift": "edited"}
        out = data[:s] + data[e:]
        try:
            if edited:  # the user's own words inside the block: keep a copy
                self._backup(path, data, stamp)
            if rec.get("created") and _blank(out):
                if _read(path) != data:
                    raise FileChanged("it changed while Jarvis was writing it")
                path.unlink()
            else:
                _atomic_write(path, out, expect=data)
        except FileChanged as exc:
            return {**rec, "error": f"{exc}, so it was left alone — the next change tries again"}
        except OSError as exc:
            return {**rec, "error": f"could not change it ({exc.__class__.__name__})"}
        return None

    # ---------------------------------------------------------------- sync
    def sync(self, cli: str | None = None, *, force_paths: set[str] | None = None) -> None:
        """Bring the files of ``cli`` (every CLI when None) in line with the
        switch and the current content. Blocking — call off the loop."""
        with self._lock:
            doc = self._load()
            files: dict[str, Any] = doc["files"]
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
            content: dict[str, Any] | None = None
            for cid in [cli] if cli else list(CLIS):
                spec = CLIS[cid]
                mine = {k: v for k, v in files.items() if isinstance(v, dict) and v.get("cli") == cid}
                if not self.is_on(cid):
                    for k, rec in mine.items():
                        kept = self._remove_one(rec, edited_ok=True, stamp=stamp)
                        if kept is None:
                            files.pop(k, None)
                        else:
                            files[k] = kept
                    continue
                if content is None:
                    content = render_content(self._engine)
                    self._last_content = content
                homes, known = self.homes(cid)
                targets: dict[str, tuple[Path, dict[str, Any]]] = {}
                if content["text"]:
                    for h in homes:
                        p = target_file(spec, Path(h["home"]))
                        targets[_key(p)] = (p, h)
                for k, (p, h) in targets.items():
                    rec = dict(files.get(k) or {})
                    rec["account"] = h["account"]
                    rec["account_id"] = h["account_id"]
                    force = bool(force_paths) and k in force_paths
                    files[k] = self._write_one(cid, p, content["text"], rec, force=force, stamp=stamp)
                for k, rec in mine.items():
                    if k in targets:
                        continue
                    if rec.get("account_id") and not known:
                        continue  # Iron-Proxy not read yet: leave account files alone
                    kept = self._remove_one(rec, edited_ok=False, stamp=stamp)
                    if kept is None:
                        files.pop(k, None)
                    else:
                        files[k] = kept
            self._save(doc)

    def set_on(self, cli: str, on: bool) -> None:
        """Flip the switch, persist it, and write / remove now (blocking)."""
        from ..core.config import persist_config_values

        spec = CLIS[cli]
        with self._lock:
            setattr(self._config, spec.config_key, bool(on))
            persist_config_values(self._config.home, {spec.config_key: bool(on)})
            self.sync(cli)

    def overwrite(self, cli: str, path: str | None = None) -> int:
        """The user's press: write our block over their edit (or put back a
        block they removed). Returns how many files it applied to."""
        with self._lock:
            doc = self._load()
            keys = self._drift_keys(cli, doc, path, include_held=True)
            if not keys:
                return 0
            self.sync(cli, force_paths=set(keys))
            return len(keys)

    def keep_mine(self, cli: str, path: str | None = None) -> int:
        """The user's press: keep their version — the file is no longer updated
        (until Overwrite)."""
        with self._lock:
            doc = self._load()
            keys = self._drift_keys(cli, doc, path, include_held=False)
            for k in keys:
                rec = doc["files"].get(k)
                if isinstance(rec, dict):
                    rec["held"] = True
                    rec["drift"] = None
            if keys:
                self._save(doc)
            return len(keys)

    def _drift_keys(self, cli: str, doc: dict[str, Any], path: str | None, *, include_held: bool) -> list[str]:
        want = _key(path) if path else None
        out = []
        for k, rec in doc["files"].items():
            if not isinstance(rec, dict) or rec.get("cli") != cli:
                continue
            if want is not None and k != want:
                continue
            look = self._inspect(Path(rec.get("path") or ""), rec)
            if look["drift"] or (include_held and rec.get("held")):
                out.append(k)
        return out

    # --------------------------------------------------------------- views
    def state(self) -> dict[str, Any]:
        """``{clis: [...], limit}`` — read-only (no write), blocking."""
        with self._lock:
            doc = self._load()
        files = doc["files"]
        out = []
        for cid, spec in CLIS.items():
            on = self.is_on(cid)
            rows = []
            homes, known = self.homes(cid)
            # The files a switch-on WOULD write (the sentence under the switch
            # names them before the press).
            targets = [
                {"path": str(target_file(spec, Path(h["home"]))), "account": h["account"]}
                for h in homes
            ]
            if on:
                listed = set()
                for h in homes:
                    p = target_file(spec, Path(h["home"]))
                    k = _key(p)
                    listed.add(k)
                    rows.append(self._row(p, files.get(k), h["account"]))
                for k, rec in files.items():
                    if isinstance(rec, dict) and rec.get("cli") == cid and k not in listed:
                        rows.append(self._row(Path(rec.get("path") or ""), rec, rec.get("account")))
            else:
                for rec in files.values():
                    if isinstance(rec, dict) and rec.get("cli") == cid:
                        rows.append(self._row(Path(rec.get("path") or ""), rec, rec.get("account")))
            written = [r["last_written"] for r in rows if r["last_written"]]
            content = self._last_content if on else {}
            out.append({
                "cli": cid,
                "label": spec.label,
                "vendor": spec.vendor,
                "available": self.available(cid),
                "on": on,
                "file_name": spec.files[-1],
                "files": rows,
                "targets": targets,
                "last_written": max(written) if written else None,
                "drift": any(r["drift"] for r in rows),
                "accounts_known": known,
                "chars": int(content.get("chars") or 0),
                "omitted": int(content.get("omitted") or 0),
            })
        return {"clis": out, "limit": MAX_SHARE_CHARS}

    def _row(self, path: Path, rec: dict[str, Any] | None, account: Any) -> dict[str, Any]:
        rec = rec if isinstance(rec, dict) else {}
        look = self._inspect(path, rec)
        held = bool(rec.get("held"))
        return {
            "path": str(path),
            "account": account or None,
            "exists": look["exists"],
            "last_written": rec.get("written_at"),
            "drift": None if held else (look["drift"] if rec else None),
            "held": held,
            "created": bool(rec.get("created")),
            "error": rec.get("error") or look["error"],
        }

    # ------------------------------------------------------------- the timer
    def poke(self) -> None:
        """Re-render soon (debounced) on a timer thread. Returns at once."""
        with self._timer_lock:
            if self._closed:
                return
            if self._timer is not None:
                self._timer.cancel()
            t = threading.Timer(DEBOUNCE_S, self._run)
            t.daemon = True
            t.name = "profile-share"
            self._timer = t
            t.start()

    def _run(self) -> None:
        try:
            if not any(self.is_on(c) for c in CLIS) and not self._load()["files"]:
                return
            self.sync()
        except Exception:  # noqa: BLE001 — a share never breaks the daemon
            log.warning("profile share re-render failed", exc_info=True)

    def wait_idle(self, timeout: float = 10.0) -> bool:
        """Wait for a pending / running re-render (tests, shutdown)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._timer_lock:
                t = self._timer
            if t is None or not t.is_alive():
                with self._timer_lock:
                    if self._timer is t:
                        return True
                continue
            t.join(max(0.0, deadline - time.monotonic()))
        return False

    def close(self) -> None:
        with self._timer_lock:
            self._closed = True
            if self._timer is not None:
                self._timer.cancel()
