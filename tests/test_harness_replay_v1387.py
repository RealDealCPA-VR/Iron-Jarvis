"""IJ harness part 3, Phase 3 — THE SAME WORK IN FEWER CALLS, replayed.

Two recorded Jarvis-on-Claude runs from the Phase 1 measurement (read-only,
copied into ``tests/fixtures/harness_replay_v1387.json`` with the run's own
folder replaced by ``<WORK>``): ``hard-explain-trace`` (12 tool calls, 10 of
them reads) and ``hard-cross-module-bug`` repeat 2 (10 calls, 6 reads and a
``glob``). A scripted model makes the recorded calls through the REAL stream
lane of a Build session, on a fresh copy of the task, three ways:

* AS RECORDED — the replay makes exactly the recorded number of calls (the
  lane counts as the benchmark counts: every finished tool step);
* CLAUDE DIALECT + THE MAP — Jarvis on Claude today: the listing call the
  project map already answered is not made (the map is checked to name every
  file the run went on to read). Claude Code's ``Read`` has no multi-file
  form, so its reads stay one a call;
* GENERIC DIALECT + THE MAP + ``read_file(paths=…)`` — Jarvis's own names
  (glm's dialect, by measurement): each round's reads become ONE call.

The task's HIDDEN CHECK grades the folder each way: the same work, passed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iron_jarvis.harness import project_map as PM
from iron_jarvis.harness_bench import catalog, grading
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import ToolCall
from tests._fakes.harness_lane import body, finished_ids, no_finish_check, stream, wire

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "harness_replay_v1387.json").read_text(encoding="utf-8"))
#: The calls the map answers: a listing made before the run changed anything.
LISTING = ("glob", "list_files")
#: Claude Code's names for the real tools these runs used (the claude
#: dialect translates them back at the boundary).
CLAUDE = {"read_file": "Read", "write_file": "Write", "glob": "Glob", "list_files": "LS", "shell": "Bash"}


def _args(args: dict, ws: Path) -> dict:
    text = json.dumps(args).replace("<WORK>\\\\", ws.as_posix() + "/").replace("<WORK>", ws.as_posix())
    return json.loads(text)


def _claude_args(tool: str, args: dict) -> dict:
    a = dict(args)
    if tool in ("read_file", "write_file") and "path" in a:
        a["file_path"] = a.pop("path")
    if tool == "read_file":
        a.pop("numbered", None)  # Claude Code's Read is always numbered
    return a


def _moves(rounds: list, ws: Path, way: str) -> list:
    moves: list = []
    n = 0
    changed = False
    for rnd in rounds:
        calls: list[ToolCall] = []
        reads: list[str] = []
        read_at = None
        for c in rnd:
            tool, args = c["tool"], _args(c["args"], ws)
            if way != "recorded" and tool in LISTING and not changed:
                continue  # the map already said it
            if tool in ("write_file", "edit_file", "apply_patch", "multi_edit", "shell"):
                changed = True
            n += 1
            if way == "generic+map+paths" and tool == "read_file":
                if read_at is None:
                    read_at = len(calls)
                    calls.append(None)  # its place in the round
                reads.append(args["path"])
                continue
            if way == "claude+map":
                calls.append(ToolCall(id=f"c{n}", name=CLAUDE[tool], arguments=_claude_args(tool, args)))
            else:
                calls.append(ToolCall(id=f"c{n}", name=tool, arguments=args))
        if read_at is not None:
            calls[read_at] = ToolCall(id=f"r{n}", name="read_file", arguments={"paths": reads, "numbered": True})
        if calls:
            moves.append(calls)
    moves.append("Done.")
    return moves


def _read_paths(rounds: list) -> list[str]:
    return [Path(_args(c["args"], Path("W"))["path"]).name for r in rounds for c in r if c["tool"] == "read_file"]


@pytest.mark.parametrize("run", sorted(FIXTURE["runs"]))
async def test_the_recorded_work_takes_fewer_calls_with_the_map_and_multi_file_reads(tmp_path, monkeypatch, run):
    rec = FIXTURE["runs"][run]
    task = catalog.load_task(catalog.TASKS_DIR / rec["task"])
    counts: dict[str, int] = {}
    for way, dialect in (("recorded", "claude"), ("claude+map", "claude"), ("generic+map+paths", "generic")):
        PM.forget()
        root = tmp_path / way.replace("+", "_")
        ws = catalog.materialize(task, root / "work")
        no_finish_check(ws)
        platform = build_platform(str(root / "home"))
        model = wire(platform, monkeypatch, _moves(rec["rounds"], ws, way))
        out = await stream(platform, body(ws, text=task.prompt if hasattr(task, "prompt") else "do the task"),
                           harness=dialect)
        assert out["errors"] == [] and out["done"] is not None, out["errors"]
        counts[way] = len(finished_ids(out["frames"]))
        if way != "recorded":
            # The map the model started with names every file the run read.
            first = model.calls[0]["system"]
            assert PM.HEADING in first
            for name in _read_paths(rec["rounds"]):
                assert name in first, f"{name} is not in the map"
        verdict = grading.grade(task, ws, root / "grade", python=grading.grading_python())
        assert verdict.passed is True, (way, verdict.reason, verdict.output[-800:])
    assert counts["recorded"] == rec["recorded_tool_calls"]
    assert counts["claude+map"] < counts["recorded"]
    assert counts["generic+map+paths"] < counts["claude+map"]
    print(f"\n{run}: {counts}")
    # The numbers the report quotes (pinned so a change is seen).
    expected = {
        "hard-explain-trace": {"recorded": 12, "claude+map": 11, "generic+map+paths": 4},
        "hard-cross-module-bug.r2": {"recorded": 10, "claude+map": 9, "generic+map+paths": 5},
    }[run]
    assert counts == expected
