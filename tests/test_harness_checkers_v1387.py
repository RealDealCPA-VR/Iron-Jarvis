"""IJ harness part 3, Phase 3 — THE PROJECT'S OWN CHECKERS AFTER AN EDIT
(``harness/checkers``; the user's decision 7).

Driven through the REAL lanes on a real platform (registry, permission
engine, cards, ledger, shell tool); only the model is scripted. The checker
programs are real programs: a stand-in ``ruff`` (a script on the session's
PATH that prints findings the way ruff's concise format does) for the
posture pins, and the REAL mypy and tsc for the type-error pins (skipped
cleanly where they are not on this machine — see each skip's reason).

Every "it runs" pin fails on the code before Phase 3 (there were no
checkers: the edit's result said only "Check: the file parses.").
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
from pathlib import Path

import pytest

from iron_jarvis.harness import checkers as C
from iron_jarvis.harness import finish as F
from iron_jarvis.platform import build_platform
from tests._fakes.harness_lane import (
    SESSION,
    body,
    call,
    finished_ids,
    ledger,
    no_finish_check,
    post,
    stream,
    wire,
)

FAKE_RUFF = r'''
import os, sys
log = os.environ.get("FAKE_RUFF_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(" ".join(sys.argv[1:]) + "\n")
files = [a for a in sys.argv[1:] if a.endswith(".py")]
n = int(os.environ.get("FAKE_RUFF_LINES", "1"))
found = 0
for f in files:
    text = open(f, encoding="utf-8").read()
    if "CLEAN" in text:
        continue
    for i in range(n):
        print(f"{f}:{i + 1}:1: F401 fake finding {i} in {f}")
        found += 1
sys.exit(1 if found else 0)
'''


def _fake_ruff(bin_dir: Path) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / "fake_ruff.py").write_text(FAKE_RUFF, encoding="utf-8")
    if os.name == "nt":
        (bin_dir / "ruff.cmd").write_text(
            f'@"{sys.executable}" "%~dp0fake_ruff.py" %*\r\n', encoding="utf-8"
        )
    else:
        script = bin_dir / "ruff"
        script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{bin_dir}/fake_ruff.py" "$@"\n', encoding="utf-8")
        script.chmod(0o755)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_bytes(b"X = 1\n")
    no_finish_check(ws)
    bin_dir = tmp_path / "bin"
    _fake_ruff(bin_dir)
    log = tmp_path / "ruff.log"
    monkeypatch.setenv("FAKE_RUFF_LOG", str(log))
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    return platform, ws, log


def _ruff_on(ws: Path) -> None:
    (ws / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")


def _edit_then_done(path: str = "app.py", content: str = "import os\nX = 2\n") -> list:
    return [[call("write_file", "w1", path=path, content=content)], "Done."]


def _check_rows(platform) -> list:
    return [r for r in ledger(platform) if r.called_as == C.CALLED_AS]


# --------------------------------------------------------------------------- #
# it runs, and its findings ride the edit's own result
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_a_checker_the_folder_has_runs_after_the_edit_and_its_findings_ride_the_edit(env, lane, monkeypatch):
    platform, ws, log = env
    _ruff_on(ws)
    if lane == "post":
        # The POST lane shows no card, so it arms a command tool only where
        # the user's permissions allow it.
        monkeypatch.setitem(platform.permissions._base, "shell", "allow")
    model = wire(platform, monkeypatch, _edit_then_done())
    out = await (stream(platform, body(ws)) if lane == "stream" else post(platform, body(ws)))
    done = out["done"] if lane == "stream" else out
    assert done is not None
    # The model's next call reads the findings ON THE EDIT's result, under its
    # syntax line — and no tool message of the checker's own.
    sent = model.tool_messages(1)
    assert set(sent) == {"w1"}
    text = sent["w1"]
    assert text.startswith("wrote 16 bytes to app.py\nCheck: the file parses.\nCheck (ruff): 1 problem in app.py\n")
    assert "app.py:1:1: F401 fake finding 0 in app.py" in text
    # It ran as an ordinary command call, ledgered as the project's check,
    # with the safe flags (never a fix, never a cache in the folder).
    rows = _check_rows(platform)
    assert [(r.tool, r.session_id) for r in rows] == [("shell", SESSION)]
    command = json.loads(rows[0].args_json)["command"]
    assert command == "ruff check --no-fix --no-cache --force-exclude --output-format concise app.py"
    assert log.read_text(encoding="utf-8").split() == [
        "check", "--no-fix", "--no-cache", "--force-exclude", "--output-format", "concise", "app.py"]
    if lane == "stream":
        # The screen sees the run as a step right after the edit.
        assert finished_ids(out["frames"]) == ["w1", "ij-check-1"]


async def test_a_clean_file_says_no_problems(env, monkeypatch):
    platform, ws, _log = env
    _ruff_on(ws)
    model = wire(platform, monkeypatch, _edit_then_done(content="X = 2  # CLEAN\n"))
    await stream(platform, body(ws))
    assert model.tool_messages(1)["w1"].endswith("Check: the file parses.\nCheck (ruff): no problems in app.py.")


async def test_findings_are_bounded_and_the_cut_is_said(env, monkeypatch):
    platform, ws, _log = env
    _ruff_on(ws)
    monkeypatch.setenv("FAKE_RUFF_LINES", "60")
    model = wire(platform, monkeypatch, _edit_then_done())
    await stream(platform, body(ws))
    text = model.tool_messages(1)["w1"]
    assert "Check (ruff): 60 problems in app.py" in text
    shown = [ln for ln in text.splitlines() if "fake finding" in ln]
    assert len(shown) == C.MAX_FINDING_LINES
    assert text.endswith(f"… {60 - C.MAX_FINDING_LINES} more lines not shown")


# --------------------------------------------------------------------------- #
# a folder without checkers behaves exactly as before
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("setup", ["no config", "config, no program"])
@pytest.mark.parametrize("lane", ["stream", "post"])
async def test_no_checkers_installed_means_the_same_calls_and_the_same_check_text(env, lane, setup, monkeypatch):
    platform, ws, log = env
    if setup == "config, no program":
        _ruff_on(ws)
        # The session's PATH has no ruff at all.
        monkeypatch.setenv("PATH", os.pathsep.join(
            p for p in os.environ.get("PATH", "").split(os.pathsep) if not (Path(p) / "fake_ruff.py").exists()
        ))
        monkeypatch.setattr(C.shutil, "which", lambda name, path=None: None)
    if lane == "post":
        monkeypatch.setitem(platform.permissions._base, "shell", "allow")
    model = wire(platform, monkeypatch, _edit_then_done())
    out = await (stream(platform, body(ws)) if lane == "stream" else post(platform, body(ws)))
    assert model.tool_messages(1) == {"w1": "wrote 16 bytes to app.py\nCheck: the file parses."}
    assert len(model.calls) == 2
    assert [r.tool for r in ledger(platform)] == ["write_file"]
    assert not log.exists()
    if lane == "stream":
        assert finished_ids(out["frames"]) == ["w1"]


# --------------------------------------------------------------------------- #
# the posture decides, exactly as for a command the model ran
# --------------------------------------------------------------------------- #


async def test_ask_first_cards_the_checker_and_a_declined_card_runs_nothing(env, monkeypatch):
    platform, ws, log = env
    _ruff_on(ws)
    asked: list[dict] = []

    def answer(frame):
        asked.append(frame)
        return "deny"

    model = wire(platform, monkeypatch, _edit_then_done())
    await stream(platform, body(ws, approval_mode="approve_for_me"), answer=answer)
    # ONE card: the checker's command (the write is allow-tier there).
    assert [(f["tool"], f["args"]["command"].split()[:2]) for f in asked] == [("shell", ["ruff", "check"])]
    assert not log.exists(), "a declined checker ran"
    text = model.tool_messages(1)["w1"]
    assert "Check: the file parses.\nCheck (ruff) was not run: " in text
    assert "declined" in text
    # The refusal is ledgered as the check's (a decision a human made).
    rows = _check_rows(platform)
    assert len(rows) == 1 and not rows[0].ok


async def test_ask_first_allowed_once_runs_it(env, monkeypatch):
    platform, ws, log = env
    _ruff_on(ws)
    model = wire(platform, monkeypatch, _edit_then_done())
    await stream(platform, body(ws, approval_mode="approve_for_me"), answer=lambda f: "once")
    assert log.exists()
    assert "Check (ruff): 1 problem in app.py" in model.tool_messages(1)["w1"]


async def test_local_only_cards_it_allow_once_only(env, monkeypatch):
    from iron_jarvis.providers import egress

    platform, ws, log = env
    _ruff_on(ws)
    asked: list[dict] = []
    model = wire(platform, monkeypatch, _edit_then_done())
    with egress.local_only_scope(True):
        await stream(platform, body(ws), answer=lambda f: (asked.append(f) or "once"))
    assert [f["tool"] for f in asked if f["args"].get("command", "").startswith("ruff")] == ["shell"]
    card = next(f for f in asked if f["args"].get("command", "").startswith("ruff"))
    assert card.get("once_only") is True
    assert "Check (ruff): 1 problem" in model.tool_messages(1)["w1"]


async def test_low_trust_never_runs_a_checker(env, monkeypatch):
    platform, ws, log = env
    _ruff_on(ws)
    model = wire(platform, monkeypatch, _edit_then_done())
    out = await stream(platform, body(ws), trust="low", trust_reason="test")
    assert not log.exists()
    assert _check_rows(platform) == []
    assert "Check (ruff)" not in model.tool_messages(1).get("w1", "")
    assert "ij-check-1" not in finished_ids(out["frames"])


async def test_the_post_lane_without_an_allowed_command_tool_runs_no_checker(env, monkeypatch):
    platform, ws, log = env
    _ruff_on(ws)
    model = wire(platform, monkeypatch, _edit_then_done())
    await post(platform, body(ws))
    assert not log.exists() and _check_rows(platform) == []
    assert model.tool_messages(1) == {"w1": "wrote 16 bytes to app.py\nCheck: the file parses."}


async def test_plain_chat_and_a_non_build_harness_turn_run_no_checker(env, monkeypatch):
    platform, ws, log = env
    _ruff_on(ws)
    for kw in ({"harness": None, "session_id": "chat"}, {"session_id": "chat"}):
        model = wire(platform, monkeypatch, _edit_then_done())
        b = body(ws, tools=["write_file"])
        await stream(platform, b, **kw)
        assert not log.exists() and _check_rows(platform) == []
        assert "Check (ruff)" not in "".join(model.tool_messages(-1).values())


# --------------------------------------------------------------------------- #
# what it runs on, and what it never does
# --------------------------------------------------------------------------- #


async def test_an_edit_that_did_not_apply_runs_no_checker(env, monkeypatch):
    platform, ws, log = env
    _ruff_on(ws)
    model = wire(platform, monkeypatch, [
        [call("edit_file", "e1", path="app.py", old="NOT THERE", new="Y = 1")], "Done.",
    ])
    out = await stream(platform, body(ws))
    assert not log.exists() and _check_rows(platform) == []
    assert "Check (ruff)" not in model.tool_messages(1)["e1"]
    # (Its step still settles — answered without running.)
    assert finished_ids(out["frames"]) == ["e1", "ij-check-1"]


async def test_one_run_per_checker_covers_the_rounds_edits_and_only_files_it_reads(env, monkeypatch):
    platform, ws, log = env
    _ruff_on(ws)
    (ws / "b.py").write_bytes(b"Y = 1\n")
    model = wire(platform, monkeypatch, [[
        call("write_file", "w1", path="app.py", content="import os\n"),
        call("write_file", "w2", path="notes.md", content="hi\n"),
        call("edit_file", "w3", path="b.py", old="Y = 1", new="import sys\nY = 2"),
    ], "Done."])
    await stream(platform, body(ws))
    assert log.read_text(encoding="utf-8").split()[-2:] == ["app.py", "b.py"]
    sent = model.tool_messages(1)
    # The findings ride the LAST edit of the round that changed a checked file.
    assert "Check (ruff)" not in sent["w1"] and "Check (ruff)" not in sent["w2"]
    assert "Check (ruff): 2 problems in app.py, b.py" in sent["w3"]
    assert len(_check_rows(platform)) == 1


async def test_one_run_takes_a_bounded_number_of_files_and_says_so(env, monkeypatch):
    platform, ws, log = env
    _ruff_on(ws)
    monkeypatch.setattr(C, "MAX_FILES_PER_RUN", 2)
    model = wire(platform, monkeypatch, [[
        call("write_file", f"w{i}", path=f"m{i}.py", content="import os\n") for i in range(3)
    ], "Done."])
    await stream(platform, body(ws))
    assert log.read_text(encoding="utf-8").split()[-2:] == ["m0.py", "m1.py"]
    assert ("Check (ruff): 2 problems in m0.py, m1.py (1 more changed file not checked: one run "
            "takes at most 2)") in model.tool_messages(1)["w2"]


async def test_a_file_name_a_command_line_cannot_carry_is_never_checked(env, monkeypatch):
    platform, ws, log = env
    _ruff_on(ws)
    model = wire(platform, monkeypatch, [[
        call("write_file", "w1", path="a&calc.py", content="X = 1\n"),
        call("write_file", "w2", path="-rf.py", content="X = 1\n"),
    ], "Done."])
    await stream(platform, body(ws))
    assert not log.exists() and _check_rows(platform) == []
    assert all("Check (ruff)" not in t for t in model.tool_messages(1).values())


def test_detection_needs_the_config_and_the_program_and_never_installs(tmp_path, monkeypatch):
    ws = tmp_path / "p"
    ws.mkdir()
    monkeypatch.setattr(C.shutil, "which", lambda name, path=None: None)
    assert C.detect(ws, path_env="") == []
    (ws / "pyproject.toml").write_text("[tool.ruff]\nline-length = 90\n[tool.mypy]\nstrict = true\n", encoding="utf-8")
    (ws / "tsconfig.json").write_text("{}", encoding="utf-8")
    (ws / "eslint.config.js").write_text("export default [];\n", encoding="utf-8")
    # Config, but no program anywhere: nothing (and nothing installed).
    assert C.detect(ws, path_env="") == []
    # The project's OWN program wins; the PATH is the fallback.
    venv = ws / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    venv.mkdir(parents=True)
    (venv / ("ruff.exe" if os.name == "nt" else "ruff")).write_bytes(b"")
    nm = ws / "node_modules" / ".bin"
    nm.mkdir(parents=True)
    (nm / ("tsc.cmd" if os.name == "nt" else "tsc")).write_bytes(b"")
    monkeypatch.setattr(C.shutil, "which", lambda name, path=None: f"/usr/bin/{name}" if name == "mypy" else None)
    found = {c.name: c for c in C.detect(ws, path_env="x")}
    assert set(found) == {"ruff", "mypy", "tsc"}
    assert found["ruff"].where == "project" and found["ruff"].program.replace("\\", "/").endswith(".venv/Scripts/ruff.exe"
                                                                                                    if os.name == "nt" else ".venv/bin/ruff")
    assert found["mypy"].where == "path" and found["mypy"].program == "mypy"
    assert found["tsc"].where == "project"
    for c in found.values():
        line = C.command_line(c.argv(["a.py"]))
        assert "npx" not in line and "uvx" not in line and "pip" not in line and "install" not in line
    # ruff never fixes, mypy never writes a cache into the folder.
    assert "--no-fix" in found["ruff"].argv(["a.py"]) and "--no-cache" in found["ruff"].argv(["a.py"])
    mypy_argv = found["mypy"].argv(["a.py"])
    assert mypy_argv[mypy_argv.index("--cache-dir") + 1] in ("nul", "/dev/null")


def test_a_config_without_its_table_turns_nothing_on(tmp_path, monkeypatch):
    ws = tmp_path / "p"
    ws.mkdir()
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    (ws / "setup.cfg").write_text("[flake8]\nmax-line-length = 9\n", encoding="utf-8")
    monkeypatch.setattr(C.shutil, "which", lambda name, path=None: "/bin/" + name)
    assert C.detect(ws, path_env="x") == []


async def test_detection_runs_off_the_event_loop(tmp_path, monkeypatch):
    seen: list[int] = []
    real = C.detect

    def spy(*a, **kw):
        seen.append(threading.get_ident())
        return real(*a, **kw)

    monkeypatch.setattr(C, "detect", spy)

    class Turn:
        class plan:
            gate = {"shell", "write_file"}

    await C.for_turn(tmp_path, harness_turn=Turn())
    assert seen and threading.get_ident() not in seen


async def test_the_run_is_prepared_and_read_off_the_event_loop(env, monkeypatch):
    platform, ws, _log = env
    _ruff_on(ws)
    seen: dict[str, list[int]] = {"prepare": [], "findings": []}
    real_prepare, real_findings = C.ProjectCheckers.prepare, C.findings

    def prepare(self, call):
        seen["prepare"].append(threading.get_ident())
        return real_prepare(self, call)

    def findings(*a, **kw):
        seen["findings"].append(threading.get_ident())
        return real_findings(*a, **kw)

    monkeypatch.setattr(C.ProjectCheckers, "prepare", prepare)
    monkeypatch.setattr(C, "findings", findings)
    model = wire(platform, monkeypatch, _edit_then_done())
    await stream(platform, body(ws))
    assert "Check (ruff): 1 problem" in model.tool_messages(1)["w1"]
    loop_thread = threading.get_ident()
    assert seen["prepare"] and loop_thread not in seen["prepare"]
    assert seen["findings"] and loop_thread not in seen["findings"]


def test_scheduling_on_the_loop_touches_no_disk(tmp_path, monkeypatch):
    """``schedule`` runs ON the event loop, so it is string work only."""
    from iron_jarvis.providers.adapters.base import ToolCall

    checker = C.Checker(name="ruff", program="ruff", where="path", config="ruff.toml", suffixes=C.PY_SUFFIXES)
    pc = C.ProjectCheckers(tmp_path, [checker], "shell")

    def no_disk(*a, **kw):
        raise AssertionError("schedule touched the disk")

    monkeypatch.setattr(Path, "resolve", no_disk)
    monkeypatch.setattr(os, "stat", no_disk)
    calls = [ToolCall(id="w1", name="write_file", arguments={"path": "pkg/a.py", "content": "x"}),
             ToolCall(id="w2", name="write_file", arguments={"path": str(tmp_path / "b.py"), "content": "x"}),
             ToolCall(id="w3", name="write_file", arguments={"path": "../out.py", "content": "x"})]
    out = pc.schedule(calls)
    assert [c.id for c in out] == ["w1", "w2", "ij-check-1", "w3"]
    assert out[2].arguments["command"].endswith(" pkg/a.py b.py")


def test_the_finish_check_never_reads_a_checker_run_as_the_models_command(env):
    """A project check's ``shell`` row is not a command the model ran: the
    finish check's source of "the test command you ran" (a carried-on
    reply's earlier commands) skips it like its own runs."""
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ToolInvocation

    platform, _ws, _log = env
    with session_scope(platform.engine) as db:
        for called_as, cmd in (("", "python -m pytest -q"), (C.CALLED_AS, "ruff check --no-fix a.py"),
                               (F.CALLED_AS, "python -m pytest -q")):
            db.add(ToolInvocation(session_id=SESSION, agent_run_id="", tool="shell",
                                  args_json=json.dumps({"command": cmd}), confinement="native",
                                  called_as=called_as))
        db.commit()
    got = F.ran_commands(platform.engine, SESSION, 0, 10**9)
    assert [(a["command"], c) for _t, a, c in got] == [("python -m pytest -q", "")]


