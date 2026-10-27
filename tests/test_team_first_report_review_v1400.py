"""v1.400.0 review of TEAM-FIRST: the combined report, as tonight's root
will hold it.

``C:\\ijb`` already holds last night's two setups (Iron Jarvis 1.396.1: the
team and Sonnet alone, same planner, model and effort); tonight adds three.
Each team setup must be answered against the alone row of its OWN Iron
Jarvis, the table must show the row its answer compares with (and say which
it is), and "The team at work" — the first number the user looks for — must
count every run in which the planner handed a piece out.
"""

from __future__ import annotations

from iron_jarvis.harness_bench import team_report as TRP
from iron_jarvis.harness_bench.ledger import PLANNER
from iron_jarvis.harness_bench.report import combined_report


def _section(report: str, config: dict) -> str:
    """The comparison of ONE team setup (its table, its numbers, its answer)."""
    head = f"**Team {str(config['key'])[:12]}"
    part = report.split("## The team beside one model", 1)[1]
    return part.split(head, 1)[1].split("**Team ", 1)[0]


def _cfg(runner: str, key: str, *, team_first: bool = False, version: str = "1.400.0",
         created: str = "2026-10-28T01:00:00Z") -> dict:
    harness = {"team": {"planner": {}, "members": [], **({"team_first": True} if team_first else {})}} \
        if runner == "jarvis-team" else {}
    return {"runner": runner, "provider": "claude-cli", "model": "sonnet", "effort": "medium", "key": key,
            "version": version, "harness": harness, "created_at": created}


def _run(task: str, repeat: int, passed: "bool | None", cost: float, *, worked: "bool | None" = None) -> dict:
    parts = {PLANNER: {"vendor": "claude", "provider": "claude-cli", "model": "sonnet",
                       "cost_list_price_usd": cost, "cost_metered_usd": 0.0, "tokens_in": 100, "tokens_out": 10,
                       "model_calls": 5, "pieces": {}}}
    helpers = []
    if worked is not None:
        parts["glm"] = {"vendor": "local", "provider": "custom", "model": "glm-5.3-flash", "cost_list_price_usd": 0.0,
                        "cost_metered_usd": 0.0, "tokens_in": 500 if worked else 0, "tokens_out": 50 if worked else 0,
                        "model_calls": 2 if worked else 0, "pieces": {"code": 1} if worked else {}}
        if worked:
            helpers = [{"id": "h1", "member": "glm", "role": "code", "state": "done", "reason": "local"}]
    return {"task_id": task, "tier": "frontier", "category": "bugfix", "passed": passed, "ok_run": True,
            "parts": parts, "helpers": helpers, "tool_calls": 3, "model_rounds": 2, "wall_s": 10.0,
            "tokens_in": 100, "tokens_out": 10, "cost_metered_usd": 0.0, "cost_list_price_usd": cost,
            **({"repeat": repeat} if repeat > 1 else {})}


TASKS3 = [(t, k) for t in ("frontier-a", "frontier-b") for k in (1, 2, 3)]


def _last_night_and_tonight():
    """Last night (1.396.1): the team handed nothing; Sonnet alone failed
    everything. Tonight (1.400.0): the plain team, Sonnet alone (passes all),
    and team-first — the same tasks and repeats, so every alone row shares
    every run with every team row."""
    night_team = (_cfg("jarvis-team", "11111111aaaa", version="1.396.1", created="2026-10-27T03:04:19Z"),
                  [_run(t, k, True, 0.30, worked=False) for t, k in TASKS3])
    night_alone = (_cfg("jarvis", "22222222bbbb", version="1.396.1", created="2026-10-27T03:41:38Z"),
                   [_run(t, k, False, 0.30) for t, k in TASKS3])
    team = (_cfg("jarvis-team", "33333333cccc", created="2026-10-28T03:00:00Z"),
            [_run(t, k, True, 0.30, worked=False) for t, k in TASKS3])
    alone = (_cfg("jarvis", "44444444dddd", created="2026-10-28T05:00:00Z"),
             [_run(t, k, True, 0.30) for t, k in TASKS3])
    first = (_cfg("jarvis-team", "55555555eeee", team_first=True, created="2026-10-28T06:00:00Z"),
             [_run(t, k, True, 0.10, worked=True) for t, k in TASKS3])
    return night_team, night_alone, team, alone, first


