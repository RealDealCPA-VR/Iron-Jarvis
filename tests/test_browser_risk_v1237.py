"""Ship 3 (v1.237.0) — the risk decision and the action protocol.

Every test here pins a behaviour that can FAIL. Ship 2's review found three
facades that passed while the behaviour was absent, so each assertion below is
written against the thing that would break if the rule were removed: the reason
string the user reads, the verdict the permission engine returns, the frame the
add-on receives — never the mere existence of a symbol.

What this file covers:

* :func:`iron_jarvis.computeruse.policy.escalate_browser` — the D12 escalation,
  its rule ORDER, its purity, and the fact that it reads the EXISTING
  vocabularies rather than copies of them.
* :mod:`iron_jarvis.browser.risk` — the one door every acting tool passes
  through, the base-risk table of plan §8.5, and Q03's action justification.
* ``DENY_FLOOR_TOOLS`` — the D11 consequences, all three of them.
* :mod:`iron_jarvis.browser.protocol` — the acting params/results of §8.6 and
  the download payload of §10.3.
"""

from __future__ import annotations

import copy

import pytest

from iron_jarvis.browser import protocol as P
from iron_jarvis.browser import risk as R
from iron_jarvis.browser.errors import BrowserError, BrowserErrorCode
from iron_jarvis.computeruse import policy as CP
from iron_jarvis.computeruse.base import Action, Page, Selector
from iron_jarvis.computeruse.policy import (
    ComputerUsePolicy,
    Decision,
    escalate_browser,
)
from iron_jarvis.core.models import PermissionMode
from iron_jarvis.tools.base import RiskClass
from iron_jarvis.tools.permissions import DENY_FLOOR_TOOLS, PermissionEngine

# --------------------------------------------------------------------------- #
# escalate_browser — plan section 8.2's worked examples
# --------------------------------------------------------------------------- #


def _click(label: str) -> Decision:
    """A click addressed by element_id: no name in the selector, so the target
    rules of escalate_browser are the only thing that can see the label.

    That is the REAL shape of the common call — §8.6 prefers `element_id`
    targets — and it is what makes rules 3 and 4 live code rather than a second
    scan that `classify` had already done.
    """
    return escalate_browser(
        RiskClass.PAGE_ACTION,
        Action(kind="click", selector=Selector()),
        None,
        target_label=label,
    )


def test_expand_details_does_not_escalate():
    decision = _click("Expand details")
    assert decision.allowed is True
    assert decision.requires_approval is False
    assert decision.reason == "allowed by policy"


def test_delete_account_escalates_on_the_destructive_vocabulary():
    decision = _click("Delete account")
    assert decision.requires_approval is True
    # Names the word that matched, so the approval card can say WHY.
    assert "delete" in decision.reason
    assert "destructive" in decision.reason


def test_submit_payment_escalates():
    # The plan's third worked example. It matches `_DESTRUCTIVE_WORDS` first
    # ("pay" is in that tuple, and the destructive rule is ordered before the
    # payment rule), so the reason names the destructive vocabulary. What the
    # example asserts — that "Submit payment" escalates — holds either way.
    decision = _click("Submit payment")
    assert decision.requires_approval is True
    assert "pay" in decision.reason


def test_a_payment_only_label_escalates_on_the_payment_vocabulary():
    # Reaches rule 4: "card number" is in `_PAYMENT_WORDS` and in no destructive
    # word, so this is the case that proves the payment arm is wired at all.
    decision = _click("Enter card number")
    assert decision.requires_approval is True
    assert decision.reason == "payment/transactional target"


def test_read_and_local_ui_never_escalate_however_alarming_the_label():
    # Rule 1 short-circuits before any vocabulary is consulted: scrolling a page
    # whose heading says "Delete account" commits nothing.
    for base in (RiskClass.READ, RiskClass.LOCAL_UI):
        decision = escalate_browser(
            base,
            Action(kind="click", selector=Selector()),
            None,
            target_label="Delete account and wire payment",
        )
        assert decision.requires_approval is False, base
        assert decision.reason == "allowed by policy"


def test_an_undeclared_risk_class_asks_rather_than_acts():
    # Tool.risk_class DEFAULTS to EXTERNAL_COMMIT, so a future acting tool that
    # forgets to declare must land here and ask.
    decision = escalate_browser(
        RiskClass.EXTERNAL_COMMIT,
        Action(kind="click", selector=Selector()),
        None,
        target_label="Continue",
    )
    assert decision.requires_approval is True
    assert "undeclared" in decision.reason


def test_typing_into_a_password_field_escalates_even_with_a_harmless_name():
    # The field's `type` is what catches it — the visible name says nothing about
    # a password, which is the case a human reviewer would miss.
    field = {"role": "textbox", "name": "Continue", "type": "password", "css": "#f"}
    decision = escalate_browser(
        RiskClass.PAGE_ACTION,
        Action(kind="type", selector=Selector(css="#f"), value="hunter2"),
        Page(url="https://example.com/login", a11y_tree=[field]),
        target_label="Continue",
    )
    assert decision.requires_approval is True
    assert decision.reason == "typing into a password field"


