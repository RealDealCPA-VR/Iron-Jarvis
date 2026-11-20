"""Build › Code: checkpoints that LAST, and the whole folder put back.

Every message of a Code-view session already records the folder as a git tree
(:func:`panes.record_turn_start`). Two things were missing:

* **Nothing kept that tree.** No ref pointed at it, so git's own clean-up
  (``git gc``, by default after about two weeks) could delete it. Now every
  recorded tree gets a hidden ref that points STRAIGHT AT THE TREE:
  ``refs/ironjarvis/checkpoints/<session>/<name>``. No commit is made, so the
  ref is not in ``git log --all``, ``git branch -a`` or ``git tag`` (measured:
  a tree ref survives ``git gc --prune=now``; the same tree with no ref is
  deleted). The refs go with the session (:func:`forget_session`). A plain
  push, fetch or clone does not carry them; ``push --mirror``, ``clone
  --mirror`` and ``bundle --all`` DO (measured), and the Handbook says so.
* **A rewind never touched files.** :func:`plan` reads what putting the folder
  back to a message's checkpoint would do, and :func:`restore` does it.

THE RULE OF THE RESTORE: nothing is removed or written over without a copy.
git itself does not keep that rule — ``git restore`` DELETES a whole folder
that stands where a file must go (with the ignored files, the nested
repository or the junction inside it), deletes a file that stands where a
folder must go, and writes over a file it ignores (review of the first
version: each measured). So, in this order (:func:`_apply`):

1. the plan refuses, before anything changes, a path where something that is
   in no checkpoint stands in the way (:func:`_plan`: ``problems``), and
   LEAVES a file git ignores now (``left``);
2. the files made since are MOVED to ``<trash>/made-since/``;
3. the same "is anything in the way" check runs again on the folder as it now
   is (:func:`_still_clear`);
4. every file git is about to write over is COPIED to ``<trash>/replaced/``
   (no copy, no restore);
5. only then ``git restore --source=<tree> --worktree`` writes, by literal
   paths, through a PRIVATE index (``GIT_INDEX_FILE``): the user's index is
   neither changed nor locked, so a git that is stopped half way leaves no
   ``index.lock`` in their repository.

When the folder's ``.gitignore`` files are among what changes, they go back
FIRST and the files "made since" are then asked of git again
(:func:`_ignored_now`): one that the checkpoint's own rules have git ignore
STAYS where it is — it is in no checkpoint, so nothing can say it was not
there (a ``.env`` the user filled in after the tool un-ignored it).

A failure returns the folder from those copies (exact bytes) and is then
CHECKED against the folder's tree before it is called whole. The folder as it
was just before is also kept as a ``before_rewind`` checkpoint, which is what
:func:`undo` puts back; an undo goes through the same five steps, so what IT
replaces is kept too.

Everything here is BLOCKING (a caller on the event loop hops it off) and the
caller holds the session's and the folder's git locks (the route passes them
in). A refusal is one plain sentence (:class:`RestoreRefused`).

Not restored, and said in the plan: a nested repository (submodule), a link,
a file git ignores (in no checkpoint), a name that changed only in upper or
lower case.

LINE ENDINGS. A checkpoint is the tree git makes with the folder's own
settings, so under ``core.autocrlf`` or a ``.gitattributes`` rule it holds a
file's NORMALISED text, not which line endings the file had; git then writes
the endings its settings give (measured on the owner's PC,
``core.autocrlf=input``: a CRLF ``run.bat`` came back LF). After git writes,
:func:`_keep_endings` therefore gives a restored file the line-ending style of
the version it replaced — the file as it was a moment ago, which is the style
it had at the checkpoint unless the endings themselves were changed since. An
undo goes one better: the rewind's own copy is the exact file
(:func:`_exact_endings`). A file that was deleted since has no version to
read the style from and keeps git's; the plan says all of this (``endings``).
"""

from __future__ import annotations

import contextlib
import contextvars
import functools
import hashlib
import logging
import os
import re
import shutil
import stat
import uuid
from pathlib import Path
from typing import Any

from . import panes as PN

log = logging.getLogger(__name__)

#: Where the hidden refs live in the user's repository.
REF_ROOT = "refs/ironjarvis/checkpoints"
#: How many checkpoints of each kind one session keeps (newest kept).
MAX_STARTS = 300
MAX_SEEN = 30
MAX_BEFORE = 10
#: A restore of more files than this is refused (use git for it).
RESTORE_MAX_FILES = 5_000
#: How many names of each list a plan shows (the counts are exact).
LIST_SHOWN = 50
#: How long git may take to write the files (a read's 30 s is too short for
#: thousands of files; a git stopped half way is the worst outcome here).
RESTORE_S = 300.0
#: A folder that stands where a file must go is walked at most this far.
WALK_MAX = 20_000
#: An undo makes line endings exact from the rewind's own copies, for files
#: up to this size.
EXACT_MAX_BYTES = 50 * 1024 * 1024

HOW_START = "start"
HOW_SEEN = "seen"
HOW_BEFORE = "before_rewind"

#: The folders inside one rewind's trash folder.
MADE = "made-since"
REPLACED = "replaced"
ROLLED = "rolled-back"

NO_CHECKPOINT = (
    "Iron Jarvis has no checkpoint of the folder for this message, so its files cannot be "
    "put back from here."
)
CHECKPOINT_GONE = (
    "The checkpoint for this message is no longer kept, so nothing was changed."
)
NOT_REPO = "This folder is not in a git repository, so its files cannot be put back from here."
#: v1.546.0: git itself is missing (a folder with no repository has checkpoints
#: of its own now, so "not a repository" is no reason any more).
NO_GIT = "git is not installed on this PC, so the folder's files cannot be put back from here."
TOO_MANY = (
    "{n} files changed since this message, which is more than Iron Jarvis puts back in one "
    "go. Use git for this one."
)
FOLDER_CHANGED = (
    "The folder changed after this list was read, so nothing was changed. Read it again."
)
NOT_KEPT = (
    "Iron Jarvis could not keep a checkpoint of the folder as it is now, so nothing was changed."
)
PROBLEMS = "Some files cannot be put back right now, so nothing was changed."
FAILED_ROLLED_BACK = "The files could not be put back ({why}), so the folder was left as it was."
FAILED_PARTIAL = (
    "The files could not all be put back ({why}), and the folder could not be returned to how "
    "it was either. What was replaced or moved is in Iron Jarvis's trash ({trash})."
)
NOTHING_TO_UNDO = "There is no rewind to undo."
UNDO_TOO_LATE = "A message was sent after that rewind, so it can no longer be undone from here."
WHY_LOCKED = "it cannot be written right now (another program may have it open)"
WHY_SUBMODULE = "it is a repository of its own"
WHY_LINK = "it is a link"
WHY_PATH = "its path cannot be used here"
WHY_IGNORED = "git ignores it now, so it is in no checkpoint"
WHY_IGNORED_OWN = "checkpoints leave it out (a folder such as node_modules, or a .gitignore rule)"
WHY_CASE = "only the upper or lower case of its name changed"
WHY_OLDER = "it was there before that message, so git ignored it then and it is in no checkpoint"
WHY_OLDER_OWN = "it was there before that message and was left out then, so it is in no checkpoint"
WHY_UNWATCHED = "git is told not to watch it, so it is in no checkpoint"
WHY_FOLDER = "a folder stands in its place and holds files that are in no checkpoint"
WHY_PARENT = "a file that is in no checkpoint stands where its folder must go"
WHY_NO_COPY = "a copy of a file could not be kept first"
WHY_IN_THE_WAY = "something that is in no checkpoint stands where a file must go"
WHY_GIT_SLOW = "git did not finish in time"
WHY_MOVE = "a file could not be moved"
ENDINGS = (
    "git changes line endings in this folder ({why}). A file that goes back keeps the kind of "
    "line endings it has now, and a file that was deleted comes back with the ones git writes, "
    "so line endings can differ from what they were."
)
DONE = (
    "The folder's files are back as they were when that message started: {back} put back, "
    "{moved} made since moved to Iron Jarvis's trash."
)
DONE_KEPT = " The versions that were replaced are kept there too."
DONE_NOTHING = "The folder's files were already as they were when that message started."
DONE_COMMITS = " {n} commit{s} made since then {are} still in git, so git shows the difference as changes."
DONE_LEFT = " {n} left as {they} ({why})."
DONE_STAYED = " {n} that git ignores again stayed where {they}."
DONE_STAYED_OWN = " {n} that checkpoints leave out stayed where {they}."
TOO_MANY_OWN = (
    "{n} files changed since this message, which is more than Iron Jarvis puts back in one go."
)
WHY_SLOW_OWN = "the folder could not be read in time"
UNDONE = "The rewind's file changes were undone: the folder is as it was just before it."
UNDONE_KEPT = " What the undo replaced is kept in Iron Jarvis's trash."


