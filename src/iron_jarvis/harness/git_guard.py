"""What the AGENT may do with git while a Build session works on its own
branch (v1.375.0, IJ harness part 2 Phase 4).

The buttons (Commit, Push, Create PR, Merge, Discard) are a person's actions.
The agent can still run git through its command tools (``shell``,
``process_start``, ``process_write`` — Claude Code's Bash and Codex's
exec_command map onto them), so while a turn works on a branch
(``branches.current()``):

* a ``git commit``, a ``git push`` and a ``gh pr create`` / ``gh pr merge``
  ASK FIRST, in EVERY posture (auto-approve included) and past every grant —
  each tool's ``spend_check`` answers ``SpendCheck(ask=True, once_only=True)``:
  the stream lane's card offers "Allow once" and Deny only, and the POST lane,
  which cannot show a card, refuses (``chat_turn.spend_not_asked_invoke_kwargs``,
  lock-step);
* a FORCE push (``-f``, ``--force``, ``--force-with-lease``,
  ``--force-if-includes``, ``--mirror``, a ``+refspec``) is REFUSED outright by
  the tool itself, whatever the posture or the answer to a card.

Everything else (history rewrites such as ``reset --hard``, ``rebase``,
``branch -D``) stays under the harness guardrail line "only when the user asks
for them" (``components.DEFAULT_GUARDRAILS``). A session that works in its
folder directly is untouched — exactly as before.

HONEST LIMITS: this reads the command's text. It catches git as a model
writes it (``git push``, ``git -C x commit``, ``cmd /c "git push"``,
``git.exe push``); an alias, a script file or code that builds the command at
run time is not caught. It is a guardrail against a careless model, not a
sandbox.
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
    """One git action found in a command: ``commit`` | ``push`` | ``force``
    (a force push) | ``pr`` (gh pr create / merge)."""

    kind: str
    remote: str = ""


def _tokens(segment: str) -> list[str]:
    return [t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t
            for t in _TOKEN_RX.findall(segment)]


def _subcommand(tokens: list[str]) -> "tuple[str, list[str]]":
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in _GLOBAL_WITH_VALUE:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        return tok.lower(), tokens[i + 1:]
    return "", []


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


def _remote_of(args: list[str]) -> str:
    rest = [a for a in args if not a.startswith("-")]
    return rest[0] if rest else ""


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
        prog = m.group("prog").lower()
        if prog == "gh":
            if len(toks) >= 2 and toks[0].lower() == "pr" and toks[1].lower() in ("create", "merge"):
                out.append(GitAct("pr"))
            continue
        sub, args = _subcommand(toks)
        if sub == "push":
            out.append(GitAct("force" if _is_force(args) else "push", _remote_of(args)))
        elif sub == "commit":
            out.append(GitAct("commit"))
    return out


FORCE_REFUSAL = (
    "Refused: a force push rewrites the remote's history, and a Build session that works on its "
    "own branch never does that. Push without --force, or let the user press Push (it never forces)."
)


def _branch_words() -> str:
    from .branches import current

    tb = current()
    return tb.branch if tb is not None else "this session's branch"


def card_words(acts: list[GitAct]) -> str:
    """The card's one quiet line (``cost_note``): what the command publishes."""
    kinds = {a.kind for a in acts}
    branch = _branch_words()
    if "pr" in kinds:
        return "This opens or merges a pull request on GitHub. Every pull request asks you first."
    if "push" in kinds:
        remote = next((a.remote for a in acts if a.kind == "push" and a.remote), "")
        to = f" to {remote}" if remote else " to a remote"
        return f"This pushes{to}. Every push from a session's branch ({branch}) asks you first."
    return f"This makes a commit on {branch}. Every commit asks you first."


def guard_check(command: Any) -> Any:
    """``SpendCheck`` for a command tool's call while the turn works on a
    branch: ask once, every time, for a commit / push / pull request; None for
    anything else (and for a force push — the tool refuses it before it runs,
    so there is nothing to approve) and whenever the turn has no branch."""
    from ..tools.base import SpendCheck
    from .branches import current

    if current() is None:
        return None
    acts = classify(command)
    if not acts or any(a.kind == "force" for a in acts):
        return None
    return SpendCheck(ask=True, words=card_words(acts), cost_note="asks every time",
                      once_only=True, reason="git")


def force_refusal(command: Any) -> str:
    """The refusal for a force push while the turn works on a branch, else
    "" (no branch, or no force push in the command)."""
    from .branches import current

    if current() is None:
        return ""
    return FORCE_REFUSAL if any(a.kind == "force" for a in classify(command)) else ""


__all__ = ["FORCE_REFUSAL", "GitAct", "card_words", "classify", "force_refusal", "guard_check"]