def test_escalate_browser_never_denies():
    # It escalates; it never denies. Denial belongs to the permission engine and
    # the deny floor, whose refusals a permissions screen can explain.
    for label in ("Expand details", "Delete account", "Enter card number"):
        assert _click(label).allowed is True


def test_escalate_browser_is_pure_and_mutates_nothing_it_is_given():
    action = Action(kind="type", selector=Selector(css="#f"), value="secret")
    field = {"role": "textbox", "name": "Card number", "type": "text"}
    page = Page(url="https://shop.example/checkout", a11y_tree=[field])
    policy = ComputerUsePolicy(domain_allowlist=["example.com"])
    before = (copy.deepcopy(action), copy.deepcopy(page), copy.deepcopy(policy))

    first = escalate_browser(
        RiskClass.PAGE_ACTION, action, page, target_label="Pay now", policy=policy
    )
    second = escalate_browser(
        RiskClass.PAGE_ACTION, action, page, target_label="Pay now", policy=policy
    )

    assert first == second, "same inputs must give the same answer"
    assert (action, page, policy) == before, "escalate_browser mutated an argument"


def test_escalation_reads_the_existing_vocabularies_not_a_second_copy():
    # D12: no competing browser-specific policy engine. If someone forked the
    # word lists, adding a word to the real one would stop changing the browser's
    # answer — and the two halves would each look right alone.
    # A sentinel that is in NO vocabulary and contains no vocabulary word as a
    # substring, so the only thing that can make it escalate is the patch below.
    # (It used to be "unsubscribeeverything", which stopped being a sentinel the
    # moment "unsubscribe" entered the shared destructive list.)
    word = "zzqqxx"
    for vocab in (CP._DESTRUCTIVE_WORDS, CP._PAYMENT_WORDS, CP._PASSWORD_WORDS, CP._PII_WORDS):
        assert not any(w in word for w in vocab), "the sentinel is no longer neutral"
    assert _click(f"{word} now").requires_approval is False
    patched = CP._DESTRUCTIVE_WORDS + (word,)
    original = CP._DESTRUCTIVE_WORDS
    CP._DESTRUCTIVE_WORDS = patched
    try:
        assert _click(f"{word} now").requires_approval is True
    finally:
        CP._DESTRUCTIVE_WORDS = original
    assert _click(f"{word} now").requires_approval is False


def test_the_browser_does_not_hold_its_own_word_lists():
    import inspect

    source = inspect.getsource(R)
    for name in ("_DESTRUCTIVE_WORDS", "_PAYMENT_WORDS", "_PASSWORD_WORDS"):
        assert f"{name} =" not in source, f"{name} was copied into browser/risk.py"


# --------------------------------------------------------------------------- #
# browser/risk.py — the one door
# --------------------------------------------------------------------------- #


def test_base_risk_declares_all_fourteen_tools_at_the_plans_classes():
    assert len(R.BASE_RISK) == 14
    reads = {n for n, r in R.BASE_RISK.items() if r is RiskClass.READ}
    local = {n for n, r in R.BASE_RISK.items() if r is RiskClass.LOCAL_UI}
    actions = {n for n, r in R.BASE_RISK.items() if r is RiskClass.PAGE_ACTION}
    assert len(reads) == 6 and len(local) == 4 and len(actions) == 4
    assert actions == {
        "browser_click",
        "browser_type",
        "browser_press_key",
        "browser_navigate",
    }
    # The table and the wire agree: every tool's method sits in the method tuple
    # of its own risk class, so a tool cannot be classified one way here and
    # another way at the transport's read-only gate.
    for name, method in R._TOOL_METHOD.items():
        expected = {
            RiskClass.LOCAL_UI: P.LOCAL_UI_METHODS,
            RiskClass.PAGE_ACTION: P.PAGE_ACTION_METHODS,
        }[R.BASE_RISK[name]]
        assert method in expected, f"{name} -> {method} is in the wrong risk tier"


def test_the_state_changing_set_is_exactly_the_deny_floor_four():
    assert R.STATE_CHANGING_TOOLS == {
        "browser_click",
        "browser_type",
        "browser_press_key",
        "browser_navigate",
    }
    assert R.STATE_CHANGING_TOOLS <= DENY_FLOOR_TOOLS


def test_an_unknown_tool_name_fails_safe_to_asking():
    assert R.base_risk("browser_wire_money") is RiskClass.EXTERNAL_COMMIT
    assert R.browser_risk_decision("browser_wire_money").requires_approval is True


def test_the_door_reaches_the_same_verdicts_as_the_plans_examples():
    assert (
        R.browser_risk_decision("browser_click", target_label="Expand details").requires_approval
        is False
    )
    assert (
        R.browser_risk_decision("browser_click", target_label="Delete account").requires_approval
        is True
    )
    assert (
        R.browser_risk_decision("browser_click", target_label="Submit payment").requires_approval
        is True
    )


def test_typing_is_classified_as_typing_not_as_clicking():
    # The action KIND decides which arms of `classify` run. A `type` presented as
    # a `click` would let a password reach a field with no card shown, and the
    # only visible symptom would be a missing approval.
    decision = R.browser_risk_decision(
        "browser_type",
        target_label="Continue",
        text="hunter2",
        target_element={"role": "textbox", "name": "Continue", "autocomplete": "current-password"},
    )
    assert decision.requires_approval is True
    assert decision.reason == "typing into a password field"


