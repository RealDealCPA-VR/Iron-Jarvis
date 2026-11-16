"""What the AGENT may do with git in a coding session (v1.375.0, IJ harness
part 2 Phase 4; the user's own folder since v1.511.0).

The buttons (Commit, Push, Create PR, Merge, Discard) are a person's actions.
The agent can still run git through its command tools (``shell``,
``process_start``, ``process_write`` — Claude Code's Bash and Codex's
exec_command map onto them). There are two kinds of coding session, and the
guard reads each.

IN THE USER'S OWN FOLDER (v1.511.0, second review T2, the owner's decision
E3). Working in the folder directly is the composer's default kind of session
(its "own branch" switch starts off), so there the agent's git acts on the
user's REAL checkout and remote. In a coding session's turn whose folder is in
a git repository (:data:`TURN_FOLDER`, armed by ``routes/build.
start_build_turn``: the folder, and whether it is in a repository, found ONCE
off the event loop — :func:`folder_facts` — never by a stat while a command is
judged), every action this guard reads EXCEPT A COMMIT asks EVERY TIME, with
words that say it acts on the user's own folder or remote: a push, a force
push, a push that deletes a remote branch, git that throws work away, a
history rewrite, deleting or force-moving a branch or tag, the repository's
worktrees, taking work from the stash, a git setting, a pull request and any
other GitHub change. Nothing is REFUSED there: what a session's own branch
refuses is the user's to allow in their own folder, so it asks instead. A
commit runs under the session's posture, as before. A folder that is not in a
repository, and a command tool's call that is not a coding session's (chat,
agents, schedules), are exactly as before: nothing here asks.

ON A SESSION'S OWN BRANCH, while a turn works on a branch
(``branches.current()``):

* a ``git commit``, a ``git push``, a ``gh pr create`` / ``gh pr merge`` and
  any other ``gh`` command that changes something on GitHub ASK FIRST, in
  EVERY posture (auto-approve included) and past every grant — and so does
  git that THROWS AWAY work not committed yet (``reset --hard``, ``clean
  -f``, ``checkout -- <paths>``, ``restore``, ``stash drop|clear``, ``switch
  --discard-changes``, ``checkout <file>``), taking work from the stash
  (``stash pop|apply|branch`` — the stash is shared by every worktree, so an
  entry may be the user's own) and ``git config`` that WRITES a setting or
  ``git remote add|remove|rename|set-url|…`` (a worktree's repository config
  is the one the user's checkout reads) — each
  tool's ``spend_check`` answers
  ``SpendCheck(ask=True, once_only=True)``: the stream lane's card offers
  "Allow once" and Deny only, and the POST lane, which cannot show a card,
  refuses (``chat_turn.spend_not_asked_invoke_kwargs``, lock-step);
* what the plan rules out is REFUSED outright by the tool itself, whatever
  the posture — and with NO card first (the spend check answers ``ask=False``
  for it, so no lane asks about a call the tool will refuse): a FORCE push
  (``-f``, ``--force*``, ``--mirror``, a ``+refspec``), a push that DELETES a
  remote branch (``--delete``, ``-d``, a ``:ref`` refspec, ``--prune``), a
  history REWRITE (``commit --amend``, ``rebase``, ``pull --rebase``, a
  ``reset`` to another commit, ``filter-branch``, ``filter-repo``,
  ``update-ref``, ``replace``; a ``reset`` to another commit includes
  ``reset <branch or tag>`` — a reset's one word that is not a path of the
  working tree is read as a commit), deleting or force-moving a BRANCH or a tag
  (``branch -d|-D|-f|-m``, ``checkout -B``, ``switch -C``, ``tag -d``),
  changing the user's WORKTREES (``worktree remove|prune|move`` — a prune
  forgets every worktree whose folder is missing right then) and ``gh repo
  delete``.

Everything else stays under the harness guardrail line "only when the user
asks for them" (``components.DEFAULT_GUARDRAILS``).

NOBODY TO ASK (the POST lane, the night shift, a check fix): a once-only card
is refused there, or declined after the night's wait — in both kinds of
session, exactly as for every once-only card.

HOW A COMMAND IS CUT (v1.511.0, T8): a command ends at an unquoted ``;``,
``&&``, ``||``, ``|``, ``&``, ``)`` or line break. Quotes are read the way
BOTH shells read them (bash's backslash escape and PowerShell's backtick
escape, each as its own reading) and every reading's actions are kept, so a
separator inside a quoted message (``git commit -m "fix (a); b" --amend``)
never ends the command; a quote that never closes FAILS CLOSED (the command is
read both cut at its first separator and to the end of the line). Whether a
``reset`` / ``checkout`` word is a path is read from the syntax (``--``) and
an existing file or folder under the session's folder (one look, only for
those two commands); a word that cannot be told is read the stricter way.
The command tools judge a command that names git or gh in a worker thread
(:func:`spend_check_off_loop`, :func:`refusal_off_loop`, v1.516.0), so that
look and the reading of the user's git config never run on the event loop;
past :data:`CHECK_TIMEOUT_S` the check asks, and the run refuses, in words.

HONEST LIMITS: this reads the command's text. It catches git as a model
writes it (``git push``, ``git -C x commit``, ``cmd /c "git push"``,
``bash -lc 'git push'``, ``git.exe push``, an alias given with ``-c
alias.x=…``) and — since the v1.377.0 review, when Phase 6 gave a Build
session's commands the user's own home folder and so made every alias in
their git config LIVE in the agent's shell — an alias of the user's own
(:func:`user_aliases`: ``GIT_CONFIG_GLOBAL``, else ``$XDG_CONFIG_HOME/git/
config`` and ``~/.gitconfig``; the repository's own config; ``[include]`` /
``[includeIf]`` followed; ``GIT_CONFIG_COUNT`` pairs), an alias of an alias
and a shell alias (``!git push --force``). An alias never hides one of git's
own commands (git ignores such an alias, and so does this guard — an alias
``push = status`` must not make a real push read as a status). A script file,
the system-wide config or code that builds the command at run time is not
caught. A command that only MENTIONS git (``echo git push``) is read as if it
ran it. It is a guardrail against a careless model, not a sandbox.
"""


