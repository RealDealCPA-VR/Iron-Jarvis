"""v1.400.0 review of TEAM-FIRST: TONIGHT's run, end to end.

TONIGHT (2026-10-27, from 11 PM) the benchmark runs two invocations in one
``--root``, with the user's real team (``tests/_fakes/bench_tonight_v1396``:
planner claude-cli / sonnet; ``glm`` on the custom endpoint, roles read code
test; ``codex`` on codex-cli, roles code review):

* A: ``--runner jarvis-team --team <plain file> --repeats 3 --effort medium
  --with-alone`` — the team as today, then Sonnet alone;
* B: the same with a copy of the file that says ``team_first = true``, and
  without ``--with-alone``.

These pins drive BOTH invocations through the real command line, the real
bench, the real chat lane and the real report, with scripted models (the
planner hands pieces out only when the team-first words are in its system
prompt, as the 2026-10-26 live planner did not), and check what the user
will read in the morning — in that order, the other way round, and with B
killed and A cut off before its alone row.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from iron_jarvis.agents import model_team as MT
from iron_jarvis.harness_bench import team_report as TRP
from tests._fakes import bench_team_v1396 as W
from tests._fakes import bench_tonight_v1396 as N

BOTH = f"{W.TASK_A},{W.TASK_B}"


def _first(text: str) -> str:
    return text.replace("[planner]\n", "[planner]\nteam_first = true\n", 1)


@pytest.fixture
def gateway():
    g = N.Gateway()
    try:
        yield g
    finally:
        g.close()


class Night:
    """Both invocations of tonight, through ``bench-harness run``."""

    def __init__(self, tmp_path: Path, monkeypatch, gateway: N.Gateway) -> None:
        from iron_jarvis.harness_bench import bench as bench_mod

        monkeypatch.setenv("IRON_PROXY_DATA_DIR", str(tmp_path / "ipd"))
        self.root = tmp_path / "bench-root"
        self.plain = tmp_path / "team-plain.toml"
        self.plain.write_text(N.team_toml(), encoding="utf-8")
        self.first = tmp_path / "team-first.toml"
        self.first.write_text(_first(N.team_toml()), encoding="utf-8")
        #: one row per model call: who answered, and what its prompt held
        self.calls: list[dict[str, Any]] = []
        self.runners: list[Any] = []
        night = self
        real_make = bench_mod.make_runner
        real_stream = W.Double.stream

        async def stream(double, *, system, messages, tools, **kw):
            platform = getattr(double.world, "platform", None)
            night.calls.append({
                "invocation": getattr(double.world, "invocation", ""),
                "provider": double.provider,
                "team_shown": "# Your model team" in (system or ""),
                "words": MT.TEAM_FIRST_LEAD in (system or ""),
                "setting": getattr(getattr(platform, "config", None), "team_first", None),
                "sentence": any(str(t.get("description") or "").startswith("Your model team is listed under")
                                for t in tools or ()),
            })
            async for frame in real_stream(double, system=system, messages=messages, tools=tools, **kw):
                yield frame

        monkeypatch.setattr(W.Double, "stream", stream)

        def make_runner(*a, **kw):
            runner = real_make(*a, **kw)
            w = N.wire(runner.platform, monkeypatch, gateway=gateway)
            # The planner works as a team ONLY when told to.
            w.team_words = MT.TEAM_FIRST_LEAD
            w.platform = runner.platform
            w.invocation = night.label
            night.runners.append(runner)
            return runner

        monkeypatch.setattr(bench_mod, "make_runner", make_runner)
        self.label = ""

    def run(self, label: str, team: Path, *, with_alone: bool, repeats: int = 3, tasks: str = BOTH,
            extra: tuple[str, ...] = ()) -> str:
        from typer.testing import CliRunner

        from iron_jarvis.harness_bench.cli import bench_app

        self.label = label
        args = ["run", "--runner", "jarvis-team", "--root", str(self.root), "--tier", "frontier",
                "--tasks", tasks, "--team", str(team), "--rounds", "12", "--repeats", str(repeats),
                "--effort", "medium", *(["--with-alone"] if with_alone else []), *extra]
        out = CliRunner().invoke(bench_app, args)
        assert out.exit_code == 0, out.output
        return out.output

    @property
    def bench(self) -> Path:
        return self.root / ".ironjarvis" / "harness-bench"

    def configs(self) -> list[tuple[dict, list[dict]]]:
        data = json.loads((self.bench / "report.json").read_text(encoding="utf-8"))
        return [(row["config"], row["records"]) for row in data]

    def report(self) -> str:
        return (self.bench / "report.md").read_text(encoding="utf-8")


def _setups(configs):
    team = [(c, r) for c, r in configs if c["runner"] == "jarvis-team" and not TRP.is_team_first(c)]
    first = [(c, r) for c, r in configs if TRP.is_team_first(c)]
    alone = [(c, r) for c, r in configs if c["runner"] == "jarvis"]
    return team, first, alone


def _section(report: str, config: dict) -> str:
    """The comparison of ONE team setup (its table, its numbers, its answer)."""
    head = f"**Team {str(config['key'])[:12]}"
    part = report.split("## The team beside one model", 1)[1]
    return part.split(head, 1)[1].split("**Team ", 1)[0]


def test_tonights_two_invocations_end_to_end(tmp_path, monkeypatch, gateway):
    night = Night(tmp_path, monkeypatch, gateway)
    a_out = night.run("A", night.plain, with_alone=True)
    b_out = night.run("B", night.first, with_alone=False)

    # THE ALONE RUN IN A NEVER SEES TEAM-FIRST (nor the team), and the plain
    # team in A is shown the team without the words; B's planner gets them
    # on every call, with the setting on only while B runs.
    a_calls = [c for c in night.calls if c["invocation"] == "A" and c["provider"] == N.PLANNER[0]]
    b_calls = [c for c in night.calls if c["invocation"] == "B" and c["provider"] == N.PLANNER[0]]
    assert a_calls and b_calls
    assert not any(c["words"] or c["sentence"] for c in a_calls)
    assert any(c["team_shown"] for c in a_calls) and any(not c["team_shown"] for c in a_calls)
    assert all(c["setting"] is False for c in a_calls)
    assert all(c["words"] and c["sentence"] and c["team_shown"] and c["setting"] is True for c in b_calls)
    # Every member's own run (glm, codex) is never told to hand work on.
    members = [c for c in night.calls if c["provider"] != N.PLANNER[0]]
    assert members and not any(c["words"] or c["sentence"] for c in members)
    # Each invocation's platform has the setting back off when it ends.
    assert [r.platform.config.team_first for r in night.runners] == [False, False]

    configs = night.configs()
    team, first, alone = _setups(configs)
    assert (len(team), len(first), len(alone)) == (1, 1, 1)
    # NOTHING IN B REUSES A'S TEAM FOLDER: three folders, B's team runs all
    # its own (none reused), A's alone is not run again.
    assert len({c["folder"] for c, _r in configs}) == 3
    assert first[0][0]["key"] != team[0][0]["key"]
    assert not any(r.get("reused") for r in first[0][1])
    assert len(first[0][1]) == len(team[0][1]) == len(alone[0][1]) == 6
    # B's ALONE PAIRING FINDS A'S ALONE ROW (and so does A's team).
    pairs = [(c, r) for c, r in configs]
    for config, records in (team[0], first[0]):
        found = TRP.find_alone(config, records, pairs)
        assert found is not None and found[0]["key"] == alone[0][0]["key"]

    # THE COMBINED REPORT: three setups, the two teams told apart, each
    # answered against the same alone row, the team at work for both.
    report = night.report()
    head = report.split("\n## ", 1)[0]
    rows = [ln.split(" | ")[0] for ln in head.splitlines() if ln.startswith("| jarvis")]
    assert sorted(rows) == ["| jarvis", "| jarvis-team", "| jarvis-team (team-first)"]
    plain_part, first_part = _section(report, team[0][0]), _section(report, first[0][0])
    alone_key = str(alone[0][0]["key"])[:12]
    for part in (plain_part, first_part):
        assert f"| {alone_key} | jarvis |" in part
        assert f"The answer compares with {alone_key} (jarvis, Iron Jarvis {alone[0][0]['version']})." in part
    assert "**The team at work:** The planner handed at least one piece to a team member in 0 of 6 runs." \
        in plain_part
    assert "**The team at work:** The planner handed at least one piece to a team member in 6 of 6 runs." \
        in first_part
    assert f"**The answer:** {TRP.NO_MEMBER_WORK}" in plain_part
    assert "**The answer:** Yes on these runs: as many tasks, with less Claude list-price." in first_part
    # The command line said each team's number once.
    assert [ln for ln in a_out.splitlines() if ln.startswith("The planner handed")] == [
        "The planner handed at least one piece to a team member in 0 of 6 runs."]
    assert [ln for ln in b_out.splitlines() if ln.startswith("The planner handed")] == [
        "The planner handed at least one piece to a team member in 6 of 6 runs."]


def test_b_first_a_killed_after_its_team_then_a_again(tmp_path, monkeypatch, gateway):
    """The other orders the night can take: B before A; B KILLED (its members
    left in the home, the setting on in its dead process); A cut off after
    its team, before its alone row; A run again. Each setup's runs are its
    own, nothing of team-first reaches A, and the report ends right."""
    from iron_jarvis.harness_bench import team as T
    from iron_jarvis.platform import build_platform

    night = Night(tmp_path, monkeypatch, gateway)
    night.run("B", night.first, with_alone=False)
    # B alone in the root: answered "not enough", never against nothing.
    report = night.report()
    (first_cfg, _r), = [(c, r) for c, r in night.configs() if TRP.is_team_first(c)]
    part = _section(report, first_cfg)
    assert "**The answer:** not enough runs to say: there is no Claude-alone row" in part
    assert "The answer compares with" not in part
    # B killed mid-run: its members stay registered in the home and its
    # process had the setting on — a process that is gone takes that with it.
    dead = build_platform(str(night.root))
    T.JarvisTeamRunner(dead, team=T.load_team_file(night.first)).prepare()
    assert dead.config.team_first is True
    # A's team, then A cut off before its alone row.
    night.run("A1", night.plain, with_alone=False)
    # A again, with its alone row: the team's runs are reused, not re-run.
    night.run("A2", night.plain, with_alone=True)
    a_calls = [c for c in night.calls if c["invocation"].startswith("A") and c["provider"] == N.PLANNER[0]]
    assert a_calls and not any(c["words"] or c["sentence"] or c["setting"] for c in a_calls)
    assert not any(c["team_shown"] for c in a_calls if c["invocation"] == "A2")
    team, first, alone = _setups(night.configs())
    assert (len(team), len(first), len(alone)) == (1, 1, 1)
    # (the ledger keeps what each invocation reused)
    team_ledger = json.loads((night.bench / team[0][0]["folder"] / "ledger.json").read_text(encoding="utf-8"))
    alone_ledger = json.loads((night.bench / alone[0][0]["folder"] / "ledger.json").read_text(encoding="utf-8"))
    assert [len(i["reused"]) for i in team_ledger["invocations"]] == [0, 6]
    assert [len(i["reused"]) for i in alone_ledger["invocations"]] == [0]
    report = night.report()
    alone_key = str(alone[0][0]["key"])[:12]
    for config, handed in ((team[0][0], "0 of 6"), (first[0][0], "6 of 6")):
        part = _section(report, config)
        assert f"The answer compares with {alone_key} (jarvis, Iron Jarvis" in part
        assert f"to a team member in {handed} runs." in part
    assert f"**The answer:** {TRP.NO_MEMBER_WORK}" in _section(report, team[0][0])