def test_a_field_the_page_marked_sensitive_asks_even_with_no_other_evidence():
    # D13B's scrubber nulls the value and sets `sensitive`, so by the time the row
    # reaches the classifier the evidence it would have used can be gone.
    decision = R.browser_risk_decision(
        "browser_type",
        target_label="Code",
        text="123456",
        target_element={"role": "textbox", "name": "Code", "sensitive": True},
    )
    assert decision.requires_approval is True
    assert "sensitive" in decision.reason


def test_the_target_element_is_matched_so_its_autocomplete_is_actually_read():
    # An element_id target has no selector of its own; without the sentinel that
    # ties Selector to the one-element Page, `_field_for` would find nothing and
    # every autocomplete-only credential field would pass unescalated.
    quiet_field = {"role": "textbox", "name": "Continue", "autocomplete": "cc-number"}
    decision = R.browser_risk_decision(
        "browser_type", target_label="Continue", text="4111111111111111",
        target_element=quiet_field,
    )
    assert decision.requires_approval is True
    assert decision.reason == "typing payment/credit-card data"


def test_a_configured_domain_allowlist_is_enforced_on_navigate():
    policy = ComputerUsePolicy(domain_allowlist=["example.com"])
    allowed = R.browser_risk_decision(
        "browser_navigate", url="https://example.com/reports", policy=policy
    )
    assert allowed.requires_approval is False
    off_list = R.browser_risk_decision(
        "browser_navigate", url="https://evil.test/steal", policy=policy
    )
    assert off_list.requires_approval is True
    assert "allowlist" in off_list.reason


def test_an_unconfigured_allowlist_does_not_make_every_navigation_ask():
    # An empty allowlist is the DEFAULT. Reading it as "nothing is allowed" would
    # put an identical card in front of every navigation, which is how a user
    # learns to approve without reading.
    for policy in (None, ComputerUsePolicy()):
        decision = R.browser_risk_decision(
            "browser_navigate", url="https://example.com/reports", policy=policy
        )
        assert decision.requires_approval is False, policy


def test_risk_row_names_the_class_and_what_was_done_about_it():
    allowed = R.risk_row(
        "browser_click", Decision(True, False, "allowed by policy")
    )
    assert allowed == {
        "base": "page_action",
        "decision": "allowed",
        "reason": "allowed by policy",
        "tool": "browser_click",
    }
    asked = R.risk_row("browser_click", Decision(True, True, "destructive"))
    assert asked["decision"] == "approved"
    # A tool judged by ANOTHER tool's rules (``_ActingTool.risk_name``) records
    # both: ``base`` is the rules that applied, ``tool`` is what actually ran. A
    # row naming browser_navigate for a tab that was OPENED sends an auditor
    # looking for a page that was replaced.
    borrowed = R.risk_row(
        "browser_navigate", Decision(True, False, "allowed by policy"), tool="browser_create_tab"
    )
    assert borrowed["base"] == "page_action"
    assert borrowed["tool"] == "browser_create_tab"


# --------------------------------------------------------------------------- #
# Q03 — the action justification (plan section 9.5)
# --------------------------------------------------------------------------- #


class _FakeSnapshot:
    def __init__(self, security):
        self.security = security


class _FakeCache:
    def __init__(self, rows):
        self._rows = dict(rows)

    def get(self, tab_id):
        return self._rows.get(tab_id)


def test_a_flagged_tab_is_read_off_the_snapshots_security_note():
    cache = _FakeCache(
        {
            7: _FakeSnapshot({"warning": True, "category": "instruction_override", "reason": "x"}),
            8: _FakeSnapshot(None),
        }
    )
    assert R.tab_is_flagged(cache, 7) is True
    assert R.tab_is_flagged(cache, 8) is False
    # A tab nobody has read is not flagged: the acting call is already refused
    # STALE_SNAPSHOT upstream, with a remedy the model can act on.
    assert R.tab_is_flagged(cache, 99) is False
    assert R.tab_is_flagged(None, 7) is False


def test_a_flagged_page_plus_an_off_request_target_escalates():
    decision = R.browser_risk_decision(
        "browser_click",
        # Neutral words: nothing in the escalation vocabulary, so the ONLY rule
        # that can raise this call is Q03's justification.
        target_label="Continue to step 3",
        page_flagged=True,
        request_text="summarise this invoice for me",
    )
    assert decision.requires_approval is True
    assert decision.reason == R.INJECTION_JUSTIFICATION_REASON


def test_a_flagged_page_does_not_escalate_a_target_the_user_named():
    decision = R.browser_risk_decision(
        "browser_click",
        target_label="Next page",
        page_flagged=True,
        request_text="Please click Next page and tell me what it says",
    )
    assert decision.requires_approval is False


def test_missing_request_text_fails_closed_to_requiring_approval():
    # The plan is explicit: if the request text is unavailable it must fail CLOSED.
    # Treating "we could not check" as "it checked out" would be invisible.
    for request_text in (None, "", "   "):
        decision = R.browser_risk_decision(
            "browser_click",
            target_label="Next page",
            page_flagged=True,
            request_text=request_text,
        )
        assert decision.requires_approval is True, repr(request_text)
        assert decision.reason == R.INJECTION_JUSTIFICATION_REASON