from __future__ import annotations

import os
import re
from contextvars import ContextVar, Token
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Where a git (or gh) program name starts in a command line.
_PROG_RX = re.compile(
    r"""(?:^|[\s;&|(`"'])(?:[^\s;&|(`"']*[\\/])?(?P<prog>git|gh)(?:\.exe)?(?=[\s"'])""",
    re.IGNORECASE,
)
#: A line continuation (bash's ``\`` / cmd's ``^`` right before a line break).
_CONTINUATION_RX = re.compile(r"[\\^]\r?\n")
#: What ends one command of a line.
_END_RX = re.compile(r"&&|\|\||[;|\n\r)]|(?<![<>0-9])&(?![>&])")
_TOKEN_RX = re.compile(r'"[^"]*"|\'[^\']*\'|\S+')
#: git's global options that take a separate value.
_GLOBAL_WITH_VALUE = frozenset({"-c", "-C", "--git-dir", "--work-tree", "--namespace", "--config-env",
                                "--exec-path", "--super-prefix", "--list-cmds"})


@dataclass(frozen=True)
class GitAct:
    """One git action found in a command. CARDED (asks every time):
    ``commit`` | ``push`` | ``pr`` (gh pr create / merge) | ``gh`` (another
    gh command that changes something) | ``discard`` (throws away work not
    committed) | ``stash`` (takes work from the shared stash) | ``config``
    (``git config`` that writes a setting, ``git remote`` that changes one).
    REFUSED on a session's own branch (asked every time in the user's own
    folder): ``force`` (a force push) | ``delete_remote`` (a
    push that deletes a remote branch) | ``rewrite`` (a history rewrite) |
    ``delete_branch`` (deleting or force-moving a branch or tag) |
    ``worktree`` (``worktree remove|prune|move``) | ``delete_repo``."""

    kind: str
    remote: str = ""


#: The kinds the tool refuses outright (the plan: no force push, no history
#: rewrite, no deleting a branch).
REFUSED_KINDS = frozenset({"force", "delete_remote", "rewrite", "delete_branch", "worktree", "delete_repo"})

#: Characters a quoted command line leaves around a word (``cmd /c "git
#: push"`` reads ``push"``; ``bash -lc 'git push'`` reads ``push'``).
_STRAY = "\"'`"


# --------------------------------------------------------------------------- #
# the user's own folder (v1.511.0, second review T2, decision E3)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class TurnFolder:
    """A coding session's turn that works in its folder directly."""

    #: The session's working folder.
    folder: str
    #: The top folder of the git repository it is in ("" = not in one).
    repo: str = ""
    #: That repository's config file, for the user's own aliases ("" = none).
    config: str = ""


#: The folder of THIS coding session's turn (``routes/build.start_build_turn``
#: arms it beside the branch; the driver task and its helpers copy it). None
#: for every other caller of a command tool: chat, agents, schedules.
TURN_FOLDER: "ContextVar[TurnFolder | None]" = ContextVar("ij_git_guard_folder", default=None)

#: How many folders up a repository's ``.git`` is looked for.
_REPO_DEPTH = 64


def folder_facts(folder: Any) -> "TurnFolder | None":
    """The :class:`TurnFolder` of ``folder``: is it in a git repository (a
    ``.git`` folder, or a worktree's ``.git`` file, in it or a folder above)?
    BLOCKING (a few stats): the caller runs it off the event loop, once per
    turn. Never raises (a folder that cannot be read is not a repository).
    None for no folder."""
    text = str(folder or "").strip()
    if not text:
        return None
    repo = ""
    try:
        here = Path(text).resolve()
        for i, p in enumerate((here, *here.parents)):
            if i > _REPO_DEPTH:
                break
            if os.path.lexists(p / ".git"):
                repo = str(p)
                break
    except (OSError, ValueError, RuntimeError):
        repo = ""
    cfg = _repo_config(repo) if repo else None
    return TurnFolder(folder=text, repo=repo, config=str(cfg) if cfg is not None else "")


def set_turn_folder(facts: "TurnFolder | None") -> "Token[TurnFolder | None]":
    """Arm this coding session's turn folder (None = not a coding session)."""
    return TURN_FOLDER.set(facts)


def reset_turn_folder(token: "Token[TurnFolder | None]") -> None:
    try:
        TURN_FOLDER.reset(token)
    except (ValueError, LookupError):  # another context's token
        TURN_FOLDER.set(None)


def _work_folder() -> str:
    """The folder the turn's commands run in: the branch's worktree folder,
    else the user's own folder, else ""."""
    from .branches import current

    tb = current()
    if tb is not None:
        return tb.folder or tb.worktree
    facts = TURN_FOLDER.get()
    return facts.folder if facts is not None else ""


#: A word that is a pattern of paths (git reads it as a pathspec).
_GLOB_RX = re.compile(r"[*?\[]")


