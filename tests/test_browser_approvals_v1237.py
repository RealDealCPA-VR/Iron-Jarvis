"""Browser approvals ride the queue the user already knows (v1.237.0, Ship 3).

Plan §8.2's closing sentence is the whole design: ``ComputerUsePolicy.check``
and ``WebActionTool``'s approval branch are untouched, "so the existing
``ApprovalQueue`` consume-on-use path serves browser approvals unchanged, and
the approval card the user already knows renders browser asks with no new UI."

That sentence is a claim about behaviour, and this file is what makes it
checkable. It drives the REAL
:func:`~iron_jarvis.browser.risk.browser_risk_decision` (the one door — no
acting tool may call ``escalate_browser`` itself) and the REAL
:class:`~iron_jarvis.computeruse.approvals.ApprovalQueue`, and asserts the two
halves that matter on a page in someone's bank account:

* **An escalated action PAUSES.** Not "is flagged", not "logs a warning" — the
  frame is never sent. The stand-in below records every frame it would have put
  on the wire, and the pins count them. A gate that decides correctly and acts
  anyway is the failure mode this ship cannot afford, and it is invisible to any
  test that only asserts ``requires_approval``.
* **An approval is spent ONCE.** The easy half is that approving unblocks the
  next identical call. The hard half — the one that makes "approve once, act
  once" true rather than "approve once, act forever" — is that a SECOND
  identical action after that finds nothing to reuse and asks again. Without it,
  approving one transfer would silently approve every later identical transfer
  in the same run.

WHAT IS REAL AND WHAT IS A STAND-IN. ``browser/tools.py``'s eight acting tools
are a sibling lane's file and this lane may not edit them, so ``_ActingTool``
below implements the contract §8.2 and ``risk.py``'s own docstring specify for
every acting ``execute()``: one call to ``browser_risk_decision``, then the
queue, then the frame. Every decision, every reason and every queue transition
asserted here comes from the shipped modules; only the tool body is local. The
sibling lane's obligation, stated so a reviewer can trace it, is that each real
``execute()`` follows exactly this order — the risk decision BEFORE the send,
never beside it.
"""

from __future__ import annotations

import json
from datetime import timedelta, timezone

from sqlmodel import select

from iron_jarvis.browser.risk import (
    INJECTION_JUSTIFICATION_REASON,
    browser_risk_decision,
    risk_row,
)
from iron_jarvis.computeruse.approvals import APPROVAL_MAX_AGE_S, ApprovalQueue
from iron_jarvis.computeruse.base import Action, Selector
from iron_jarvis.computeruse.models import ApprovalRequest
from iron_jarvis.computeruse.policy import ComputerUsePolicy
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.tools.base import RiskClass, Tool, ToolContext, ToolResult

SECRET = "hunter2-not-in-the-ledger"
TAB_URL = "https://bank.example/transfers"
TAB_TITLE = "Acme Bank — Transfers"


def _ctx(platform, tmp_path, run_id: str = "run-a"):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=workspace,
        session_id="s-browser",
        agent_run_id=run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _requests(platform, status: str | None = None) -> list[ApprovalRequest]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(ApprovalRequest)))
    return [r for r in rows if status is None or r.status == status]


def _age_request(platform, request_id: str, seconds: float) -> None:
    """Move an approval's ``created_at`` back by ``seconds``, in the database.

    The expiry is a property of the ROW's age, so the honest way to test it is
    to age the row. Sleeping for fifteen minutes would be a wall-clock
    assertion; monkeypatching the clock would test the patch."""
    with session_scope(platform.engine) as db:
        req = db.get(ApprovalRequest, request_id)
        assert req is not None
        created = req.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        req.created_at = created - timedelta(seconds=seconds)
        db.add(req)
        db.commit()


def _rows(platform, tool: str | None = None) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation)))
    return [r for r in rows if tool is None or r.tool == tool]