# --------------------------------------------------------------------------- #
# the REAL type checkers: a type error an edit introduced comes back on it
# --------------------------------------------------------------------------- #


def _mypy_dir() -> "str | None":
    """A folder holding a runnable ``mypy`` (this machine: the PATH, or
    ``IJ_TEST_MYPY_BIN``)."""
    found = shutil.which("mypy")
    if found:
        return str(Path(found).parent)
    extra = os.environ.get("IJ_TEST_MYPY_BIN", "")
    if extra and shutil.which("mypy", path=extra):
        return extra
    return None


def _typescript() -> "str | None":
    """A typescript package folder — ``IJ_TEST_TYPESCRIPT``, else the
    dashboard's own (``dashboard/node_modules/typescript``: the release gate
    installs the dashboard before the Python suite) — and node to run it."""
    if not shutil.which("node"):
        return None
    for pkg in (os.environ.get("IJ_TEST_TYPESCRIPT", ""),
                str(Path(__file__).resolve().parents[1] / "dashboard" / "node_modules" / "typescript")):
        if pkg and (Path(pkg) / "bin" / "tsc").is_file():
            return pkg
    return None


def _real_ruff() -> "str | None":
    found = shutil.which("ruff")
    if not found:
        return None
    # (never this test file's stand-in)
    return None if (Path(found).parent / "fake_ruff.py").exists() else str(Path(found).parent)