def _in_tree(word: str) -> bool:
    """Does ``word`` name a file or folder of the turn's working tree (or a
    pattern of paths)? One ``lexists``, only for a ``reset`` / ``checkout``
    word. False when there is no folder to look in."""
    if _GLOB_RX.search(word):
        return True
    folder = _work_folder()
    if not folder or not word or word.startswith("-"):
        return False
    try:
        path = word if os.path.isabs(word) else os.path.join(folder, word)
        return os.path.lexists(path)
    except (OSError, ValueError):
        return False


def _tokens(segment: str) -> list[str]:
    out = []
    for t in _TOKEN_RX.findall(segment):
        if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'":
            t = t[1:-1]
        else:
            t = t.strip(_STRAY)
        if t:
            out.append(t)
    return out


#: git's OWN commands. git ignores an alias that would hide one ("aliases
#: that hide existing Git commands are ignored"), so this guard never expands
#: one either: ``git -c alias.push=status push`` is a real push.
_GIT_COMMANDS = frozenset("""
add am annotate apply archive backfill bisect blame branch bugreport bundle cat-file check-attr
check-ignore check-mailmap check-ref-format checkout checkout-index cherry cherry-pick citool clean
clone column commit commit-graph commit-tree config count-objects credential describe diagnose diff
diff-files diff-index diff-tree difftool fast-export fast-import fetch fetch-pack filter-branch fmt-merge-msg
for-each-ref for-each-repo format-patch fsck gc get-tar-commit-id grep gui hash-object help hook
index-pack init instaweb interpret-trailers log ls-files ls-remote ls-tree mailinfo mailsplit
maintenance merge merge-base merge-file merge-index merge-tree mergetool mktag mktree multi-pack-index
mv name-rev notes pack-objects pack-refs prune prune-packed pull push range-diff read-tree rebase
reflog refs remote repack replace replay request-pull rerere reset restore rev-list rev-parse revert
rm send-email shortlog show show-branch show-index show-ref sparse-checkout stash status stripspace
submodule switch symbolic-ref tag unpack-objects update-index update-ref update-server-info var
verify-commit verify-pack verify-tag version whatchanged worktree write-tree
""".split())
#: How deep an alias of an alias (of an alias…) is followed.
_ALIAS_DEPTH = 6
#: A SHELL alias (``!…``) is returned as this subcommand; :func:`classify`
#: judges its text as a command line of its own.
_SHELL_ALIAS = "!"


def _subcommand(tokens: list[str], depth: int = 0) -> "tuple[str, list[str]]":
    aliases: dict[str, str] = {}
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _GLOBAL_WITH_VALUE:
            if tok == "-c" and i + 1 < len(tokens):
                # `git -c alias.p=push p` — an alias given on the line itself.
                m = re.fullmatch(r"alias\.([^=]+)=(.*)", tokens[i + 1], re.S | re.I)
                if m:
                    aliases[m.group(1).lower()] = m.group(2)
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        sub, rest = tok.lower(), tokens[i + 1:]
        if sub in _GIT_COMMANDS or depth >= _ALIAS_DEPTH:
            return sub, rest
        # v1.377.0 review: the user's own aliases (live in a Build session's
        # shell since Phase 6) after the line's own `-c alias.*`.
        body = aliases.get(sub)
        if body is None:
            body = user_aliases().get(sub)
        if body is None:
            return sub, rest
        body = body.strip()
        if body.startswith("!"):
            return _SHELL_ALIAS, [body[1:]]
        expanded = _tokens(body)
        if not expanded:
            return sub, rest
        return _subcommand(expanded + rest, depth + 1)
    return "", []


# --------------------------------------------------------------------------- #
# the user's own git aliases (v1.377.0 review)
# --------------------------------------------------------------------------- #

#: path -> ((mtime_ns, size), {alias: body}, [included paths]) — one stat per
#: file per judgment; a file is re-read only when it changed.
_ALIAS_CACHE: "dict[str, tuple[tuple[int, int], dict[str, str], list[str]]]" = {}
#: A config file larger than this is not read (git configs are a few KB).
_CONFIG_MAX_BYTES = 512 * 1024
#: How deep ``[include]`` / ``[includeIf]`` is followed.
_INCLUDE_DEPTH = 4
_SECTION_RX = re.compile(r'^\[\s*([A-Za-z0-9.-]+)(?:\s+"(?:[^"\\]|\\.)*")?\s*\]')
_KEY_RX = re.compile(r"^([A-Za-z][A-Za-z0-9-]*)\s*(?:=\s*(.*))?$")


def _value(raw: str) -> str:
    """A git config value: quotes removed, an unquoted ``#`` / ``;`` comment
    cut, ``\\"`` / ``\\\\`` unescaped."""
    out: list[str] = []
    quoted = False
    i = 0
    while i < len(raw):
        ch = raw[i]
        if ch == "\\" and i + 1 < len(raw):
            nxt = raw[i + 1]
            out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
            i += 2
            continue
        if ch == '"':
            quoted = not quoted
        elif ch in "#;" and not quoted:
            break
        else:
            out.append(ch)
        i += 1
    return "".join(out).strip()


