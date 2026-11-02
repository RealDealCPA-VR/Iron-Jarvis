"""READ-ONLY GIT for the team's reviewer (v1.421.0, Wave 1, A4).

The finish check's team reviewer and the CI-fix reviewer held only the
reading tools (``harness/helpers.REVIEW_TOOLS``: read_file, glob, grep,
list_files) and the owner's live review said so: "This session only has read
and search" — it could not see the change the way a person checks one
(``git diff``, ``git log``, ``git blame``). The owner's decision (2026-11-01):
the reviewer gets READ-ONLY commands, but not test runs (they write caches;
running the tests stays the finish check's job). read_file / glob / grep /
list_files already cover cat / ls / find / grep, so this module adds ONE
tool, ``git_read``, and it is git only.

WHAT IT RUNS: an argv, never a shell — ``git`` with one of :data:`ALLOWED`
(status, diff, log, show, blame, ls-files, rev-parse, describe, shortlog,
grep; branch and tag in LIST form only) and the model's own words after it,
each checked:

* v1.422.2, the owner's "safer option": the OPTIONS are an ALLOWLIST per
  command (:data:`OPTIONS`, built from ``_ROWS``) — the v1.421.0 review found
  nine holes in a blocklist over git's huge option and config surface, so a
  word that starts with "-" must now be one of its command's own options a
  reviewer needs, else it is refused in ONE plain sentence naming it and the
  common options that command does take (:data:`_COMMON`). Each option was
  checked against git 2.50's own docs for its effect: none can run a
  program, write a file, read a file the model names, reach a remote or
  change state (``--untracked`` / ``--contents`` / ``-f`` / ``-X`` read files
  of their own and are refused too: read_file and grep cover those). The
  rules, each pinned in ``tests/test_git_read_allowlist_v14222.py``:
  options are SPELLED IN FULL (git takes an unambiguous shortening —
  ``--bra`` is status's ``--branch``, ``--open-files`` was
  ``--open-files-in-pager`` — so a shortening is refused, naming the full
  spelling when one matches); a CLUSTER (``-sb``, ``-nA3``) is read LETTER
  BY LETTER as git reads it and every letter must be allowed, a letter that
  takes a value taking the rest of the word; a value is checked by its kind
  (a number, a choice, a line range, a format with no signature, a commit
  with no path out of the folder; a pattern or date is text git reads no
  file by); ``--opt=v`` and ``--opt v`` both work where git takes both, a
  glued-only option (``--stat=80``, ``-U5``) leaves a separate word to be
  read as a revision or path the way git reads it, and a separate value
  that starts with "-" is refused (the glued form says it plainly); ``--``
  ends the options and every word after it is a path;
* REFUSED, in plain words, besides any option not on the list: any other git
  command, a signature shown (a ``%G`` / ``%(signature`` format: gpg is a
  program the folder's config can name), a path that leaves the working
  folder (absolute, a drive, a network share, ``..``), and for ``branch`` /
  ``tag`` anything that is not a listing (a name with no list option CREATES
  one). The options KNOWN to be dangerous (``--output``, ``--ext-diff``,
  ``--textconv``, ``-O``, ``--no-index``, ``--show-signature``,
  ``--remerge-diff``, ``--submodule``, status ``-v``, describe ``--dirty``…)
  keep their own sentence saying why (:data:`_DANGER_ALL`, ``_DANGER_FOR``),
  their shortenings too;
* A WORKER MAY HAVE WRITTEN ``.git/config`` — so nothing it says may run a
  program: ``--no-pager``, ``-c core.fsmonitor=false``, ``-c
  core.hooksPath=<the null device>``, ``-c log.showSignature=false``, ``-c
  protocol.allow=never`` (a partial clone's lazy fetch, an ``ext::``
  transport) plus ``GIT_NO_LAZY_FETCH=1``, ``--no-ext-diff --no-textconv``
  where the command takes them, ``--ignore-submodules=all`` for status and
  diff, and every FILTER DRIVER the repository's own config defines (local or
  worktree scope — a ``.gitattributes`` + ``filter.<x>.clean`` runs a program
  on ``git diff`` and ``git status``; measured) emptied with ``-c
  filter.<x>.clean= … required=false``. The user's own global filters (Git
  LFS) are left as they are. v1.422.2 review: nor may it make git READ a file
  and show it — a ``mailmap.file`` or ``core.attributesFile`` the
  repository's own config sets is pointed at the null device
  (:data:`_FILE_SETTINGS`: a mailmap line ``<text> <a@b.c>`` of ANY file came
  back as an author's name, an attributes file's bad names on stderr;
  measured);
* the environment is a Build session's git environment
  (``git.integration.git_env``: no terminal prompt, never another repository
  through ``GIT_DIR`` & co.) with ``GIT_OPTIONAL_LOCKS=0`` (a status never
  takes the index lock) and no ``GIT_EXTERNAL_DIFF``;
* through ``git.integration.run_git`` → ``core.procs`` (its own Job, bounded
  by :data:`GIT_READ_TIMEOUT_S`, the tree killed on a timeout), off the loop;
* the output MASKED (``detections.redact.mask``) and cut to
  :data:`OUTPUT_CHARS`, saying so.

v1.421.0 REVIEW (each measured against real git 2.50 with a trapped
repository, each pinned in ``tests/test_git_read_review_v14210.py``; since
v1.422.2 the option ones are refused EARLIER, as options not on the list —
the trapped harness still runs every pin): the first cut still ran a program
the folder named, or read past it, through ONE allowed call —

* ``status -v`` showed the staged diff through the folder's TEXTCONV (status
  takes no ``--no-textconv``): ``--verbose`` and its shortenings, and a ``v``
  in a cluster, are refused for status (``_DANGER_FOR``);
* a filter whose NAME holds ``=`` could not be emptied (``git -c`` splits a
  key at its first ``=``): such a folder is refused
  (:data:`FILTER_NAME_REFUSAL`); the settings are read NUL-separated;
* the repo's own ``format.pretty`` / ``pretty.<alias>`` holding ``%G?`` ran
  ``gpg.program`` (and the ssh / x509 programs) on a plain ``git log`` of a
  commit with a fake signature: every signature program is set EMPTY (git
  says "cannot spawn" and runs nothing);
* ``grep -nO<program>``: a ``-O`` INSIDE a cluster ran the pager — any
  cluster holding ``O`` is refused, and grep ``-f`` / blame ``-S`` /
  ls-files ``-X`` (a file glued to a cluster read past the path check) are
  refused outright since v1.422.2;
* ``diff.submodule = diff`` (the repo's own) or the model's
  ``--submodule=diff`` ran ``git diff`` INSIDE a submodule, whose own
  ``diff.external`` ran; the model's ``--ignore-submodules=none`` beat the
  guard: ``diff.submodule=short`` and ``submodule.recurse=false`` are forced,
  ``--submodule`` / ``--ignore-submodules`` refused;
* ``core.worktree`` sent git to files OUTSIDE the folder: the working folder
  git would use must CONTAIN this one (:func:`_place_problem`), and a
  network spelling is refused by its text before git is asked;
* ``blame --contents`` printed a key file and ``grep --untracked`` searched
  the secrets store in a folder holding Iron Jarvis's own state (the user's
  home holds it): the folder git reads (the repository's top, else this one)
  must hold none of ``core.fs_policy``'s protected roots
  (:data:`PROTECTED_REFUSAL`) — the rule ``read_file`` and ``grep`` follow;
* ``blame.ignoreRevsFile`` naming a file outside the folder: blame echoed
  its first line in an error — blame is refused unless every such file is a
  plain path inside (:data:`SKIP_FILE_REFUSAL`);
* a REMERGE (``--remerge-diff``, ``--diff-merges=remerge``, or a plain
  ``log -m -p`` under the repo's own ``log.diffMerges = remerge``) re-ran a
  merge in memory and ran the merge driver the folder names: refused, and
  ``log.diffMerges=separate`` (git's default) forced;
* ``describe --dirty`` / ``--broken`` REWROTE ``.git/index``: refused.

Allow tier, READONLY (so replay-safe), on no roster: the review-only helper
arms it ALWAYS (``harness/helpers.helper_definition``), and nothing else
does. Its output is the folder's text another model wrote, so it is fenced
and scanned like a read file's (``returns_untrusted_content``)."""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from typing import Any