@pytest.mark.skipif(_real_ruff() is None, reason="ruff is not on this machine's PATH")
async def test_the_real_ruff_takes_the_flags_and_its_finding_comes_back(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "proj"
    ws.mkdir()
    no_finish_check(ws)
    (ws / "pyproject.toml").write_text("[tool.ruff]\nline-length = 100\nfix = true\n", encoding="utf-8")
    model = wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content="import os\nX = 1\n")],
                                         "Done."])
    await stream(platform, body(ws))
    text = model.tool_messages(1)["w1"]
    assert "Check (ruff): 1 problem in app.py\n" in text and "F401" in text, text
    # `fix = true` in the config, and the file is untouched (--no-fix).
    assert (ws / "app.py").read_text(encoding="utf-8") == "import os\nX = 1\n"
    assert not (ws / ".ruff_cache").exists()


def test_eslint_json_is_read_as_lines_for_the_changed_files(tmp_path):
    checker = C.Checker(name="eslint", program="eslint", where="path", config="eslint.config.js",
                        suffixes=C.JS_SUFFIXES)
    out = json.dumps([
        {"filePath": str(tmp_path / "src" / "a.js"), "messages": [
            {"line": 3, "column": 7, "severity": 2, "message": "'x' is assigned a value but never used.",
             "ruleId": "no-unused-vars"},
            {"line": 9, "column": 1, "severity": 1, "message": "Unexpected console statement.", "ruleId": "no-console"},
        ]},
        {"filePath": str(tmp_path / "b.js"), "messages": [
            {"line": 0, "column": 0, "severity": 1, "message": "File ignored because of a matching ignore pattern."},
        ]},
    ])
    lines, others, understood = C.findings(checker, "[warning] sandbox: note\n" + out, ["src/a.js", "b.js"], tmp_path)
    assert understood and others == 0
    assert lines == [
        "src/a.js:3:7: error 'x' is assigned a value but never used. (no-unused-vars)",
        "src/a.js:9:1: warning Unexpected console statement. (no-console)",
    ]
    # A config error is not JSON: shown raw, as a run that could not finish.
    assert C.findings(checker, "Oops! Something went wrong!", ["src/a.js"], tmp_path)[2] is False


