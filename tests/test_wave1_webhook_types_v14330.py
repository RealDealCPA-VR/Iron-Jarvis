"""v1.433.0 (Wave 1, W1) — an outbound webhook names events Iron Jarvis SENDS.

Delivery matches an event's type EXACTLY (``OutboundWebhooks.on_event`` looks
the type up in its index), and the save only checked the list was not empty.
So "sesion.completed" (or a name nothing publishes, like "session.failed")
saved happily and the webhook was never sent anything — silently.

Now every door that writes an outbound webhook refuses a name Iron Jarvis
never publishes, in one sentence naming the closest real names:
``POST /webhooks`` (the page shows the route's ``detail``), the chat tools
``webhook_add`` and ``webhook_update`` (both go through
``OutboundWebhooks.register``). The known names are ``EventType``'s values
plus ``core.events.OTHER_PUBLISHED_TYPES`` (names published as module
constants), plus whatever a schedule of kind "event" publishes in this
install. The scan at the bottom keeps that list honest: every publish call in
``src/`` with a literal or a module-level constant name must be in it.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.events import OTHER_PUBLISHED_TYPES, EventType, known_event_types
from iron_jarvis.daemon.app import create_app
from iron_jarvis.webhooks.outbound import unknown_event_types, unknown_event_types_sentence

SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis"


def _client(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    return client, client.app.state.platform


def _slugs(client) -> set[str]:
    return {w["slug"] for w in client.get("/webhooks").json()["webhooks"]}


def _out(types):
    return {"slug": "zap", "direction": "outbound", "target_url": "https://example.com/hook", "event_types": types}


def test_a_misspelt_event_type_is_refused_at_post_and_names_the_real_one(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/webhooks", json=_out(["sesion.completed"]))
    assert r.status_code == 400, r.text
    detail = r.json()["detail"]
    assert "sesion.completed" in detail, detail
    assert "Did you mean session.completed" in detail, detail
    assert "never" in detail and "daemon" not in detail.lower(), detail
    assert "zap" not in _slugs(client), "a dead row was saved"


def test_a_name_nothing_publishes_is_refused_even_when_another_listed_one_is_real(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post("/webhooks", json=_out(["session.completed", "session.failed"]))
    assert r.status_code == 400, r.text
    assert "“session.failed”" in r.json()["detail"]
    assert "zap" not in _slugs(client)


def test_real_event_types_still_save_and_deliver(tmp_path, monkeypatch):
    """Anti-vacuity control: names from EventType AND from the module
    constants list save, trimmed, and a real one is delivered."""
    client, platform = _client(tmp_path)
    monkeypatch.setattr("iron_jarvis.webhooks.outbound.assert_safe_webhook_url", lambda *a, **k: None)
    posted: list[str] = []
    platform.outbound_webhooks.http_post = lambda url, payload, headers: posted.append(payload["type"]) or {"ok": True}
    r = client.post("/webhooks", json=_out([" session.completed ", "goal.stopped", "media.completed"]))
    assert r.status_code == 200, r.text
    row = next(w for w in client.get("/webhooks").json()["webhooks"] if w["slug"] == "zap")
    assert row["event_types_json"] == '["session.completed", "goal.stopped", "media.completed"]', row
    asyncio.run(platform.event_bus.publish("session.completed", {"status": "completed"}))
    assert posted == ["session.completed"]


async def test_the_webhook_add_tool_refuses_an_unknown_type_too(tmp_path):
    from iron_jarvis.webhooks.tools import WebhookAddTool

    client, platform = _client(tmp_path)
    res = await WebhookAddTool(platform).execute(
        {"slug": "agent-out", "direction": "outbound", "target_url": "https://example.com/h",
         "event_types": ["workflow.finished"]},
        None,
    )
    assert res.ok is False
    assert "“workflow.finished”" in (res.error or "") and "workflow.completed" in (res.error or ""), res.error
    assert "agent-out" not in _slugs(client)


async def test_the_webhook_update_tool_refuses_an_unknown_type_and_changes_nothing(tmp_path, monkeypatch):
    from iron_jarvis.settings.records import WebhookUpdateTool, snap_webhook

    client, platform = _client(tmp_path)
    monkeypatch.setattr("iron_jarvis.webhooks.outbound.assert_safe_webhook_url", lambda *a, **k: None)
    platform.outbound_webhooks.register("deploys", "https://example.com/h", ["session.completed"])
    before = snap_webhook(platform, "deploys")
    res = await WebhookUpdateTool(platform).execute({"slug": "deploys", "event_types": ["sessions.completed"]}, None)
    assert res.ok is False, res
    assert "session.completed" in (res.error or ""), res.error
    assert snap_webhook(platform, "deploys") == before


def test_a_schedules_own_event_name_may_be_subscribed_to(tmp_path):
    """A schedule of kind "event" publishes a name of the user's own
    (``payload.type``). A webhook may listen for it; before the schedule
    exists, the same name is refused."""
    client, platform = _client(tmp_path)
    r = client.post("/webhooks", json=_out(["nightly.report"]))
    assert r.status_code == 400, r.text
    platform.scheduler.add_task("nightly", "0 2 * * *", kind="event", payload={"type": "nightly.report"})
    r = client.post("/webhooks", json=_out(["nightly.report"]))
    assert r.status_code == 200, r.text
    assert "zap" in _slugs(client)


def test_the_sentence_helpers():
    known = known_event_types()
    assert unknown_event_types(["session.completed", " x.y ", "x.y", ""], known) == ["x.y"]
    one = unknown_event_types_sentence(["sesion.completed"], known)
    assert one.startswith("Iron Jarvis never sends an event called “sesion.completed”"), one
    many = unknown_event_types_sentence(["zzz.qqq", "yyy.ppp"], known)
    assert "events called “zzz.qqq” or “yyy.ppp”" in many and "session.completed" in many, many
    long = unknown_event_types_sentence(["a." + "b" * 200], known)
    assert len(long) < 260, long


# --------------------------------------------------------------------------
# The known list covers what the code publishes
# --------------------------------------------------------------------------


def _module_constants() -> dict[str, set[str]]:
    """Every module-level ``NAME = "a.b"`` string assignment in src/."""
    out: dict[str, set[str]] = {}
    for p in SRC.rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in tree.body:
            targets = []
            if isinstance(node, ast.Assign):
                targets = [t for t in node.targets if isinstance(t, ast.Name)]
                value = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                targets = [node.target]
                value = node.value
            else:
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str) and "." in value.value:
                for t in targets:
                    out.setdefault(t.id, set()).add(value.value)
    return out


def _published_names() -> dict[str, str]:
    """name -> where, for every publish-like call whose first argument is a
    string literal or a module constant (locals cannot be resolved)."""
    consts = _module_constants()
    found: dict[str, str] = {}
    for p in SRC.rglob("*.py"):
        src = p.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and n.args):
                continue
            f = n.func
            fname = f.attr if isinstance(f, ast.Attribute) else (f.id if isinstance(f, ast.Name) else "")
            if fname not in ("publish", "_publish", "_publish_bg"):
                continue
            a = n.args[0]
            where = f"{p.relative_to(SRC)}:{n.lineno}"
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                if "." in a.value:
                    found[a.value] = where
            elif isinstance(a, ast.Name) and a.id in consts and a.id.isupper():
                for v in consts[a.id]:
                    found[v] = where
            elif isinstance(a, ast.Attribute) and not (isinstance(a.value, ast.Name) and a.value.id == "EventType"):
                if a.attr in consts and a.attr.isupper():
                    for v in consts[a.attr]:
                        found[v] = where
    return found


def test_the_scan_sees_the_names_it_is_about():
    names = _published_names()
    # Anti-vacuity: literals and module constants both resolve.
    for known in ("workflow.waiting", "goal.stopped", "mcp.pack_failed", "media.completed", "webhook.received"):
        assert known in names, sorted(names)


def test_every_published_name_is_one_a_webhook_may_subscribe_to():
    known = known_event_types()
    missing = {name: where for name, where in _published_names().items() if name not in known}
    assert not missing, (
        "these names are published but missing from core/events.OTHER_PUBLISHED_TYPES, so an outbound "
        f"webhook for them would be refused: {missing}"
    )


def test_the_other_published_list_does_not_repeat_event_type():
    own = {v for k, v in vars(EventType).items() if k.isupper() and isinstance(v, str)}
    assert not (OTHER_PUBLISHED_TYPES & own), OTHER_PUBLISHED_TYPES & own


@pytest.mark.parametrize("name", sorted(OTHER_PUBLISHED_TYPES))
def test_every_extra_name_is_really_published_somewhere(name):
    """A stale entry would let a webhook save for a name nothing sends."""
    hay = "\n".join(p.read_text(encoding="utf-8") for p in SRC.rglob("*.py") if p.name != "events.py")
    assert f'"{name}"' in hay, name
