"""IJ harness part 3, Phase 3 — THE REVIEW's pins.

Each pin drives the REAL pieces (a real platform: registry, permission engine,
cards, ledger, shell tool; the real claude-cli adapter against the fake CLI;
the real detections ledger and rules) with only the model scripted.

1. A PROJECT CHECKER NEEDS A PERSON, EVEN UNDER YOLO. A checker (ruff, mypy,
   eslint, tsc) is a command the APP writes and the PROJECT supplies: its own
   ``.venv`` / ``node_modules`` program, an ESLint JS config, a mypy plugin —
   and cmd.exe runs a bare ``ruff`` from the folder itself first. That is the
   v1.386.1 rule for the finish check's folder command (``needs_a_person``)
   and the v1.366.0 rule for a folder's plugin checks: never auto-approve's
   per-call grant. So under yolo a checker cards exactly as under
   approve-for-me — unless a grant WIDER than one call covers the command
   tool (the user's permission table, "Allow for this chat", a standing
   grant). The round's checkers share ONE card (v1.247.0). FAILS ON THE
   REVIEWED CODE: yolo ran the project's checker with no card.
2. A READ OF SEVERAL FILES IS SEEN BY THE SAFETY CHECKS. ``read_file(paths=…)``
   produced ONE ``file.read`` event with no path, so a multi-read of a
   project's ``.env`` never raised ``credential-file-read``. FAILS ON THE
   REVIEWED CODE.
3. THE MAP'S NAMES ARE BOUNDED BY TIME. The walk had a deadline; reading and
   parsing the files' top-level names had none — measured on a 4,800-file
   synthetic tree: 88 s before the first model call of the turn, to show a
   3,084-character map. FAILS ON THE REVIEWED CODE.
4. Pins of what the reviewed code already did right (each mutation-checked):
   the checker runs in the platform's own shell under the claude dialect;
   a folder's deny rule keeps it from running; an unknown or unarmed name
   from the claude-cli adapter never runs and the turn goes on; a multi-read
   keeps every file behind the same gates as a single read.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from iron_jarvis.harness import checkers as C
from iron_jarvis.harness import project_map as PM
from iron_jarvis.platform import build_platform
from tests._fakes.harness_lane import body, call, finished_ids, ledger, no_finish_check, post, stream, wire
from tests.test_harness_checkers_v1387 import FAKE_RUFF

# --------------------------------------------------------------------------- #
# 1. a project checker needs a person, even under yolo
# --------------------------------------------------------------------------- #


def _fake_program(bin_dir: Path, name: str) -> None:
    """``name`` on the session's PATH: the checkers' stand-in (prints a
    ruff-style finding per checked .py file, logs its arguments)."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    (bin_dir / f"fake_{name}.py").write_text(FAKE_RUFF, encoding="utf-8")
    if os.name == "nt":
        (bin_dir / f"{name}.cmd").write_text(
            f'@"{sys.executable}" "%~dp0fake_{name}.py" %*\r\n', encoding="utf-8"
        )
    else:
        script = bin_dir / name
        script.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{bin_dir}/fake_{name}.py" "$@"\n',
                          encoding="utf-8")
        script.chmod(0o755)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "app.py").write_bytes(b"X = 1\n")
    no_finish_check(ws)
    bin_dir = tmp_path / "bin"
    _fake_program(bin_dir, "ruff")
    _fake_program(bin_dir, "mypy")
    log = tmp_path / "checkers.log"
    monkeypatch.setenv("FAKE_RUFF_LOG", str(log))
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    (ws / "ruff.toml").write_text("line-length = 100\n", encoding="utf-8")
    return platform, ws, log


def _program_of(command: str) -> str:
    """The checker program's own name ("ruff") from a checker's command line:
    its first word is the program the detection found — the project's own
    copy (a relative path) or, found on the PATH, its FULL path (Phase 3
    review), double-quoted when it holds a space."""
    first = command[1:command.index('"', 1)] if command.startswith('"') else command.split()[0]
    return Path(first.replace("\\", "/")).stem.lower()