class _ActingTool(Tool):
    """One acting browser tool, written to §8.2's contract and nothing else.

    ``sent`` is the wire. Every pin about pausing counts it, because "the
    decision said approval was required" and "the click did not happen" are
    different statements and only the second one protects an account.
    """

    description = "test double for an acting browser tool"
    input_schema = {
        "type": "object",
        "properties": {
            "target": {"type": "object"},
            "text": {"type": "string"},
        },
        "required": ["target"],
    }
    permission_key = "read_file"  # the PERMISSION engine is not this file's subject
    risk_class = RiskClass.PAGE_ACTION

    def __init__(
        self,
        name: str = "browser_click",
        *,
        approvals: ApprovalQueue,
        policy: ComputerUsePolicy | None = None,
        element: dict | None = None,
        page_flagged: bool = False,
        request_text: str = "",
    ) -> None:
        self.name = name
        self.approvals = approvals
        self.policy = policy or ComputerUsePolicy()
        self.element = element
        self.page_flagged = page_flagged
        self.request_text = request_text
        self.sent: list[dict] = []
        self.decisions: list = []

    @staticmethod
    def _action(name: str, label: str) -> Action:
        """The queue's identity for this call. ``approved_unconsumed`` matches on
        ``json.dumps(action.to_dict())``, so the same tool aimed at the same
        control is the same action — which is exactly the granularity "approve
        once, act once" needs."""
        return Action(kind="type" if name == "browser_type" else "click",
                      selector=Selector(role="button", name=label))

    def redact_args(self, args: dict) -> dict:
        if not args.get("text"):
            return args
        red = dict(args)
        red["text"] = "***REDACTED***"
        return red

    async def execute(self, args, ctx):  # noqa: D102
        label = str((args.get("target") or {}).get("name") or "")
        decision = browser_risk_decision(
            self.name,
            target_label=label,
            target_element=self.element,
            text=str(args.get("text") or ""),
            page_url=TAB_URL,
            page_flagged=self.page_flagged,
            request_text=self.request_text,
            policy=self.policy,
        )
        self.decisions.append(decision)

        if decision.requires_approval:
            action = self._action(self.name, label)
            prior = self.approvals.approved_unconsumed(ctx.agent_run_id, action)
            if prior is not None:
                # Consume-on-use: a human approved the PREVIOUS, pending call in
                # the dashboard; spend it here and proceed exactly once.
                self.approvals.consume(prior.id)
            else:
                req = self.approvals.create_request(
                    ctx.agent_run_id, action, decision.reason
                )
                return ToolResult(
                    ok=False,
                    error=(
                        f"approval required: {decision.reason}. "
                        f"Pending approval id={req.id}."
                    ),
                    output=(
                        f"approval required on {TAB_TITLE} ({TAB_URL}) — "
                        f"{decision.reason}"
                    ),
                    data={
                        "approval_id": req.id,
                        "status": "pending",
                        "risk": risk_row(self.name, decision),
                    },
                )

        self.sent.append({"method": self.name, "label": label})
        return ToolResult(
            ok=True,
            output=f"{self.name} on {TAB_TITLE} ({TAB_URL}) — \"{label}\"",
            data={
                "tab_id": 7,
                "url": TAB_URL,
                "title": TAB_TITLE,
                "target": dict(args.get("target") or {}),
                "risk": risk_row(self.name, decision),
            },
        )


async def _call(platform, tool: _ActingTool, args: dict, ctx) -> ToolResult:
    platform.registry.register(tool)
    return await platform.registry.invoke(
        tool.name, args, ctx, platform.permissions
    )


# =============================================================================
# 1. An ordinary action is not gated at all
# =============================================================================


async def test_an_ordinary_click_is_not_gated_and_reaches_the_page(
    platform, tmp_path
):
    """The decision record's own example: "Expand details" must stay
    unescalated. A gate that fires on everything teaches the user to approve
    without reading, which is worse than no gate."""
    tool = _ActingTool(approvals=ApprovalQueue(platform.engine))
    res = await _call(
        platform, tool, {"target": {"element_id": "e3", "name": "Expand details"}},
        _ctx(platform, tmp_path),
    )

    assert res.ok is True
    assert tool.sent == [{"method": "browser_click", "label": "Expand details"}]
    assert _requests(platform) == []
    # Field by field rather than dict equality: `risk_row` is a shared shape
    # that other lanes add to (it grew a `tool` key mid-ship), and a pin that
    # breaks on an addition teaches the next author to stop reading it.
    risk = res.data["risk"]
    assert risk["base"] == "page_action"
    assert risk["decision"] == "allowed"
    assert risk["reason"] == "allowed by policy"


