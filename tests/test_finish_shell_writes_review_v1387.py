"""THE FINISH CHECK SEES COMMANDS — the review's pins.

Each pin names the defect it found and FAILS on the doer's code (69a32324,
merged with Phase 3 as 52e3ae5b) — the two open points the doer left in the
lost runs themselves, and what the review found past them — or is a GUARD
(it passes on both and is mutation-checked against the fix).

1a. HEREDOCS (the lost runs' "No tests found"): under bash, a here-document's
    BODY is not part of the command line — the test-part reader read the
    whole 5,871-character command, refused it past ``MAX_COMMAND_CHARS``, and
    so the model's own ``python -m unittest`` was never found; and a body
    line that LOOKED like a test command was read as one. The body is now
    left out before the length check and the split
    (``finish.strip_heredocs``), a segment fed by one is never a test or a
    setup step, and cmd's reading is unchanged.
1b. THE FALSE ACCUSATION: both lost runs' replies ended with the chat's "no
    file was actually written this turn" after a bash heredoc WROTE calc.py,
    and every process-http-server reply ("saved … to status.json") got
    "nothing was written to disk … that file does not exist". Neither note
    now claims nothing was written after something that can write without a
    file tool RAN — a command (in a Build turn, one that failed too:
    ``FinishCheck.may_have_written``), code, a hand-off, an app's tool. The
    folder comparison is not used to say the opposite: it never sees an
    ignored or pruned folder or a file outside the session's folder, so it
    can prove a write, never its absence. Both lanes, lock-step.
2.  WHAT COUNTS AS A CHANGE: every nested ``.gitignore`` is read (past the
    64th folder none was — on this PC's own Iron Jarvis checkout the snapshot
    always fell back as "more than 5000 files"); ``.github`` and the other
    source dot-folders are walked, every other dot-folder is not; a
    ``.gitignore`` that ignores everything hides every change (decided); a
    linked folder is one entry, never followed; the "gone" words restore only
    what the model's own command removed.
3.  THE WORDS for a command's files: a scratch file the model wrote can go
    (and why it should), a program's log stays; a folder the request names
    does not exempt its files from the keep words (decided); a let-go first
    snapshot never blames a restart.
4.  COST: the walk's deadline is the project map's (3 s, both walks run on
    every reply); one stalled listing never holds the reply (the wait is
    bounded, the walk left on its thread, never a second beside it).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import _claimed_write_note, _creation_honesty_note, run_chat_turn
from iron_jarvis.harness import finish as F
from iron_jarvis.harness import folder_changes as FC
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.providers.adapters.base import ToolCall
from iron_jarvis.tools.base import ToolResult
from tests.test_finish_lanes_v1386 import (  # noqa: F401 — the fixture
    NOTE_HEAD,
    SESSION,
    Model,
    _body,
    _frames,
    _lane,
    _shell,
    _stream,
    env,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "finish_shell_replays_v1387.json").read_text(encoding="utf-8")
)


@pytest.fixture(autouse=True)
def _no_kept_snapshots():
    FC.forget_kept()
    yield
    FC.forget_kept()


def _recorded_command(name: str, work: str = "C:/work/calc") -> str:
    for move in FIXTURE["runs"][name]["moves"]:
        for call in move["calls"]:
            if call["tool"] == "shell":
                return call["args"]["command"].replace("{WORK/}", work)
    raise AssertionError(name)


def _py(code: str) -> str:
    """A `python -c` command both cmd.exe and bash read the same way."""
    return f'python -c "{code}"'


# --------------------------------------------------------------------------- #
# 1a. a heredoc's body is not the command line
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", ["claude-r1", "claude-r2"])
def test_the_lost_runs_own_test_command_is_found_past_the_heredoc(name):
    """THE LOST RUNS: `cd {WORK} && python - <<'EOF' … EOF` then `python -m
    unittest -q 2>&1|tail -3` — the test part is the cd and the unittest run
    (its pipe left out, as for any command). OLD CODE: "" (the command is
    longer than MAX_COMMAND_CHARS, so it was never read)."""
    command = _recorded_command(name)
    assert len(command) > F.MAX_COMMAND_CHARS  # the recorded shape, untouched
    part = F.test_part(command, posix=True)
    assert part == "cd C:/work/calc && python -m unittest -q 2>&1"
    # The body is never part of what is run again, nor counted as a test.
    assert "open('calc.py'" not in part and "EOF" not in part
    assert F.test_heads(command, posix=True) == ["python -m unittest -q"]


def test_cmds_reading_is_unchanged():
    """cmd has no here-documents: its reading of the same text is what it was
    (too long, so no test part) and a short line reads as before."""
    command = _recorded_command("claude-r1")
    assert F.test_part(command, posix=False) == ""
    assert F.test_part("python -m pytest -q", posix=False) == "python -m pytest -q"


@pytest.mark.parametrize("command, part", [
    # quoted, unquoted, double-quoted, a backslash, a space after <<
    ("cat <<'EOF' > a.py\nx = 1\nEOF\npytest -q", "pytest -q"),
    ("cat <<EOF > a.py\nx = 1\nEOF\npytest -q", "pytest -q"),
    ('cat << "END" > a.py\nx = 1\nEND\npytest -q', "pytest -q"),
    ("cat <<\\EOF > a.py\nx = 1\nEOF\npytest -q", "pytest -q"),
    # <<- : the closing word may be indented with tabs
    ("cat <<-EOF > a.py\n\tx = 1\n\tEOF\npytest -q", "pytest -q"),
    # two here-documents on one line: their bodies follow in order
    ("cat <<A > a.py; cat <<B > b.py\n1\nA\npytest -q\nB\npytest -x", "pytest -x"),
    # the operator line keeps what follows it on the same line
    ("cat <<EOF > a.py && pytest -q\nx = 1\nEOF", "pytest -q"),
    # a body that holds a test command is never a test command (OLD CODE read
    # the body's `pytest -q` line as one, and would have run it again)
    ("python - <<'EOF'\nimport os\nos.system('pytest')\nEOF", ""),
    ("cat <<EOF\npytest -q\nEOF", ""),
    # a body never closed runs to the end (bash reads it so): nothing after it
    ("cat <<EOF > a.py\nx = 1\npytest -q", ""),
    # the closing word must be the whole line
    ("cat <<EOF > a.py\nEOF2\npytest -q\nEOF\npytest -x", "pytest -x"),
    # a test fed by a here-document cannot be run again as it ran: not a test
    ("pytest -q <<EOF\ny\nEOF", ""),
    # ...nor a setup step one feeds
    ("cd /w <<EOF\nx\nEOF\npytest -q", "pytest -q"),
])
def test_heredoc_bodies_are_left_out_under_bash(command, part):
    assert F.test_part(command, posix=True) == part


@pytest.mark.parametrize("command, part", [
    # a here-STRING is not a here-document
    ("cat <<< 'x'\npytest -q", "pytest -q"),
    # inside quotes, `<<` is text
    ("echo 'a <<EOF'\npytest -q", "pytest -q"),
    ('echo "a <<EOF"\npytest -q', "pytest -q"),
    # (the next lines are not a body: read as one, it would hide `pytest -q`)
    ("echo 'a <<EOF b'\npytest -q\nEOF\npytest -x", "pytest -q && pytest -x"),
    # arithmetic shifts are not here-documents (read as one, the next line
    # would be taken for a body)
    ("echo $((1<<2))\npytest -q", "pytest -q"),
    ("x=$((1 << 2))\npytest -q", "pytest -q"),
    # a comment's `<<` is not one either
    ("echo hi # see <<EOF\npytest -q", "pytest -q"),
])
def test_what_is_not_a_heredoc_is_read_as_before(command, part):
    assert F.test_part(command, posix=True) == part


def test_the_length_limit_still_holds_past_the_bodies():
    """Only the body is left out: a command LINE longer than the limit is
    still not read."""
    long_line = "echo " + "x" * F.MAX_COMMAND_CHARS + " && pytest -q"
    assert F.test_part(long_line, posix=True) == ""
    body = "\n".join("x = 1" for _ in range(2000))
    assert F.test_part(f"cat <<EOF > a.py\n{body}\nEOF\npytest -q", posix=True) == "pytest -q"


def test_strip_heredocs_keeps_every_other_byte():
    command = "cd /w && python - <<'EOF'\nprint(1)\nEOF\npython -m unittest -q 2>&1|tail -3\necho done"
    assert F.strip_heredocs(command) == "cd /w && python - <<'EOF'\npython -m unittest -q 2>&1|tail -3\necho done"
    assert F.strip_heredocs("no heredoc here\npytest") == "no heredoc here\npytest"


def test_a_heredoc_run_is_judged_by_its_test_status_only_when_the_chain_says_so():
    """`status_is_tests` reads the same line: a body never makes a chain look
    like setup-and-tests."""
    assert F.status_is_tests("cd /w && pytest -q <<EOF\ny\nEOF", posix=True) is False
    assert F.status_is_tests("cd /w && pytest -q", posix=True) is True
    command = "cat <<EOF > a.py\nx = 1\nEOF\n"
    assert F.status_is_tests(command + "cd /w && pytest -q", posix=True) is False  # a line break joins it


# --------------------------------------------------------------------------- #
# 1b. never "no file was written" after a command wrote one
# --------------------------------------------------------------------------- #

#: The chat's two notes that say nothing was written (their own words).
NOTHING_WRITTEN = ("no file was actually written", "nothing was written to disk")
ASKS_FOR_A_FILE = "create a csv file report.csv with the totals"


def _said_nothing_written(reply: str) -> bool:
    return any(words in reply for words in NOTHING_WRITTEN)


def _met(model: Model) -> str:
    assert model.calls[-1]["messages"][-1].content.startswith(NOTE_HEAD)
    return "1. met: checked.\nNothing needed changing."


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_file_a_command_wrote_is_never_called_unwritten(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """THE LOST RUNS' SECOND DEFECT: the user asked for a file, a COMMAND
    wrote it (no file tool ran), and the reply still ended with the chat's
    "no file was actually written this turn" — and, for a reply that names
    the file, "nothing was written to disk … that file does not exist". The
    Build turn's folder says it was written. OLD CODE: both notes."""
    platform, ws, wire = env
    wire([_shell(_py("open('report.csv', 'w').write('a,b')")), "Created report.csv with the totals.", _met])
    done, _ = await _lane(lane, platform, _body(ws, text=ASKS_FOR_A_FILE), monkeypatch)
    assert (ws / "report.csv").exists()
    assert "finish" in done  # the folder said the reply changed files
    assert not _said_nothing_written(done["reply"]), done["reply"]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_command_that_ran_and_failed_after_writing_is_never_accused(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """A command wrote the file and then FAILED (a heredoc, then a test that
    exits 1): ``tools_used`` holds only the calls that succeeded, so the
    notes' own rule could not see it — the Build turn's finish check can
    (``FinishCheck.may_have_written``). OLD CODE: both notes."""
    platform, ws, wire = env
    write_then_fail = _py("open('report.csv', 'w').write('a,b'); import sys; sys.exit(1)")
    wire([_shell(write_then_fail), "Created report.csv with the totals.", _met])
    done, _ = await _lane(lane, platform, _body(ws, text=ASKS_FOR_A_FILE), monkeypatch)
    assert (ws / "report.csv").exists() and "shell" not in done["tools_used"]
    assert not _said_nothing_written(done["reply"]), done["reply"]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_file_written_where_the_folder_is_not_compared_is_never_accused(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """WHY THE FOLDER'S SILENCE IS NOT EVIDENCE: the command wrote the file
    the user asked for into a folder the .gitignore names (`out/`, never
    walked) — the comparison saw nothing, and "nothing was written" would be
    false. A snapshot can prove a write, never that none happened. OLD CODE:
    both notes."""
    platform, ws, wire = env
    (ws / ".gitignore").write_text("out/\n", encoding="utf-8")
    write = _py("import os; os.makedirs('out', exist_ok=True); open('out/report.csv', 'w').write('a,b')")
    model = wire([_shell(write), "Created out/report.csv with the totals."])
    done, _ = await _lane(lane, platform, _body(ws, text=ASKS_FOR_A_FILE), monkeypatch)
    assert (ws / "out" / "report.csv").exists()
    assert model.notes() == [] and "finish" not in done  # the comparison saw nothing to check
    assert not _said_nothing_written(done["reply"]), done["reply"]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_build_reply_that_ran_nothing_still_gets_the_true_notes(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """THE GUARD: no tool ran at all, and the reply claims a file — "nothing
    was written" is TRUE and both notes say it, as they always did
    (mutation: a writer counted that never ran)."""
    platform, ws, wire = env
    model = wire(["Created report.csv with the totals."])
    done, _ = await _lane(lane, platform, _body(ws, text=ASKS_FOR_A_FILE), monkeypatch)
    assert not (ws / "report.csv").exists() and model.notes() == [] and "finish" not in done
    assert "no file was actually written" in done["reply"], done["reply"]
    assert "The reply mentions `report.csv`, but no document-writing tool ran" in done["reply"]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_plain_chat_claims_nothing_after_a_command(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """Plain chat (no finish check): after a command SUCCEEDED (it may have
    written the file), neither note says nothing was written. OLD CODE: the
    claim note ("report.csv does not exist"), though the command wrote it."""
    platform, ws, wire = env
    wire([_shell(_py("open('report.csv', 'w').write('a,b')")), "Created report.csv with the totals."])
    body = _body(ws, text=ASKS_FOR_A_FILE, tools=["shell", "write_file"])
    if lane == "stream":
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body)
        done = [d for e, d in _frames([c async for c in gen]) if e == "done"][0]
    else:
        monkeypatch.setitem(platform.permissions._base, "shell", "allow")
        done = await run_chat_turn(platform, BUILTIN_PERSONAS, body)
    assert (ws / "report.csv").exists() and "shell" in done["tools_used"]
    assert not _said_nothing_written(done["reply"]), done["reply"]


def test_may_have_written_is_any_writer_that_ran(tmp_path):
    """``FinishCheck.may_have_written``: a command that RAN (passing or
    failing), never one the tool refused before any program started, never a
    reader, never the harness's own call."""
    check = F.FinishCheck(engine=None, home=tmp_path, event_bus=None, session_id=SESSION,
                          folder=tmp_path, mark=0, request="x")
    assert check.may_have_written() is False
    read = ToolResult(ok=True, output="x", data={})
    check.observe(ToolCall(id="r1", name="read_file", arguments={"path": "a"}), read, refused=False)
    refused = ToolResult(ok=False, output="", error="the git guard refused it", data={})
    check.observe(ToolCall(id="m0", name="shell", arguments={"command": "git push -f"}), refused, refused=False)
    assert check.may_have_written() is False
    failed = ToolResult(ok=False, output="", data={"returncode": 1})  # it ran, and failed
    check.observe(ToolCall(id="m1", name="shell", arguments={"command": "make"}), failed, refused=False)
    assert check.may_have_written() is True


def test_the_notes_never_claim_nothing_after_a_writer_ran():
    body = SimpleNamespace(messages=[SimpleNamespace(role="user", content=ASKS_FOR_A_FILE)])
    armed = ["write_file", "shell"]
    true_note = _creation_honesty_note(body, armed, ["read_file"])
    assert "no file was actually written" in true_note
    assert _creation_honesty_note(body, armed, ["read_file"], ran_writer=False) == true_note
    assert _creation_honesty_note(body, armed, ["shell"]) == ""
    assert _creation_honesty_note(body, armed, ["read_file"], ran_writer=True) == ""
    assert _creation_honesty_note(body, armed, ["bump"], custom_tools={"bump"}) == ""
    claim = "Saved to report.csv."
    judged = _claimed_write_note(claim, ["read_file"])
    assert judged != "" and _claimed_write_note(claim, ["read_file"], ran_writer=False) == judged
    assert _claimed_write_note(claim, ["shell"]) == ""
    assert _claimed_write_note(claim, ["read_file"], ran_writer=True) == ""


@pytest.mark.parametrize("name", ["claude-r1", "claude-r2"])
async def test_the_lost_runs_reply_is_never_told_no_file_was_written(tmp_path, name, monkeypatch):
    """THE LOST RUNS, as they were recorded — read, read, a bash heredoc that
    WROTE calc.py, the reply — through the real stream lane in real Git bash.
    Their recorded replies ended with "no file was actually written this
    turn". OLD CODE (the doer's): still that note."""
    from tests.test_finish_shell_replay_v1387 import BASH, _points, _replay_lost_run

    if BASH is None:
        pytest.skip("the recorded runs wrote calc.py in Git for Windows' bash, which is not installed here")

    def all_met(model: Model) -> str:
        note = model.calls[-1]["messages"][-1].content
        assert note.startswith(NOTE_HEAD)
        return "\n".join(f"{n}. met: checked." for n in _points(note)) + "\nNothing needed changing."

    out = await _replay_lost_run(tmp_path, name, [all_met], monkeypatch)
    reply = out["done"]["reply"]
    assert "evaluate" in (out["work"] / "calc.py").read_text(encoding="utf-8")
    assert out["done"]["tools_used"] == ["read_file", "read_file", "shell", "shell"]  # + the check's own run
    assert not _said_nothing_written(reply), reply


# --------------------------------------------------------------------------- #
# 2. what counts as a change
# --------------------------------------------------------------------------- #


def _put(root: Path, rel: str, data: bytes = b"x") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def test_a_nested_gitignore_is_read_however_many_folders_the_walk_saw(tmp_path):
    """THE USER'S OWN REPO: ``GitIgnore`` read at most 64 folders'
    ``.gitignore`` (enough for the few files the new-files list asks about),
    and the snapshot asks about EVERY folder — so past the 64th, a nested
    ``.gitignore`` was never read and the folder it ignores was walked.
    Measured on this PC's Iron Jarvis checkout: ``desktop/.gitignore`` names
    ``release/`` (3,696 files), the walk reached it after 64 folders and the
    snapshot said "more than 5000 files" — the finish check fell back to the
    journal alone on the user's main project, every reply (3,853 files, 63 ms
    when read whole). Here: 70 folders, each ignoring its own ``out/``;
    whatever the walk's order, at least 6 come past the 64th. OLD CODE: their
    ``out/`` files were walked (over the cap)."""
    for i in range(70):
        _put(tmp_path, f"b{i:02d}/.gitignore", b"out/\n")
        _put(tmp_path, f"b{i:02d}/keep.py")
        _put(tmp_path, f"b{i:02d}/out/bundle.js")
    snap = FC.take(tmp_path, limit=150)
    assert snap.usable, snap.problem
    assert not [r for r in snap.files if "/out/" in r]
    assert len(snap.files) == 140


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_ignored_files_in_many_folders_are_never_changes(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """The same cap in the COMPARISON: a command (a test run) rewrote an
    ignored log in 70 folders — the reply changed nothing. OLD CODE: past the
    64th folder the logs read as changes, and the check ran on a reply that
    changed nothing."""
    platform, ws, wire = env
    for i in range(70):
        _put(ws, f"m{i:02d}/.gitignore", b"*.log\n")
        _put(ws, f"m{i:02d}/run.log", b"a")
    touch = _py("import glob; [open(p, 'w').write('more') for p in glob.glob('m*/run.log')]")
    model = wire([_shell(touch), "Ran it."])
    done, _ = await _lane(lane, platform, _body(ws, text="run it"), monkeypatch)
    assert (ws / "m69" / "run.log").read_text() == "more"
    assert model.notes() == [] and "finish" not in done


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_command_edit_of_the_projects_ci_workflow_is_a_change(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """DECIDED: ``.github/`` (and the other dot-folders that hold a project's
    own source — CI, dev container, git hooks: ``SOURCE_DOT_DIRS``) is
    walked; every other dot-folder (caches, virtualenvs, tool state) is not.
    A reply that changed only ``.github/workflows/ci.yml`` with a command
    changed files. OLD CODE: invisible (every dot-folder was skipped), so the
    check never ran."""
    platform, ws, wire = env
    _put(ws, ".github/workflows/ci.yml", b"on: push\n")
    edit = _py("open('.github/workflows/ci.yml', 'a').write('# lint too')")
    model = wire([_shell(edit), "Added lint to CI.", "1. met."])
    done, _ = await _lane(lane, platform, _body(ws, text="add lint to the CI workflow"), monkeypatch)
    assert len(model.notes()) == 1 and "finish" in done


async def test_other_dot_folders_stay_unwalked(env):  # noqa: F811 — the fixture
    """THE GUARD for the decision above: a test run's tool state in other
    dot-folders (``.tox``, ``.cache``, ``.venv`` …) is never a change."""
    platform, ws, wire = env
    plant = [".tox/py312/log.txt", ".cache/x/y", ".nox/s/z", ".hypothesis/examples/a", ".idea/ws.xml"]
    code = "import os; [os.makedirs(os.path.dirname(p), exist_ok=True) or open(p, 'w').write('x') for p in " \
           + repr(plant).replace('"', "'") + "]"
    model = wire([_shell(_py(code)), "Done."])
    out = await _stream(platform, _body(ws, text="run tox"))
    assert all((ws / p).exists() for p in plant)
    assert model.notes() == [] and "finish" not in out["done"]
    snap = FC.take(ws)
    assert not [r for r in snap.files if r.startswith(".")]  # (no dot-FILE planted here)


async def test_a_gitignore_that_ignores_everything_hides_every_change(env):  # noqa: F811 — the fixture
    """DECIDED (the brief's question): a folder whose ``.gitignore`` ignores
    everything (``*``) — git itself would see no change, and neither does the
    comparison: a command's write there does not open the check, exactly as
    for the new-files list. (The honesty notes still never say "nothing was
    written": a command ran.) A file tool's write still does (its journal)."""
    platform, ws, wire = env
    (ws / ".gitignore").write_text("*\n", encoding="utf-8")
    model = wire([_shell(_py("open('app.py', 'w').write('X = 2\\n')")), "Set X to 2."])
    out = await _stream(platform, _body(ws))
    assert (ws / "app.py").read_text() == "X = 2\n"
    assert model.notes() == [] and "finish" not in out["done"]
    assert not _said_nothing_written(out["done"]["reply"])


def test_the_gone_words_never_restore_what_someone_else_removed():
    """A file gone since the reply began may be one the USER deleted while it
    ran (or between a cut-off reply and its carry-on, hours later): the note
    asks the model to put back only what ITS OWN command removed. OLD CODE:
    "If the task did not ask for that, put them back." — for every file."""
    assert "someone working in this folder" in F.GONE_ASK
    assert "If a command you ran removed one the task did not ask to remove, put it back." in F.GONE_ASK
    assert "put them back" not in F.GONE_ASK


# --------------------------------------------------------------------------- #
# 4. cost and the loop
# --------------------------------------------------------------------------- #


def test_the_walks_deadline_is_the_maps_and_its_cap_list_files():
    """Both walks run at the start (and the end) of EVERY Build reply, before
    the first model call: the project map's start-of-turn deadline (3 s), not
    list_files' 10 s for one call. The file cap stays list_files'. OLD CODE:
    10 s — on slow storage, up to 10 s before every reply's first word, and
    again at its end."""
    from iron_jarvis.harness import project_map
    from iron_jarvis.tools import builtins as B

    assert FC.WALK_DEADLINE_S == project_map.WALK_DEADLINE_S == 3.0
    assert FC._caps() == (B._MAX_WALK_ENTRIES, FC.WALK_DEADLINE_S)


async def test_a_stalled_walk_never_holds_the_reply_and_is_never_started_twice(env, monkeypatch):  # noqa: F811 — the fixture
    """A folder whose listing never returns (a network share gone away): the
    walk's own deadline is read between folders, so ONE stalled
    ``os.scandir`` held the reply's first model call for as long as it
    stalled. Now the reply waits the deadline and a grace, then goes on with
    the journal alone (said); the stalled walk is left on its thread, and a
    second reply in that folder does not start another while it runs. OLD
    CODE: the first model call waited the whole stall."""
    import threading
    import time

    platform, ws, wire = env
    monkeypatch.setattr(FC, "_caps", lambda: (5000, 0.2))
    monkeypatch.setattr(FC, "WALK_GRACE_S", 0.3)
    release = threading.Event()
    started: list[float] = []
    real = FC.take

    def stalled(folder, **kw):
        started.append(time.monotonic())
        release.wait(8.0)
        return real(folder, **kw)

    monkeypatch.setattr(FC, "take", stalled)
    first_call: list[float] = []

    def stamp(model: Model):
        first_call.append(time.monotonic())
        return _shell(_py("open('app.py', 'w').write('X = 2\\n')"))

    try:
        for _turn in range(2):
            began = time.monotonic()
            first_call.clear()
            model = wire([stamp, "Done."])
            out = await _stream(platform, _body(ws))
            assert first_call and first_call[0] - began < 3.0, first_call[0] - began
            assert model.notes() == [] and "finish" not in out["done"]
        assert len(started) == 1, "a second walk of a folder still being read was started"
    finally:
        release.set()


# --------------------------------------------------------------------------- #
# 3. the note's words for what a command made
# --------------------------------------------------------------------------- #

FILES_HEAD = "## Files this reply made that the request does not name\n"


def _files(note: str) -> str:
    return note.split(FILES_HEAD, 1)[1].split("\n\n", 1)[0]


def test_the_appeared_words_warn_that_a_left_scratch_file_breaks_the_test_run():
    """A scratch file a COMMAND wrote (``cat > _probe.py <<EOF``) is listed
    beside a program's log: the words keep the log and let the model remove
    its own scratch file — and, like the file tools' list, say why it should
    (a stray test file is picked up by the project's own test run, and by a
    grader's). OLD WORDS: keep-only, no reason to remove one."""
    assert "may be just what the task needs, so leave these as they are" in F.APPEARED_KEEP
    assert "remove one only if you wrote it yourself as a scratch file and nothing needs it" in F.APPEARED_KEEP
    assert "can break the project's own test run" in F.APPEARED_KEEP


async def test_a_scratch_file_a_command_wrote_can_go_and_a_programs_log_stays(env):  # noqa: F811 — the fixture
    """Replayed through the claude dialect in real Git bash: the model wrote
    ``_probe.py`` with a heredoc and ran it, and a program wrote
    ``server.log``. The note lists both in the keep-what-a-program-wrote
    words; a model that follows them removes its own probe with a command
    and keeps the log. The record counts the FILE TOOLS' files only, so
    neither is counted ("1 scratch file removed" never means a command's)."""
    from tests.test_finish_shell_replay_v1387 import BASH

    if BASH is None:
        pytest.skip("Git for Windows' bash is not installed here")
    platform, ws, wire = env
    probe = "cat > _probe.py <<'EOF'\nimport app\nprint(app.X)\nEOF\npython _probe.py"
    program = 'python -c "open(\'server.log\', \'w\').write(\'listening\')"'
    seen: list[str] = []

    def follow_the_words(model: Model):
        files = _files(model.calls[-1]["messages"][-1].content)
        seen.append(files)
        assert "- `_probe.py`" in files and "- `server.log`" in files
        assert F.APPEARED_KEEP in files
        return ("1. met: checked.", [ToolCall(id="rm", name="Bash", arguments={"command": "rm _probe.py"})])

    wire([[ToolCall(id="b1", name="Bash", arguments={"command": probe})],
          [ToolCall(id="b2", name="Bash", arguments={"command": program})],
          "Probed X; the server is up.", follow_the_words, "Removed my probe; kept the server's log."])
    out = await _stream(platform, _body(ws, text="check what X is in app.py and start the server"), harness="claude")
    assert seen, "no finish note"
    assert not (ws / "_probe.py").exists() and (ws / "server.log").read_text() == "listening"
    assert out["done"]["finish"]["removed"] == 0 and out["done"]["finish"]["kept"] == 0


async def test_files_put_in_a_folder_the_request_names_are_listed_with_the_keep_words(env):  # noqa: F811 — the fixture
    """DECIDED: a folder the request names (``inbox/``) does not exempt the
    files under it — a named ``tests/`` folder would then hide a stray
    scratch test file from the file tools' list — but what a command put
    there is listed only in the keep-what-the-task-needs words, never in the
    "remove the rest" list."""
    platform, ws, wire = env
    (ws / "incoming").mkdir()
    (ws / "inbox").mkdir()
    (ws / "incoming" / "a.csv").write_text("amount\n1\n", encoding="utf-8")
    copy = _py("import shutil; shutil.copy('incoming/a.csv', 'inbox/a.csv')")
    model = wire([_shell(copy), "Copied it.", "1. met."])
    await _stream(platform, _body(ws, text="copy the file from incoming/ into inbox/"))
    files = _files(model.notes()[0])
    assert files == "None made with your file tools. " + F.APPEARED_HEAD + "\n- `inbox/a.csv`\n" + F.APPEARED_KEEP
    assert "remove the rest" not in files


async def test_a_carry_on_whose_first_snapshot_was_let_go_never_blames_a_restart(env, monkeypatch):  # noqa: F811 — the fixture
    """The first snapshot of a cut-off reply is kept for the newest replies of
    a few sessions only: a carry-on hours later, after work in four other
    Build sessions, finds it gone with NO restart. OLD WORDS: "(Iron Jarvis
    restarted after the reply was cut off)" — said as fact either way."""
    from tests.test_finish_shell_writes_v1387 import _cut_then_carry_on

    def other_sessions_since():
        for i in range(FC.MAX_KEPT_REPLIES):
            FC.keep(f"build:other-{i}", f"turn-{i}", FC.unavailable("x", "y"))

    monkeypatch.setattr(FC, "forget_kept", other_sessions_since)
    model, _out = await _cut_then_carry_on(platform=env[0], ws=env[1], wire=env[2], forget=True,
                                           carry_moves=[_write_call("notes.md"), "Done.", "1. met."])
    files = _files(model.notes()[0])
    assert F.FinishCheck.CANNOT_COMPARE.format(why=FC.PROBLEM_NOT_KEPT) in files
    assert "Iron Jarvis restarted" not in files


def _write_call(path: str) -> list[ToolCall]:
    return [ToolCall(id="k1", name="write_file", arguments={"path": path, "content": "- x\n"})]


def test_a_linked_folder_is_recorded_as_a_link_and_never_followed(tmp_path):
    """A symbolic link to a folder OUTSIDE the session's folder is one entry
    (the link itself), never walked: what changes out there is not this
    folder's change (the doer pinned the junction; this is the link)."""
    outside = tmp_path / "outside"
    _put(outside, "secret.txt", b"x")
    work = tmp_path / "work"
    _put(work, "a.txt")
    try:
        os.symlink(outside, work / "link", target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links cannot be made here (Windows without developer mode)")
    before = FC.take(work)
    assert before.usable and sorted(before.files) == ["a.txt", "link"]
    (outside / "secret.txt").write_bytes(b"changed outside")
    _put(outside, "new.txt")
    assert not FC.compare(before, FC.take(work)).any
