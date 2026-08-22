"""Doors (v1.199.0) — honest links into the surface a chat turn just changed.

Contract under test (agreed with the frontend):

* every turn's response payload carries ``"doors": [{"href", "label"}, ...]``
  — deduped by href, capped at 4, present (possibly EMPTY) in BOTH lanes;
* a door is derived ONLY from a tool call that actually executed ok — the
  gate is the same ``if ran:`` block that appends to ``tools_used``, so a
  FAILED call must never mint one (integration-proven, not just unit-proven);
* every catalog tool name exists in the REAL registry (the drift guard — a
  door for a tool that does not exist is a silent no-show forever);
* a saved thread message round-trips ``doors`` verbatim (the PUT stores
  unknown message fields untouched, the same way route/tools_used persist),
  so doors survive a reload.

Fully offline; the mock-provider/_force_calls harness is the same one
``test_chat_workflows_v1170.py`` uses for the workflow_run receipt.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.doors import (
    _CATALOG,
    MAX_DOORS,
    collect_doors,
    door_for,
)
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"


def _lane(name: str) -> str:
    return (_SRC / name).read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _capture_complete(platform, monkeypatch, seen: dict, reply: str = "ok"):
    async def fake_complete(*, provider=None, model=None, system, messages,
                            tools, task_class):
        seen["system"] = system
        return RouteResult(LLMResponse(text=reply), "mock", "mock")

    monkeypatch.setattr(platform.router, "complete", fake_complete)


def _capture_stream(platform, seen: dict, reply: str = "ok"):
    async def fake_stream(*, provider=None, model=None, system, messages,
                          tools, session_id=None, task_class=None):
        seen["system"] = system
        yield {"type": "text", "text": reply}
        yield {"type": "final", "response": LLMResponse(text=reply),
               "provider": "mock", "model": "mock"}

    platform.router.stream = fake_stream


def _force_calls(client, monkeypatch, calls_per_round):
    """Make the mock adapter emit chosen tool_calls round by round (the
    test_chat_workflows_v1170 harness)."""
    platform = client.app.state.platform
    real_get = platform.providers.get
    rounds = {"n": 0}

    def spy(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def complete(*, system, messages, tools):
            resp = await real_complete(system=system, messages=messages,
                                       tools=tools)
            i = rounds["n"]
            rounds["n"] += 1
            resp.tool_calls = calls_per_round(i)
            return resp

        adapter.complete = complete
        return adapter

    monkeypatch.setattr(platform.providers, "get", spy)
    return rounds


# --------------------------------------------------------------------------- #
# 1 — door_for: catalog mapping + label enrichment
# --------------------------------------------------------------------------- #


def test_unknown_tool_opens_no_door():
    assert door_for("read_file", SimpleNamespace(data={})) is None
    assert door_for("", None) is None


def test_every_catalog_entry_maps_without_a_result_payload():
    """A tool whose data carries nothing still opens its plain door."""
    for name, spec in _CATALOG.items():
        door = door_for(name, SimpleNamespace(data=None))
        assert door == {"href": spec["href"], "label": spec["label"]}, name


def test_result_without_a_data_attribute_still_opens_the_plain_door():
    door = door_for("workflow_create", object())
    assert door == {
        "href": "/workflows",
        "label": "Open the canvas — your workflow is saved there",
    }


def test_workflow_create_label_is_enriched_with_the_saved_name():
    door = door_for(
        "workflow_create",
        SimpleNamespace(data={"name": "nightly-books", "steps": 2, "id": "w1"}),
    )
    assert door == {
        "href": "/workflows",
        "label": "Open the canvas — 'nightly-books' is saved there",
    }


def test_entity_names_never_reach_the_href():
    """Page-level hrefs only — no target page parses entity ids yet, and a
    lying deep-link is worse than a page-level one."""
    for name in _CATALOG:
        door = door_for(name, SimpleNamespace(data={"name": "abc", "slug": "abc"}))
        assert door["href"] == _CATALOG[name]["href"], name
        assert "abc" not in door["href"], name


def test_hostile_names_are_flattened_and_clipped_in_the_label():
    """Names are stored VERBATIM by their tools (the saved-workflows prompt
    block learned this the hard way) — the label must be one bounded line."""
    door = door_for(
        "workflow_create",
        SimpleNamespace(data={"name": "evil\n# header\t" + "x" * 200}),
    )
    assert "\n" not in door["label"] and "\t" not in door["label"]
    assert len(door["label"]) < 120
    assert door["label"].startswith("Open the canvas — 'evil # header")


def test_tools_whose_data_has_no_human_name_keep_the_plain_label():
    # ltm_append's data is {ref, source}; remember_preference's {id, weight,
    # scope} — neither is a human name, so neither label is enriched.
    assert door_for(
        "ltm_append", SimpleNamespace(data={"ref": "brain/note.md", "source": "brain"})
    ) == {"href": "/memory?scope=longterm", "label": "See what it remembered"}
    assert door_for(
        "remember_preference", SimpleNamespace(data={"id": "p1", "weight": 3})
    ) == {"href": "/memory?scope=lessons", "label": "See the lesson it saved"}


def test_door_for_never_raises_on_a_hostile_result():
    class _Bomb:
        @property
        def data(self):
            raise RuntimeError("boom")

    door = door_for("workflow_create", _Bomb())
    assert door["href"] == "/workflows"  # plain label beats no reply


# --------------------------------------------------------------------------- #
# 2 — collect_doors: dedupe, cap, junk tolerance
# --------------------------------------------------------------------------- #


def test_collect_dedupes_by_href_first_seen_wins():
    doors = collect_doors([
        {"href": "/workflows", "label": "first"},
        {"href": "/schedules", "label": "sched"},
        {"href": "/workflows", "label": "second"},
    ])
    assert doors == [
        {"href": "/workflows", "label": "first"},
        {"href": "/schedules", "label": "sched"},
    ]


def test_collect_caps_at_four():
    entries = [{"href": f"/p{i}", "label": f"l{i}"} for i in range(7)]
    doors = collect_doors(entries)
    assert len(doors) == MAX_DOORS == 4
    assert [d["href"] for d in doors] == ["/p0", "/p1", "/p2", "/p3"]


def test_collect_skips_none_and_junk_entries():
    # Call sites append door_for(...) unconditionally — None is the common case.
    assert collect_doors([None, "junk", {}, {"href": "", "label": "x"},
                          {"href": "/tools", "label": "t"}, None]) == [
        {"href": "/tools", "label": "t"}
    ]


def test_collect_handles_empty_and_none_input():
    assert collect_doors([]) == []
    assert collect_doors(None) == []


# --------------------------------------------------------------------------- #
# 3 — the drift guard: every catalog name is a REAL registered tool
# --------------------------------------------------------------------------- #


def test_every_catalog_tool_exists_in_the_live_registry(client):
    """A catalog entry for a tool the registry does not hold is dead code at
    best and a silent no-show at worst — this pins each name to the platform
    the app actually boots."""
    registry = client.app.state.platform.registry
    for name in _CATALOG:
        assert registry.get(name) is not None, (
            f"doors catalog names '{name}' but the live registry has no such "
            "tool — the door can never open"
        )


def test_catalog_hrefs_are_page_level_paths():
    for name, spec in _CATALOG.items():
        href = spec["href"]
        assert href.startswith("/"), name
        path = href.split("?", 1)[0]
        # One path segment — page-level, no entity ids baked in.
        assert re.fullmatch(r"/[a-z-]+", path), (name, href)


# --------------------------------------------------------------------------- #
# 4 — integration: the gate lives at the call site, in BOTH lanes
# --------------------------------------------------------------------------- #

_WF_ARGS = {"name": "nightly-books", "steps": [{"name": "S1", "task": "t1"}]}
_WF_DOOR = {
    "href": "/workflows",
    "label": "Open the canvas — 'nightly-books' is saved there",
}


def _chat_with_workflow_create(client, monkeypatch, *, args=_WF_ARGS,
                               calls=None):
    _force_calls(
        client, monkeypatch,
        calls or (
            lambda i: (
                [ToolCall(id="tc1", name="workflow_create", arguments=args)]
                if i == 0
                else []
            )
        ),
    )
    return client.post(
        "/chat",
        json={
            "messages": [{"role": "user", "content": "save my nightly flow"}],
            "tools": ["workflow_create"],
        },
    )


def test_successful_create_opens_a_door_in_the_nonstream_lane(client, monkeypatch):
    body = _chat_with_workflow_create(client, monkeypatch).json()
    assert body["doors"] == [_WF_DOOR]
    assert "workflow_create" in body["tools_used"]


def test_failed_tool_call_opens_no_door(client, monkeypatch):
    """THE honesty gate: workflow_create with no name returns ok=False, so
    the turn ran the tool loop, the call failed, and no door may appear."""
    body = _chat_with_workflow_create(
        client, monkeypatch, args={"steps": [{"name": "S1", "task": "t1"}]}
    ).json()
    assert body["doors"] == []
    assert "workflow_create" not in body["tools_used"]


def test_turn_without_tools_still_carries_the_empty_key(client, monkeypatch):
    seen: dict = {}
    _capture_complete(client.app.state.platform, monkeypatch, seen)
    body = client.post(
        "/chat", json={"messages": [{"role": "user", "content": "hello"}]}
    ).json()
    assert body["doors"] == []  # present and empty, never absent


def test_two_creates_in_one_turn_dedupe_to_one_door(client, monkeypatch):
    body = _chat_with_workflow_create(
        client, monkeypatch,
        calls=lambda i: (
            [
                ToolCall(id="a", name="workflow_create", arguments=_WF_ARGS),
                ToolCall(id="b", name="workflow_create",
                         arguments={**_WF_ARGS, "name": "second-flow"}),
            ]
            if i == 0
            else []
        ),
    ).json()
    assert body["tools_used"].count("workflow_create") == 2
    assert body["doors"] == [_WF_DOOR]  # first-seen label wins, one href


def _stream_done(client, payload):
    with client.stream("POST", "/chat/stream", json=payload) as r:
        assert r.status_code == 200
        done = None
        for line in r.iter_lines():
            if line.startswith("data: "):
                frame = json.loads(line[6:])
                if "escalate" in frame:  # only the done frame carries this
                    done = frame
    assert done is not None, "no done frame arrived"
    return done


def test_stream_done_frame_carries_the_same_door(client, monkeypatch):
    _force_calls(
        client, monkeypatch,
        lambda i: (
            [ToolCall(id="tc1", name="workflow_create", arguments=_WF_ARGS)]
            if i == 0
            else []
        ),
    )
    done = _stream_done(client, {
        "messages": [{"role": "user", "content": "save my nightly flow"}],
        "tools": ["workflow_create"],
    })
    assert done["doors"] == [_WF_DOOR]
    assert "workflow_create" in done["tools_used"]


def test_stream_done_frame_carries_the_empty_key_when_nothing_ran(client):
    seen: dict = {}
    _capture_stream(client.app.state.platform, seen)
    done = _stream_done(
        client, {"messages": [{"role": "user", "content": "hello"}]}
    )
    assert done["doors"] == []


def test_stream_failed_call_opens_no_door(client, monkeypatch):
    _force_calls(
        client, monkeypatch,
        lambda i: (
            [ToolCall(id="tc1", name="workflow_create",
                      arguments={"steps": []})]  # no name -> ok=False
            if i == 0
            else []
        ),
    )
    done = _stream_done(client, {
        "messages": [{"role": "user", "content": "save it"}],
        "tools": ["workflow_create"],
    })
    assert done["doors"] == []


# --------------------------------------------------------------------------- #
# 5 — lock-step source pins (both lanes, byte-identical mechanism)
# --------------------------------------------------------------------------- #


def test_door_collection_is_in_both_lanes():
    """A door in one lane only is the exact v1.144.0-class bug this repo
    documents — pin the append AND the payload key in each lane's source."""
    for name in ("chat_turn.py", "routes/chat.py"):
        src = _lane(name)
        assert "door_entries.append(door_for(tc.name, result))" in src, (
            f"{name} lost the door append"
        )
        assert '"doors": collect_doors(door_entries),' in src, (
            f"{name} lost the doors payload key"
        )
        # The append must sit INSIDE the executed-ok gate, right where
        # tools_used is recorded — that placement IS the honesty guarantee.
        at = src.index("door_entries.append(door_for(tc.name, result))")
        gate = src.rindex("tools_used.append(tc.name)", 0, at)
        assert at - gate < 600, (
            f"{name}: the door append drifted away from the tools_used gate"
        )


# --------------------------------------------------------------------------- #
# 6 — persistence: doors ride the saved thread message and survive a reload
# --------------------------------------------------------------------------- #


def test_doors_round_trip_through_the_thread_store(client):
    """The autosave PUT stores unknown message fields verbatim (the same
    contract route/tools_used persist under) — prove doors survive it."""
    msgs = [
        {"role": "user", "content": "save my nightly flow"},
        {
            "role": "assistant",
            "content": "Saved.",
            "tools_used": ["workflow_create"],
            "doors": [_WF_DOOR],
        },
    ]
    r = client.put("/chat/threads/new", json={"messages": msgs})
    assert r.status_code == 200
    tid = r.json()["id"]
    stored = client.get(f"/chat/threads/{tid}").json()["messages"]
    assert stored[1]["doors"] == [_WF_DOOR]
    assert stored[1]["tools_used"] == ["workflow_create"]
