"""v1.403.0 — THE CODING-SESSION TEAM in the benchmark.

A planner TOLD to hand work to its team handed out nothing in 108 runs
(v1.400.0's team-first words). ``[planner] team = true`` in a team file runs
the planner as a coding session's team LEAD instead — locked (no tool that
changes a file or runs a command), its ``code`` members its workers and its
``review`` member its reviewer — so the result can be measured again:

* the file takes ``team = true|false`` (a non-bool, more than two workers, a
  second reviewer or a member that is neither is refused in words);
* ``team_mode`` is in the identity ONLY when true;
* every task's turn runs with the session's team armed, the planner shown no
  Write / Edit / Bash, the piece routed to the worker and the change to the
  reviewer — through the real bench, the real lane and the real report,
  which names the setup ``lead (locked)``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.harness import team as HT
from iron_jarvis.harness_bench import team as T
from iron_jarvis.harness_bench import team_report as TRP
from iron_jarvis.harness_bench.bench import BenchRefused, run_bench
from iron_jarvis.harness_bench.catalog import load_tasks
from iron_jarvis.platform import build_platform
from tests._fakes import bench_team_v1396 as W

TASKS = load_tasks()


def _toml(team: "bool | str | None", extra: str = "") -> str:
    text = W.team_toml() + extra
    if team is None:
        return text
    value = team if isinstance(team, str) else ("true" if team else "false")
    return text.replace("[planner]\n", f"[planner]\nteam = {value}\n", 1)


def _spec(tmp_path: Path, team: "bool | str | None" = None, name: str = "team.toml", extra: str = "") -> T.TeamSpec:
    path = tmp_path / name
    path.write_bytes(_toml(team, extra).encode("utf-8"))
    return T.load_team_file(path)


def test_the_file_takes_team_true_or_false_and_names_the_sessions_team(tmp_path):
    assert _spec(tmp_path).team_mode is False and _spec(tmp_path).session_team() is None
    on = _spec(tmp_path, True)
    assert on.team_mode is True
    assert on.session_team() == HT.SessionTeam(workers=("glm",), reviewer="codex")
    with pytest.raises(BenchRefused, match=r"team is true or false \(without quotes\)"):
        _spec(tmp_path, '"yes"')


@pytest.mark.parametrize("extra,words", [
    ('\n[[members]]\nname = "w2"\nprovider = "fleet-spark"\nmodel = "glm-5.3-flash"\nroles = ["code"]\n'
     '\n[[members]]\nname = "w3"\nprovider = "fleet-spark"\nmodel = "glm-5.3-flash"\nroles = ["code"]\n',
     "at most 2 workers"),
    ('\n[[members]]\nname = "rev2"\nprovider = "codex-cli"\nmodel = "gpt-6-astra"\nroles = ["review"]\n',
     "at most one reviewer"),
    ('\n[[members]]\nname = "reader"\nprovider = "fleet-spark"\nmodel = "glm-5.3-flash"\nroles = ["read"]\n',
     "reader is neither"),
])
def test_a_team_mode_file_that_is_not_a_session_team_is_refused(tmp_path, extra, words):
    with pytest.raises(BenchRefused, match=words):
        _spec(tmp_path, True, extra=extra)
    # The same file without team = true is a team as before.
    assert _spec(tmp_path, None, "plain.toml", extra=extra).team_mode is False


def test_team_mode_is_in_the_identity_only_when_true(tmp_path):
    absent, off, on = _spec(tmp_path, None, "a.toml"), _spec(tmp_path, False, "b.toml"), _spec(tmp_path, True, "c.toml")
    assert absent.identity() == off.identity() and absent.key == off.key
    assert "team_mode" not in absent.identity()
    assert on.identity() == {**off.identity(), "team_mode": True} and on.key != off.key


def test_the_runner_arms_the_sessions_team_and_shows_the_lead_no_writing_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "root"))
    W.wire(platform, monkeypatch)
    plain = T.JarvisTeamRunner(platform, team=_spec(tmp_path, None, "p.toml"), round_budget=12)
    lead = T.JarvisTeamRunner(platform, team=_spec(tmp_path, True, "l.toml"), round_budget=12)
    with plain.turn_scope():
        assert HT.current() is None
    with lead.turn_scope():
        assert HT.current() == HT.SessionTeam(workers=("glm",), reviewer="codex")
    assert HT.current() is None
    plain_tools = set(plain.identity()["tools"])
    lead_id = lead.identity()
    lead_tools = set(lead_id["tools"])
    # Claude Code's own names: the lead keeps Read / Grep / Glob / Agent and
    # loses every tool that writes or runs.
    assert {"Write", "Edit", "Bash"} <= plain_tools
    assert not lead_tools & {"Write", "Edit", "MultiEdit", "Bash", "NotebookEdit"}
    assert {"Read", "Agent"} <= lead_tools
    assert lead_id["team"]["team_mode"] is True and "team_mode_sha256" in lead_id
    assert "team_mode_sha256" not in plain.identity()
    assert any(T.TEAM_MODE_LINE in line for line in lead.dry_run_lines())


def test_the_report_names_a_team_mode_setup_lead_locked():
    config = {"runner": T.TEAM_RUNNER, "harness": {"team": {"team_mode": True}}}
    assert TRP.runner_label(config) == "jarvis-team · lead (locked)"
    both = {"runner": T.TEAM_RUNNER, "harness": {"team": {"team_mode": True, "team_first": True}}}
    assert TRP.runner_label(both) == "jarvis-team (team-first) · lead (locked)"
    assert TRP.runner_label({"runner": T.TEAM_RUNNER, "harness": {"team": {}}}) == "jarvis-team"


async def test_a_team_mode_run_hands_the_piece_to_the_worker_and_the_change_to_the_reviewer(
        tmp_path, monkeypatch):
    monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
    platform = build_platform(str(tmp_path / "root"))
    world = W.wire(platform, monkeypatch)
    systems: list[str] = []

    def script(setup: str, task: str) -> list:
        # A LEAD (its brief in the system prompt) hands the piece out by role.
        return [W.agent("p-a1", "code", "Fix recur/rules.py so a monthly series never drifts or skips a month."),
                "The team fixed recur/rules.py."] if task == W.TASK_A else ["Done."]

    real_stream = W.Double.stream

    async def stream(self, *, system, messages, tools, **kw):
        if self.provider == W.PLANNER[0]:
            systems.append(system or "")
        async for frame in real_stream(self, system=system, messages=messages, tools=tools, **kw):
            yield frame

    monkeypatch.setattr(W, "planner_script", script)
    monkeypatch.setattr(W.Double, "stream", stream)
    runner = T.JarvisTeamRunner(platform, team=_spec(tmp_path, True), round_budget=12)
    res = await run_bench(runner=runner, home=platform.config.home, tasks=TASKS, names=[W.TASK_A], seed=0)
    (rec,) = res.records
    assert rec["ok_run"] is True, rec.get("error")
    assert rec["passed"] is True
    assert systems and HT.TEAM_BRIEF_HEADER in systems[0] and "custom:glm" in systems[0]
    by_member = {(h["member"], h["role"]) for h in rec["helpers"]}
    assert ("glm", "code") in by_member and ("codex", "review") in by_member
    assert TRP.handed_to_a_member(rec)
    report = (res.config_dir / "report.md").read_text(encoding="utf-8")
    assert "lead (locked)" in report
    assert world.calls  # the doubles really ran