# =============================================================================
# 2. An escalated action PAUSES — the frame is never sent
# =============================================================================


async def test_a_destructive_target_pauses_before_anything_is_sent(
    platform, tmp_path
):
    """The one that matters. "Delete account" escalates on the EXISTING
    destructive vocabulary, and the click does not happen: ``sent`` is empty,
    which is the only assertion that distinguishes a gate from a warning."""
    queue = ApprovalQueue(platform.engine)
    tool = _ActingTool(approvals=queue)
    res = await _call(
        platform, tool, {"target": {"element_id": "e9", "name": "Delete account"}},
        _ctx(platform, tmp_path),
    )

    assert res.ok is False
    assert tool.sent == []                      # nothing reached the browser
    pending = _requests(platform, "pending")
    assert len(pending) == 1
    assert "delete" in pending[0].reason.lower()
    assert res.data["status"] == "pending"
    assert res.data["approval_id"] == pending[0].id
    assert res.data["risk"]["decision"] == "approved"


async def test_a_payment_target_pauses_too(platform, tmp_path):
    tool = _ActingTool(approvals=ApprovalQueue(platform.engine))
    res = await _call(
        platform, tool, {"target": {"element_id": "e4", "name": "Submit payment"}},
        _ctx(platform, tmp_path),
    )

    assert res.ok is False
    assert tool.sent == []
    assert len(_requests(platform, "pending")) == 1


async def test_typing_into_a_field_the_page_marked_sensitive_pauses(
    platform, tmp_path
):
    """The password case, and the reason the row's ``sensitive`` flag exists:
    §9.4 nulls the value at capture time, so by the time the row reaches the
    risk decision the evidence ``classify`` would have used is already gone."""
    queue = ApprovalQueue(platform.engine)
    tool = _ActingTool(
        "browser_type",
        approvals=queue,
        element={"role": "textbox", "type": "password", "sensitive": True},
    )
    res = await _call(
        platform,
        tool,
        {"target": {"element_id": "e2", "name": "Password"}, "text": SECRET},
        _ctx(platform, tmp_path),
    )

    assert res.ok is False
    assert tool.sent == []
    assert len(_requests(platform, "pending")) == 1


async def test_a_flagged_page_pauses_an_off_request_target(platform, tmp_path):
    """Q03's action justification, end to end: after a read that tripped the
    injection detector, a state-changing call whose target the user never named
    asks — with the verbatim reason the user reads on the card."""
    tool = _ActingTool(
        approvals=ApprovalQueue(platform.engine),
        page_flagged=True,
        request_text="please click Sign in",
    )
    res = await _call(
        platform, tool, {"target": {"element_id": "e8", "name": "Export contacts"}},
        _ctx(platform, tmp_path),
    )

    assert res.ok is False
    assert tool.sent == []
    assert _requests(platform, "pending")[0].reason == INJECTION_JUSTIFICATION_REASON


async def test_a_flagged_page_still_lets_through_what_the_user_asked_for(
    platform, tmp_path
):
    """The other side of Q03, which is what keeps the check from being noise:
    the target the user NAMED still goes through on the same flagged page."""
    tool = _ActingTool(
        approvals=ApprovalQueue(platform.engine),
        page_flagged=True,
        request_text="please click Sign in",
    )
    res = await _call(
        platform, tool, {"target": {"element_id": "e1", "name": "Sign in"}},
        _ctx(platform, tmp_path),
    )

    assert res.ok is True
    assert tool.sent == [{"method": "browser_click", "label": "Sign in"}]
    assert _requests(platform) == []


# =============================================================================
# 3. Consume-on-use: approve once, act once
# =============================================================================