def _check_rows(platform) -> list:
    return [r for r in ledger(platform) if r.called_as == C.CALLED_AS]


def _checker_cards(asked: list[dict]) -> list[dict]:
    return [f for f in asked if str(f.get("call_id", "")).startswith(C.CALL_ID_PREFIX)]


async def test_yolo_cards_a_project_checker_and_a_declined_card_runs_nothing(env, monkeypatch):
    platform, ws, log = env
    asked: list[dict] = []
    model = wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content="import os\n")], "Done."])
    await stream(platform, body(ws, approval_mode="yolo"), answer=lambda f: (asked.append(f) or "deny"))
    cards = _checker_cards(asked)
    assert [(f["tool"], _program_of(f["args"]["command"])) for f in cards] == [("shell", "ruff")], asked
    # The model's own edit did not card (yolo), only the project's checker.
    assert len(asked) == 1
    assert not log.exists(), "the project's checker ran with nobody asked"
    assert "Check (ruff) was not run: " in model.tool_messages(1)["w1"]


async def test_yolo_allowed_once_runs_it_and_the_rounds_checkers_share_one_card(env, monkeypatch):
    platform, ws, log = env
    (ws / "mypy.ini").write_text("[mypy]\n", encoding="utf-8")
    asked: list[dict] = []
    model = wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content="import os\n")], "Done."])
    await stream(platform, body(ws, approval_mode="yolo"), answer=lambda f: (asked.append(f) or "once"))
    # ONE card for the round's two checkers (v1.247.0), naming both commands.
    assert len(asked) == 1 and asked[0].get("count") == 2, asked
    assert [_program_of(e["command"]) for e in asked[0]["examples"]] == ["ruff", "mypy"]
    text = model.tool_messages(1)["w1"]
    assert "Check (ruff): 1 problem in app.py" in text and "Check (mypy): 1 problem in app.py" in text
    assert len(_check_rows(platform)) == 2 and all(r.ok is False for r in _check_rows(platform))


async def test_yolo_allow_for_this_chat_covers_the_next_rounds_checker(env, monkeypatch):
    """The least noisy honest form: ONE card per session — "Allow for this
    chat" is a grant wider than one call, and covers every later run."""
    platform, ws, log = env
    asked: list[dict] = []
    model = wire(platform, monkeypatch, [
        [call("write_file", "w1", path="app.py", content="import os\n")],
        [call("write_file", "w2", path="app.py", content="import sys\n")],
        "Done.",
    ])
    await stream(platform, body(ws, approval_mode="yolo"), answer=lambda f: (asked.append(f) or "conversation"))
    assert len(asked) == 1, asked
    assert "Check (ruff): 1 problem" in model.tool_messages(1)["w1"]
    assert "Check (ruff): 1 problem" in model.tool_messages(2)["w2"]
    assert len(_check_rows(platform)) == 2


async def test_a_grant_wider_than_one_call_runs_it_with_no_card(env, monkeypatch):
    """The session's earlier "Allow for this chat" (``granted_tools``, what
    a Build session sends every turn after it) covers the checker. (The POST
    lane never cards and arms no ask-tier tool — a grant never arms one —
    so there a checker runs only where the user's permission table allows
    the command tool: ``test_harness_checkers_v1387``'s POST case.)"""
    platform, ws, log = env
    model = wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content="import os\n")], "Done."])
    await stream(platform, body(ws, approval_mode="yolo", granted_tools=["shell"]))  # any card fails
    assert log.exists()
    assert "Check (ruff): 1 problem in app.py" in model.tool_messages(1)["w1"]


async def test_the_post_lane_under_yolo_with_no_allowed_command_tool_runs_no_checker(env, monkeypatch):
    platform, ws, log = env
    model = wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content="import os\n")], "Done."])
    await post(platform, body(ws, approval_mode="yolo", granted_tools=["shell"]))
    assert not log.exists()
    assert model.tool_messages(1) == {"w1": "wrote 10 bytes to app.py\nCheck: the file parses."}