def test_an_unnamed_target_on_a_flagged_page_fails_closed_too():
    decision = R.browser_risk_decision(
        "browser_click",
        target_label="",
        page_flagged=True,
        request_text="click the button",
    )
    assert decision.requires_approval is True


def test_the_justification_only_constrains_state_changing_calls():
    # Scrolling a flagged page commits nothing; gating it would put a card in
    # front of ordinary reading.
    decision = R.browser_risk_decision(
        "browser_scroll", target_label="down", page_flagged=True, request_text=None
    )
    assert decision.requires_approval is False


def test_the_justification_can_only_raise_never_lower():
    # A destructive target the user DID name still escalates on the vocabulary.
    decision = R.browser_risk_decision(
        "browser_click",
        target_label="Delete account",
        page_flagged=True,
        request_text="delete account for me",
    )
    assert decision.requires_approval is True
    assert "destructive" in decision.reason


def test_the_request_text_seam_has_one_spelling():
    class _Runtime:
        request_text = "click Sign in"

    assert R.request_text_of(_Runtime()) == "click Sign in"
    assert R.request_text_of(None) == ""
    assert R.request_text_of(object()) == ""


# --------------------------------------------------------------------------- #
# The deny floor (D11, plan section 8.3)
# --------------------------------------------------------------------------- #

_FLOOR_FOUR = ("browser_click", "browser_type", "browser_press_key", "browser_navigate")


@pytest.mark.parametrize("tool", _FLOOR_FOUR)
def test_the_four_page_action_tools_are_on_the_deny_floor(tool):
    assert tool in DENY_FLOOR_TOOLS


@pytest.mark.parametrize(
    "tool",
    [
        "browser_get_status",
        "browser_list_tabs",
        "browser_get_active_tab",
        "browser_read_page",
        "browser_get_elements",
        "browser_screenshot",
        "browser_activate_tab",
        "browser_scroll",
        "browser_create_tab",
        "browser_close_tab",
    ],
)
def test_the_read_and_local_ui_tools_are_not_on_the_deny_floor(tool):
    # D11 names exactly four. A floor that crept wider would make reading the
    # page an ask an agent definition could never grant.
    assert tool not in DENY_FLOOR_TOOLS


@pytest.mark.parametrize("tool", _FLOOR_FOUR)
def test_an_allow_override_on_a_floor_browser_tool_is_dropped(tool):
    engine = PermissionEngine({tool: "ask"})
    assert engine.mode_for(tool, {tool: "allow"}) is PermissionMode.ASK
    # And with no resolver — the headless case — that ask fails closed.
    assert engine.authorize(tool, {}, {tool: "allow"}).allowed is False


@pytest.mark.parametrize("tool", _FLOOR_FOUR)
def test_a_session_grant_still_lifts_the_ask(tool):
    # This is what makes "Allow for this conversation" on the approval card work.
    # Without it every step of a multi-step flow would re-ask.
    engine = PermissionEngine({tool: "ask"})
    decision = engine.authorize(tool, {}, None, session_allow=[tool])
    assert decision.allowed is True
    assert decision.reason == "granted for this task"


@pytest.mark.parametrize("tool", _FLOOR_FOUR)
def test_a_base_deny_is_never_lifted_by_a_session_grant(tool):
    engine = PermissionEngine({tool: "deny"})
    assert engine.authorize(tool, {}, None, session_allow=[tool]).allowed is False


@pytest.mark.parametrize("tool", _FLOOR_FOUR)
def test_an_override_may_still_lower_a_floor_tool(tool):
    engine = PermissionEngine({tool: "ask"})
    assert engine.mode_for(tool, {tool: "deny"}) is PermissionMode.DENY


# --------------------------------------------------------------------------- #
# The action protocol (plan section 8.6)
# --------------------------------------------------------------------------- #


def test_every_acting_method_has_a_params_and_a_result_shape():
    for method in P.LOCAL_UI_METHODS + P.PAGE_ACTION_METHODS:
        assert method in P.PARAM_SHAPES, f"{method} has no params shape"
        assert method in P.RESULT_SHAPES, f"{method} has no result shape"


def test_tab_id_is_optional_on_every_acting_method_but_the_two_that_name_a_tab():
    # Section 8.6's shared convention: omitted tab_id means the active tab, so a
    # model never needs two calls to act on what the user is looking at. The two
    # exceptions name a tab by definition — "activate whatever is already active"
    # is meaningless, and "close whatever is active" is how the user loses the tab
    # they were reading.
    #
    # Optionality is read off the ANNOTATION, not off __optional_keys__: this
    # module uses `from __future__ import annotations`, which leaves that set
    # empty — the same trap the protocol generator documents.
    from typing import NotRequired, get_origin, get_type_hints

    required_tab = {P.METHOD_ACTIVATE_TAB, P.METHOD_CLOSE_TAB}
    seen = 0
    for method, shape in P.PARAM_SHAPES.items():
        hints = get_type_hints(shape, include_extras=True)
        if "tab_id" not in hints:
            continue
        seen += 1
        is_optional = get_origin(hints["tab_id"]) is NotRequired
        assert is_optional is (method not in required_tab), method
    assert seen == len(P.PARAM_SHAPES) - 1, "only create_tab has no tab_id at all"


