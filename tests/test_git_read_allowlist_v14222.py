"""v1.422.2 — ``git_read``'s OPTIONS are an ALLOWLIST, per command.

The v1.421.0 review found nine holes in ``git_read``'s option BLOCKLIST
against real git 2.50 (``tests/test_git_read_review_v14210.py``: a status
``-v``, a ``-O`` inside a cluster, a ``--submodule=diff`` …). The owner chose
"the safer option": every word that starts with "-" must now be one of its
command's own options a reviewer needs (``harness/git_read.OPTIONS``), else
it is refused in one plain sentence.

What these pins hold, each against the REAL git on this PC:

1. every allowed option RUNS — a table of calls, each through the tool
   against a real repository (commits, a merge, tags, a branch, a staged and
   an unstaged change), each answered; and the table uses EVERY option on
   every command's list (a new option needs a call here);
2. every allowed long spelling is one git KNOWS as itself: in the command's
   own option list (``--git-completion-helper-all``) or, for the revision
   options git reads by hand, a shortening of none of them — so git can
   never read an allowed spelling as a shortening of something else;
3. what is refused: options not on the list (today's and a made-up future
   one), the dangerous ones with their own sentence, a cluster with ONE bad
   letter, a value of the wrong kind, a separate value that starts with "-",
   a glued-only option's separate word read as a word of its own (as git
   reads it), and SHORTENINGS of long options — git takes an unambiguous one
   (``git status --bra`` is ``--branch``: the control proves it), the tool
   never does, and names the full spelling;
4. the refusal is one plain sentence naming the option and options the
   command does take; the curated names it gives are all on the list.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from iron_jarvis.harness import git_read as G
from iron_jarvis.tools.base import ToolContext


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "-c", "core.autocrlf=false", *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=60,
    )


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    """A repository with history to read: two commits on main, a side branch
    merged with a merge commit, an annotated and a light tag, a staged change,
    an unstaged change and an untracked file."""
    repo = tmp_path_factory.mktemp("allow") / "repo"
    repo.mkdir()
    assert _git(repo, "init", "-q", "-b", "main").returncode == 0
    (repo / "app.py").write_bytes(b"X = 1\n\ndef f():\n    return X\n")
    (repo / "notes.txt").write_bytes(b"hello\n")
    (repo / ".gitignore").write_bytes(b"*.tmp\n")
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "first").returncode == 0
    assert _git(repo, "tag", "-a", "v1.0", "-m", "one").returncode == 0
    assert _git(repo, "tag", "light").returncode == 0
    assert _git(repo, "switch", "-q", "-c", "side").returncode == 0
    (repo / "notes.txt").write_bytes(b"hello side\n")
    assert _git(repo, "commit", "-q", "-am", "side").returncode == 0
    assert _git(repo, "switch", "-q", "main").returncode == 0
    (repo / "app.py").write_bytes(b"X = 2\n\ndef f():\n    return X\n")
    assert _git(repo, "commit", "-q", "-am", "second").returncode == 0
    assert _git(repo, "merge", "-q", "--no-ff", "side", "-m", "merge side").returncode == 0
    (repo / "notes.txt").write_bytes(b"hello staged\n")
    assert _git(repo, "add", "notes.txt").returncode == 0
    (repo / "app.py").write_bytes(b"X = 3\n\ndef f():\n    return X\n")
    (repo / "new.txt").write_bytes(b"untracked X\n")
    (repo / "scratch.tmp").write_bytes(b"ignored\n")
    return repo


def _ctx(folder: Path) -> ToolContext:
    return ToolContext(workspace=folder, session_id="s", agent_run_id="r", config=None,
                       event_bus=None, engine=None)


# --------------------------------------------------------------------------- #
# 1. every allowed option runs, and the table uses every one
# --------------------------------------------------------------------------- #

RUN_TABLE: list[tuple[str, list[str]]] = [
    # status
    ("status", []), ("status", ["-s"]), ("status", ["-sb"]), ("status", ["--short", "--branch"]),
    ("status", ["--porcelain"]), ("status", ["--porcelain=v2"]), ("status", ["--long"]),
    ("status", ["-uno"]), ("status", ["-u"]), ("status", ["--untracked-files=all"]),
    ("status", ["--ignored"]), ("status", ["--ignored=matching"]), ("status", ["--show-stash"]),
    ("status", ["--ahead-behind"]), ("status", ["--no-ahead-behind"]), ("status", ["-z"]),
    ("status", ["--null"]), ("status", ["--renames"]), ("status", ["--no-renames"]),
    ("status", ["--find-renames=50%"]), ("status", ["-s", "--", "app.py"]),
    # diff
    ("diff", []), ("diff", ["--stat"]), ("diff", ["--stat=80,40"]), ("diff", ["--cached"]),
    ("diff", ["--staged", "--name-only"]), ("diff", ["--name-status", "HEAD~1"]),
    ("diff", ["--merge-base", "HEAD~1"]), ("diff", ["--numstat"]), ("diff", ["--shortstat"]),
    ("diff", ["--raw", "--abbrev=8"]), ("diff", ["--summary"]), ("diff", ["--compact-summary"]),
    ("diff", ["--check"]), ("diff", ["-U5"]), ("diff", ["--unified=1"]), ("diff", ["-p"]),
    ("diff", ["-u"]), ("diff", ["--patch"]), ("diff", ["-s"]), ("diff", ["--no-patch"]),
    ("diff", ["-w"]), ("diff", ["-wb"]), ("diff", ["--ignore-all-space"]),
    ("diff", ["--ignore-space-change"]), ("diff", ["--ignore-space-at-eol"]),
    ("diff", ["--ignore-cr-at-eol"]), ("diff", ["--ignore-blank-lines"]), ("diff", ["-W"]),
    ("diff", ["--function-context"]), ("diff", ["--word-diff"]), ("diff", ["--word-diff=plain"]),
    ("diff", ["--minimal"]), ("diff", ["--patience"]), ("diff", ["--histogram"]),
    ("diff", ["--diff-algorithm=patience"]), ("diff", ["--diff-algorithm", "myers"]),
    ("diff", ["-M"]), ("diff", ["-M50%"]), ("diff", ["--find-renames=40"]), ("diff", ["-C"]),
    ("diff", ["--find-copies"]), ("diff", ["--find-copies-harder"]), ("diff", ["--no-renames"]),
    ("diff", ["--diff-filter=M"]), ("diff", ["--diff-filter", "AM"]), ("diff", ["-S", "X"]),
    ("diff", ["-GX"]), ("diff", ["--pickaxe-regex", "-S", "X"]), ("diff", ["--pickaxe-all", "-SX"]),
    ("diff", ["-R"]), ("diff", ["-a"]), ("diff", ["--text"]), ("diff", ["--full-index"]),
    ("diff", ["--color=never"]), ("diff", ["--no-color"]), ("diff", ["--no-ext-diff", "--no-textconv"]),
    ("diff", ["--output-indicator-new=>"]), ("diff", ["--output-indicator-old", "<"]),
    ("diff", ["--output-indicator-context=."]), ("diff", ["HEAD~1", "HEAD", "--", "app.py"]),
    ("diff", ["--", "app.py"]),
    # log
    ("log", ["-3"]), ("log", ["-n", "2"]), ("log", ["-n2"]), ("log", ["--max-count=2"]),
    ("log", ["--max-count", "2"]), ("log", ["--oneline"]),
    ("log", ["--graph", "--oneline", "--all"]), ("log", ["--format=%h %an %s"]), ("log", ["--pretty"]),
    ("log", ["--pretty=oneline"]), ("log", ["--abbrev-commit", "-1"]), ("log", ["--no-abbrev-commit", "-1"]),
    ("log", ["--date=iso", "-1"]), ("log", ["--date", "short", "-1"]), ("log", ["--relative-date", "-1"]),
    ("log", ["--decorate", "-1"]), ("log", ["--decorate=full", "-1"]), ("log", ["--no-decorate", "-1"]),
    ("log", ["-m", "-1", "--stat"]), ("log", ["--cc", "-1"]),
    ("log", ["--diff-merges=first-parent", "-p", "-1"]), ("log", ["--diff-merges", "separate", "-1"]),
    ("log", ["--no-diff-merges", "-1"]), ("log", ["--first-parent"]), ("log", ["--since=1.year.ago"]),
    ("log", ["--since", "2000-01-01"]), ("log", ["--after=2000-01-01"]), ("log", ["--until=now"]),
    ("log", ["--before", "now"]), ("log", ["--author=t"]), ("log", ["--author", "t"]),
    ("log", ["--grep=first"]), ("log", ["--grep", "merge", "-i"]),
    ("log", ["--regexp-ignore-case", "--grep=FIRST"]), ("log", ["-E", "--grep=fir.t"]),
    ("log", ["--extended-regexp", "--grep=fir.t"]), ("log", ["-F", "--grep=first"]),
    ("log", ["--fixed-strings", "--grep=first"]), ("log", ["--all-match", "--grep=s", "--grep=e"]),
    ("log", ["--invert-grep", "--grep=zzz"]), ("log", ["--all"]), ("log", ["--merges"]),
    ("log", ["--no-merges"]), ("log", ["--reverse"]), ("log", ["--date-order"]), ("log", ["--topo-order"]),
    ("log", ["--follow", "--", "app.py"]), ("log", ["--parents", "-1"]), ("log", ["-p", "-1"]),
    ("log", ["-u", "-1"]), ("log", ["--patch", "-1"]), ("log", ["-s", "-1"]), ("log", ["--no-patch", "-1"]),
    ("log", ["-pU1", "-1"]), ("log", ["-pw", "-1"]), ("log", ["-S", "X"]), ("log", ["-SX"]),
    ("log", ["-pSX"]), ("log", ["-GX"]), ("log", ["--name-only", "-1"]), ("log", ["--name-status", "-1"]),
    ("log", ["--stat", "-1"]), ("log", ["-M", "--stat"]), ("log", ["--", "app.py"]),
    ("log", ["--", "--stat"]),  # after --, a path (named like an option): git finds none
    # show
    ("show", []), ("show", ["HEAD"]), ("show", ["--stat"]), ("show", ["--name-only"]), ("show", ["-s"]),
    ("show", ["--no-patch"]), ("show", ["--format=%H", "-s"]), ("show", ["--oneline", "-s"]),
    ("show", ["--pretty=fuller", "-s"]), ("show", ["-U1"]), ("show", ["HEAD:app.py"]), ("show", ["v1.0"]),
    ("show", ["-m", "HEAD"]), ("show", ["--cc", "HEAD"]), ("show", ["--diff-merges=on", "HEAD"]),
    ("show", ["--first-parent", "HEAD"]), ("show", ["--date=iso", "-s"]), ("show", ["--decorate", "-s"]),
    ("show", ["--word-diff", "HEAD~1"]),
    # blame
    ("blame", ["app.py"]), ("blame", ["-L", "1,2", "app.py"]), ("blame", ["-L1,2", "app.py"]),
    ("blame", ["-L", ",2", "app.py"]), ("blame", ["-L", "1,+1", "app.py"]), ("blame", ["-w", "app.py"]),
    ("blame", ["-M", "app.py"]), ("blame", ["-M50", "app.py"]), ("blame", ["-C", "app.py"]),
    ("blame", ["-C", "-C", "app.py"]), ("blame", ["-e", "app.py"]), ("blame", ["--show-email", "app.py"]),
    ("blame", ["--date", "iso", "app.py"]), ("blame", ["--date=short", "app.py"]), ("blame", ["-s", "app.py"]),
    ("blame", ["-l", "app.py"]), ("blame", ["-t", "app.py"]), ("blame", ["-f", "app.py"]),
    ("blame", ["--show-name", "app.py"]), ("blame", ["-n", "app.py"]), ("blame", ["--show-number", "app.py"]),
    ("blame", ["-p", "app.py"]), ("blame", ["--porcelain", "app.py"]), ("blame", ["--line-porcelain", "app.py"]),
    ("blame", ["--root", "app.py"]), ("blame", ["--minimal", "app.py"]), ("blame", ["-b", "app.py"]),
    ("blame", ["--abbrev=10", "app.py"]), ("blame", ["--show-stats", "app.py"]),
    ("blame", ["--first-parent", "app.py"]), ("blame", ["--ignore-rev", "HEAD~2", "app.py"]),
    ("blame", ["-sw", "app.py"]), ("blame", ["HEAD", "--", "app.py"]),
    # ls-files
    ("ls-files", []), ("ls-files", ["-c"]), ("ls-files", ["--cached"]), ("ls-files", ["-m"]),
    ("ls-files", ["--modified"]), ("ls-files", ["-o"]), ("ls-files", ["--others", "--exclude-standard"]),
    ("ls-files", ["-d"]), ("ls-files", ["--deleted"]), ("ls-files", ["-s"]), ("ls-files", ["--stage"]),
    ("ls-files", ["-u"]), ("ls-files", ["--unmerged"]), ("ls-files", ["-i", "--exclude-standard", "-o"]),
    ("ls-files", ["--ignored", "-o", "-x", "*.tmp"]), ("ls-files", ["--exclude=*.tmp", "-o"]),
    ("ls-files", ["-z"]), ("ls-files", ["--directory", "-o"]),
    ("ls-files", ["--no-empty-directory", "-o", "--directory"]), ("ls-files", ["--eol"]),
    ("ls-files", ["--full-name"]), ("ls-files", ["--error-unmatch", "app.py"]), ("ls-files", ["--deduplicate"]),
    ("ls-files", ["--abbrev", "-s"]), ("ls-files", ["--abbrev=8", "-s"]),
    ("ls-files", ["--format=%(objectname) %(path)"]), ("ls-files", ["-cmo"]), ("ls-files", ["--", "app.py"]),
    # rev-parse
    ("rev-parse", ["HEAD"]), ("rev-parse", ["--abbrev-ref", "HEAD"]), ("rev-parse", ["--abbrev-ref=strict", "HEAD"]),
    ("rev-parse", ["--short", "HEAD"]), ("rev-parse", ["--short=8", "HEAD"]), ("rev-parse", ["--verify", "HEAD"]),
    ("rev-parse", ["-q", "--verify", "HEAD"]), ("rev-parse", ["--quiet", "--verify", "HEAD"]),
    ("rev-parse", ["--symbolic", "HEAD"]), ("rev-parse", ["--symbolic-full-name", "HEAD"]),
    ("rev-parse", ["--show-toplevel"]), ("rev-parse", ["--show-prefix"]), ("rev-parse", ["--show-cdup"]),
    ("rev-parse", ["--git-dir"]), ("rev-parse", ["--absolute-git-dir"]), ("rev-parse", ["--git-common-dir"]),
    ("rev-parse", ["--is-inside-work-tree"]), ("rev-parse", ["--is-inside-git-dir"]),
    ("rev-parse", ["--is-bare-repository"]), ("rev-parse", ["--is-shallow-repository"]),
    # describe
    ("describe", ["--tags"]), ("describe", ["--always"]), ("describe", ["--all"]),
    ("describe", ["--long", "--tags"]), ("describe", ["--abbrev=4", "--tags"]),
    ("describe", ["--first-parent", "--tags"]), ("describe", ["--exact-match", "v1.0"]),
    ("describe", ["--contains", "--tags", "v1.0"]), ("describe", ["--candidates=3", "--tags"]),
    ("describe", ["--candidates", "3", "--tags"]), ("describe", ["--match", "v*"]),
    ("describe", ["--match=v*"]), ("describe", ["--exclude", "x*", "--tags"]), ("describe", ["--always", "HEAD"]),
    # shortlog
    ("shortlog", ["HEAD"]), ("shortlog", ["-s", "HEAD"]), ("shortlog", ["-sn", "HEAD"]),
    ("shortlog", ["-sne", "HEAD"]), ("shortlog", ["--summary", "--numbered", "--email", "HEAD"]),
    ("shortlog", ["--max-count=2", "HEAD"]), ("shortlog", ["--since=2000-01-01", "HEAD"]),
    ("shortlog", ["--after", "2000-01-01", "HEAD"]), ("shortlog", ["--until=now", "HEAD"]),
    ("shortlog", ["--before=now", "HEAD"]), ("shortlog", ["--author", "t", "HEAD"]),
    ("shortlog", ["--grep=first", "HEAD"]), ("shortlog", ["--all"]), ("shortlog", ["--merges", "HEAD"]),
    ("shortlog", ["--no-merges", "HEAD"]), ("shortlog", ["--first-parent", "HEAD"]),
    ("shortlog", ["-2", "HEAD"]),
    # grep
    ("grep", ["X"]), ("grep", ["-n", "X"]), ("grep", ["--line-number", "X"]), ("grep", ["-i", "x"]),
    ("grep", ["--ignore-case", "x"]), ("grep", ["-w", "X"]), ("grep", ["--word-regexp", "X"]),
    ("grep", ["-l", "X"]), ("grep", ["--files-with-matches", "X"]), ("grep", ["--name-only", "X"]),
    ("grep", ["-L", "zzz"]), ("grep", ["--files-without-match", "zzz"]), ("grep", ["-c", "X"]),
    ("grep", ["--count", "X"]), ("grep", ["-e", "X"]), ("grep", ["-eX"]),
    ("grep", ["-e", "X", "--or", "-e", "Y"]), ("grep", ["-E", "X+"]), ("grep", ["--extended-regexp", "X+"]),
    ("grep", ["-F", "X"]), ("grep", ["--fixed-strings", "X"]), ("grep", ["-P", "X"]),
    ("grep", ["--perl-regexp", "X"]), ("grep", ["-G", "X"]), ("grep", ["--basic-regexp", "X"]),
    ("grep", ["--cached", "X"]), ("grep", ["-v", "zzz"]), ("grep", ["--invert-match", "zzz"]),
    ("grep", ["-o", "X"]), ("grep", ["--only-matching", "X"]), ("grep", ["-I", "X"]), ("grep", ["-h", "X"]),
    ("grep", ["-H", "X"]), ("grep", ["--break", "--heading", "X"]), ("grep", ["-A", "1", "X"]),
    ("grep", ["-A1", "X"]), ("grep", ["--after-context=1", "X"]), ("grep", ["--after-context", "1", "X"]),
    ("grep", ["-B1", "X"]), ("grep", ["--before-context=1", "X"]), ("grep", ["-C", "1", "X"]),
    ("grep", ["--context=1", "X"]), ("grep", ["-2", "X"]), ("grep", ["-m", "1", "X"]),
    ("grep", ["--max-count=1", "X"]), ("grep", ["--max-depth", "1", "X"]),
    ("grep", ["--all-match", "-e", "X", "--and", "-e", "="]), ("grep", ["--not", "-e", "zzz"]),
    ("grep", ["-p", "X"]), ("grep", ["--show-function", "X"]), ("grep", ["-W", "X"]),
    ("grep", ["--function-context", "X"]), ("grep", ["--column", "X"]), ("grep", ["--full-name", "X"]),
    ("grep", ["-a", "X"]), ("grep", ["--text", "X"]), ("grep", ["-z", "X"]), ("grep", ["--null", "X"]),
    ("grep", ["--no-color", "X"]), ("grep", ["-niw", "X"]), ("grep", ["-nA1", "X"]), ("grep", ["X", "HEAD"]),
    ("grep", ["X", "--", "app.py"]),
    # branch (listings)
    ("branch", []), ("branch", ["--list"]), ("branch", ["-l"]), ("branch", ["-a"]), ("branch", ["--all"]),
    ("branch", ["-r"]), ("branch", ["--remotes"]), ("branch", ["-v"]), ("branch", ["-vv"]),
    ("branch", ["--verbose"]), ("branch", ["--show-current"]), ("branch", ["--contains"]),
    ("branch", ["--contains", "HEAD"]), ("branch", ["--no-contains", "HEAD~1"]), ("branch", ["--merged"]),
    ("branch", ["--merged", "main", "s*"]), ("branch", ["--no-merged", "HEAD"]),
    ("branch", ["--points-at", "HEAD"]), ("branch", ["--sort=-committerdate"]),
    ("branch", ["--format=%(refname:short)"]), ("branch", ["--format", "%(refname)"]),
    ("branch", ["-i", "-l", "M*"]), ("branch", ["--ignore-case", "--list", "M*"]), ("branch", ["--omit-empty"]),
    ("branch", ["--color=never"]), ("branch", ["--no-color"]), ("branch", ["--abbrev=8", "-v"]),
    ("branch", ["--no-abbrev", "-v"]), ("branch", ["--column=never"]), ("branch", ["--no-column"]),
    ("branch", ["--list", "--", "s*"]),
    # tag (listings)
    ("tag", []), ("tag", ["-l"]), ("tag", ["--list", "v*"]), ("tag", ["-n"]), ("tag", ["-n2"]),
    ("tag", ["-l", "-n3"]), ("tag", ["--contains"]), ("tag", ["--contains", "HEAD~2"]),
    ("tag", ["--no-contains", "HEAD"]), ("tag", ["--merged", "HEAD"]), ("tag", ["--no-merged"]),
    ("tag", ["--points-at", "HEAD~2"]), ("tag", ["--points-at"]), ("tag", ["--sort=-creatordate"]),
    ("tag", ["--sort", "refname"]), ("tag", ["--format=%(refname:short)"]), ("tag", ["-i", "-l", "V*"]),
    ("tag", ["--ignore-case", "--list", "V*"]), ("tag", ["--omit-empty"]), ("tag", ["--color=never"]),
    ("tag", ["--no-color"]), ("tag", ["--column=never"]), ("tag", ["--no-column"]),
]


#: A value of each kind that git takes (a few options want their own).
_SAMPLES = {"int": "3", "score": "50", "stat": "80", "line_range": "1,2", "diff_filter": "M",
            "column": "never", "porcelain": "v2", "untracked": "no", "ignored": "matching",
            "word_diff": "plain", "color": "never", "decorate": "short", "algorithm": "patience",
            "abbrev_ref": "strict", "diff_merges": "separate", "format": "%h", "text": "X", "rev": "HEAD"}
_SAMPLE_FOR = {"--date": "iso"}


def _sample(command: str, spelling: str) -> list[str]:
    """One word using ``spelling`` (its value glued, the form git always takes)."""
    arity, kind = G.OPTIONS[command][spelling]
    if arity in (G.FLAG, G.OPTIONAL, G.LASTARG):
        return [spelling]
    value = _SAMPLE_FOR.get(spelling, _SAMPLES[kind])
    return [f"{spelling}={value}" if spelling.startswith("--") else f"{spelling}{value}"]


def _spellings(rows) -> list[str]:
    return sorted({s for spellings, _a, _k in rows for s in spellings.split()})


# log and show take every diff option, and show every way a commit is shown:
# one generated call each (the hand-written ones above cover the forms).
RUN_TABLE += [("log", [*_sample("log", s), "-1"]) for s in _spellings(G._DIFF_ROWS)]
RUN_TABLE += [("show", [*_sample("show", s), "HEAD~1"]) for s in _spellings(G._DIFF_ROWS)]
RUN_TABLE += [("show", [*_sample("show", s), "-s", "HEAD"]) for s in _spellings(G._SHOW_ROWS)]
RUN_TABLE += [("log", [*_sample("log", s), "-1"]) for s in _spellings(G._SHOW_ROWS)]

@pytest.mark.parametrize("command,args", RUN_TABLE, ids=[f"{c} {' '.join(a)}" for c, a in RUN_TABLE])
async def test_every_allowed_call_runs_against_real_git(repo, command, args):
    assert G.refusal(command, args) == "", (command, args)
    res = await G.GitReadTool().execute({"command": command, "args": args}, _ctx(repo))
    assert res.ok, (command, args, res.error)


def test_the_table_uses_every_allowed_option():
    """A new option on a list needs a call in :data:`RUN_TABLE` (that it
    runs is the proof it is a real option of that command)."""
    used: dict[str, set[str]] = {c: set() for c in G.OPTIONS}
    for command, args in RUN_TABLE:
        problem, names = G._check(command, args)
        assert problem == "", (command, args, problem)
        used[command].update(names)
    for command, table in G.OPTIONS.items():
        missing = sorted(set(table) - used[command])
        assert not missing, (command, missing)


def test_the_run_table_reads_what_it_should(repo):
    """The table's calls are not vacuous: a few answers read as git's own."""
    import asyncio

    async def out(command, args):
        res = await G.GitReadTool().execute({"command": command, "args": args}, _ctx(repo))
        assert res.ok, res.error
        return res.output

    async def go():
        assert "app.py" in await out("diff", ["--name-only"])
        assert "notes.txt" in await out("diff", ["--cached", "--name-only"])
        assert "merge side" in await out("log", ["--oneline", "-1"])
        assert "v1.0" in await out("tag", ["-l", "v*"])
        assert "side" in await out("branch", ["--merged", "main", "s*"])
        assert "main" == (await out("rev-parse", ["--abbrev-ref", "HEAD"])).strip()

    asyncio.run(go())


# --------------------------------------------------------------------------- #
# 2. every allowed long spelling is one git knows as ITSELF
# --------------------------------------------------------------------------- #


def _known(repo: Path, command: str) -> set[str]:
    """git's own list of a command's long options (parse-options), the
    ``=`` dropped. log / show add diff's (they take them)."""
    names: set[str] = set()
    for cmd in ({command, "diff"} if command in ("log", "show") else {command}):
        got = _git(repo, cmd, "--git-completion-helper-all")
        names.update(w.rstrip("=") for w in got.stdout.split() if w.startswith("--") and w != "--")
    return names


