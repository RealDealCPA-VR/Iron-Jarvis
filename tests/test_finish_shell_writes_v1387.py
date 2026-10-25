"""THE FINISH CHECK SEES FILES A REPLY CHANGED THROUGH COMMANDS.

The part 3 Phase 2 measurement (Jarvis on Claude, sonnet medium, 3 repeats)
passed 124 of 126; both failures were ``hard-expression-evaluator`` runs whose
model wrote ``calc.py`` with a bash heredoc: the undo journal showed no file
change, ``extra.finish`` was null, the check never ran, and the one-argument
``min`` / ``max`` point it exists to catch went unchecked.

Now the check also compares the session folder as the reply BEGAN with the
folder as it is when the model is about to end it (``harness/folder_changes``)
— beside the journal, never instead of it. Driven through the REAL lanes on a
real platform (registry, permission engine, cards, ledger, journal and the
shell tool are the real ones; a command really runs); only the model is
scripted. MIRROR NOTE (lock-step): every behaviour a POST answer can show is
pinned on both lanes.

Each "it now sees" pin FAILS on the code before this change (v1.386.2): the
check never ran, or its note did not list or read what a command did — the
docstring of each says which. The guard pins (a reply that changed nothing,
caches and checker droppings, a reply that ran no command) pass on the old
code and are mutation-checked against the new one.
"""

from __future__ import annotations

import logging
import sys
import threading

import pytest

from iron_jarvis.daemon.chat_stream import stream_chat_turn
from iron_jarvis.daemon.chat_turn import run_chat_turn
from iron_jarvis.harness import finish as F
from iron_jarvis.harness import folder_changes as FC
from iron_jarvis.personas.builtins import BUILTIN_PERSONAS
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ProviderError, ToolCall
from tests.test_finish_lanes_v1386 import (  # noqa: F401 — the fixture
    NOTE_HEAD,
    PASSING,
    SESSION,
    TEST_CMD,
    Model,
    _body,
    _calls_seen,
    _finish_rows,
    _frames,
    _lane,
    _shell,
    _stream,
    _write,
    env,
)

FILES_HEAD = "## Files this reply made that the request does not name\n"


@pytest.fixture(autouse=True)
def _no_kept_snapshots():
    FC.forget_kept()
    yield
    FC.forget_kept()


def _py(code: str) -> str:
    """A `python -c` command both cmd.exe and bash read the same way (the
    code uses single quotes only)."""
    return f'python -c "{code}"'


def _files(note: str) -> str:
    """The note's files section (it holds no blank line)."""
    return note.split(FILES_HEAD, 1)[1].split("\n\n", 1)[0]


def _gone(note: str) -> str:
    if F.GONE_HEAD not in note:
        return ""
    return note.split(F.GONE_HEAD + "\n", 1)[1].split("\n\n", 1)[0]


def _met(model: Model) -> str:
    note = model.calls[-1]["messages"][-1].content
    assert note.startswith(NOTE_HEAD)
    return "1. met: checked.\nNothing needed changing."


