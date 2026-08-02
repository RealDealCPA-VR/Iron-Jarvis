"""v1.129.0 — the predefined categorized export prompt + structured read-back.

The paste lane grew a contract: EXPORT_PROMPT asks the other model for a
categorized, dated export (Instructions / Identity / Career / Projects /
Preferences, ``[YYYY-MM-DD] -`` lines in a code block), and
parse_categorized_dump reads that reply back DETERMINISTICALLY with the
structure intact — category and original date survive preview and commit.
"""

from __future__ import annotations

from iron_jarvis.memory.importers import (
    CATEGORIES,
    EXPORT_PROMPT,
    parse_categorized_dump,
    parse_memory_dump,
)


# A faithful reply to EXPORT_PROMPT — code block, headers, dated lines,
# completeness sentence after the block.
STRUCTURED_REPLY = """Here is your export:

```
## Instructions

[2025-03-14] - Always answer in plain English without preamble.
[unknown] - Never use emojis in responses.

## Identity

[2024-11-02] - You live in Virginia with your family.

## Career

[2024-11-02] - You are a CPA running a tax firm.

## Projects

[2025-06-20] - Iron Jarvis: a local-first AI OS you build and use daily.
[2025-07-01] - IronCore: your own Codex CLI for open-source models.

## Preferences

[unknown] - You prefer concise, direct answers.
```

This is the complete set of my stored memories about you.
"""


# --------------------------------------------------------------------------- #
# the prompt itself — the ask half of the contract
# --------------------------------------------------------------------------- #


def test_export_prompt_carries_every_category_and_the_format():
    for cat in CATEGORIES:
        assert cat in EXPORT_PROMPT
    assert "[YYYY-MM-DD]" in EXPORT_PROMPT
    assert "[unknown]" in EXPORT_PROMPT
    assert "code block" in EXPORT_PROMPT
    assert "verbatim" in EXPORT_PROMPT


# --------------------------------------------------------------------------- #
# the read-back — deterministic, structure intact
# --------------------------------------------------------------------------- #


def test_parses_the_faithful_reply_with_categories_and_dates():
    entries = parse_categorized_dump(STRUCTURED_REPLY)
    assert len(entries) == 7
    by_cat: dict[str, list[dict]] = {}
    for e in entries:
        by_cat.setdefault(e["category"], []).append(e)
    assert set(by_cat) == set(CATEGORIES)
    assert by_cat["Instructions"][0]["date"] == "2025-03-14"
    assert by_cat["Instructions"][1]["date"] == ""  # [unknown] normalizes to ""
    assert by_cat["Projects"][0]["text"].startswith("Iron Jarvis:")
    # The fence and the completeness sentence are not entries.
    assert not any("complete set" in e["text"] for e in entries)
    assert not any("```" in e["text"] for e in entries)


def test_header_dressing_is_tolerated():
    text = (
        "**Instructions**\n[2025-01-01] - Be terse.\n"
        "3. Career:\n[2025-01-02] - Runs an accounting practice.\n"
    )
    entries = parse_categorized_dump(text)
    assert [e["category"] for e in entries] == ["Instructions", "Career"]


def test_undated_bullets_under_a_category_still_count():
    text = (
        "## Preferences\n"
        "[2025-02-02] - Likes plain English.\n"
        "[2025-02-03] - Dislikes filler.\n"
        "- Prefers spreadsheets over slides\n"
    )
    entries = parse_categorized_dump(text)
    assert len(entries) == 3
    assert entries[2] == {
        "text": "Prefers spreadsheets over slides",
        "category": "Preferences",
        "date": "",
    }


def test_loose_lists_fall_through_to_the_plain_parser():
    loose = "- User is a CPA\n- User prefers concise answers\n- User runs a firm"
    assert parse_categorized_dump(loose) == []
    assert len(parse_memory_dump(loose)) == 3


def test_one_dated_line_is_not_enough_structure():
    # A single [date] line inside otherwise-plain text must not hijack the lane.
    text = "- User is a CPA\n- [2025-01-01] - joined the gym\n- User runs a firm"
    assert parse_categorized_dump(text) == []


# --------------------------------------------------------------------------- #
# the routes — prompt served, structure through preview, provenance on commit
# --------------------------------------------------------------------------- #

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def test_prompt_endpoint_serves_the_canonical_ask(client):
    r = client.get("/memory/import/prompt")
    assert r.status_code == 200
    out = r.json()
    assert out["prompt"] == EXPORT_PROMPT
    assert out["categories"] == list(CATEGORIES)


def test_preview_keeps_category_and_date_and_never_distills_structured(client):
    # Two entries: below the loose lane's distill threshold — structured
    # input must stay exact (offline distillation would 400 here).
    text = (
        "```\n## Projects\n[2025-06-20] - Iron Jarvis: a local-first AI OS.\n"
        "## Preferences\n[2024-01-05] - Prefers concise answers.\n```\n"
    )
    r = client.post("/memory/import/preview", json={"text": text, "provider": "chatgpt"})
    assert r.status_code == 200
    out = r.json()
    assert out["structured"] is True and out["distilled"] is False
    assert out["count"] == 2
    assert out["candidates"][0]["category"] == "Projects"
    assert out["candidates"][0]["date"] == "2025-06-20"
    assert out["candidates"][1]["category"] == "Preferences"


def _imported_dir(client, provider: str):
    # The daemon home is <root>/.ironjarvis (or IRONJARVIS_HOME) — read it
    # off the live platform instead of guessing the layout.
    return client.app.state.platform.config.home / "imported" / provider


def test_commit_stores_category_and_original_date_with_provenance(client, tmp_path):
    entries = [
        {
            "text": "Iron Jarvis: a local-first AI OS the user builds.",
            "category": "Projects",
            "date": "2025-06-20",
        },
        {"text": "Prefers concise answers.", "category": "Preferences", "date": ""},
    ]
    r = client.post(
        "/memory/import/commit", json={"entries": entries, "provider": "claude"}
    )
    assert r.status_code == 200
    assert r.json() == {"added": 2, "source": "claude-memories"}
    notes = {
        p.name: p.read_text(encoding="utf-8")
        for p in _imported_dir(client, "claude").glob("*.md")
    }
    assert len(notes) == 2
    joined = "\n".join(notes.values())
    assert "category: Projects" in joined
    assert "original date: 2025-06-20" in joined
    assert "imported from claude on" in joined
    # The undated entry carries its category but no fabricated date line.
    pref_note = next(t for t in notes.values() if "Prefers concise" in t)
    assert "category: Preferences" in pref_note
    assert "original date" not in pref_note


def test_commit_plain_items_still_work(client):
    r = client.post(
        "/memory/import/commit",
        json={"items": ["User loves spreadsheets"], "provider": "chatgpt"},
    )
    assert r.status_code == 200
    assert r.json() == {"added": 1, "source": "chatgpt-memories"}


def test_full_round_trip_preview_to_commit(client, tmp_path):
    out = client.post(
        "/memory/import/preview",
        json={"text": STRUCTURED_REPLY, "provider": "chatgpt"},
    ).json()
    assert out["structured"] is True
    entries = [
        {"text": c["text"], "category": c["category"], "date": c["date"]}
        for c in out["candidates"]
    ]
    r = client.post(
        "/memory/import/commit", json={"entries": entries, "provider": "chatgpt"}
    )
    assert r.json()["added"] == 7
    joined = "\n".join(
        p.read_text(encoding="utf-8")
        for p in _imported_dir(client, "chatgpt").glob("*.md")
    )
    for cat in CATEGORIES:
        assert f"category: {cat}" in joined