@pytest.mark.parametrize("command", sorted(G.OPTIONS))
def test_every_allowed_long_option_is_one_git_knows_as_itself(repo, command):
    """git takes an unambiguous SHORTENING of a long option (parse-options
    commands; the control below proves it). An allowed spelling that is
    NOT one of git's own names would be read as a shortening of whichever
    option it starts — so each is either in git's list exactly, or (git's
    revision options, read by hand: ``--since``, ``--grep``…) a prefix of
    none of them. rev-parse reads every option by hand; it echoes one it
    does not know, so each must not come back as itself."""
    longs = sorted(s for s in G.OPTIONS[command] if s.startswith("--"))
    if command == "rev-parse":
        for spelling in longs:
            got = _git(repo, "rev-parse", spelling, "HEAD")
            assert spelling not in got.stdout, (spelling, got.stdout)
        # The control: an option rev-parse does not know comes back as itself.
        assert "--no-such-option" in _git(repo, "rev-parse", "--no-such-option").stdout
        return
    known = _known(repo, command)
    assert len(known) > 5, (command, known)
    for spelling in longs:
        if spelling in known:
            continue
        shortening_of = sorted(n for n in known if n.startswith(spelling))
        assert not shortening_of, (command, spelling, shortening_of)


def test_the_control_git_itself_takes_a_shortening(repo):
    """The reason shortenings are refused: git status reads ``--bra`` as
    ``--branch`` (and grep reads ``--line-n`` as ``--line-number``)."""
    assert "## main" in _git(repo, "status", "--short", "--bra").stdout
    assert _git(repo, "--no-pager", "grep", "--line-n", "X").returncode == 0
    for command, word, full in (("status", "--bra", "--branch"), ("grep", "--line-n", "--line-number"),
                                ("diff", "--name-on", "--name-only"), ("log", "--onel", "--oneline"),
                                ("log", "--max-c=2", "--max-count")):
        said = G.refusal(command, [word] if command != "grep" else [word, "X"])
        assert said and full in said, (command, word, said)