# --------------------------------------------------------------------------- #
# it sees what a command did
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_file_only_a_command_changed_makes_a_reply_that_changed_files(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """The lost runs' shape: no file tool, a command rewrites the file. OLD
    CODE: no note, no extra round, no ``finish`` (the journal was empty)."""
    platform, ws, wire = env
    model = wire([_shell(_py("open('app.py', 'w').write('X = 2\\n')")), _shell(TEST_CMD, "s2"),
                  "Set X to 2.", _met])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch)
    assert len(model.calls) == 4 and len(model.notes()) == 1
    note = model.notes()[0]
    assert f"`{TEST_CMD}` (the test part of a command you ran in this reply) passed (exit code 0)." in note
    # app.py was CHANGED, not made: nothing to list — and the folder was
    # compared, so "None." is a claim the check can make (no caveat).
    assert _files(note) == "None."
    assert "## The request, point by point\n1. set X to 2 in app.py and run the tests" in note
    assert done["finish"] == {"tests": "passed", "command": TEST_CMD, "removed": 0, "kept": 0,
                              "points": 1, "unmet": 0, "after": ""}
    assert [r.tool for r in _finish_rows(platform)] == ["shell"]


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_files_a_command_made_are_listed_apart_and_a_named_one_is_left_out(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """A file no file tool made is listed APART from the journal's, in words
    that ask to keep what a program wrote (a server's log) and remove only a
    scratch file the model wrote itself; a file the request names
    (status.json) is never listed. OLD CODE: only the journal's file, and the
    caveat that a command may have made others."""
    platform, ws, wire = env
    make = _py("open('status.json', 'w').write('{}'); open('server.log', 'w').write('started');"
               " open('_scratch.py', 'w').write('print(1)')")
    model = wire([_write("app.py", "X = 2\n"), _write("notes.md", "- x\n", "w2"), _shell(make), "Done.", _met])
    text = "set X to 2 in app.py and save the server's status to status.json"
    done, _ = await _lane(lane, platform, _body(ws, text=text), monkeypatch)
    files = _files(model.notes()[0])
    assert "- `_scratch.py`" in files and "- `server.log`" in files and "status.json" not in files
    assert files == (
        "- `notes.md`\nKeep each one only if the work needs it; remove the rest with apply_patch "
        "(\"*** Delete File: <path>\"), which can be undone. A scratch script or test file left in "
        "the folder can break the project's own test run.\n"
        + F.APPEARED_ALSO_HEAD + "\n- `_scratch.py`\n- `server.log`\n" + F.APPEARED_KEEP
    )
    # The record counts the FILE TOOLS' files only (the journal list).
    assert done["finish"]["kept"] == 1 and done["finish"]["removed"] == 0


async def test_a_file_a_command_removed_is_listed_as_gone_and_a_move_is_neither(env):  # noqa: F811 — the fixture
    """A file that was there when the reply began and is gone (the v1.386.2
    `rm -f server.port; python server.py` case deleted server.py) is named;
    a MOVE (a watcher sending inbox/a.csv to done/) is neither made nor gone
    — "put it back" would undo the task. OLD CODE: nothing about either."""
    platform, ws, wire = env
    (ws / "keep.txt").write_text("the user's\n", encoding="utf-8")
    (ws / "inbox").mkdir()
    (ws / "done").mkdir()
    (ws / "inbox" / "a.csv").write_text("amount\n1\n", encoding="utf-8")
    gone = _py("import os; os.remove('keep.txt'); os.replace('inbox/a.csv', 'done/a.csv')")
    model = wire([_write("app.py", "X = 2\n"), _shell(gone), "Done.", _met])
    await _stream(platform, _body(ws))
    note = model.notes()[0]
    assert _gone(note) == "- `keep.txt`\n" + F.GONE_ASK
    assert "a.csv" not in note
    assert _files(note) == "None."


async def test_the_description_is_read_as_it_was_before_a_command_changed_it(env):  # noqa: F811 — the fixture
    """The request points to calc.py's docstring; a COMMAND rewrote it (no
    journal row). OLD CODE: the check (run here by a file-tool write beside
    it) read the docstring as it is NOW, so the original rule was lost."""
    platform, ws, wire = env
    (ws / "calc.py").write_text(
        '"""Calculator.\n\nRules:\n\n- ``min`` and ``max`` take one or more arguments.\n"""\nX = 1\n',
        encoding="utf-8",
    )
    rewrite = _py("open('calc.py', 'w').write(chr(34)*3 + 'Calculator.' + chr(34)*3 + chr(10) + 'X = 2' + chr(10))")
    model = wire([_write("other.py", "Y = 1\n"), _shell(rewrite), "Done.", "1. met."])
    await _stream(platform, _body(ws, text="Implement calc.py exactly as its docstring describes."))
    note = model.notes()[0]
    assert "one or more arguments" not in (ws / "calc.py").read_text(encoding="utf-8")
    assert "``min`` and ``max`` take one or more arguments." in note
    assert "The request points to the docstring of `calc.py`" in note


async def test_a_folder_check_applies_to_a_file_a_command_changed(env):  # noqa: F811 — the fixture
    """`[[checks]] files = ["*.py"]` runs when a COMMAND changed a .py file.
    OLD CODE: no note at all."""
    platform, ws, wire = env
    hd = ws / ".ironjarvis" / "harness"
    hd.mkdir(parents=True)
    (hd / "plugins.toml").write_text(f'[[checks]]\nfiles = ["*.py"]\ncommand = "{TEST_CMD}"\n', encoding="utf-8")
    model = wire([_shell(_py("open('app.py', 'w').write('X = 2\\n')")), "Done.", "1. met."])
    out = await _stream(platform, _body(ws), answer=lambda f: "once")
    assert "(the folder's own check) passed" in model.notes()[0]
    assert out["done"]["finish"]["tests"] == "passed"


async def test_removed_and_kept_still_count_only_the_files_the_file_tools_made(env):  # noqa: F811 — the fixture
    """"1 scratch file removed" still means a file the reply made with its
    file tools and then removed: a file that APPEARED while commands ran may
    be a program's (or the user's), so its removal is never counted. OLD
    CODE: the command-made file was never listed."""
    platform, ws, wire = env

    def answer(model: Model):
        files = _files(model.calls[-1]["messages"][-1].content)
        assert "- `scratch.py`" in files and "- `cmd_scratch.py`" in files
        patch = "*** Begin Patch\n*** Delete File: scratch.py\n*** Delete File: cmd_scratch.py\n*** End Patch"
        return ("1. met.", [ToolCall(id="rm", name="apply_patch", arguments={"patch": patch})])

    wire([_write("app.py", "X = 2\n"), _write("scratch.py", "print(1)\n", "w2"),
          _shell(_py("open('cmd_scratch.py', 'w').write('print(2)')")), "Done.", answer, "Removed both."])
    out = await _stream(platform, _body(ws, text="set X to 2 in app.py"))
    assert not (ws / "scratch.py").exists() and not (ws / "cmd_scratch.py").exists()
    assert out["done"]["finish"]["removed"] == 1 and out["done"]["finish"]["kept"] == 0


# --------------------------------------------------------------------------- #
# it never counts what is not the reply's change
# --------------------------------------------------------------------------- #


REAL_START = F.start


async def _no_start(**_kw):
    return None


@pytest.mark.parametrize("bytecode", ["on", "off"])
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_reply_that_read_and_ran_its_tests_changed_nothing(tmp_path, lane, bytecode, monkeypatch):
    """The test run writes ``__pycache__`` (bytecode on — CI's setting; this
    PC sets PYTHONDONTWRITEBYTECODE, so both are run here) and pytest's
    ``.pytest_cache``: never a change. The model is sent the SAME calls,
    byte for byte, as with no finish check at all, and nothing is noted."""
    if bytecode == "on":
        monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    else:
        monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    pytest_cmd = f'"{sys.executable}" -m pytest -q -p no:xdist -p no:randomly test_mini.py'
    ws = tmp_path / "ws"

    async def run(name: str, old: bool):
        platform = build_platform(str(tmp_path / name))
        ws.mkdir(exist_ok=True)
        (ws / "app.py").write_bytes(b"X = 1\n")
        (ws / "test_mini.py").write_bytes(PASSING.replace("2)", "1)").encode())
        model = Model([[ToolCall(id="r1", name="read_file", arguments={"path": "app.py"})],
                       _shell(pytest_cmd), "X is 1 and the test passes."])
        monkeypatch.setattr(platform.router, "stream", model.stream)
        monkeypatch.setattr(platform.router, "complete", model.complete)
        monkeypatch.setattr(F, "start", _no_start if old else REAL_START)
        try:
            done, _ = await _lane(lane, platform, _body(ws, text="what is X in app.py?"), monkeypatch)
        finally:
            monkeypatch.setattr(F, "start", REAL_START)
        return platform, model, done

    platform, new_model, new_done = await run("home-new", old=False)
    # The test run really left its caches in the folder.
    assert (ws / ".pytest_cache").is_dir()
    assert (ws / "__pycache__").is_dir() is (bytecode == "on")
    _old, old_model, old_done = await run("home-old", old=True)
    assert "1 passed" in str(new_model.calls[-1]["messages"][-1].content)
    assert _calls_seen(new_model) == _calls_seen(old_model)
    assert len(new_model.calls) == 3 and new_model.notes() == []
    assert "finish" not in new_done and new_done["reply"] == old_done["reply"]
    assert _finish_rows(platform) == []


async def test_checker_and_tool_droppings_are_never_changes(env):  # noqa: F811 — the fixture
    """Caches, coverage output, what .gitignore ignores — and what a
    project's own checkers leave (part 3 Phase 3 runs ruff / mypy / eslint /
    tsc after an edit): tsc's ``*.tsbuildinfo`` (written under --noEmit too
    with `incremental`), mypy's and ruff's cache folders, eslint's cache, the
    mypy daemon's file. A reply whose command made only those changed
    nothing: no note, exactly the calls of no check at all."""
    platform, ws, wire = env
    (ws / ".gitignore").write_text("*.log\nout/\n", encoding="utf-8")
    plant = [
        "run.log", "out/bundle.js", "__pycache__/app.cpython-312.pyc", ".pytest_cache/v/x",
        ".mypy_cache/3.12/app.data.json", ".ruff_cache/0.6/123", "node_modules/.cache/x",
        ".next/build-manifest.json", ".coverage", ".coverage.host.1.2", "coverage.xml",
        "htmlcov/index.html", "pkg.egg-info/PKG-INFO", "stray.pyc", "tsconfig.tsbuildinfo",
        "web/app.tsbuildinfo", ".eslintcache", ".dmypy.json",
    ]
    code = "import os; [os.makedirs(os.path.dirname(p) or '.', exist_ok=True) or open(p, 'w').write('x') for p in " \
           + repr(plant).replace('"', "'") + "]"
    model = wire([_shell(_py(code)), "Done."])
    out = await _stream(platform, _body(ws, text="build it"))
    assert all((ws / p).exists() for p in plant)
    assert model.notes() == [] and "finish" not in out["done"] and len(model.calls) == 2


async def test_a_reply_that_ran_nothing_that_writes_ignores_the_folder(env, monkeypatch):  # noqa: F811 — the fixture
    """The user's editor saves a file while a reply only READS: not the
    reply's change (no command, no hand-off ran) — no note, and the folder is
    not even walked a second time."""
    platform, ws, wire = env
    takes: list[str] = []
    real = FC.take
    monkeypatch.setattr(FC, "take", lambda folder, **kw: (takes.append(str(folder)), real(folder, **kw))[1])

    def users_editor(_model):
        (ws / "user_edit.txt").write_text("typed by the user\n", encoding="utf-8")
        (ws / "app.py").write_bytes(b"X = 7\n")
        return "X is 1."

    model = wire([[ToolCall(id="r", name="read_file", arguments={"path": "app.py"})], users_editor])
    out = await _stream(platform, _body(ws, text="what is X in app.py?"))
    assert model.notes() == [] and "finish" not in out["done"]
    assert len(takes) == 1, "the folder was walked again for a reply that ran nothing that writes"


async def test_a_project_checks_call_is_never_the_replys_work(tmp_path):
    """Part 3 Phase 3 runs a project's checkers (ruff / mypy / eslint / tsc)
    after an edit as synthetic calls of the turn's command tool, ledgered
    ``called_as == "project check"``: a checker is not the reply's command —
    it neither opens the folder comparison nor becomes the test command, in
    the reply (``observe``) or in the cut part a carry-on reads (the ledger)."""
    import json

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ToolInvocation
    from iron_jarvis.harness.mode import HarnessTurn
    from iron_jarvis.tools.base import ToolResult

    platform = build_platform(str(tmp_path / "home"))
    # The real HarnessTurn's own marking (how a harness-made call is named),
    # without building a whole turn.
    turn = HarnessTurn.__new__(HarnessTurn)
    turn._called_as, turn._marked_syntax, turn._called_tool = {}, {}, {}
    turn.mark_called_as("pc-1", F.PROJECT_CHECK_CALLED_AS)
    check = F.FinishCheck(engine=platform.engine, home=tmp_path, event_bus=None, session_id=SESSION,
                          folder=tmp_path, mark=0, request="x", harness=turn)
    ran = ToolResult(ok=True, output="ok", data={"returncode": 0})
    check.observe(ToolCall(id="pc-1", name="shell", arguments={"command": "npx tsc --noEmit"}), ran, refused=False)
    assert check._may_write is False and check._commands == []
    check.observe(ToolCall(id="m-1", name="shell", arguments={"command": "pytest"}), ran, refused=False)
    assert check._may_write is True and [c[0] for c in check._commands] == ["shell"]

    with session_scope(platform.engine) as db:
        db.add(ToolInvocation(session_id=SESSION, agent_run_id="r", tool="shell", ok=True,
                              args_json=json.dumps({"command": "ruff check ."}), confinement="native",
                              called_as=F.PROJECT_CHECK_CALLED_AS))
        db.commit()
    upto = F.ledger_mark(platform.engine)
    assert F.ran_unjournaled(platform.engine, SESSION, 0, upto) is False
    assert F.ran_commands(platform.engine, SESSION, 0, upto) == []
    # A model's shell call the tool REFUSED before anything ran carries no
    # confinement: it changed nothing either.
    with session_scope(platform.engine) as db:
        db.add(ToolInvocation(session_id=SESSION, agent_run_id="r", tool="shell", ok=False,
                              args_json=json.dumps({"command": "git push -f"}), confinement=None, called_as="Bash"))
        db.commit()
    upto = F.ledger_mark(platform.engine)
    assert F.ran_unjournaled(platform.engine, SESSION, 0, upto) is False
    with session_scope(platform.engine) as db:
        db.add(ToolInvocation(session_id=SESSION, agent_run_id="r", tool="shell", ok=True,
                              args_json=json.dumps({"command": "pytest"}), confinement="native", called_as="Bash"))
        db.commit()
    upto = F.ledger_mark(platform.engine)
    assert F.ran_unjournaled(platform.engine, SESSION, 0, upto) is True
    assert [a["command"] for _t, a, _c in F.ran_commands(platform.engine, SESSION, 0, upto)] == ["pytest"]


# --------------------------------------------------------------------------- #
# a folder that cannot be compared: the journal alone, and said
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_folder_too_big_falls_back_to_the_journal_and_says_so(env, lane, monkeypatch):  # noqa: F811 — the fixture
    """More files than list_files' cap: never a half picture — the journal
    alone, and the note says why. OLD CODE: the plain caveat, no reason."""
    platform, ws, wire = env
    monkeypatch.setattr(FC, "_caps", lambda: (3, 10.0))
    for name in ("a.txt", "b.txt"):
        (ws / name).write_text("x", encoding="utf-8")
    model = wire([_write("app.py", "X = 2\n"), _shell(_py("open('made.py', 'w').write('1')")), "Done.", "1. met."])
    await _lane(lane, platform, _body(ws), monkeypatch)
    assert _files(model.notes()[0]) == "None.\n" + F.FinishCheck.CANNOT_COMPARE.format(
        why="this folder has more than 3 files to compare"
    )


async def test_with_the_journal_empty_a_folder_too_big_ends_the_reply_as_before_and_logs_why(
        env, monkeypatch, caplog):  # noqa: F811 — the fixture
    platform, ws, wire = env
    monkeypatch.setattr(FC, "_caps", lambda: (1, 10.0))
    caplog.set_level(logging.INFO, logger="iron_jarvis.harness.finish")
    model = wire([_shell(_py("open('app.py', 'w').write('X = 2\\n')")), "Done."])
    out = await _stream(platform, _body(ws))
    assert model.notes() == [] and "finish" not in out["done"]
    assert "finish check: this folder has more than 1 files to compare" in caplog.text


async def test_a_folder_that_grew_past_the_cap_during_the_reply_is_said_too(env, monkeypatch, caplog):  # noqa: F811 — the fixture
    """The first snapshot was whole; the reply's command then made more files
    than the cap — the second walk stops, and the comparison is NOT made from
    a half picture (the files it did see would read as the only changes)."""
    platform, ws, wire = env
    monkeypatch.setattr(FC, "_caps", lambda: (5, 10.0))
    caplog.set_level(logging.INFO, logger="iron_jarvis.harness.finish")
    grow = _py("[open('gen%d.txt' % i, 'w').write('x') for i in range(6)]")
    model = wire([_write("app.py", "X = 2\n"), _shell(grow), "Done.", "1. met."])
    await _stream(platform, _body(ws))
    assert _files(model.notes()[0]) == "None.\n" + F.FinishCheck.CANNOT_COMPARE.format(
        why="this folder has more than 5 files to compare"
    )
    # ...and with the journal empty, the reply ends as before and the log says
    # why (the folder starts small again, and grows past the cap).
    (ws / "app.py").write_bytes(b"X = 1\n")
    for p in ws.glob("gen*.txt"):
        p.unlink()
    model = wire([_shell(_py("[open('more%d.txt' % i, 'w').write('x') for i in range(4)]")), "Done."])
    out = await _stream(platform, _body(ws))
    assert model.notes() == [] and "finish" not in out["done"]
    assert caplog.text.count("finish check: this folder has more than 5 files to compare") == 2


async def test_a_walk_past_its_deadline_is_said_too(env, monkeypatch):  # noqa: F811 — the fixture
    platform, ws, wire = env
    monkeypatch.setattr(FC, "_caps", lambda: (5000, 0.0))
    model = wire([_write("app.py", "X = 2\n"), _shell(_py("open('made.py', 'w').write('1')")), "Done.", "1. met."])
    await _stream(platform, _body(ws))
    assert "reading this folder took longer than 0 s, so the files a command made could not be found" in (
        _files(model.notes()[0])
    )


# --------------------------------------------------------------------------- #
# when the snapshot is taken, and where never
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_the_snapshot_is_taken_off_the_loop_before_any_tool_runs(env, lane, monkeypatch):  # noqa: F811 — the fixture
    platform, ws, wire = env
    loop_thread = threading.get_ident()
    seen: list[tuple[int, int]] = []
    real = FC.take

    def spy(folder, **kw):
        seen.append((threading.get_ident(), F.ledger_mark(platform.engine)))
        return real(folder, **kw)

    monkeypatch.setattr(FC, "take", spy)
    wire([_shell(_py("open('app.py', 'w').write('X = 2\\n')")), "Done.", "1. met."])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch)
    assert "finish" in done
    assert len(seen) == 2, seen  # when the reply began, and at its end
    assert all(ident != loop_thread for ident, _rows in seen), "a folder walk ran on the event loop"
    assert seen[0][1] == 0, "the first snapshot was taken after a tool of the reply had run"


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_plain_chat_a_chat_session_and_plan_mode_take_no_snapshot(env, lane, monkeypatch):  # noqa: F811 — the fixture
    platform, ws, wire = env
    takes: list[str] = []
    monkeypatch.setattr(FC, "take", lambda folder, **kw: takes.append(str(folder)))
    cmd = _py("open('app.py', 'w').write('X = 2\\n')")
    # Plain chat: no harness at all.
    model = wire([_shell(cmd), "Done."])
    body = _body(ws, text="set X to 2 in app.py", tools=["shell"])
    if lane == "stream":
        gen = await stream_chat_turn(platform, BUILTIN_PERSONAS, body)
        done = [d for e, d in _frames([c async for c in gen]) if e == "done"][0]
    else:
        monkeypatch.setitem(platform.permissions._base, "shell", "allow")
        done = await run_chat_turn(platform, BUILTIN_PERSONAS, body)
    assert model.notes() == [] and "finish" not in done
    # A harness turn under the chat's own session id.
    model = wire([_shell(cmd), "Done."])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch, session_id="chat")
    assert model.notes() == [] and "finish" not in done
    # A Build session in plan mode.
    model = wire(["A plan."])
    done, _ = await _lane(lane, platform, _body(ws), monkeypatch, plan_mode=True)
    assert model.notes() == [] and "finish" not in done
    assert takes == []