def test_the_type_result_has_no_field_that_could_carry_the_typed_text():
    from typing import get_type_hints

    keys = set(get_type_hints(P.TypeTextResult, include_extras=True))
    assert "text" not in keys
    assert "value" not in keys
    # And the params shape, which does carry it, is the redaction contract's
    # subject: `text` is required there so no caller can omit it and type nothing.
    assert "text" in get_type_hints(P.TypeTextParams, include_extras=True)


def test_a_target_with_two_forms_is_refused_by_name():
    with pytest.raises(BrowserError) as caught:
        P.normalise_target({"element_id": "e17", "css": "#submit"})
    assert caught.value.code == BrowserErrorCode.ELEMENT_NOT_FOUND.value
    assert "element_id" in caught.value.message and "css" in caught.value.message


def test_an_empty_target_is_refused_with_the_three_forms_named():
    with pytest.raises(BrowserError) as caught:
        P.normalise_target({})
    assert "element_id" in caught.value.message
    assert "browser_read_page" in caught.value.message


def test_a_role_target_needs_its_name():
    with pytest.raises(BrowserError):
        P.normalise_target({"role": "button"})
    assert P.normalise_target({"role": "button", "name": "Sign in"}) == {
        "role": "button",
        "name": "Sign in",
    }


def test_a_target_keeps_only_the_form_it_named():
    assert P.normalise_target({"element_id": "e17"}) == {"element_id": "e17"}
    assert P.normalise_target({"css": "#submit"}) == {"css": "#submit"}
    # An unknown key is dropped, not refused: a caller a version ahead must not
    # break a well-formed call.
    assert P.normalise_target({"element_id": "e2", "frame": "top"}) == {"element_id": "e2"}


def test_an_unknown_scroll_direction_is_refused_before_a_frame_exists():
    # The content script compares the raw string, so a bad direction would scroll
    # nowhere and answer success.
    with pytest.raises(BrowserError) as caught:
        P.scroll_params(direction="Down")
    assert "up, down, top, bottom" in caught.value.message
    assert P.scroll_params(direction="down") == {"direction": "down"}
    assert P.scroll_params(4, direction="up", amount=300) == {
        "direction": "up",
        "tab_id": 4,
        "amount": 300,
    }


def test_navigate_params_refuse_a_page_chrome_closes_to_add_ons():
    with pytest.raises(BrowserError) as caught:
        P.navigate_params("chrome://settings")
    assert caught.value.code == BrowserErrorCode.UNSUPPORTED_PAGE.value
    assert "chrome:" in caught.value.message
    with pytest.raises(BrowserError):
        P.navigate_params("")
    assert P.navigate_params("https://example.com", 3) == {
        "url": "https://example.com",
        "tab_id": 3,
    }


def test_type_text_params_always_send_both_booleans():
    # A missing key would have to be defaulted in the add-on, and the two
    # possible defaults differ by whether the user's existing text survives.
    params = P.type_text_params({"element_id": "e4"}, "hello")
    assert params["clear"] is False and params["press_enter"] is False
    assert params == {
        "target": {"element_id": "e4"},
        "text": "hello",
        "clear": False,
        "press_enter": False,
    }
    emptied = P.type_text_params({"element_id": "e4"}, "", clear=True)
    assert emptied["text"] == "" and emptied["clear"] is True


def test_press_key_refuses_an_empty_key():
    with pytest.raises(BrowserError):
        P.press_key_params("")
    assert P.press_key_params("Enter", 2, target={"element_id": "e9"}) == {
        "key": "Enter",
        "tab_id": 2,
        "target": {"element_id": "e9"},
    }


def test_a_bool_is_not_accepted_as_a_tab_id():
    # `True` is an int in Python; `activate_tab(True)` would put tab_id 1 on the
    # wire and activate a tab the caller never named.
    with pytest.raises(BrowserError):
        P.activate_tab_params(True)
    with pytest.raises(BrowserError):
        P.close_tab_params(0)
    assert P.activate_tab_params(12) == {"tab_id": 12}


def test_create_tab_omits_an_empty_url_rather_than_inventing_about_blank():
    assert P.create_tab_params() == {"active": True}
    assert P.create_tab_params("   ", active=False) == {"active": False}
    assert P.create_tab_params("https://example.com") == {
        "active": True,
        "url": "https://example.com",
    }


def test_the_download_payload_carries_no_local_path_the_daemon_has_not_verified():
    from typing import get_type_hints

    keys = set(get_type_hints(P.DownloadPayload, include_extras=True))
    assert "filename" in keys and "source_url" in keys
    assert "local_path" not in keys, (
        "local_path is the daemon's verified-absolute key; a wire field of that "
        "name would let the add-on hand the file tools a path nobody checked"
    )
    assert P.EVENT_PAYLOAD_SHAPES[P.EVENT_DOWNLOAD_COMPLETED] is P.DownloadPayload


