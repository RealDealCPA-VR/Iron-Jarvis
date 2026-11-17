"""Third north-star fix plan, group 17 (PG2, decision K2): a workflow's TOOL
step reads the tool's OWN "ask every time" check before it runs.

THE DEFECT. A tool step asked only the permission tier
(``permissions.authorize``) and then called ``registry.invoke``. A tool can
also carry its own check (``Tool.spend_check``): ``webhook_add`` with
``direction="outbound"`` sends Iron Jarvis's events to another address from
then on, so it is asked about every time, in every lane (the agent runtime
pauses or refuses, the stream lane cards, the POST lane refuses). The engine
never read it, so a scheduled workflow (which an agent may author unasked:
``workflow_create`` / ``schedule_create`` are allow-tier) added an outgoing
webhook with nobody asked.

THE RULE. The step reads the SAME check through the one helper the chat lanes
and the runtime call (``tools/base.spend_checks_for``). A check that says ask
is refused in words (a workflow has nobody watching to answer, and no
standing approval covers a call its own check asks about), through the
registry's ledgered deny seam, and the tool never runs. An inbound webhook and
an ordinary allow-tier step still run.

Proved through the layer that refuses: the real engine, the real registry,
the real permission engine and the real tools. Only the outbound delivery's
HTTP post is a recorder (no network), and the outbound store skips its DNS
check (``allow_internal``) so a red run would really store the row.
"""

from __future__ import annotations

# Register workflow tables on SQLModel.metadata BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import select

import iron_jarvis.workflows.engine as engine_mod
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ToolInvocation
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.webhooks.models import WebhookRecord
from iron_jarvis.workflows.engine import Step, WorkflowDef, WorkflowEngine

HOST = "hooks.example.test"
OUT = {
    "slug": "nightly-out",
    "direction": "outbound",
    "target_url": f"https://{HOST}/path/abc",
    "event_types": ["session.completed"],
}
IN = {"slug": "nightly-in", "direction": "inbound"}


def _quiet_outbound(platform) -> list:
    """No network: the outbound store skips its DNS check (so a red run really
    stores the row) and its POST is a recorder."""
    posted: list = []
    platform.outbound_webhooks.allow_internal = True
    platform.outbound_webhooks.http_post = lambda *a, **k: posted.append((a, k))
    return posted


def _webhooks(platform) -> dict[str, str]:
    with session_scope(platform.engine) as db:
        return {r.slug: r.direction for r in db.exec(select(WebhookRecord))}


def _rows(platform, run_id: str, tool: str) -> list[ToolInvocation]:
    with session_scope(platform.engine) as db:
        rows = list(
            db.exec(
                select(ToolInvocation).where(
                    ToolInvocation.session_id == run_id, ToolInvocation.tool == tool
                )
            )
        )
        for r in rows:
            db.expunge(r)
        return rows


async def test_an_outbound_webhook_step_is_refused_and_nothing_is_stored(tmp_path):
    platform = build_platform(str(tmp_path))
    _quiet_outbound(platform)
    wf = WorkflowDef(
        name="send events out",
        steps=[Step(name="Wire it", kind="tool", tool="webhook_add", args=dict(OUT))],
    )
    rec = await WorkflowEngine(platform).run(wf)
    # Pins the engine's spend check (``_tool_step_own_check``): without it the
    # webhook is created and the run completes.
    assert "nightly-out" not in _webhooks(platform), "a workflow made an outbound webhook unasked"
    assert rec.status == "failed"
    out = json.loads(rec.outputs_json)["Wire it"]
    summary = out["summary"]
    # The check's own words (the host only, never the path) and the way out.
    assert HOST in summary and "/path/abc" not in summary, summary
    assert "every time" in summary and "from chat" in summary, summary
    for engine_word in ("headless", "auto-approve", "spend", "SpendCheck", "interactive"):
        assert engine_word not in summary, summary
    # Ledgered as a refusal (the registry's deny seam), never as a run.
    rows = _rows(platform, rec.id, "webhook_add")
    assert len(rows) == 1 and rows[0].ok is False, rows


