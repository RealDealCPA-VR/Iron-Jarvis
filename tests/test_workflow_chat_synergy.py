"""v1.120.0 — W-A: chat ⇄ workflow synergy.

Covers the batch's backend legs:
  A1  the workflow_draft exit tool: chat proposes a saveable workflow card
      instead of prose steps — sanitized, never auto-saved, and the draft
      wins over a stray escalate call in the same reply;
  A2  crystallize: a saved thread generalizes into a workflow draft via a
      real model (offline refuses honestly — no fabricated processes);
  A3  live run narration: the engine publishes workflow.step_started /
      step_completed as each step begins and settles — the wire chat's run
      card streams from.
"""

from __future__ import annotations

# Register workflow tables on SQLModel.metadata BEFORE any platform is built.
import iron_jarvis.workflows.models  # noqa: F401

import json

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.events import EventType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.workflows.engine import Step, WorkflowDef, WorkflowEngine


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _force_calls(client, monkeypatch, calls_per_round, text_per_round=None):
    """Make the mock provider emit chosen tool_calls (and optionally chosen
    text) round by round — the harness test_chat_escalation.py proved for the
    first exit tool."""
    platform = client.app.state.platform
    real_get = platform.providers.get
    rounds = {"n": 0}

    def spy(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def complete(*, system, messages, tools):
            resp = await real_complete(system=system, messages=messages, tools=tools)
            i = rounds["n"]
            rounds["n"] += 1
            resp.tool_calls = calls_per_round(i)
            if text_per_round is not None:
                resp.text = text_per_round(i)
            return resp

        adapter.complete = complete
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy)
    return rounds


DRAFT_ARGS = {
    "name": "friday-receipts",
    "description": "Weekly receipt review",
    "steps": [
        {"name": "Gather", "agent": "researcher", "task": "collect the week's receipts"},
        {"name": "Check", "agent": "not-a-real-agent", "task": "verify each receipt"},
    ],
}


# --------------------------------------------------------------------------- #
# A1 — the workflow_draft exit tool
# --------------------------------------------------------------------------- #


def test_workflow_draft_spec_rides_every_turn(client, monkeypatch):
    seen = {}
    platform = client.app.state.platform
    real_get = platform.providers.get

    def spy(p, m=None):
        a = real_get(p, m)
        rc = a.complete

        async def complete(*, system, messages, tools):
            seen["tools"] = tools
            seen["system"] = system
            return await rc(system=system, messages=messages, tools=tools)

        a.complete = complete
        return a

    monkeypatch.setattr(platform.providers, "get", spy)
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 200
    names = [t.get("name") for t in seen["tools"]]
    assert "workflow_draft" in names and "escalate_to_agent" in names
    # The system prompt teaches WHEN, and never mentions modes.
    assert "workflow_draft" in seen["system"]


def test_draft_call_ends_turn_with_sanitized_draft(client, monkeypatch):
    _force_calls(
        client, monkeypatch,
        lambda i: [ToolCall(id="tc1", name="workflow_draft", arguments=DRAFT_ARGS)],
    )
    r = client.post(
        "/chat",
        json={"messages": [{"role": "user", "content": "every friday check receipts"}]},
    )
    assert r.status_code == 200
    out = r.json()
    draft = out["workflow_draft"]
    assert draft["name"] == "friday-receipts"
    assert [s["name"] for s in draft["steps"]] == ["Gather", "Check"]
    assert draft["steps"][0]["agent"] == "researcher"
    assert draft["steps"][1]["agent"] == "builder"  # unknown agent coerced
    assert out["escalate"] is False
    # Suggest-don't-act: proposing must NOT save anything.
    assert client.get("/workflows").json()["workflows"] == []


def test_draft_beats_escalate_in_same_reply(client, monkeypatch):
    _force_calls(
        client, monkeypatch,
        lambda i: [
            ToolCall(id="tc1", name="escalate_to_agent", arguments={"reason": "big"}),
            ToolCall(id="tc1", name="workflow_draft", arguments=DRAFT_ARGS),
        ],
    )
    out = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "process"}]}
    ).json()
    assert out["workflow_draft"] is not None
    assert out["escalate"] is False


def test_unusable_draft_does_not_hijack_the_turn(client, monkeypatch):
    _force_calls(
        client, monkeypatch,
        lambda i: [ToolCall(id="tc1", name="workflow_draft", arguments={"name": "x", "steps": []})],
    )
    out = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hello"}]}
    ).json()
    assert out["workflow_draft"] is None  # nothing usable — the turn just ends


def test_draft_exit_reply_is_honest_not_placeholder(client, monkeypatch):
    # Model calls ONLY the exit tool with no prose: the reply must be EMPTY —
    # not "(no reply)" (the client captions the card), and never a
    # creation-honesty note calling a successful proposal a failure.
    _force_calls(
        client, monkeypatch,
        lambda i: [ToolCall(id="tc1", name="workflow_draft", arguments=DRAFT_ARGS)],
        text_per_round=lambda i: "",
    )
    out = client.post(
        "/chat",
        json={
            "messages": [
                {
                    "role": "user",
                    "content": "every friday create an excel summary of my invoices",
                }
            ]
        },
    ).json()
    assert out["workflow_draft"] is not None
    assert out["reply"] == ""