# --------------------------------------------------------------------------- #
# 3. what is refused
# --------------------------------------------------------------------------- #

REFUSED: list[tuple[str, list[str]]] = [
    # Options git has that are not on the list (each harmless or not — the
    # list decides, not a guess about git's surface).
    ("diff", ["--relative"]), ("diff", ["--binary"]), ("diff", ["--color-moved"]), ("diff", ["--src-prefix=a/"]),
    ("diff", ["--dirstat"]), ("diff", ["-X"]), ("diff", ["-B"]), ("diff", ["-D"]), ("diff", ["-l5"]),
    ("diff", ["--exit-code"]), ("diff", ["--quiet"]), ("diff", ["--rotate-to=x"]), ("diff", ["--find-object=x"]),
    ("log", ["-L", "1,2:app.py"]), ("log", ["--walk-reflogs"]), ("log", ["-g"]), ("log", ["--notes"]),
    ("log", ["--use-mailmap"]), ("log", ["--stdin"]), ("log", ["--end-of-options", "HEAD"]),
    ("log", ["--branches"]), ("log", ["--perl-regexp", "--grep=x"]), ("log", ["-c"]),
    ("show", ["--quiet"]), ("show", ["-n", "2"]), ("blame", ["--incremental", "app.py"]),
    ("blame", ["--reverse", "HEAD", "app.py"]), ("blame", ["-c", "app.py"]),
    ("ls-files", ["-k"]), ("ls-files", ["-t"]), ("ls-files", ["--with-tree=HEAD"]), ("ls-files", ["--sparse"]),
    ("rev-parse", ["--git-path", "x"]), ("rev-parse", ["--resolve-git-dir", "x"]), ("rev-parse", ["--sq-quote", "x"]),
    ("rev-parse", ["--parseopt"]), ("rev-parse", ["--local-env-vars"]), ("rev-parse", ["--since=x"]),
    ("describe", ["--debug"]), ("shortlog", ["-c", "HEAD"]), ("shortlog", ["--committer", "HEAD"]),
    ("shortlog", ["--group=author", "HEAD"]), ("shortlog", ["-w", "HEAD"]), ("grep", ["-q", "X"]),
    ("grep", ["--threads=2", "X"]), ("grep", ["--exclude-standard", "X"]), ("grep", ["--color", "X"]),
    ("grep", ["-r", "X"]), ("status", ["--column"]), ("branch", ["-q"]), ("tag", ["-v", "v1.0"]),
    # A made-up option of a future git.
    *[(c, ["--frobnicate"]) for c in G.ALLOWED], *[(c, ["--frobnicate=1"]) for c in G.ALLOWED],
    *[(c, ["-Y"]) for c in G.ALLOWED],
    # A bare "-".
    ("log", ["-"]), ("diff", ["-"]), ("grep", ["-", "X"]),
    # The dangerous ones (their own sentence).
    ("diff", ["--output=x"]), ("diff", ["--ext-diff"]), ("log", ["--textconv"]), ("diff", ["--no-index", "a", "b"]),
    ("log", ["--show-signature"]), ("grep", ["--open-files-in-pager", "X"]), ("grep", ["--untracked", "X"]),
    ("grep", ["-f", "p.txt"]), ("blame", ["--contents", "app.py", "app.py"]), ("blame", ["-S", "revs", "app.py"]),
    ("blame", ["--ignore-revs-file=x", "app.py"]), ("ls-files", ["--exclude-from=x"]), ("ls-files", ["-X", "x"]),
    ("ls-files", ["--recurse-submodules"]), ("ls-files", ["--exclude-per-directory=x"]),
    # A cluster with ONE bad letter.
    ("status", ["-sbv"]), ("status", ["-sq"]), ("grep", ["-niq", "X"]), ("grep", ["-nf", "x"]),
    ("diff", ["-pwO"]), ("diff", ["-wX"]), ("blame", ["-swS", "x", "app.py"]), ("ls-files", ["-cmX"]),
    ("ls-files", ["-ck"]), ("log", ["-pg"]), ("branch", ["-ad"]), ("branch", ["-lD"]), ("tag", ["-ld"]),
    ("rev-parse", ["-qq"]), ("rev-parse", ["-q1"]),
    # A value of the wrong kind.
    ("status", ["--porcelain=v3"]), ("status", ["-uxyz"]), ("status", ["--untracked-files=some"]),
    ("status", ["--ignored=all"]), ("diff", ["--diff-filter=Q"]), ("diff", ["-Uabc"]), ("diff", ["--unified=-1"]),
    ("diff", ["--stat=wide"]), ("diff", ["--word-diff=fancy"]), ("diff", ["--diff-algorithm=quick"]),
    ("diff", ["-Mabc"]), ("diff", ["--color=rainbow"]), ("log", ["-n", "x"]), ("log", ["-nx"]),
    ("log", ["--max-count=-1"]), ("log", ["--decorate=all"]), ("log", ["--diff-merges=remerge"]),
    ("log", ["--diff-merges", "r"]), ("log", ["--diff-merges=sideways"]), ("blame", ["-L", "x,y", "app.py"]),
    ("blame", ["-L", ":f", "app.py"]), ("blame", ["-L", "../app.py", "app.py"]), ("blame", ["-Mx", "app.py"]),
    ("grep", ["-A", "x", "X"]), ("grep", ["-Ax", "X"]), ("grep", ["--max-depth=-1", "X"]),
    ("tag", ["-nx"]), ("rev-parse", ["--abbrev-ref=loud", "HEAD"]), ("rev-parse", ["--short=x", "HEAD"]),
    ("describe", ["--candidates=x"]), ("branch", ["--contains", "../x"]), ("branch", ["--points-at=/etc/x"]),
    ("branch", ["--column=1;x"]),
    # A format that shows a signature (both forms).
    ("log", ["--format=%G?"]), ("log", ["--pretty=format:%GS"]), ("show", ["--format=%GK", "-s"]),
    ("branch", ["--format=%(signature)"]), ("tag", ["--format", "%(signature:key)"]),
    ("ls-files", ["--format", "%G?"]),
    # The forms.
    ("diff", ["--name-only=yes"]), ("status", ["--short=1"]), ("log", ["--format", "%h"]),
    ("log", ["--author", "-x"]), ("grep", ["-e", "-x"]), ("grep", ["-A", "-1", "X"]), ("log", ["--author"]),
    ("grep", ["-e"]), ("blame", ["-L"]), ("describe", ["--match"]), ("branch", ["--sort", "-refname"]),
    ("branch", ["--contains", "-a"]),
    # Shortenings of long options git would take.
    ("status", ["--bra"]), ("status", ["--sho"]), ("diff", ["--name-on"]), ("diff", ["--cach"]),
    ("diff", ["--stat-w=50"]), ("log", ["--onel"]), ("log", ["--max-c=1"]), ("log", ["--form=%h"]),
    ("log", ["--auth=t"]), ("grep", ["--line-n", "X"]), ("grep", ["--ignore-c", "X"]),
    ("blame", ["--porc", "app.py"]), ("ls-files", ["--oth"]), ("rev-parse", ["--show-top"]),
    ("describe", ["--alw"]), ("shortlog", ["--summ", "HEAD"]), ("branch", ["--li"]), ("tag", ["--li"]),
    ("tag", ["--point=HEAD"]),
]


