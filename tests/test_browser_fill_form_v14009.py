"""One card for a whole form: ``browser_fill_form`` (v1.400.9).

The user's complaint: a twelve-field form was twelve approval cards, or the
Auto-allow switch for every page. The batch tool is the middle: one call lists
every step, the lane cards it ONCE (ask tier, deny floor — the ordinary per-call
card), and every step then runs through the REAL click/type/press_key tool, so
nothing in the list is judged more leniently than the same action made alone.

What is pinned here, through the REAL tools and the REAL ``BrowserRuntime`` over
the scripted peer (the Ship-3 rig in ``test_browser_actions_v1237``):

* it is declared like the four page-action tools (risk class, deny floor, ask
  tier, default "ask", a tab grant covers it);
* every step reaches the page through the ONE risk door — one door call per
  step, spied, not read;
* a payment / password / destructive step still stops for ITS OWN card, the page
  is untouched past it, and the answer says which steps ran and which did not;
* it stops when a step leaves the page, keeps every step in the tab it resolved
  first, honours a step tool the user set to Deny, and refuses a malformed list
  before anything is sent;
* typed text is redacted from the arguments (only its length survives), and is
  absent from the answer;
* every step runs EXACTLY as the model sent it: an element id is never
  rewritten into a role and name (the review found that a rewritten step landed
  on a NEW control sharing the old one's name after "Continue" swapped the
  page), so a stale id stops the list honestly, as the same call alone would;
* a step tool set to Deny for the run — the global table or an agent's own
  ``deny_tools`` — refuses the whole list through the REAL registry;
* the card's words: Python's ``describe_form_step`` and the dashboard's
  ``formStepWords`` read the SAME shared fixture.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from iron_jarvis.browser import panel as panel_mod
from iron_jarvis.browser import protocol as P
from iron_jarvis.browser import tools as tools_mod
from iron_jarvis.browser.grants import is_acting_browser_tool
from iron_jarvis.browser.risk import BASE_RISK
from iron_jarvis.browser.tools import (
    FORM_STEP_TOOLS,
    BrowserClickTool,
    MAX_FORM_STEPS,
    BrowserFillFormTool,
    browser_tools,
    describe_form_step,
    form_steps_problem,
)
from iron_jarvis.computeruse.policy import ComputerUsePolicy
from iron_jarvis.core.config import default_permissions
from iron_jarvis.tools.autoselect import ASK_TIER_TOOLS
from iron_jarvis.tools.base import Reversibility, RiskClass
from iron_jarvis.tools.permissions import DENY_FLOOR_TOOLS

from ._fakes.browser_peer import BrowserPeer, FakeElement, FakeTab, ScriptedBrowser
from .test_browser_actions_v1237 import (
    SECRET,
    ActRuntime,
    DoorSpy,
    FakeApprovals,
    _ctx,
    _form_page,
    _peer,
    _ready,
    _run,
    _sent,
)

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "dashboard"
    / "__tests__"
    / "fixtures"
    / "form-steps-v1400-9.json"
)


@pytest.fixture()
def door(monkeypatch):
    """Spy on the single risk door, as the TOOLS module sees it (calls through)."""
    spy = DoorSpy(tools_mod.browser_risk_decision)
    monkeypatch.setattr(tools_mod, "browser_risk_decision", spy)
    return spy


def _runtime(page: ScriptedBrowser | None = None, **kw: Any) -> ActRuntime:
    kw.setdefault("policy", ComputerUsePolicy())
    kw.setdefault("approvals", FakeApprovals())
    return ActRuntime(_peer(page or _form_page()), **kw)


def _acting(runtime: ActRuntime) -> list[tuple[str, dict[str, Any]]]:
    """Every command that CHANGED the page, in order."""
    acting = {P.METHOD_CLICK, P.METHOD_TYPE_TEXT, P.METHOD_PRESS_KEY, P.METHOD_NAVIGATE}
    return [(m, p) for m, p in runtime.calls if m in acting]


# --------------------------------------------------------------------------- #
# 1. Declared like the page-action tools
# --------------------------------------------------------------------------- #


def test_it_is_registered_and_declared_like_the_page_action_tools():
    tools = {t.name: t for t in browser_tools(_runtime())}
    tool = tools["browser_fill_form"]
    assert len(tools) == 15
    assert tool.risk_class is RiskClass.PAGE_ACTION
    assert tool.reversibility is Reversibility.IRREVERSIBLE
    assert tool.min_access == "interactive"
    assert tool.returns_untrusted_content is False
    assert BASE_RISK["browser_fill_form"] is RiskClass.PAGE_ACTION
    assert "browser_fill_form" in DENY_FLOOR_TOOLS
    assert "browser_fill_form" in ASK_TIER_TOOLS
    assert default_permissions()["browser_fill_form"] == "ask"
    # "Allow for this tab" covers it like every other acting tool.
    assert is_acting_browser_tool("browser_fill_form")
    assert tool.input_schema["required"] == ["steps"]
    # The three step tools it hands each step to are exactly the three that act
    # on an element; navigate is not a form step.
    assert FORM_STEP_TOOLS == {
        "type": "browser_type",
        "click": "browser_click",
        "press_key": "browser_press_key",
    }


# --------------------------------------------------------------------------- #
# 2. Every step through the real tool and the one door
# --------------------------------------------------------------------------- #


def test_every_step_runs_through_the_real_tool_and_the_one_risk_door(tmp_path, door):
    runtime = _runtime()
    _ready(runtime, tmp_path=tmp_path)
    before = len(door.calls)
    result = _run(
        BrowserFillFormTool(runtime),
        _ctx(tmp_path),
        {
            "steps": [
                {"action": "type", "target": {"role": "textbox", "name": "Search"}, "text": "tax forms"},
                {"action": "press_key", "key": "Tab", "target": {"role": "textbox", "name": "Search"}},
                {"action": "click", "target": {"role": "button", "name": "Expand details"}},
            ]
        },
    )
    assert result.ok, result.error
    assert result.data["done"] == 3 and result.data["total"] == 3
    assert result.data["stopped"] == ""
    assert [m for m, _ in _acting(runtime)] == [
        P.METHOD_TYPE_TEXT,
        P.METHOD_PRESS_KEY,
        P.METHOD_CLICK,
    ]
    assert _sent(runtime, P.METHOD_TYPE_TEXT)[0]["text"] == "tax forms"
    # ONE door call per step: the batch adds no path around the door and
    # skips none of it.
    assert len(door.calls) - before == 3
    assert result.output.startswith("Filled the form in tab 42: all 3 steps done.")
    assert [row["status"] for row in result.data["steps"]] == ["done", "done", "done"]


def _wizard_page() -> ScriptedBrowser:
    """An app-style wizard: "Continue" swaps in the next part, which ALSO has a
    textbox called "Amount" — the reviewer's case. In the fake page both live in
    one registry; what matters is that the click moves the page."""
    return ScriptedBrowser(
        [
            FakeTab(
                id=42,
                title="Transfer",
                url="https://bank.example/transfer",
                active=True,
                elements=[
                    FakeElement(id="e1", role="textbox", name="Amount", field_type="text"),
                    FakeElement(id="e2", role="button", name="Continue"),
                ],
            )
        ]
    )


def test_an_element_id_is_sent_exactly_as_the_model_sent_it(tmp_path):
    runtime = _runtime()
    _ready(runtime, tmp_path=tmp_path)
    result = _run(
        BrowserFillFormTool(runtime),
        _ctx(tmp_path),
        {"steps": [{"action": "type", "target": {"element_id": "e7"}, "text": "w-2"}]},
    )
    assert result.ok, result.error
    assert _sent(runtime, P.METHOD_TYPE_TEXT)[0]["target"] == {"element_id": "e7"}


def test_a_stale_id_after_the_page_moved_stops_the_list_instead_of_landing_elsewhere(tmp_path):
    """[click Continue, type into e1] where the click moves the page: the same
    two calls made alone refuse the type as stale, so the list must too — it
    must not type "100" into whatever "Amount" box the new page shows."""
    runtime = _runtime(_wizard_page())
    _ready(runtime, tmp_path=tmp_path)
    result = _run(
        BrowserFillFormTool(runtime),
        _ctx(tmp_path),
        {
            "steps": [
                {"action": "click", "target": {"element_id": "e2"}},
                {"action": "type", "target": {"element_id": "e1"}, "text": "100"},
            ]
        },
    )
    assert _sent(runtime, P.METHOD_CLICK)[0]["target"] == {"element_id": "e2"}
    assert _sent(runtime, P.METHOD_TYPE_TEXT) == [], "typed into a field from a page that moved"
    assert not result.ok
    assert result.data["done"] == 1
    assert "Stopped after 1 of 2 steps" in result.output
    assert "browser_read_page" in result.output

    # Control: the SAME two calls made one at a time refuse the type the same way.
    alone = _runtime(_wizard_page())
    _ready(alone, tmp_path=tmp_path)
    assert _run(BrowserClickTool(alone), _ctx(tmp_path), {"target": {"element_id": "e2"}}).ok
    second = _run(
        tools_mod.BrowserTypeTool(alone), _ctx(tmp_path),
        {"target": {"element_id": "e1"}, "text": "100"},
    )
    assert not second.ok and _sent(alone, P.METHOD_TYPE_TEXT) == []


def test_a_role_and_name_list_keeps_going_after_the_page_moves(tmp_path):
    """What the description tells the model to send: a role and name is found
    again at every step, exactly as the same call alone would find it."""
    runtime = _runtime(_wizard_page())
    _ready(runtime, tmp_path=tmp_path)
    result = _run(
        BrowserFillFormTool(runtime),
        _ctx(tmp_path),
        {
            "steps": [
                {"action": "click", "target": {"role": "button", "name": "Continue"}},
                {"action": "type", "target": {"role": "textbox", "name": "Amount"}, "text": "100"},
            ]
        },
    )
    assert result.ok, result.error
    assert _sent(runtime, P.METHOD_TYPE_TEXT)[0]["target"] == {"role": "textbox", "name": "Amount"}


def test_an_id_with_no_snapshot_row_is_left_for_the_step_tool_to_refuse(tmp_path):
    runtime = _runtime()
    _ready(runtime, tmp_path=tmp_path)
    result = _run(
        BrowserFillFormTool(runtime),
        _ctx(tmp_path),
        {"steps": [{"action": "click", "target": {"element_id": "e99"}}]},
    )
    assert not result.ok
    assert _acting(runtime) == []
    assert result.data["steps"][0]["status"] == "refused"


# --------------------------------------------------------------------------- #
# 3. A step that needs its own card still gets one
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "dangerous",
    [
        {"action": "click", "target": {"role": "button", "name": "Delete account"}},
        {"action": "click", "target": {"role": "button", "name": "Submit payment"}},
        {"action": "type", "target": {"role": "textbox", "name": "Password"}, "text": SECRET},
    ],
    ids=["destructive", "payment", "password"],
)
def test_a_floor_step_stops_for_its_own_card_and_nothing_after_it_runs(tmp_path, dangerous):
    approvals = FakeApprovals()
    runtime = _runtime(approvals=approvals)
    _ready(runtime, tmp_path=tmp_path)
    result = _run(
        BrowserFillFormTool(runtime),
        _ctx(tmp_path),
        {
            "steps": [
                {"action": "type", "target": {"role": "textbox", "name": "Search"}, "text": "report"},
                dangerous,
                {"action": "click", "target": {"role": "button", "name": "Expand details"}},
            ]
        },
    )
    assert not result.ok
    acted = _acting(runtime)
    assert [m for m, _ in acted] == [P.METHOD_TYPE_TEXT], "a step past the card reached the page"
    assert len(approvals.rows) == 1, "the dangerous step did not ask for its own approval"
    assert result.data["done"] == 1
    assert result.data["steps"][1]["status"] == "waiting"
    assert "step 2 is waiting for the user's approval" in result.output
    assert "Not sent to the page: 3. click 'Expand details'" in result.output
    # The step's own remedy ("make the identical call again") would mean
    # re-sending the steps above, which already ran: the list says what to send.
    assert "identical call again" not in result.output
    assert "Pending approval id=" not in result.output
    assert "send this step and the ones after it again in one call" in result.output
    assert "send only the steps that were not done" in result.output
    assert SECRET not in result.output and SECRET not in json.dumps(result.data)


def test_with_the_sidebars_answer_the_dangerous_step_runs_once_and_the_rest_follow(tmp_path):
    """The resolver path (the sidebar answers a floor card in place, v1.276.0):
    the user allows the payment step, it runs, and the list carries on."""
    asked: list[Any] = []

    def resolver(req: Any, *args: Any, **kw: Any) -> bool:
        asked.append(req)
        return True

    approvals = FakeApprovals()
    runtime = _runtime(approvals=approvals, approval_resolver=resolver)
    _ready(runtime, tmp_path=tmp_path)
    result = _run(
        BrowserFillFormTool(runtime),
        _ctx(tmp_path),
        {
            "steps": [
                {"action": "click", "target": {"role": "button", "name": "Expand details"}},
                {"action": "click", "target": {"role": "button", "name": "Submit payment"}},
                {"action": "press_key", "key": "Tab", "target": {"role": "textbox", "name": "Search"}},
            ]
        },
    )
    assert result.ok, result.error
    assert len(asked) == 1, "only the payment step needed its own answer"
    assert [row.status for row in approvals.rows] == ["consumed"]


# --------------------------------------------------------------------------- #
# 4. Stops, the one tab, Deny, validation
# --------------------------------------------------------------------------- #


class _NavPeer(BrowserPeer):
    """Clicking "Sign in" leaves the page, like a real submit."""

    def _h_click(self, params: dict[str, Any]) -> dict[str, Any]:
        out = super()._h_click(params)
        if out["clicked"]["name"] == "Sign in":
            out["navigated"] = True
            out["url"] = "https://portal.example/home"
        return out


def test_a_step_tool_set_to_deny_for_the_run_refuses_the_list_through_the_real_registry(tmp_path):
    """The steps call the step tools' ``execute`` directly, so their own Deny
    would never be read: ``acts_through`` makes the REGISTRY refuse the batch —
    for the global table AND for one run's overrides (an agent's deny_tools)."""
    import asyncio

    from iron_jarvis.platform import build_platform
    from iron_jarvis.tools.base import ToolContext
    from iron_jarvis.tools.permissions import PermissionEngine, PermissionMode

    p = build_platform(str(tmp_path / "home"))
    tool = p.registry.get("browser_fill_form")
    assert tool is not None and set(tool.acts_through) == {
        "browser_type", "browser_click", "browser_press_key",
    }
    ctx = ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id="r", config=p.config,
        event_bus=p.event_bus, engine=p.engine,
    )
    args = {"steps": [{"action": "type", "target": {"role": "textbox", "name": "Email"}, "text": SECRET}]}
    allow_batch = PermissionEngine({**p.config.permissions, "browser_fill_form": "allow"})

    async def go(perms, overrides=None):
        return await p.registry.invoke(
            "browser_fill_form", dict(args), ctx, perms, agent_overrides=overrides,
        )

    # One run's override (a custom agent's deny_tools) refuses it.
    refused = asyncio.run(go(allow_batch, {"browser_type": "deny"}))
    assert not refused.ok
    assert refused.error.startswith("permission denied:")
    assert "browser_type" in refused.error
    # The global table refuses it too.
    table = PermissionEngine({**p.config.permissions, "browser_fill_form": "allow", "browser_click": "deny"})
    assert "browser_click" in asyncio.run(go(table)).error
    # Control: nothing denied -> past the gate (the browser is not connected
    # here, so the tool itself answers, in its own words).
    passed = asyncio.run(go(allow_batch))
    assert not passed.error.startswith("permission denied")
    assert allow_batch.mode_for("browser_type") is not PermissionMode.DENY


def test_it_stops_when_a_step_leaves_the_page(tmp_path):
    runtime = ActRuntime(
        _NavPeer(None, page=_form_page()), policy=ComputerUsePolicy(), approvals=FakeApprovals()
    )
    _ready(runtime, tmp_path=tmp_path)
    result = _run(
        BrowserFillFormTool(runtime),
        _ctx(tmp_path),
        {
            "steps": [
                {"action": "click", "target": {"element_id": "e3"}},
                {"action": "type", "target": {"role": "textbox", "name": "Search"}, "text": "x"},
            ]
        },
    )
    assert not result.ok
    assert result.data["navigated"] is True
    assert _sent(runtime, P.METHOD_TYPE_TEXT) == [], "typed into the page that replaced the form"
    assert "took the page to" in result.output
    assert "portal.example/home" in result.output
    assert "Not sent to the page: 2. type 1 character into 'Search'" in result.output


class _SwitchPeer(BrowserPeer):
    """The user switches to another tab while the form fills."""

    def _h_type_text(self, params: dict[str, Any]) -> dict[str, Any]:
        out = super()._h_type_text(params)
        self.page.activate(43)
        return out


def test_every_step_stays_in_the_tab_it_resolved_first(tmp_path):
    runtime = ActRuntime(
        _SwitchPeer(None, page=_form_page()), policy=ComputerUsePolicy(), approvals=FakeApprovals()
    )
    _ready(runtime, tmp_path=tmp_path)
    result = _run(
        BrowserFillFormTool(runtime),
        _ctx(tmp_path),
        {
            "steps": [
                {"action": "type", "target": {"role": "textbox", "name": "Search"}, "text": "a"},
                {"action": "click", "target": {"role": "button", "name": "Expand details"}},
            ]
        },
    )
    assert result.ok, result.error
    assert [p["tab_id"] for _, p in _acting(runtime)] == [42, 42]
    assert result.data["tab_id"] == 42


def test_a_step_tool_set_to_deny_refuses_the_whole_list_before_anything_runs(tmp_path):
    runtime = _runtime()
    _ready(runtime, tmp_path=tmp_path)
    ctx = _ctx(tmp_path)
    ctx.config = SimpleNamespace(permissions={"browser_click": "deny"})  # type: ignore[assignment]
    result = _run(
        BrowserFillFormTool(runtime),
        ctx,
        {
            "steps": [
                {"action": "type", "target": {"role": "textbox", "name": "Search"}, "text": "a"},
                {"action": "click", "target": {"role": "button", "name": "Expand details"}},
            ]
        },
    )
    assert not result.ok
    assert result.data["status"] == "denied"
    assert _acting(runtime) == []
    assert "step 2 uses browser_click" in result.output


@pytest.mark.parametrize(
    "steps, words",
    [
        ([], "1 to 20"),
        ([{"action": "click", "target": {"role": "button", "name": "x"}}] * (MAX_FORM_STEPS + 1), "at most 20"),
        ("type into email", "1 to 20"),
        ([{"action": "submit"}], "action"),
        ([{"action": "click"}], "target"),
        ([{"action": "type", "target": {"role": "textbox", "name": "x"}}], "text"),
        ([{"action": "press_key", "key": ""}], "key"),
        ([{"action": "click", "target": {"role": "button", "name": "x"}, "url": "https://evil"}], "url"),
        (["click the button"], "object"),
    ],
)
def test_a_malformed_list_is_refused_before_anything_is_sent(tmp_path, steps, words):
    runtime = _runtime()
    _ready(runtime, tmp_path=tmp_path)
    assert form_steps_problem(steps)
    result = _run(BrowserFillFormTool(runtime), _ctx(tmp_path), {"steps": steps})
    assert not result.ok
    assert result.data["status"] == "invalid"
    assert result.output.startswith("Nothing was done to the page:")
    assert words in result.output
    assert _acting(runtime) == []


def test_read_only_access_refuses_it_like_every_acting_tool(tmp_path):
    runtime = _runtime(access="read_only")
    result = _run(
        BrowserFillFormTool(runtime),
        _ctx(tmp_path),
        {"steps": [{"action": "click", "target": {"role": "button", "name": "Expand details"}}]},
    )
    assert not result.ok
    assert _acting(runtime) == []


# --------------------------------------------------------------------------- #
# 5. Typed text is never written down
# --------------------------------------------------------------------------- #


def test_typed_text_is_redacted_to_its_length_and_absent_from_the_answer(tmp_path):
    tool = BrowserFillFormTool(_runtime())
    args = {
        "tab_id": 42,
        "steps": [
            {"action": "type", "target": {"role": "textbox", "name": "Search"}, "text": SECRET, "clear": True},
            {"action": "click", "target": {"role": "button", "name": "Expand details"}},
        ],
    }
    red = tool.redact_args(args)
    assert SECRET not in json.dumps(red)
    assert red["steps"][0]["text"] == "***REDACTED***"
    assert red["steps"][0]["chars"] == len(SECRET)
    assert red["steps"][1] == args["steps"][1]
    assert args["steps"][0]["text"] == SECRET, "redaction must not mutate the call it was handed"

    runtime = tool.browser
    _ready(runtime, tmp_path=tmp_path)
    result = _run(tool, _ctx(tmp_path), args)
    assert result.ok, result.error
    assert SECRET not in result.output and SECRET not in json.dumps(result.data)
    assert _sent(runtime, P.METHOD_TYPE_TEXT)[0]["text"] == SECRET, "the page must still get it"


@pytest.mark.parametrize(
    "args",
    [
        {"steps": json.dumps([{"action": "type", "target": {"name": "Email"}, "text": SECRET}])},
        {"steps": {"action": "type", "target": {"name": "Email"}, "text": SECRET}},
        {"steps": [f"type {SECRET} into email"]},
        {"steps": [{"action": "type", "target": {"name": "Email"}, "value": SECRET}]},
        {"steps": [], "password": SECRET},
        "not even an object " + SECRET,
    ],
    ids=["json-string", "one-object", "string-step", "unknown-key", "unknown-top-key", "not-a-dict"],
)
def test_a_malformed_call_is_redacted_too_because_its_refusal_is_written_down(args):
    """The registry refuses a malformed list, but ``_record`` still stores the
    redacted arguments: a list that is not a list must not reach disk in plain
    text (review fix)."""
    red = BrowserFillFormTool(_runtime()).redact_args(args)  # type: ignore[arg-type]
    assert SECRET not in json.dumps(red)


# --------------------------------------------------------------------------- #
# 6. The card's words — one fixture, two implementations
# --------------------------------------------------------------------------- #


def _fixture_cases() -> list[dict[str, Any]]:
    t = {"role": "textbox", "name": "Email"}
    return [
        {"step": {"action": "type", "target": t, "text": "***REDACTED***", "chars": 18}},
        {"step": {"action": "type", "target": t, "text": "***REDACTED***", "chars": 1, "clear": True}},
        {"step": {"action": "type", "target": t, "text": "***REDACTED***"}},
        {"step": {"action": "type", "target": {"element_id": "e7"}, "text": "***REDACTED***", "chars": 0}},
        {"step": {"action": "type", "target": {"css": "#zip"}, "text": "***REDACTED***", "chars": 5}},
        {"step": {"action": "type", "text": "***REDACTED***", "chars": 3}},
        {"step": {"action": "click", "target": {"role": "checkbox", "name": "I agree to the terms"}}},
        {"step": {"action": "click", "target": {"role": "button", "name": "  Next \n step  "}}},
        {"step": {"action": "click", "target": {"role": "button", "name": "N" * 80}}},
        {"step": {"action": "click", "target": {"role": "button", "name": "Café 🍰 " * 12}}},
        {"step": {"action": "click"}},
        {"step": {"action": "press_key", "key": "Tab"}},
        {"step": {"action": "press_key", "key": "Enter", "target": {"role": "textbox", "name": "Search"}}},
        {"step": {"action": "press_key"}},
        {"step": {"action": "scroll"}},
        {"step": "not a step"},
    ]


def test_the_card_words_match_the_shared_fixture_the_dashboard_reads():
    """``IJ_REGEN_FORM_STEPS=1`` rewrites the fixture after an intended change;
    ``dashboard/__tests__/form-card-v1400-9.test.tsx`` feeds the same cases
    through ``formStepWords``."""
    cases = [dict(c, words=describe_form_step(c["step"])) for c in _fixture_cases()]
    if os.environ.get("IJ_REGEN_FORM_STEPS") == "1":
        FIXTURE.write_bytes(
            (json.dumps({"cases": cases}, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
    stored = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    assert stored == cases
    words = [c["words"] for c in cases]
    assert words[0] == "type 18 characters into 'Email'"
    assert words[1] == "type 1 character into 'Email' (clearing it first)"
    assert words[2] == "type some text into 'Email'"
    assert words[3] == "type 0 characters into 'element e7'"
    assert words[6] == "click 'I agree to the terms'"
    assert words[7] == "click 'Next step'"
    assert words[11] == "press Tab"
    assert words[12] == "press Enter on 'Search'"
    assert all("REDACTED" not in w for w in words)


def test_an_unredacted_step_still_never_repeats_its_text():
    """The answer's "not sent" list reads the REAL steps: a count, not words."""
    words = describe_form_step({"action": "type", "target": {"name": "Email"}, "text": SECRET})
    assert words == f"type {len(SECRET)} characters into 'Email'"
    assert describe_form_step({"action": "type", "target": {"name": "x"}, "chars": True}) == (
        "type some text into 'x'"
    ), "a boolean is not a count"


