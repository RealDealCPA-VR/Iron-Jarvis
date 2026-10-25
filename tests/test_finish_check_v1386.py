"""IJ harness part 3, Phase 2 — THE FINISH CHECK: the pure parts.

``harness/finish`` decides, at the end of a Build reply that changed files,
what one note tells the model: the tests (the folder's own checks, else the
TEST PART of the last command the model ran that looks like a test run), the
files the reply made that the request does not name, and the request as a
checklist of its points (plus the written description it points to). These
pins hold the rules themselves; ``test_finish_lanes_v1386.py`` drives them
through both chat lanes, ``test_finish_replay_v1386.py`` replays the two runs
the benchmark lost.

Every pin here fails on the code before Phase 2 (there was no
``harness/finish`` module, no ``finish.toml`` and no record).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import iron_jarvis
from iron_jarvis.harness import components as C
from iron_jarvis.harness import finish as F

TASK = Path(iron_jarvis.__file__).parent / "harness_bench" / "tasks" / "hard-expression-evaluator"


# --------------------------------------------------------------------------- #
# the test command: the detection rule
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "command, part",
    [
        # the plan's list
        ("pytest", "pytest"),
        ("pytest -q tests/test_cart.py", "pytest -q tests/test_cart.py"),
        ("py.test -x", "py.test -x"),
        ("python -m pytest -q", "python -m pytest -q"),
        ("python3 -m pytest", "python3 -m pytest"),
        ("py -3 -m pytest", "py -3 -m pytest"),
        ("uv run pytest -q", "uv run pytest -q"),
        ("uv run --with pytest pytest -q", "uv run --with pytest pytest -q"),
        ("poetry run pytest", "poetry run pytest"),
        ("npm test", "npm test"),
        ("npm t", "npm t"),
        ("npm run test:unit -- --watch=false", "npm run test:unit -- --watch=false"),
        ("pnpm test", "pnpm test"),
        ("yarn test", "yarn test"),
        ("go test ./...", "go test ./..."),
        ("cargo test --all", "cargo test --all"),
        ("dotnet test", "dotnet test"),
        ("mvn -q test", "mvn -q test"),
        ("./gradlew test", "./gradlew test"),
        ("gradle clean test", "gradle clean test"),
        ("node --test", "node --test"),
        # python -m unittest (the standard library's runner — both lost runs used it)
        ("python -m unittest -q test_calc", "python -m unittest -q test_calc"),
        ("python -m unittest discover -s tests", "python -m unittest discover -s tests"),
        # the test PART of a longer command: setup kept, the rest dropped
        ('cd "C:\\w" && python -m unittest -q test_calc 2>&1 | tail -3 && python -c "print(1)"',
         'cd "C:\\w" && python -m unittest -q test_calc 2>&1'),
        ('cd "C:\\w" && rm _check.py && python -m unittest -q test_calc 2>&1 | tail -1',
         'cd "C:\\w" && python -m unittest -q test_calc 2>&1'),
        ("python test_spec.py; echo ---; python -m unittest test_calc -v", "python -m unittest test_calc -v"),
        ("git commit -am wip && pytest", "pytest"),
        ("pytest | tail -3; git push", "pytest"),
        ("FOO=1 pytest -q", "FOO=1 pytest -q"),
        ("set PYTHONPATH=src && pytest", "set PYTHONPATH=src && pytest"),
        (".venv\\Scripts\\activate && pytest", ".venv\\Scripts\\activate && pytest"),
        ("pytest a && pytest b", "pytest a && pytest b"),
    ],
)
def test_a_test_command_is_found_and_only_its_test_part_is_kept(command, part):
    assert F.test_part(command, posix=False) == part


@pytest.mark.parametrize(
    "command",
    [
        "python test_spec.py",  # a script of its own: not the rule's
        "python -c \"import unittest\"",
        "python -m py_compile calc.py",
        "pnpm build",
        "npm install",
        "npm run build",
        "go build ./...",
        "cargo build",
        "git status",
        "echo pytest",
        "rm -rf build",
        "cat test_calc.py",
        "",
    ],
)
def test_a_command_that_runs_no_tests_has_no_test_part(command):
    assert F.test_part(command, posix=False) == ""


def test_a_redirection_is_not_a_separator_and_a_lone_ampersand_is():
    assert F._segments("pytest 2>&1", posix=False) == ["pytest 2>&1"]
    assert F._segments("echo a & pytest", posix=False) == ["echo a", "pytest"]
    assert F._segments('python -c "a; b" && pytest', posix=False) == ['python -c "a; b"', "pytest"]


def test_a_command_the_folders_docs_name_counts(tmp_path):
    (tmp_path / "README.md").write_text(
        "# demo\n\nRun the tests with `./check.sh --all`.\n\n"
        "Install the test tools first with `pip install -r dev.txt`.\n\n"
        "To test:\n\n```\n$ make check\n```\n\nThe build: `make dist`.\n",
        encoding="utf-8",
    )
    named = F.doc_test_commands(tmp_path)
    assert "./check.sh --all" in named and "make check" in named
    assert "pip install -r dev.txt" not in named, "an install step is not a test run"
    assert "make dist" not in named, "a line that does not mention tests names no test command"
    assert F.test_part("make check", named, posix=False) == "make check"
    assert F.test_part("./check.sh --all -v", named, posix=False) == "./check.sh --all -v"
    assert F.test_part("make dist", named, posix=False) == ""


# --------------------------------------------------------------------------- #
# the request as points, and what it points to
# --------------------------------------------------------------------------- #


def _bench_prompt(folder: str = "C:\\work") -> str:
    from iron_jarvis.harness_bench.runners import PROMPT_TEMPLATE

    task = (TASK / "task.md").read_text(encoding="utf-8").strip()
    return PROMPT_TEMPLATE.format(task=task, folder=folder)


def test_the_request_becomes_its_points_in_order():
    points, left = F.request_points(_bench_prompt())
    assert left == 0
    assert points[0].startswith("Implement `evaluate(expression, variables=None)`")
    assert points[1].startswith("Do not use `eval`")
    assert "the docstring is the full specification" in points[2]
    assert any(p.startswith("Work only inside this folder") for p in points)
    assert points[-1] == "When you are finished, reply with a short summary of what you did."


def test_list_items_and_code_are_their_own_points():
    points, _ = F.request_points(
        "Please do three things:\n- add a --dry-run flag\n- update the help text\n"
        "1. keep the old flag working\n\n```python\nprint('hi')\n```\nThanks, e.g. soon."
    )
    assert points[:4] == [
        "Please do three things:", "add a --dry-run flag", "update the help text", "keep the old flag working",
    ]
    assert points[4].startswith("The code block in the request (print('hi')")
    assert points[5] == "Thanks, e.g. soon."


def test_the_docstring_the_request_points_to_puts_the_one_argument_point_in():
    source = (TASK / "repo" / "calc.py").read_text(encoding="utf-8")
    points, left = F.spec_points(F.module_docstring(source))
    assert left == 0
    names = [p for p in points if p.startswith("NAMES:")]
    assert names and "``min`` and ``max`` (one or more arguments)" in names[0]
    # The grammar block rides with the line that introduces it.
    assert any(p.startswith("GRAMMAR, lowest precedence first::") and "atom" in p for p in points)
    # Each error rule is its own point.
    assert sum(1 for p in points if "the index" in p) >= 4


def test_a_docstring_is_read_by_parsing_never_by_running(tmp_path):
    marker = tmp_path / "ran.txt"
    source = f'"""The spec."""\nopen({str(marker)!r}, "w").write("x")\n'
    assert F.module_docstring(source) == "The spec."
    assert not marker.exists()
    assert F.module_docstring("def broken(:\n") == ""


def test_the_request_text_skips_a_carry_on_message():
    class M:
        def __init__(self, role, content):
            self.role, self.content = role, content

    msgs = [M("user", "fix the cart total"), M("assistant", "…"), M("user", "continue")]
    assert F.request_text(msgs) == "fix the cart total"
    msgs = [M("user", "fix the cart total"), M("user", "Carry on with the task above.")]
    assert F.request_text(msgs, carry_on=["Carry on with the task above."]) == "fix the cart total"
    assert F.request_text([M("user", "make hello.py")]) == "make hello.py"


@pytest.mark.parametrize(
    "rel, asked, named",
    [
        ("test_spec.py", "Implement calc.py; test_calc.py shows cases", False),
        ("test_calc.py", "Implement calc.py; `test_calc.py` shows cases", True),
        ("src/report.md", "write src/report.md with the totals", True),
        ("src/report.md", "write report.md with the totals", True),
        ("notes.md", "write the notes.md.", True),
        ("_fix.py", "fix the bug", False),
    ],
)
def test_a_file_the_request_names_is_not_a_scratch_file(rel, asked, named):
    assert F._named_in(rel, asked) is named


# --------------------------------------------------------------------------- #
# .gitignore
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "rel, ignored",
    [
        ("build/out.txt", True),
        ("src/build/out.txt", True),
        ("debug.log", True),
        ("logs/keep.log", False),  # negated
        ("node_modules/x/index.js", True),
        ("dist", False),  # a FILE named like the dir-only rule
        ("dist/app.js", True),
        ("scratch/a.py", True),
        ("lib/scratch/a.py", False),  # anchored: only the top folder
        ("test_spec.py", False),
        ("docs/notes.tmp", True),
    ],
)
def test_the_folders_gitignore_decides(tmp_path, rel, ignored):
    (tmp_path / ".gitignore").write_text(
        "# comment\nbuild/\n*.log\n!logs/keep.log\nnode_modules\ndist/\n/scratch/\n**/*.tmp\n",
        encoding="utf-8",
    )
    rules = F.gitignore_rules(tmp_path)
    assert F.ignored(rel, rules) is ignored


# --------------------------------------------------------------------------- #
# the model's answer, the record
# --------------------------------------------------------------------------- #


def test_the_answer_is_read_point_by_point():
    text = (
        "1. met: the parser is hand-written\n"
        "2. not met: min(4) raises TypeError\n"
        "**3.** met — the error positions match\n"
        "- 4. Not met: trailing comma\n"
        "5) done\n"
        "12. not met: out of range\n"
        "Done."
    )
    met, unmet = F.answered_points(text, 5)
    assert met == {1, 3, 5} and unmet == {2, 4}


def test_the_saved_record_keeps_only_the_known_keys():
    raw = {"tests": "failed", "command": "pytest", "removed": 1, "kept": 0, "points": 9,
           "unmet": 2, "after": "passed", "why": "x", "secret": "no", "output": "huge"}
    assert F.saved_record(raw) == {"tests": "failed", "command": "pytest", "removed": 1, "kept": 0,
                                   "points": 9, "unmet": 2, "after": "passed"}
    assert F.saved_record({"tests": "not_run", "why": "you declined it"})["why"] == "you declined it"
    assert F.saved_record({"tests": "maybe"}) is None
    assert F.saved_record(None) is None
    assert F.saved_record({"tests": "passed", "removed": -1, "kept": True})["removed"] == 0


# --------------------------------------------------------------------------- #
# the folder's switch (finish.toml)
# --------------------------------------------------------------------------- #


def _layer(tmp_path: Path, finish: str) -> C.ProjectLayer:
    hd = tmp_path / ".ironjarvis" / "harness"
    hd.mkdir(parents=True, exist_ok=True)
    (hd / "finish.toml").write_text(finish, encoding="utf-8")
    return C.read_project_layer(tmp_path)


def test_a_folder_can_turn_the_finish_check_or_a_part_of_it_off(tmp_path):
    layer = _layer(tmp_path, "enabled = false\n")
    assert layer.used and layer.finish == F.FinishConfig(enabled=False)
    tree = C.compose(dialect_section="x", dialect_id="generic", project=layer)
    assert tree.finish.enabled is False
    layer = _layer(tmp_path, "new_files = false\nrequest = false\n")
    assert layer.finish == F.FinishConfig(new_files=False, request=False)


@pytest.mark.parametrize(
    "text, why",
    [
        ("enabled = 'no'\n", "`enabled` must be true or false"),
        ("enabled = false\nrun = true\n", "unknown keys (run)"),
        ("enabled = \n", "not valid TOML"),
    ],
)
def test_a_bad_finish_toml_voids_the_layer_with_one_note(tmp_path, text, why):
    layer = _layer(tmp_path, text)
    assert not layer.used and "finish.toml" in layer.note and why in layer.note


def test_the_default_switch_leaves_the_fingerprint_as_it_was_and_a_change_changes_it(tmp_path):
    tree = C.compose(dialect_section="x", dialect_id="generic")
    rendered = C.render(tree)
    assert tree.finish == F.DEFAULT_CONFIG and tree.finish.mark() == ""
    assert C.fingerprint(rendered, "generic", tree.finish.mark()) == C.fingerprint(rendered, "generic")
    off = C.compose(dialect_section="x", dialect_id="generic", project=_layer(tmp_path, "enabled = false\n"))
    assert C.render(off) == rendered, "the switch renders nothing into the prompt"
    assert C.fingerprint(rendered, "generic", off.finish.mark()) != C.fingerprint(rendered, "generic")


async def test_it_is_never_built_for_plan_mode_plain_chat_or_a_chat_session(tmp_path):
    from types import SimpleNamespace

    harness = SimpleNamespace(plan_mode=False, tree=SimpleNamespace(finish=F.DEFAULT_CONFIG, plugins=None),
                              plan=SimpleNamespace(gate={"shell"}, dialect_tools=[], passthrough=["shell"]))
    kw = dict(engine=None, home=None, event_bus=None, folder=str(tmp_path), messages=[])
    assert await F.start(session_id="build:s1", harness=harness, **kw) is not None
    assert await F.start(session_id="build:s1", harness=None, **kw) is None  # plain chat
    assert await F.start(session_id="chat", harness=harness, **kw) is None
    assert await F.start(session_id="", harness=harness, **kw) is None
    harness.plan_mode = True
    assert await F.start(session_id="build:s1", harness=harness, **kw) is None
    harness.plan_mode = False
    harness.tree.finish = F.FinishConfig(enabled=False)
    assert await F.start(session_id="build:s1", harness=harness, **kw) is None


def test_the_harness_turns_fingerprint_names_a_folder_that_changed_the_switch(tmp_path):
    from iron_jarvis.harness import mode as M
    from iron_jarvis.platform import build_platform

    platform = build_platform(str(tmp_path / "home"))

    def turn(layer):
        return M.build(registry=platform.registry, permissions=platform.permissions, requested="generic",
                       provider="anthropic", model="claude-sonnet-4-5", stream=True, layer=layer, today="")

    plain = turn(C.EMPTY_LAYER)
    off = turn(_layer(tmp_path, "enabled = false\n"))
    assert off.rendered == plain.rendered and off.fingerprint != plain.fingerprint
    assert plain.fingerprint == C.fingerprint(plain.rendered, plain.plan.dialect.id)


# --------------------------------------------------------------------------- #
# which command the model RAN: executed, ended
# --------------------------------------------------------------------------- #


def _check() -> F.FinishCheck:
    return F.FinishCheck(engine=None, home=None, event_bus=None, session_id="build:x", folder=Path("."),
                         mark=0, request="fix it")


def _call(name: str, **args):
    from types import SimpleNamespace

    return SimpleNamespace(name=name, arguments=args, id=name)


def _res(ok: bool = True, **data):
    from types import SimpleNamespace

    return SimpleNamespace(ok=ok, data=data, error=None if ok else "exit 1", output="")


def test_a_refused_command_was_not_run_and_is_never_the_test_command():
    fc = _check()
    fc.observe(_call("shell", command="pytest -q"), _res(False), refused=True)
    fc.observe(_call("shell", command="pytest -q"), None, refused=False)  # a crash: no result
    assert fc._last_test_command() is None
    fc.observe(_call("shell", command="pytest -q -k cart"), _res(False, returncode=1), refused=False)
    assert fc._last_test_command()[2] == "pytest -q -k cart", "a run that FAILED still ran"


def test_a_program_that_never_ended_is_not_the_test_command_until_it_ends():
    fc = _check()
    fc.observe(_call("shell", command="pytest -q"), _res(True, returncode=0), refused=False)
    # a watcher started after it: still running when its start answered
    fc.observe(_call("process_start", command="npm test -- --watch"), _res(True, id="p1", running=True),
               refused=False)
    assert fc._last_test_command()[2] == "pytest -q"
    # a later run that ENDED (its process_read saw the exit) counts
    fc.observe(_call("process_start", command="npm test"), _res(True, id="p2", running=True), refused=False)
    fc.observe(_call("process_read", id="p2"), _res(True, id="p2", running=False, exit_code=1), refused=False)
    assert fc._last_test_command()[2] == "npm test"


@pytest.mark.parametrize(
    "tool, ok, data, verdict",
    [
        ("shell", True, {"returncode": 0, "timed_out": False}, F.TESTS_PASSED),
        ("shell", False, {"returncode": 1, "timed_out": False}, F.TESTS_FAILED),
        # cut off by its time limit: failed, NEVER passed
        ("shell", False, {"returncode": -9, "timed_out": True}, F.TESTS_FAILED),
        ("shell", True, {"returncode": 0, "timed_out": True}, F.TESTS_FAILED),
        # refused inside the tool before anything ran (no exit status): not run
        ("shell", False, {}, F.TESTS_NOT_RUN),
        ("shell", False, {"confinement": "refused", "low_trust": True}, F.TESTS_NOT_RUN),
        ("process_start", True, {"id": "p1", "running": False, "exit_code": 0}, F.TESTS_PASSED),
        ("process_start", True, {"id": "p1", "running": False, "exit_code": 2}, F.TESTS_FAILED),
        ("process_start", False, {}, F.TESTS_NOT_RUN),
    ],
)
async def test_the_verdict_is_the_commands_own_exit_status(tool, ok, data, verdict):
    from types import SimpleNamespace

    fc = _check()
    fc._by_id["ij-finish-1"] = F._Run(command="pytest", args={}, source="yours")
    call = SimpleNamespace(id="ij-finish-1", name=tool)
    result = SimpleNamespace(ok=ok, data=data, error=None if ok else "exit 1", output="")
    await fc.take(call, result, "out", refused=False)
    assert fc._by_id["ij-finish-1"].verdict == verdict


async def test_a_process_still_running_at_its_budget_is_stopped_and_failed(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(F, "_PROCESS_STEP_MS", 10)
    fc = _check()
    fc._by_id["ij-finish-1"] = F._Run(command="pytest", args={}, source="folder", timeout_s=1)
    calls: list[str] = []

    async def invoke(name, args):
        import asyncio

        calls.append(name)
        await asyncio.sleep(0.05)
        return SimpleNamespace(ok=True, output="...", data={"id": "p1", "running": True})

    result = SimpleNamespace(ok=True, data={"id": "p1", "running": True}, output="", error=None)
    await fc.take(SimpleNamespace(id="ij-finish-1", name="process_start"), result, "", refused=False,
                  invoke=invoke)
    run = fc._by_id["ij-finish-1"]
    assert run.verdict == F.TESTS_FAILED and run.timed_out is True
    assert calls[-1] == "process_stop" and "process_read" in calls


# --------------------------------------------------------------------------- #
# the ledger: what the reply changed / made (the undo journal)
# --------------------------------------------------------------------------- #


async def test_the_reply_s_files_are_read_from_its_own_journal_rows(tmp_path):
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.tools.base import ToolContext

    platform = create_app(str(tmp_path / "home")).state.platform
    engine, home = platform.engine, platform.config.home
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "old.txt").write_bytes(b"before\n")

    def ctx(sid):
        return ToolContext(workspace=ws, session_id=sid, agent_run_id=sid, config=platform.config,
                           event_bus=platform.event_bus, engine=engine)

    async def write(sid, path, content):
        res = await platform.registry.invoke(
            "write_file", {"path": path, "content": content}, ctx(sid), platform.permissions,
            {"write_file": "allow"},
        )
        assert res.ok, res.error

    # an EARLIER reply of the same session, and another session: never this reply's
    await write("build:s1", "earlier.txt", "x")
    await write("build:s2", "other.txt", "x")
    mark = F.ledger_mark(engine)
    await write("build:s1", "new.txt", "made")
    await write("build:s1", "old.txt", "after\n")
    files = F.read_turn_files(engine, "build:s1", mark)
    names = sorted(p.name for p in files.changed)
    assert names == ["new.txt", "old.txt"]
    assert [rel for _p, rel in files.created] == ["new.txt"]
    first = files.first[F._key((ws / "old.txt").resolve())]
    assert F.before_bytes(home, first, (ws / "old.txt").resolve()) == b"before\n"
    assert F.read_turn_files(engine, "build:s1", F.ledger_mark(engine)).changed == []