# --------------------------------------------------------------------------- #
# a carried-on reply compares with the folder as THAT reply began
# --------------------------------------------------------------------------- #


async def _cut_then_carry_on(platform, ws, wire, *, forget: bool, carry_moves: list):
    import uuid

    from iron_jarvis.daemon.routes.build import CONTINUE_TEXT
    from iron_jarvis.daemon.schemas import ChatMessageBody
    from iron_jarvis.harness import sessions as S

    t1, t2 = f"t1-{uuid.uuid4().hex[:8]}", f"t2-{uuid.uuid4().hex[:8]}"
    row = S.create(platform.engine, str(ws), "t")
    S.append_lines(platform.engine, row.id, [{"role": "user", "content": "set X to 2 in app.py", "turnId": t1}])
    sid = S.session_id_for(row.id)
    # The cut part: a COMMAND writes app.py and a scratch file, then the
    # account's limit ends the turn.
    write = _py("open('app.py', 'w').write('X = 2\\n'); open('cut_scratch.py', 'w').write('print(1)')")
    wire([_shell(write, "c1"), ProviderError("limit")])
    await _stream(platform, _body(ws, text="set X to 2 in app.py", thread_id=row.id, turn_id=t1), session_id=sid)
    S.append_lines(platform.engine, row.id, [
        {"role": "assistant", "content": "", "turnId": t1, "error": "limit"},
        {"role": "user", "content": CONTINUE_TEXT, "turnId": t2, "continuation": True},
    ])
    if forget:
        FC.forget_kept()  # what a restart of Iron Jarvis does to the kept snapshot
    model = wire(carry_moves)
    body = _body(ws, thread_id=row.id, turn_id=t2)
    body.messages = [ChatMessageBody(role="user", content="set X to 2 in app.py"),
                     ChatMessageBody(role="user", content=CONTINUE_TEXT)]
    out = await _stream(platform, body, session_id=sid)
    return model, out