from ..tools.base import Reversibility, RiskClass, Tool, ToolContext, ToolResult

#: The tool's name (``harness/helpers.REVIEW_TOOLS`` holds it).
NAME = "git_read"
#: The git commands it runs (branch / tag in list form only).
ALLOWED: tuple[str, ...] = (
    "status", "diff", "log", "show", "blame", "ls-files", "rev-parse", "describe",
    "shortlog", "grep", "branch", "tag",
)
#: The commands that take ``--no-ext-diff`` / ``--no-textconv``.
NO_EXT_DIFF = frozenset({"diff", "log", "show"})
NO_TEXTCONV = frozenset({"diff", "log", "show", "blame", "grep"})
#: The commands that take ``--ignore-submodules=all`` (a submodule's own
#: config is another repository's).
NO_SUBMODULES = frozenset({"status", "diff"})
#: Bounds.
GIT_READ_TIMEOUT_S = 60.0
OUTPUT_CHARS = 30_000
ERROR_CHARS = 2_000
MAX_ARGS = 60
MAX_ARG_CHARS = 1_000
#: At most this many filter drivers are emptied (a config naming more is
#: refused: something is off).
MAX_FILTERS = 20

# --------------------------------------------------------------------------- #
# v1.422.2 — the OPTIONS are an ALLOWLIST, per command (the owner's "safer
# option"): the v1.421.0 review found nine holes in a blocklist over git's
# huge option and config surface. Every word that starts with "-" must be one
# of its command's options below, SPELLED IN FULL (git takes an unambiguous
# shortening of a long option, so ``--name-on`` would be ``--name-only`` — and
# ``--open-files`` was ``--open-files-in-pager``; an exact spelling is never
# read as a shortening of something else, because every spelling here is a
# real option of that command: ``test_every_allowed_long_option_is_one_git_
# knows``). A cluster of short options (``-sb``) is read LETTER BY LETTER, as
# git reads it, and every letter must be allowed; a letter that takes a value
# takes the rest of the word (``-U5``, ``-pSfoo``).
# --------------------------------------------------------------------------- #

#: How an option takes its value (each one measured against git 2.50).
FLAG = "flag"          # none: ``--stat`` (``--name-only=x`` is refused)
REQUIRED = "required"  # ``--opt=v`` or ``--opt v``; a letter: ``-A3`` or ``-A 3``
OPTIONAL = "optional"  # glued only — ``--stat=80``, ``-U5``: git reads ``--stat 80``'s
#                        "80" as a word of its own (a revision), so this does too
EQUALS = "equals"      # ``--opt=v`` only (log's ``--format``: ``--format %h`` is refused)
LASTARG = "lastarg"    # ``--contains [<commit>]``: the next word when there is one

#: The value kinds: each a full-match pattern, or a word the checker reads
#: (``text`` — a pattern, a date, a sort key: never a path git reads;
#: ``format`` — checked for a signature; ``rev`` — a commit, path-checked;
#: ``diff_merges`` — ``remerge`` refused).
_KIND_PATTERNS: dict[str, "re.Pattern[str]"] = {
    "int": re.compile(r"\d{1,6}"),
    "score": re.compile(r"\d{1,3}(?:\.\d{1,3})?%?"),
    "stat": re.compile(r"\d{1,4}(?:,\d{1,4}){0,2}"),
    "line_range": re.compile(r"\d{1,9}(?:,[+-]?\d{1,9})?|,[+-]?\d{1,9}"),
    "diff_filter": re.compile(r"[ACDMRTUXB*]{1,20}", re.IGNORECASE),
    "column": re.compile(r"[a-z]{1,10}(?:,[a-z]{1,10}){0,5}"),
}
_KIND_CHOICES: dict[str, tuple[str, ...]] = {
    "porcelain": ("v1", "v2"),
    "untracked": ("no", "normal", "all"),
    "ignored": ("traditional", "matching", "no"),
    "word_diff": ("color", "plain", "porcelain", "none"),
    "color": ("never", "auto", "always"),
    "decorate": ("short", "full", "auto", "no"),
    "algorithm": ("myers", "default", "minimal", "patience", "histogram"),
    "abbrev_ref": ("strict", "loose"),
    "diff_merges": ("off", "none", "on", "m", "first-parent", "1", "separate", "combined", "c",
                    "dense-combined", "cc"),
}
#: What each patterned kind is, in words (for the sentence a bad value gets).
_KIND_WORDS = {
    "int": "a whole number", "score": "a similarity like 50 or 50%",
    "stat": "a width like 80 or 80,40", "line_range": "a line range like 10,20 or 10,+5",
    "diff_filter": "letters from ACDMRTUXB", "column": "words like always,row",
}

Row = tuple[str, str, str]  # (the spellings, its arity, its value kind)

