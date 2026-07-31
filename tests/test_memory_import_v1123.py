"""v1.123.0 — importing another model's memories (paste + export lanes).

The parser is DETERMINISTIC (offline-safe); export extraction recognizes the
real provider shapes; distillation is owned by the route (tested there with
the honest-offline rule: identity facts are never fabricated).
"""

from __future__ import annotations

import json
import zipfile

from iron_jarvis.memory.importers import (
    MAX_FACTS,
    extract_export_text,
    parse_memory_dump,
)


# --------------------------------------------------------------------------- #
# the paste lane — every list shape assistants actually produce
# --------------------------------------------------------------------------- #


def test_parses_chatgpt_style_numbered_dump():
    text = """Here's everything I remember about you:

1. You are a CPA running a tax firm in Virginia.
2. You prefer concise answers without preamble.
3. **Projects**: You are building a local-first AI OS called Iron Jarvis.
"""
    facts = parse_memory_dump(text)
    assert len(facts) == 3
    assert facts[0] == "You are a CPA running a tax firm in Virginia."
    assert "Iron Jarvis" in facts[2]
    assert "**" not in facts[2]  # markdown bold stripped


def test_parses_bullets_with_sections_and_continuations():
    text = """Work:
- Runs a small accounting practice
- Uses Lacerte for tax prep
    and QuickBooks for bookkeeping

Preferences:
* Wants answers in plain English
"""
    facts = parse_memory_dump(text)
    assert facts[0] == "Work: Runs a small accounting practice"
    assert "QuickBooks" in facts[1]  # continuation merged
    assert facts[2].startswith("Preferences: Wants answers")


def test_prose_returns_empty_so_the_route_distills_instead():
    prose = (
        "Well, from our conversations I know quite a bit about you overall. "
        "You seem to work in accounting and you often ask about tax software, "
        "and you generally like short replies. " * 3
    )
    assert parse_memory_dump(prose) == []


def test_dump_caps_hold():
    text = "\n".join(f"- fact number {i} about the user" for i in range(500))
    assert len(parse_memory_dump(text)) == MAX_FACTS


# --------------------------------------------------------------------------- #
# the export lane — provider shapes
# --------------------------------------------------------------------------- #


def _chatgpt_zip(tmp_path):
    convs = [
        {
            "title": "Tax season prep",
            "mapping": {
                "a": {
                    "message": {
                        "author": {"role": "user"},
                        "content": {"parts": ["I run a CPA firm and need to plan Q1"]},
                    }
                },
                "b": {
                    "message": {
                        "author": {"role": "assistant"},
                        "content": {"parts": ["Sure — here's a plan."]},
                    }
                },
            },
        }
    ]
    p = tmp_path / "chatgpt-export.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("conversations.json", json.dumps(convs))
        z.writestr("user.json", json.dumps({"id": "u"}))
    return p


def test_chatgpt_export_zip_extracts_user_turns_only(tmp_path):
    text, detected = extract_export_text(_chatgpt_zip(tmp_path))
    assert detected == "chatgpt"
    assert "I run a CPA firm" in text
    assert "here's a plan" not in text  # assistant turns don't carry user facts


def test_claude_export_detected(tmp_path):
    convs = [
        {
            "name": "Ledger help",
            "chat_messages": [
                {"sender": "human", "text": "My firm has 40 clients on Karbon"},
                {"sender": "assistant", "text": "Noted."},
            ],
        }
    ]
    p = tmp_path / "conversations.json"
    p.write_text(json.dumps(convs), encoding="utf-8")
    text, detected = extract_export_text(p)
    assert detected == "claude"
    assert "Karbon" in text
    assert "Noted." not in text


def test_unrecognizable_file_fails_honestly(tmp_path):
    p = tmp_path / "photo.json"
    p.write_text(json.dumps({"pixels": [1, 2, 3]}), encoding="utf-8")
    import pytest

    with pytest.raises(ValueError) as e:
        extract_export_text(p)
    assert "couldn't find conversations" in str(e.value)


# --------------------------------------------------------------------------- #
# the routes: preview (nothing saved) → commit (provenance-tagged base)
# --------------------------------------------------------------------------- #

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.providers.adapters.base import LLMResponse


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


DUMP = "- User is a CPA in Virginia\n- User prefers concise answers\n- User runs a firm"


def test_preview_parses_offline_and_saves_nothing(client):
    r = client.post(
        "/memory/import/preview", json={"text": DUMP, "provider": "chatgpt"}
    )
    assert r.status_code == 200
    out = r.json()
    assert out["count"] == 3 and out["distilled"] is False
    # Suggest-don't-act: preview must not have written anything.
    hits = client.get("/ltm/search", params={"q": "CPA Virginia"}).json()
    blob = json.dumps(hits)
    assert "chatgpt-memories" not in blob


def test_prose_offline_refuses_with_the_working_alternative(client):
    prose = "Honestly I know a lot about you from our chats over the years. " * 5
    r = client.post("/memory/import/preview", json={"text": prose})
    assert r.status_code == 400
    assert "paste your model's memory LIST" in r.json()["detail"]


def test_export_distills_via_real_adapter(client, tmp_path):
    class _FakeAdapter:
        provider = "anthropic"
        model = "claude-opus-4-8"

        async def complete(self, *, system, messages, tools=None):
            assert "NEVER invent" in system
            return LLMResponse(
                text="- User is a tax professional\n- User plans Q1 workloads"
            )

    p = _chatgpt_zip(tmp_path)
    client.app.state.platform.providers.get = lambda pr, m=None: _FakeAdapter()
    r = client.post(
        "/memory/import/preview",
        json={"path": str(p), "llm_provider": "anthropic"},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["distilled"] is True
    assert out["provider"] == "chatgpt"  # detected from the export shape
    assert out["count"] == 2


def test_commit_creates_provenance_base_and_recall_sees_it(client):
    items = [c["text"] for c in client.post(
        "/memory/import/preview", json={"text": DUMP, "provider": "chatgpt"}
    ).json()["candidates"]]
    r = client.post(
        "/memory/import/commit", json={"items": items, "provider": "chatgpt"}
    )
    assert r.status_code == 200
    assert r.json() == {"added": 3, "source": "chatgpt-memories"}
    # The base is LIVE (no restart) and provenance rides every note.
    hits = client.get(
        "/ltm/search", params={"q": "concise answers", "source": "chatgpt-memories"}
    ).json()
    blob = json.dumps(hits)
    assert "From chatgpt" in blob
    # A second import REUSES the base.
    r2 = client.post(
        "/memory/import/commit", json={"items": ["User loves spreadsheets"], "provider": "chatgpt"}
    )
    assert r2.json()["source"] == "chatgpt-memories"


def test_preview_flags_already_known_facts(client):
    client.post(
        "/memory/import/commit",
        json={"items": ["User is a CPA in Virginia"], "provider": "chatgpt"},
    )
    out = client.post(
        "/memory/import/preview",
        json={"text": "- User is a CPA in Virginia\n- User has a dog named Rex\n- User bikes on weekends"},
    ).json()
    flags = {c["text"]: c["duplicate"] for c in out["candidates"]}
    assert flags["User is a CPA in Virginia"] is True
    assert flags["User has a dog named Rex"] is False


def test_commit_requires_a_selection(client):
    assert (
        client.post("/memory/import/commit", json={"items": [], "provider": "x"}).status_code
        == 400
    )