async def test_approving_in_the_dashboard_unblocks_the_next_identical_call(
    platform, tmp_path
):
    """The production path, with no resolver anywhere: the first call leaves a
    pending row, the human approves it on the card they already know, and the
    agent's NEXT identical call spends it and acts exactly once."""
    queue = ApprovalQueue(platform.engine)
    tool = _ActingTool(approvals=queue)
    ctx = _ctx(platform, tmp_path)
    args = {"target": {"element_id": "e9", "name": "Delete account"}}

    first = await _call(platform, tool, args, ctx)
    assert first.ok is False and tool.sent == []

    queue.approve(_requests(platform, "pending")[0].id)

    second = await _call(platform, tool, args, ctx)
    assert second.ok is True
    assert tool.sent == [{"method": "browser_click", "label": "Delete account"}]
    assert len(_requests(platform, "consumed")) == 1
    assert _requests(platform, "approved") == []


async def test_a_second_identical_action_does_not_reuse_the_first_approval(
    platform, tmp_path
):
    """THE HARD HALF. "Approve once, act once" is only true if the approval is
    SPENT. Delete the ``consume`` call and this is the pin that goes red: the
    third call below would delete the account a second time on the strength of
    an approval the user gave for the first one."""
    queue = ApprovalQueue(platform.engine)
    tool = _ActingTool(approvals=queue)
    ctx = _ctx(platform, tmp_path)
    args = {"target": {"element_id": "e9", "name": "Delete account"}}

    await _call(platform, tool, args, ctx)                       # 1: asks
    queue.approve(_requests(platform, "pending")[0].id)
    await _call(platform, tool, args, ctx)                       # 2: acts once
    assert len(tool.sent) == 1

    third = await _call(platform, tool, args, ctx)               # 3: asks again
    assert third.ok is False
    assert len(tool.sent) == 1                                   # still ONE action
    assert len(_requests(platform, "pending")) == 1
    assert len(_requests(platform, "consumed")) == 1


async def test_a_denied_request_never_becomes_permission(platform, tmp_path):
    """A refusal must not decay into an approval. ``approved_unconsumed`` only
    matches ``approved`` rows, so a denied ask leaves the next identical call
    exactly where it started: paused."""
    queue = ApprovalQueue(platform.engine)
    tool = _ActingTool(approvals=queue)
    ctx = _ctx(platform, tmp_path)
    args = {"target": {"element_id": "e9", "name": "Delete account"}}

    await _call(platform, tool, args, ctx)
    queue.deny(_requests(platform, "pending")[0].id)

    second = await _call(platform, tool, args, ctx)
    assert second.ok is False
    assert tool.sent == []
    assert len(_requests(platform, "denied")) == 1


async def test_an_approval_does_not_travel_to_a_different_target(
    platform, tmp_path
):
    """Approving "Delete account" is not approving "Transfer funds". The queue
    matches on the action signature, which is why the acting tools must build
    that signature from the RESOLVED element and not from a call counter."""
    queue = ApprovalQueue(platform.engine)
    tool = _ActingTool(approvals=queue)
    ctx = _ctx(platform, tmp_path)

    await _call(platform, tool, {"target": {"name": "Delete account"}}, ctx)
    queue.approve(_requests(platform, "pending")[0].id)

    other = await _call(platform, tool, {"target": {"name": "Send payment"}}, ctx)
    assert other.ok is False
    assert tool.sent == []
    assert len(_requests(platform, "approved")) == 1  # the first one, untouched


async def test_an_approval_does_not_travel_to_a_different_run(platform, tmp_path):
    """``approved_unconsumed`` is keyed by ``run_id``. A grant given while the
    user was watching one job must not silently arm the next one."""
    queue = ApprovalQueue(platform.engine)
    tool = _ActingTool(approvals=queue)
    args = {"target": {"name": "Delete account"}}

    await _call(platform, tool, args, _ctx(platform, tmp_path, run_id="run-a"))
    queue.approve(_requests(platform, "pending")[0].id)

    elsewhere = await _call(
        platform, tool, args, _ctx(platform, tmp_path, run_id="run-b")
    )
    assert elsewhere.ok is False
    assert tool.sent == []


# =============================================================================
# 4. The ask is ledgered, and it is redacted
# =============================================================================


async def test_the_pause_writes_exactly_one_ledger_row(platform, tmp_path):
    """An ask is an event in the account's history: the model tried. One row,
    ok=False, carrying the tab context and the reason — the same shape as any
    other outcome, through the same ``_record``."""
    tool = _ActingTool(approvals=ApprovalQueue(platform.engine))
    await _call(
        platform, tool, {"target": {"element_id": "e9", "name": "Delete account"}},
        _ctx(platform, tmp_path),
    )

    rows = _rows(platform, "browser_click")
    assert len(rows) == 1
    assert rows[0].ok is False
    assert TAB_URL in rows[0].output
    assert "delete" in rows[0].output.lower()