#: The diff options (diff, log, show).
_DIFF_ROWS: tuple[Row, ...] = (
    ("-p -u --patch", FLAG, ""), ("-s --no-patch", FLAG, ""), ("-U --unified", OPTIONAL, "int"),
    ("--stat", OPTIONAL, "stat"), ("--numstat", FLAG, ""), ("--shortstat", FLAG, ""),
    ("--name-only", FLAG, ""), ("--name-status", FLAG, ""), ("--raw", FLAG, ""),
    ("--summary", FLAG, ""), ("--compact-summary", FLAG, ""), ("--check", FLAG, ""),
    ("-w --ignore-all-space", FLAG, ""), ("-b --ignore-space-change", FLAG, ""),
    ("--ignore-space-at-eol", FLAG, ""), ("--ignore-cr-at-eol", FLAG, ""),
    ("--ignore-blank-lines", FLAG, ""), ("-W --function-context", FLAG, ""),
    ("--word-diff", OPTIONAL, "word_diff"), ("--minimal", FLAG, ""), ("--patience", FLAG, ""),
    ("--histogram", FLAG, ""), ("--diff-algorithm", REQUIRED, "algorithm"),
    ("-M --find-renames", OPTIONAL, "score"), ("-C --find-copies", OPTIONAL, "score"),
    ("--find-copies-harder", FLAG, ""), ("--no-renames", FLAG, ""),
    ("--diff-filter", REQUIRED, "diff_filter"), ("-S", REQUIRED, "text"), ("-G", REQUIRED, "text"),
    ("--pickaxe-regex", FLAG, ""), ("--pickaxe-all", FLAG, ""), ("-R", FLAG, ""),
    ("-a --text", FLAG, ""), ("--abbrev", OPTIONAL, "int"), ("--full-index", FLAG, ""),
    ("--color", OPTIONAL, "color"), ("--no-color", FLAG, ""),
    ("--no-ext-diff", FLAG, ""), ("--no-textconv", FLAG, ""),
    ("--output-indicator-new --output-indicator-old --output-indicator-context", REQUIRED, "text"),
)
#: How a commit is shown (log, show).
_SHOW_ROWS: tuple[Row, ...] = (
    ("--oneline", FLAG, ""), ("--format", EQUALS, "format"), ("--pretty", OPTIONAL, "format"),
    ("--abbrev-commit", FLAG, ""), ("--no-abbrev-commit", FLAG, ""), ("--date", REQUIRED, "text"),
    ("--relative-date", FLAG, ""), ("--decorate", OPTIONAL, "decorate"), ("--no-decorate", FLAG, ""),
    ("-m", FLAG, ""), ("--cc", FLAG, ""), ("--diff-merges", REQUIRED, "diff_merges"),
    ("--no-diff-merges", FLAG, ""), ("--first-parent", FLAG, ""),
)
#: Which commits (log; shortlog takes most of them).
_LIMIT_ROWS: tuple[Row, ...] = (
    ("--max-count", REQUIRED, "int"),
    ("--since --after --until --before", REQUIRED, "text"), ("--author", REQUIRED, "text"),
    ("--grep", REQUIRED, "text"), ("--all-match", FLAG, ""), ("--invert-grep", FLAG, ""),
    ("--all", FLAG, ""), ("--merges", FLAG, ""), ("--no-merges", FLAG, ""),
    ("--reverse", FLAG, ""), ("--date-order", FLAG, ""), ("--topo-order", FLAG, ""),
)

_ROWS: dict[str, tuple[Row, ...]] = {
    "status": (
        ("-s --short", FLAG, ""), ("-b --branch", FLAG, ""), ("--porcelain", OPTIONAL, "porcelain"),
        ("--long", FLAG, ""), ("-u --untracked-files", OPTIONAL, "untracked"),
        ("--ignored", OPTIONAL, "ignored"), ("--show-stash", FLAG, ""), ("--ahead-behind", FLAG, ""),
        ("--no-ahead-behind", FLAG, ""), ("-z --null", FLAG, ""), ("--renames", FLAG, ""),
        ("--no-renames", FLAG, ""), ("--find-renames", OPTIONAL, "score"),
    ),
    "diff": (
        ("--cached --staged", FLAG, ""), ("--merge-base", FLAG, ""), *_DIFF_ROWS,
    ),
    "log": (
        ("-n", REQUIRED, "int"), *_SHOW_ROWS[:1], ("--graph", FLAG, ""), *_SHOW_ROWS[1:], *_LIMIT_ROWS,
        ("-i --regexp-ignore-case", FLAG, ""), ("-E --extended-regexp", FLAG, ""),
        ("-F --fixed-strings", FLAG, ""), ("--follow", FLAG, ""), ("--parents", FLAG, ""),
        *_DIFF_ROWS,
    ),
    "show": (*_SHOW_ROWS, *_DIFF_ROWS),
    "blame": (
        ("-L", REQUIRED, "line_range"), ("-w", FLAG, ""), ("-M", OPTIONAL, "int"),
        ("-C", OPTIONAL, "int"), ("-e --show-email", FLAG, ""), ("--date", REQUIRED, "text"),
        ("-s", FLAG, ""), ("-l", FLAG, ""), ("-t", FLAG, ""), ("-f --show-name", FLAG, ""),
        ("-n --show-number", FLAG, ""), ("-p --porcelain", FLAG, ""), ("--line-porcelain", FLAG, ""),
        ("--root", FLAG, ""), ("--minimal", FLAG, ""), ("-b", FLAG, ""), ("--abbrev", OPTIONAL, "int"),
        ("--show-stats", FLAG, ""), ("--first-parent", FLAG, ""), ("--ignore-rev", REQUIRED, "rev"),
    ),
    "ls-files": (
        ("-c --cached", FLAG, ""), ("-m --modified", FLAG, ""), ("-o --others", FLAG, ""),
        ("-d --deleted", FLAG, ""), ("-s --stage", FLAG, ""), ("-u --unmerged", FLAG, ""),
        ("-i --ignored", FLAG, ""), ("--exclude-standard", FLAG, ""), ("-x --exclude", REQUIRED, "text"),
        ("-z", FLAG, ""), ("--directory", FLAG, ""), ("--no-empty-directory", FLAG, ""),
        ("--eol", FLAG, ""), ("--full-name", FLAG, ""), ("--error-unmatch", FLAG, ""),
        ("--deduplicate", FLAG, ""), ("--abbrev", OPTIONAL, "int"), ("--format", REQUIRED, "format"),
    ),
    "rev-parse": (
        ("--abbrev-ref", OPTIONAL, "abbrev_ref"), ("--short", OPTIONAL, "int"), ("--verify", FLAG, ""),
        ("-q --quiet", FLAG, ""), ("--symbolic", FLAG, ""), ("--symbolic-full-name", FLAG, ""),
        ("--show-toplevel", FLAG, ""), ("--show-prefix", FLAG, ""), ("--show-cdup", FLAG, ""),
        ("--git-dir", FLAG, ""), ("--absolute-git-dir", FLAG, ""), ("--git-common-dir", FLAG, ""),
        ("--is-inside-work-tree", FLAG, ""), ("--is-inside-git-dir", FLAG, ""),
        ("--is-bare-repository", FLAG, ""), ("--is-shallow-repository", FLAG, ""),
    ),
    "describe": (
        ("--tags", FLAG, ""), ("--always", FLAG, ""), ("--all", FLAG, ""), ("--long", FLAG, ""),
        ("--abbrev", OPTIONAL, "int"), ("--first-parent", FLAG, ""), ("--exact-match", FLAG, ""),
        ("--contains", FLAG, ""), ("--candidates", REQUIRED, "int"), ("--match", REQUIRED, "text"),
        ("--exclude", REQUIRED, "text"),
    ),
    "shortlog": (
        # shortlog's own -n is "numbered" and -c "committer" (never log's).
        ("-s --summary", FLAG, ""), ("-n --numbered", FLAG, ""), ("-e --email", FLAG, ""),
        ("--max-count", REQUIRED, "int"), ("--since --after --until --before", REQUIRED, "text"),
        ("--author", REQUIRED, "text"), ("--grep", REQUIRED, "text"), ("--all", FLAG, ""),
        ("--merges", FLAG, ""), ("--no-merges", FLAG, ""), ("--first-parent", FLAG, ""),
    ),
    "grep": (
        ("-n --line-number", FLAG, ""), ("-i --ignore-case", FLAG, ""), ("-w --word-regexp", FLAG, ""),
        ("-l --files-with-matches --name-only", FLAG, ""), ("-L --files-without-match", FLAG, ""),
        ("-c --count", FLAG, ""), ("-e", REQUIRED, "text"), ("-E --extended-regexp", FLAG, ""),
        ("-F --fixed-strings", FLAG, ""), ("-P --perl-regexp", FLAG, ""), ("-G --basic-regexp", FLAG, ""),
        ("--cached", FLAG, ""), ("-v --invert-match", FLAG, ""), ("-o --only-matching", FLAG, ""),
        ("-I", FLAG, ""), ("-h", FLAG, ""), ("-H", FLAG, ""), ("--break", FLAG, ""),
        ("--heading", FLAG, ""), ("-A --after-context", REQUIRED, "int"),
        ("-B --before-context", REQUIRED, "int"), ("-C --context", REQUIRED, "int"),
        ("-m --max-count", REQUIRED, "int"), ("--max-depth", REQUIRED, "int"),
        ("--and --or --not --all-match", FLAG, ""), ("-p --show-function", FLAG, ""),
        ("-W --function-context", FLAG, ""), ("--column", FLAG, ""), ("--full-name", FLAG, ""),
        ("-a --text", FLAG, ""), ("-z --null", FLAG, ""), ("--no-color", FLAG, ""),
    ),
    "branch": (
        ("-l --list", FLAG, ""), ("-a --all", FLAG, ""), ("-r --remotes", FLAG, ""),
        ("-v --verbose", FLAG, ""), ("--show-current", FLAG, ""),
        ("--contains --no-contains --merged --no-merged", LASTARG, "rev"),
        ("--points-at", REQUIRED, "rev"), ("--sort", REQUIRED, "text"), ("--format", REQUIRED, "format"),
        ("-i --ignore-case", FLAG, ""), ("--omit-empty", FLAG, ""), ("--color", OPTIONAL, "color"),
        ("--no-color", FLAG, ""), ("--abbrev", OPTIONAL, "int"), ("--no-abbrev", FLAG, ""),
        ("--column", OPTIONAL, "column"), ("--no-column", FLAG, ""),
    ),
    "tag": (
        ("-l --list", FLAG, ""), ("-n", OPTIONAL, "int"),
        ("--contains --no-contains --merged --no-merged --points-at", LASTARG, "rev"),
        ("--sort", REQUIRED, "text"), ("--format", REQUIRED, "format"), ("-i --ignore-case", FLAG, ""),
        ("--omit-empty", FLAG, ""), ("--color", OPTIONAL, "color"), ("--no-color", FLAG, ""),
        ("--column", OPTIONAL, "column"), ("--no-column", FLAG, ""),
    ),
}


