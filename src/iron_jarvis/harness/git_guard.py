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
alias.x=…``); an alias in the user's own git config, a script file or code
that builds the command at run time is not caught. A command that only
MENTIONS git (``echo git push``) is read as if it ran it. It is a guardrail
against a careless model, not a sandbox.
"""


from __future__ import annotations

import re
from dataclasses import dataclass
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


def _subcommand(tokens: list[str]) -> "tuple[str, list[str]]":
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
        if sub in aliases:
            expanded = _tokens(aliases[sub].lstrip("!"))
            if expanded:
                return expanded[0].lower(), expanded[1:] + rest
        return sub, rest
    return "", []


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


def classify(command: Any) -> list[GitAct]:
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
            act = _git_act(*_subcommand(toks))
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
    "guard_check", "refusal",
]
