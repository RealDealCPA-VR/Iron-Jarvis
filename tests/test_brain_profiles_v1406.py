"""Brain Profiles (v1.406.0) — a chosen memory base is the user's durable brain.

Driven through the REAL app (both chat lanes, an agent session) with the
built-in folder base (`brain`) and fake connectors — never the user's real
Notion or Obsidian. What each test pins:

* off is byte-identical (no block, no tools, ``brain: null``);
* the block reaches BOTH chat lanes and an agent run, before the planner,
  with the trusted operating guide as instructions and retrieved notes FENCED;
* the one resolve rule (project "off" / project id / the app's choice);
* saves: update-not-duplicate, read back, code knowledge refused, read-only
  refused, "ask" cards, a down base keeps an unsaved change (+ Retry);
* brain_change asks EVERY time, Allow once only; a folder delete is a move.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.brain import profiles as P
from iron_jarvis.brain import turn as T
from iron_jarvis.brain.tools import BrainChangeTool, BrainSaveTool, BrainSearchTool
from iron_jarvis.daemon.app import create_app
from iron_jarvis.ltm.base import MarkdownDirConnector
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext
from tests.test_chat_turn_stop_v1241 import _drive_stream

GUIDE_MARK = "GUIDE-MARK-always-cite-the-client-file"
NOTE_MARK = "NOTE-MARK-acme-retainer-renews-in-march"
HEADER = "# Your durable brain"


@pytest.fixture(autouse=True)
def _fresh_guides():
    T.forget_guides()
    yield
    T.forget_guides()


def _app(tmp_path):
    app = create_app(str(tmp_path))
    return app, TestClient(app), app.state.platform


def _seed(platform):
    platform.ltm.append("operating guide", GUIDE_MARK, "brain")
    platform.ltm.append("acme retainer", NOTE_MARK, "brain")


def _profile(client, **over):
    body = {"name": "Work brain", "primary": "brain", "guide_ref": "operating guide", **over}
    r = client.post("/brain/profiles", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _choose(client, pid):
    r = client.put("/settings/values", json={"values": {"brain_profile": pid}})
    assert r.status_code == 200, r.text


def _capture_complete(seen: dict, script=None):
    rounds = {"n": 0}

    async def fake_complete(*, provider=None, model=None, system, messages, tools, task_class=None, **kw):
        rounds["n"] += 1
        seen.setdefault("systems", []).append(system)
        seen.setdefault("tools", []).append(
            [getattr(t, "name", None) or (t.get("name") if isinstance(t, dict) else "") for t in tools or []]
        )
        if script and rounds["n"] == 1:
            return RouteResult(LLMResponse(text="", tool_calls=script), "mock", "mock")
        return RouteResult(LLMResponse(text="Done."), "mock", "mock")

    return fake_complete


def _ctx(platform, project_id=None, session_id="chat"):
    return ToolContext(
        workspace=Path(platform.config.home), session_id=session_id, agent_run_id="r",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
        project_id=project_id,
    )


# --------------------------------------------------------------------------- #
# Off is byte-identical
# --------------------------------------------------------------------------- #


def test_with_no_profile_nothing_changes(tmp_path, monkeypatch):
    app, client, platform = _app(tmp_path)
    _seed(platform)
    seen: dict = {}
    monkeypatch.setattr(platform.router, "complete", _capture_complete(seen))
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "acme retainer?"}], "auto_tools": False})
    assert r.status_code == 200, r.text
    assert r.json()["brain"] is None
    assert all(HEADER not in s for s in seen["systems"])
    assert all(not any(n.startswith("brain_") for n in ts) for ts in seen["tools"])


# --------------------------------------------------------------------------- #
# Profiles: CRUD + validation + the one resolve rule
# --------------------------------------------------------------------------- #


def test_profile_routes_validate_in_plain_words(tmp_path):
    app, client, platform = _app(tmp_path)
    bad = client.post("/brain/profiles", json={"name": "x", "primary": "nope"})
    assert bad.status_code == 400 and "no memory base called 'nope'" in bad.json()["detail"]
    assert client.post("/brain/profiles", json={"name": "", "primary": "brain"}).status_code == 400
    assert client.post("/brain/profiles", json={"name": "x", "save_to": "cloud"}).status_code == 400
    assert client.post("/brain/profiles", json={"name": "x", "skills": ["no-such-skill"]}).status_code == 400
    assert client.post("/brain/profiles", json={"name": "x", "bogus": 1}).status_code == 400
    p = _profile(client)
    assert p["primary"] == "brain" and p["save_to"] == "primary" and p["search_first"] is True
    view = client.get("/brain").json()
    assert [x["id"] for x in view["profiles"]] == [p["id"]]
    assert any(b["name"] == "brain" and b["label"] == "Iron Jarvis memory" for b in view["bases"])
    upd = client.patch(f"/brain/profiles/{p['id']}", json={"save_to": "ask"})
    assert upd.status_code == 200 and upd.json()["save_to"] == "ask"
    assert client.patch("/brain/profiles/bp_missing", json={"name": "y"}).status_code == 404


def test_resolve_project_off_project_id_and_app_choice(tmp_path):
    app, client, platform = _app(tmp_path)
    a = _profile(client, name="App brain")
    b = _profile(client, name="Client brain")
    proj = client.post("/projects", json={"name": "Acme"}).json()
    pid = proj["id"]
    assert P.resolve(platform, pid) is None
    _choose(client, a["id"])
    assert P.resolve(platform, pid).profile["id"] == a["id"]
    assert P.resolve(platform, pid).source == "app"
    assert client.patch(f"/projects/{pid}", json={"brain_profile": b["id"]}).status_code == 200
    got = P.resolve(platform, pid)
    assert got.profile["id"] == b["id"] and got.source == "project"
    assert client.get(f"/brain/effective?project_id={pid}").json()["source"] == "project"
    assert client.patch(f"/projects/{pid}", json={"brain_profile": "off"}).status_code == 200
    assert P.resolve(platform, pid) is None
    assert client.get(f"/brain/effective?project_id={pid}").json() == {"profile": None, "source": "off"}
    bad = client.patch(f"/projects/{pid}", json={"brain_profile": "bp_gone"})
    assert bad.status_code == 400
    # Deleting a profile clears every choice that named it.
    client.patch(f"/projects/{pid}", json={"brain_profile": b["id"]})
    out = client.delete(f"/brain/profiles/{b['id']}").json()
    assert out["cleared"]["projects"] == 1
    assert P.resolve(platform, pid).profile["id"] == a["id"]  # back to the app's
    out = client.delete(f"/brain/profiles/{a['id']}").json()
    assert out["cleared"]["app"] is True
    assert platform.config.brain_profile == ""
    assert P.resolve(platform, pid) is None


def test_the_setting_refuses_a_shape_that_is_not_a_profile(tmp_path):
    app, client, platform = _app(tmp_path)
    r = client.put("/settings/values", json={"values": {"brain_profile": "../etc"}})
    assert r.status_code == 400


# --------------------------------------------------------------------------- #
# The block reaches every seam
# --------------------------------------------------------------------------- #


def test_post_lane_carries_the_block_guide_fenced_notes_tools_and_receipt(tmp_path, monkeypatch):
    app, client, platform = _app(tmp_path)
    _seed(platform)
    p = _profile(client)
    _choose(client, p["id"])
    seen: dict = {}
    monkeypatch.setattr(platform.router, "complete", _capture_complete(seen))
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "acme retainer"}], "auto_tools": False})
    assert r.status_code == 200, r.text
    system = seen["systems"][0]
    assert HEADER in system and GUIDE_MARK in system
    # The trusted guide is NOT fenced; the retrieved note IS.
    guide_at = system.index(GUIDE_MARK)
    note_at = system.index(NOTE_MARK)
    fence_at = system.index("[UNTRUSTED CONTENT")
    assert guide_at < fence_at < note_at
    assert {"brain_search", "brain_read", "brain_save", "brain_change"} <= set(seen["tools"][0])
    brain = r.json()["brain"]
    assert brain["name"] == "Work brain" and brain["base"] == "Iron Jarvis memory"
    assert brain["guide"] == "loaded" and brain["searched"] >= 1
    assert list(r.json().keys())[-1] == "brain"


@pytest.mark.asyncio
async def test_stream_lane_carries_the_same_block_and_receipt(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    _seed(platform)
    client = TestClient(app)
    p = _profile(client)
    _choose(client, p["id"])
    captured: dict = {}

    async def fake_stream(*, provider=None, model=None, system, messages, tools, session_id=None, task_class=None, **kw):
        captured["system"] = system
        captured["tools"] = [getattr(t, "name", None) or t.get("name") for t in tools or []]
        yield {"type": "text", "text": "ok"}
        yield {"type": "final", "response": LLMResponse(text="ok"), "provider": "mock", "model": "mock"}

    platform.router.stream = fake_stream
    frames = await _drive_stream(app, {"messages": [{"role": "user", "content": "acme retainer"}], "auto_tools": False})
    done = [d for ev, d in frames if ev == "done"][0]
    assert HEADER in captured["system"] and GUIDE_MARK in captured["system"] and NOTE_MARK in captured["system"]
    assert "brain_save" in captured["tools"]
    assert done["brain"]["guide"] == "loaded"


def test_an_agent_run_gets_the_block_and_the_tools(tmp_path):
    app, client, platform = _app(tmp_path)
    _seed(platform)
    p = _profile(client)
    _choose(client, p["id"])
    seen: dict = {}
    real_get = platform.providers.get

    def spy_get(prov, m=None):
        adapter = real_get(prov, m)
        real = adapter.complete

        async def spy(*, system, messages, tools):
            seen.setdefault("systems", []).append(system)
            seen.setdefault("tools", []).append([t.get("name") if isinstance(t, dict) else getattr(t, "name", "") for t in tools or []])
            return await real(system=system, messages=messages, tools=tools)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get
    assert client.post("/sessions", json={"task": "acme retainer notes", "wait": True}).status_code == 200
    assert any(HEADER in s and GUIDE_MARK in s for s in seen["systems"])
    assert any("brain_search" in ts for ts in seen["tools"])


def test_low_trust_keeps_the_writers_away(tmp_path, monkeypatch):
    app, client, platform = _app(tmp_path)
    p = _profile(client)
    _choose(client, p["id"])
    from iron_jarvis.core.trust import LOW_TRUST_DENY

    assert {"brain_save", "brain_change"} <= LOW_TRUST_DENY


# --------------------------------------------------------------------------- #
# Saves
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_save_updates_an_existing_note_and_is_read_back(tmp_path):
    app, client, platform = _app(tmp_path)
    p = _profile(client, guide_ref="")
    _choose(client, p["id"])
    tool = BrainSaveTool(platform)
    ctx = _ctx(platform)
    r1 = await tool.execute({"title": "Acme Retainer", "content": "Renews in March."}, ctx)
    assert r1.ok and r1.data["brain"]["action"] == "created" and r1.data["brain"]["verified"] is True
    r2 = await tool.execute({"title": "acme retainer", "content": "Fee is 2,000."}, ctx)
    assert r2.ok and r2.data["brain"]["action"] == "updated", r2
    notes = sorted(Path(platform.config.home, "brain").glob("*.md"))
    assert [n.name for n in notes] == ["acme-retainer.md"]
    text = notes[0].read_text(encoding="utf-8")
    assert "Renews in March." in text and "Fee is 2,000." in text


@pytest.mark.asyncio
async def test_code_knowledge_and_read_only_profiles_are_refused(tmp_path):
    app, client, platform = _app(tmp_path)
    p = _profile(client, guide_ref="")
    _choose(client, p["id"])
    tool = BrainSaveTool(platform)
    ctx = _ctx(platform)
    code = await tool.execute({"title": "build", "content": "run pnpm build", "about": "code"}, ctx)
    assert not code.ok and "repository" in code.error
    client.patch(f"/brain/profiles/{p['id']}", json={"repo_routing": False})
    ok = await tool.execute({"title": "build", "content": "run pnpm build", "about": "code"}, ctx)
    assert ok.ok
    client.patch(f"/brain/profiles/{p['id']}", json={"save_to": "never"})
    ro = await tool.execute({"title": "x", "content": "y"}, ctx)
    assert not ro.ok and "read-only" in ro.error


@pytest.mark.asyncio
async def test_ask_profiles_card_every_save_and_change_always_asks_once(tmp_path):
    app, client, platform = _app(tmp_path)
    p = _profile(client, guide_ref="")
    _choose(client, p["id"])
    ctx = _ctx(platform)
    save = BrainSaveTool(platform)
    assert await save.spend_check({"title": "t", "content": "c"}, ctx) is None
    client.patch(f"/brain/profiles/{p['id']}", json={"save_to": "ask"})
    chk = await save.spend_check({"title": "t", "content": "c"}, ctx)
    assert chk is not None and chk.ask and "Iron Jarvis memory" in chk.words
    change = BrainChangeTool(platform)
    c = await change.spend_check({"action": "delete", "ref": "t"}, ctx)
    assert c.ask and c.once_only and c.words.startswith("Delete")


@pytest.mark.asyncio
async def test_a_folder_delete_is_a_move_to_the_trash(tmp_path):
    app, client, platform = _app(tmp_path)
    p = _profile(client, guide_ref="")
    _choose(client, p["id"])
    ctx = _ctx(platform)
    await BrainSaveTool(platform).execute({"title": "old idea", "content": "drop me"}, ctx)
    res = await BrainChangeTool(platform).execute({"action": "delete", "ref": "old idea"}, ctx)
    assert res.ok, res
    folder = Path(platform.config.home, "brain")
    assert not (folder / "old-idea.md").exists()
    assert (folder / ".trash" / "old-idea.md").exists()
    found = await BrainSearchTool(platform).execute({"query": "drop me"}, ctx)
    assert "old idea" not in found.output


def test_a_note_outside_the_folder_is_never_read(tmp_path):
    folder = tmp_path / "vault"
    folder.mkdir()
    (tmp_path / "secret.md").write_text("no", encoding="utf-8")
    conn = MarkdownDirConnector(folder, create=False)
    with pytest.raises(ValueError):
        conn.read(str(tmp_path / "secret.md"))
    with pytest.raises(ValueError):
        conn.read("../secret.md")


@pytest.mark.asyncio
async def test_a_base_that_is_down_keeps_an_unsaved_change_and_retry_saves_it(tmp_path):
    app, client, platform = _app(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    r = client.post("/ltm/sources", json={"name": "workvault", "kind": "markdown", "path": str(vault)})
    assert r.status_code in (200, 201), r.text
    p = _profile(client, primary="workvault", guide_ref="")
    _choose(client, p["id"])
    vault.rename(tmp_path / "vault-unplugged")  # the drive went away
    res = await BrainSaveTool(platform).execute({"title": "call notes", "content": "Agreed on Friday."}, _ctx(platform))
    assert not res.ok and "unavailable" in res.error and "unsaved change" in res.error
    assert res.data["brain"]["kind"] == "unsaved"
    pending = client.get("/brain/pending").json()["pending"]
    assert [x["title"] for x in pending] == ["call notes"]
    # Still down: Retry says so and keeps it.
    again = client.post(f"/brain/pending/{pending[0]['id']}/retry")
    assert again.status_code == 409
    assert len(client.get("/brain/pending").json()["pending"]) == 1
    (tmp_path / "vault-unplugged").rename(vault)  # back
    ok = client.post(f"/brain/pending/{pending[0]['id']}/retry")
    assert ok.status_code == 200 and ok.json()["saved"]["verified"] is True
    assert client.get("/brain/pending").json()["pending"] == []
    assert "Agreed on Friday." in (vault / "call-notes.md").read_text(encoding="utf-8")


class _LyingConnector:
    name = "liar"

    def search(self, query, k=5):
        return []

    def append(self, title, content):
        return "page-1"

    def read(self, ref):
        return "something else entirely"


@pytest.mark.asyncio
async def test_a_save_the_base_cannot_show_back_is_never_called_saved(tmp_path):
    app, client, platform = _app(tmp_path)
    platform.ltm.register(_LyingConnector())
    p = _profile(client, primary="liar", guide_ref="")
    _choose(client, p["id"])
    res = await BrainSaveTool(platform).execute({"title": "t", "content": "keep this"}, _ctx(platform))
    assert not res.ok and "Not confirmed" in res.error
    # Not queued: it may have landed, and a retry would duplicate it.
    assert client.get("/brain/pending").json()["pending"] == []


def test_the_post_lane_receipt_says_what_was_saved(tmp_path, monkeypatch):
    app, client, platform = _app(tmp_path)
    p = _profile(client, guide_ref="")
    _choose(client, p["id"])
    seen: dict = {}
    call = ToolCall(id="c1", name="brain_save", arguments={"title": "Lunch", "content": "Likes sushi."})
    monkeypatch.setattr(platform.router, "complete", _capture_complete(seen, script=[call]))
    r = client.post("/chat", json={"messages": [{"role": "user", "content": "remember I like sushi"}], "auto_tools": False})
    assert r.status_code == 200, r.text
    saved = r.json()["brain"]["saved"]
    assert saved and saved[0]["title"] == "Lunch" and saved[0]["verified"] is True