async def test_the_users_permission_table_allowing_the_command_tool_runs_it_with_no_card(env, monkeypatch):
    platform, ws, log = env
    monkeypatch.setitem(platform.permissions._base, "shell", "allow")
    model = wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content="import os\n")], "Done."])
    await stream(platform, body(ws, approval_mode="yolo"))
    assert "Check (ruff): 1 problem in app.py" in model.tool_messages(1)["w1"]


async def test_the_models_own_command_still_runs_with_no_card_under_yolo(env, monkeypatch):
    """Only the APP's command for the project needs a person: the model's
    own shell call in the same round keeps yolo's per-call grant."""
    platform, ws, log = env
    asked: list[dict] = []
    model = wire(platform, monkeypatch, [[
        call("write_file", "w1", path="app.py", content="import os\n"),
        call("shell", "s1", command="echo hello-from-the-model"),
    ], "Done."])
    await stream(platform, body(ws, approval_mode="yolo"), answer=lambda f: (asked.append(f) or "deny"))
    assert [f["call_id"] for f in asked] == [f"{C.CALL_ID_PREFIX}1"]
    assert "hello-from-the-model" in model.tool_messages(1)["s1"]


@pytest.mark.parametrize("mode", ["approve_for_me", "always_ask"])
async def test_every_asking_posture_cards_it_once_for_the_round(env, monkeypatch, mode):
    platform, ws, log = env
    (ws / "mypy.ini").write_text("[mypy]\n", encoding="utf-8")
    asked: list[dict] = []
    wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content="import os\n")], "Done."])
    await stream(platform, body(ws, approval_mode=mode), answer=lambda f: (asked.append(f) or "once"))
    cards = _checker_cards(asked)
    assert len(cards) == 1 and cards[0].get("count") == 2, asked


async def test_folder_deny_rules_on_the_command_tools_keep_every_checker_from_running(env, monkeypatch):
    """A folder that denies the command tools gets no checker. (A rule names
    ONE tool: a folder that denies only ``shell`` still lets the checker run
    through ``process_start`` — ``check_carrier``, as for a plugin check and
    as the model itself could — so this folder denies both.)"""
    platform, ws, log = env
    (ws / ".ironjarvis" / "harness" / "plugins.toml").write_text(
        '[[rules]]\ntool = "shell"\naction = "deny"\n\n[[rules]]\ntool = "process_start"\naction = "deny"\n',
        encoding="utf-8")
    model = wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content="import os\n")], "Done."])
    await stream(platform, body(ws, approval_mode="yolo"))
    assert not log.exists()
    assert "Check (ruff)" not in model.tool_messages(1)["w1"]
    assert [r.tool for r in ledger(platform)] == ["write_file"]


def _git_bash() -> "str | None":
    try:
        from iron_jarvis.sandbox.git_bash import find

        return find()
    except Exception:  # noqa: BLE001
        return None


FAKE_TSC = r'''
import sys
print("src/total.ts(2,3): error TS2322: Type 'string' is not assignable to type 'number'.")
sys.exit(2)
'''


@pytest.mark.skipif(os.name != "nt" or _git_bash() is None,
                    reason="Windows with Git for Windows' bash (the claude dialect's Bash)")