@pytest.mark.skipif(_mypy_dir() is None, reason="mypy is not on this machine (PATH or IJ_TEST_MYPY_BIN)")
async def test_a_type_error_an_edit_introduced_comes_back_from_the_real_mypy(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "proj"
    ws.mkdir()
    no_finish_check(ws)
    (ws / "mypy.ini").write_text("[mypy]\nwarn_unused_configs = False\n", encoding="utf-8")
    (ws / "util.py").write_text("def double(n: int) -> int:\n    return n * 2\n", encoding="utf-8")
    monkeypatch.setenv("PATH", _mypy_dir() + os.pathsep + os.environ.get("PATH", ""))
    bad = "from util import double\n\n\ndef label(n: int) -> str:\n    return double(n)\n"
    model = wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content=bad)], "Done."])
    await stream(platform, body(ws))
    text = model.tool_messages(1)["w1"]
    assert "Check: the file parses.\nCheck (mypy): 1 problem in app.py\n" in text, text
    assert "app.py:5: error: Incompatible return value type" in text
    # Only the changed file's lines; nothing written into the project.
    assert not (ws / ".mypy_cache").exists()
    rows = _check_rows(platform)
    assert len(rows) == 1 and json.loads(rows[0].args_json)["command"].startswith("mypy --cache-dir")


