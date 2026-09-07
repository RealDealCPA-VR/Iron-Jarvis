"""The browser ledger (v1.237.0, Ship 3, D24 / plan §8.1 and §10.4).

D24's decision is that **the existing Jarvis ledger is the authoritative
browser action log** — no second logging subsystem, no new table. This file is
what makes that claim true rather than aspirational, and it pins four things
that each have a named silent failure behind them.

1. **``risk_class`` rides ``tool.executed`` and ``tool.denied``.** Ship 1
   (v1.235.0) landed ``Tool.risk_class`` deliberately inert and said so; this
   is the ship that starts reading it. It is read for LOGGING ONLY: the pins
   below drive a deny-floor browser tool that declares itself ``READ`` and
   assert it is still refused, because an attribute that could lower a verdict
   would be a way for a dynamically created tool to declare its way down off
   ``DENY_FLOOR_TOOLS``.

2. **Exactly ONE row per call, on every path.** Success, the tool's own
   refusal, a permission denial, a malformed call, a deadline, a cancellation
   and an invented tool name. The v1.228.0 rule is the reason: a cancelled
   ``await`` does not cancel the worker thread and a deadline is a RESULT, so a
   browser action whose effect LANDED in the user's real browser while the row
   was never written is the worst outcome this ship can produce. Two rows would
   be almost as bad — an audit view that double-counts a click on "Confirm
   transfer" cannot be read at speed.

3. **The row is RECONSTRUCTABLE months later.** D24 asks for tab title, URL and
   target metadata; those reach the ledger through the result's ``output``,
   which ``_record`` persists (capped at 4,000 characters, head-first). The pin
   drives a long result and asserts the D24 header survives the cap.

4. **``browser_type``'s typed text is NEVER in ``args_json``** — in the success
   path and in *every* failure path. A redactor that only works when things go
   well is not a redactor: ``args_json`` is stored at rest, returned by session
   export and included in backups, and the failure paths are exactly the ones a
   support session reads.

WHAT THESE TESTS DRIVE, STATED PLAINLY. This file's subject is the REGISTRY,
so most pins use a stand-in tool: inheriting ``_BrowserTool``'s runtime, socket
and access gate would make a pin about the ledger fail for a sibling module's
reasons. Everything asserted around it is real — ``ToolRegistry.invoke``,
``ToolRegistry._record``, the real ``PermissionEngine`` with the real
``DENY_FLOOR_TOOLS``, the real ``ToolInvocation`` rows and the real event
payloads.

THE STAND-IN CARRIES NO COPY OF ANYTHING IT IS USED TO TEST. That rule is
written here because breaking it cost this file eight pins: the stand-in had a
private ``redact_args``, and replacing the shipped ``BrowserTypeTool.redact_args``
with ``return args`` left every "the typed text is absent when ..." test green.
The stand-in now delegates to the shipped method itself (see
``_ActingStandIn.redact_args``), the same mutation turns those pins red, and
two further pins drive the shipped ``BrowserTypeTool`` and ``BrowserClickTool``
end to end through the registry so the delegation cannot become the only thing
that is checked. ``test_the_real_acting_tools_declare_what_this_file_stands_
in_for`` guards the boundary itself: every registered acting tool must agree
with ``BASE_RISK``, ``browser_type`` must redact, and the stand-in must still
be delegating rather than re-implementing.
"""

from __future__ import annotations

import asyncio
import json
import types

import pytest
from sqlmodel import select

from iron_jarvis.browser.risk import BASE_RISK
from iron_jarvis.browser.tools import BrowserTypeTool
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.events import EventType
from iron_jarvis.core.models import PermissionMode, ToolInvocation
from iron_jarvis.tools.base import (
    Reversibility,
    RiskClass,
    Tool,
    ToolContext,
    ToolResult,
)
from iron_jarvis.tools.permissions import DENY_FLOOR_TOOLS
from iron_jarvis.tools.registry import risk_value

# =============================================================================
# Fixtures and stand-ins
# =============================================================================

#: The typed secret every redaction pin looks for. A single spelling, so a pin
#: that stops looking cannot be mistaken for a pin that stopped finding.
SECRET = "hunter2-not-in-the-ledger"

#: D24's reconstructable context, in the shape an acting tool's result carries.
TAB_TITLE = "Acme Bank — Transfers"
TAB_URL = "https://bank.example/transfers"
TARGET = {"element_id": "e17", "role": "button", "name": "Confirm transfer"}