def _read_config(path: Path) -> "tuple[dict[str, str], list[str]]":
    """``({alias: body}, [include paths as written])`` of ONE config file
    (cached by its stat). Never raises: an unreadable file has none."""
    try:
        st = path.stat()
    except OSError:
        return {}, []
    key = str(path)
    sig = (st.st_mtime_ns, st.st_size)
    hit = _ALIAS_CACHE.get(key)
    if hit is not None and hit[0] == sig:
        return hit[1], hit[2]
    aliases: dict[str, str] = {}
    includes: list[str] = []
    try:
        if st.st_size > _CONFIG_MAX_BYTES:
            raise OSError("too large")
        text = path.read_bytes().decode("utf-8", "replace")
    except OSError:
        text = ""
    section = ""
    lines = text.replace("\r\n", "\n").split("\n")
    joined: list[str] = []
    buf = ""
    for line in lines:
        if line.endswith("\\") and not line.endswith("\\\\"):
            buf += line[:-1]
            continue
        joined.append(buf + line)
        buf = ""
    if buf:
        joined.append(buf)
    for raw in joined:
        line = raw.strip()
        if not line or line[0] in "#;":
            continue
        m = _SECTION_RX.match(line)
        if m:
            section = m.group(1).lower()
            line = line[m.end():].strip()
            if not line:
                continue
        km = _KEY_RX.match(line)
        if not km:
            continue
        name, val = km.group(1).lower(), _value(km.group(2) or "")
        if section == "alias" and val:
            aliases[name] = val
        elif section in ("include", "includeif") and name == "path" and val:
            includes.append(val)
    _ALIAS_CACHE[key] = (sig, aliases, includes)
    if len(_ALIAS_CACHE) > 64:
        _ALIAS_CACHE.pop(next(iter(_ALIAS_CACHE)), None)
    return aliases, includes


def _collect(path: Path, out: dict[str, str], depth: int, seen: set[str]) -> None:
    key = os.path.normcase(str(path))
    if depth > _INCLUDE_DEPTH or key in seen:
        return
    seen.add(key)
    aliases, includes = _read_config(path)
    out.update(aliases)
    for inc in includes:
        target = Path(os.path.expanduser(inc))
        if not target.is_absolute():
            target = path.parent / target
        # An [includeIf] is followed whatever its condition: an extra alias
        # can only make this guard stricter (none can hide a git command).
        _collect(target, out, depth + 1, seen)


def _home() -> str:
    env = os.environ
    home = env.get("HOME") or env.get("USERPROFILE") or ""
    if not home and env.get("HOMEDRIVE") and env.get("HOMEPATH"):
        home = env["HOMEDRIVE"] + env["HOMEPATH"]
    return home


def _repo_config(folder: str) -> "Path | None":
    """The repository config git reads for ``folder`` (a checkout's
    ``.git/config``; a worktree's ``.git`` file names its git dir, whose
    ``commondir`` holds the shared config)."""
    if not folder:
        return None
    dot = Path(folder) / ".git"
    try:
        if dot.is_dir():
            return dot / "config"
        if dot.is_file():
            text = dot.read_text(encoding="utf-8", errors="replace").strip()
            if text.lower().startswith("gitdir:"):
                gitdir = Path(text.split(":", 1)[1].strip())
                if not gitdir.is_absolute():
                    gitdir = Path(folder) / gitdir
                common = gitdir / "commondir"
                if common.is_file():
                    rel = common.read_text(encoding="utf-8", errors="replace").strip()
                    base = Path(rel) if Path(rel).is_absolute() else gitdir / rel
                    return base / "config"
                return gitdir / "config"
    except OSError:
        return None
    return None


def user_aliases() -> dict[str, str]:
    """The git aliases a command of this turn's Build session would get
    (lower-cased names): the user's global config — ``GIT_CONFIG_GLOBAL``,
    else ``$XDG_CONFIG_HOME/git/config`` then ``~/.gitconfig`` — then the
    repository's (the session's worktree, else its repository), then
    ``GIT_CONFIG_COUNT`` pairs; later wins, as in git. Never raises (an
    unreadable config has no aliases); reads only small local files, each
    re-read only when it changed."""
    from .branches import current

    out: dict[str, str] = {}
    try:
        env = os.environ
        seen: set[str] = set()
        files: list[Path] = []
        if env.get("GIT_CONFIG_SYSTEM"):
            files.append(Path(env["GIT_CONFIG_SYSTEM"]))
        if env.get("GIT_CONFIG_GLOBAL"):
            files.append(Path(env["GIT_CONFIG_GLOBAL"]))
        else:
            home = _home()
            xdg = env.get("XDG_CONFIG_HOME") or (os.path.join(home, ".config") if home else "")
            if xdg:
                files.append(Path(xdg) / "git" / "config")
            if home:
                files.append(Path(home) / ".gitconfig")
        tb = current()
        if tb is not None:
            repo_cfg = _repo_config(tb.worktree) or _repo_config(tb.repo)
            if repo_cfg is not None:
                files.append(repo_cfg)
        else:
            # v1.511.0 (T2): the user's own folder's repository config, found
            # when the turn was armed (off the loop): one file, as on a branch.
            facts = TURN_FOLDER.get()
            if facts is not None and facts.config:
                files.append(Path(facts.config))
        for f in files:
            _collect(f, out, 0, seen)
        try:
            count = int(env.get("GIT_CONFIG_COUNT", "0") or 0)
        except ValueError:
            count = 0
        for i in range(min(count, 100)):
            k = env.get(f"GIT_CONFIG_KEY_{i}", "")
            if k.lower().startswith("alias."):
                out[k[6:].lower()] = env.get(f"GIT_CONFIG_VALUE_{i}", "")
    except Exception:  # noqa: BLE001 — not knowing an alias is the old behaviour, never a crash
        return out
    return out


