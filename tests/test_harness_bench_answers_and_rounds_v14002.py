"""v1.400.2 — two benchmark fixes found while reading v1.400.0's team tier.

1. An answer file is read in the encoding the agent wrote it in. An agent on
   Windows writes ``answer.json`` however its shell writes files, and Windows
   PowerShell 5.1's ``Out-File`` (and ``>``) writes UTF-16 with a BOM. The core
   rubric (:func:`grading.run_rubric`) and frontier-legacy-callers' hidden check
   read UTF-8 only, so a RIGHT answer saved that way failed as "not valid
   JSON". The team tier's checks already read the bytes; now every reader does.

2. A task may ask for more tool rounds than the runner's budget
   (``rounds`` in ``task.toml``, :func:`catalog.rounds_for`). The team tier's
   jobs read or change dozens of files; a planner working alone could run out
   of the runner's 60 rounds where a team would not, and the benchmark would
   then measure a limit the product does not have (a Build reply has had no
   step limit since v1.385.0, only a safety stop at 1000). The team tasks ask
   for that ceiling, so their time limit decides; every other tier keeps 60.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from iron_jarvis.daemon.chat_turn import MAX_TURN_ROUNDS
from iron_jarvis.platform import build_platform
from iron_jarvis.harness.mode import BUILD_SAFETY_ROUNDS
from iron_jarvis.harness_bench.bench import run_bench
from iron_jarvis.harness_bench.catalog import MAX_TASK_ROUNDS, TaskError, load_task, rounds_for
from iron_jarvis.harness_bench.grading import grade, run_rubric
from iron_jarvis.harness_bench.runners import DEFAULT_ROUNDS, JarvisRunner
from iron_jarvis.harness_bench.setup_step import prepare_workspace
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from tests.test_harness_bench_v1363 import TASKS, apply_solution

RUBRIC_TASKS = sorted(t.id for t in TASKS.values() if t.rubric)
#: How an agent's shell can save a file on Windows: plain UTF-8, UTF-8 with a
#: BOM (PowerShell 7's ``-Encoding utf8BOM``, Notepad), and the UTF-16 with a
#: BOM that Windows PowerShell 5.1's ``Out-File`` and ``>`` write.
ENCODINGS = ["utf-8", "utf-8-sig", "utf-16"]


# --------------------------------------------------------------------------- #
# 1. Answers in any encoding
# --------------------------------------------------------------------------- #


def _right_answer(task) -> str:
    """A right answer for ``task``'s rubric: the expected keys, and every
    required phrase (its first spelling) in the text."""
    body: dict = dict(task.rubric.get("json_equals") or {})
    phrases = [p[0] if isinstance(p, list) else p for p in task.rubric.get("required_phrases") or []]
    if phrases:
        body["notes"] = " ".join(str(p) for p in phrases)
    return json.dumps(body)


@pytest.mark.parametrize("encoding", ENCODINGS)
@pytest.mark.parametrize("task_id", RUBRIC_TASKS)
def test_a_right_answer_passes_the_rubric_in_any_encoding(task_id, encoding, tmp_path):
    task = TASKS[task_id]
    answer = tmp_path / str(task.rubric["answer_file"])
    answer.write_bytes(_right_answer(task).encode(encoding))
    part = run_rubric(task, tmp_path)
    assert part["passed"] is True, (part["reason"], encoding)
    assert "﻿" not in part["output"] and "\x00" not in part["output"]


def test_a_wrong_utf16_answer_is_wrong_not_unreadable(tmp_path):
    task = TASKS["explain-retry-default"]
    (tmp_path / "answer.json").write_bytes(json.dumps({"retries": 99, "file": "nowhere.py"}).encode("utf-16"))
    part = run_rubric(task, tmp_path)
    assert part["passed"] is False
    assert part["reason"].startswith("the answer is wrong"), part["reason"]


def test_a_file_that_is_not_utf_anything_reads_as_before(tmp_path):
    # A cp1252 dash (0x96) in a plain-text answer: decoded as UTF-8 with a
    # replacement character, exactly as the rubric did before v1.400.2.
    task = TASKS["explain-retry-default"]
    raw = b'{"retries": 99, "file": "x.py", "note": "a \x96 b"}'
    (tmp_path / "answer.json").write_bytes(raw)
    part = run_rubric(task, tmp_path)
    assert part["output"] == raw.decode("utf-8", errors="replace")


@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16"])
def test_the_legacy_callers_check_reads_a_right_answer_in_any_encoding(encoding, tmp_path):
    task = TASKS["frontier-legacy-callers"]
    work = prepare_workspace(task, tmp_path / "w")
    apply_solution(task.id, work)
    answer = work / "answer.json"
    answer.write_bytes(answer.read_bytes().decode("utf-8").encode(encoding))
    g = grade(task, work, tmp_path / "grade")
    assert g.passed is True, (g.reason, g.output[-2000:])


# --------------------------------------------------------------------------- #
# 2. Rounds a task asks for
# --------------------------------------------------------------------------- #


def test_the_team_tasks_ask_for_the_products_own_ceiling_and_no_other_tier_does():
    assert MAX_TASK_ROUNDS == MAX_TURN_ROUNDS == BUILD_SAFETY_ROUNDS == 1000
    for task in TASKS.values():
        if task.tier == "team":
            assert task.rounds == MAX_TASK_ROUNDS, task.id
            assert rounds_for(task, DEFAULT_ROUNDS) == MAX_TASK_ROUNDS, task.id
        else:
            assert task.rounds == 0, task.id
            assert rounds_for(task, DEFAULT_ROUNDS) == DEFAULT_ROUNDS, task.id


def test_a_task_never_lowers_the_runners_budget():
    task = TASKS["bugfix-leap-year"]
    assert rounds_for(replace(task, rounds=10), 60) == 60
    assert rounds_for(replace(task, rounds=200), 60) == 200
    assert rounds_for(replace(task, rounds=0), 25) == 25


def _task_dir(root: Path, rounds_line: str) -> Path:
    folder = root / "rounds-task"
    (folder / "repo").mkdir(parents=True)
    (folder / "repo" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (folder / "task.md").write_text("Say what x is in answer.json.\n", encoding="utf-8")
    (folder / "task.toml").write_text(
        f'category = "explain"\ntitle = "t"\n{rounds_line}\n[rubric]\nanswer_file = "answer.json"\n',
        encoding="utf-8",
    )
    return folder


@pytest.mark.parametrize("value", ["0", "-1", "1001", "true", '"100"', "1.5"])
def test_a_bad_rounds_value_is_refused(value, tmp_path):
    with pytest.raises(TaskError, match="rounds must be a whole number from 1 to 1000"):
        load_task(_task_dir(tmp_path, f"rounds = {value}"))


def test_no_rounds_line_means_the_runners_budget(tmp_path):
    assert load_task(_task_dir(tmp_path, "")).rounds == 0
    assert load_task(_task_dir(tmp_path / "b", "rounds = 250")).rounds == 250


def _endless_tools(platform, seen: list[bool], *, answer_at: int | None = None):
    async def stream(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        seen.append(bool(tools))
        if tools and (answer_at is None or len(seen) < answer_at):
            resp = LLMResponse(text="", tool_calls=[ToolCall(id=f"r{len(seen)}", name="list_files", arguments={})],
                               usage={"input_tokens": 5, "output_tokens": 1})
        else:
            resp = LLMResponse(text="Done.", usage={"input_tokens": 5, "output_tokens": 1})
        yield {"type": "final", "response": resp, "provider": "scripted", "model": "s"}

    return stream


async def test_the_runner_gives_a_task_the_rounds_it_asks_for(tmp_path, monkeypatch):
    """A model that never stops asking for tools, on a task asking for 6 rounds
    under a runner budget of 4: the turn runs 6, then one tool-less answer."""
    platform = build_platform(str(tmp_path / "root"))
    seen: list[bool] = []
    monkeypatch.setattr(platform.router, "stream", _endless_tools(platform, seen))
    runner = JarvisRunner(platform, provider="scripted", model="s", round_budget=4)
    task = replace(TASKS["bugfix-leap-year"], rounds=6)
    res = await run_bench(runner=runner, home=platform.config.home, tasks=[task], names=[task.id])
    rec = res.records[0]
    assert seen == [True] * 6 + [False]
    assert rec["model_rounds"] == 6 and rec["facts"]["cut_off"] is True


async def test_a_turn_that_ends_inside_the_tasks_rounds_was_not_cut_off(tmp_path, monkeypatch):
    """Five rounds on a task asking for 6, under a runner budget of 4: the
    model answered on its own, so it was NOT cut off (the old check compared
    with the runner's 4 and said it was)."""
    platform = build_platform(str(tmp_path / "root"))
    seen: list[bool] = []
    monkeypatch.setattr(platform.router, "stream", _endless_tools(platform, seen, answer_at=5))
    runner = JarvisRunner(platform, provider="scripted", model="s", round_budget=4)
    task = replace(TASKS["bugfix-leap-year"], rounds=6)
    res = await run_bench(runner=runner, home=platform.config.home, tasks=[task], names=[task.id])
    rec = res.records[0]
    assert seen == [True] * 5
    assert rec["model_rounds"] == 5 and rec["facts"]["cut_off"] is False