async def test_under_the_claude_dialect_a_checker_runs_in_the_platforms_own_shell(tmp_path, monkeypatch):
    """DECIDED: a checker's command line is written for the platform's own
    shell (cmd.exe quoting, the project program's ``node_modules\\.bin\\x.cmd``
    path with backslashes) and runs there — never in the Git bash Claude's
    own ``Bash`` commands run in (bash reads ``\\.`` as an escape and finds no
    program). The finish check's folder command runs the same way."""
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "web"
    ws.mkdir()
    no_finish_check(ws)
    (ws / "tsconfig.json").write_text("{}", encoding="utf-8")
    bin_dir = ws / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "fake_tsc.py").write_text(FAKE_TSC, encoding="utf-8")
    (bin_dir / "tsc.cmd").write_text(f'@"{sys.executable}" "%~dp0fake_tsc.py" %*\r\n', encoding="utf-8")
    (ws / "src").mkdir()
    seen: list[tuple[str, str]] = []
    from iron_jarvis.sandbox import shell_tool

    real = shell_tool.SandboxedShellTool.execute

    async def spy(self, args, ctx):
        seen.append((str(args.get("command")), str(getattr(ctx, "shell_syntax", ""))))
        return await real(self, args, ctx)

    monkeypatch.setattr(shell_tool.SandboxedShellTool, "execute", spy)
    model = wire(platform, monkeypatch, [
        [call("Write", "w1", file_path=str(ws / "src" / "total.ts"), content="export const n = 1;\n")], "Done.",
    ])
    await stream(platform, body(ws, approval_mode="yolo"), harness="claude", answer=lambda f: "once")
    assert seen == [(r"node_modules\.bin\tsc.cmd --noEmit --pretty false -p tsconfig.json", "")], seen
    assert "Check (tsc): 1 problem in src/total.ts" in model.tool_messages(1)["w1"]


# --------------------------------------------------------------------------- #
# 2. a read of several files is seen by the safety checks
# --------------------------------------------------------------------------- #


def test_a_multi_file_read_gives_the_safety_checks_one_read_per_file():
    from iron_jarvis.core.models import ToolInvocation
    from iron_jarvis.detections.engine import scan
    from iron_jarvis.detections.ledger import to_events

    row = ToolInvocation(id="t1", session_id="build:x", agent_run_id="r", tool="read_file",
                         args_json=json.dumps({"paths": ["app.py", ".env", "notes.md"]}), ok=True)
    events = to_events(row)
    assert [e["path"] for e in events] == ["app.py", ".env", "notes.md"]
    assert all(e["action"] == "file.read" for e in events)
    assert "credential-file-read" in {f.rule_id for f in scan(events)}
    # `path` and `paths` together: each file once, `path` first.
    both = ToolInvocation(id="t2", session_id="s", agent_run_id="r", tool="read_file",
                          args_json=json.dumps({"path": "a.py", "paths": ["a.py", "b.py"]}), ok=True)
    assert [e["path"] for e in to_events(both)] == ["a.py", "b.py"]
    # A single read is one event, exactly as before.
    one = ToolInvocation(id="t3", session_id="s", agent_run_id="r", tool="read_file",
                         args_json=json.dumps({"path": ".env"}), ok=True)
    assert [e["path"] for e in to_events(one)] == [".env"]
    # Bounded like a patch's paths (and like the read itself: 20 files).
    many = ToolInvocation(id="t4", session_id="s", agent_run_id="r", tool="read_file",
                          args_json=json.dumps({"paths": [f"f{i}.py" for i in range(200)]}), ok=True)
    assert len(to_events(many)) == 50


# --------------------------------------------------------------------------- #
# 3. the map's names are bounded by time
# --------------------------------------------------------------------------- #