@pytest.mark.skipif(_typescript() is None, reason="typescript + node are not on this machine (IJ_TEST_TYPESCRIPT)")
async def test_a_type_error_an_edit_introduced_comes_back_from_the_projects_own_tsc(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "web"
    ws.mkdir()
    no_finish_check(ws)
    (ws / "tsconfig.json").write_text(json.dumps({
        "compilerOptions": {"strict": True, "noEmit": True, "target": "es2020"}, "include": ["src"],
    }), encoding="utf-8")
    (ws / "src").mkdir()
    (ws / "src" / "other.ts").write_text("export const n: number = 1;\n", encoding="utf-8")
    # The project's OWN tsc (node_modules/.bin) — project code, run as a command.
    bin_dir = ws / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    tsc_js = Path(_typescript()) / "bin" / "tsc"
    if os.name == "nt":
        (bin_dir / "tsc.cmd").write_text(f'@node "{tsc_js}" %*\r\n', encoding="utf-8")
    else:
        sh = bin_dir / "tsc"
        sh.write_text(f'#!/bin/sh\nexec node "{tsc_js}" "$@"\n', encoding="utf-8")
        sh.chmod(0o755)
    bad = "export function total(xs: number[]): number {\n  return xs.join(',');\n}\n"
    model = wire(platform, monkeypatch, [[call("write_file", "w1", path="src/total.ts", content=bad)], "Done."])
    await stream(platform, body(ws))
    text = model.tool_messages(1)["w1"]
    assert "Check (tsc): 1 problem in src/total.ts\n" in text, text
    assert "src/total.ts(2,3): error TS2322" in text
    rows = _check_rows(platform)
    assert len(rows) == 1 and "--noEmit" in json.loads(rows[0].args_json)["command"]
