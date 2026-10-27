"""v1.399.2 — the team report never gives a verdict about a team that did
not work as a team.

The 2026-10-26 live run (12 frontier tasks x 3 repeats, a Claude Sonnet
planner with glm and Codex as members): the team passed 35 of 36, Claude
alone 35 of 36, the team used 2% less Claude list-price — and the planner
handed ZERO pieces to a member in all 36 runs. The report still said "Yes on
these runs: as many tasks, with less Claude list-price": the planner's own
run-to-run variation read as a verdict about the team. Now a team row whose
planner handed nothing to a member gets ``NO_MEMBER_WORK`` before any other
verdict, and a team that worked in only some runs says how many.
"""

from __future__ import annotations

import pytest

from iron_jarvis.harness_bench import team_report as TRP
from iron_jarvis.harness_bench.ledger import PLANNER

TEAM_CFG = {"runner": "jarvis-team", "provider": "claude-cli", "model": "sonnet"}
ALONE_CFG = {"runner": "jarvis", "provider": "claude-cli", "model": "sonnet"}


def _planner(cost):
    return {"vendor": "claude", "cost_list_price_usd": cost, "cost_metered_usd": 0.0,
            "tokens_in": 100, "tokens_out": 10, "model_calls": 7}


def _idle_member(vendor):
    # What the live night's records held: the member's part, with no calls.
    return {"vendor": vendor, "cost_list_price_usd": 0.0, "cost_metered_usd": 0.0,
            "tokens_in": 0, "tokens_out": 0, "model_calls": 0, "pieces": {}}


def _night(task, passed, cost, *, worked=False, helpers=()):
    parts = {PLANNER: _planner(cost), "codex": _idle_member("codex"), "glm": _idle_member("local")}
    if worked:
        parts["glm"] = {**parts["glm"], "tokens_in": 500, "tokens_out": 50, "model_calls": 3,
                        "pieces": {"code": 1}}
    return {"task_id": task, "passed": passed, "parts": parts, "helpers": list(helpers)}


def _alone(task, passed, cost):
    return {"task_id": task, "passed": passed, "parts": {PLANNER: _planner(cost)}}


ALONE = [_alone("a", True, 0.30), _alone("b", True, 0.30)]


def test_the_live_nights_shape_reads_no_answer_never_yes():
    team = [_night("a", True, 0.29), _night("b", True, 0.29)]
    words = TRP.answer((TEAM_CFG, team), (ALONE_CFG, ALONE))
    assert words.startswith(TRP.NO_MEMBER_WORK), words
    assert "Yes" not in words
    # The numbers are still said, after the "no answer".
    assert "The team passed 2 of 2, Claude alone 2 of 2" in words
    assert "Claude list-price: $0.5800 for the team vs $0.6000 alone" in words
    # A memberless team gets no "members worked in …" line either.
    assert "Team members worked in" not in words


def _unknown_price_night():
    rec = _night("a", True, 0.29)
    rec["parts"][PLANNER] = _planner(None)
    return rec


@pytest.mark.parametrize("team, alone", [
    # fewer passed: never "No: the team passed fewer tasks"
    ([_night("a", True, 0.29), _night("b", False, 0.29)], ALONE),
    # more Claude: never "No: … not less Claude list-price"
    ([_night("a", True, 0.40), _night("b", True, 0.40)], ALONE),
    # an unknown list price: never "Not known: …"
    ([_unknown_price_night()], ALONE[:1]),
    # alone used nothing to compare with: never "Not known: …"
    ([_night("a", True, 0.29)], [_alone("a", True, 0.0)]),
], ids=["fewer", "dearer", "unknown-price", "alone-free"])
def test_no_verdict_of_any_kind_when_no_member_worked(team, alone):
    words = TRP.answer((TEAM_CFG, team), (ALONE_CFG, alone))
    assert words.startswith(TRP.NO_MEMBER_WORK), words


def test_a_planner_with_no_list_price_gets_no_verdict_either():
    cfg = {"runner": "jarvis-team", "provider": "custom", "model": "glm-5.3-flash"}
    alone_cfg = {"runner": "jarvis", "provider": "custom", "model": "glm-5.3-flash"}
    team = [{"task_id": "a", "passed": True, "parts": {PLANNER: {**_planner(0.0), "vendor": "local"},
                                                         "codex": _idle_member("codex")}}]
    alone = [{"task_id": "a", "passed": True, "parts": {PLANNER: {**_planner(0.0), "vendor": "local"}}}]
    words = TRP.answer((cfg, team), (alone_cfg, alone))
    assert words.startswith(TRP.NO_MEMBER_WORK), words


def test_a_team_whose_members_worked_keeps_its_verdict():
    team = [_night("a", True, 0.20, worked=True), _night("b", True, 0.20, worked=True)]
    words = TRP.answer((TEAM_CFG, team), (ALONE_CFG, ALONE))
    assert words.startswith("Yes on these runs: as many tasks, with less Claude list-price."), words
    assert "Team members worked in" not in words  # every run: nothing to add


def test_members_in_some_runs_says_how_many():
    team = [_night("a", True, 0.20, worked=True), _night("b", True, 0.20)]
    words = TRP.answer((TEAM_CFG, team), (ALONE_CFG, ALONE))
    assert words.startswith("Yes on these runs"), words
    assert "Team members worked in 1 of the 2 runs; in the rest the planner worked alone." in words


def test_a_piece_a_member_failed_or_was_refused_still_went_to_the_team():
    # No model call reached the member (its hold refused it), but the planner
    # DID hand the piece to the team.
    refused = _night("a", True, 0.20, helpers=[{"member": "glm", "role": "code", "state": "failed"}])
    assert TRP.handed_to_a_member(refused) is True
    # A helper on the planner's own model is not the team.
    own = _night("a", True, 0.20, helpers=[{"member": "", "agent": "general-purpose", "state": "done"}])
    assert TRP.handed_to_a_member(own) is False
    # The planner's own calls never count as the team.
    assert TRP.handed_to_a_member({"parts": {PLANNER: _planner(0.2)}}) is False
    assert TRP.handed_to_a_member(_night("a", True, 0.2, worked=True)) is True
