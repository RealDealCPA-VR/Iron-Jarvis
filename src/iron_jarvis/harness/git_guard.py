"""What the AGENT may do with git while a Build session works on its own
branch (v1.375.0, IJ harness part 2 Phase 4).

The buttons (Commit, Push, Create PR, Merge, Discard) are a person's actions.
The agent can still run git through its command tools (``shell``,
``process_start``, ``process_write`` — Claude Code's Bash and Codex's
exec_command map onto them), so while a turn works on a branch
(``branches.current()``):

* a ``git commit``, a ``git push``, a ``gh pr create`` / ``gh pr merge`` and
  any other ``gh`` command that changes something on GitHub ASK FIRST, in
  EVERY posture (auto-approve included) and past every grant — and so does
  git that THROWS AWAY work not committed yet (``reset --hard``, ``clean
  -f``, ``checkout -- <paths>``, ``restore``, ``stash drop|clear``, ``switch
  --discard-changes``) and ``git config`` that WRITES a setting (a
  worktree's repository config is the one the user's checkout reads) — each
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
  ``update-ref``, ``replace``), deleting or force-moving a BRANCH or a tag
  (``branch -d|-D|-f|-m``, ``checkout -B``, ``switch -C``, ``tag -d``),
  changing the user's WORKTREES (``worktree remove|prune|move`` — a prune
  forgets every worktree whose folder is missing right then) and ``gh repo
  delete``.

Everything else stays under the harness guardrail line "only when the user
asks for them" (``components.DEFAULT_GUARDRAILS``). A session that works in
its folder directly is untouched — exactly as before.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Where a git (or gh) program name starts in a command line.
_PROG_RX = re.compile(
    r"""(?:^|[\s;&|(`"'])(?:[^\s;&|(`"']*[\\/])?(?P<prog>git|gh)(?:\.exe)?(?=[\s"'])""",
    re.IGNORECASE,
)
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
    committed) | ``config`` (``git config`` that writes a setting). REFUSED: ``force`` (a force push) | ``delete_remote`` (a
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
    # (mixed) reset only with a word that reads as a commit.
    target = [w for w in words if moves_ref or _REV_RX.search(w)]
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
            return GitAct("force")
        if _push_deletes(args):
            return GitAct("delete_remote")
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
    if sub == "config" and _config_writes(args):
        return GitAct("config")
    return None


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
    out: list[GitAct] = []
    for m in _PROG_RX.finditer(text):
        # A quoted path ("C:\Program Files\Git\cmd\git.exe" push) closes its
        # quote right after the program's name.
        rest = text[m.end():].lstrip("\"'")
        end = _END_RX.search(rest)
        segment = rest[: end.start()] if end else rest
        toks = _tokens(segment)
        if m.group("prog").lower() == "gh":
            act = _gh_act(toks)
        else:
            sub, args = _subcommand(toks)
            if sub == _SHELL_ALIAS:
                # A shell alias (`!git push --force`, `!f() { git commit …; }; f`):
                # its text is a command line of its own.
                if _depth < _ALIAS_DEPTH:
                    out.extend(classify(args[0] if args else "", _depth + 1))
                continue
            act = _git_act(sub, args)
        if act is not None:
            out.append(act)
    return out


FORCE_REFUSAL = (
    "Refused: a force push rewrites the remote's history, and a Build session that works on its "
    "own branch never does that. Push without --force, or let the user press Push (it never forces)."
)
_REFUSALS = {
    "force": FORCE_REFUSAL,
    "delete_remote": (
        "Refused: this push deletes a branch on the remote, and a Build session that works on its "
        "own branch never deletes a branch. The user can do that themself."
    ),
    "rewrite": (
        "Refused: this rewrites the branch's history (an amend, a rebase, a reset to another commit, "
        "or a ref moved by hand), and a Build session that works on its own branch never does that. "
        "Make a new commit instead; the user can rewrite history themself."
    ),
    "delete_branch": (
        "Refused: this deletes, renames or force-moves a branch or a tag, and a Build session never "
        "does that. The user can do that themself."
    ),
    "worktree": (
        "Refused: this changes the repository's worktrees (a prune forgets every worktree whose "
        "folder is missing right now, the user's own included). The session's own worktree is "
        "removed with Discard."
    ),
    "delete_repo": "Refused: this deletes a repository on GitHub. A Build session never does that.",
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
    if "config" in kinds:
        return ("This changes a git setting (the repository's, which your own checkout reads too, "
                "or yours). It asks you every time.")
    return f"This makes a commit on {branch}. Every commit asks you first."


def refusal(command: Any) -> str:
    """The refusal for a command the plan rules out while the turn works on a
    branch (:data:`REFUSED_KINDS`), else "" (no branch, or nothing refused)."""
    from .branches import current

    if current() is None:
        return ""
    for act in classify(command):
        if act.kind in REFUSED_KINDS:
            return _REFUSALS[act.kind]
    return ""


#: The name the phase shipped with (a force push was its only refusal).
force_refusal = refusal


def guard_check(command: Any) -> Any:
    """``SpendCheck`` for a command tool's call while the turn works on a
    branch: ask once, every time, for a commit / push / pull request /
    GitHub change / discarding work / a git setting; ``ask=False`` for a command the tool
    REFUSES (so no lane shows a card for a call that will not run — the
    tool's ``execute`` refuses it before anything runs); None for anything
    else and whenever the turn has no branch."""
    from ..tools.base import SpendCheck
    from .branches import current

    if current() is None:
        return None
    acts = classify(command)
    if not acts:
        return None
    if any(a.kind in REFUSED_KINDS for a in acts):
        return SpendCheck(ask=False, words=refusal(command), cost_note="refused before it ran",
                          reason="git")
    return SpendCheck(ask=True, words=card_words(acts), cost_note="asks every time",
                      once_only=True, reason="git")


__all__ = [
    "FORCE_REFUSAL", "GitAct", "REFUSED_KINDS", "card_words", "classify", "force_refusal",
    "guard_check", "refusal", "user_aliases",
]
