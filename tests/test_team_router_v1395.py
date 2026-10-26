"""v1.395.0 (the long-jobs plan's Phase 6, decisions 7 and 8): THE TEAM ROUTER.

``agents/model_team.route_role`` is the ONE function that picks the member for
a role. These pins drive it against a REAL platform with a cached Iron-Proxy
snapshot and a fleet sampler's last pass (``tests/_fakes/team_world_v1395``):

* the filters, in order: Local only, paused / out of allowance, a coordinator,
  the provider there (CACHED answers only — never a probe), the usage
  thresholds (no new work at 80%, nothing at 95%, unknown after known-low),
  pay per use only under the monthly cap (default $0 = never);
* the order: local members with a good or not-yet-known record first, then
  subscription members (lowest usage first), then pay per use, then a busy or
  poorly recorded local member as the last resort;
* the record (``agents/team_record``): good = 3+ pieces at 70%+; two failures
  in a row stop a local member getting the role first until it passes again;
* a piece that failed twice on one member moves ONE step up, never across
  Local only;
* one plain sentence of why, and plain words naming the earliest reset when
  nothing can take it.
"""

from __future__ import annotations

import pytest

from iron_jarvis.agents import model_team as T
from iron_jarvis.agents import team_record as TR
from tests._fakes.team_world_v1395 import LOCAL, make_world, member, note


@pytest.fixture
def w(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch)


def team(w, *, local_roles=("read", "code", "review")):
    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=local_roles)
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code", "review"))
    member(w.p, "codex", "codex-cli", "subscription", roles=("code", "test"))


# --------------------------------------------------------------------------- #
# the role words
# --------------------------------------------------------------------------- #


def test_role_words_are_read_in_every_spelling_and_an_unknown_one_is_a_sentence():
    for raw, want in (("read", "read"), ("Reading", "read"), ("role:code", "code"),
                      ("Writing code", "code"), ("reviewer", "review"), ("testing", "test"),
                      ("Running and fixing tests", "test")):
        assert T.role_id(raw) == want, raw
    assert T.role_id("design") == "" and T.role_id("") == ""
    assert T.role_named("role:Review") == "Review" and T.role_named("custom:glm") is None
    words = T.unknown_role_refusal("design")
    assert words.startswith("'design' is not a team role") and "read, code, review or test" in words


def test_no_member_holds_the_role_is_said_in_words(w):
    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=("read",))
    pick = T.route_role(w.p, "test")
    assert pick.record is None
    assert pick.refusal.startswith("No one on your model team holds testing yet.")


# --------------------------------------------------------------------------- #
# local first, by its record
# --------------------------------------------------------------------------- #


def test_a_local_member_with_no_record_is_tried_first_and_says_so(w):
    team(w)
    pick = T.route_role(w.p, "review")
    assert pick.record.name == "glm"
    assert pick.reason == "local: no record yet for reviewing"


def test_a_local_member_with_a_good_record_is_first_and_says_so(w):
    team(w)
    note(w.p, "glm", "read", [True, True, True, False])
    pick = T.route_role(w.p, "read")
    assert pick.record.name == "glm" and pick.reason == "local: good record for reading"


def test_a_local_member_with_too_few_pieces_is_still_tried_first(w):
    team(w)
    note(w.p, "glm", "code", [True, True])
    pick = T.route_role(w.p, "code")
    assert pick.record.name == "glm"
    assert pick.reason == "local: only 2 pieces of coding so far"


def test_a_local_member_whose_record_is_not_good_comes_after_the_cloud(w):
    team(w)
    note(w.p, "glm", "code", [False, True, False, True, False, True])  # 3 of 6
    pick = T.route_role(w.p, "code")
    assert pick.record.name == "codex" or pick.record.name == "claude-code"
    assert "the local member's record for coding is not good (3 of 6 passed)" in pick.reason


def test_two_failures_in_a_row_stop_a_local_member_getting_the_role_first_until_it_passes(w):
    team(w)
    # 8 of 10 passed — a GOOD rate: only the two failures in a row demote it.
    note(w.p, "glm", "code", [True] * 8 + [False, False])
    assert TR.role_record(w.p.engine, "glm", "code").rate >= TR.GOOD_RATE
    pick = T.route_role(w.p, "code")
    assert pick.record.name != "glm"
    assert "the local member failed coding 2 times in a row" in pick.reason
    # It passes again (named directly, or as the last resort): first again.
    note(w.p, "glm", "code", [True], scope="s-new")
    pick = T.route_role(w.p, "code")
    assert pick.record.name == "glm" and pick.reason == "local: good record for coding"