def test_reading_the_maps_names_stops_at_its_deadline_and_says_so(tmp_path, monkeypatch):
    PM.forget()
    ws = tmp_path / "big"
    for d in range(4):
        sub = ws / f"pkg{d}"
        sub.mkdir(parents=True)
        for k in range(10):
            (sub / f"m{k}.py").write_text(f"def f{k}():\n    pass\n", encoding="utf-8")
    clock = {"t": 1000.0}
    # (the module's own clock name — the reviewed code read time.monotonic
    # directly, so on it the stub below only slows the parse down)
    monkeypatch.setattr(PM, "_now", lambda: clock["t"], raising=False)
    real = PM.python_names

    def slow(source):
        clock["t"] += 0.2  # every parse "takes" 0.2 s
        return real(source)

    monkeypatch.setattr(PM, "python_names", slow)
    base = PM.BUILDS["parsed"]
    try:
        m = PM.build(ws, limit=PM.share_chars(200_000))
        parsed = PM.BUILDS["parsed"] - base
        # The deadline, not the 40 files, decided how many were parsed.
        assert 0 < parsed < 40, parsed
        assert m.complete is False
        assert any("not read in time" in part for part in m.left_out), m.left_out
        assert "not read in time" in m.text
        # A later turn reads more (the per-file names are kept; the unfinished
        # map is never served as if it were whole).
        PM.build(ws, limit=PM.share_chars(200_000))
        assert PM.BUILDS["parsed"] - base > parsed
        for _ in range(40):
            if PM.BUILDS["parsed"] - base >= 40:
                break
            PM.build(ws, limit=PM.share_chars(200_000))
        m3 = PM.build(ws, limit=PM.share_chars(200_000))
        assert PM.BUILDS["parsed"] - base == 40  # every file parsed exactly once
        assert "not read in time" not in m3.text and m3.complete
        assert "m9.py — f9()" in m3.text
    finally:
        PM.forget()


# --------------------------------------------------------------------------- #
# 4. unknown and unarmed names never run, and the turn goes on
# --------------------------------------------------------------------------- #

FAKE_CLAUDE = Path(__file__).parent / "fixtures" / "fake_claude_v1300.py"


def _claude_adapter(tmp_path: Path, monkeypatch, calls: list):
    from iron_jarvis.providers import cli_auth
    from iron_jarvis.providers.adapters import subprocess_cli as sc
    from iron_jarvis.providers.adapters.claude_native import transport
    from iron_jarvis.providers.cli_auth import CliAuthProbe

    for key in [k for k in os.environ if k.startswith("ANTHROPIC_")] + list(transport.BACKEND_SWITCHES):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(transport, "_STRIP_LOGGED", False)
    monkeypatch.setattr(cli_auth, "DEFAULT_PROBE", CliAuthProbe(which=lambda b: None))
    monkeypatch.setattr(transport, "DEFAULT_PROBE", cli_auth.DEFAULT_PROBE)
    if os.name == "nt":
        shim = tmp_path / "claude.cmd"
        shim.write_text(f'@"{sys.executable}" "{FAKE_CLAUDE}" %*\r\n', encoding="utf-8")
    else:
        shim = tmp_path / "claude"
        shim.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{FAKE_CLAUDE}" "$@"\n', encoding="utf-8")
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("FAKE_CLAUDE_SCENARIO", "tools")
    monkeypatch.setenv("FAKE_CLAUDE_RECORD", str(tmp_path / "record.json"))
    monkeypatch.setenv("FAKE_CLAUDE_CALLS", json.dumps(calls))
    return sc.make_claude_cli(which=lambda _b: str(shim), model="claude-sonnet-5")