def _ctx(platform, tmp_path, session_id: str = "s-browser", run_id: str = "r-browser"):
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    return ToolContext(
        workspace=workspace,
        session_id=session_id,
        agent_run_id=run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _rows(platform, tool: str | None = None) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = list(db.exec(select(ToolInvocation)))
    return [r for r in rows if tool is None or r.tool == tool]


def _events(platform, type_: str) -> list[dict]:
    return [e.payload for e in platform.event_bus.history if e.type == type_]


def _acting_output(note: str = "", tail: str = "") -> str:
    """The D24 header a stand-in's ``output`` leads with, plus any tail.

    HEAD-FIRST HERE, AND THAT IS NOT WHAT THE SHIPPED TOOLS DO. ``_record``
    caps ``output`` at 4,000 characters by keeping the HEAD; the real acting
    tools APPEND their page context (``_with_ledger_context``), so the claim
    "the D24 fields survive the cap" is not a claim this shape can make on
    their behalf. It is made against the shipped tools instead, by
    ``test_a_hostile_label_cannot_push_the_page_context_past_the_cap``, which
    is where it belongs. This helper's own pin below asserts only what it
    can: that ``_record`` caps by keeping the head.
    """
    head = (
        f"{note or 'clicked'} on {TAB_TITLE} ({TAB_URL}) — "
        f"{TARGET['element_id']} {TARGET['role']} \"{TARGET['name']}\""
    )
    return f"{head}\n{tail}" if tail else head


class _ActingStandIn(Tool):
    """The contract an acting browser tool must satisfy, and nothing more.

    Deliberately NOT a subclass of ``_BrowserTool``: this file's subject is the
    registry, and inheriting a runtime, a socket and an access gate would make
    every pin below depend on a sibling lane's file to fail for the right
    reason. What it copies exactly is what the ledger reads — the name, the
    ``risk_class``, ``redact_args`` (plan §8.5, verbatim) and a result carrying
    D24's fields.
    """

    description = "test double for an acting browser tool"
    input_schema = {
        "type": "object",
        "properties": {"target": {"type": "object"}, "text": {"type": "string"}},
        "required": ["target"],
    }
    #: Borrow an allow-by-default key so the PERMISSION engine is not the
    #: subject of a pin about the LEDGER. The deny-floor pins below use the real
    #: key on purpose.
    permission_key = "read_file"
    reversibility = Reversibility.IRREVERSIBLE
    risk_class = RiskClass.PAGE_ACTION

    def __init__(
        self,
        name: str = "browser_click",
        *,
        result: ToolResult | None = None,
        hang: bool = False,
        raises: BaseException | None = None,
        risk_class: object = RiskClass.PAGE_ACTION,
    ) -> None:
        self.name = name
        self.risk_class = risk_class  # type: ignore[assignment]
        self._result = result or ToolResult(
            ok=True,
            output=_acting_output(),
            data={"tab_id": 7, "url": TAB_URL, "title": TAB_TITLE, "target": TARGET},
        )
        self._hang = hang
        self._raises = raises
        self.calls = 0

    #: THE SHIPPED REDACTOR ITSELF, not a copy of it. This was a private
    #: reimplementation, and a mutation proved what that costs: replacing
    #: ``BrowserTypeTool.redact_args``'s body with ``return args`` left all
    #: eight "the typed text is absent when ..." pins below GREEN, because they
    #: were redacting through this file's own copy. Delegating means the eight
    #: paths they exercise - success, refusal, denial, malformed call, raise,
    #: deadline, cancel, event payload - defend the code that ships.
    #:
    #: Unbound on purpose: the real method reads ``args`` and nothing else, so
    #: it needs none of ``_BrowserTool``'s runtime, socket or access gate, and
    #: inheriting those would make every pin here depend on a sibling module to
    #: fail for the right reason.
    redact_args = BrowserTypeTool.redact_args

    async def execute(self, args, ctx):  # noqa: D102
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        if self._hang:
            await asyncio.sleep(3600)
        return self._result


async def _invoke(platform, tool: Tool, args: dict, ctx, **kw) -> ToolResult:
    platform.registry.register(tool)
    return await platform.registry.invoke(
        tool.name, args, ctx, platform.permissions, **kw
    )


# =============================================================================
# 1. risk_class rides the verdict events — and only rides them
# =============================================================================


async def test_a_successful_browser_call_publishes_its_risk_class(platform, tmp_path):
    """The ledger's counterpart in the live feed. Without this, an audit view
    can see THAT a browser tool ran and not what class of action it was — a
    scroll and a click on "Delete account" read identically."""
    tool = _ActingStandIn("browser_click")
    res = await _invoke(platform, tool, {"target": TARGET}, _ctx(platform, tmp_path))

    assert res.ok is True
    payload = _events(platform, EventType.TOOL_EXECUTED)[-1]
    assert payload["tool"] == "browser_click"
    assert payload["risk_class"] == "page_action"
    # The precedent it follows is still there — this is additive, not a swap.
    assert payload["reversibility"] == "irreversible"


async def test_a_denied_browser_call_publishes_its_risk_class_too(platform, tmp_path):
    """A REFUSED page action is the row an incident review reads first: it says
    the model tried. ``tool.denied`` carrying no class would leave the reviewer
    unable to tell an attempted read from an attempted transfer."""
    tool = _ActingStandIn("browser_type")
    tool.risk_class = RiskClass.PAGE_ACTION
    res = await _invoke(
        platform,
        tool,
        {"target": TARGET, "text": SECRET},
        _ctx(platform, tmp_path),
        deny_reason="`browser_type` is not one of this agent's tools",
        deny_label="not armed",
    )

    assert res.ok is False
    payload = _events(platform, EventType.TOOL_DENIED)[-1]
    assert payload["tool"] == "browser_type"
    assert payload["risk_class"] == "page_action"
    assert payload["kind"] == "not armed"


async def test_an_invented_tool_name_reports_no_risk_class_rather_than_a_phantom(
    platform, tmp_path
):
    """A name the model made up has no declaration to report. ``None`` — the
    same answer ``reversibility`` gives on that path — because inventing
    ``external_commit`` for a call that never existed would put a phantom
    high-risk row in the audit view of a run that did nothing."""
    res = await platform.registry.invoke(
        "browser_clik", {"target": TARGET}, _ctx(platform, tmp_path),
        platform.permissions,
    )

    assert res.ok is False
    payload = _events(platform, EventType.TOOL_EXECUTED)[-1]
    assert payload["tool"] == "browser_clik"
    assert payload["risk_class"] is None
    assert payload["reversibility"] is None


async def test_a_tool_declaring_a_bare_string_does_not_break_the_publish_path(
    platform, tmp_path
):
    """``risk_class = "page_action"`` is what a hand-written or dynamically
    built tool will do, and ``.value`` on a ``str`` raises — from inside the
    publish path of EVERY tool call, turning one malformed tool into a
    daemon-wide outage. The declaration is reported as written."""
    tool = _ActingStandIn("browser_press_key", risk_class="page_action")
    res = await _invoke(platform, tool, {"target": TARGET}, _ctx(platform, tmp_path))

    assert res.ok is True
    assert _events(platform, EventType.TOOL_EXECUTED)[-1]["risk_class"] == "page_action"


async def test_an_undeclared_tool_is_logged_as_the_strictest_class(platform, tmp_path):
    """Fail-safe, in the direction that matters. A row reading ``read`` for a
    call that changed a page is the one wrong answer an audit log must not
    give, because it is wrong in the reassuring direction."""
    tool = _ActingStandIn("browser_navigate", risk_class=None)
    await _invoke(platform, tool, {"target": TARGET}, _ctx(platform, tmp_path))

    assert (
        _events(platform, EventType.TOOL_EXECUTED)[-1]["risk_class"]
        == "external_commit"
    )
    assert risk_value(tool) == "external_commit"


def test_risk_value_never_raises_on_a_broken_declaration():
    """The helper's whole job, asserted directly: every shape a tool can
    declare produces a string (or ``None`` for no tool), never an exception."""
    assert risk_value(None) is None
    assert risk_value(_ActingStandIn(risk_class=RiskClass.READ)) == "read"
    assert risk_value(_ActingStandIn(risk_class="local_ui")) == "local_ui"
    assert risk_value(_ActingStandIn(risk_class="")) == "external_commit"
    # A tool with NO ``risk_class`` attribute at all - an MCP or custom tool
    # built without subclassing ``Tool``, or a later refactor that drops the
    # class-level default. This is the ``getattr`` fail-safe, and until this
    # line it was unreachable: every branch above sets the attribute, so
    # mutating the default to ``RiskClass.READ`` left the suite green and the
    # docstring's second bullet described a protection nothing held up.
    assert risk_value(types.SimpleNamespace()) == "external_commit"


async def test_risk_class_never_lowers_a_verdict_on_the_deny_floor(platform, tmp_path):
    """THE CLAIM THIS SHIP MUST NOT BREAK. ``risk_class`` is read for logging;
    it is never compared against a ``PermissionMode``. A deny-floor browser tool
    that declares itself a harmless READ, with an agent definition trying to
    raise it to ``allow``, is still refused — and the refusal event carries the
    class it claimed, which is precisely the evidence an auditor wants."""
    assert "browser_click" in DENY_FLOOR_TOOLS

    tool = _ActingStandIn("browser_click", risk_class=RiskClass.READ)
    tool.permission_key = "browser_click"  # the REAL key, not the borrowed one
    res = await _invoke(
        platform,
        tool,
        {"target": TARGET},
        _ctx(platform, tmp_path),
        agent_overrides={"browser_click": "allow"},
    )

    assert res.ok is False
    assert tool.calls == 0  # it never reached the page
    payload = _events(platform, EventType.TOOL_DENIED)[-1]
    assert payload["tool"] == "browser_click"
    assert payload["risk_class"] == "read"      # what it claimed
    assert payload["mode"] == PermissionMode.ASK.value  # what it got


async def test_a_session_grant_still_lifts_the_ask_and_the_class_is_reported(
    platform, tmp_path
):
    """The other half of the floor, and the reason "Allow for this
    conversation" works on a browser ask: the floor blocks an agent DEFINITION
    from raising the tool, and an explicit per-session grant still runs it."""
    tool = _ActingStandIn("browser_click")
    tool.permission_key = "browser_click"
    res = await _invoke(
        platform,
        tool,
        {"target": TARGET},
        _ctx(platform, tmp_path),
        session_allow=["browser_click"],
    )

    assert res.ok is True
    assert tool.calls == 1
    assert _events(platform, EventType.TOOL_EXECUTED)[-1]["risk_class"] == "page_action"


# =============================================================================
# 2. Exactly one row per call — on every path
# =============================================================================


async def test_a_successful_action_writes_exactly_one_row(platform, tmp_path):
    tool = _ActingStandIn("browser_click")
    await _invoke(platform, tool, {"target": TARGET}, _ctx(platform, tmp_path))

    rows = _rows(platform, "browser_click")
    assert len(rows) == 1
    assert rows[0].ok is True
    assert rows[0].verdict == PermissionMode.ALLOW


async def test_a_tools_own_refusal_writes_exactly_one_row(platform, tmp_path):
    """``READ_ONLY_MODE`` — the access gate refusing a page action while the
    user has the browser on read-only. The model was answered; the ledger must
    say the attempt happened."""
    refusal = ToolResult(
        ok=False,
        error="READ_ONLY_MODE: your browser is set to read-only",
        output="READ_ONLY_MODE: your browser is set to read-only",
        data={"code": "READ_ONLY_MODE"},
    )
    tool = _ActingStandIn("browser_click", result=refusal)
    res = await _invoke(platform, tool, {"target": TARGET}, _ctx(platform, tmp_path))

    assert res.ok is False
    rows = _rows(platform, "browser_click")
    assert len(rows) == 1
    assert rows[0].ok is False
    assert "READ_ONLY_MODE" in rows[0].output
    assert len(_events(platform, EventType.TOOL_EXECUTED)) == 1


async def test_a_permission_denial_writes_exactly_one_row(platform, tmp_path):
    tool = _ActingStandIn("browser_navigate")
    tool.permission_key = "browser_navigate"
    await _invoke(
        platform,
        tool,
        {"target": TARGET},
        _ctx(platform, tmp_path),
        agent_overrides={"browser_navigate": "allow"},
    )

    rows = _rows(platform, "browser_navigate")
    assert len(rows) == 1
    assert rows[0].ok is False
    assert len(_events(platform, EventType.TOOL_DENIED)) == 1
    assert not _events(platform, EventType.TOOL_EXECUTED)  # denied, not executed


async def test_a_malformed_call_writes_exactly_one_row(platform, tmp_path):
    """The v1.228.0 shape check runs BEFORE ``execute``. One row, and the tool
    never touched the page."""
    tool = _ActingStandIn("browser_click")
    res = await _invoke(platform, tool, {"text": "no target"}, _ctx(platform, tmp_path))

    assert res.ok is False
    assert "missing required" in (res.error or "")
    assert tool.calls == 0
    assert len(_rows(platform, "browser_click")) == 1


async def test_a_deadline_writes_exactly_one_row_and_names_itself(platform, tmp_path):
    """A deadline is a RESULT (v1.228.0). The command may still be in flight in
    the user's browser, so the row is the only record that the attempt was
    made."""
    tool = _ActingStandIn("browser_click", hang=True)
    res = await _invoke(
        platform, tool, {"target": TARGET}, _ctx(platform, tmp_path), deadline_s=0.01
    )

    assert res.ok is False
    assert "did not finish" in (res.error or "")
    rows = _rows(platform, "browser_click")
    assert len(rows) == 1
    assert rows[0].ok is False
    assert "did not finish" in rows[0].output
    assert len(_events(platform, EventType.TOOL_EXECUTED)) == 1


async def test_a_raising_tool_writes_exactly_one_row(platform, tmp_path):
    tool = _ActingStandIn("browser_click", raises=RuntimeError("socket died"))
    res = await _invoke(platform, tool, {"target": TARGET}, _ctx(platform, tmp_path))

    assert res.ok is False
    rows = _rows(platform, "browser_click")
    assert len(rows) == 1
    assert "socket died" in rows[0].output


async def test_a_cancelled_action_still_writes_exactly_one_row(platform, tmp_path):
    """THE WORST OUTCOME THIS SHIP CAN PRODUCE, pinned. The user presses Cancel
    (or the chat client goes away) while a click is in flight: the ``await`` is
    cancelled, the browser may well perform the click anyway, and without this
    row the ledger would say nothing ran while the user's account changed.

    Ordering, never wall-clock: the cancel is delivered after ``execute`` has
    demonstrably been entered, and the row is awaited on the registry's own
    interrupt task rather than on a sleep."""
    entered = asyncio.Event()

    class _Blocking(_ActingStandIn):
        async def execute(self, args, ctx):  # noqa: D102
            self.calls += 1
            entered.set()
            await asyncio.sleep(3600)

    tool = _Blocking("browser_click")
    platform.registry.register(tool)
    ctx = _ctx(platform, tmp_path)

    task = asyncio.ensure_future(
        platform.registry.invoke(
            "browser_click", {"target": TARGET, "text": SECRET}, ctx,
            platform.permissions,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # The registry files the row on an INDEPENDENT task (v1.228.0, CL1) so the
    # cancel scope cannot strip it; wait for that task, not for a duration.
    for job in list(platform.registry._interrupt_jobs):
        await asyncio.gather(job, return_exceptions=True)

    rows = _rows(platform, "browser_click")
    assert len(rows) == 1
    assert rows[0].ok is False
    assert "its effect may have landed" in rows[0].output
    payload = _events(platform, EventType.TOOL_EXECUTED)[-1]
    assert payload["interrupted"] is True
    assert payload["risk_class"] == "page_action"


async def test_a_cancel_after_execute_returns_cannot_erase_the_row_or_the_event(
    platform, tmp_path
):
    """THE OTHER CANCEL WINDOW — the one where the click has ALREADY HAPPENED.

    The pin above covers a cancel delivered *during* ``execute``. This one is
    delivered *after* it returns, while the ledger write is in flight, and it is
    the worse of the two: the tool has been to the browser, the click landed in
    the user's real, logged-in account, and the only thing left is the record of
    it. Before v1.237.0 that record was written by a bare
    ``await asyncio.to_thread(self._record, ...)`` followed by a bare
    ``await ...publish(...)``, both outside the ``except CancelledError`` guard,
    so a cancel arriving in that window took BOTH: measured on a contended
    executor, ``effect landed: 1  ledger rows: 0  events: 0``.

    Deterministic, and not a wall-clock test in any part:

    * the loop's default executor is a single worker and that worker is
      OCCUPIED, so the ``_record`` job is queued and not yet running — which is
      what makes the concurrent future cancellable, and is an ordinary state for
      the daemon under load (DB writes, the notifier, grounding and run-record
      writes all offload this way);
    * the cancel is scheduled with ``call_soon`` from inside ``execute``, which
      returns without awaiting, so ``invoke`` runs straight on to the ledger
      write and is parked exactly there when the cancellation is delivered.
    """
    import concurrent.futures
    import threading

    loop = asyncio.get_running_loop()
    previous = getattr(loop, "_default_executor", None)
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(pool)
    gate = threading.Event()
    occupied = loop.run_in_executor(None, gate.wait)  # the one worker is taken

    class _CancelAfterReturn(_ActingStandIn):
        async def execute(self, args, ctx):  # noqa: D102
            self.calls += 1
            asyncio.get_running_loop().call_soon(asyncio.current_task().cancel)
            return self._result  # the click has landed in the real browser

    try:
        tool = _CancelAfterReturn("browser_click")
        platform.registry.register(tool)
        ctx = _ctx(platform, tmp_path)

        task = asyncio.ensure_future(
            platform.registry.invoke(
                "browser_click", {"target": TARGET}, ctx, platform.permissions
            )
        )
        with pytest.raises(asyncio.CancelledError):
            await task
        assert tool.calls == 1, "the effect never landed; this pin proves nothing"

        # Whatever ledger work is still in flight is waited FOR, not slept on.
        jobs = list(platform.registry._interrupt_jobs)
        gate.set()
        await occupied
        await asyncio.gather(*jobs, return_exceptions=True)
    finally:
        gate.set()
        # Restored by attribute: `set_default_executor` refuses `None`, and the
        # loop's default is created lazily, so `None` is the normal prior value.
        loop._default_executor = previous
        pool.shutdown(wait=False)

    rows = _rows(platform, "browser_click")
    assert len(rows) == 1, (
        "the click landed in the user's browser and the ledger has no row for it"
    )
    assert rows[0].ok is True
    assert TAB_URL in rows[0].output  # D24 context survived the detour
    executed = _events(platform, EventType.TOOL_EXECUTED)
    assert len(executed) == 1, "the row survived but tool.executed did not"
    assert executed[-1]["tool"] == "browser_click"
    assert executed[-1]["risk_class"] == "page_action"
    assert executed[-1]["invocation_id"] == rows[0].id
    # It finished; it was not interrupted mid-execute. A row that claimed
    # otherwise would tell an incident review the effect was uncertain.
    assert "interrupted" not in executed[-1]
    # ...and the mechanism that made it survive: an independent task, the same
    # answer v1.228.0 gave the during-execute window.
    assert jobs, "the terminal ledger write was not re-filed onto its own task"


async def test_an_invented_browser_name_still_writes_exactly_one_row(
    platform, tmp_path
):
    await platform.registry.invoke(
        "browser_clik", {"target": TARGET}, _ctx(platform, tmp_path),
        platform.permissions,
    )
    assert len(_rows(platform, "browser_clik")) == 1


async def test_eight_calls_write_eight_rows_and_no_more(platform, tmp_path):
    """The aggregate a reviewer actually checks: drive every one of Ship 3's
    acting tool names once and count. A path that wrote twice (or not at all)
    is invisible per-test and obvious here."""
    ctx = _ctx(platform, tmp_path)
    acting = [n for n, r in BASE_RISK.items() if r is not RiskClass.READ]
    assert len(acting) == 8

    for name in acting:
        await _invoke(platform, _ActingStandIn(name), {"target": TARGET}, ctx)

    rows = _rows(platform)
    assert len(rows) == 8
    assert sorted(r.tool for r in rows) == sorted(acting)


# =============================================================================
# 3. The row is reconstructable months later (D24)
# =============================================================================


async def test_the_row_carries_the_tab_title_url_and_target(platform, tmp_path):
    """D24's list, in one assertion each. ``output`` is where a browser tool's
    page context lands, and ``ToolInvocation`` is the authoritative log."""
    tool = _ActingStandIn("browser_click")
    await _invoke(platform, tool, {"target": TARGET}, _ctx(platform, tmp_path))

    row = _rows(platform, "browser_click")[0]
    assert TAB_TITLE in row.output
    assert TAB_URL in row.output
    assert TARGET["element_id"] in row.output
    assert TARGET["role"] in row.output
    assert TARGET["name"] in row.output
    # The rest of D24's list is columns, not text.
    assert row.session_id == "s-browser"
    assert row.agent_run_id == "r-browser"
    assert row.created_at is not None
    assert row.reversibility == "irreversible"


async def test_the_page_context_survives_the_four_thousand_character_cap(
    platform, tmp_path
):
    """``_record`` caps ``output`` at 4,000 characters by keeping the HEAD.

    Asserted on a stand-in because that is all a stand-in can say. What the
    SHIPPED tools do with the cap - they append, and are bounded so they never
    reach it - is pinned against them in
    ``test_a_hostile_label_cannot_push_the_page_context_past_the_cap``."""
    tool = _ActingStandIn(
        "browser_click",
        result=ToolResult(ok=True, output=_acting_output(tail="x" * 20_000)),
    )
    await _invoke(platform, tool, {"target": TARGET}, _ctx(platform, tmp_path))

    row = _rows(platform, "browser_click")[0]
    assert len(row.output) <= 4000
    assert TAB_URL in row.output
    assert TARGET["name"] in row.output


# =============================================================================
# 4. The typed text is never in args_json — on EVERY path
# =============================================================================


def _args_json_of(platform, tool_name: str = "browser_type") -> str:
    rows = _rows(platform, tool_name)
    assert rows, f"no ledger row for {tool_name}"
    return rows[-1].args_json or ""


def _assert_redacted(platform, tool_name: str = "browser_type") -> None:
    raw = _args_json_of(platform, tool_name)
    assert SECRET not in raw
    assert "***REDACTED***" in raw
    # The rest of the call must survive: a redactor that ate the target would
    # make the row unreconstructable, which is the failure D24 forbids.
    assert TARGET["element_id"] in raw
    assert json.loads(raw)["text"] == "***REDACTED***"


async def test_the_typed_text_is_absent_on_the_success_path(platform, tmp_path):
    tool = _ActingStandIn("browser_type")
    res = await _invoke(
        platform, tool, {"target": TARGET, "text": SECRET}, _ctx(platform, tmp_path)
    )
    assert res.ok is True
    _assert_redacted(platform)


async def test_the_typed_text_is_absent_when_the_tool_refuses(platform, tmp_path):
    tool = _ActingStandIn(
        "browser_type",
        result=ToolResult(
            ok=False,
            error="STALE_ELEMENT: the page changed after the previous snapshot",
            output="STALE_ELEMENT: the page changed after the previous snapshot",
        ),
    )
    await _invoke(
        platform, tool, {"target": TARGET, "text": SECRET}, _ctx(platform, tmp_path)
    )
    _assert_redacted(platform)


async def test_the_typed_text_is_absent_when_permission_is_denied(platform, tmp_path):
    """The path a support session reads most: the model tried to type a
    password into a page it was not allowed to touch."""
    tool = _ActingStandIn("browser_type")
    tool.permission_key = "browser_type"
    await _invoke(
        platform,
        tool,
        {"target": TARGET, "text": SECRET},
        _ctx(platform, tmp_path),
        agent_overrides={"browser_type": "allow"},
    )
    assert _rows(platform, "browser_type")[-1].ok is False
    _assert_redacted(platform)


async def test_the_typed_text_is_absent_when_the_call_is_malformed(platform, tmp_path):
    """The shape check records ``args`` before ``execute`` ever runs — and a
    call missing its target still carries the text."""
    tool = _ActingStandIn("browser_type")
    res = await _invoke(
        platform, tool, {"text": SECRET}, _ctx(platform, tmp_path)
    )
    assert res.ok is False
    raw = _args_json_of(platform)
    assert SECRET not in raw
    assert "***REDACTED***" in raw


async def test_the_typed_text_is_absent_when_the_tool_raises(platform, tmp_path):
    tool = _ActingStandIn("browser_type", raises=RuntimeError("socket died"))
    await _invoke(
        platform, tool, {"target": TARGET, "text": SECRET}, _ctx(platform, tmp_path)
    )
    _assert_redacted(platform)


async def test_the_typed_text_is_absent_when_the_deadline_expires(platform, tmp_path):
    tool = _ActingStandIn("browser_type", hang=True)
    await _invoke(
        platform,
        tool,
        {"target": TARGET, "text": SECRET},
        _ctx(platform, tmp_path),
        deadline_s=0.01,
    )
    _assert_redacted(platform)


async def test_the_typed_text_is_absent_when_the_call_is_cancelled(platform, tmp_path):
    """The cancellation row is written from a task created inside the ``except
    CancelledError`` branch, which is a second place ``args`` could have been
    persisted raw. It goes through the same ``_record``, so it redacts."""
    entered = asyncio.Event()

    class _Blocking(_ActingStandIn):
        async def execute(self, args, ctx):  # noqa: D102
            entered.set()
            await asyncio.sleep(3600)

    platform.registry.register(_Blocking("browser_type"))
    ctx = _ctx(platform, tmp_path)
    task = asyncio.ensure_future(
        platform.registry.invoke(
            "browser_type", {"target": TARGET, "text": SECRET}, ctx,
            platform.permissions,
        )
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for job in list(platform.registry._interrupt_jobs):
        await asyncio.gather(job, return_exceptions=True)

    _assert_redacted(platform)


async def test_the_typed_text_is_absent_from_the_event_payload_too(platform, tmp_path):
    """``tool.executed`` is persisted as an ``EventRecord`` and streamed to
    every open dashboard window. The registry never puts ``args`` on it — pinned
    so a later author adding "helpful" context cannot leak the field the ledger
    is careful about."""
    tool = _ActingStandIn("browser_type")
    await _invoke(
        platform, tool, {"target": TARGET, "text": SECRET}, _ctx(platform, tmp_path)
    )
    for payload in _events(platform, EventType.TOOL_EXECUTED):
        assert SECRET not in json.dumps(payload, default=str)


# =============================================================================
# 5. The gap to the real tools, closed the moment they land
# =============================================================================


async def test_a_real_click_writes_a_row_that_names_the_tab_title_and_url(
    platform, tmp_path
):
    """D24's list, against the SHIPPED ``BrowserClickTool`` — and the pin that
    caught this lane's one real defect.

    The tool puts ``tab_id``, ``url``, ``title`` and ``target`` in
    ``result.data`` (``_base_data``, which says in its own docstring that this
    is "so a ledger row read months later names the page"). ``ToolInvocation``
    has no column for ``data``: ``_record`` persists ``result.output`` and
    nothing else. Measured on the shipped code, the row for a real click reads
    in full::

        Clicked button "Sign in" in tab 7.

    — no title, no URL, no element id. Three months later that row cannot be
    tied to a page at all, which is precisely the reconstruction D24 exists to
    guarantee, and the gap is invisible from ``browser/tools.py``'s own tests
    because they assert ``data``.

    THE FIX BELONGS IN ``browser/tools.py`` (a sibling lane's file, so this
    lane reports rather than edits): each acting ``render`` appends the page
    context to its output line, e.g. ``f" Page: {title} ({url})."`` and the
    element id beside the role and name. Everything else here already works —
    the row, the redaction and the ``risk_class`` are all in place.
    """
    from iron_jarvis.browser.service import ActionTarget

    browser = platform.browser
    platform.config.browser_access = "interactive"
    plan = ActionTarget(
        tab={"id": 7, "title": TAB_TITLE, "url": TAB_URL},
        tab_id=7,
        snapshot_id="snap-1",
        target={"element_id": "e17"},
        element={"element_id": "e17", "role": "button", "name": "Sign in"},
        label="Sign in",
    )

    async def _prepare(tab_id=None, *args, **kw):
        return plan

    async def _command(method, params=None, *args, **kw):
        return {
            "clicked": {"element_id": "e17", "role": "button", "name": "Sign in"},
            "url": TAB_URL,
            "page_version": 3,
            "navigated": False,
        }

    browser.prepare_action = _prepare
    browser.command = _command

    res = await platform.registry.invoke(
        "browser_click", {"target": {"element_id": "e17"}},
        _ctx(platform, tmp_path), platform.permissions,
        session_allow=["browser_click"],
    )
    assert res.ok is True, res.error

    row = _rows(platform, "browser_click")[0]
    # What already works.
    assert "Sign in" in row.output
    assert "button" in row.output
    # D24's list, in full. These are the assertions that go red today.
    assert TAB_TITLE in row.output, (
        "the ledger row does not name the tab TITLE — D24 requires it and "
        f"`data` is not persisted; the row reads {row.output!r}"
    )
    assert TAB_URL in row.output, (
        "the ledger row does not name the URL — D24 requires it; the row reads "
        f"{row.output!r}"
    )
    assert "e17" in row.output, (
        "the ledger row does not name the element ID, so the action cannot be "
        f"tied to a snapshot row; the row reads {row.output!r}"
    )


def _drive_the_real_tool(platform, *, label: str, method_result: dict) -> list:
    """Point a shipped acting tool at ``label`` with a stub wire.

    Only the RESOLUTION and the TRANSPORT are stubbed. The access gate, the risk
    decision, the approval gate, ``redact_args``, ``render`` and
    ``_with_ledger_context`` are all the shipped code, which is the point: these
    pins are about what the LEDGER stores for a real call.
    """
    from iron_jarvis.browser.service import ActionTarget

    sent: list = []
    platform.config.browser_access = "interactive"
    plan = ActionTarget(
        tab={"id": 7, "title": TAB_TITLE, "url": TAB_URL},
        tab_id=7,
        snapshot_id="snap-1",
        target={"element_id": "e17"},
        element={"element_id": "e17", "role": "textbox", "name": label},
        label=label,
    )

    async def _prepare(tab_id=None, *args, **kw):
        return plan

    async def _command(method, params=None, *args, **kw):
        sent.append({"method": method, "params": params})
        return dict(method_result)

    platform.browser.prepare_action = _prepare
    platform.browser.command = _command
    return sent


async def test_the_shipped_type_tool_writes_a_row_that_has_no_typed_text(
    platform, tmp_path
):
    """THE REDACTION, AGAINST THE TOOL THAT SHIPS, through the real registry.

    The eight pins above now delegate to ``BrowserTypeTool.redact_args``, but
    they still call it from a stand-in. This one drives the shipped
    ``BrowserTypeTool`` end to end - registry, permission engine, access gate,
    ``_record`` - and reads the row out of SQLite. If the registry ever stopped
    calling ``redact_args`` at all, every delegating pin above would still be
    green and this one would not.
    """
    sent = _drive_the_real_tool(
        platform,
        label="Search",
        method_result={
            "typed_into": {"element_id": "e17", "role": "textbox", "name": "Search"},
            "url": TAB_URL,
            "page_version": 3,
            "cleared": False,
            "submitted": False,
        },
    )
    res = await platform.registry.invoke(
        "browser_type",
        {"target": {"element_id": "e17"}, "text": SECRET},
        _ctx(platform, tmp_path),
        platform.permissions,
        session_allow=["browser_type"],
    )
    assert res.ok is True, res.error
    assert len(sent) == 1
    # It really was typed: the wire carried the text, and only the wire.
    assert SECRET in json.dumps(sent[0]["params"], default=str)

    row = _rows(platform, "browser_type")[0]
    assert SECRET not in (row.args_json or "")
    assert json.loads(row.args_json)["text"] == "***REDACTED***"
    assert SECRET not in (row.output or "")
    # ...and the row is still reconstructable: redaction never ate the target.
    assert "e17" in (row.args_json or "")
    assert TAB_URL in row.output
    for payload in _events(platform, EventType.TOOL_EXECUTED):
        assert SECRET not in json.dumps(payload, default=str)


async def test_the_shipped_type_tools_refusal_row_has_no_typed_text_either(
    platform, tmp_path
):
    """The failure path, on the shipped tool. A support session reads the rows
    where things went wrong, and the registry redacts BEFORE ``execute`` runs -
    so the refusal carries the marker for the same reason the success does."""
    _drive_the_real_tool(
        platform,
        label="Search",
        method_result={"typed_into": {}, "url": TAB_URL, "page_version": 3},
    )
    platform.config.browser_access = "read_only"  # the access gate refuses

    res = await platform.registry.invoke(
        "browser_type",
        {"target": {"element_id": "e17"}, "text": SECRET},
        _ctx(platform, tmp_path),
        platform.permissions,
        session_allow=["browser_type"],
    )
    assert res.ok is False
    row = _rows(platform, "browser_type")[0]
    assert row.ok is False
    assert SECRET not in (row.args_json or "")
    assert json.loads(row.args_json)["text"] == "***REDACTED***"
    assert SECRET not in (row.output or "")


async def test_a_hostile_label_cannot_push_the_page_context_past_the_cap(
    platform, tmp_path
):
    """THE CAP, ASSERTED WHERE IT IS ACTUALLY DECIDED (the stand-in pin above
    cannot make this claim: the shipped tools APPEND their D24 fields).

    A page can name its own controls, so a button called with five thousand
    characters is a page's decision, not the model's. If that name reached the
    output whole, the appended ``Page: <title> (<url>)`` would fall off the far
    side of ``_record``'s 4,000-character head cap and the row would name the
    element and nothing else. It does not, because every page-authored string is
    bounded by ``_fence_label`` before it is printed.
    """
    hostile = "Sign in " + ("A" * 5000)
    sent = _drive_the_real_tool(
        platform,
        label=hostile,
        method_result={
            "clicked": {"element_id": "e17", "role": "button", "name": hostile},
            "url": TAB_URL,
            "page_version": 3,
            "navigated": False,
        },
    )
    res = await platform.registry.invoke(
        "browser_click",
        {"target": {"element_id": "e17"}},
        _ctx(platform, tmp_path),
        platform.permissions,
        session_allow=["browser_click"],
    )
    assert res.ok is True, res.error
    assert len(sent) == 1

    row = _rows(platform, "browser_click")[0]
    assert len(row.output) <= 4000
    assert TAB_TITLE in row.output, (
        "a page-authored name pushed the tab title out of the ledger row"
    )
    assert TAB_URL in row.output, (
        "a page-authored name pushed the URL out of the ledger row"
    )
    assert "e17" in row.output


def test_the_real_acting_tools_declare_what_this_file_stands_in_for(platform):
    """The stand-ins above pin the REGISTRY; this pins the sibling lane's tools
    as soon as they exist, and says so out loud rather than passing quietly.

    Any acting tool class present in ``browser/tools.py`` must agree with
    ``BASE_RISK`` (a class that disagrees is how a page-acting tool would be
    ledgered as a read) and ``browser_type`` must redact. A name that is not
    registered yet is skipped — a lane that has not landed is not a failure —
    and the pin becomes real, per tool, the instant it does.
    """
    registered = {
        name: platform.registry._tools.get(name)
        for name in BASE_RISK
    }
    live = {n: t for n, t in registered.items() if t is not None}
    assert live, "no browser tools registered at all — Ship 1 should have three"

    for name, tool in live.items():
        assert risk_value(tool) == BASE_RISK[name].value, (
            f"{name} declares {risk_value(tool)!r} but BASE_RISK says "
            f"{BASE_RISK[name].value!r} — the ledger would report the wrong class"
        )

    typer = live.get("browser_type")
    if typer is not None:
        red = typer.redact_args({"target": TARGET, "text": SECRET})
        assert red.get("text") == "***REDACTED***"
        assert SECRET not in json.dumps(red, default=str)
        # THE BRIDGE. The stand-in must not carry a private copy of the method
        # under test - that is exactly how eight redaction pins came to be
        # unable to fail. Identity, not behaviour: two implementations that
        # agree today can drift tomorrow, and the drift is invisible.
        assert (
            _ActingStandIn.redact_args is type(typer).redact_args
        ), (
            "the ledger stand-in has its own redactor again - the pins that "
            "use it would stop defending the shipped one"
        )