def test_the_acting_shapes_reach_the_generated_typescript():
    from iron_jarvis.browser import gen_protocol

    generated = gen_protocol.render()
    for shape in (
        P.Target,
        P.TargetRef,
        P.ClickParams,
        P.TypeTextParams,
        P.PressKeyParams,
        P.NavigateParams,
        P.ScrollParams,
        P.ActivateTabParams,
        P.CreateTabParams,
        P.CloseTabParams,
        P.DownloadPayload,
        P.ClickResult,
        P.TypeTextResult,
        P.PressKeyResult,
        P.NavigateResult,
    ):
        assert f"export interface {shape.__name__} {{" in generated
    for direction in P.SCROLL_DIRECTIONS:
        assert f'"{direction}"' in generated


# --------------------------------------------------------------------------- #
# The gate BETWEEN a real tool and the door (the two S1s of the Ship-3 review)
#
# Everything above this line calls ``browser_risk_decision`` or
# ``escalate_browser`` directly, and that is exactly how the review's worst
# finding stayed invisible: a pin that supplies the input it claims the system
# supplies proves nothing about the system. The gap was never inside the door, it
# was between the TOOL and the door — ``plan.label`` is "" for a css target, for a
# control with no accessible name, and for a bare key press, and an empty label
# used to disarm the whole vocabulary.
#
# So these drive the REAL tools over the scripted peer and assert against the
# TRANSPORT: the frame that was, or was not, sent. A card shown after the click
# has happened is not a gate.
# --------------------------------------------------------------------------- #

import asyncio  # noqa: E402
import json  # noqa: E402
from pathlib import Path  # noqa: E402
from types import SimpleNamespace  # noqa: E402
from typing import Any  # noqa: E402

from iron_jarvis.browser.service import ACCESS_INTERACTIVE, BrowserRuntime  # noqa: E402
from iron_jarvis.browser.snapshot import SnapshotCache  # noqa: E402
from iron_jarvis.browser.tools import (  # noqa: E402
    BrowserClickTool,
    BrowserNavigateTool,
    BrowserPressKeyTool,
    BrowserReadPageTool,
    BrowserTypeTool,
)
from iron_jarvis.tools.base import ToolContext  # noqa: E402

from ._fakes.browser_peer import BrowserPeer, FakeElement, FakeTab, ScriptedBrowser  # noqa: E402
from .test_browser_tools_v1235 import FakeBackend, FakeConfig  # noqa: E402

#: A credential-shaped string with none of the classifier's own vocabulary in it,
#: so a substring search over a whole payload PROVES absence rather than
#: suggesting it, and so its presence never escalates a call by accident.
GATE_SECRET = "hunter2-XYZZY-NEVER-WRITTEN-DOWN-9f3a"


class _Approvals:
    """``ApprovalQueue``'s surface in memory, with consume-on-use intact.

    Present so an escalated call takes the REAL approval branch — a runtime with
    no queue refuses with different words, and a test that keyed off those words
    would pass on any refusal at all, including a broken tool.
    """

    def __init__(self) -> None:
        self.rows: list[Any] = []
        self._n = 0

    def create_request(self, run_id: str, action: Any, reason: str, **kw: Any) -> Any:
        self._n += 1
        row = SimpleNamespace(
            id=f"apr_{self._n}",
            run_id=str(run_id),
            action_json=json.dumps(action.to_dict(), default=str),
            reason=str(reason),
            status="pending",
        )
        self.rows.append(row)
        return row

    def approved_unconsumed(self, run_id: str, action: Any, *args: Any, **kw: Any) -> Any:
        signature = json.dumps(action.to_dict(), default=str)
        for row in reversed(self.rows):
            if row.run_id == run_id and row.status == "approved" and row.action_json == signature:
                return row
        return None

    def _set(self, request_id: str, status: str) -> Any:
        for row in self.rows:
            if row.id == request_id:
                row.status = status
                return row
        return None

    def approve(self, request_id: str, *args: Any, **kw: Any) -> Any:
        return self._set(request_id, "approved")

    def deny(self, request_id: str, *args: Any, **kw: Any) -> Any:
        return self._set(request_id, "denied")

    def consume(self, request_id: str, *args: Any, **kw: Any) -> Any:
        return self._set(request_id, "consumed")


class _Runtime(BrowserRuntime):
    """The REAL runtime with a real ``SnapshotCache``, over the scripted peer."""

    def __init__(self, peer: BrowserPeer, approvals: Any) -> None:
        backend = FakeBackend(peer)
        super().__init__(
            backend=backend,
            config=FakeConfig(ACCESS_INTERACTIVE),
            snapshots=SnapshotCache(),
            policy=ComputerUsePolicy(),
            approvals=approvals,
        )
        #: ``(method, params)`` per command that reached the transport.
        self.calls = backend.calls


#: Controls a real user would recognise. NONE of them carries markup that would
#: give the classifier a second way to catch it (no ``type="password"``, no
#: payment ``autocomplete``): every one escalates on its NAME or not at all,
#: which is what makes these pins fail when the label scan is removed.
_GATE_PAGE_ELEMENTS: tuple[FakeElement, ...] = (
    FakeElement(id="e1", role="textbox", name="Search", field_type="text"),
    FakeElement(id="e2", role="button", name="Expand details"),
    FakeElement(id="e3", role="button", name="Password"),
    FakeElement(id="e4", role="button", name="Update SSN"),
    FakeElement(id="e5", role="button", name="Revoke access"),
    FakeElement(id="e6", role="button", name="Withdraw"),
    FakeElement(id="e7", role="button", name="Empty trash"),
    FakeElement(id="e8", role="button", name="Sign out"),
    FakeElement(id="e9", role="button", name="Terminate"),
    FakeElement(id="e10", role="button", name="Reset password"),
    FakeElement(id="e11", role="button", name="Sell"),
    FakeElement(id="e12", role="button", name="Delete account"),
)