def test_the_record_reads_the_finish_check_and_the_reviewer_not_just_finished(w):
    """A piece that FINISHED but failed its check, or that a reviewer said needs
    changes, is a failure — "good" is not "it finished"."""
    TR.note_run(w.p.engine, member="m", role="code", session_id="a", scope="s", finished=True)
    TR.note_run(w.p.engine, member="m", role="code", session_id="b", scope="s", finished=True)
    TR.note_run(w.p.engine, member="m", role="code", session_id="c", scope="s", finished=True)
    assert TR.role_record(w.p.engine, "m", "code").status == TR.GOOD
    TR.credit_check(w.p.engine, ["b"], TR.CHECK_FAILED)
    review = TR.note_run(w.p.engine, member="r", role="review", session_id="d", scope="s", finished=True)
    assert TR.credit_review(w.p.engine, scope="s", review_row=review, verdict=TR.REVIEW_BAD) == 3
    rec = TR.role_record(w.p.engine, "m", "code")
    assert (rec.runs, rec.passed, rec.status) == (3, 0, TR.NOT_GOOD)


# --------------------------------------------------------------------------- #
# a busy or missing local box
# --------------------------------------------------------------------------- #


def test_a_busy_local_box_sends_the_piece_to_a_subscription_member(w):
    team(w)
    w.sampler.waiting["ollama"] = 3
    pick = T.route_role(w.p, "code")
    assert pick.record.name == "claude-code"
    assert pick.reason == "Claude Code (Work Max): the local box is busy"


def test_a_local_box_that_is_not_answering_is_skipped(w, tmp_path, monkeypatch):
    w2 = make_world(tmp_path / "b", monkeypatch, available={"claude-cli", "codex-cli"})
    team(w2)
    pick = T.route_role(w2.p, "code")
    assert pick.record.name == "claude-code"
    assert pick.reason.startswith("Claude Code (Work Max): the local box is not answering")


def test_a_busy_box_is_the_last_resort_before_waiting(w):
    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=("read",))
    w.sampler.waiting["ollama"] = 2
    pick = T.route_role(w.p, "read")
    assert pick.record.name == "glm"
    assert pick.reason == "local: nothing else can take reading now; its box is busy"


# --------------------------------------------------------------------------- #
# the usage thresholds
# --------------------------------------------------------------------------- #


def test_no_new_work_on_an_account_at_80_percent_it_goes_to_the_next_one(w):
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 83)
    pick = T.route_role(w.p, "code")
    # The other Claude account (no numbers yet) takes it, pinned.
    assert pick.record.name == "claude-code"
    assert pick.pin_set and pick.pin.profile_id == "c-spare"
    assert pick.reason == ("Claude Code (Spare): no local member holds coding; "
                           "skipped Claude Code (Work Max): 83% of its 5-hour window")


def test_known_low_usage_is_tried_before_an_account_with_no_numbers(w):
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    pick = T.route_role(w.p, "code")
    assert pick.pin.profile_id == "c-work"  # 12% known beats "not known"


def test_subscription_members_go_lowest_usage_first(w):
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    member(w.p, "codex", "codex-cli", "subscription", roles=("code",))
    assert T.route_role(w.p, "code").record.name == "claude-code"  # 12% < 40%
    w.svc.set_percent("c-work", 70)
    w.svc.set_percent("c-spare", 75)
    assert T.route_role(w.p, "code").record.name == "codex"  # 40% < 70%


def test_nothing_starts_on_an_account_at_95_percent(w):
    member(w.p, "codex", "codex-cli", "subscription", roles=("test",))
    w.svc.set_percent("x-plus", 96, resets_in=5400)
    pick = T.route_role(w.p, "test")
    assert pick.record is None and pick.waited
    assert "skipped Codex (Codex Plus): 96% of its 5-hour window" in pick.refusal


def test_when_every_cloud_account_is_near_its_limit_the_piece_waits_and_names_the_reset(w):
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    w.svc.set_percent("c-work", 85, resets_in=7200)
    w.svc.park("c-spare", until_in=1800)
    pick = T.route_role(w.p, "code")
    assert pick.record is None and pick.waited
    assert pick.refusal.startswith("No team member can take coding now: skipped Claude Code (Work Max): "
                                   "85% of its 5-hour window; skipped Claude Code (Spare): at its limit until ")
    assert " The earliest reset is " in pick.refusal
    assert "Nothing was started on an account near its limit" in pick.refusal
    assert pick.reason.startswith("waited: every cloud account that can take coding is near its limit; "
                                  "the earliest reset is ")


