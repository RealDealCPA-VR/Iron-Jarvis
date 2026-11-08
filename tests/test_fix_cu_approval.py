"""Regression tests for the consume-on-use approval fix (resolver-less path).

Audit finding (MEDIUM): in the wired platform ``CUContext.approval_resolver`` is
``None`` (the platform builds it without a resolver). Approving a pending
``ApprovalRequest`` in the dashboard had ZERO effect — the agent's next
``web_action`` minted a fresh pending request and never matched the prior
approval, so sensitive actions could never complete through production.

The fix is consume-on-use: a dashboard approval of the FIRST (pending) call
unblocks the NEXT identical call; the approval is then marked ``consumed`` so it
cannot be replayed by a third identical call. These tests pin that behaviour and
confirm the injected-resolver (synchronous/test) path is unchanged.

Fully offline — FakeBrowser only; never launches a real browser.
"""

from __future__ import annotations

import pytest
from sqlmodel import select

import iron_jarvis.computeruse.models  # noqa: F401  (register tables before init_db)
from iron_jarvis.computeruse import (
    Action,
    ApprovalQueue,
    ComputerUsePolicy,
    CUContext,
    FakeBrowser,
    Selector,
    computeruse_tools,
)
from iron_jarvis.computeruse.models import ApprovalRequest
from iron_jarvis.core.db import init_db, make_engine, session_scope
from iron_jarvis.tools.base import ToolContext


# --------------------------------------------------------------------------- #
# Fixtures / helpers (mirrors tests/test_computeruse.py)
# --------------------------------------------------------------------------- #


@pytest.fixture
def engine(tmp_path):
    e = make_engine(str(tmp_path / "cu.db"))
    init_db(e)
    return e


@pytest.fixture
def approvals(engine):
    return ApprovalQueue(engine)


def make_pages() -> dict[str, dict]:
    return {
        "https://example.com/login": {
            "text": "Sign in to your account",
            "a11y": [{"role": "heading", "name": "Sign in"}],
            "fields": [
                {"selector": "#user", "type": "text", "name": "Username"},
                {"selector": "#password", "type": "password", "name": "Password"},
            ],
        },
    }


def make_policy(**kw) -> ComputerUsePolicy:
    base = dict(
        enabled=True,
        domain_allowlist=["example.com"],
        action_allowlist=[
            "navigate", "read", "extract", "screenshot", "wait", "type", "click"
        ],
        max_steps=20,
        max_retries=2,
    )
    base.update(kw)
    return ComputerUsePolicy(**base)


def make_ctx(engine, tmp_path, run="r") -> ToolContext:
    return ToolContext(
        workspace=tmp_path, session_id="s", agent_run_id=run,
        config=None, event_bus=None, engine=engine,
    )


def _all_approvals(engine) -> list[ApprovalRequest]:
    with session_scope(engine) as db:
        return list(db.exec(select(ApprovalRequest)))


# The exact, identical args the agent re-sends across model turns.
_PASSWORD_ACTION = {"kind": "type", "css": "#password", "value": "hunter2"}


# --------------------------------------------------------------------------- #
# Consume-on-use: dashboard approval unblocks the NEXT identical call
# --------------------------------------------------------------------------- #