def _gate_page() -> ScriptedBrowser:
    return ScriptedBrowser(
        [
            FakeTab(
                id=42,
                title="Account",
                url="https://portal.example/account",
                active=True,
                text="Manage your account.",
                elements=list(_GATE_PAGE_ELEMENTS),
            )
        ]
    )


def _gate_ctx(tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="test",
        agent_run_id="run",
        config=SimpleNamespace(),  # type: ignore[arg-type]
        event_bus=SimpleNamespace(),  # type: ignore[arg-type]
        engine=SimpleNamespace(),  # type: ignore[arg-type]
    )


def _gate_run(tool: Any, tmp_path: Path, args: dict[str, Any]) -> Any:
    return asyncio.run(tool.execute(args, _gate_ctx(tmp_path)))


def _gate_wire(runtime: "_Runtime", method: str) -> list[dict[str, Any]]:
    return [params for name, params in runtime.calls if name == method]


def _gate_ready(runtime: "_Runtime", tmp_path: Path) -> None:
    """Read the page, so an acting call has a snapshot to be judged against."""
    assert _gate_run(BrowserReadPageTool(runtime), tmp_path, {"tab_id": 42}).ok


def _gate_setup(tmp_path: Path) -> tuple["_Runtime", "_Approvals"]:
    approvals = _Approvals()
    runtime = _Runtime(BrowserPeer(None, page=_gate_page()), approvals)
    _gate_ready(runtime, tmp_path)
    return runtime, approvals


def _asked(result: Any, approvals: "_Approvals", reason_part: str) -> None:
    """A card was raised naming ``reason_part``, and the model was told to wait."""
    assert not result.ok, result.output
    assert "approval required" in (result.error or ""), result.error
    assert reason_part in (result.error or ""), result.error
    assert approvals.rows, "no approval row was written"
    assert reason_part in approvals.rows[-1].reason


def test_a_css_target_cannot_reach_the_page_without_a_card(tmp_path):
    """The finding, driven: a "Delete account" button addressed by css used to click.

    ``P.target_label`` is "" for a css form and no snapshot row is resolved for
    one, so the label the whole vocabulary is scanned against arrived empty and
    the verdict was "allowed by policy". The daemon must stop this before the
    frame exists, because that is the only place it CAN be stopped: a css selector
    is resolved in the page, so by the time anything could read the button's name
    the click has already happened.

    Asserted against the transport. The scripted peer would refuse this css target
    anyway — the gate under test is the daemon's, and it has to hold for the css
    targets the real content script resolves perfectly well.
    """
    runtime, approvals = _gate_setup(tmp_path)
    result = _gate_run(
        BrowserClickTool(runtime),
        tmp_path,
        {"tab_id": 42, "target": {"css": "#delete-account"}},
    )
    _asked(result, approvals, R.UNCHECKED_TARGET_REASON)
    assert _gate_wire(runtime, P.METHOD_CLICK) == [], "the click reached the page"


def test_typing_into_a_css_addressed_field_asks_before_the_text_is_sent(tmp_path):
    """The same hole with the higher cost: a password typed into an unnamed field.

    A css target resolves no snapshot row, so the field's ``type``, its
    ``autocomplete`` and its accessible name — the three things that catch a
    credential field — are all absent, and the text was sent anyway. It must not
    reach the page, and it must not reach the card the user reads either.
    """
    runtime, approvals = _gate_setup(tmp_path)
    result = _gate_run(
        BrowserTypeTool(runtime),
        tmp_path,
        {"tab_id": 42, "target": {"css": "#pw"}, "text": GATE_SECRET},
    )
    _asked(result, approvals, R.UNCHECKED_TARGET_REASON)
    assert _gate_wire(runtime, P.METHOD_TYPE_TEXT) == []
    written = json.dumps(
        [row.reason for row in approvals.rows]
        + [row.action_json for row in approvals.rows]
        + [result.output, result.error or ""]
    )
    assert GATE_SECRET not in written


def test_a_bare_key_press_asks_and_naming_the_target_is_the_remedy(tmp_path):
    """``browser_press_key`` with no target carried nothing at all, and was allowed.

    Enter on a focused "Delete account" button is the same event as clicking it,
    and the daemon cannot see what has focus. Both halves are asserted, because
    the first alone is satisfied by a tool that refuses everything: an untargeted
    press asks, and the SAME key press addressed to a named control goes straight
    through — which is also the remedy the model can act on, and the form section
    8.6 already prefers.
    """
    runtime, approvals = _gate_setup(tmp_path)
    blind = _gate_run(BrowserPressKeyTool(runtime), tmp_path, {"tab_id": 42, "key": "Enter"})
    _asked(blind, approvals, R.UNCHECKED_TARGET_REASON)
    assert _gate_wire(runtime, P.METHOD_PRESS_KEY) == []

    _gate_ready(runtime, tmp_path)
    named = _gate_run(
        BrowserPressKeyTool(runtime),
        tmp_path,
        {"tab_id": 42, "key": "Enter", "target": {"element_id": "e1"}},
    )
    assert named.ok, named.error
    assert _gate_wire(runtime, P.METHOD_PRESS_KEY)[-1]["key"] == "Enter"
    assert len(approvals.rows) == 1, "the named key press should not have asked"