@pytest.mark.parametrize("posture", ["yolo", "approve_for_me"])
async def test_invented_names_from_the_claude_adapter_never_run_and_the_turn_goes_on(tmp_path, monkeypatch, posture):
    """Plan mode (no write tool armed): ``mcp__ij__write_file`` (a real tool
    this reply does not hold), a name with path characters, a 10,000-char
    name, and ``python`` — each refused by the REAL registry, nothing
    written, no card asked, the model reads the refusal, the turn ends with a
    reply, and the transcript the next request is built from pairs every
    ``tool_use`` with its ``tool_result``."""
    from iron_jarvis.providers.adapters.base import LLMMessage
    from iron_jarvis.providers.adapters.claude_native import frames as F

    huge = "x" * 10_000
    adapter = _claude_adapter(tmp_path, monkeypatch, [
        ["mcp__ij__write_file", {"path": "pwned.txt", "content": "x"}],
        ["mcp__ij__..\\..\\evil", {}],
        [huge, {}],
        ["python", {"code": "open('pwned.txt','w')"}],
    ])
    tools = [{"name": "Read", "description": "r", "input_schema": {"type": "object", "properties": {}}}]
    resp = await adapter.complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=tools)
    assert [c.name for c in resp.tool_calls] == ["write_file", "..\\..\\evil", huge[:120], "python"]
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    no_finish_check(ws)
    model = wire(platform, monkeypatch, [resp, "Sorry. Done."])
    out = await stream(platform, body(ws, approval_mode=posture), harness="claude", plan_mode=True)
    assert out["errors"] == [] and out["done"]["reply"].startswith("Sorry. Done.")
    assert not (ws / "pwned.txt").exists()
    rows = [(r.tool, r.ok) for r in ledger(platform)]
    assert rows == [("write_file", False), ("..\\..\\evil", False), (huge[:120], False), ("python", False)]
    sent = model.tool_messages(1)
    assert sent["toolu_00"].startswith("not armed: ")
    assert all(t.startswith("unknown tool '") for k, t in sent.items() if k != "toolu_00")
    assert all("(The tools you can call in this reply: " in t for t in sent.values())
    # The next request's frames: every tool_use answered by its tool_result.
    history = [m for m in model.calls[1]["messages"]]
    _system, frames = F.history_frames("", history + [LLMMessage(role="user", content="next")],
                                       F.ToolNames.build(["Read"]))
    uses = [b["id"] for fr in frames if fr["type"] == "assistant" for b in fr["message"]["content"]
            if b.get("type") == "tool_use"]
    results = [b["tool_use_id"] for fr in frames if fr["type"] == "user" for b in fr["message"]["content"]
               if b.get("type") == "tool_result"]
    assert uses == results == ["toolu_00", "toolu_01", "toolu_02", "toolu_03"]
    assert finished_ids(out["frames"]) == ["toolu_00", "toolu_01", "toolu_02", "toolu_03"]


# --------------------------------------------------------------------------- #
# 5. a read of several files keeps every gate of a single read
# --------------------------------------------------------------------------- #


@pytest.fixture()
def plat(tmp_path):
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    return platform, ws


def _ctx(platform, ws: Path):
    from iron_jarvis.tools.base import ToolContext

    return ToolContext(session_id="build:review", agent_run_id="", workspace=ws, config=platform.config,
                       event_bus=platform.event_bus, engine=platform.engine)


async def _read(platform, ws: Path, args: dict):
    return await platform.registry.invoke("read_file", args, _ctx(platform, ws), platform.permissions)


async def test_a_protected_key_among_ten_good_files_is_refused_in_its_place(plat):
    platform, ws = plat
    names = []
    for i in range(10):
        (ws / f"f{i}.py").write_text(f"N = {i}\n", encoding="utf-8")
        names.append(f"f{i}.py")
    (ws / ".secrets.key").write_text("TOPSECRET", encoding="utf-8")
    res = await _read(platform, ws, {"paths": names[:5] + [".secrets.key"] + names[5:]})
    assert res.ok
    assert "TOPSECRET" not in res.output
    assert "==> .secrets.key <==\n[not read: '.secrets.key' cannot be read here: " in res.output
    assert all(f"==> f{i}.py <==\nN = {i}\n" in res.output for i in range(10))


@pytest.mark.skipif(os.name != "nt", reason="a Windows junction")
async def test_a_junction_out_of_the_folder_is_refused_in_its_place(plat, tmp_path):
    platform, ws = plat
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("OUTSIDE", encoding="utf-8")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(ws / "link"), str(outside)],
                   check=True, capture_output=True)
    (ws / "ok.txt").write_text("fine", encoding="utf-8")
    res = await _read(platform, ws, {"paths": ["ok.txt", "link/secret.txt"]})
    assert "OUTSIDE" not in res.output
    assert "==> link/secret.txt <==\n[not read: path 'link/secret.txt' escapes the session workspace]" in res.output