@pytest.mark.parametrize("command,args", REFUSED, ids=[f"{c} {' '.join(a)}" for c, a in REFUSED])
def test_anything_off_the_list_is_refused_in_one_plain_sentence(command, args):
    said = G.refusal(command, args)
    assert said, (command, args)
    assert said.startswith(("git_read", "git ")) and "Traceback" not in said
    assert len(said) < 260 and said.count("\n") == 0, (len(said), said)


@pytest.mark.parametrize("command", sorted(G.OPTIONS))
async def test_a_refused_option_runs_nothing(command, repo, monkeypatch):
    ran: list = []
    monkeypatch.setattr(G, "run", lambda *a, **k: ran.append(a))
    res = await G.GitReadTool().execute({"command": command, "args": ["--frobnicate"]}, _ctx(repo))
    assert not res.ok and res.data["refused"] is True and ran == []


def test_the_refusal_names_the_option_and_options_the_command_takes():
    said = G.refusal("log", ["--frobnicate"])
    assert "--frobnicate" in said and "git log" in said
    assert "--oneline" in said and "--stat" in said and "spelled in full" in said
    said = G.refusal("grep", ["-q", "X"])
    assert "-q" in said and "-n" in said and "--cached" in said
    said = G.refusal("tag", ["-v", "v1"])
    assert "-v" in said and "--list" in said and "only to LIST" in said
    # A shortening that matches one option names its full spelling.
    assert "--name-only" in G.refusal("diff", ["--name-on"])
    # A cluster names its bad LETTER.
    assert "-q" in G.refusal("status", ["-sq"]) and "-v" in G.refusal("status", ["-sbv"])
    # A word of nonsense length is not echoed whole.
    long = "--" + "x" * 400
    assert len(G.refusal("diff", [long])) < 200