def _build(rows: tuple[Row, ...]) -> dict[str, tuple[str, str]]:
    table: dict[str, tuple[str, str]] = {}
    for spellings, arity, kind in rows:
        for spelling in spellings.split():
            table.setdefault(spelling, (arity, kind))
    return table


#: Every command's options: spelling (``-n``, ``--max-count``) → (arity, kind).
OPTIONS: dict[str, dict[str, tuple[str, str]]] = {cmd: _build(rows) for cmd, rows in _ROWS.items()}
#: Commands that take a COUNT as a word (``-3``: log's last three commits,
#: grep's three lines of context).
_COUNTS = frozenset({"log", "shortlog", "grep"})
#: rev-parse reads its options by hand: it takes no cluster (``-qq`` would be
#: passed through as a word of its own).
_NO_CLUSTERS = frozenset({"rev-parse"})
#: ``branch`` / ``tag``: the options that make a call a LISTING (its other
#: words are then patterns, never a new name).
_LISTING: dict[str, frozenset[str]] = {
    "branch": frozenset({"-l", "--list", "-a", "--all", "-r", "--remotes", "--contains", "--no-contains",
                         "--merged", "--no-merged", "--points-at"}),
    "tag": frozenset({"-l", "--list", "-n", "--contains", "--no-contains", "--merged", "--no-merged",
                      "--points-at"}),
}

#: The sentences an option KNOWN to be dangerous gets (it would be refused
#: anyway — the allowlist does not hold it — but the model is told why).
_WRITES_OR_RUNS = "git_read does not take {opt}: it can write a file, run a program or read outside this folder."
_PAGER = "git_read does not take -O: it can run a program or read a file outside this folder."
SIGNATURE_REFUSAL = "git_read does not show signatures: checking one runs a program the folder can name."
REMERGE_REFUSAL = ("git_read does not re-merge a merge: re-merging runs the folder's own merge program. "
                   "Use --diff-merges=separate or --cc.")
_READS_A_FILE = ("git_read does not take {opt} for {command}: it reads a file of its own. Read files "
                 "with read_file or grep.")
_DANGER_ALL: dict[str, str] = {
    **{o: _WRITES_OR_RUNS for o in (
        "--output", "--ext-diff", "--textconv", "--no-index", "--open-files-in-pager", "--exec",
        "--exec-path", "--upload-pack", "--recurse-submodules",
        # v1.421.0 review: ``--submodule=diff`` runs ``git diff`` INSIDE a
        # submodule, whose own config names its external diff;
        # ``--ignore-submodules=none`` beat the guard's ``=all`` (measured).
        "--submodule", "--ignore-submodules",
    )},
    "-O": _PAGER,
    "--show-signature": SIGNATURE_REFUSAL,
    # v1.421.0 review: a REMERGE re-runs a merge in memory, which runs the
    # merge driver ``.gitattributes`` + ``merge.<x>.driver`` name (measured).
    "--remerge-diff": REMERGE_REFUSAL,
}
#: v1.421.0 review, kept as their own sentences: ``git status -v`` shows the
#: staged diff through the folder's own TEXTCONV driver (status takes no
#: ``--no-textconv``; measured: the program ran); ``git describe --dirty`` /
#: ``--broken`` save the refreshed index (git takes the index lock whatever
#: ``GIT_OPTIONAL_LOCKS`` says; measured: ``.git/index`` rewritten). And the
#: options that read a FILE the model names (the old cut checked the path;
#: none is needed to review a change).
_STATUS_VERBOSE = ("git_read does not take {opt} for status: it shows the change through the folder's "
                   "own converters, which can run a program. Use git diff --cached to see the staged "
                   "change.")
_DESCRIBE_DIRTY = ("git_read does not take {opt} for describe: it rewrites git's index. Use git status "
                   "to see whether the folder has changes.")
_DANGER_FOR: dict[str, dict[str, str]] = {
    "status": {"--verbose": _STATUS_VERBOSE, "-v": _STATUS_VERBOSE},
    "describe": {"--dirty": _DESCRIBE_DIRTY, "--broken": _DESCRIBE_DIRTY},
    "blame": {o: _READS_A_FILE for o in ("--contents", "--ignore-revs-file", "-S")},
    "grep": {
        "-f": _READS_A_FILE,
        # v1.422.2: files git does not track are searched with the grep tool,
        # which follows the file rules (a junction inside the folder that
        # leads out was followed by ``git grep --untracked``).
        "--untracked": ("git_read does not take --untracked: search files git does not track with the "
                        "grep tool."),
    },
    "ls-files": {o: _READS_A_FILE for o in ("-X", "--exclude-from", "--exclude-per-directory")},
}