def test_when_cloud_is_near_its_limit_the_work_goes_local(w):
    team(w)
    note(w.p, "glm", "code", [False, False])  # would not be first
    w.svc.set_percent("c-work", 90)
    w.svc.park("c-spare")
    w.svc.set_percent("x-plus", 81)
    pick = T.route_role(w.p, "code")
    assert pick.record.name == "glm"
    assert pick.reason.startswith("local: every cloud account that can take coding is near its limit; its record is")


def test_the_earliest_reset_is_read_as_time_never_as_text():
    # 10:00 at +05:00 is 05:00Z — earlier than 06:00Z, though not as text.
    assert T._earliest(["2026-10-26T06:00:00Z", "2026-10-26T10:00:00+05:00", "junk"]) == "2026-10-26T10:00:00+05:00"
    assert T._earliest([]) == "" and T._earliest(["junk"]) == ""


def test_only_accounts_passed_over_for_usage_are_named_in_a_cloud_pick(w):
    """A skipped member whose NAME happens to hold "limit" or "%" is not an
    account at its limit (the reason is built from flags, never from words)."""
    member(w.p, "limit-50%", LOCAL[0], LOCAL[1], roles=("code",))
    w.p.agents_registry.set_paused("limit-50%", "off today")
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    pick = T.route_role(w.p, "code")
    assert pick.record.name == "claude-code"
    assert pick.reason == "Claude Code (Work Max): the local member is paused"


def test_a_session_on_a_busy_account_never_moves_its_helper_to_another_account(w):
    """A member with no account of its own, called from work pinned to an
    account of its vendor, runs on THAT account (the v1.372.0 rule) — at 80%
    it is skipped, never moved."""
    from iron_jarvis.iron_proxy.pin import AccountPin

    member(w.p, "claude-code", "claude-cli", "subscription", roles=("review",))
    w.svc.set_percent("c-work", 88)
    pick = T.route_role(w.p, "review", caller_pin=AccountPin(profile_id="c-work", ij_provider="claude-cli"))
    assert pick.record is None
    assert "skipped Claude Code (Work Max): 88% of its 5-hour window" in pick.refusal


# --------------------------------------------------------------------------- #
# pay per use, under the cap
# --------------------------------------------------------------------------- #


def test_pay_per_use_is_off_at_the_default_cap(w):
    member(w.p, "gpt", "openai", "gpt-5", roles=("code",))
    assert w.p.config.team_paid_cap_usd == 0.0
    pick = T.route_role(w.p, "code")
    assert pick.record is None
    assert "skipped gpt (pay per use is off: your monthly cap is $0)" in pick.refusal


def test_pay_per_use_runs_under_the_cap_and_stops_at_it(w):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import AgentType, Session

    member(w.p, "gpt", "openai", "gpt-5", roles=("code",))
    w.p.config.team_paid_cap_usd = 5.0
    pick = T.route_role(w.p, "code")
    assert pick.record.name == "gpt"
    assert pick.reason == "gpt-5: no local member holds coding; pay per use: $0.00 of your $5.00 monthly cap used"
    with session_scope(w.p.engine) as db:
        db.add(Session(task="t", agent_type=AgentType.BUILDER, provider="openai", model="gpt-5",
                       agent_name="custom:gpt", cost_usd=5.25))
        # A subscription's list-price equivalent never counts toward the cap.
        db.add(Session(task="t", agent_type=AgentType.BUILDER, provider="claude-cli", model="claude-sonnet-4-5",
                       agent_name="custom:gpt", cost_usd=50.0))
        db.commit()
    assert T.paid_spend_this_month(w.p) == pytest.approx(5.25)
    pick = T.route_role(w.p, "code")
    assert pick.record is None
    assert "skipped gpt (this month's pay-per-use spend, $5.25, reached your $5.00 cap)" in pick.refusal


def test_the_cap_is_a_settings_row_that_asks_every_time():
    from iron_jarvis.settings import schema as S

    row = next(d for d in S.SETTINGS if d.key == "team_paid_cap_usd")
    assert (row.group, row.type, row.tier, row.section) == ("models", "number", "ask-floor", "Model team")


# --------------------------------------------------------------------------- #
# the other filters
# --------------------------------------------------------------------------- #


def test_local_only_never_considers_a_cloud_member(w):
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    pick = T.route_role(w.p, "code", local_only=True)
    assert pick.record is None
    assert "this work is Local only" in pick.refusal and "Claude" not in pick.refusal
    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=("code",))
    w.sampler.waiting["ollama"] = 4
    pick = T.route_role(w.p, "code", local_only=True)
    assert pick.record.name == "glm"  # busy, but the only one on this network