def _short_flags(args: list[str]) -> str:
    """Every single-dash flag letter (``-fdx`` gives ``fdx``)."""
    return "".join(a[1:] for a in args if re.fullmatch(r"-[a-zA-Z]+", a))


def _is_force(args: list[str]) -> bool:
    for a in args:
        low = a.lower()
        if low in ("--force", "--mirror", "--force-if-includes") or low.startswith("--force-with-lease"):
            return True
        if re.fullmatch(r"-[a-zA-Z]+", a) and "f" in a[1:]:
            return True
        if a.startswith("+") and len(a) > 1:
            return True  # a +refspec forces that ref
    return False


def _push_deletes(args: list[str]) -> bool:
    """A push that deletes a remote branch: ``--delete``, ``-d``, a ``:ref``
    refspec, ``--prune`` (deletes the remote's branches this repository does
    not have)."""
    for a in args:
        low = a.lower()
        if low in ("--delete", "--prune"):
            return True
        if re.fullmatch(r"-[a-zA-Z]+", a) and "d" in a[1:]:
            return True
        if a.startswith(":") and len(a) > 1:
            return True
    return False


def _remote_of(args: list[str]) -> str:
    rest = [a for a in args if not a.startswith("-")]
    return rest[0] if rest else ""


#: A word that names another commit (``HEAD~1``, ``abc1234``, ``@{u}``,
#: ``origin/main``) rather than a file.
_REV_RX = re.compile(r"[~^]|@\{|^[0-9a-f]{7,40}$|^(?:refs|origin|upstream)/", re.I)


def _reset_act(args: list[str]) -> "str | None":
    before_paths = args[: args.index("--")] if "--" in args else args
    low = [a.lower() for a in before_paths]
    words = [a for a in before_paths if not a.startswith("-")]
    moves_ref = any(m in low for m in ("--hard", "--soft", "--keep", "--merge"))
    # A mode that takes no paths names a commit with ANY word; a plain
    # (mixed) reset with a word that reads as a commit — or (v1.511.0, T8)
    # its ONE word that is not a path of the working tree: `git reset main`
    # moves the branch to main's commit. A path after `--`, or one that
    # exists, only unstages (`git reset app.py`), and so does a reset with
    # paths (`git reset main app.py` reads main's copy of app.py into the
    # index; the branch stays).
    paths_after = args[args.index("--") + 1:] if "--" in args else []
    lone = len(words) == 1 and not paths_after
    target = [w for w in words if moves_ref or _REV_RX.search(w) or (lone and not _in_tree(w))]
    if any(w.upper() not in ("HEAD", "@") for w in target):
        return "rewrite"
    if "--hard" in low or "--merge" in low or "--keep" in low:
        return "discard"
    return None


_CONFIG_WRITES = frozenset({"--unset", "--unset-all", "--add", "--replace-all", "--rename-section",
                            "--remove-section", "--edit", "-e"})
_CONFIG_READS = frozenset({"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--list", "-l",
                           "--get-color", "--get-colorbool"})
_CONFIG_VALUED = frozenset({"-f", "--file", "--blob", "--type", "--default", "--comment", "--value"})


def _config_writes(args: list[str]) -> bool:
    """``git config`` that WRITES a setting (``<name> <value>``, ``set``,
    ``--unset``, …) rather than reading one. A worktree's repository config
    is the one the user's own checkout reads (their name, their remotes)."""
    low = [a.lower() for a in args]
    if any(a in _CONFIG_WRITES for a in low):
        return True
    if any(a in _CONFIG_READS for a in low):
        return False
    words: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a.lower() in _CONFIG_VALUED:
            skip = True
            continue
        if not a.startswith("-"):
            words.append(a)
    if words and words[0].lower() in ("set", "unset", "rename-section", "remove-section", "edit"):
        return True
    if words and words[0].lower() in ("get", "list"):
        return False
    return len(words) >= 2


def _git_act(sub: str, args: list[str]) -> "GitAct | None":
    low = [a.lower() for a in args]
    short = _short_flags(args)
    if sub == "push":
        if _is_force(args):
            return GitAct("force", _remote_of(args))
        if _push_deletes(args):
            return GitAct("delete_remote", _remote_of(args))
        return GitAct("push", _remote_of(args))
    if sub == "commit":
        return GitAct("rewrite" if "--amend" in low else "commit")
    if sub in ("rebase", "filter-branch", "filter-repo", "update-ref", "replace"):
        return GitAct("rewrite")
    if sub == "pull" and (
        "r" in short or any(a == "--rebase" or (a.startswith("--rebase=") and a != "--rebase=false")
                            for a in low)
    ):
        return GitAct("rewrite")
    if sub == "reset":
        kind = _reset_act(args)
        return GitAct(kind) if kind else None
    if sub == "branch" and (
        any(a in ("--delete", "--force", "--move") for a in low) or set(short) & set("dDfmM")
    ):
        return GitAct("delete_branch")
    if sub == "tag" and ("--delete" in low or "d" in short):
        return GitAct("delete_branch")
    if sub == "worktree" and args and low[0] in ("remove", "prune", "move"):
        return GitAct("worktree")
    if sub == "checkout":
        if "B" in short:
            return GitAct("delete_branch")
        if "--" in args or "." in args or "--force" in low or "f" in short:
            return GitAct("discard")
        # v1.511.0 (T8): `git checkout <file>` (no `--`) overwrites that
        # file's uncommitted changes; `git checkout <branch>` is a switch.
        if any(_in_tree(w) for w in _checkout_words(args)):
            return GitAct("discard")
        return None
    if sub == "switch":
        if "C" in short or "--force-create" in low:
            return GitAct("delete_branch")
        if "--discard-changes" in low or "--force" in low or "f" in short:
            return GitAct("discard")
        return None
    if sub == "clean" and ("--force" in low or "f" in short):
        return GitAct("discard")
    if sub == "restore":
        staged_only = ("--staged" in low or "S" in short) and not ("--worktree" in low or "W" in short)
        return None if staged_only else GitAct("discard")
    if sub == "stash" and args and low[0] in ("drop", "clear"):
        return GitAct("discard")
    if sub == "stash" and args and low[0] in ("pop", "apply", "branch"):
        # v1.511.0 (T8): the stash is shared by every worktree of the
        # repository — an entry popped here may be the user's own work.
        return GitAct("stash")
    if sub == "config" and _config_writes(args):
        return GitAct("config")
    if sub == "remote" and args and low[0] in _REMOTE_WRITES:
        # v1.511.0 (T8): writes the repository config the user's own
        # checkout reads, like `git config remote.origin.url <other>`.
        return GitAct("config")
    return None