#: A format that SHOWS a signature runs gpg (a program the config names).
_SIGNATURE_FORMAT = re.compile(r"%G|%\(signature", re.IGNORECASE)
#: A drive ("C:\x", and a drive-relative "C:x") at the start of a word.
_DRIVE = re.compile(r"(?:^|[=,])[A-Za-z]:")
#: ``--diff-merges``'s values that re-merge (``remerge`` and its letter).
_REMERGE = frozenset({"remerge", "r"})


def allowed_options(command: str) -> str:
    """Every option ``command`` takes (``-n/--max-count``), in the table's
    order. Pure."""
    return ", ".join("/".join(spellings.split()) for spellings, _a, _k in _ROWS.get(command, ()))


#: The options a refusal NAMES (the most useful ones a reviewer reaches for —
#: the full lists run to 1,000 characters; the sentence stays short). Each is
#: in :data:`OPTIONS` (pinned).
_COMMON: dict[str, str] = {
    "status": "-s/--short, -b/--branch, --porcelain, -u/--untracked-files, --ignored",
    "diff": "--stat, --name-only, --name-status, --cached, -U/--unified, -w, --word-diff, --diff-filter",
    "log": "--oneline, -n, --format, --stat, -p, --since, --author, --grep, --name-only, -S, --follow",
    "show": "--stat, --name-only, -s/--no-patch, --format, --oneline, -U/--unified",
    "blame": "-L, -w, -M, -C, -e, --date, -s, --porcelain",
    "ls-files": "-c, -m, -o, -d, -s, --exclude-standard, -z",
    "rev-parse": "--abbrev-ref, --short, --verify, -q, --show-toplevel, --is-inside-work-tree",
    "describe": "--tags, --always, --abbrev, --long, --match, --contains",
    "shortlog": "-s, -n, -e, --since, --author",
    "grep": "-n, -i, -w, -l, -c, -e, -E, -F, --cached, -A, -B, -C",
    "branch": "--list, -a, -r, -v, --contains, --merged, --sort, --format",
    "tag": "--list, -n, --contains, --points-at, --sort, --format",
}


def _danger(command: str, name: str) -> str:
    said = _DANGER_FOR.get(command, {}).get(name) or _DANGER_ALL.get(name)
    return said.format(opt=name, command=command) if said else ""


def _unknown(command: str, name: str) -> str:
    """The sentence for an option ``command`` does not take (its exact
    spelling, a dangerous one's shortening, or a shortening of one it does
    take). Pure."""
    if len(name) > 40:
        return f"git_read does not take {_shown(name)} for git {command}: no option is that long."
    said = _danger(command, name)
    if said:
        return said
    table = OPTIONS[command]
    if name.startswith("--") and len(name) > 2:
        # A shortening of a dangerous option says why it is dangerous (git
        # would have read ``--open-files`` as ``--open-files-in-pager``).
        for full in (*_DANGER_FOR.get(command, {}), *_DANGER_ALL):
            if full.startswith("--") and full.startswith(name):
                return _danger(command, full).replace(full, name, 1)
        matches = [s for s in table if s.startswith(name)]
        if len(matches) == 1:
            return (f"git_read takes git's options spelled in full: write {matches[0]} for {name} "
                    f"(git {command}).")
    if command in _LISTING:
        return (f"git_read runs git {command} only to LIST, and does not take {name} (a listing takes "
                f"options such as {_COMMON[command]}).")
    return (f"git_read does not take {name} for git {command}; it takes common read-only options "
            f"spelled in full, such as {_COMMON[command]}.")


def _shown(word: str) -> str:
    """A word as a refusal quotes it: at most 40 characters."""
    return word if len(word) <= 40 else word[:40] + "…"


def _value_problem(command: str, name: str, kind: str, value: str) -> str:
    """The sentence a value of ``name`` is refused with, or "". Pure."""
    if value == "":
        return f"git_read needs a value after {name}."
    if kind == "text":
        return ""  # a pattern, a date, a sort key: git reads no file by it
    if kind == "format":
        return SIGNATURE_REFUSAL if _SIGNATURE_FORMAT.search(value) else ""
    if kind == "rev":
        return _path_problem(value)
    if kind == "diff_merges" and value.strip().lower() in _REMERGE:
        return REMERGE_REFUSAL
    choices = _KIND_CHOICES.get(kind)
    if choices is not None:
        if value in choices:
            return ""
        return f"git_read takes {name} with one of {', '.join(choices)}; not \"{_shown(value)}\"."
    pattern = _KIND_PATTERNS.get(kind)
    if pattern is not None and pattern.fullmatch(value):
        return ""
    return f"git_read takes {name} with {_KIND_WORDS.get(kind, 'a plain value')}; not \"{_shown(value)}\"."


def _word_problem(word: Any) -> str:
    if not isinstance(word, str):
        return "git_read takes its words as text."
    if "\x00" in word or len(word) > MAX_ARG_CHARS:
        return "git_read cannot use that word: it is too long or holds a NUL."
    return ""


def _next_value(command: str, name: str, kind: str, args: list[str], i: int,
                glue: str) -> tuple[str, int]:
    """The value in the NEXT word (``--author x``, ``-A 3``): ``(problem,
    words used)``. A next word that starts with "-" is refused — git would
    take it as the value, and reading it as an option here is the one place
    the two could disagree; the glued form says it plainly (``glue``)."""
    if i + 1 >= len(args):
        return f"git_read needs a value after {name}.", 0
    nxt = args[i + 1]
    problem = _word_problem(nxt)
    if problem:
        return problem, 1
    if nxt.startswith("-"):
        return (f"git_read cannot tell \"{_shown(nxt)}\" from an option: write it as {glue}{_shown(nxt)}.", 1)
    return _value_problem(command, name, kind, nxt), 1


def _long_problem(command: str, arg: str, args: list[str], i: int) -> tuple[str, int, str]:
    """A ``--word``: ``(problem, extra words used, its name)``."""
    name, eq, value = arg.partition("=")
    spec = OPTIONS[command].get(name)
    if spec is None:
        return _unknown(command, name), 0, name
    arity, kind = spec
    if arity == FLAG:
        return (f"git_read takes {name} with no value." if eq else ""), 0, name
    if eq:
        return _value_problem(command, name, kind, value), 0, name
    if arity == OPTIONAL:
        return "", 0, name
    if arity == EQUALS:
        return f"git_read takes {name} as {name}=<value>.", 0, name
    if arity == LASTARG and i + 1 >= len(args):
        return "", 0, name
    problem, used = _next_value(command, name, kind, args, i, f"{name}=")
    return problem, used, name