async def test_the_typed_text_is_absent_from_the_paused_calls_row(
    platform, tmp_path
):
    """The approval path is a FAILURE path, and a redactor that only works when
    things go well is not a redactor. A password typed at a page that asked for
    approval is the single most likely secret this ledger will ever see."""
    tool = _ActingTool(
        "browser_type",
        approvals=ApprovalQueue(platform.engine),
        element={"role": "textbox", "type": "password", "sensitive": True},
    )
    await _call(
        platform,
        tool,
        {"target": {"element_id": "e2", "name": "Password"}, "text": SECRET},
        _ctx(platform, tmp_path),
    )

    row = _rows(platform, "browser_type")[0]
    assert SECRET not in (row.args_json or "")
    assert json.loads(row.args_json)["text"] == "***REDACTED***"
    assert SECRET not in (row.output or "")
    assert SECRET not in (row.output or "")


async def test_the_approval_request_row_carries_no_typed_text(platform, tmp_path):
    """The queue persists the ACTION, and ``Action.value`` is where a typed
    string would live. The acting tools build the signature from the target
    only — a card that displayed the password to approve typing it would defeat
    every redaction upstream of it."""
    tool = _ActingTool(
        "browser_type",
        approvals=ApprovalQueue(platform.engine),
        element={"role": "textbox", "type": "password", "sensitive": True},
    )
    await _call(
        platform,
        tool,
        {"target": {"element_id": "e2", "name": "Password"}, "text": SECRET},
        _ctx(platform, tmp_path),
    )

    req = _requests(platform, "pending")[0]
    assert SECRET not in req.action_json
    assert SECRET not in req.reason


# =============================================================================
# 5. The same thing, against the SHIPPED tool
# =============================================================================


def _drive_the_real_click(platform, label: str, sent: list) -> None:
    """Point the shipped ``BrowserClickTool`` at ``label`` with a stub wire.

    Only the RESOLUTION and the TRANSPORT are stubbed — everything the ship is
    about (the access gate, ``browser_risk_decision``, ``_require_approval``,
    the real ``ApprovalQueue`` the platform built, the render) is the shipped
    code. ``sent`` is appended to by the stub, so "the frame was never put on
    the wire" is a fact about the real tool and not about a double.
    """
    from iron_jarvis.browser.service import ActionTarget

    platform.config.browser_access = "interactive"
    plan = ActionTarget(
        tab={"id": 7, "title": TAB_TITLE, "url": TAB_URL},
        tab_id=7,
        snapshot_id="snap-1",
        target={"element_id": "e9"},
        element={"element_id": "e9", "role": "button", "name": label},
        label=label,
    )

    async def _prepare(tab_id=None, *args, **kw):
        return plan

    async def _command(method, params=None, *args, **kw):
        sent.append({"method": method, "params": params})
        return {
            "clicked": {"element_id": "e9", "role": "button", "name": label},
            "url": TAB_URL,
            "page_version": 3,
            "navigated": False,
        }

    platform.browser.prepare_action = _prepare
    platform.browser.command = _command


async def test_the_shipped_click_tool_pauses_and_spends_its_approval_once(
    platform, tmp_path
):
    """Item 5 against the real thing: pause, approve, act ONCE, then ask again.

    The third call is the assertion that matters. If the shipped tool looked up
    a standing approval and forgot to consume it, calls two, three and four
    would all delete the account on the strength of one card the user answered
    for call one — and every other test in this file would still pass."""
    sent: list = []
    _drive_the_real_click(platform, "Delete account", sent)
    ctx = _ctx(platform, tmp_path)
    queue = platform.browser.approvals
    args = {"target": {"element_id": "e9"}}

    first = await platform.registry.invoke(
        "browser_click", args, ctx, platform.permissions,
        session_allow=["browser_click"],
    )
    assert first.ok is False
    assert sent == [], "the click reached the browser before anyone approved it"
    pending = _requests(platform, "pending")
    assert len(pending) == 1
    assert "delete" in pending[0].reason.lower()

    queue.approve(pending[0].id)
    second = await platform.registry.invoke(
        "browser_click", args, ctx, platform.permissions,
        session_allow=["browser_click"],
    )
    assert second.ok is True, second.error
    assert len(sent) == 1
    assert len(_requests(platform, "consumed")) == 1

    third = await platform.registry.invoke(
        "browser_click", args, ctx, platform.permissions,
        session_allow=["browser_click"],
    )
    assert third.ok is False
    assert len(sent) == 1, "a second identical action reused a spent approval"
    assert len(_requests(platform, "pending")) == 1