class RestoreRefused(Exception):
    """A restore that did not happen, in one sentence (and an HTTP status)."""

    def __init__(self, sentence: str, status: int = 409, *, problems: list[dict[str, str]] | None = None) -> None:
        super().__init__(sentence)
        self.sentence = sentence
        self.status = status
        self.problems = problems or []


class _Stopped(Exception):
    """A restore step that did not finish, with the plain reason."""


# --------------------------------------------------------------------------- #
# the refs
# --------------------------------------------------------------------------- #

_SAFE = re.compile(r"[^A-Za-z0-9_-]")
_REPOS: dict[str, str] = {}


def _part(text: Any) -> str:
    """One path part of a ref name: letters, digits, ``_`` and ``-`` only
    (git's rules refuse much more than this ever holds)."""
    return _SAFE.sub("_", str(text or ""))[:120].strip("_-")


def ref_for(sid: Any, name: Any) -> str:
    """The hidden ref for one checkpoint, or "" when either part is empty."""
    a, b = _part(sid), _part(name)
    return f"{REF_ROOT}/{a}/{b}" if a and b else ""


def _usable(path: str) -> bool:
    return bool(path) and not PN._network(path) and os.path.isdir(path)


# --------------------------------------------------------------------------- #
# v1.546.0 (Release D, phase 1d): a folder that is NOT a git repository
# --------------------------------------------------------------------------- #
#
# A session whose folder is in no repository keeps its checkpoints in a
# repository of Iron Jarvis's OWN, one per session, outside the folder:
# ``<home>/workspaces/code-checkpoints/<sid>.git``. git is told where it is
# for each call (``GIT_DIR``) and that the session's folder is its work tree
# (``GIT_WORK_TREE``), so everything above and below — the snapshot through a
# private index, the hidden ref, the plan, the copy-first restore, Undo — is
# the SAME code, and NOTHING is written into the user's folder (no ``.git``).
#
# * Under ``workspaces/``: a backup leaves it out (it is a copy of the user's
#   files, and those are the user's to back up), and it goes with the session.
# * Its own settings: no line-ending conversion (a restore is byte for byte),
#   no file-mode noise, long paths, the user's global ignore and attribute
#   files left out (what a checkpoint holds never depends on this PC's git
#   settings).
# * Its own ignore list (``info/exclude``): the folders every walk of this app
#   leaves out (``tools.builtins._SKIP_DIRS``: ``node_modules``, ``.venv``,
#   build output…). A ``.gitignore`` in the folder is honoured too. What is
#   left out is in no checkpoint: a rewind neither puts it back nor removes it.
# * Bounded like the project map's walk: at most :data:`OWN_MAX_FILES` files
#   and :data:`OWN_MAX_BYTES` in all. A folder past either gets NO checkpoint
#   for that message, and the Changes tab says why.
#
# Which store a call uses is decided at the few entry points that know the
# session (:func:`in_own_store`) and read where git is spawned
# (:func:`store_env`): a context variable, set for ONE folder, so a call about
# any other folder inside the same block is untouched.

#: Where the sessions' own stores live (beside the kept indexes: not backed up).
OWN_DIR = ("workspaces", "code-checkpoints")
#: The most files one checkpoint of a non-git folder holds (the restore's own limit).
OWN_MAX_FILES = 5_000
#: The most bytes those files are in all.
OWN_MAX_BYTES = 256 * 1024 * 1024
#: Beside the store's own files: why the newest message got no checkpoint ("" = it got one).
OWN_NOTE_FILE = "ij-note.txt"
OWN_WORDS = (
    "Iron Jarvis keeps its own copy of this folder's files at each message (on this PC, in its own data "
    "folder), so \"Rewind to here\" can put them back."
)
OWN_TOO_MANY = (
    "This folder holds more than {n:,} files Iron Jarvis would have to copy, so the newest message has no "
    "checkpoint and \"Rewind to here\" cannot put its files back. Folders such as node_modules are not "
    "counted; a .gitignore file in the folder leaves more out."
)
OWN_TOO_BIG = (
    "The files in this folder are more than {mb:,} MB in all, so the newest message has no checkpoint and "
    "\"Rewind to here\" cannot put its files back. A .gitignore file in the folder leaves files out."
)
OWN_SLOW = (
    "Iron Jarvis could not read this folder's files in time, so the newest message has no checkpoint and "
    "\"Rewind to here\" cannot put its files back."
)
OWN_NOT_READ = (
    "Iron Jarvis could not copy this folder's files ({why}), so the newest message has no checkpoint and "
    "\"Rewind to here\" cannot put its files back."
)
OWN_LATE = (
    "Copying this folder's files took longer than a message waits for it, so the newest message has no "
    "checkpoint and \"Rewind to here\" cannot put its files back."
)
#: A preview, rewind or Undo that could not read the folder (no git named).
OWN_UNREAD = "Iron Jarvis could not read this folder's files: {why}."
#: What a refusal or a result says in a folder git does not know (it has no
#: git to "ignore" anything): the same facts, without the word.
_OWN_SAYS: dict[str, str] = {
    WHY_IGNORED: WHY_IGNORED_OWN, WHY_OLDER: WHY_OLDER_OWN, DONE_STAYED: DONE_STAYED_OWN,
    TOO_MANY: TOO_MANY_OWN, WHY_GIT_SLOW: WHY_SLOW_OWN,
}

_OWN: "contextvars.ContextVar[tuple[str, str] | None]" = contextvars.ContextVar("ij_code_own_store", default=None)