def test_sanitize_draft_hardening():
    # The draft is MODEL OUTPUT: step count capped, tasks capped, names
    # deduped (live-run state is name-keyed), workflow name slugged (a "/"
    # would make the saved row unreachable via /workflows/{name}).
    from iron_jarvis.daemon.routes.chat import _sanitize_draft

    draft = _sanitize_draft(
        {
            "name": "reports/weekly\nq",
            "steps": (
                [{"name": "Same", "task": "a" * 9000}, {"name": "Same", "task": "b"}]
                + [{"task": f"t{i}"} for i in range(20)]
            ),
        }
    )
    assert "/" not in draft["name"] and "\n" not in draft["name"]
    assert len(draft["steps"]) <= 12
    assert len(draft["steps"][0]["task"]) <= 4000
    names = [s["name"] for s in draft["steps"]]
    assert len(names) == len(set(names))


# --------------------------------------------------------------------------- #
# A2 — crystallize a thread into a draft
# --------------------------------------------------------------------------- #


class _FakeAdapter:
    """A REAL-adapter stand-in (deliberately not MockLLMAdapter)."""

    provider = "anthropic"
    model = "claude-opus-4-8"

    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict] = []

    async def complete(self, *, system, messages, tools=None):
        self.calls.append({"system": system, "messages": messages})
        return LLMResponse(text=self._text)


def _seed_thread(client, project_id=None):
    body = {
        "messages": [
            {"role": "user", "content": "pull this week's receipts and check them"},
            {"role": "assistant", "content": "Gathered 12 receipts; 2 flagged."},
        ]
    }
    if project_id:
        body["project_id"] = project_id
    return client.put("/chat/threads/new", json=body).json()["id"]


def test_crystallize_offline_refuses_honestly(client):
    tid = _seed_thread(client)
    r = client.post(f"/chat/threads/{tid}/crystallize", json={})
    assert r.status_code == 400
    assert "connect a model" in r.json()["detail"]


def test_crystallize_generalizes_thread_into_unsaved_draft(client):
    proj = client.post("/projects", json={"name": "Taxes"}).json()
    pid = proj.get("id") or proj.get("project", {}).get("id")
    tid = _seed_thread(client, project_id=pid)
    fake = _FakeAdapter(
        json.dumps(
            {
                "name": "weekly-receipt-check",
                "description": "Gather and verify the week's receipts",
                "steps": [
                    {"name": "Gather", "agent": "researcher", "task": "collect the provided receipts"},
                    {"name": "Verify", "agent": "reviewer", "task": "flag anything suspicious"},
                ],
            }
        )
    )
    client.app.state.platform.providers.get = lambda p, m=None: fake
    r = client.post(f"/chat/threads/{tid}/crystallize", json={"provider": "anthropic"})
    assert r.status_code == 200
    out = r.json()
    assert out["name"] == "weekly-receipt-check"
    assert len(out["steps"]) == 2
    assert out["project_id"] == pid  # the thread's project rides along
    # The model saw the ACTUAL conversation.
    sent = fake.calls[0]["messages"][0].content
    assert "receipts" in sent
    # Suggest-don't-act: crystallize returns a draft, it does not save.
    assert client.get("/workflows").json()["workflows"] == []


def test_crystallize_unknown_thread_404s(client):
    assert client.post("/chat/threads/nope/crystallize", json={}).status_code == 404


async def test_engine_narrates_each_step_live(tmp_path):
    platform = build_platform(str(tmp_path))
    heard: list = []
    platform.event_bus.add_handler(lambda e: heard.append(e))

    wf = WorkflowDef(
        name="narrated",
        steps=[
            Step(name="gather", agent="builder", task="collect the things"),
            Step(name="report", agent="builder", task="write the report"),
        ],
    )
    rec = await engine_run(platform, wf)
    assert rec.status == "completed"

    started = [e for e in heard if e.type == EventType.WORKFLOW_STEP_STARTED]
    settled = [e for e in heard if e.type == EventType.WORKFLOW_STEP_COMPLETED]
    assert [e.payload["step"] for e in started] == ["gather", "report"]
    assert [e.payload["step"] for e in settled] == ["gather", "report"]

    # Every event carries what the run card needs: identity and position.
    # (v1.121.0: step_started fires the moment the step BEGINS — before any
    # session exists — so the session id rides step_completed instead.)
    first = started[0].payload
    assert first["run_id"] == rec.id
    assert first["workflow"] == "narrated"
    assert (first["index"], first["total"]) == (0, 2)

    done = settled[-1].payload
    assert done["status"] == "completed"
    assert "summary" in done
    assert done["session_id"].startswith("session_")

    # The terminal workflow.completed event still fires after the narration.
    terminal = [e for e in heard if e.type == EventType.WORKFLOW_COMPLETED]
    assert len(terminal) == 1
    assert terminal[0].payload["run_id"] == rec.id


async def engine_run(platform, wf):
    return await WorkflowEngine(platform).run(wf)