async def test_twenty_one_paths_are_refused_whole_and_the_limit_hit_mid_file_is_said(plat):
    platform, ws = plat
    for i in range(21):
        (ws / f"f{i}.txt").write_text("x", encoding="utf-8")
    res = await _read(platform, ws, {"paths": [f"f{i}.txt" for i in range(21)]})
    assert not res.ok and "one call reads at most 20" in (res.error or "")
    (ws / "big1.txt").write_text("a" * 20_000, encoding="utf-8")
    (ws / "big2.txt").write_text("b" * 20_000, encoding="utf-8")
    res = await _read(platform, ws, {"paths": ["big1.txt", "big2.txt", "f0.txt"]})
    assert res.ok and res.output.startswith("[read 2 of 3 files")
    assert "not read: big2.txt" in res.output.splitlines()[0]
    assert "b" * 100 not in res.output and len(res.output) <= 30_000 + 400


async def test_a_null_paths_is_no_argument_and_gets_the_missing_path_sentence(plat):
    """A model's ``"paths": null`` used to satisfy the required ``path``
    (``required_alternatives``) and crash the tool with ``KeyError: 'path'``
    — a traceback names nothing the model can correct (v1.228.0). It reads
    the registry's own missing-argument sentence again."""
    platform, ws = plat
    res = await _read(platform, ws, {"paths": None})
    assert not res.ok and (res.error or "").startswith("missing required: path"), res.error
    assert "KeyError" not in (res.error or "")


def test_files_only_a_deeper_tree_could_show_are_never_read(tmp_path):
    """The map reads names only at the levels a tree with NO names could
    fit in its share: a deeper file is only ever inside a "(N files)"
    count. Same map, none of those files read (the reviewed code read all
    400 here, and 4,800 large ones in 88 s on the measured tree)."""
    PM.forget()
    ws = tmp_path / "wide"
    for d in range(10):
        for s in range(2):
            sub = ws / f"pkg{d}" / f"sub{s}"
            sub.mkdir(parents=True)
            for k in range(20):
                (sub / f"m{k}.py").write_text(f"def f{k}():\n    pass\n", encoding="utf-8")
    base = PM.BUILDS["parsed"]
    try:
        m = PM.build(ws, limit=PM.share_chars(32_000))
        assert PM.BUILDS["parsed"] == base, PM.BUILDS["parsed"] - base
        assert "pkg0/\n  sub0/ (20 files)" in m.text
        assert "the folders below level 2 (shown as file counts)" in m.left_out
        assert "names" not in " ".join(m.left_out)
    finally:
        PM.forget()


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe runs a bare program name from the folder itself first")
async def test_a_checker_found_on_the_path_is_named_by_its_full_path_so_the_folder_cannot_stand_in(env, monkeypatch):
    """A checker found on the session's PATH (no project copy) used to be
    written as its BARE name — and cmd.exe looks in the CURRENT folder before
    the PATH, so a ``ruff.bat`` the project ships ran instead, behind a card
    reading "ruff check …". Now the command names the program the detection
    found, by its full path: the card shows exactly what runs, and a folder's
    own ``ruff.bat`` never stands in for it."""
    platform, ws, log = env
    # The packaged app's environment, not this test runner's: a parent that
    # sets NoDefaultCurrentDirectoryInExePath (Claude Code does) hides the
    # current-folder lookup this pin is about.
    for key in [k for k in os.environ if k.lower() == "nodefaultcurrentdirectoryinexepath"]:
        monkeypatch.delenv(key)
    (ws / "ruff.bat").write_text(
        "@echo app.py:1:1: F999 HIJACKED-BY-THE-FOLDER\r\n@exit /b 1\r\n", encoding="utf-8")
    asked: list[dict] = []
    model = wire(platform, monkeypatch, [[call("write_file", "w1", path="app.py", content="import os\n")], "Done."])
    await stream(platform, body(ws, approval_mode="yolo"), answer=lambda f: (asked.append(f) or "once"))
    found = shutil.which("ruff", path=os.environ["PATH"])
    assert found and Path(found).parent != ws
    command = asked[0]["args"]["command"]
    assert command.startswith(f'"{found}" check' if " " in found else f"{found} check"), command
    text = model.tool_messages(1)["w1"]
    assert "HIJACKED-BY-THE-FOLDER" not in text
    assert "Check (ruff): 1 problem in app.py" in text and log.exists()