def test_every_named_option_is_on_its_list_and_no_dangerous_one_is():
    for command, names in G._COMMON.items():
        for piece in names.split(", "):
            for spelling in piece.split("/"):
                assert spelling in G.OPTIONS[command], (command, spelling)
    for command, table in G.OPTIONS.items():
        assert not set(table) & set(G._DANGER_ALL), command
        assert not set(table) & set(G._DANGER_FOR.get(command, {})), command
        for spelling in table:
            assert spelling.startswith("-") and " " not in spelling and "=" not in spelling, spelling
            assert spelling.startswith("--") or len(spelling) == 2, spelling


def test_a_glued_only_option_leaves_the_next_word_to_be_read_as_git_reads_it(repo):
    """``-U 5``: git reads "5" as a revision of its own (measured: "ambiguous
    argument '5'"); so does the checker — the word is path-checked, never
    taken as -U's value."""
    assert G.refusal("diff", ["-U", "../x"])  # a path outside, as git would read it
    assert G.refusal("diff", ["--stat", "/etc/passwd"])
    assert G.refusal("diff", ["-U", "5"]) == ""
    # ... and never checked as its value: a revision after --stat, a path
    # after -u, are words of their own.
    assert G.refusal("diff", ["--stat", "HEAD~1"]) == ""
    assert G.refusal("status", ["-u", "app.py"]) == ""
    assert "ambiguous argument '5'" in _git(repo, "--no-pager", "diff", "-U", "5").stderr
    # A REQUIRED value takes the next word whatever it looks like (git does
    # too): "../x" is grep's PATTERN here, never a path.
    assert G.refusal("grep", ["-e", "../x"]) == ""
    assert G.refusal("log", ["--grep", "C:x"]) == ""