#: ``git remote`` verbs that change the repository's config.
_REMOTE_WRITES = frozenset({"add", "remove", "rm", "rename", "set-url", "set-head", "set-branches", "prune"})
#: ``git checkout`` options whose next word is a NEW branch's name.
_CHECKOUT_NAME_OPTS = frozenset({"-b", "--orphan"})


def _checkout_words(args: list[str]) -> list[str]:
    """The plain words of a ``git checkout`` (a branch, a commit or paths), a
    new branch's name left out."""
    out: list[str] = []
    skip = False
    for a in args:
        if skip:
            skip = False
            continue
        if a in _CHECKOUT_NAME_OPTS:
            skip = True
            continue
        if a.startswith("-"):
            continue
        out.append(a)
    return out


#: gh commands that only READ (everything else asks first on a branch).
_GH_READS: "dict[str, set[str] | None]" = {
    "pr": {"view", "list", "status", "checks", "diff"},
    "issue": {"view", "list", "status"},
    "run": {"view", "list", "watch"},
    "workflow": {"view", "list"},
    "repo": {"view", "list"},
    "release": {"view", "list"},
    "auth": {"status"},
    "search": None,  # every search reads
}
#: ``gh api`` sends a request that can change something with these.
_GH_API_WRITE_RX = re.compile(r"^(?:-X.*|-[fF]|--method(?:=.*)?|--(?:raw-)?field(?:=.*)?|--input(?:=.*)?)$")


def _gh_act(toks: list[str]) -> "GitAct | None":
    words = [t.lower() for t in toks]
    if not words or words[0].startswith("-") or words[0] in ("help", "version", "completion"):
        return None
    group, verb = words[0], (words[1] if len(words) > 1 else "")
    if (group, verb) == ("repo", "delete"):
        return GitAct("delete_repo")
    if (group, verb) in (("pr", "create"), ("pr", "merge")):
        return GitAct("pr")
    if group == "api":
        return GitAct("gh") if any(_GH_API_WRITE_RX.match(t) for t in toks[1:]) else None
    reads = _GH_READS.get(group, set())
    if reads is None or verb in reads:
        return None
    return GitAct("gh")


def classify(command: Any, _depth: int = 0) -> list[GitAct]:
    """The git actions in ``command`` that this guard is about, in order (a
    command line may hold several). [] for anything else."""
    text = str(command or "")
    if not text or ("git" not in text.lower() and "gh" not in text.lower()):
        return []
    # v1.386.0 review: a LINE CONTINUATION joins two lines into one command —
    # bash's backslash-newline (the IJ harness's Claude dialect runs in Git
    # for Windows' bash), cmd's caret-newline. Read as written, `git push \`
    # + a break + `--force` was a plain push (a card) and `git \` + a break +
    # `push -f` was nothing at all, while bash ran a force push. Joined
    # always: where only the first line runs (cmd.exe), reading more only
    # makes the guard stricter.
    text = _CONTINUATION_RX.sub(" ", text)
    out: list[GitAct] = []
    for m in _PROG_RX.finditer(text):
        # A quoted path ("C:\Program Files\Git\cmd\git.exe" push) closes its
        # quote right after the program's name.
        rest = text[m.end():].lstrip("\"'")
        # v1.511.0 (T8): cut with quotes respected, every shell's reading kept.
        found: list[GitAct] = []
        for segment in _segments(rest):
            toks = _tokens(segment)
            if m.group("prog").lower() == "gh":
                acts = [a for a in (_gh_act(toks),) if a is not None]
            else:
                sub, args = _subcommand(toks)
                if sub == _SHELL_ALIAS:
                    # A shell alias (`!git push --force`, `!f() { git commit …; }; f`):
                    # its text is a command line of its own.
                    acts = (classify(args[0] if args else "", _depth + 1)
                            if _depth < _ALIAS_DEPTH else [])
                else:
                    acts = [a for a in (_git_act(sub, args),) if a is not None]
            found.extend(a for a in acts if a not in found)
        out.extend(found)
    return out


def _scan_end(rest: str, esc: str) -> "int | None":
    """Where the command at the start of ``rest`` ends (the index of its
    separator, ``len(rest)`` for none), reading quotes as a shell whose escape
    character is ``esc`` (bash ``\\``, PowerShell ``` ` ```): outside quotes
    and inside double quotes it makes the next character plain; single quotes
    have none (a doubled ``''`` / ``""`` closes and reopens: still inside).
    None when a quote never closes."""
    quote = ""
    i, n = 0, len(rest)
    while i < n:
        ch = rest[i]
        if ch == esc and quote != "'" and i + 1 < n:
            i += 2
            continue
        if quote:
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
        elif _END_RX.match(rest, i):
            return i
        i += 1
    return None if quote else n


