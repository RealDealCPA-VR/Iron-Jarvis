"""v1.225.0 — workflow creation is reliable on a local model, and runs say
what they silently decided.

The user's report: workflows created in a chat are sometimes unreliable and
"require a project", and work outside a project though not reliably. The
live install's default is a local fleet endpoint, and every creation door
depended on that model producing EXACT JSON — a tool call with well-formed
arguments, or a reply that is only a JSON object. Near-misses (a trailing
comma, a fence, steps as sentences, the workflow written in prose) each
became "no card" / "try rephrasing". And a card born inside a project never
carried the project: Save wrote an unpinned workflow and Run forced
`project_id: ""`, so a process drafted in a project ran with no folder.

Pinned here:
- core/jsonish recovers the JSON models actually emit; the OpenAI-compatible
  adapter (the fleet endpoint's) recovers broken tool arguments instead of
  handing back `{}`;
- `_sanitize_draft` accepts string args, string/numbered steps, bare-sentence
  steps and the common key aliases;
- BOTH chat lanes make the card from: the exit tool, an unarmed
  workflow_create call, or a JSON workflow written in a text-only reply —
  and never from an unrelated JSON answer;
- the draft carries the chat's project (both lanes); no project → no pin;
- the page generator takes ONE repair round before it 422s;
- a step that cannot run is refused at SAVE time, naming the step;
- a run pinned to a project whose folder is gone carries a note saying so.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.jsonish import first_balanced, loads_lenient, loads_object
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _sanitize_draft
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall

WF_JSON = {
    "name": "friday-receipts",
    "description": "Weekly receipt review",
    "steps": [
        {"name": "Gather", "agent": "researcher", "task": "collect the week's receipts"},
        {"name": "Check", "agent": "reviewer", "task": "verify each receipt"},
    ],
}


# ------------------------------------------------------------- jsonish


@pytest.mark.parametrize(
    "text",
    [
        json.dumps(WF_JSON),
        "Here is the workflow:\n```json\n" + json.dumps(WF_JSON) + "\n```\nLet me know.",
        "Sure! " + json.dumps(WF_JSON) + " — want changes?",
        json.dumps(WF_JSON)[:-1] + ",}",  # trailing comma before the closing brace
        "{'name': 'friday-receipts', 'description': 'x', 'steps': [{'name': 'Gather', 'task': 't'}]}",
    ],
)
def test_loads_object_recovers_what_models_emit(text):
    obj = loads_object(text)
    assert obj is not None and obj["name"] == "friday-receipts"
    assert obj["steps"][0]["name"] == "Gather"


def test_loads_object_is_honest_about_garbage():
    assert loads_object("I cannot help with that.") is None
    assert loads_object('{"name": "unterminated", "steps": [') is None
    assert loads_lenient("[1, 2, 3,]", want=list) == [1, 2, 3]
    assert first_balanced('say {"a": "}"} then {"b": 1}') == '{"a": "}"}'


def test_openai_adapter_recovers_broken_tool_arguments():
    from iron_jarvis.providers.adapters.openai import OpenAIAdapter

    broken = json.dumps(WF_JSON)[:-1] + ",}"
    data = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"id": "c1", "function": {"name": "workflow_draft", "arguments": broken}}
                    ],
                },
            }
        ]
    }
    resp = OpenAIAdapter._parse(data)
    assert resp.tool_calls[0].arguments["name"] == "friday-receipts"
    data["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "nonsense"
    assert OpenAIAdapter._parse(data).tool_calls[0].arguments == {}


# ------------------------------------------------------ the sanitizer


def test_sanitize_accepts_string_args_string_steps_and_sentences():
    as_string = _sanitize_draft(json.dumps(WF_JSON))
    assert as_string and [s["name"] for s in as_string["steps"]] == ["Gather", "Check"]
    steps_as_string = _sanitize_draft({"name": "x", "steps": json.dumps(WF_JSON["steps"])})
    assert steps_as_string and len(steps_as_string["steps"]) == 2
    sentences = _sanitize_draft({"name": "x", "steps": ["Collect receipts", "Verify each one"]})
    assert sentences and sentences["steps"][0]["task"] == "Collect receipts"
    assert sentences["steps"][0]["agent"] == "builder"
    numbered = _sanitize_draft({"title": "Numbered", "steps": {"1": {"task": "a"}, "2": {"task": "b"}}})
    assert numbered and numbered["name"] == "Numbered" and len(numbered["steps"]) == 2
    aliases = _sanitize_draft({"name": "x", "steps": [{"title": "T", "instruction": "do it"}]})
    assert aliases and aliases["steps"][0] == {**aliases["steps"][0], "name": "T", "task": "do it"}
    assert _sanitize_draft("not json at all") is None
    assert _sanitize_draft({"name": "x", "steps": []}) is None


def test_draft_from_text_needs_a_name_and_steps():
    # Imported here so the lane tests still collect against a build without
    # the helper — the mutation check must fail on the ASSERTION.
    from iron_jarvis.daemon.chat_turn import _draft_from_text

    assert _draft_from_text("Here you go:\n```json\n" + json.dumps(WF_JSON) + "\n```")["name"] == "friday-receipts"
    # An unrelated JSON answer with a "steps" key but no name is NOT a card.
    assert _draft_from_text('{"steps": [{"task": "x"}], "total": 3}') is None
    assert _draft_from_text("plain prose about steps") is None


# ------------------------------------------------- both chat lanes


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _force(client, monkeypatch, calls_per_round, text_per_round=None):
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


def _stream_done(client, body):
    with client.stream("POST", "/chat/stream", json=body) as r:
        assert r.status_code == 200
        done = None
        for line in r.iter_lines():
            if line.startswith("data: "):
                payload = json.loads(line[6:])
                if "escalate" in payload:
                    done = payload
    assert done is not None, "no done frame"
    return done


MSG = {"messages": [{"role": "user", "content": "every friday check the receipts"}]}


def test_a_workflow_written_as_json_text_becomes_the_card_in_both_lanes(client, monkeypatch):
    text = "Here's a workflow:\n```json\n" + json.dumps(WF_JSON) + "\n```"
    _force(client, monkeypatch, lambda i: [], text_per_round=lambda i: text)
    out = client.post("/chat", json=MSG).json()
    assert out["workflow_draft"]["name"] == "friday-receipts"
    assert [s["agent"] for s in out["workflow_draft"]["steps"]] == ["researcher", "reviewer"]
    assert "project_id" not in out["workflow_draft"]  # no project → no pin
    done = _stream_done(client, MSG)
    assert done["workflow_draft"]["name"] == "friday-receipts"
    # Suggest-don't-act: nothing was saved by either lane.
    assert client.get("/workflows").json()["workflows"] == []


def test_an_unarmed_workflow_create_call_becomes_the_card_not_a_refusal(client, monkeypatch):
    _force(
        client, monkeypatch,
        # Every round, not just round 0: the spy counts completions ACROSS the
        # two requests below, and a draft call ends its turn anyway.
        lambda i: [ToolCall(id="c1", name="workflow_create", arguments=WF_JSON)],
    )
    out = client.post("/chat", json=MSG).json()
    assert out["workflow_draft"]["name"] == "friday-receipts"
    assert "workflow_create" not in out.get("denied_tools", [])
    assert client.get("/workflows").json()["workflows"] == []
    done = _stream_done(client, MSG)
    assert done["workflow_draft"]["name"] == "friday-receipts"


def test_a_prose_only_reply_is_still_just_a_reply(client, monkeypatch):
    _force(client, monkeypatch, lambda i: [], text_per_round=lambda i: "1. Gather 2. Check")
    out = client.post("/chat", json=MSG).json()
    assert out["workflow_draft"] is None and out["reply"] == "1. Gather 2. Check"


def test_the_draft_carries_the_chats_project_in_both_lanes(client, monkeypatch):
    pid = client.post("/projects", json={"name": "Acme"}).json()["id"]
    _force(
        client, monkeypatch,
        lambda i: [ToolCall(id="c1", name="workflow_draft", arguments=WF_JSON)],
    )
    body = {**MSG, "project_id": pid}
    assert client.post("/chat", json=body).json()["workflow_draft"]["project_id"] == pid
    assert _stream_done(client, body)["workflow_draft"]["project_id"] == pid
    # A project id that does not resolve stamps nothing.
    ghost = {**MSG, "project_id": "proj_nope"}
    assert "project_id" not in client.post("/chat", json=ghost).json()["workflow_draft"]


# ----------------------------------------------------- the page generator


class _Scripted:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    async def complete(self, *, system, messages, tools):
        self.calls += 1
        return LLMResponse(text=self.replies.pop(0) if self.replies else "")


def test_generate_takes_one_repair_round_before_giving_up(tmp_path):
    app = create_app(str(tmp_path))
    prose = "Step 1: gather receipts. Step 2: check them."
    adapter = _Scripted([prose, json.dumps(WF_JSON)])
    app.state.platform.providers.get = lambda *a, **k: adapter
    client = TestClient(app)
    r = client.post("/workflows/generate", json={"description": "friday receipts"})
    assert r.status_code == 200, r.text
    assert adapter.calls == 2
    assert [s["name"] for s in r.json()["steps"]] == ["Gather", "Check"]
    # Still honest when the repair fails too — and it does not keep asking.
    adapter2 = _Scripted([prose, "still prose"])
    app.state.platform.providers.get = lambda *a, **k: adapter2
    r = client.post("/workflows/generate", json={"description": "x"})
    assert r.status_code == 422 and adapter2.calls == 2
    assert "no steps could be read" in r.json()["detail"]


def test_generate_tolerates_sentence_steps(tmp_path):
    app = create_app(str(tmp_path))
    app.state.platform.providers.get = lambda *a, **k: _Scripted(
        [json.dumps({"name": "list-only", "steps": ["Gather receipts", "Check them"]})]
    )
    client = TestClient(app)
    r = client.post("/workflows/generate", json={"description": "x"})
    assert r.status_code == 200, r.text
    assert [s["task"] for s in r.json()["steps"]] == ["Gather receipts", "Check them"]


# ---------------------------------------------------- save-time validation


def test_save_refuses_steps_that_cannot_run(client):
    bad = [
        ({"name": "", "agent": "builder", "task": ""}, "an agent step needs a task"),
        ({"name": "Call", "kind": "tool", "args": {}}, "a tool step needs a tool name"),
        ({"name": "Ask", "kind": "ask"}, "an ask step needs a message"),
    ]
    for step, msg in bad:
        r = client.post("/workflows", json={"name": "w", "steps": [step]})
        assert r.status_code == 422, r.text
        assert msg in r.json()["detail"]
    ok = client.post("/workflows", json={"name": "w", "steps": [
        {"name": "Gather", "task": "collect"},
        {"name": "Ping", "kind": "notify", "message": "done"},
        {"name": "Q", "kind": "ask", "message": "continue?"},
    ]})
    assert ok.status_code == 200, ok.text


# --------------------------------------------- the missing-folder note


def _wait_terminal(client, run_id, seconds=20):
    deadline = time.time() + seconds
    while time.time() < deadline:
        rec = client.get(f"/workflows/runs/{run_id}").json()
        if rec.get("status") in ("completed", "failed", "cancelled", "interrupted"):
            return rec
        time.sleep(0.2)
    return client.get(f"/workflows/runs/{run_id}").json()


def test_a_run_whose_pinned_folder_is_gone_says_so_on_the_record(tmp_path):
    import shutil

    with TestClient(create_app(str(tmp_path))) as client:
        folder = tmp_path / "acme"
        folder.mkdir()
        pid = client.post("/projects", json={"name": "Acme", "root": str(folder)}).json()["id"]
        shutil.rmtree(folder)
        r = client.post("/workflows/run", json={
            "name": "wf", "project_id": pid,
            "steps": [{"name": "Gather", "agent": "builder", "task": "collect"}],
        })
        assert r.status_code == 200, r.text
        rec = _wait_terminal(client, r.json()["id"])
        notes = json.loads(rec.get("notes_json") or "[]")
        assert notes and "Acme" in notes[0] and "scratch workspace" in notes[0]
        # A healthy pin, or no pin, carries no note.
        good = tmp_path / "good"
        good.mkdir()
        pid2 = client.post("/projects", json={"name": "Good", "root": str(good)}).json()["id"]
        for pin in (pid2, ""):
            r = client.post("/workflows/run", json={
                "name": "wf2", "project_id": pin,
                "steps": [{"name": "Gather", "agent": "builder", "task": "collect"}],
            })
            rec = _wait_terminal(client, r.json()["id"])
            assert json.loads(rec.get("notes_json") or "[]") == []
