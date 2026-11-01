"""v1.421.0 (Wave 1, A4) — the team's reviewer can VERIFY: read-only git.

The live review said "This session only has read and search": the finish
check's reviewer and the CI-fix reviewer held read_file / glob / grep /
list_files only. The owner's decision: read-only commands, never a test run
(tests write caches; running them stays the finish check's job). read_file &
co. already cover cat / ls / find / grep, so ONE tool was added: ``git_read``
(``harness/git_read``).

What these pins hold:

1. it runs only status / diff / log / show / blame / ls-files / rev-parse /
   describe / shortlog / grep and branch / tag LISTINGS, and refuses in plain
   words anything that writes a file, runs a program, reads outside the
   folder or changes a branch or tag — shortened long options too (git takes
   ``--open-files`` for ``--open-files-in-pager``; measured);
2. a ``.git/config`` a worker wrote cannot make it run a program: a filter
   driver (``.gitattributes`` + ``filter.<x>.clean``), ``core.fsmonitor``,
   ``diff.external`` and a textconv driver — each RUNS on plain git here
   (the control) and never through ``git_read``;
3. its output is masked and capped, saying so; a folder that is no repository
   is one sentence;
4. it is registered at the allow tier, READONLY (replay-safe), its output
   fenced as untrusted; armed for a review-only helper ALWAYS (the finish
   review's and the CI reviewer's) and for nobody else — through the real
   stream lane, the reviewer's tools hold it and the lead's and the worker's
   do not.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from iron_jarvis.agents.types import get_agent_definition
from iron_jarvis.core.models import AgentType
from iron_jarvis.harness import git_read as G
from iron_jarvis.harness import helpers as H
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Reversibility, ToolContext, replay_safe
from tests.test_session_team_v1403 import (  # noqa: F401 — `world` is a fixture
    answer_note,
    body,
    lead_calls,
    members,
    reviewer_calls,
    stream,
    world,
    worker_calls,
    write_x,
)

# --------------------------------------------------------------------------- #
# 1. what it runs, what it refuses
# --------------------------------------------------------------------------- #

ALLOWED_CALLS = [
    ("status", ["--short"]),
    ("diff", ["--stat", "HEAD~1..HEAD"]),
    ("diff", ["--text", "--", "app.py"]),
    ("diff", ["--cached", "--name-only"]),
    ("log", ["-n", "3", "--oneline"]),
    ("log", ["--format=%h %s", "-p", "--", "src/app.py"]),
    ("log", ["--output-indicator-new=+", "-1"]),
    ("show", ["HEAD:app.py"]),
    ("show", ["--stat", "HEAD"]),
    ("blame", ["-L", "1,5", "app.py"]),
    ("ls-files", ["--others", "--exclude-standard"]),
    ("rev-parse", ["--abbrev-ref", "HEAD"]),
    # (review, moved on purpose: ``--dirty`` rewrites git's index —
    # tests/test_git_read_review_v14210.py.)
    ("describe", ["--always", "--tags"]),
    ("shortlog", ["-s", "-n", "HEAD"]),
    ("grep", ["-n", "TODO", "--", "src"]),
    ("grep", ["--only-matching", "x"]),
    ("branch", []),
    ("branch", ["--list", "ij/*"]),
    ("branch", ["-a", "-v"]),
    ("branch", ["--contains", "HEAD"]),
    ("branch", ["--merged", "main", "ij/*"]),
    ("branch", ["--show-current"]),
    ("tag", ["-l", "v1.*"]),
    ("tag", ["-n3"]),
    ("tag", ["--sort=-creatordate", "--list"]),
]


@pytest.mark.parametrize("command,args", ALLOWED_CALLS)
def test_read_only_calls_are_allowed(command, args):
    assert G.refusal(command, args) == ""


REFUSED_CALLS = [
    # Not a read: every other git command.
    ("commit", ["-m", "x"]), ("push", []), ("checkout", ["main"]), ("reset", ["--hard"]),
    ("config", ["core.fsmonitor", "x"]), ("fetch", []), ("stash", []), ("difftool", []),
    ("", []),
    # A setting (can run a program).
    ("log", ["-c", "core.pager=evil"]), ("diff", ["-cfoo"]),
    # A file written.
    ("log", ["--output=x.txt"]), ("log", ["--output", "x.txt"]), ("diff", ["-o", "x"]),
    ("diff", ["--outp=x.txt"]),
    # A program the folder names.
    ("diff", ["--ext-diff"]), ("diff", ["--ext"]), ("log", ["-p", "--textconv"]), ("show", ["--textc"]),
    ("diff", ["-Oorder.txt"]), ("grep", ["-O", "x"]), ("grep", ["--open-files-in-pager", "x"]),
    ("grep", ["--open-files", "x"]),
    ("grep", ["--recurse-submodules", "x"]),
    # A read outside the repository.
    ("diff", ["--no-index", "a", "b"]), ("diff", ["--no-ind", "a", "b"]),
    # A signature checked (runs gpg, a program the config names).
    ("log", ["--show-signature"]), ("log", ["--format=%h %G?"]), ("log", ["--pretty=%GS"]),
    ("tag", ["--list", "--format", "%(signature)"]), ("tag", ["-v", "v1"]),
    # A path outside the folder.
    ("diff", ["/etc/passwd", "a"]), ("diff", ["C:\\Users\\x\\secret.txt", "a"]),
    ("diff", ["C:secret.txt", "a"]), ("diff", ["\\\\host\\share\\x", "a"]),
    ("diff", ["//host/share/x"]), ("show", ["HEAD:../x"]), ("log", ["--", "src/../../x"]),
    ("diff", [".."]), ("grep", ["-f", "../patterns.txt"]), ("ls-files", ["--exclude-from=../x"]),
    # branch / tag: anything that is not a listing.
    ("branch", ["new-branch"]), ("branch", ["-d", "main"]), ("branch", ["-D", "x"]),
    ("branch", ["-m", "a", "b"]), ("branch", ["-c", "a", "b"]), ("branch", ["--delete", "x"]),
    ("branch", ["--set-upstream-to=origin/x"]), ("branch", ["-f", "main", "HEAD~3"]),
    ("tag", ["v9"]), ("tag", ["-a", "v9", "-m", "x"]), ("tag", ["-d", "v1"]), ("tag", ["-f", "v1"]),
]


@pytest.mark.parametrize("command,args", REFUSED_CALLS)
def test_anything_else_is_refused_in_plain_words(command, args):
    said = G.refusal(command, args)
    assert said, (command, args)
    assert "git_read" in said or "git " in said
    assert "Traceback" not in said and len(said) < 260


def test_the_command_may_be_written_as_a_git_line():
    assert G._split_command("git diff --stat", ["HEAD"]) == ("diff", ["--stat", "HEAD"])
    assert G._split_command("LOG", []) == ("log", [])
    assert G._split_command("", ["x"]) == ("", ["x"])


# --------------------------------------------------------------------------- #
# 2. a worker's .git/config cannot run a program
# --------------------------------------------------------------------------- #


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", "user.email=a@b.c", "-c", "user.name=t", "-c", "core.autocrlf=false", *args],
        cwd=str(cwd), capture_output=True, text=True, timeout=60,
    )


@pytest.fixture
def trapped_repo(tmp_path):
    """A repository whose OWN config (what a worker can write) names a
    program four ways; every program appends to ``marker``."""
    repo = tmp_path / "repo"
    repo.mkdir()
    marker = (tmp_path / "marker").as_posix()
    assert _git(repo, "init", "-q").returncode == 0
    (repo / "app.py").write_bytes(b"X = 1\n")
    (repo / "notes.txt").write_bytes(b"hello\n")
    assert _git(repo, "add", "-A").returncode == 0
    assert _git(repo, "commit", "-q", "-m", "first").returncode == 0
    (repo / ".gitattributes").write_bytes(b"*.py filter=evil\n*.txt diff=conv\n")
    for key, value in (
        ("filter.evil.clean", f"sh -c 'echo filter >> {marker}; cat'"),
        ("filter.evil.smudge", f"sh -c 'echo smudge >> {marker}; cat'"),
        ("filter.evil.required", "true"),
        ("core.fsmonitor", f"sh -c 'echo fsmonitor >> {marker}'"),
        ("diff.external", f"sh -c 'echo external >> {marker}'"),
        ("diff.conv.textconv", f"sh -c 'echo textconv >> {marker}; cat \"$0\"'"),
    ):
        assert _git(repo, "config", key, value).returncode == 0
    # A change in the working tree (the diff and status read it); an old
    # mtime so git re-reads (and would filter) the file.
    (repo / "app.py").write_bytes(b"X = 2\n")
    (repo / "notes.txt").write_bytes(b"hello again\n")
    return repo, tmp_path / "marker"


def _ctx(folder: Path) -> ToolContext:
    return ToolContext(workspace=folder, session_id="s", agent_run_id="r", config=None,
                       event_bus=None, engine=None)


async def test_a_worker_written_config_runs_nothing_through_git_read(trapped_repo):
    repo, marker = trapped_repo
    # The CONTROL: plain git here runs what the config names (the trap is real).
    _git(repo, "--no-pager", "diff")
    _git(repo, "--no-pager", "log", "-p", "-1", "--", "notes.txt")
    assert marker.exists(), "the control did not run the trap: the test would be vacuous"
    marker.unlink()
    tool = G.GitReadTool()
    for command, args in (
        ("diff", []), ("status", []), ("diff", ["--stat"]), ("log", ["-p", "-2"]),
        ("show", ["HEAD"]), ("blame", ["app.py"]), ("grep", ["-n", "X"]),
        ("diff", ["--", "notes.txt"]),
    ):
        res = await tool.execute({"command": command, "args": args}, _ctx(repo))
        assert res.ok, (command, res.error)
        assert not marker.exists(), (command, marker.read_text() if marker.exists() else "")
    # It READ the change all the same (the filter emptied, not the diff).
    res = await tool.execute({"command": "diff", "args": []}, _ctx(repo))
    assert "X = 2" in res.output and "hello again" in res.output


def test_the_argv_carries_every_guard():
    argv = G.safe_argv("diff", ["--stat"], ["evil"])
    joined = " ".join(argv)
    for piece in ("--no-pager", "core.fsmonitor=false", "core.hooksPath=", "log.showSignature=false",
                  "protocol.allow=never", "filter.evil.clean=", "filter.evil.process=",
                  "filter.evil.required=false", "--no-ext-diff", "--no-textconv",
                  "--ignore-submodules=all"):
        assert piece in joined, piece
    # The model's words come AFTER the guards (a later --ext-diff would win —
    # which is why it is refused, not just preceded).
    assert argv.index("diff") < argv.index("--stat")
    env = G.git_read_env()
    assert env["GIT_OPTIONAL_LOCKS"] == "0" and env["GIT_NO_LAZY_FETCH"] == "1"
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert "GIT_DIR" not in env and "GIT_EXTERNAL_DIFF" not in env


# --------------------------------------------------------------------------- #
# 3. masked, capped, plain words
# --------------------------------------------------------------------------- #


async def test_output_is_masked_and_capped(tmp_path, monkeypatch):
    repo = tmp_path / "r"
    repo.mkdir()
    _git(repo, "init", "-q")
    key = "sk-ant-api03-" + "Q" * 48
    (repo / "conf.py").write_bytes(f"KEY = '{key}'\n".encode() + b"# pad\n" * 400)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "c")
    res = await G.GitReadTool().execute({"command": "show", "args": ["HEAD"]}, _ctx(repo))
    assert res.ok and key not in res.output
    monkeypatch.setattr(G, "OUTPUT_CHARS", 200)
    res = await G.GitReadTool().execute({"command": "show", "args": ["HEAD"]}, _ctx(repo))
    assert res.ok and res.data["truncated"] is True
    assert "[cut: the first 200 of" in res.output


async def test_a_folder_that_is_no_repository_is_one_sentence(tmp_path):
    folder = tmp_path / "plain"
    folder.mkdir()
    res = await G.GitReadTool().execute({"command": "status"}, _ctx(folder))
    # (A temp folder inside a repository would answer; this one is not.)
    if res.ok:
        pytest.skip("the temp folder sits inside a git repository on this machine")
    assert res.error == "This folder is not in a git repository."


async def test_a_refused_call_runs_nothing(tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(G, "run", lambda *a, **k: ran.append(a))
    res = await G.GitReadTool().execute({"command": "push"}, _ctx(tmp_path))
    assert not res.ok and res.data["refused"] is True and ran == []


# --------------------------------------------------------------------------- #
# 4. registered once, armed for the reviewer only
# --------------------------------------------------------------------------- #


def test_it_is_registered_read_only_at_the_allow_tier(tmp_path):
    p = build_platform(str(tmp_path))
    tool = p.registry.get("git_read")
    assert tool is not None
    assert tool.reversibility is Reversibility.READONLY and replay_safe(tool)
    assert tool.returns_untrusted_content is True
    assert p.config.permissions.get("git_read") == "allow"
    # On no built-in roster.
    for t in AgentType:
        assert "git_read" not in (get_agent_definition(t).tools or []), t
    from iron_jarvis.harness import mode

    assert "git_read" not in mode.CODING_TOOLS


def _parent(**kw) -> H.HelperParent:
    base = {"session_id": "build:s", "folder": "", "provider": "p", "model": "m"}
    return H.HelperParent(**{**base, **kw})


def test_a_review_only_helper_holds_it_whatever_the_turn_holds():
    reviewer = get_agent_definition(AgentType.REVIEWER)
    # The finish review: the turn's reading tools, and git_read ALWAYS.
    got = H.helper_definition(_parent(tools=("read_file", "grep", "shell", "write_file"), review_only=True),
                              reviewer)
    assert set(got.tools) == {"read_file", "grep", "git_read"}
    # A turn with no reading tool at all still gives it git_read.
    got = H.helper_definition(_parent(tools=(), review_only=True), reviewer)
    assert got.tools == ["git_read"]
    # The CI reviewer (routes/ci_watch.run_review's parent: REVIEW_TOOLS).
    got = H.helper_definition(_parent(tools=tuple(H.REVIEW_TOOLS), review_only=True, own_model=True), reviewer)
    assert set(got.tools) == set(H.REVIEW_TOOLS) and "git_read" in got.tools
    # Any other helper: never (the builder gets the turn's set, which lacks it).
    turn = ("read_file", "grep", "write_file", "shell")
    for kind in (AgentType.BUILDER, AgentType.RESEARCHER, AgentType.REVIEWER):
        got = H.helper_definition(_parent(tools=turn), get_agent_definition(kind))
        assert "git_read" not in got.tools, kind


async def test_through_the_lane_the_reviewer_holds_it_and_the_lead_and_worker_do_not(world):  # noqa: F811
    members(world.p)
    world.main = [[*_named_piece()], "The worker set X to 2.", answer_note(world)]
    world.worker = [write_x(2), "Changed app.py."]
    world.reviewer = ["Good.\nVERDICT: approved"]
    await stream(world.p, body(world.ws))
    assert reviewer_calls(world), "the finish review did not run"
    assert "git_read" in reviewer_calls(world)[0]["tools"]
    assert not {"shell", "write_file", "edit_file"} & set(reviewer_calls(world)[0]["tools"])
    assert all("git_read" not in c["tools"] for c in lead_calls(world))
    assert all("git_read" not in c["tools"] for c in worker_calls(world))


def _named_piece():
    from iron_jarvis.providers.adapters.base import ToolCall

    return [ToolCall(id="n1", name="delegate",
                     arguments={"agent_type": "custom:qwen", "task": "Set X to 2 in app.py."})]