def _cluster_problem(command: str, arg: str, args: list[str], i: int) -> tuple[str, int, list[str]]:
    """A ``-abc`` word, LETTER BY LETTER as git reads it: ``(problem, extra
    words used, the letters' spellings)``. A letter that takes a value takes
    the rest of the word, else (when it needs one) the next word."""
    table = OPTIONS[command]
    if command in _NO_CLUSTERS and len(arg) > 2:
        return _unknown(command, arg), 0, []
    seen: list[str] = []
    letters = arg[1:]
    for k, ch in enumerate(letters):
        name = "-" + ch
        spec = table.get(name)
        if spec is None:
            return _unknown(command, name), 0, seen
        seen.append(name)
        arity, kind = spec
        if arity == FLAG:
            continue
        rest = letters[k + 1:]
        if rest:
            return _value_problem(command, name, kind, rest), 0, seen
        if arity == OPTIONAL:
            return "", 0, seen
        problem, used = _next_value(command, name, kind, args, i, name)
        return problem, used, seen
    return "", 0, seen


def refusal(command: str, args: list[str]) -> str:
    """The sentence a call is refused with, or "". Pure.

    Every word before ``--`` that starts with "-" is one of the command's
    :data:`OPTIONS` (or a count, for :data:`_COUNTS`), its value checked by
    its kind; every other word — and every word after ``--`` — is a revision
    or a path, read for a place outside the folder (:func:`_path_problem`)."""
    return _check(command, args)[0]


def _check(command: str, args: list[str]) -> tuple[str, list[str]]:
    """:func:`refusal`'s work: ``(the sentence or "", the option spellings
    the words used, in order)``. Pure."""
    problem, used_names, _revision = _walk(command, args)
    return problem, used_names


def _walk(command: str, args: list[str]) -> tuple[str, list[str], bool]:
    """:func:`_check`'s reading, plus whether a word BEFORE ``--`` is a
    revision or a path (not an option's value). Pure."""
    if command not in ALLOWED:
        return (f"git_read runs only read-only git commands ({', '.join(ALLOWED)}); "
                f"\"{_shown(command) or '(none)'}\" is not one of them."), [], False
    if len(args) > MAX_ARGS:
        return f"git_read takes at most {MAX_ARGS} words after the command.", [], False
    used_names: list[str] = []
    listing = False
    positional = False
    revision = False
    ended = False
    i = 0
    while i < len(args):
        arg = args[i]
        problem = _word_problem(arg)
        if problem:
            return problem, used_names, revision
        if ended or not arg.startswith("-"):
            problem = _path_problem(arg)
            if problem:
                return problem, used_names, revision
            positional = True
            revision = revision or not ended
            i += 1
            continue
        if arg == "--":
            ended = True
            i += 1
            continue
        names: list[str]
        if arg == "-":
            return _unknown(command, arg), used_names, revision
        if command in _COUNTS and re.fullmatch(r"-\d{1,6}", arg):
            problem, used, names = "", 0, []
        elif arg.startswith("--"):
            problem, used, name = _long_problem(command, arg, args, i)
            names = [name]
        else:
            problem, used, names = _cluster_problem(command, arg, args, i)
        if problem:
            return problem, used_names, revision
        used_names += names
        if any(n in _LISTING.get(command, ()) for n in names):
            listing = True
        i += 1 + used
    if command in _LISTING and positional and not listing:
        return (f"git {command} with a name makes a new {command}; git_read only lists them — add "
                "--list (the names are then patterns)."), used_names, revision
    return "", used_names, revision


#: v1.422.2 REVIEW: ``git shortlog`` with no revision reads its STANDARD
#: INPUT (git adds HEAD only when that is a terminal), and git_read's is
#: empty — so a reviewer's ``shortlog -sn`` answered "(git printed nothing)"
#: as if nobody had committed (measured). With no word before ``--`` (and no
#: ``--all``), HEAD is the revision, as in a terminal
#: (:func:`_default_revision`); a shortlog that still prints nothing says why.
SHORTLOG_EMPTY = ("git shortlog printed nothing: with no revision it reads its input, which is empty "
                  "here. Name one, such as HEAD or --all.")


def _default_revision(command: str, args: list[str]) -> list[str]:
    """``args`` with HEAD as ``shortlog``'s revision when it names none (see
    :data:`SHORTLOG_EMPTY`); every other call unchanged. Pure."""
    if command != "shortlog":
        return list(args)
    problem, names, revision = _walk(command, args)
    if problem or revision or "--all" in names:
        return list(args)
    if "--" in args:
        k = args.index("--")
        return [*args[:k], "HEAD", *args[k:]]
    return [*args, "HEAD"]


def _path_problem(arg: str) -> str:
    """A word that names a place OUTSIDE the working folder, by its text: an
    absolute path, a drive, a network share or a ``..`` step ("" when
    none). ``a..b`` (a range of commits) is no step. Pure."""
    value = arg.split("=", 1)[1] if (arg.startswith("--") and "=" in arg) else arg
    if not value:
        return ""
    if value.startswith(("\\\\", "//", "/", "\\")) or _DRIVE.search(value):
        return "git_read reads inside this folder only: give paths relative to it."
    if any(part == ".." for part in re.split(r"[\\/:=,]", value)):
        return "git_read reads inside this folder only: a path may not step up with \"..\"."
    return ""


def _split_command(raw: Any, args: list[str]) -> tuple[str, list[str]]:
    """``command`` as the model wrote it: "diff", or "git diff --stat" (the
    words after the command join ``args``). Never a shell — the words are
    split the way a command line would be."""
    text = " ".join(str(raw or "").split())
    if not text:
        return "", list(args)
    try:
        words = shlex.split(text, posix=True)
    except ValueError:
        words = text.split()
    if words and words[0].lower() == "git":
        words = words[1:]
    if not words:
        return "", list(args)
    return words[0].lower(), [*words[1:], *args]


#: v1.422.2 REVIEW: settings that make git READ a file the config names and
#: show what it read, so the folder's own (a worker can write it) is pointed
#: at the null device (:func:`safe_argv`; ``tests/test_git_read_file_
#: settings_v14222.py``): ``mailmap.file`` — log, show, blame and shortlog
#: map every author through it (``log.mailmap`` is on by default), so a line
#: ``<text> <a@b.c>`` of ANY file came back as an author's name (measured: an
#: outside file's text in ``git log``, ``blame`` and ``shortlog``);
#: ``core.attributesFile`` — a word that is not a valid attribute name is
#: warned about BY NAME on stderr, which a failing call shows (measured). The
#: in-tree ``.mailmap`` / ``.gitattributes`` still count, and the user's own
#: global setting is left as it is (as for filters). Lowercase key → the
#: spelling ``-c`` sets.
_FILE_SETTINGS: dict[str, str] = {"mailmap.file": "mailmap.file",
                                  "core.attributesfile": "core.attributesFile"}