def test_a_project_team_bounds_the_candidates(w):
    team(w)
    pick = T.route_role(w.p, "code", team=["custom:codex"])
    assert pick.record.name == "codex"
    pick = T.route_role(w.p, "read", team=["custom:codex", "builder"])
    assert pick.record is None
    assert pick.refusal.startswith("No one on this project's team holds reading")


def test_a_paused_member_and_a_coordinator_are_skipped_with_why(w):
    member(w.p, "glm", LOCAL[0], LOCAL[1], roles=("code",))
    w.p.agents_registry.set_paused("glm", "on a break")
    w.p.agents_registry.register("boss", "You lead.", ["delegate"], provider="codex-cli",
                                 model="subscription", roles=["code"])
    pick = T.route_role(w.p, "code")
    assert pick.record is None
    assert "skipped glm (it is paused)" in pick.refusal
    assert "skipped boss (it hands work out itself)" in pick.refusal


def test_routing_never_starts_a_sign_in_probe(w, monkeypatch):
    """CACHED answers only: a stale or missing sign-in verdict is never
    refreshed from routing (``cli_auth.never_probe``)."""
    from iron_jarvis.providers import cli_auth

    started: list[str] = []
    monkeypatch.setattr(cli_auth.DEFAULT_PROBE, "_start", lambda b: started.append(b))
    monkeypatch.setattr(w.p.providers, "available", lambda name: cli_auth.DEFAULT_PROBE.verdict("claude") is not False)
    member(w.p, "claude-code", "claude-cli", "subscription", roles=("code",))
    T.route_role(w.p, "code")
    assert started == []
    cli_auth.DEFAULT_PROBE.verdict("claude")  # outside routing the probe still refreshes
    assert started == ["claude"]


def test_the_completion_only_local_oracle_is_never_read(w, monkeypatch):
    """Decision 7: the record replaces the completion-only oracle for these
    roles; chat's Auto keeps its oracle (it is not touched here)."""
    import inspect

    def boom(*_a, **_k):
        raise AssertionError("the team router read the local oracle")

    monkeypatch.setattr(w.p.router, "_local_oracle", boom)
    team(w)
    assert T.route_role(w.p, "code").record.name == "glm"
    src = inspect.getsource(T)
    assert "local_oracle(" not in src and "router._local_oracle" not in src
    assert "_local_oracle" not in inspect.getsource(TR)
    # Chat's Auto still asks the oracle (the router's own code, untouched).
    from iron_jarvis.providers import router as R

    assert "self._local_oracle(task_class)" in inspect.getsource(R.ModelRouter)


# --------------------------------------------------------------------------- #
# a piece that fails twice moves one step up
# --------------------------------------------------------------------------- #


def test_a_piece_that_failed_twice_on_a_member_moves_one_step_up(w):
    team(w)
    piece = T.piece_key("", "Write util.py")
    note(w.p, "glm", "code", [False, False], scope="build:s1", piece=piece)
    # glm's record stays GOOD (5 of 7) and its newest piece passed, so only
    # the PIECE rule moves it.
    note(w.p, "glm", "code", [True] * 5, scope="other")
    pick = T.route_role(w.p, "code", scope="build:s1", piece=piece)
    assert pick.record.name == "claude-code"
    assert pick.reason == ("Claude Code (Work Max): this piece failed its check twice on glm, "
                           "so it moved up one")
    # ...and only ONE step: if it fails twice there too, it stays.
    note(w.p, "claude-code", "code", [False, False], scope="build:s1", piece=piece)
    pick = T.route_role(w.p, "code", scope="build:s1", piece=piece)
    assert pick.record.name == "claude-code"
    # Another piece of the same work is untouched.
    other = T.route_role(w.p, "code", scope="build:s1", piece=T.piece_key("", "Write cli.py"))
    assert other.record.name == "glm"


def test_a_piece_never_moves_across_local_only(w):
    team(w)
    piece = T.piece_key("util", "")
    note(w.p, "glm", "code", [False, False], scope="build:s2", piece=piece)
    note(w.p, "glm", "code", [True] * 5, scope="other")
    pick = T.route_role(w.p, "code", local_only=True, scope="build:s2", piece=piece)
    assert pick.record.name == "glm"
    assert pick.reason == ("local: this piece failed its check twice here, but this work is Local only "
                           "and no other member on this PC or your network holds coding")