async def test_a_standing_approval_expires_instead_of_arming_a_later_conversation(
    platform, tmp_path
):
    """THE REPLAY THIS SHIP HAD, driven the way it actually happens.

    Chat builds EVERY ToolContext with ``agent_run_id="chat"`` - one constant
    for every conversation the user will ever have. So turn 1 asks to click
    "Delete account", the user approves the card, and the model never comes back
    for it (the turn ended, the user walked away). The row sat at
    ``status='approved'``. Days later, in a completely different conversation,
    the identical call found it through ``approved_unconsumed``, consumed it,
    and the click landed with the user shown nothing.

    The grant now EXPIRES (``APPROVAL_MAX_AGE_S``): the later conversation is
    asked again. Age, never duration - the row's ``created_at`` is moved back in
    the database, so nothing here waits for a clock.
    """
    sent: list = []
    _drive_the_real_click(platform, "Delete account", sent)
    queue = platform.browser.approvals
    args = {"target": {"element_id": "e9"}}
    # The run id chat really uses, in both conversations.
    first_conversation = _ctx(platform, tmp_path, run_id="chat")
    later_conversation = _ctx(platform, tmp_path, run_id="chat")

    first = await platform.registry.invoke(
        "browser_click", args, first_conversation, platform.permissions,
        session_allow=["browser_click"],
    )
    assert first.ok is False and sent == []
    approved = _requests(platform, "pending")[0]
    queue.approve(approved.id)                       # the user says yes, once

    _age_request(platform, approved.id, APPROVAL_MAX_AGE_S + 60)

    later = await platform.registry.invoke(
        "browser_click", args, later_conversation, platform.permissions,
        session_allow=["browser_click"],
    )
    assert later.ok is False, "a stale approval acted in a later conversation"
    assert sent == [], "the click reached the browser on a days-old approval"
    assert later.data["status"] == "pending"
    # The stale grant is IGNORED, not spent: it must not silently disappear
    # from the audit trail, and the user gets a fresh card to answer.
    assert [r.id for r in _requests(platform, "approved")] == [approved.id]
    assert len(_requests(platform, "pending")) == 1
    assert _requests(platform, "consumed") == []


async def test_an_approval_inside_the_window_is_still_spent_by_the_retry(
    platform, tmp_path
):
    """The other direction, so the expiry cannot be satisfied by a queue that
    simply stopped honouring approvals. A grant the user gave a minute ago -
    aged in the database, not waited for - still unblocks the identical retry
    exactly once."""
    sent: list = []
    _drive_the_real_click(platform, "Delete account", sent)
    queue = platform.browser.approvals
    ctx = _ctx(platform, tmp_path, run_id="chat")
    args = {"target": {"element_id": "e9"}}

    await platform.registry.invoke(
        "browser_click", args, ctx, platform.permissions,
        session_allow=["browser_click"],
    )
    approved = _requests(platform, "pending")[0]
    queue.approve(approved.id)
    _age_request(platform, approved.id, APPROVAL_MAX_AGE_S - 60)

    second = await platform.registry.invoke(
        "browser_click", args, ctx, platform.permissions,
        session_allow=["browser_click"],
    )
    assert second.ok is True, second.error
    assert len(sent) == 1
    assert len(_requests(platform, "consumed")) == 1