def _repo_settings(cwd: Any, env: dict[str, str]) -> "tuple[list[str], list[str], list[str], list[str]] | None":
    """``(filters, worktrees, skip_files, file_settings)``: the filter
    drivers the REPOSITORY's own config defines (local or worktree scope —
    what a worker can write; an included file counts as the scope that
    includes it), every ``core.worktree`` it names (any scope), the
    ``blame.ignoreRevsFile`` files it names (local or worktree scope), and
    which of :data:`_FILE_SETTINGS` it sets (local or worktree scope; any
    scope when git cannot say); None when the config could not be read. Read
    NUL-separated (``-z``): a driver's name may hold a space. BLOCKING."""
    from ..git.integration import run_git

    pattern = r"^(filter\.|core\.worktree$|blame\.ignorerevsfile$|mailmap\.file$|core\.attributesfile$)"
    got = run_git(["--no-pager", "config", "-z", "--show-scope", "--get-regexp", pattern], cwd,
                  timeout=15.0, env=env, identity=False, encoding="utf-8")
    scoped = True
    if got.code not in (0, 1) or got.timed_out:
        # An older git with no --show-scope: every filter is emptied.
        got = run_git(["--no-pager", "config", "-z", "--get-regexp", pattern], cwd,
                      timeout=15.0, env=env, identity=False, encoding="utf-8")
        scoped = False
        if got.code not in (0, 1) or got.timed_out:
            return None
    tokens = [t for t in str(got.out or "").split("\0")]
    names: list[str] = []
    worktrees: list[str] = []
    skip_files: list[str] = []
    file_settings: list[str] = []
    i = 0
    while i < len(tokens):
        scope = ""
        if scoped:
            scope, i = tokens[i].strip(), i + 1
            if i >= len(tokens):
                break
        entry, i = tokens[i], i + 1
        if not entry.strip():
            continue
        key, _, value = entry.partition("\n")
        low = key.lower()
        if low == "core.worktree":
            worktrees.append(value)
            continue
        if scoped and scope not in ("local", "worktree"):
            continue
        if low == "blame.ignorerevsfile":
            skip_files.append(value)
            continue
        if low in _FILE_SETTINGS:
            if low not in file_settings:
                file_settings.append(low)
            continue
        if not low.startswith("filter.") or key.count(".") < 2:
            continue
        name = key[len("filter."):].rsplit(".", 1)[0]
        if name and name not in names:
            names.append(name)
    return names, worktrees, skip_files, file_settings


def _repo_filters(cwd: Any, env: dict[str, str]) -> "list[str] | None":
    """The filter drivers the REPOSITORY's own config defines (local or
    worktree scope — what a worker can write), or None when the config could
    not be read. BLOCKING."""
    got = _repo_settings(cwd, env)
    return None if got is None else got[0]


#: v1.421.0 review: ``blame.ignoreRevsFile`` in the repository's config makes
#: ``git blame`` READ that file — and git names a line it cannot read as a
#: commit in its error ("fatal: invalid object name: <the line>"; measured: a
#: file outside the folder's first line came back). An empty ``-c`` value does
#: not clear it (git reads the list sorted, the empty name first). A file the
#: config names must be a plain path inside the repository (``.git-blame-
#: ignore-revs``, the usual one), else blame is refused.
SKIP_FILE_REFUSAL = ("This folder's git settings name a file of commits for blame to skip that lies "
                     "outside this folder, so git_read ran nothing.")


def _skip_file_problem(values: list[str]) -> str:
    for value in values:
        v = str(value or "").strip()
        if not v:
            continue  # an empty name clears the list: nothing is read
        if v.startswith("~") or "%(" in v or _path_problem(v):
            return SKIP_FILE_REFUSAL
    return ""


#: v1.421.0 review: ``git -c`` splits a key at its FIRST "=", so a filter
#: driver whose NAME holds one (``[filter "a=b"]``, named by
#: ``.gitattributes`` ``filter=a=b``) cannot be emptied — measured: the
#: override set another key and the filter ran on diff, status and blame.
FILTER_NAME_REFUSAL = ("This folder's git settings name a file filter git_read cannot switch off, so it "
                       "ran nothing.")
#: v1.421.0 review: ``core.worktree`` in the repository's config sends git to
#: files OUTSIDE the folder (measured: ``git status`` listed them).
WORKTREE_REFUSAL = ("This folder's git settings point git at files outside this folder, so git_read "
                    "ran nothing.")


def _network_spelling(value: str) -> bool:
    """A UNC / device spelling, read from its TEXT (looking one up is a
    connection to that host — the v1.379.0 rule)."""
    v = str(value or "").strip()
    return v.startswith(("\\\\", "//", "\\/", "/\\"))


#: v1.421.0 review: ``read_file`` and ``grep`` refuse Iron Jarvis's own keys
#: and records (``core.fs_policy``'s protected roots: the secrets and undo
#: stores, the database); git reads files by itself, so in a folder that HOLDS
#: one (the user's home holds the app's state home) ``git blame --contents``
#: printed a key file and ``git grep --untracked`` searched the secrets store
#: (measured).
PROTECTED_REFUSAL = ("This folder holds Iron Jarvis's own keys or records, which are never read, so "
                     "git_read ran nothing.")


def _holds_protected(folder: str) -> bool:
    """Is ``folder`` inside one of Iron Jarvis's protected roots, or does it
    hold one? Fail-closed (an unreadable answer is yes). BLOCKING."""
    from ..core import fs_policy

    try:
        if fs_policy.is_protected_path(folder):
            return True
        here = fs_policy._canonical(folder)
        return any(fs_policy._within(root, here) for root in fs_policy.protected_roots())
    except Exception:  # noqa: BLE001
        return True


def _place_problem(cwd: Any, worktrees: list[str], filters: list[str],
                   env: dict[str, str]) -> str:
    """Where git would read must be safe to read, else the sentence why
    ("" when it is). Two rules, after the repository's top is found
    (``rev-parse --show-toplevel``; none outside a repository):

    * a ``core.worktree`` must name a working folder that CONTAINS ``cwd``
      (with none, git found the repository by walking UP from ``cwd``, so its
      top always does) — else :data:`WORKTREE_REFUSAL`; a network spelling is
      refused by its TEXT before git is asked anything (looking it up is a
      connection to that host);
    * the folder git reads — the repository's top (``:/`` and ``HEAD:<path>``
      reach all of it), else ``cwd`` — holds none of Iron Jarvis's protected
      roots: :data:`PROTECTED_REFUSAL`.

    BLOCKING."""
    if any(_network_spelling(w) for w in worktrees):
        return WORKTREE_REFUSAL
    from ..git.integration import run_git

    got = run_git(safe_argv("rev-parse", ["--show-toplevel"], filters), cwd, timeout=15.0, env=env,
                  identity=False, encoding="utf-8")
    out = str(got.out or "").strip()
    top = out.splitlines()[0].strip() if out else ""
    found = got.code == 0 and not got.timed_out and bool(top)
    if worktrees:
        if not found or _network_spelling(top):
            return WORKTREE_REFUSAL
        try:
            here = os.path.normcase(os.path.realpath(str(cwd)))
            root = os.path.normcase(os.path.realpath(top))
            if os.path.commonpath([here, root]) != root:
                return WORKTREE_REFUSAL
        except (OSError, ValueError):
            return WORKTREE_REFUSAL
    if _holds_protected(top if found else str(cwd)):
        return PROTECTED_REFUSAL
    return ""