def own_store(home: Any, sid: Any) -> Path | None:
    """Where session *sid*'s own checkpoint store is (or would be)."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", str(sid or ""))[:120].strip(".")
    if home is None or not safe:
        return None
    return Path(str(home)).joinpath(*OWN_DIR, f"{safe}.git")


def has_own_store(home: Any, sid: Any) -> bool:
    """BLOCKING (one stat): does this session keep its checkpoints in a store
    of its own? Decided once, at its first checkpoint, and for good: a folder
    that becomes a git repository later goes on with the store it has."""
    path = own_store(home, sid)
    try:
        return path is not None and (path / "HEAD").is_file()
    except OSError:
        return False


def _key(folder: str) -> str:
    return os.path.normcase(os.path.abspath(folder)) if folder else ""


def store_env(folder: Any) -> dict[str, str]:
    """What git is told for a call in *folder* while the session's own store
    is in use (:func:`in_own_store`): where the repository is and that the
    folder is its work tree. ``{}`` for every other folder and every other time."""
    cur = _OWN.get()
    if cur is None or not folder or _key(str(folder)) != cur[0]:
        return {}
    return {"GIT_DIR": cur[1], "GIT_WORK_TREE": str(folder)}


@contextlib.contextmanager
def in_own_store(home: Any, sid: Any, folder: Any):
    """Inside the block, git calls for *folder* use session *sid*'s OWN store
    — when it has one (else nothing changes: the folder's own repository)."""
    path = own_store(home, sid)
    if not folder or path is None or not has_own_store(home, sid):
        yield None
        return
    token = _OWN.set((_key(str(folder)), str(path)))
    try:
        yield path
    finally:
        _OWN.reset(token)


def _stored(fn: Any) -> Any:
    """``fn(home, sid, row_or_folder, …)`` run inside the session's own store
    when it has one."""
    @functools.wraps(fn)
    def inner(home: Any, sid: Any, where: Any, *args: Any, **kwargs: Any) -> Any:
        folder = where if isinstance(where, str) else str(getattr(where, "folder", "") or "")
        with in_own_store(home, sid, folder):
            return fn(home, sid, where, *args, **kwargs)

    return inner


#: The folders a checkpoint of a plain folder leaves out, at any depth: what a
#: TOOL makes and makes again (package installs, caches, other version
#: control's own data), never a name a person gives a folder of their work
#: (the review: "Build", "Target", "dist" or "venv" may be a client's folder,
#: and a checkpoint without it put nothing back and said "already as it was").
OWN_LEFT_OUT = (
    "node_modules", ".venv", "__pycache__", ".next", ".turbo", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".gradle", "site-packages", ".hg", ".svn",
)
#: The store reads the folder's files as bytes: never a line-ending or
#: encoding filter of the folder's own ``.gitattributes`` (outranks it).
OWN_ATTRIBUTES = "* -text -filter -ident -working-tree-encoding\n"


def own_exclude() -> str:
    """The store's own ignore list (``info/exclude``): :data:`OWN_LEFT_OUT`."""
    return "# Iron Jarvis: left out of this session's checkpoints\n" + "".join(f"{n}/\n" for n in OWN_LEFT_OUT)


def _own_info(store_dir: Path) -> None:
    """The store's own ignore list and attributes, written when they are not
    what this version says (a store made by an earlier one is brought up to
    date at its next use). Raises ``OSError``."""
    info = store_dir / "info"
    info.mkdir(exist_ok=True)
    for name, text in (("exclude", own_exclude()), ("attributes", OWN_ATTRIBUTES)):
        path = info / name
        try:
            same = path.read_text(encoding="utf-8") == text
        except OSError:
            same = False
        if not same:
            path.write_text(text, encoding="utf-8", newline="\n")


def make_own_store(home: Any, sid: Any) -> Path:
    """BLOCKING: the session's own store, made when it is not there yet (an
    empty repository with its own settings; nothing of the user's is in it
    yet). Raises :class:`~.panes.GitUnreadable` / ``OSError`` when it cannot
    be made."""
    from ..git.integration import git_env, run_git
    from ..harness import branches as B

    path = own_store(home, sid)
    if path is None:
        raise OSError("no home")
    if (path / "HEAD").is_file():
        _own_info(path)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    made = path.with_name(path.name + f".{uuid.uuid4().hex[:8]}.new")

    def run(args: list[str]) -> Any:
        return run_git(args, path.parent, env=git_env(), identity=False, encoding="utf-8", timeout=B.QUICK_S)

    try:
        got = run(["init", "--bare", "--quiet", str(made)])
        if not got.ok:
            raise PN.GitUnreadable(got)
        for name, value in (("core.bare", "false"), ("core.autocrlf", "false"), ("core.safecrlf", "false"),
                            ("core.fileMode", "false"), ("core.longpaths", "true"),
                            ("core.excludesFile", ""), ("core.attributesFile", ""),
                            ("gc.autoDetach", "false")):
            got = run(["--git-dir", str(made), "config", name, value])
            if not got.ok:
                raise PN.GitUnreadable(got)
        _own_info(made)
        try:
            os.replace(made, path)
        except OSError:
            if not (path / "HEAD").is_file():  # (another message made it meanwhile: fine)
                raise
    finally:
        shutil.rmtree(made, ignore_errors=True)
    return path


def forget_own_store(home: Any, sid: Any) -> bool:
    """BLOCKING, never raises: a deleted session's own store goes with it (a
    copy of files that are still in the user's folder: never moved to the
    trash). True when there was one."""
    path = own_store(home, sid)
    try:
        if path is None or not path.is_dir():
            return False
        shutil.rmtree(path, onerror=_writable_then_again)
        return not path.exists()
    except OSError:
        log.debug("could not remove a session's own checkpoint store", exc_info=True)
        return False


def _writable_then_again(fn: Any, path: str, _exc: Any) -> None:
    """git's object files are read-only (Windows refuses to delete them)."""
    try:
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    except OSError:
        pass


def own_fits(folder: str) -> str:
    """BLOCKING, in the session's own store: "" when a checkpoint of *folder*
    stays inside the limits, else why not, in words. Counted as git itself
    will read it (its ignore rules applied); a file that cannot be read is
    counted as empty. Raises :class:`~.panes.GitUnreadable`."""
    got = PN._git(["ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", "."], folder)
    if not got.ok:
        raise PN.GitUnreadable(got)
    names = {n for n in got.out.split("\0") if n}
    if len(names) > OWN_MAX_FILES:
        return OWN_TOO_MANY.format(n=OWN_MAX_FILES)
    total = 0
    for rel in names:
        try:
            total += os.lstat(os.path.join(folder, *rel.split("/"))).st_size
        except OSError:
            continue
        if total > OWN_MAX_BYTES:
            return OWN_TOO_BIG.format(mb=OWN_MAX_BYTES // (1024 * 1024))
    return ""


def _own_limits(folder: str) -> None:
    """Inside the session's own store: refuse, in words, BEFORE the folder is
    copied for a preview, a rewind or an Undo, when a copy would pass the
    limits (the copy of the folder as it is now is kept by a rewind)."""
    if _OWN.get() is None:
        return
    why = own_fits(folder)
    if why:
        raise RestoreRefused(why.split(", so ")[0] + ", so nothing was changed.")


def unread_note(gone: Any) -> str:
    """Why the folder could not be copied, for the Changes tab: a time-out,
    or the reason git gave (masked, one line)."""
    got = getattr(gone, "got", None)
    if got is None or getattr(got, "timed_out", False):
        return OWN_SLOW
    return OWN_NOT_READ.format(why=PN._git_reason(got))


def _unread_words(gone: Any) -> str:
    """A preview's, rewind's or Undo's refusal when git did not read the folder."""
    if _OWN.get() is None:
        return str(getattr(gone, "words", "") or "")
    got = getattr(gone, "got", None)
    why = PN.GIT_TIMED_OUT if got is None or getattr(got, "timed_out", False) else PN._git_reason(got)
    return OWN_UNREAD.format(why=why)


def own_note(home: Any, sid: Any) -> str:
    """BLOCKING, never raises: why the session's newest message got no
    checkpoint in its own store ("" = it got one, or it has no store)."""
    path = own_store(home, sid)
    try:
        return (path / OWN_NOTE_FILE).read_text(encoding="utf-8").strip()[:600] if path is not None else ""
    except OSError:
        return ""


def set_own_note(home: Any, sid: Any, words: str) -> None:
    path = own_store(home, sid)
    try:
        if path is None or not path.is_dir():
            return
        note = path / OWN_NOTE_FILE
        if words:
            note.write_text(words, encoding="utf-8")
        else:
            note.unlink(missing_ok=True)
    except OSError:
        log.debug("could not note why a message got no checkpoint", exc_info=True)


def own_words(home: Any, sid: Any) -> str:
    """BLOCKING, never raises: the sentence the Changes tab adds for a session
    that keeps its own checkpoints ("" for one that does not)."""
    if not has_own_store(home, sid):
        return ""
    return own_note(home, sid) or OWN_WORDS


def tidy_own_store(folder: str) -> None:
    """BLOCKING, in the session's own store, never raises: let git pack the
    store when it has grown (``gc --auto``: it does nothing most times, never
    runs in the background here, and keeps everything a checkpoint's ref
    names)."""
    try:
        PN._run_git(["gc", "--auto", "--quiet"], folder, reads=False)
    except Exception:  # noqa: BLE001
        log.debug("could not tidy a session's own checkpoint store", exc_info=True)


def _say(words: str) -> str:
    """*words* as said while the session's own store is in use: the folder has
    no git, so nothing in it is "ignored by git"."""
    return _OWN_SAYS.get(words, words) if _OWN.get() is not None else words


def repo_of(folder: str) -> str:
    """The repository's own (common) git directory, absolute: where a ref
    lives even after *folder* itself is gone (a removed worktree, a deleted
    subfolder). "" when git did not say. Never raises."""
    key = os.path.normcase(os.path.abspath(folder)) if folder else ""
    if not key:
        return ""
    own = store_env(folder)
    if own:
        # v1.546.0: the session's own store — never remembered for the FOLDER
        # (another session in it, or the folder's own repository later, is not this).
        return own["GIT_DIR"]
    if key in _REPOS:
        return _REPOS[key]
    out = ""
    try:
        got = PN._git(["rev-parse", "--path-format=absolute", "--git-common-dir"], folder)
        text = got.out.strip() if got.ok else ""
        if not text or text.startswith("--"):  # a git older than 2.31
            got = PN._git(["rev-parse", "--git-common-dir"], folder)
            text = got.out.strip() if got.ok else ""
            if text and not os.path.isabs(text):
                text = os.path.join(folder, text)
        if text and os.path.isdir(text):
            out = os.path.abspath(text)
    except Exception:  # noqa: BLE001
        out = ""
    if out:
        if len(_REPOS) > 500:
            _REPOS.clear()
        _REPOS[key] = out
    return out


def lock_key(folder: str) -> str:
    """The name of the lock every write of a folder's files takes: one per
    REPOSITORY (a session in ``repo`` and one in ``repo/sub`` share it; two
    spellings of one folder do too), or per folder outside a repository."""
    where = repo_of(folder) or (os.path.abspath(folder) if folder else "")
    return "code-folder:" + os.path.normcase(where) if where else ""


def keep(folder: str, ref: str, tree: str) -> bool:
    """Point *ref* at *tree* (never a commit). Never raises."""
    if not ref or not tree:
        return False
    try:
        return bool(PN._g_write(["update-ref", ref, tree], folder).ok)
    except Exception:  # noqa: BLE001 — a ref never fails a message
        log.debug("could not keep a checkpoint's ref", exc_info=True)
        return False


def _delete_refs(names: list[str], *, folder: str = "", repo: str = "") -> None:
    """One ``git update-ref --stdin`` in *folder*, or (the folder is gone)
    straight in the repository's git directory *repo*."""
    from ..harness.branches import _g

    body = "".join(f"delete {r}\n" for r in names).encode("utf-8")
    if _usable(folder):
        PN._run_git(["update-ref", "--stdin"], folder, reads=False, input=body)
    elif _usable(repo):
        _g(["--git-dir", repo, "update-ref", "--stdin"], repo, reads=False, input=body)


def drop(folder: str, refs: list[str], repo: str = "") -> None:
    """Delete these refs in one git call. Never raises."""
    names = [r for r in refs if isinstance(r, str) and r.startswith(REF_ROOT + "/")]
    if not names:
        return
    try:
        _delete_refs(names, folder=folder, repo=repo)
    except Exception:  # noqa: BLE001
        log.debug("could not drop checkpoint refs", exc_info=True)


def drop_rows(folder: str, rows: list[dict[str, Any]]) -> None:
    """The refs of rows that left the record (each through its own
    repository when the folder is gone). Never raises."""
    by: dict[str, list[str]] = {}
    for r in rows:
        ref = str(r.get("ref") or "")
        if ref:
            by.setdefault(str(r.get("repo") or ""), []).append(ref)
    for repo, refs in by.items():
        drop(folder, refs, repo)


def forget_session(home: Any, sid: str, folder: str) -> int:
    """A deleted session's checkpoint refs go with it (git then cleans the
    trees up in its own time): looked for in the folder AND in every
    repository its rows name, so a folder that is already gone (a removed
    worktree) leaves nothing behind. Returns how many it named. Never raises.
    Call it BEFORE the session's record is moved away."""
    try:
        # v1.546.0: a store of its own goes with it. And the sweep below still
        # runs: a folder that became a repository later (or a folder above it)
        # may hold names of this session too (review: they outlived it).
        forget_own_store(home, sid)
        root = ref_for(sid, "x").rsplit("/", 1)[0] if ref_for(sid, "x") else ""
        if not root:
            return 0
        from ..harness import branches as B

        if B.git_path() is None:
            return 0
        places: list[tuple[str, str]] = []
        if _usable(folder):
            places.append(("", folder))
        for repo in sorted({str(r.get("repo") or "") for r in PN.read_trees(home, sid)}):
            if _usable(repo):
                places.append((repo, repo))
        named: set[str] = set()
        for gitdir, cwd in places:
            pre = ["--git-dir", gitdir] if gitdir else []
            got = PN._git([*pre, "for-each-ref", "--format=%(refname)", root + "/"], cwd)
            names = [ln.strip() for ln in got.out.splitlines() if ln.strip()] if got.ok else []
            names = [n for n in names if n.startswith(root + "/")]
            if names:
                _delete_refs(names, folder="" if gitdir else cwd, repo=gitdir)
                named.update(names)
        return len(named)
    except Exception:  # noqa: BLE001
        log.debug("could not forget a session's checkpoint refs", exc_info=True)
        return 0


@_stored
def forget_turns(home: Any, sid: str, folder: str, kept_turns: set[str]) -> int:
    """A conversation rewind dropped messages: their checkpoints go with them
    (no message is left to rewind to). Returns how many. Never raises."""
    try:
        with PN._TREES_LOCK:
            rows = PN.read_trees(home, sid)
            gone = [r for r in rows if r.get("how") == HOW_START and str(r.get("turn") or "") not in kept_turns]
            if gone:
                PN._write_trees(home, sid, [r for r in rows if r not in gone])
        drop_rows(folder, gone)
        return len(gone)
    except Exception:  # noqa: BLE001 — the rewind itself is done
        log.debug("could not forget rewound-away checkpoints", exc_info=True)
        return 0


def tree_kept(folder: str, tree: str) -> bool:
    """Does the repository still hold this tree? (``git gc`` can delete one
    recorded before refs were kept.)"""
    if not tree or not re.fullmatch(r"[0-9a-f]{40,64}", str(tree)):
        return False
    return bool(PN._git(["cat-file", "-e", f"{tree}^{{tree}}"], folder).ok)


def trim(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(kept, dropped)``: each kind of checkpoint has its own limit, so idle
    reads can never push a message's checkpoint out (they shared one limit of
    60). Order is kept."""
    limits = {HOW_START: MAX_STARTS, HOW_SEEN: MAX_SEEN, HOW_BEFORE: MAX_BEFORE}
    seen: dict[str, int] = {}
    keep_at: set[int] = set()
    for i in range(len(rows) - 1, -1, -1):
        how = str(rows[i].get("how") or "")
        n = seen.get(how, 0)
        if n < limits.get(how, MAX_SEEN):
            keep_at.add(i)
            seen[how] = n + 1
    kept = [r for i, r in enumerate(rows) if i in keep_at]
    dropped = [r for i, r in enumerate(rows) if i not in keep_at]
    return kept, dropped


def head_of(folder: str) -> str:
    """The commit HEAD names now ("" when none, or git did not say)."""
    try:
        got = PN._git(["rev-parse", "-q", "--verify", "HEAD^{commit}"], folder)
        return got.out.strip() if got.ok else ""
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# the plan
# --------------------------------------------------------------------------- #


def start_row(home: Any, sid: str, turn_id: str) -> dict[str, Any] | None:
    """The checkpoint recorded when *turn_id* started (None when none)."""
    for row in PN.read_trees(home, sid):
        if row.get("how") == HOW_START and row.get("turn") == turn_id:
            return row
    return None


def _raw(folder: str, a: str, b: str) -> list[tuple[str, str, str, str]]:
    """``[(status, src mode, dst mode, rel)]`` between two trees, inside the
    session's folder (renames off: every path is its own row)."""
    got = PN._git(["diff", "--raw", "-z", "--no-renames", "--no-abbrev", "--relative", a, b, "--", "."], folder)
    if not got.ok:
        raise PN.GitUnreadable(got)
    out: list[tuple[str, str, str, str]] = []
    parts = got.out.split("\0")
    i = 0
    while i + 1 < len(parts):
        head, rel = parts[i], parts[i + 1]
        i += 2
        if not head.startswith(":"):
            continue
        cols = head[1:].split(" ")
        if len(cols) < 5 or not rel:
            continue
        out.append((cols[4][:1], cols[0], cols[1], rel))
    return out


def _lstat(path: str) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except (OSError, ValueError):
        return None


def _link(path: str, st: os.stat_result) -> bool:
    from ..harness.code_editor import _is_link

    return _is_link(path, st)


def _writable(path: str) -> bool:
    """Can this existing file be written now? A file another program holds
    open without sharing cannot (Windows). A read-only file is not judged
    (git replaces those itself)."""
    try:
        if not os.path.lexists(path) or os.path.isdir(path):
            return True
        if not os.access(path, os.W_OK):
            return True
        fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        os.close(fd)
        return True
    except PermissionError:
        return False
    except OSError:
        return True


def _only_new_inside(path: str, rel: str, new: set[str]) -> bool:
    """Does the folder *path* (in a file's place) hold ONLY files that are
    about to be moved to the trash? A link inside, or a file that is in no
    checkpoint (git ignores it, or it sits in a nested repository), answers
    no: git would delete the whole folder, that file with it. Links are never
    followed."""
    seen = 0
    todo = [(path, rel)]
    while todo:
        at, at_rel = todo.pop()
        try:
            entries = list(os.scandir(at))
        except OSError:
            return False
        for e in entries:
            seen += 1
            if seen > WALK_MAX:
                return False
            child_rel = f"{at_rel}/{e.name}"
            st = _lstat(e.path)
            if st is None or _link(e.path, st):
                return False
            if stat.S_ISDIR(st.st_mode):
                todo.append((e.path, child_rel))
            elif child_rel not in new:
                return False
    return True


def _parent_in_the_way(folder: str, rel: str, new: set[str]) -> bool:
    """Is a part of *rel*'s own folder path a FILE now (not one that is about
    to be moved)? git would delete it to make the folder."""
    parts = rel.split("/")[:-1]
    for i in range(len(parts)):
        sub = "/".join(parts[: i + 1])
        st = _lstat(os.path.join(folder, *parts[: i + 1]))
        if st is None:
            return False
        if _link(os.path.join(folder, *parts[: i + 1]), st):
            return True
        if not stat.S_ISDIR(st.st_mode):
            return sub not in new
    return False


def _endings(folder: str) -> str:
    """Why a restored file's line endings can differ from what it had ("" when
    nothing here changes them). Never raises."""
    if _OWN.get() is not None:
        return ""  # v1.546.0: the store reads and writes bytes (``OWN_ATTRIBUTES``)
    try:
        got = PN._git(["config", "--get", "core.autocrlf"], folder)
        value = got.out.strip().lower() if got.ok else ""
        if value in ("true", "input"):
            return ENDINGS.format(why=f"core.autocrlf is {value}")
        at = os.path.abspath(folder)
        for _ in range(40):
            if os.path.isfile(os.path.join(at, ".gitattributes")):
                return ENDINGS.format(why="a .gitattributes file")
            if os.path.lexists(os.path.join(at, ".git")) or os.path.dirname(at) == at:
                break
            at = os.path.dirname(at)
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _born(st: os.stat_result) -> float | None:
    """When the file was MADE, where the system says (Windows; macOS). None
    elsewhere: a change time is not a birth time."""
    born = getattr(st, "st_birthtime", None)
    if born is None and os.name == "nt":
        born = st.st_ctime
    return float(born) if born else None


def _unwatched(folder: str) -> list[str]:
    """The files in the folder git is TOLD not to watch (``assume-unchanged``,
    ``skip-worktree``) and that are on disk: ``git add`` skips them, so a
    checkpoint holds the index's old version of them, never the file. Never
    raises."""
    try:
        got = PN._git(["ls-files", "-v", "-z", "--", "."], folder)
        if not got.ok:
            return []
        out = []
        for entry in got.out.split("\0"):
            tag, _, rel = entry.partition(" ")
            if rel and (tag == "S" or tag.islower()) and os.path.lexists(os.path.join(folder, *rel.split("/"))):
                out.append(rel)
        return out
    except Exception:  # noqa: BLE001
        return []


def _plan(home: Any, sid: str, folder: str, tree: str, *, head: str = "", since: Any = None) -> dict[str, Any]:
    """What putting *folder* back to *tree* would do. Reads only. *since*:
    when that tree was recorded (a datetime) — a file MADE before it that the
    tree does not hold was ignored then, whatever the rules say now."""
    now = PN.snapshot_tree(folder, keep=PN.kept_index(home, sid))
    left: list[dict[str, str]] = []
    problems: list[dict[str, str]] = []
    rows: list[tuple[str, str]] = []  # (status, clean rel) of the rows that may be acted on
    for status, src, dst, rel in _raw(folder, tree, now):
        if "160000" in (src, dst):
            left.append({"rel": rel, "why": WHY_SUBMODULE})
            continue
        if "120000" in (src, dst):
            left.append({"rel": rel, "why": WHY_LINK})
            continue
        try:
            if "�" in rel:  # a name git's output could not spell here
                raise PN.RevertRefused(PN.REVERT_BAD_PATH, 400)
            rows.append((status, PN._clean_rel(folder, rel)))
        except PN.RevertRefused:
            left.append({"rel": rel, "why": WHY_PATH})
    # The files that would move to the trash, before any of them is judged: a
    # folder or a file in another path's way is fine when it is one of these.
    new_all = {rel for status, rel in rows if status == "A"}
    folded = {rel.casefold(): rel for rel in new_all}
    case_only: set[str] = set()
    back: list[str] = []
    gone: list[str] = []  # the part of ``back`` that is not in the folder now
    for status, clean in rows:
        if status == "A":
            continue
        path = os.path.join(folder, *clean.split("/"))
        st = _lstat(path)
        if st is not None and _link(path, st):
            left.append({"rel": clean, "why": WHY_LINK})
            continue
        if st is not None and stat.S_ISDIR(st.st_mode):
            if not _only_new_inside(path, clean, new_all):
                problems.append({"rel": clean, "why": WHY_FOLDER})
                continue
        elif status == "D" and st is not None:
            twin = folded.get(clean.casefold())
            if twin is not None and twin != clean:
                # The same file under a name that differs only in case (a
                # file system that ignores case): neither is touched.
                case_only.add(twin)
                left.append({"rel": clean, "why": WHY_CASE})
            else:
                # On disk, yet not in the folder's tree: git ignores it now.
                # Its content is in NO checkpoint, so it is never written over.
                left.append({"rel": clean, "why": _say(WHY_IGNORED)})
            continue
        if _parent_in_the_way(folder, clean, new_all):
            problems.append({"rel": clean, "why": WHY_PARENT})
            continue
        if not _writable(path):
            problems.append({"rel": clean, "why": WHY_LOCKED})
            continue
        back.append(clean)
        if status == "D":
            gone.append(clean)
    new: list[str] = []
    # A file made BEFORE the checkpoint that the checkpoint does not hold was
    # ignored then (a tree holds every file git did not ignore): it is in no
    # checkpoint, so it is never called "made since" (review: a rule in the
    # repository's root .gitignore, in .git/info/exclude or in a global file
    # was removed since, and the user's own .env went to the trash).
    cutoff = since.timestamp() - 2.0 if since is not None else None
    needed = set(back)
    for status, clean in rows:
        if status != "A" or clean in case_only:
            continue
        path = os.path.join(folder, *clean.split("/"))
        st = _lstat(path)
        born = _born(st) if st is not None else None
        in_the_way = any(b == clean or b.startswith(clean + "/") or clean.startswith(b + "/") for b in needed)
        if cutoff is not None and born is not None and born < cutoff and not in_the_way:
            left.append({"rel": clean, "why": _say(WHY_OLDER)})
            continue
        if not _writable(path):
            problems.append({"rel": clean, "why": WHY_LOCKED})
            continue
        new.append(clean)
    listed = {x["rel"] for x in left}
    for rel in _unwatched(folder):
        if rel not in listed:
            left.append({"rel": rel, "why": WHY_UNWATCHED})
    commits = 0
    if head:
        got = PN._git(["rev-list", "--count", f"{head}..HEAD"], folder)
        if got.ok and got.out.strip().isdigit():
            commits = int(got.out.strip())
    plan_id = hashlib.sha1(f"{tree}:{now}".encode("ascii", "replace")).hexdigest()[:16]
    return {
        "id": plan_id, "tree": tree, "now": now, "own": _OWN.get() is not None,
        "back": back, "gone": gone, "new": new, "left": left, "problems": problems,
        "commits_since": commits,
        "endings": _endings(folder) if back else "",
        "can": not problems and len(back) + len(new) <= RESTORE_MAX_FILES,
    }


def _checked(row: Any) -> str:
    folder = str(getattr(row, "folder", "") or "")
    if not folder:
        raise RestoreRefused(PN.NO_FOLDER)
    if PN._network(folder):
        raise RestoreRefused(PN.ON_SHARE)
    if not os.path.isdir(folder):
        raise RestoreRefused(PN.FOLDER_GONE)
    from ..harness import branches as B

    if B.git_path() is None:
        raise RestoreRefused(NO_GIT)
    inside = PN._git(["rev-parse", "--is-inside-work-tree"], folder)
    if not inside.ok or inside.out.strip() != "true":
        # v1.546.0: a folder in no repository keeps checkpoints of its own
        # from its next message on (``in_own_store``); this one has none yet.
        raise RestoreRefused(NO_CHECKPOINT)
    return folder


def public(plan: dict[str, Any]) -> dict[str, Any]:
    """A plan as the screen gets it: counts exact, names capped, no tree ids
    but the plan's own id (what the second press sends back)."""
    def names(rows: list[Any]) -> list[Any]:
        return rows[:LIST_SHOWN]

    n = len(plan["back"]) + len(plan["new"])
    return {
        "id": plan["id"],
        "can": bool(plan["can"]),
        "back": names(plan["back"]), "back_count": len(plan["back"]),
        "new": names(plan["new"]), "new_count": len(plan["new"]),
        "left": names(plan["left"]), "left_count": len(plan["left"]),
        "problems": names(plan["problems"]), "problems_count": len(plan["problems"]),
        "commits_since": int(plan.get("commits_since") or 0),
        "endings": str(plan.get("endings") or ""),
        "too_many": (TOO_MANY_OWN if plan.get("own") else TOO_MANY).format(n=n) if n > RESTORE_MAX_FILES else "",
        # v1.546.0: kept in Iron Jarvis's own store (a folder in no git
        # repository): the screen's words then name no git.
        "own": bool(plan.get("own")),
    }


@_stored
def plan(home: Any, sid: str, row: Any, turn_id: str) -> dict[str, Any]:
    """What "put the files back to before this message" would do (reads
    only). Raises :class:`RestoreRefused` when there is no usable
    checkpoint."""
    folder = _checked(row)
    entry = start_row(home, sid, turn_id) if turn_id else None
    if entry is None:
        raise RestoreRefused(NO_CHECKPOINT)
    tree = str(entry.get("tree") or "")
    try:
        if not tree_kept(folder, tree):
            raise RestoreRefused(CHECKPOINT_GONE)
        _own_limits(folder)  # v1.546.0
        return _plan(home, sid, folder, tree, head=str(entry.get("head") or ""),
                     since=PN._at(entry.get("at")))
    except PN.GitUnreadable as gone:
        raise RestoreRefused(_unread_words(gone)) from None


# --------------------------------------------------------------------------- #
# the restore
# --------------------------------------------------------------------------- #


def _index_dir(home: Any) -> Path:
    return Path(home).joinpath(*PN.KEPT_INDEX_DIR, "restore")


def _restore_paths(folder: str, tree: str, rels: list[str], index_dir: Any) -> Any:
    """``git restore --source=<tree> --worktree`` for exactly these paths
    (each a literal pathspec), through a PRIVATE index file: the user's own
    index is neither changed nor locked (measured: with their index, git holds
    ``.git/index.lock`` for the whole write and a git stopped half way leaves
    it there, so their next ``git add`` fails)."""
    from ..git.integration import git_env, run_git

    body = "\0".join(PN._literal(r) for r in rels).encode("utf-8")
    where = Path(index_dir)
    where.mkdir(parents=True, exist_ok=True)
    idx = where / f"{uuid.uuid4().hex}.idx"
    env = git_env({"GIT_INDEX_FILE": str(idx), **store_env(folder)})  # (v1.546.0: or the session's own store)
    # The user's own hooks stay out of it (measured: ``post-checkout`` ran
    # inside the restore and saw the PRIVATE index as the repository's state).
    no_hooks = where / "no-hooks"
    no_hooks.mkdir(exist_ok=True)
    try:
        return run_git(
            ["-c", "core.quotepath=off", "-c", f"core.hooksPath={no_hooks}",
             "restore", f"--source={tree}", "--worktree",
             "--pathspec-from-file=-", "--pathspec-file-nul"],
            Path(folder), env=env, identity=False, encoding="utf-8", timeout=RESTORE_S, input=body,
        )
    finally:
        for leftover in (idx, idx.with_name(idx.name + ".lock")):
            try:
                leftover.unlink()
            except OSError:
                pass


def _trash_root(home: Any, sid: str) -> Path:
    stamp = PN._now_utc().strftime("%Y%m%d-%H%M%S-%f")
    return Path(home) / "trash" / stamp / "code-rewind" / str(sid)


def _move_new(folder: str, dest_root: Path, rels: list[str], moved: list[tuple[str, str]]) -> None:
    """MOVE the files made since into the trash (never delete). Each move is
    added to *moved* as it happens, so a failure at any point (making the
    trash folder included) leaves the caller knowing exactly what to move
    back. Raises ``OSError``."""
    for rel in rels:
        src = os.path.join(folder, *rel.split("/"))
        if not os.path.lexists(src):
            continue
        dest = dest_root.joinpath(*rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(src, str(dest))
        moved.append((rel, str(dest)))


def _copy_replaced(folder: str, dest_root: Path, rels: list[str], copied: list[tuple[str, str]]) -> None:
    """COPY every file git is about to write over into the trash first (the
    v1.510.0 rule of the per-file Revert: no copy, no restore). Raises
    ``OSError``."""
    for rel in rels:
        src = os.path.join(folder, *rel.split("/"))
        st = _lstat(src)
        if st is None or not stat.S_ISREG(st.st_mode) or _link(src, st):
            # git read it a moment ago (it is in the folder's tree), so "not
            # there" here means it cannot be read: never written over blind.
            raise OSError(f"no copy can be made of {rel}")
        dest = dest_root.joinpath(*rel.split("/"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, str(dest))
        copied.append((rel, str(dest)))


def _still_clear(folder: str, back: list[str], gone: set[str]) -> None:
    """Right before git writes: nothing that is in no checkpoint may stand
    where a file must go (the plan checked this; the folder is looked at
    again as it NOW is, after the new files moved out). Raises
    :class:`_Stopped`."""
    for rel in back:
        path = os.path.join(folder, *rel.split("/"))
        st = _lstat(path)
        if st is not None and (_link(path, st) or stat.S_ISDIR(st.st_mode) or rel in gone):
            raise _Stopped(WHY_IN_THE_WAY)
        at = folder
        for part in rel.split("/")[:-1]:
            at = os.path.join(at, part)
            ast = _lstat(at)
            if ast is None:
                break
            if _link(at, ast) or not stat.S_ISDIR(ast.st_mode):
                raise _Stopped(WHY_IN_THE_WAY)


def _drop_empty_folders(folder: str, rels: list[str]) -> None:
    """A folder that stands where a file must go and holds NO file at all
    (only empty folders) is removed: it holds nothing, and git would refuse
    nothing there anyway (review: the preview said yes and the press then
    refused). A folder with any file or link in it is not touched."""
    for rel in rels:
        top = os.path.join(folder, *rel.split("/"))
        st = _lstat(top)
        if st is None or _link(top, st) or not stat.S_ISDIR(st.st_mode):
            continue
        dirs: list[str] = []
        clean = True
        todo = [top]
        while todo and clean and len(dirs) <= WALK_MAX:
            at = todo.pop()
            dirs.append(at)
            try:
                entries = list(os.scandir(at))
            except OSError:
                clean = False
                break
            for e in entries:
                est = _lstat(e.path)
                if est is None or _link(e.path, est) or not stat.S_ISDIR(est.st_mode):
                    clean = False
                    break
                todo.append(e.path)
        if not clean or len(dirs) > WALK_MAX:
            continue
        for d in sorted(dirs, key=len, reverse=True):
            try:
                os.rmdir(d)
            except OSError:
                break


def _prune_empty(folder: str, rels: list[str]) -> None:
    """Remove the folders the moved files left EMPTY (never one that still
    holds anything, never the session's folder itself)."""
    top = os.path.normcase(os.path.abspath(folder))
    for rel in rels:
        d = os.path.dirname(os.path.join(folder, *rel.split("/")))
        for _ in range(64):
            if os.path.normcase(os.path.abspath(d)) == top or not os.path.isdir(d):
                break
            try:
                os.rmdir(d)  # only an empty folder goes
            except OSError:
                break
            d = os.path.dirname(d)


def _roll_back(folder: str, root: Path, copied: list[tuple[str, str]], made_again: list[str],
               moved: list[tuple[str, str]]) -> bool:
    """Return the folder to how it was after a failed restore: the files git
    wrote over come back from their COPIES (exact bytes, no git needed), the
    ones git made again (they were not there) are MOVED aside into the trash,
    then the new files come back out of the trash. True when every step
    worked."""
    ok = True
    for rel, where in copied:
        dest = os.path.join(folder, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copy2(where, dest)
        except OSError:
            ok = False
    aside: list[str] = []
    for rel in made_again:
        src = os.path.join(folder, *rel.split("/"))
        if not os.path.lexists(src):
            continue
        dest = root.joinpath(ROLLED, *rel.split("/"))
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(src, str(dest))
            aside.append(rel)
        except OSError:
            ok = False
    _prune_empty(folder, aside)
    for rel, where in reversed(moved):
        dest = os.path.join(folder, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            if os.path.lexists(dest):
                ok = False  # something stands there now: it is never written over
                continue
            shutil.move(where, dest)
        except OSError:
            ok = False
    return ok


def _is_rules(rel: str) -> bool:
    """A file that changes how git reads OTHER files: its ignore rules, and
    its attributes (a filter or eol line removed since — review: files that
    sort before ``.gitattributes`` were written under the tool's rules)."""
    return rel.rsplit("/", 1)[-1] in (".gitignore", ".gitattributes")


def _ignored_now(folder: str, rels: list[str]) -> set[str]:
    """Which of *rels* the folder's ignore rules cover NOW (by the rules
    alone, whatever any index holds). Unknown answers "none"."""
    if not rels:
        return set()
    try:
        body = "\0".join(rels).encode("utf-8")
        got = PN._git(["check-ignore", "--no-index", "-z", "--stdin"], folder, input=body)
        return {p for p in str(got.out or "").split("\0") if p} & set(rels)
    except Exception:  # noqa: BLE001
        return set()


def _is_as_it_was(home: Any, sid: str, folder: str, tree: str, fallback: bool) -> bool:
    """Is the folder the tree it was before the restore? Read from the folder
    itself (a roll-back that reported a fault may still have left it whole —
    a restore that never started; one that reported none is checked too).
    *fallback* when the folder cannot be read."""
    try:
        return PN.snapshot_tree(folder, keep=PN.kept_index(home, sid)) == tree
    except Exception:  # noqa: BLE001
        return fallback


def _flat(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def _exact_endings(folder: str, rels: list[str], copies: Path) -> set[str]:
    """After an UNDO's git write: a file whose bytes differ from the rewind's
    own copy of it ONLY in line endings gets that copy's exact bytes (git
    wrote the endings its settings give; the copy is the file as it really
    was). Any other difference is left as git wrote it. Returns the files it
    settled (exact now). Never raises."""
    settled: set[str] = set()
    for rel in rels:
        try:
            dest = os.path.join(folder, *rel.split("/"))
            dst_st = _lstat(dest)
            if dst_st is None or not stat.S_ISREG(dst_st.st_mode):
                continue
            for part in (REPLACED, MADE):
                src = copies.joinpath(part, *rel.split("/"))
                st = _lstat(str(src))
                if st is None or not stat.S_ISREG(st.st_mode) or st.st_size > EXACT_MAX_BYTES:
                    continue
                kept, now = src.read_bytes(), Path(dest).read_bytes()
                if _flat(kept) == _flat(now):
                    if kept != now:
                        Path(dest).write_bytes(kept)
                    settled.add(rel)
                break
        except OSError:
            continue
    return settled


def _style(data: bytes) -> str:
    """``crlf`` | ``lf`` when EVERY line of a text ends that way; "" for a
    file with both, with none, or with a NUL byte (not text)."""
    if b"\0" in data:
        return ""
    lines = data.count(b"\n")
    if not lines:
        return ""
    crlf = data.count(b"\r\n")
    if crlf == lines:
        return "crlf"
    return "lf" if crlf == 0 else ""


def _autocrlf(folder: str) -> str:
    try:
        got = PN._git(["config", "--get", "core.autocrlf"], folder)
        return got.out.strip().lower() if got.ok else ""
    except Exception:  # noqa: BLE001
        return ""


def _attrs(folder: str, rels: list[str]) -> dict[str, dict[str, str]]:
    """``{rel: {"text": .., "eol": ..}}`` as git reads the attributes NOW."""
    out: dict[str, dict[str, str]] = {}
    got = PN._git(["check-attr", "-z", "--stdin", "text", "eol"], folder, input="\0".join(rels).encode("utf-8"))
    parts = str(got.out or "").split("\0") if got.ok else []
    for i in range(0, len(parts) - 2, 3):
        out.setdefault(parts[i], {})[parts[i + 1]] = parts[i + 2]
    return out


def _tree_blobs(folder: str, tree: str) -> dict[str, str]:
    """``{rel: blob id}`` of the checkpoint's files under the folder."""
    got = PN._git(["ls-tree", "-r", "-z", tree], folder)
    out: dict[str, str] = {}
    for entry in (str(got.out or "").split("\0") if got.ok else []):
        head, _, rel = entry.partition("\t")
        cols = head.split(" ")
        if rel and len(cols) == 3 and cols[1] == "blob":
            out[rel] = cols[2]
    return out


def _blob_id(data: bytes, like: str) -> str:
    h = hashlib.sha256() if len(like) == 64 else hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def _keep_endings(folder: str, tree: str, copied: list[tuple[str, str]], skip: set[str]) -> int:
    """After git's write: a restored file whose line endings are uniformly
    ONE kind, where the version it replaced was uniformly the OTHER, is
    rewritten in the replaced version's kind — but ONLY where the checkpoint
    cannot say which kind the file had, i.e. git's own conversion decides it:

    * ``core.autocrlf`` is on, or the file's ``text`` attribute is set;
    * no ``eol=`` attribute names the kind, and the file is not ``-text`` or
      ``binary`` (then git's answer is the user's own rule, or exact);
    * the checkpoint's blob IS the file with LF endings (so both kinds of
      the file are that same blob to git, and ``git status`` stays clean).

    Anywhere else the checkpoint is exact and git's write is left alone
    (review: this ran on every restore and re-applied the endings a tool had
    flipped, and changed a byte in a binary file with no NUL in it). Returns
    how many it changed. Never raises."""
    todo: list[tuple[str, Path, bytes, str]] = []
    for rel, copy in copied:
        if rel in skip:
            continue
        try:
            dest = Path(os.path.join(folder, *rel.split("/")))
            st, cst = _lstat(str(dest)), _lstat(copy)
            if (st is None or cst is None or not stat.S_ISREG(st.st_mode)
                    or max(st.st_size, cst.st_size) > EXACT_MAX_BYTES):
                continue
            want = _style(Path(copy).read_bytes())
            now = dest.read_bytes()
            have = _style(now)
            if not want or not have or want == have:
                continue
            todo.append((rel, dest, now, want))
        except OSError:
            continue
    if not todo:
        return 0
    changed = 0
    try:
        converts = _autocrlf(folder) in ("true", "input")
        attrs = _attrs(folder, [rel for rel, _d, _n, _w in todo])
        blobs = _tree_blobs(folder, tree)
    except Exception:  # noqa: BLE001 — unknown means: leave git's write alone
        return 0
    for rel, dest, now, want in todo:
        mine = attrs.get(rel) or {}
        text, eol = mine.get("text", "unspecified"), mine.get("eol", "unspecified")
        if text == "unset" or eol != "unspecified":
            continue
        if not (converts or text in ("set", "auto")):
            continue
        blob = blobs.get(rel) or ""
        if not blob or _blob_id(_flat(now), blob) != blob:
            continue
        try:
            dest.write_bytes(now.replace(b"\n", b"\r\n") if want == "crlf" else _flat(now))
            changed += 1
        except OSError:
            continue
    return changed


def _apply(home: Any, sid: str, folder: str, tree: str, made: dict[str, Any], *,
           exact_from: Path | None = None) -> dict[str, Any]:
    """Put the folder back to *tree* per the plan *made* (already checked), in
    the module's five steps. A failure rolls back and says whether the folder
    is whole (read from the folder itself)."""
    back, new = list(made["back"]), list(made["new"])
    was_gone = set(made.get("gone") or [])
    root = _trash_root(home, sid)
    moved: list[tuple[str, str]] = []
    copied: list[tuple[str, str]] = []
    written: list[str] = []  # the paths git was asked to write so far
    stayed: list[str] = []

    def step(some_new: list[str], some_back: list[str]) -> None:
        """Steps 2 to 5 for one group of paths."""
        _move_new(folder, root / MADE, some_new, moved)
        _prune_empty(folder, [rel for rel, _ in moved])
        _drop_empty_folders(folder, some_back)
        _still_clear(folder, some_back, was_gone)
        try:
            _copy_replaced(folder, root / REPLACED, [r for r in some_back if r not in was_gone], copied)
        except OSError as exc:
            from ..core.failures import plain_failure

            plain_failure(WHY_NO_COPY, exc)  # the raw text is logged, masked
            raise _Stopped(WHY_NO_COPY) from None
        if some_back:
            written.extend(some_back)
            got = _restore_paths(folder, tree, some_back, _index_dir(home))
            if not got.ok:
                raise _Stopped(_say(WHY_GIT_SLOW) if getattr(got, "timed_out", False) else PN._git_reason(got))

    try:
        rules_new = [r for r in new if _is_rules(r)]
        rules_back = [r for r in back if _is_rules(r)]
        if rules_new or rules_back:
            # The ignore rules go back first; a "made since" file the
            # checkpoint's own rules ignore is in no checkpoint: it stays.
            # A "made since" file that stands where a rules file's own
            # folder must go moves out with them (review: it was checked
            # before it had moved, and the press refused).
            first = set(rules_new) | {n for n in new if any(r.startswith(n + "/") for r in rules_back)}
            step([n for n in new if n in first], rules_back)
            rest = [r for r in new if r not in first]
            # ... and one that sits inside a folder that must become a file
            # cannot stay there: it moves to the trash with the others.
            keep_here = {p for p in _ignored_now(folder, rest)
                         if not any(p.startswith(b + "/") for b in back)}
            stayed = [r for r in rest if r in keep_here]
            step([r for r in rest if r not in keep_here], [r for r in back if not _is_rules(r)])
        else:
            step(new, back)
    except Exception as exc:  # noqa: BLE001 — every failure rolls back
        clean = _roll_back(folder, root, copied, [r for r in written if r in was_gone], moved)
        whole = _is_as_it_was(home, sid, folder, str(made["now"]), clean)
        if isinstance(exc, _Stopped):
            why = str(exc)
        else:
            from ..core.failures import plain_failure

            plain_failure(WHY_MOVE, exc)
            why = WHY_MOVE
        if whole:
            raise RestoreRefused(FAILED_ROLLED_BACK.format(why=why), 409) from None
        raise RestoreRefused(FAILED_PARTIAL.format(why=why, trash=str(root)), 500) from None
    settled = _exact_endings(folder, back, exact_from) if exact_from is not None else set()
    _keep_endings(folder, tree, copied, settled)
    kept = bool(moved or copied)
    return {"back": len(back), "moved": len(moved), "replaced": len(copied), "stayed": len(stayed),
            "trash": str(root) if kept else ""}


def _words(result: dict[str, Any], made: dict[str, Any]) -> str:
    if not result["back"] and not result["moved"]:
        words = DONE_NOTHING
    else:
        words = DONE.format(back=result["back"], moved=result["moved"])
        if result.get("replaced"):
            words += DONE_KEPT
    n = int(made.get("commits_since") or 0)
    if n:
        words += DONE_COMMITS.format(n=n, s="" if n == 1 else "s", are="is" if n == 1 else "are")
    left = made.get("left") or []
    if left:
        whys = sorted({str(x.get("why") or "") for x in left})
        words += DONE_LEFT.format(n=len(left), they="it is" if len(left) == 1 else "they are", why="; ".join(whys))
    stayed = int(result.get("stayed") or 0)
    if stayed:
        words += _say(DONE_STAYED).format(n=stayed, they="it is" if stayed == 1 else "they are")
    return words


def _refuse_unless_possible(made: dict[str, Any]) -> None:
    if made["problems"]:
        raise RestoreRefused(PROBLEMS, problems=list(made["problems"][:LIST_SHOWN]))
    if not made["can"]:
        raise RestoreRefused(_say(TOO_MANY).format(n=len(made["back"]) + len(made["new"])))


@_stored
def restore(home: Any, sid: str, row: Any, turn_id: str, *, expect: str = "") -> dict[str, Any]:
    """Put the session's folder back as it was when *turn_id* started.

    Order: the plan again (and it must still be the one the screen showed:
    *expect*) → a ``before_rewind`` checkpoint of the folder as it is, kept by
    a ref and checked → the files (:func:`_apply`). Returns ``{ok, words,
    back, moved, replaced, trash, before}``; raises :class:`RestoreRefused`."""
    made = plan(home, sid, row, turn_id)
    folder = str(row.folder)
    if expect and made["id"] != expect:
        raise RestoreRefused(FOLDER_CHANGED)
    _refuse_unless_possible(made)
    before = _keep_before(home, sid, folder, str(made["now"]), turn_id)
    try:
        result = _apply(home, sid, folder, str(made["tree"]), made)
    except RestoreRefused as r:
        if r.status == 409:
            # The folder is as it was: this attempt is nothing to undo, and
            # it must not hide an earlier rewind's Undo either (``void`` rows
            # are skipped; review: marking it "undone" ended the search).
            PN.mark_tree(home, sid, before, HOW_BEFORE, void=True)
        raise
    if not result["back"] and not result["moved"]:
        # Nothing was changed, so there is nothing to undo (review: Undo was
        # still offered and later swept the user's own work back).
        PN.mark_tree(home, sid, before, HOW_BEFORE, void=True)
    # Where this rewind's own copies are: an undo makes line endings exact
    # from them.
    PN.mark_tree(home, sid, before, HOW_BEFORE, trash=str(result.get("trash") or ""))
    return {"ok": True, "words": _words(result, made), "before": before, **result}


def _keep_before(home: Any, sid: str, folder: str, tree: str, turn_id: str) -> str:
    """Record the folder as it is NOW (the plan's own snapshot) as a
    ``before_rewind`` checkpoint. No ref, no restore."""
    at = PN._iso(PN._now_utc())
    ref = ref_for(sid, "before-" + re.sub(r"[^0-9]", "", at))
    if not keep(folder, ref, tree) or not tree_kept(folder, tree):
        raise RestoreRefused(NOT_KEPT)
    entry = {"how": HOW_BEFORE, "turn": str(turn_id), "tree": tree, "at": at, "ref": ref}
    repo = repo_of(folder)
    if repo:
        entry["repo"] = repo
    drop_rows(folder, PN._add_tree(home, sid, entry))
    return at


def close_undo(home: Any, sid: str) -> None:
    """A message is starting: every rewind made so far can no longer be
    undone. Said HERE, in the send path, and not read off the message's own
    checkpoint row — that row is missing when the folder could not be read in
    time, and a later conversation rewind deletes it (review: Undo came back
    and wrote over the message's work). Never raises."""
    try:
        with PN._TREES_LOCK:
            rows = PN.read_trees(home, sid)
            hit = False
            for r in rows:
                if r.get("how") == HOW_BEFORE and not r.get("closed"):
                    r["closed"] = True
                    hit = True
            if hit:
                PN._write_trees(home, sid, rows)
    except Exception:  # noqa: BLE001
        log.warning("could not close a Code-view session's rewind undo", exc_info=True)


def close_undo_in_folder(home: Any, engine: Any, sid: str, folder: str) -> None:
    """A message is starting in *folder*: no rewind of ANY session that works
    in it (or in a folder inside it, or one that holds it) can be undone any
    more (review: another session's message left Undo offered, and Undo then
    put that message's work back). Never raises."""
    close_undo(home, sid)
    try:
        if not folder or PN._network(folder):
            return
        from . import store

        mine = os.path.normcase(os.path.abspath(folder))
        for row in store.list_rows(engine, archived=True):
            other = str(getattr(row, "folder", "") or "")
            if row.id == sid or not other or PN._network(other):
                continue
            theirs = os.path.normcase(os.path.abspath(other))
            if theirs == mine or theirs.startswith(mine + os.sep) or mine.startswith(theirs + os.sep):
                close_undo(home, row.id)
    except Exception:  # noqa: BLE001
        log.warning("could not close the rewind undo of the folder's other sessions", exc_info=True)


def undo_row(home: Any, sid: str) -> tuple[dict[str, Any] | None, bool]:
    """``(row, late)``: the newest rewind that is still to be undone (one
    already undone, or one that changed nothing, is skipped), and whether a
    message started after it (then it no longer can be)."""
    rows = PN.read_trees(home, sid)
    for i in range(len(rows) - 1, -1, -1):
        r = rows[i]
        if r.get("how") != HOW_BEFORE or r.get("undone") or r.get("void"):
            continue
        late = bool(r.get("closed")) or any(x.get("how") == HOW_START for x in rows[i + 1:])
        return r, late
    return None, False


def can_undo(home: Any, sid: str) -> bool:
    row, late = undo_row(home, sid)
    return row is not None and not late


@_stored
def undo(home: Any, sid: str, row: Any) -> dict[str, Any]:
    """Undo the newest rewind's FILE changes: the folder goes back to its
    ``before_rewind`` checkpoint, through the same steps as a restore (what
    the undo itself replaces or moves is kept in the trash). The conversation
    is not changed."""
    folder = _checked(row)
    entry, late = undo_row(home, sid)
    if entry is None:
        raise RestoreRefused(NOTHING_TO_UNDO)
    if late:
        raise RestoreRefused(UNDO_TOO_LATE)
    tree = str(entry.get("tree") or "")
    try:
        if not tree_kept(folder, tree):
            raise RestoreRefused(CHECKPOINT_GONE)
        _own_limits(folder)  # v1.546.0
        # (No ``since`` here: the files a rewind made again carry their OLD
        # creation time on Windows — a name deleted and made again within
        # seconds keeps it — so "made before" says nothing in an undo.)
        made = _plan(home, sid, folder, tree)
    except PN.GitUnreadable as gone:
        raise RestoreRefused(_unread_words(gone)) from None
    _refuse_unless_possible(made)
    copies = str(entry.get("trash") or "")
    result = _apply(home, sid, folder, tree, made,
                    exact_from=Path(copies) if copies and os.path.isdir(copies) else None)
    PN.mark_tree(home, sid, str(entry.get("at") or ""), HOW_BEFORE, undone=True)
    words = UNDONE + (UNDONE_KEPT if result.get("trash") else "")
    return {"ok": True, "words": words, **result}