def test_each_team_is_answered_against_the_alone_row_of_its_own_iron_jarvis():
    """C:\\ijb already holds last night's team and Sonnet-alone rows (1.396.1).
    Tonight's rows share every task and repeat with them, and the alone row
    the answer used was picked by runs in common, then the NEWEST — so last
    night's team was re-answered against TONIGHT's alone row (another Iron
    Jarvis, another harness), and its numbers changed overnight. The same
    version comes first; the numbers decide only among those."""
    night_team, night_alone, team, alone, first = _last_night_and_tonight()
    pairs = [(c, r) for c, r in (night_team, night_alone, team, alone, first)]
    assert TRP.find_alone(*night_team, pairs)[0] is night_alone[0]
    assert TRP.find_alone(*team, pairs)[0] is alone[0]
    assert TRP.find_alone(*first, pairs)[0] is alone[0]
    # Another version is still the answer when it is the only alone row
    # (tonight's B before A's alone has run, against last night's).
    assert TRP.find_alone(*first, [night_team, night_alone, first])[0] is night_alone[0]


def test_the_table_shows_the_alone_row_its_answer_compares_with_and_names_it():
    """Two team setups with DIFFERENT repeats (team-first stopped after its
    second repeat): the team-first table held only its own row — the alone
    row its answer compared with (3 repeats) was not in it, nor said."""
    team = (_cfg("jarvis-team", "aaaaaaaaaaaa1111"),
            [_run(t, k, True, 0.30, worked=False) for t, k in TASKS3])
    first = (_cfg("jarvis-team", "bbbbbbbbbbbb2222", team_first=True),
             [_run(t, k, True, 0.10, worked=True) for t, k in TASKS3 if k <= 2])
    alone = (_cfg("jarvis", "cccccccccccc3333"), [_run(t, k, True, 0.30) for t, k in TASKS3])
    report = combined_report([team, first, alone])
    first_part = _section(report, first[0])
    plain_part = _section(report, team[0])
    assert "| cccccccccccc | jarvis |" in first_part
    for part in (first_part, plain_part):
        assert "The answer compares with cccccccccccc (jarvis, Iron Jarvis 1.400.0)." in part
    assert first_part.index("The answer compares with") < first_part.index("**The answer:**")
    # Its numbers are its own runs' (3 repeats); the answer's are the runs both made.
    assert "Claude alone 4 of 4 (the 4 runs both made" in first_part


def test_a_comparison_across_versions_says_so():
    night_team, night_alone, _team, _alone, first = _last_night_and_tonight()
    # B before A's alone: team-first is answered against last night's alone.
    report = combined_report([night_team, night_alone, first])
    part = _section(report, first[0])
    assert ("The answer compares with 22222222bbbb (jarvis, Iron Jarvis 1.396.1), which ran on another "
            "Iron Jarvis than this team (1.400.0).") in part
    # The only alone row at another effort: said too.
    high = ({**night_alone[0], "version": "1.400.0", "effort": "high"}, night_alone[1])
    part = _section(combined_report([high, first]), first[0])
    assert ("The answer compares with 22222222bbbb (jarvis, Iron Jarvis 1.400.0), at effort high "
            "(this team's planner: medium).") in part


def test_a_run_whose_member_could_not_work_still_counts_as_handed():
    """The planner handed a piece; the member's server was down, so the run
    is "did not run" (``member_infra`` — never the team's failure). It was
    counted with the runs that never got a go at all and LEFT OUT of "handed
    in N of M": the first number the user reads understated how often
    team-first handed work out (glm's box down for an hour tonight would
    have shown it)."""
    member_down = {**_run("frontier-c", 1, None, 0.05, worked=True), "unscored_by": "infrastructure",
                   "failure_kind": "member_infra"}
    member_down["helpers"][0]["state"] = "failed"
    planner_limit = {**_run("frontier-d", 1, None, 0.0), "unscored_by": "infrastructure",
                     "failure_kind": "limit", "parts": {}}
    records = [_run("frontier-a", 1, True, 0.10, worked=True), _run("frontier-b", 1, True, 0.10, worked=False),
               member_down, planner_limit]
    assert TRP.handed_count(records) == (2, 3, 1)
    assert TRP.handed_line(records) == (
        "The planner handed at least one piece to a team member in 2 of 3 runs (1 did not run).")


def test_a_setup_where_nothing_ran_says_it_is_not_known():
    """Every run of the setup did not run (the planner's account at its limit
    all night): "in 0 of 0 runs" read like a number."""
    gone = [{**_run(t, k, None, 0.0), "unscored_by": "infrastructure", "parts": {}} for t, k in TASKS3]
    assert TRP.handed_line(gone) == (
        "Not known: all 6 runs did not run, so none could hand a piece to a team member.")
    one = gone[:1]
    assert TRP.handed_line(one) == "Not known: the 1 run did not run, so it could not hand a piece to a team member."