def safe_argv(command: str, args: list[str], filters: list[str],
              files: "list[str] | tuple[str, ...]" = ()) -> list[str]:
    """The whole argv after ``git`` (:func:`run` passes it to ``run_git``):
    ``files`` are the :data:`_FILE_SETTINGS` keys the repository's own config
    sets, each pointed at the null device. Pure."""
    pre = [
        "--no-pager",
        "-c", "core.fsmonitor=false",
        "-c", f"core.hooksPath={os.devnull}",
        "-c", "log.showSignature=false",
        "-c", "protocol.allow=never",
        "-c", "core.quotepath=off",
        "-c", "color.ui=never",
        # v1.421.0 review: the repo's own ``log.diffMerges = remerge`` made a
        # plain ``git log -m -p`` re-merge a merge in memory, running the
        # merge driver the folder names (measured): git's default, forced.
        "-c", "log.diffMerges=separate",
        # v1.421.0 review: a submodule's diff is never shown by running git
        # INSIDE it (its own config is another repository a worker can write:
        # the repo's ``diff.submodule = diff`` made ``git log -p`` run that
        # submodule's ``diff.external``; measured), nor searched through it.
        "-c", "diff.submodule=short",
        "-c", "submodule.recurse=false",
        # v1.421.0 review: no signature program — the repo's own
        # ``format.pretty`` / ``pretty.<alias>`` holding ``%G?`` checked a
        # (fake) signed commit's signature with the ``gpg.program`` the same
        # config named, on a PLAIN ``git log`` (measured, for all three
        # formats). Empty: git says "cannot spawn" and runs nothing.
        "-c", "gpg.program=",
        "-c", "gpg.openpgp.program=",
        "-c", "gpg.x509.program=",
        "-c", "gpg.ssh.program=",
    ]
    for name in filters:
        pre += ["-c", f"filter.{name}.clean=", "-c", f"filter.{name}.smudge=",
                "-c", f"filter.{name}.process=", "-c", f"filter.{name}.required=false"]
    for key in files:
        spelled = _FILE_SETTINGS.get(str(key).lower())
        if spelled:
            pre += ["-c", f"{spelled}={os.devnull}"]
    safe: list[str] = []
    if command in NO_EXT_DIFF:
        safe.append("--no-ext-diff")
    if command in NO_TEXTCONV:
        safe.append("--no-textconv")
    if command in NO_SUBMODULES:
        safe.append("--ignore-submodules=all")
    return [*pre, command, *safe, *args]


def git_read_env() -> dict[str, str]:
    """A Build session's git environment for a READ (see the module doc)."""
    from ..git.integration import git_env

    env = git_env({"GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1"})
    for key in ("GIT_EXTERNAL_DIFF", "GIT_PAGER", "PAGER", "GIT_SSH_COMMAND", "GIT_ASKPASS"):
        env.pop(key, None)
    return env


def _masked(text: str) -> str:
    try:
        from ..detections.redact import mask

        return str(mask(text))
    except Exception:  # noqa: BLE001 — a mask that fails: the text is not shown
        return "[output withheld: it could not be checked for secrets]"


def run(cwd: Any, command: str, args: list[str]) -> ToolResult:
    """BLOCKING: one checked, read-only git call in ``cwd``."""
    from ..git.integration import run_git

    args = _default_revision(command, args)
    env = git_read_env()
    settings = _repo_settings(cwd, env)
    if settings is None:
        return ToolResult(ok=False, error="git_read could not read this folder's git settings, so it ran nothing.")
    filters, worktrees, skip_files, files = settings
    if len(filters) > MAX_FILTERS:
        return ToolResult(ok=False, error="This folder's git settings name too many file filters; git_read ran nothing.")
    if any("=" in name for name in filters):
        return ToolResult(ok=False, error=FILTER_NAME_REFUSAL, data={"command": command})
    problem = (_skip_file_problem(skip_files) if command == "blame" else "") \
        or _place_problem(cwd, worktrees, filters, env)
    if problem:
        return ToolResult(ok=False, error=problem, data={"command": command})
    got = run_git(safe_argv(command, args, filters, files), cwd, timeout=GIT_READ_TIMEOUT_S, env=env,
                  identity=False, encoding="utf-8")
    label = f"git {command}"
    if got.timed_out:
        return ToolResult(ok=False, error=f"{label} did not finish within {int(GIT_READ_TIMEOUT_S)} seconds, "
                                          "so it was stopped.", data={"command": command})
    if got.code != 0:
        err = _masked(str(got.err or got.out or "").strip())
        if "not a git repository" in err.lower():
            return ToolResult(ok=False, error="This folder is not in a git repository.", data={"command": command})
        if len(err) > ERROR_CHARS:
            err = err[:ERROR_CHARS].rstrip() + " […]"
        return ToolResult(ok=False, error=f"{label} failed: {err or 'it gave no reason'}",
                          data={"command": command, "exit_code": got.code})
    out = _masked(str(got.out or ""))
    total = len(out)
    cut = total > OUTPUT_CHARS
    if cut:
        out = out[:OUTPUT_CHARS].rstrip() + (
            f"\n\n[cut: the first {OUTPUT_CHARS:,} of {total:,} characters were shown — narrow it "
            "with a path, -n, --stat or --name-only]"
        )
    if not out.strip() and command == "shortlog":
        out = SHORTLOG_EMPTY
    return ToolResult(ok=True, output=out or "(git printed nothing)",
                      data={"command": command, "chars": total, "truncated": cut})


class GitReadTool(Tool):
    name = NAME
    description = (
        "Ask git about this folder, read-only: status, diff, log, show, blame, ls-files, "
        "rev-parse, describe, shortlog, grep, and branch / tag listings. `command` is the git "
        "command (\"diff\"), `args` its words ([\"--stat\", \"HEAD~1\"]). It takes each "
        "command's common read-only options spelled in full (no shortenings; a refusal names the "
        "ones it takes). It never changes the repository, never writes a file and never runs a "
        "program; paths stay inside this folder. It does not run tests."
    )
    reversibility = Reversibility.READONLY
    risk_class = RiskClass.READ
    returns_untrusted_content = True  # a diff is text another model wrote
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "enum": list(ALLOWED),
                "description": "The git command: " + ", ".join(ALLOWED) + ".",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "The words after the command, one per item (paths relative to the folder).",
            },
        },
        "required": ["command"],
    }

    async def execute(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        raw_args = args.get("args")
        if raw_args is None:
            words: list[str] = []
        elif isinstance(raw_args, list):
            words = [str(a) if not isinstance(a, str) else a for a in raw_args]
        elif isinstance(raw_args, str):
            try:
                words = shlex.split(raw_args, posix=True)
            except ValueError:
                words = raw_args.split()
        else:
            return ToolResult(ok=False, error="`args` is a list of words.")
        command, words = _split_command(args.get("command"), words)
        problem = refusal(command, words)
        if problem:
            return ToolResult(ok=False, error=problem, data={"command": command, "refused": True})
        cwd = getattr(ctx, "workspace", None)
        if cwd is None or not await asyncio.to_thread(lambda: os.path.isdir(str(cwd))):
            return ToolResult(ok=False, error="git_read has no folder to read here.")
        return await asyncio.to_thread(run, cwd, command, words)


def git_read_tools() -> list[Tool]:
    return [GitReadTool()]


__all__ = [
    "ALLOWED", "GIT_READ_TIMEOUT_S", "GitReadTool", "NAME", "OPTIONS", "OUTPUT_CHARS",
    "allowed_options", "git_read_env", "git_read_tools", "refusal", "run", "safe_argv",
]