def _segments(rest: str) -> list[str]:
    """The command at the start of ``rest`` as each shell reads it (v1.511.0,
    T8): bash's reading and PowerShell's (a separator inside a quoted message
    never ends it), and — when a quote never closes — both the line cut at its
    first separator and the whole line (FAIL CLOSED: every reading's actions
    count, so the stricter one always wins)."""
    out: list[str] = []
    unclosed = False
    for esc in ("\\", "`"):
        end = _scan_end(rest, esc)
        if end is None:
            unclosed = True
            continue
        if rest[:end] not in out:
            out.append(rest[:end])
    if unclosed:
        naive = _END_RX.search(rest)
        for seg in (rest[: naive.start()] if naive else rest, rest):
            if seg not in out:
                out.append(seg)
    return out


FORCE_REFUSAL = (
    "Refused: a force push rewrites the remote's history, and a coding session that works on its "
    "own branch never does that. Push without --force, or let the user press Push (it never forces)."
)
_REFUSALS = {
    "force": FORCE_REFUSAL,
    "delete_remote": (
        "Refused: this push deletes a branch on the remote, and a coding session that works on its "
        "own branch never deletes a branch. The user can do that themself."
    ),
    "rewrite": (
        "Refused: this rewrites the branch's history (an amend, a rebase, a reset to another commit, "
        "or a ref moved by hand), and a coding session that works on its own branch never does that. "
        "Make a new commit instead; the user can rewrite history themself."
    ),
    "delete_branch": (
        "Refused: this deletes, renames or force-moves a branch or a tag, and a coding session never "
        "does that. The user can do that themself."
    ),
    "worktree": (
        "Refused: this changes the repository's worktrees (a prune forgets every worktree whose "
        "folder is missing right now, the user's own included). The session's own worktree is "
        "removed with Discard."
    ),
    "delete_repo": "Refused: this deletes a repository on GitHub. A coding session never does that.",
}


def _branch_words() -> str:
    from .branches import current

    tb = current()
    return tb.branch if tb is not None else "this session's branch"


def card_words(acts: list[GitAct]) -> str:
    """The card's one quiet line (``cost_note``): what the command does."""
    kinds = {a.kind for a in acts}
    branch = _branch_words()
    if "pr" in kinds:
        return "This opens or merges a pull request on GitHub. Every pull request asks you first."
    if "gh" in kinds:
        return "This changes something on GitHub. Every GitHub change asks you first."
    if "push" in kinds:
        remote = next((a.remote for a in acts if a.kind == "push" and a.remote), "")
        to = f" to {remote}" if remote else " to a remote"
        return f"This pushes{to}. Every push from a session's branch ({branch}) asks you first."
    if "discard" in kinds:
        return (f"This throws away changes on {branch} that are not committed yet. It asks you "
                "every time.")
    if "stash" in kinds:
        return (f"This takes work from git's stash into {branch}. The stash is shared with your own "
                "checkout, so the entry may be your work, not this session's. It asks you every time.")
    if "config" in kinds:
        return ("This changes a git setting (the repository's, which your own checkout reads too, "
                "or yours). It asks you every time.")
    return f"This makes a commit on {branch}. Every commit asks you first."


#: The user's own folder (v1.511.0, T2): what each kind does there, the most
#: serious first. A commit is not here: it does not ask.
_FOLDER_WORDS: "tuple[tuple[str, str], ...]" = (
    ("delete_repo", "This deletes a repository on GitHub. It asks you every time."),
    ("force", "This force-pushes from your own folder{to}, replacing what is there. It asks you "
              "every time."),
    ("delete_remote", "This deletes a branch on your remote{on}. It asks you every time."),
    ("rewrite", "This rewrites history in your own folder (an amend, a rebase, or a reset to another "
                "commit). It asks you every time."),
    ("delete_branch", "This deletes, renames or force-moves a branch or a tag in your own folder. It "
                      "asks you every time."),
    ("worktree", "This changes which extra copies of your repository git keeps (a prune forgets "
                 "every one whose folder is missing right now). It asks you every time."),
    ("discard", "This throws away changes in your own folder that are not committed yet. It asks "
                "you every time."),
    ("pr", "This opens or merges a pull request on GitHub. Every pull request asks you first."),
    ("gh", "This changes something on GitHub. Every GitHub change asks you first."),
    ("push", "This pushes from your own folder{to}. Every push asks you first."),
    ("stash", "This takes work from git's stash into your own folder. The stash is shared by every "
              "copy of the repository, so the entry may hold other work. It asks you every time."),
    ("config", "This changes a git setting of the repository in your own folder (or one of yours). "
               "It asks you every time."),
)


def folder_card_words(acts: list[GitAct]) -> str:
    """The card's one quiet line in the user's own folder (v1.511.0, T2)."""
    kinds = {a.kind for a in acts}
    for kind, words in _FOLDER_WORDS:
        if kind in kinds:
            remote = next((a.remote for a in acts if a.kind == kind and a.remote), "")
            return words.format(to=f" to {remote}" if remote else " to a remote",
                                on=f" ({remote})" if remote else "")
    return "This changes something with git in your own folder. It asks you every time."