async def test_a_carry_on_compares_with_the_folder_as_the_cut_reply_began(env):  # noqa: F811 — the fixture
    """Resume when ready / Continue on another account: the cut part wrote
    with a COMMAND; the carry-on ran nothing. Its check covers the whole
    reply — against the snapshot taken when the CUT reply began. OLD CODE: no
    note (the journal is empty in both parts)."""
    platform, ws, wire = env
    model, out = await _cut_then_carry_on(platform, ws, wire, forget=False, carry_moves=["Done.", "1. met."])
    note = model.notes()[0]
    assert _files(note) == "None made with your file tools. " + F.APPEARED_HEAD + "\n- `cut_scratch.py`\n" \
        + F.APPEARED_KEEP
    assert out["done"]["finish"]["points"] == 1


async def test_a_carry_on_after_a_restart_falls_back_to_the_journal_and_says_so(env):  # noqa: F811 — the fixture
    platform, ws, wire = env
    model, out = await _cut_then_carry_on(
        platform, ws, wire, forget=True, carry_moves=[_write("notes.md", "- x\n", "k1"), "Done.", "1. met."],
    )
    files = _files(model.notes()[0])
    assert "- `notes.md`" in files
    assert F.FinishCheck.CANNOT_COMPARE.format(why=FC.PROBLEM_NOT_KEPT) in files
    assert "cut_scratch.py" not in files
    # ...and with nothing the journal knows, it ends as before.
    FC.forget_kept()
    (ws / "app.py").write_bytes(b"X = 1\n")
    model, out = await _cut_then_carry_on(platform, ws, wire, forget=True, carry_moves=["Done."])
    assert model.notes() == [] and "finish" not in out["done"]