async def test_resolverless_dashboard_approval_unblocks_next_call(
    approvals, engine, tmp_path
):
    """The resolver-less shape (a lane that cannot ask).

    Readiness 0j.5 item 18 (moved on purpose): the first call's row is DENIED,
    never left PENDING — a pending row on the Browser page could be approved
    there and spent by the next identical call with no card in front of the
    person who asked. The consume-on-use branch is unchanged for a row a
    human DID approve:

    1) First sensitive web_action is refused + its row is denied.
    2) A human approves an identical request (the dashboard queue).
    3) The SAME web_action now EXECUTES exactly once and the row is consumed.
    4) A THIRD identical call is refused again (no replay of the consumed grant).
    """
    cu = CUContext(
        policy=make_policy(),
        browser=FakeBrowser(make_pages()),
        approvals=approvals,
        approval_resolver=None,  # <-- production wiring: no synchronous resolver
    )
    tools = {t.name: t for t in computeruse_tools(cu)}
    ctx = make_ctx(engine, tmp_path)
    await cu.browser.navigate("https://example.com/login")

    # --- 1) first call: blocked, pending row created -----------------------
    res1 = await tools["web_action"].execute(dict(_PASSWORD_ACTION), ctx)
    assert res1.ok is False
    assert "approval required" in (res1.error or "").lower()
    rows = _all_approvals(engine)
    assert len(rows) == 1 and rows[0].status == "denied"
    assert "password" in rows[0].reason.lower()
    # The password was NOT typed (blocked before execution).
    assert cu.browser.typed == []

    # --- 2) a human approves an identical request in the dashboard ---------
    req_id = approvals.create_request(
        getattr(ctx, "agent_run_id", "ad-hoc"),
        Action(kind="type", selector=Selector(css="#password"), value="hunter2", fallback=False),
        rows[0].reason,
    ).id
    approvals.approve(req_id)

    # --- 3) the SAME call now executes exactly once + consumes the grant ---
    res2 = await tools["web_action"].execute(dict(_PASSWORD_ACTION), ctx)
    assert res2.ok is True
    assert len(cu.browser.typed) == 1
    assert cu.browser.typed[0]["type"] == "password"
    # No NEW request was minted; the approved row is now consumed (not replayable).
    assert len(_all_approvals(engine)) == 2
    assert approvals.get(req_id).status == "consumed"

    # --- 4) a third identical call is pending again (no replay) ------------
    res3 = await tools["web_action"].execute(dict(_PASSWORD_ACTION), ctx)
    assert res3.ok is False
    assert "approval required" in (res3.error or "").lower()
    # Still typed only once; the consumed approval did not let it through.
    assert len(cu.browser.typed) == 1
    rows3 = _all_approvals(engine)
    assert len(rows3) == 3  # the first (denied), the approved (consumed), a fresh one
    assert [r for r in rows3 if r.status == "pending"] == [], "nothing is left pending"
    assert sum(1 for r in rows3 if r.status == "denied") == 2


async def test_approved_unconsumed_matches_only_exact_action(approvals, engine):
    """The signature is the action JSON: a DIFFERENT action does not match."""
    a1 = Action(kind="type", selector=Selector(css="#password"), value="hunter2")
    a2 = Action(kind="type", selector=Selector(css="#password"), value="different")
    req = approvals.create_request("r", a1, "typing into a password field")
    approvals.approve(req.id)

    # Exact match found...
    assert approvals.approved_unconsumed("r", a1) is not None
    # ...but a different value / different run does not match.
    assert approvals.approved_unconsumed("r", a2) is None
    assert approvals.approved_unconsumed("other-run", a1) is None

    # After consuming, even the exact action no longer matches (no replay).
    approvals.consume(req.id)
    assert approvals.approved_unconsumed("r", a1) is None
    assert approvals.get(req.id).status == "consumed"


# --------------------------------------------------------------------------- #
# The injected-resolver (synchronous/test) path is unchanged
# --------------------------------------------------------------------------- #


async def test_injected_resolver_true_still_executes(approvals, engine, tmp_path):
    """resolver=True approves synchronously and the action runs in one call."""
    cu = CUContext(
        policy=make_policy(),
        browser=FakeBrowser(make_pages()),
        approvals=approvals,
        approval_resolver=lambda req: True,
    )
    tools = {t.name: t for t in computeruse_tools(cu)}
    ctx = make_ctx(engine, tmp_path)
    await cu.browser.navigate("https://example.com/login")

    res = await tools["web_action"].execute(dict(_PASSWORD_ACTION), ctx)
    assert res.ok is True
    assert len(cu.browser.typed) == 1
    rows = _all_approvals(engine)
    # v1.449.0 (B4): the Allow a resolver gives is SPENT by the click it
    # allowed, so a second identical click asks again (one Allow, one action).
    assert len(rows) == 1 and rows[0].status == "consumed"


async def test_injected_resolver_false_blocks_and_denies(approvals, engine, tmp_path):
    """resolver=False is fail-closed: the action is denied and never runs."""
    cu = CUContext(
        policy=make_policy(),
        browser=FakeBrowser(make_pages()),
        approvals=approvals,
        approval_resolver=lambda req: False,
    )
    tools = {t.name: t for t in computeruse_tools(cu)}
    ctx = make_ctx(engine, tmp_path)
    await cu.browser.navigate("https://example.com/login")

    res = await tools["web_action"].execute(dict(_PASSWORD_ACTION), ctx)
    assert res.ok is False
    assert "approval required" in (res.error or "").lower()
    assert cu.browser.typed == []
    rows = _all_approvals(engine)
    assert len(rows) == 1 and rows[0].status == "denied"