def _users_folder() -> bool:
    """Is this a coding session's turn that works in its folder directly, the
    folder in a git repository (:data:`TURN_FOLDER`; no branch of its own)?"""
    from .branches import current

    if current() is not None:
        return False
    facts = TURN_FOLDER.get()
    return facts is not None and bool(facts.repo)


def refusal(command: Any) -> str:
    """The refusal for a command the plan rules out while the turn works on a
    branch (:data:`REFUSED_KINDS`), else "" (no branch, or nothing refused).
    v1.511.0 (T2): never in the user's own folder — there those commands are
    the user's to allow, and :func:`guard_check` asks every time instead."""
    from .branches import current

    if current() is None:
        return ""
    for act in classify(command):
        if act.kind in REFUSED_KINDS:
            return _REFUSALS[act.kind]
    return ""


#: The name the phase shipped with (a force push was its only refusal).
force_refusal = refusal


# --------------------------------------------------------------------------- #
# off the event loop (v1.516.0, the file checks left open by group 11)
# --------------------------------------------------------------------------- #

#: How long the guard may take to read what it looks at on disk (whether a
#: ``reset`` / ``checkout`` word is a file, the user's git config for
#: aliases) — a folder on a network drive that stopped answering.
CHECK_TIMEOUT_S = 5.0
#: The card's line when the guard could not finish in time: it asks.
SLOW_CHECK_WORDS = (
    "Iron Jarvis could not finish checking this git command in time (a folder did not answer), so it "
    "asks you this time."
)
#: The refusal when the guard could not finish in time before running it.
SLOW_CHECK_REFUSAL = (
    "Refused: Iron Jarvis could not finish checking this git command in time (a folder did not "
    "answer), so it did not run it. Try again."
)


def _may_read_files(command: Any) -> bool:
    """Can judging ``command`` read anything on disk? Only when it names a
    git or gh program (:func:`classify` reads nothing otherwise)."""
    text = str(command or "")
    if not text or ("git" not in text.lower() and "gh" not in text.lower()):
        return False
    return _PROG_RX.search(_CONTINUATION_RX.sub(" ", text)) is not None


async def _off_loop(fn: Any, command: Any) -> "tuple[bool, Any]":
    """``(finished, fn(command))``, judged in a worker thread (the turn's
    branch and folder go with it: ``to_thread`` copies the context) and
    bounded by :data:`CHECK_TIMEOUT_S`; a command that cannot read anything
    is judged right here, as before."""
    import asyncio

    if not _may_read_files(command):
        return True, fn(command)
    try:
        return True, await asyncio.wait_for(asyncio.to_thread(fn, command), timeout=CHECK_TIMEOUT_S)
    except asyncio.TimeoutError:
        return False, None


async def spend_check_off_loop(check: Any, command: Any) -> Any:
    """A command tool's spend check (``command_shell.tool_check``, which asks
    :func:`guard_check` first) without the guard's file checks on the event
    loop, bounded. Past the bound it FAILS CLOSED: it asks once, every time,
    and says why. Same answers otherwise."""
    finished, verdict = await _off_loop(check, command)
    if finished:
        return verdict
    from ..tools.base import SpendCheck

    return SpendCheck(ask=True, words=SLOW_CHECK_WORDS, cost_note="asks every time", once_only=True,
                      reason="git")


async def refusal_off_loop(command: Any) -> str:
    """:func:`refusal` without its file checks on the event loop, bounded.
    Past the bound it refuses, in words."""
    finished, refused = await _off_loop(refusal, command)
    return refused if finished else SLOW_CHECK_REFUSAL


def guard_check(command: Any) -> Any:
    """``SpendCheck`` for a command tool's call while the turn works on a
    branch: ask once, every time, for a commit / push / pull request /
    GitHub change / discarding work / a git setting; ``ask=False`` for a command the tool
    REFUSES (so no lane shows a card for a call that will not run — the
    tool's ``execute`` refuses it before anything runs); None for anything
    else.

    v1.511.0 (second review T2, decision E3): in a coding session that works
    in its folder directly, the folder in a git repository, every action read
    here but a commit asks once, every time — refused kinds included (the
    user's own folder is theirs: nothing is refused there). None for a folder
    that is not in a repository and for any caller that is not a coding
    session (nothing armed), exactly as before. Whether the folder is in a
    repository is never looked up here: it was found when the turn was armed
    (off the event loop)."""
    from ..tools.base import SpendCheck
    from .branches import current

    if current() is None:
        if not _users_folder():
            return None
        acts = [a for a in classify(command) if a.kind != "commit"]
        if not acts:
            return None
        return SpendCheck(ask=True, words=folder_card_words(acts), cost_note="asks every time",
                          once_only=True, reason="git")
    acts = classify(command)
    if not acts:
        return None
    if any(a.kind in REFUSED_KINDS for a in acts):
        return SpendCheck(ask=False, words=refusal(command), cost_note="refused before it ran",
                          reason="git")
    return SpendCheck(ask=True, words=card_words(acts), cost_note="asks every time",
                      once_only=True, reason="git")


__all__ = [
    "CHECK_TIMEOUT_S", "FORCE_REFUSAL", "GitAct", "REFUSED_KINDS", "SLOW_CHECK_REFUSAL", "SLOW_CHECK_WORDS",
    "TURN_FOLDER", "TurnFolder", "card_words", "classify", "folder_card_words", "folder_facts",
    "force_refusal", "guard_check", "refusal", "refusal_off_loop", "reset_turn_folder", "set_turn_folder",
    "spend_check_off_loop", "user_aliases",
]