def test_the_expiry_is_measured_against_the_rows_own_timestamp(platform):
    """``approved_unconsumed`` directly, because the window is the whole
    protection and a boundary that is off by a timezone is a grant that lives
    for hours. Both sides of the bound, on the real queue."""
    queue = ApprovalQueue(platform.engine)
    action = Action(
        kind="click", selector=Selector(role="button", name="Delete account")
    )
    req = queue.create_request("chat", action, "delete")
    queue.approve(req.id)

    assert queue.approved_unconsumed("chat", action) is not None
    _age_request(platform, req.id, APPROVAL_MAX_AGE_S - 30)
    assert queue.approved_unconsumed("chat", action) is not None
    _age_request(platform, req.id, 60)  # now just over the bound
    assert queue.approved_unconsumed("chat", action) is None
    # A caller that has its own scope can still opt out - and nothing in the
    # daemon does, which is why the DEFAULT is the bound.
    assert queue.approved_unconsumed("chat", action, max_age_s=None) is not None


def test_the_approval_identity_round_trips_through_the_real_queue(platform):
    """THE SIGNATURE THAT ACTUALLY DECIDES, stored and matched for real.

    Consume-on-use matches on ``json.dumps(action.to_dict())`` in SQLite, and
    the object being dumped is the shipped ``_ActingTool._approval_action`` -
    role and name from the RESOLVED plan, the css and element id from the
    target, and a digest for typed text. Nothing else in this ship crosses the
    two: the pins above build a stand-in ``Action``, and the acting-tool tests
    match the real one against an in-memory double. If the real object did not
    round-trip, an approved "Delete account" would ask a second time (safe) or,
    worse, a DIFFERENT element would match one that was approved.
    """
    from iron_jarvis.browser.service import ActionTarget

    tool = platform.registry._tools["browser_click"]
    queue = ApprovalQueue(platform.engine)

    def _plan(element_id: str, label: str) -> ActionTarget:
        return ActionTarget(
            tab={"id": 7, "title": TAB_TITLE, "url": TAB_URL},
            tab_id=7,
            snapshot_id="snap-1",
            target={"element_id": element_id},
            element={"element_id": element_id, "role": "button", "name": label},
            label=label,
        )

    args = {"target": {"element_id": "e9"}}
    action = tool._approval_action(args, _plan("e9", "Delete account"))
    req = queue.create_request("chat", action, "delete")
    queue.approve(req.id)

    # The identical call rebuilds the identical signature - through SQLite.
    rebuilt = tool._approval_action(args, _plan("e9", "Delete account"))
    found = queue.approved_unconsumed("chat", rebuilt)
    assert found is not None and found.id == req.id

    # A different control does not match, and neither does a different label on
    # the same id: the card the user read named one of them.
    other = tool._approval_action(
        {"target": {"element_id": "e4"}}, _plan("e4", "Transfer funds")
    )
    assert queue.approved_unconsumed("chat", other) is None
    relabelled = tool._approval_action(args, _plan("e9", "Delete everything"))
    assert queue.approved_unconsumed("chat", relabelled) is None
    # And the row that decides carries no secret to decide it with.
    assert "Delete account" in req.action_json


async def test_the_shipped_click_tool_does_not_gate_an_ordinary_target(
    platform, tmp_path
):
    """The other direction, so the pin above cannot be satisfied by a tool that
    simply asks about everything."""
    sent: list = []
    _drive_the_real_click(platform, "Expand details", sent)

    res = await platform.registry.invoke(
        "browser_click", {"target": {"element_id": "e9"}},
        _ctx(platform, tmp_path), platform.permissions,
        session_allow=["browser_click"],
    )
    assert res.ok is True, res.error
    assert len(sent) == 1
    assert _requests(platform) == []


async def test_the_card_reason_is_the_risk_decisions_own_words(platform, tmp_path):
    """The user reads this sentence and nothing else. It comes from the shared
    vocabulary in ``computeruse/policy.py``, not from a string typed at the call
    site — the v1.228.0 "rate limited" lesson, applied to consent."""
    tool = _ActingTool(approvals=ApprovalQueue(platform.engine))
    await _call(
        platform, tool, {"target": {"element_id": "e9", "name": "Delete account"}},
        _ctx(platform, tmp_path),
    )

    assert _requests(platform, "pending")[0].reason == tool.decisions[-1].reason
    assert tool.decisions[-1].allowed is True  # it escalates; it never denies