def test_double_dash_ends_the_options():
    # After "--" every word is a path (path-checked), whatever it looks like.
    assert G.refusal("log", ["--", "--output=x"]) == ""
    assert G.refusal("log", ["--", "-O"]) == ""
    assert G.refusal("diff", ["--", "../x"])
    # Before it, the same word is an option.
    assert G.refusal("log", ["--output=x", "--"])


def test_counts_only_where_git_reads_a_count():
    for command in ("log", "shortlog", "grep"):
        assert G.refusal(command, ["-3"] if command != "grep" else ["-3", "X"]) == "", command
    for command in ("status", "diff", "show", "blame", "ls-files", "rev-parse", "describe", "branch", "tag"):
        assert G.refusal(command, ["-3"]), command


def test_listing_rules_for_branch_and_tag_still_hold():
    assert G.refusal("branch", ["new-name"])
    assert G.refusal("branch", ["-v", "new-name"])  # -v is no listing
    assert G.refusal("branch", ["--list", "new-name"]) == ""
    assert G.refusal("branch", ["--contains", "HEAD", "x*"]) == ""  # HEAD is the commit
    assert G.refusal("tag", ["v9"])
    assert G.refusal("tag", ["-n", "v*"]) == ""
    assert G.refusal("tag", ["--points-at", "HEAD", "v*"]) == ""


async def test_a_protected_folder_is_still_refused_for_an_allowed_call_in_a_subfolder(tmp_path, monkeypatch):
    """v1.421.0's place rule, through an option that IS on the list (the old
    pin's ``grep --untracked`` is now refused as an option before the place
    is asked): a subfolder of a repository whose top holds Iron Jarvis's
    keys is refused by the place, naming it."""
    from iron_jarvis.core import fs_policy

    repo = tmp_path / "home"
    repo.mkdir()
    assert _git(repo, "init", "-q").returncode == 0
    (repo / "app.py").write_bytes(b"X = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c")
    secrets = repo / ".ironjarvis" / "secrets"
    secrets.mkdir(parents=True)
    monkeypatch.setattr(fs_policy, "_PROTECTED_ROOTS", set(fs_policy._PROTECTED_ROOTS))
    fs_policy.register_protected_root(secrets)
    sub = repo / "proj"
    sub.mkdir()
    res = await G.GitReadTool().execute({"command": "grep", "args": ["-n", "X", "--", ":/"]}, _ctx(sub))
    assert not res.ok and res.error == G.PROTECTED_REFUSAL
