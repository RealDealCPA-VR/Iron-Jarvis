"""Project chat can read/write the project folder: armed file tools run INSIDE
a grounded project's root, so read_file reaches the user's real files instead
of failing with 'escapes the session workspace'."""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult


def test_project_chat_tools_run_in_the_project_folder(tmp_path, monkeypatch):
    root = tmp_path / "workfolder"
    root.mkdir()
    (root / "note.txt").write_text("SECRET-PLAN-42")
    client = TestClient(create_app(str(tmp_path)))
    pid = client.post("/projects", json={"name": "Files", "root": str(root)}).json()["id"]

    platform = client.app.state.platform
    seen = {"round2": ""}
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class):
        n["i"] += 1
        if n["i"] == 1:
            # Round 1: ask to read the project file by the ABSOLUTE path that
            # file_search would return.
            return RouteResult(
                LLMResponse(
                    text="",
                    tool_calls=[
                        ToolCall(
                            id="c1",
                            name="read_file",
                            arguments={"path": str(root / "note.txt")},
                        )
                    ],
                ),
                "mock",
                "mock",
            )
        # Round 2: the tool result is now in the transcript — capture it + answer.
        seen["round2"] = " ".join((m.content or "") for m in messages)
        return RouteResult(LLMResponse(text="I read it."), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)

    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "read note.txt"}],
            "project_id": pid,
            "tools": ["read_file"],
        },
    )
    assert r.status_code == 200
    assert "read_file" in (r.json().get("tools_used") or [])
    # The read SUCCEEDED inside the project folder: the file content reached the
    # transcript, and there is NO workspace-escape error.
    assert "SECRET-PLAN-42" in seen["round2"]
    assert "escapes the session workspace" not in seen["round2"]


def test_project_chat_can_create_a_file_in_the_folder(tmp_path, monkeypatch):
    """The model can also GENERATE files in the project folder (write_document)."""
    root = tmp_path / "out"
    root.mkdir()
    client = TestClient(create_app(str(tmp_path)))
    pid = client.post("/projects", json={"name": "Gen", "root": str(root)}).json()["id"]
    platform = client.app.state.platform
    n = {"i": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class):
        n["i"] += 1
        if n["i"] == 1:
            return RouteResult(
                LLMResponse(
                    text="",
                    tool_calls=[
                        ToolCall(
                            id="w1",
                            name="write_document",
                            arguments={"path": "notes.md", "content": "# Plan\n- ship it"},
                        )
                    ],
                ),
                "mock",
                "mock",
            )
        return RouteResult(LLMResponse(text="wrote it"), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "write a notes.md"}],
            "project_id": pid,
            "tools": ["write_document"],
        },
    )
    assert r.status_code == 200
    # The file landed in the PROJECT folder, not a scratch dir.
    assert (root / "notes.md").is_file()
    assert "ship it" in (root / "notes.md").read_text()
