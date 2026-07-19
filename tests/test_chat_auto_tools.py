"""Seamless chat tool arming (auto_tools): the daemon reads the request and
fills the free "+" slots from the curated safe set.

Covers the selector (deterministic scoring, safe-set discipline, cap/exclude)
and the /chat wiring: opt-in only, explicit picks first, the honest
``auto_armed`` response field, and the system-prompt disclosure.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS, select_auto_tools


# --------------------------------------------------------------- selector ---


def test_web_question_arms_web_search():
    picked = select_auto_tools("search the web for the latest Python release")
    assert picked and picked[0] == "web_search"
    assert "web_fetch" in picked


def test_url_arms_web_fetch_first():
    picked = select_auto_tools("summarize https://example.com/post for me")
    assert picked and picked[0] == "web_fetch"


def test_doc_attachment_arms_read_document():
    picked = select_auto_tools("what does this say?", attachments=["q3 report.pdf"])
    assert "read_document" in picked


def test_image_attachment_arms_view_image():
    picked = select_auto_tools("thoughts?", attachments=["screenshot.png"])
    assert "view_image" in picked


def test_create_document_intent_arms_write_document():
    picked = select_auto_tools("draft a one-page report as a docx about our Q3 numbers")
    assert "write_document" in picked


def test_folder_talk_arms_file_search():
    picked = select_auto_tools("find the invoice files in my folder and summarize them")
    assert "file_search" in picked
    assert "read_document" in picked


def test_plain_conversation_arms_nothing():
    assert select_auto_tools("hey, how are you today?") == []
    assert select_auto_tools("") == []


def test_cap_and_exclude_respected():
    msg = (
        "search the web for market prices, read the spreadsheet files in my "
        "folder, and create a report as a pdf document"
    )
    picked = select_auto_tools(msg, cap=2)
    assert len(picked) == 2
    excluded = select_auto_tools(msg, exclude={"web_search", "read_document"})
    assert "web_search" not in excluded and "read_document" not in excluded
    assert select_auto_tools(msg, cap=0) == []


def test_selection_never_leaves_the_safe_set():
    kitchen_sink = (
        "search the web, run a shell command, browse with the browser, read my "
        "files and folders, generate an image with pixio, call an MCP tool, "
        "resize this photo, extract tables from the pdf, and write a report"
    )
    picked = select_auto_tools(kitchen_sink, cap=6)
    assert picked  # plenty of signal
    assert set(picked) <= AUTO_SAFE_TOOLS
    assert "shell" not in picked and "browser_use" not in picked


# ----------------------------------------------------------------- /chat ----


def _client_with_capture(tmp_path, monkeypatch):
    client = TestClient(create_app(str(tmp_path)))
    platform = client.app.state.platform
    seen: dict[str, str] = {"system": ""}

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class):
        seen["system"] = system
        return RouteResult(LLMResponse(text="Done."), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)
    return client, seen


def test_chat_auto_tools_arms_and_discloses(tmp_path, monkeypatch):
    client, seen = _client_with_capture(tmp_path, monkeypatch)
    r = client.post(
        "/chat",
        json={
            "messages": [
                {"role": "user", "content": "search the web for the latest FastAPI release"}
            ],
            "auto_tools": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "web_search" in body["auto_armed"]
    # The model is told these were auto-selected, not user-armed.
    assert "Auto-selected from this request" in seen["system"]
    assert "The user armed these tools" not in seen["system"]


def test_chat_auto_tools_off_by_default(tmp_path, monkeypatch):
    client, seen = _client_with_capture(tmp_path, monkeypatch)
    r = client.post(
        "/chat",
        json={
            "messages": [
                {"role": "user", "content": "search the web for the latest FastAPI release"}
            ],
        },
    )
    assert r.status_code == 200
    assert r.json()["auto_armed"] == []
    assert "Auto-selected" not in seen["system"]


def test_chat_explicit_tools_ride_first_and_are_not_repeated(tmp_path, monkeypatch):
    client, seen = _client_with_capture(tmp_path, monkeypatch)
    r = client.post(
        "/chat",
        json={
            "messages": [
                {"role": "user", "content": "search the web for image benchmarks"}
            ],
            "tools": ["web_search"],
            "auto_tools": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert "web_search" not in body["auto_armed"]  # explicit pick, not re-armed
    assert "The user armed these tools for this chat: web_search" in seen["system"]


def test_chat_plain_message_with_auto_stays_toolless(tmp_path, monkeypatch):
    client, seen = _client_with_capture(tmp_path, monkeypatch)
    r = client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "tell me a short joke"}],
            "auto_tools": True,
        },
    )
    assert r.status_code == 200
    assert r.json()["auto_armed"] == []
    assert "# Tools" not in seen["system"]
