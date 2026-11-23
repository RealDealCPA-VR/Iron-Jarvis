"""Leap 05-a (R1.3): an "Always for this rule" is kept on the RULE.

* a rule's run names its rule (by id, from the run's own options, never the
  model's words), so the strongest scope of an "Always" answer on it is the
  rule: ``("rule", <id>)``, ahead of its agent and its project;
* a grant kept on one rule is read on that rule's next fire and on no other
  rule, not even one with the same name;
* a deny floor tool still needs its exact arguments;
* the ask carries ``always_scope: "rule"`` to the bell and the run's page;
* removing the rule takes its grants with it; ``grant_revoke`` finds a
  rule's grant by the rule's name.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents.runtime import AgentRuntime
from iron_jarvis.core.approvals import ChatApprovals
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.grants import PLACED_ALWAYS_SCOPES, args_hash, pick_scope
from iron_jarvis.core.models import Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import DENY_FLOOR_TOOLS
from iron_jarvis.triggers import starts

pytestmark = [pytest.mark.usefixtures("no_network"), pytest.mark.timeout(300)]

ARGS = {"path": "C:/Intake/a.pdf", "content": "filed"}


@pytest.fixture()
def desk(tmp_path):
    c = TestClient(create_app(str(tmp_path / "home")))
    p = c.app.state.platform
    router = c.app.state.reflex_router
    router._launch = lambda coro, _sid: coro.close()
    yield c, p, router
    try:
        p.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass


def _fire(router, slug: str) -> Session:
    out = asyncio.run(starts.hub_for(router).signal("webhook", fields={"slug": slug, "body": "x"}))
    assert out and out[0]["ok"], out
    with session_scope(router.p.engine) as db:
        return db.get(Session, out[0]["session_id"])


def test_a_rule_run_keeps_its_always_on_the_rule_by_id(desk):
    c, p, router = desk
    rule = p.reflex.add(name="File it", source="webhook", match="a", action="session", task_template="File it")
    run = _fire(router, "a")
    scopes = AgentRuntime.grant_scopes(run)
    assert scopes == [("rule", rule.id)]
    assert pick_scope(scopes + [("agent", "custom:x"), ("project", "p1")]) == ("rule", rule.id)
    assert "rule" in PLACED_ALWAYS_SCOPES
    # A run that only CLAIMS a rule's origin, with no rule named by its door,
    # has no rule scope.
    bare = SimpleNamespace(origin="reflex:File it", options_json="{}", agent_name="", project_id="")
    assert AgentRuntime.grant_scopes(bare) == []
    spoof = SimpleNamespace(origin="rule:" + rule.id, options_json=json.dumps({"rule_id": rule.id}),
                            agent_name="", project_id="")
    assert AgentRuntime.grant_scopes(spoof) == [], "only the rule's own door names a rule"


def test_an_always_for_this_rule_is_read_on_its_next_fire_and_on_no_other_rule(desk):
    c, p, router = desk
    one = p.reflex.add(name="File it", source="webhook", match="a", action="session", task_template="x")
    two = p.reflex.add(name="File it", source="webhook", match="b", action="session", task_template="x")
    first = _fire(router, "a")
    (scope,) = AgentRuntime.grant_scopes(first)
    p.grants.create(scope[0], scope[1], "write_file", args_hash("write_file", ARGS), "write a.pdf")
    nxt = _fire(router, "a")
    assert p.grants.match(AgentRuntime.grant_scopes(nxt), "write_file", ARGS) is not None
    other = _fire(router, "b")
    assert AgentRuntime.grant_scopes(other) == [("rule", two.id)]
    assert p.grants.match(AgentRuntime.grant_scopes(other), "write_file", ARGS) is None, (
        "another rule with the same name is not covered"
    )
    assert p.grants.match(AgentRuntime.grant_scopes(nxt), "write_file", {**ARGS, "content": "else"}) is None
    assert one.id != two.id


def test_a_deny_floor_tool_still_needs_its_exact_arguments(desk):
    c, p, router = desk
    rule = p.reflex.add(name="Run it", source="webhook", match="s", action="session", task_template="x")
    floor = sorted(DENY_FLOOR_TOOLS)[0]
    with pytest.raises(ValueError, match="deny floor"):
        p.grants.create("rule", rule.id, floor, "", "any arguments")
    args = {"command": "dir"}
    p.grants.create("rule", rule.id, floor, args_hash(floor, args), "exactly this")
    run = _fire(router, "s")
    scopes = AgentRuntime.grant_scopes(run)
    assert p.grants.match(scopes, floor, args) is not None
    assert p.grants.match(scopes, floor, {"command": "del *"}) is None


async def test_the_ask_says_always_for_this_rule_where_it_is_answered():
    approvals = ChatApprovals()
    aid, _fut = approvals.request("write_file", {"path": "a"}, session_id="s1")
    approvals.note_always(aid, can_always=True, always_scope="rule", args_hash="0" * 64)
    (row,) = approvals.pending_for("s1")
    assert row["can_always"] is True and row["always_scope"] == "rule"
    from iron_jarvis.daemon.routes.sessions import _waiting_on  # the run's page

    waiting = _waiting_on(SimpleNamespace(platform=SimpleNamespace(approvals=approvals)), "s1")
    assert waiting["always_scope"] == "rule" and waiting["can_always"] is True
    # Anything else is not a place an "Always" is kept.
    other, _f = approvals.request("write_file", {"path": "b"}, session_id="s2")
    approvals.note_always(other, can_always=True, always_scope="somewhere", args_hash="0" * 64)
    assert "always_scope" not in approvals.pending_for("s2")[0]


def test_removing_the_rule_revokes_its_grants_and_grant_revoke_finds_them_by_name(desk, tmp_path):
    c, p, router = desk
    rule = p.reflex.add(name="File it", source="webhook", match="a", action="session", task_template="x")
    kept = p.grants.create("rule", rule.id, "write_file", args_hash("write_file", ARGS), "write a.pdf")
    from iron_jarvis.settings.records import resolve_grant

    assert resolve_grant(p, "File it") == (kept.id, "")
    assert p.reflex.remove(rule.id)
    assert p.grants.get(kept.id).revoked_at is not None, "a removed rule's OKs go with it"
    assert p.grants.list(scope_kind="rule", scope_id=rule.id) == []
