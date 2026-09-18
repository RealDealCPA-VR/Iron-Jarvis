"""v1.279.0 — the memory the app already had starts working FOR the user.

The daily driver's ledger: 25 lessons, 23 of them the orchestrator's per-run
"Worked well for '<task>': <summary>" reflections; the memory graph, proposals
and steward all at zero; and ``remember_preference`` — the one tool that turns
"from now on keep answers short" into a lesson every later turn reads — never
armed in chat, because it sat in AUTO_SAFE_TOOLS with no rule awarding it.

Four things, each pinned here:
1. Task reflections stay out of the prompt (they still feed dedup/distill and
   the Lessons tab; ``counts_by_source`` reports them).
2. A stated preference arms ``remember_preference`` in chat; a plain question
   does not; the brief rides BOTH lanes only beside the armed tool.
3. ``GET /memory/overview`` says what Jarvis knows — and says "nothing yet".
4. The reasoning level persists with a thread (``_clean_setup`` dropped it).
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import iron_jarvis.learning.models  # noqa: F401  (register tables before init_db)
from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import PREFERENCE_BLOCK, preference_block
from iron_jarvis.daemon.routes.chat import _clean_setup
from iron_jarvis.learning.engine import _PROMPT_EXCLUDED_SOURCES, LearningEngine
from iron_jarvis.memory.overview import memory_overview
from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS, select_auto_tools


@pytest.fixture
def learning(tmp_path):
    e = make_engine(str(tmp_path / "t.db"))
    init_db(e)
    return LearningEngine(e)


# --------------------------------------------------------------------------- #
# 1. Reflections are task notes, not knowledge about the user
# --------------------------------------------------------------------------- #


def test_reflections_stay_out_of_the_prompt_but_in_the_store(learning):
    learning.note_preference("Prefers short answers with numbered steps")
    for i in range(10):
        learning.reflect(f"s{i}", task=f"task {i}", summary="Done. Wrote RESULT.md summarizing the task.", ok=True)
    out = learning.apply_to_prompt("BASE")
    assert "Prefers short answers with numbered steps" in out
    assert "Worked well for" not in out and "RESULT.md" not in out
    # The store keeps them: the Lessons tab and distillation read the pile.
    texts = [l.text for l in learning.lessons(scope="user", limit=50)]
    assert sum(t.startswith("Worked well for") for t in texts) == 10
    assert _PROMPT_EXCLUDED_SOURCES == ("reflection",)
    assert learning.counts_by_source() == {"preference": 1, "reflection": 10}


def test_a_failed_task_note_is_a_reflection_too_and_stays_out(learning):
    learning.reflect("s1", task="ship the report", summary="", ok=False)
    assert learning.apply_to_prompt("BASE") == "BASE"
    learning.record_feedback("s1", "down", "Too verbose")
    out = learning.apply_to_prompt("BASE")
    assert "Too verbose" in out and "revisit the approach" not in out


def test_distilled_and_user_written_lessons_still_reach_the_prompt(learning):
    learning._add_lesson("Keep commits small", source="distilled", weight=2)
    learning._add_lesson("Cite the code section", source="user", weight=3)
    out = learning.apply_to_prompt("BASE")
    assert "Keep commits small" in out and "Cite the code section" in out


# --------------------------------------------------------------------------- #
# 2. A stated preference reaches the tool; the brief rides beside it, lock-step
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text",
    [
        "from now on keep answers short",
        "Going forward, always give me numbered steps",
        "I prefer bullet points over paragraphs",
        "I'd prefer you call me VR",
        "never use emojis in replies",
        "Don't ever sign emails with my full name",
        "please always address me by my first name",
        "my name is Val, by the way",
    ],
)
def test_a_lasting_preference_arms_remember_preference(text):
    assert "remember_preference" in select_auto_tools(text), text
    assert "remember_preference" in AUTO_SAFE_TOOLS


@pytest.mark.parametrize(
    "text",
    [
        "is interest always taxable?",
        "what's a 1099-NEC?",
        "search the web for rental rules in New York",
        "write me a letter about testing an application",
        "never mind, what time is it?",
    ],
)
def test_an_ordinary_ask_does_not(text):
    assert "remember_preference" not in select_auto_tools(text), text


def test_the_brief_exists_only_beside_the_armed_tool():
    assert preference_block({"remember_preference", "read_file"}) == PREFERENCE_BLOCK
    assert preference_block({"read_file"}) == ""
    assert preference_block(set()) == "" and preference_block(None) == ""
    assert "remember_preference" in PREFERENCE_BLOCK
    assert "one-off" in PREFERENCE_BLOCK and "third-person" in PREFERENCE_BLOCK


def _spy_complete(platform, seen: dict):
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools, **kw):
            seen.setdefault("systems", []).append(system)
            return await real_complete(system=system, messages=messages, tools=tools, **kw)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get


def test_both_lanes_carry_the_brief_only_when_the_tool_is_armed(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    seen: dict = {}
    _spy_complete(platform, seen)
    head = PREFERENCE_BLOCK.splitlines()[0]

    # (a) POST /chat — armed explicitly, then a control with a different tool.
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}],
                                   "tools": ["remember_preference"], "auto_tools": False})
    assert r.status_code == 200, r.text
    assert any(head in s for s in seen["systems"]), "chat seam: the brief is missing beside the armed tool"
    seen["systems"].clear()
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "hi"}],
                                   "tools": ["read_file"], "auto_tools": False})
    assert r.status_code == 200, r.text
    assert not any(head in s for s in seen["systems"]), "chat seam: a brief beside no tool"

    # (b) POST /chat/stream — the lock-step copy.
    captured: dict = {}

    async def fake_stream(*, provider=None, model=None, system, messages, tools,
                          session_id=None, task_class=None, **kw):
        captured["system"] = system
        adapter = platform.providers.get(provider or platform.router.default_provider, model)
        async for frame in adapter.stream(system=system, messages=messages, tools=tools):
            if frame.get("type") == "final":
                yield {**frame, "provider": adapter.provider, "model": adapter.model}
            else:
                yield frame

    platform.router.stream = fake_stream
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}],
                                          "tools": ["remember_preference"], "auto_tools": False})
    assert r.status_code == 200
    assert head in captured["system"], "stream seam: the brief is missing"
    r = client.post("/chat/stream", json={"messages": [{"role": "user", "content": "hi"}],
                                          "tools": ["read_file"], "auto_tools": False})
    assert r.status_code == 200
    assert head not in captured["system"], "stream seam: a brief beside no tool"


# --------------------------------------------------------------------------- #
# 3. GET /memory/overview — what Jarvis knows, and "nothing yet"
# --------------------------------------------------------------------------- #


def test_the_overview_says_nothing_yet_on_a_fresh_install_and_never_raises(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    r = client.get("/memory/overview")
    assert r.status_code == 200, r.text
    o = r.json()
    assert o["empty"] is True
    assert o["preferences"] == [] and o["profile"]["filled"] is False
    assert o["lessons"] == {"total": 0, "reflections": 0, "by_source": {}}
    names = [b["name"] for b in o["bases"]]
    assert "brain" in names, names
    brain = next(b for b in o["bases"] if b["name"] == "brain")
    assert brain["notes"] == 0 and brain["kind"]
    assert set(o["working"]) >= {"session", "project", "user", "org"}
    assert set(o["history"]) == {"docs", "available"}

    # A platform with nothing on it still answers: every part is guarded.
    class _Bare:
        pass

    o2 = memory_overview(_Bare())
    assert o2["empty"] is True and o2["bases"] == [] and o2["preferences"] == []


def test_the_overview_reports_the_profile_and_the_preferences_but_not_the_reflections(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    r = client.put("/profile", json={"values": {"about": "# About\n\n- Goes by VR, a CPA.", "tone": "neutral"}})
    assert r.status_code == 200, r.text
    platform.learning.note_preference("Prefers short answers with numbered steps")
    platform.learning.reflect("s1", task="rename files", summary="renamed 12 files", ok=True)
    platform.learning.reflect("s2", task="research", summary="found 3 sources", ok=True)

    o = client.get("/memory/overview").json()
    assert o["empty"] is False
    assert o["profile"]["filled"] is True
    assert o["profile"]["about_line"] == "Goes by VR, a CPA."  # the first real line, headings skipped
    assert o["profile"]["tone"] == "neutral"
    assert [p["text"] for p in o["preferences"]] == ["Prefers short answers with numbered steps"]
    assert o["preferences"][0]["source"] == "preference" and o["preferences"][0]["weight"] == 5
    assert o["lessons"]["total"] == 3 and o["lessons"]["reflections"] == 2
    assert o["lessons"]["by_source"] == {"preference": 1, "reflection": 2}


# --------------------------------------------------------------------------- #
# 4. The reasoning level persists with the thread
# --------------------------------------------------------------------------- #


def test_clean_setup_keeps_a_valid_reasoning_level_and_drops_the_rest():
    assert json.loads(_clean_setup({"reasoning": "High"})) == {"reasoning": "high"}
    assert json.loads(_clean_setup({"reasoning": "extreme", "skill": "x"})) == {"skill": "x"}
    assert _clean_setup({"reasoning": ""}) == ""
    assert _clean_setup({"reasoning": 7}) == ""


def test_the_level_round_trips_through_a_saved_thread(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    r = client.put(
        "/chat/threads/new",
        json={
            "messages": [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
            "setup": {"reasoning": "low", "model": "m", "provider": "p"},
        },
    )
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    got = client.get(f"/chat/threads/{tid}").json()
    assert got["setup"]["reasoning"] == "low", got["setup"]
    assert got["setup"]["model"] == "m"
