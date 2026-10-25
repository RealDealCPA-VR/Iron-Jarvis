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
    "nothing was written to disk … that file does not exist". The Build
    turn's folder comparison now decides (``FinishCheck.folder_wrote``);
    without one, neither note claims nothing was written after a command
    (or code, a hand-off, an app's tool) ran. Both lanes, lock-step.
"""

from __future__ import annotations

import json
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
    ("cat <<A > a.py; cat <<B > b.py\n1\nA\n2\nB\npytest -q", "pytest -q"),
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
])
def test_heredoc_bodies_are_left_out_under_bash(command, part):
    assert F.test_part(command, posix=True) == part


@pytest.mark.parametrize("command, part", [
    # a here-STRING is not a here-document
    ("cat <<< 'x'\npytest -q", "pytest -q"),
    # inside quotes, `<<` is text
    ("echo 'a <<EOF'\npytest -q", "pytest -q"),
    ('echo "a <<EOF"\npytest -q', "pytest -q"),
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
async def test_a_command_that_wrote_nothing_still_gets_the_true_note(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """THE GUARD: a command ran, the folder was compared, nothing was
    written — so "no file was written" is TRUE and both notes say it, as
    they always did (mutation: a folder verdict that is never False)."""
    platform, ws, wire = env
    model = wire([_shell(_py("print(1)")), "Created report.csv with the totals."])
    done, _ = await _lane(lane, platform, _body(ws, text=ASKS_FOR_A_FILE), monkeypatch)
    assert not (ws / "report.csv").exists() and model.notes() == [] and "finish" not in done
    assert "no file was actually written" in done["reply"], done["reply"]
    assert "The reply mentions `report.csv`, but no document-writing tool ran" in done["reply"]


async def test_a_command_in_the_finish_round_counts_too(env):  # noqa: F811 — the fixture
    """The folder was compared when the reply first ended (only a removed
    file then); the model's answer to the note then WROTE the file with a
    command. The comparison is stale: the notes must not say nothing was
    written. OLD CODE: both notes."""
    platform, ws, wire = env
    (ws / "old.txt").write_text("x", encoding="utf-8")

    def answer(model: Model):
        assert model.calls[-1]["messages"][-1].content.startswith(NOTE_HEAD)
        return ("1. met.", _shell(_py("open('report.csv', 'w').write('a,b')"), "s2"))

    wire([_shell(_py("import os; os.remove('old.txt')")), "Done.", answer, "Created report.csv."])
    out = await _stream(platform, _body(ws, text=ASKS_FOR_A_FILE))
    assert (ws / "report.csv").exists() and "finish" in out["done"]
    assert not _said_nothing_written(out["done"]["reply"]), out["done"]["reply"]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_plain_chat_claims_nothing_after_a_command(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """Plain chat has no folder comparison: after a command ran (it may have
    written the file), neither note says nothing was written. OLD CODE: both
    notes, though the command wrote report.csv."""
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


def test_the_folders_verdict_on_what_a_reply_wrote(tmp_path):
    """``FinishCheck.folder_wrote``: True when the comparison found a file
    written (or moved in), or something that writes ran and the folder was
    not compared — or ran again after it; False when it was compared and
    nothing was written; None when nothing that writes ran."""
    check = F.FinishCheck(engine=None, home=tmp_path, event_bus=None, session_id=SESSION,
                          folder=tmp_path, mark=0, request="x")
    assert check.folder_wrote() is None
    ran = ToolResult(ok=False, output="", data={"returncode": 1})  # it ran, and failed
    check.observe(ToolCall(id="m1", name="shell", arguments={"command": "make"}), ran, refused=False)
    assert check.folder_wrote() is True  # not compared: nothing can be claimed
    check.compared, check._seen = True, FC.Changes()
    assert check.folder_wrote() is False
    check._seen = FC.Changes(deleted=["a.txt"])
    assert check.folder_wrote() is False  # a removal writes nothing
    for found in (FC.Changes(created=["a"]), FC.Changes(changed=["a"]), FC.Changes(moved=[("a", "b/a")])):
        check._seen = found
        assert check.folder_wrote() is True
    check._seen = FC.Changes()
    check.observe(ToolCall(id="m2", name="shell", arguments={"command": "make"}), ran, refused=False)
    assert check.folder_wrote() is True  # it ran again after the comparison


def test_the_notes_read_the_folders_verdict():
    body = SimpleNamespace(messages=[SimpleNamespace(role="user", content=ASKS_FOR_A_FILE)])
    armed = ["write_file", "shell"]
    true_note = _creation_honesty_note(body, armed, ["read_file"])
    assert "no file was actually written" in true_note
    assert _creation_honesty_note(body, armed, ["shell"]) == ""  # no verdict: nothing claimed
    assert _creation_honesty_note(body, armed, ["shell"], wrote=False) == true_note
    assert _creation_honesty_note(body, armed, ["shell"], wrote=True) == ""
    assert _creation_honesty_note(body, armed, ["read_file"], wrote=True) == ""
    assert _creation_honesty_note(body, armed, ["bump"], custom_tools={"bump"}) == ""
    claim = "Saved to report.csv."
    assert _claimed_write_note(claim, ["shell"], wrote=False) == _claimed_write_note(claim, ["read_file"]) != ""


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