@pytest.mark.skipif(os.name != "nt", reason="cmd.exe quoting")
def test_a_program_path_a_command_line_cannot_carry_turns_its_checker_off(tmp_path, monkeypatch):
    ws = tmp_path / "p"
    ws.mkdir()
    (ws / "ruff.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(C.shutil, "which", lambda name, path=None: r"C:\Tools&Co\ruff.exe" if name == "ruff" else None)
    assert C.detect(ws, path_env="x") == []
    monkeypatch.setattr(C.shutil, "which", lambda name, path=None: r"C:\Program Files (x86)\ruff\ruff.exe")
    [ruff] = C.detect(ws, path_env="x")
    assert ruff.where == "path" and ruff.program == r"C:\Program Files (x86)\ruff\ruff.exe"
    assert C.command_line(ruff.argv(["a.py"])).startswith(r'"C:\Program Files (x86)\ruff\ruff.exe" check ')


PATCH = "*** Begin Patch\n*** Update File: app.py\n@@\n-X = 1\n+import os\n+X = 1\n*** End Patch\n"


@pytest.mark.parametrize("dialect, edit", [
    ("generic", ("apply_patch", {"patch": PATCH})),
    # Codex's own shape (``input``), translated to the real tool's ``patch``.
    ("gpt", ("apply_patch", {"input": PATCH})),
])
async def test_a_checker_runs_after_an_apply_patch_edit(env, monkeypatch, dialect, edit):
    """The real ``apply_patch`` takes ``patch`` (Codex's ``input`` is
    translated to it at the boundary); the checkers read ``input`` only, so a
    patch — Codex's ONLY way to edit, and the generic dialect's multi-file
    edit — never had its files checked. FAILS ON THE REVIEWED CODE."""
    platform, ws, log = env
    name, args = edit
    model = wire(platform, monkeypatch, [[call(name, "p1", **args)], "Done."])
    await stream(platform, body(ws, approval_mode="yolo"), harness=dialect, answer=lambda f: "once")
    assert (ws / "app.py").read_text(encoding="utf-8") == "import os\nX = 1\n"
    text = model.tool_messages(1)["p1"]
    assert "Check (ruff): 1 problem in app.py" in text, text
    assert log.exists()


async def test_the_post_lane_answers_an_invented_name_the_same_way(tmp_path, monkeypatch):
    """MIRROR NOTE (lock-step): the POST lane refuses the adapter's invented
    name through the real registry and tells the model what it can call."""
    adapter = _claude_adapter(tmp_path, monkeypatch, [["python", {"code": "open('pwned.txt','w')"}]])
    from iron_jarvis.providers.adapters.base import LLMMessage

    tools = [{"name": "Read", "description": "r", "input_schema": {"type": "object", "properties": {}}}]
    resp = await adapter.complete(system="", messages=[LLMMessage(role="user", content="hi")], tools=tools)
    platform = build_platform(str(tmp_path / "home"))
    ws = tmp_path / "ws"
    ws.mkdir()
    no_finish_check(ws)
    model = wire(platform, monkeypatch, [resp, "Sorry. Done."])
    out = await post(platform, body(ws, approval_mode="yolo"), harness="claude")
    assert out["reply"].startswith("Sorry. Done.")
    assert not (ws / "pwned.txt").exists()
    text = model.tool_messages(1)["toolu_00"]
    assert text.startswith("unknown tool 'python'\n(The tools you can call in this reply: ")
    assert [(r.tool, r.ok) for r in ledger(platform)] == [("python", False)]