def test_the_sidebar_card_lists_every_step_on_its_own_line():
    tool = BrowserFillFormTool(_runtime())
    red = tool.redact_args(
        {
            "steps": [
                {"action": "type", "target": {"role": "textbox", "name": "Email"}, "text": SECRET},
                {"action": "click", "target": {"role": "checkbox", "name": "Remember me"}},
                {"action": "press_key", "key": "Enter"},
            ]
        }
    )
    card = panel_mod.describe_browser_call("browser_fill_form", red, card=True)
    assert card.split("\n") == [
        "fill in a form: 3 steps",
        f"1. type {len(SECRET)} characters into 'Email'",
        "2. click 'Remember me'",
        "3. press Enter",
    ]
    assert SECRET not in card
    # The work fold's one line.
    assert panel_mod.describe_browser_call("browser_fill_form", red) == "fill in a form: 3 steps"


def test_a_long_list_on_the_sidebar_card_says_how_many_more():
    steps = [{"action": "press_key", "key": "Tab"}] * 25
    card = panel_mod.describe_form_call({"steps": steps}, card=True)
    lines = card.split("\n")
    assert lines[0] == "fill in a form: 25 steps"
    assert len(lines) == 1 + panel_mod.FORM_CARD_STEPS + 1
    assert lines[-1] == "…and 5 more"
