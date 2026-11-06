"""v1.441.0 review of lane g3 (Codex Plan, OpenCode agents and helpers).

Each pin was red on the lane's first cut.
"""

from __future__ import annotations

import pytest

from iron_jarvis.hosted import adopt as AD
from iron_jarvis.hosted import codex as X
from iron_jarvis.hosted.driver import Ask


def _driver(mode="plan"):
    got: list = []

    async def write(obj):
        got.append({"wrote": obj})
        if obj.get("method") == "turn/start":
            d._replies.settle(obj["id"], {"turn": {"id": "tu2"}})

    d = X.CodexDriver(write=write, emit=lambda e: got.append(e), ask=lambda a: got.append(a),
                      on_session_id=lambda s: None, mode=mode)
    d.session_id = "th"
    d.turn_id = "tu"
    d._thread_model = "m1"
    return d, got


def _waiting_plan_card():
    d, got = _driver()
    d.turn_open = True
    d._turn_mode = "plan"
    d._plan_text = "1. do it"
    d._turn_completed({"status": "completed"})
    ask = next(a for a in got if isinstance(a, Ask))
    return d, ask


def _restored(d, ask):
    """What a restarted Iron Jarvis holds after the code host kept the session:
    the driver's state put back on a NEW driver, the card rebuilt from its
    kept form (``hosted/adopt``)."""
    d2, got2 = _driver()
    d2.restore(d.state())
    back = AD._ask_from(AD._ask_state(ask))
    assert back is not None and back.id == ask.id
    return d2, got2, back


async def test_a_plan_card_answered_after_a_restart_still_starts_the_plan():
    """The code host keeps the session and the card (a NEW Ask object):
    the answer must still reach the driver, or the reply hangs for ever."""
    d, ask = _waiting_plan_card()
    d2, got2, back = _restored(d, ask)
    assert d2.prepare_answer(back, "allow", "", None) is None
    await d2.answer_here(back, "allow", "")
    starts = [g["wrote"] for g in got2 if isinstance(g, dict) and g.get("wrote", {}).get("method") == "turn/start"]
    assert starts and starts[0]["params"]["input"][0]["text"] == X.IMPLEMENT_TEXT


async def test_a_plan_card_denied_after_a_restart_ends_the_reply():
    d, ask = _waiting_plan_card()
    d2, got2, back = _restored(d, ask)
    await d2.answer_here(back, "deny", "")
    assert [e["stop"] for e in got2 if isinstance(e, dict) and e.get("type") == "done"] == ["completed"]
    assert d2.turn_open is False


async def test_stop_after_a_restart_while_the_plan_card_waits_ends_the_reply():
    d, ask = _waiting_plan_card()
    d2, got2, _back = _restored(d, ask)
    await d2.interrupt()
    assert [e["stop"] for e in got2 if isinstance(e, dict) and e.get("type") == "done"] == ["interrupted"]
    assert not any(isinstance(g, dict) and g.get("wrote", {}).get("method") == "turn/interrupt" for g in got2)


async def test_an_old_plan_card_never_answers_a_newer_one():
    d, ask = _waiting_plan_card()
    stale = Ask("other", "approval", X.PLAN_TOOL, None, {"plan": True, "leaves_plan_to": "ask"}, {}, "", False)
    await d.answer_here(stale, "allow", "")
    assert d.turn_open is True


async def test_plan_keeps_the_threads_own_effort_when_the_session_names_none():
    """MEASURED (codex 0.157.0, a scratch CODEX_HOME whose config.toml said
    ``model_reasoning_effort = "high"``): ``thread/start`` answered
    ``reasoningEffort: "high"``; a Plan turn with ``reasoning_effort: null``
    made the thread's effort null, and leaving Plan with null kept it null —
    entering Plan once dropped the user's own effort for the conversation."""
    d, got = _driver(mode="plan")
    d._thread_effort = "high"
    await d.send_user("plan it", [])
    d.mode = "ask"
    await d.send_user("now do it", [])
    starts = [g["wrote"]["params"] for g in got if isinstance(g, dict) and g.get("wrote", {}).get("method") == "turn/start"]
    assert starts[0]["collaborationMode"]["settings"]["reasoning_effort"] == "high"
    assert starts[1]["collaborationMode"] == {"mode": "default", "settings": {
        "model": "m1", "reasoning_effort": "high", "developer_instructions": None}}
    assert "effort" not in starts[0] and "effort" not in starts[1], "the turn's own effort key is as before"


async def test_a_chosen_effort_still_wins_over_the_threads():
    d, got = _driver(mode="plan")
    d._thread_effort = "high"
    d.effort = "low"
    await d.send_user("plan it", [])
    start = next(g["wrote"]["params"] for g in got if isinstance(g, dict) and g.get("wrote", {}).get("method") == "turn/start")
    assert start["collaborationMode"]["settings"]["reasoning_effort"] == "low"


# -- OpenCode ----------------------------------------------------------------
def test_an_opencode_agent_mode_can_be_kept_as_a_folder_default():
    """The start box offers to keep the chosen mode as the folder's default;
    an agent mode was refused "That mode is not one Iron Jarvis knows"."""
    from iron_jarvis.harness import folder_defaults as F

    got = F.clean_tool({"harness": "opencode", "mode": "agent:docs"})
    assert got == {"harness": "opencode", "mode": "agent:docs"}
    for bad in ({"harness": "codex", "mode": "agent:docs"}, {"harness": "opencode", "mode": "agent:build"},
                {"harness": "opencode", "mode": "agent:"}, {"harness": "opencode", "mode": "agent:a b"}):
        with pytest.raises(F.DefaultsRefused):
            F.clean_tool(bad)


def test_the_no_helpers_line_says_why_in_every_mode_that_refuses_them():
    from iron_jarvis.hosted import opencode as O

    for mode in ("ask", "edits", "plan"):
        assert "questions could not reach you" in O.MODE_ABOUT[mode]
    assert "questions could not reach you" in O.AGENT_ABOUT
    assert "helper" not in O.MODE_ABOUT["full"], "Full access still starts helpers"


def test_a_users_own_task_rule_is_kept_in_every_mode():
    """The v1.420.0 rule: never change a rule of the user's own — a ``task``
    rule of theirs (exact or by wildcard) is never overridden by the deny."""
    from iron_jarvis.hosted import opencode as O

    for mode in ("ask", "edits", "plan", "agent:docs"):
        for rules in ({"task": "allow"}, {"ta*": "allow"}, {"task": {"general": "allow"}}):
            perm, kept = O.permission_env(mode, rules)
            assert "task" not in perm, (mode, rules, perm)
        perm, _ = O.permission_env(mode, {})
        assert perm["task"] == "deny" and list(perm)[-1] in ("task", "edit")


def test_no_opencode_installed_starts_no_read_and_the_list_still_answers(tmp_path, monkeypatch):
    """The reader is OFF in the suite, which hid what happens with it ON and
    OpenCode missing: nothing may start, and listing the tools never fails."""
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.hosted import opencode_catalog as OCC
    from iron_jarvis.hosted import service as S

    started: list = []
    monkeypatch.setattr(OCC, "AUTO_PROBE", True)
    monkeypatch.setattr(OCC, "_refresh", lambda *a, **k: started.append(a))
    monkeypatch.setattr(S, "binary_for", lambda harness: None)
    app = create_app(str(tmp_path / "home"))
    rows = S.service_for(app.state.d.platform).harnesses()
    assert any(r["id"] == "opencode" for r in rows) and not started