def test_an_ordinary_named_control_still_clicks_with_no_card(tmp_path):
    """The counterweight, and it is load-bearing.

    A fail-closed rule that also stopped "Expand details" would put an identical
    card in front of every click, and a user who approves an identical card every
    time has stopped reading the cards that matter. The rule fires on a target
    that could not be READ, not on every target.
    """
    runtime, approvals = _gate_setup(tmp_path)
    result = _gate_run(
        BrowserClickTool(runtime), tmp_path, {"tab_id": 42, "target": {"element_id": "e2"}}
    )
    assert result.ok, result.error
    assert approvals.rows == []
    assert _gate_wire(runtime, P.METHOD_CLICK)[-1]["target"] == {"element_id": "e2"}


@pytest.mark.parametrize(
    ("element_id", "reason_part"),
    [
        ("e3", "credential target"),
        ("e4", "personal/PII target"),
    ],
)
def test_a_control_named_for_a_secret_escalates_on_its_name_alone(
    tmp_path, element_id, reason_part
):
    """The other half of the S1: the credential and PII lists were never scanned.

    ``escalate_browser`` scanned the label against the destructive and payment
    vocabularies only, and ``classify`` runs the credential/PII arms solely for a
    ``type`` action over its own haystack — which never contains the browser's
    label. So a control the user reads as "Password" or "Update SSN" escalated on
    NOTHING unless the page also reported ``type="password"``.

    Both of these are plain buttons with no such markup, which is the point: the
    name is the only evidence, and the name is what the user is reading.
    """
    runtime, approvals = _gate_setup(tmp_path)
    result = _gate_run(
        BrowserClickTool(runtime), tmp_path, {"tab_id": 42, "target": {"element_id": element_id}}
    )
    _asked(result, approvals, reason_part)
    assert _gate_wire(runtime, P.METHOD_CLICK) == []


@pytest.mark.parametrize("element_id", ["e5", "e6", "e7", "e8", "e9", "e10", "e11", "e12"])
def test_the_destructive_vocabulary_covers_the_controls_a_user_would_mind(
    tmp_path, element_id
):
    """Revoke access, Withdraw, Empty trash, Sign out, Terminate, Sell — all clicked.

    Every one of these went through with no card before this ship's review: the
    shared list held "delete" and "pay" and stopped well short of the controls on
    a bank page and an email page, which are the two tabs the user has open. Fixed
    in ``computeruse/policy._DESTRUCTIVE_WORDS`` — the ONE list — so computer use
    gains the same coverage and the two halves cannot disagree (D12).

    "Delete account" is in the list as the control: it escalated before, so a
    change that broke the whole scan would be visible here rather than only in the
    new rows.
    """
    runtime, approvals = _gate_setup(tmp_path)
    result = _gate_run(
        BrowserClickTool(runtime), tmp_path, {"tab_id": 42, "target": {"element_id": element_id}}
    )
    assert not result.ok, f"{element_id} clicked with no card: {result.output}"
    assert "approval required" in (result.error or "")
    # "target" when the label scan caught it, "action" when ``classify`` did off
    # the resolved row's own name — either is the destructive vocabulary, and
    # pinning one spelling would fail on a rule that still worked.
    assert "destructive/transactional" in (result.error or "")
    assert _gate_wire(runtime, P.METHOD_CLICK) == []


def test_navigating_to_a_transactional_url_still_escalates_by_its_words(tmp_path):
    """Rewritten: it used to hand the door a ``target_label`` navigate NEVER produces.

    ``BrowserNavigateTool.plan`` calls ``prepare_action(need_snapshot=False)`` with
    no target, so ``plan.label`` is always "" and the old pin was green while the
    behaviour was absent — https://shop.test/checkout was "allowed by policy" on
    the user's real browser. A URL is a target with words on it, so ``classify``
    now scans a navigation's destination against the same vocabulary. This drives
    the REAL tool and watches the wire, and it asserts the ordinary navigation too:
    a rule that stopped every URL would be a card nobody reads.
    """
    runtime, approvals = _gate_setup(tmp_path)
    asked = _gate_run(
        BrowserNavigateTool(runtime),
        tmp_path,
        {"tab_id": 42, "url": "https://shop.test/checkout"},
    )
    assert not asked.ok, asked.output
    assert "approval required" in (asked.error or "")
    assert "checkout" in (asked.error or "")
    assert _gate_wire(runtime, P.METHOD_NAVIGATE) == [], "the navigation happened anyway"

    ordinary = _gate_run(
        BrowserNavigateTool(runtime),
        tmp_path,
        {"tab_id": 42, "url": "https://docs.example/guide"},
    )
    assert ordinary.ok, ordinary.error
    assert _gate_wire(runtime, P.METHOD_NAVIGATE)[-1]["url"] == "https://docs.example/guide"