async def test_an_inbound_webhook_step_and_an_allow_tier_step_still_run(tmp_path):
    platform = build_platform(str(tmp_path))
    _quiet_outbound(platform)
    wf = WorkflowDef(
        name="inbound only",
        steps=[
            Step(name="Wire it", kind="tool", tool="webhook_add", args=dict(IN)),
            Step(name="List", kind="tool", tool="list_folder", args={"path": str(tmp_path)}),
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    outs = json.loads(rec.outputs_json)
    assert rec.status == "completed", outs
    assert outs["Wire it"]["status"] == "completed" and outs["List"]["status"] == "completed"
    assert _webhooks(platform).get("nightly-in") == "inbound"


async def test_a_brain_save_the_profile_wants_asked_is_refused_in_a_workflow(tmp_path):
    """A second tool with its own check: ``brain_save`` (allow-tier) asks
    before each save when the memory profile says "ask". Not attended-only:
    an Agent step would ask, so the words name that way."""
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    r = client.post(
        "/brain/profiles",
        json={"name": "Work brain", "primary": "brain", "guide_ref": "", "save_to": "ask"},
    )
    assert r.status_code == 201, r.text
    assert client.put(
        "/settings/values", json={"values": {"brain_profile": r.json()["id"]}}
    ).status_code == 200
    mark = "NOTE-MARK-weekly-report-goes-out-friday"
    wf = WorkflowDef(
        name="keep notes",
        steps=[
            Step(
                name="Keep",
                kind="tool",
                tool="brain_save",
                args={"title": "weekly report", "content": mark},
            )
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    folder = Path(platform.config.home, "brain")
    saved = [p for p in folder.rglob("*.md") if mark in p.read_text(encoding="utf-8", errors="ignore")] if folder.exists() else []
    assert saved == [], f"a workflow saved to memory unasked: {saved}"
    assert rec.status == "failed"
    summary = json.loads(rec.outputs_json)["Keep"]["summary"]
    assert "weekly report" in summary and "Agent step" in summary, summary
    rows = _rows(platform, rec.id, "brain_save")
    assert len(rows) == 1 and rows[0].ok is False, rows


async def test_a_write_into_git_hooks_is_refused_by_the_same_guard(tmp_path, monkeypatch):
    """The helper also applies Ask first's guard for Git's own files to the
    file tools (allow-tier): a workflow step writing ``.git/hooks/*`` in its
    folder is refused, exactly as an unattended agent run refuses it."""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(engine_mod.tempfile, "mkdtemp", lambda *a, **k: str(ws))
    platform = build_platform(str(tmp_path / "home"))
    wf = WorkflowDef(
        name="hooky",
        steps=[
            Step(
                name="Hook",
                kind="tool",
                tool="write_file",
                args={"path": ".git/hooks/pre-commit", "content": "echo hi"},
            )
        ],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert not (ws / ".git" / "hooks" / "pre-commit").exists()
    assert rec.status == "failed"
    summary = json.loads(rec.outputs_json)["Hook"]["summary"]
    assert ".git/hooks/pre-commit" in summary and "from chat" in summary, summary


async def test_an_ask_tier_tool_step_is_refused_in_plain_words(tmp_path):
    """The tier refusal (an Ask-tier tool that changes things) in the app's
    words: no "headless", no advice to switch the tool to auto-approve (a
    floor tool cannot be)."""
    platform = build_platform(str(tmp_path))
    wf = WorkflowDef(
        name="sneaky",
        steps=[Step(name="Own", kind="tool", tool="shell", args={"command": "echo hi"})],
    )
    rec = await WorkflowEngine(platform).run(wf)
    assert rec.status == "failed"
    summary = json.loads(rec.outputs_json)["Own"]["summary"]
    assert "Agent step" in summary and "shell" in summary, summary
    for engine_word in ("headless", "auto-approve", "interactive approval"):
        assert engine_word not in summary, summary
