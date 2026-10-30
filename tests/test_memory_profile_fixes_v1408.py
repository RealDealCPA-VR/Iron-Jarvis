"""v1.408.0 — the four open points left by v1.406.0's Brain Profiles.

1. An MCP-served memory base's tools are CONFIRMED by the user per role
   (``ltm/mcp_roles``), never guessed for a write. The fake server below has
   the REAL tool list and schemas of the user's own wiki server (hermes-brain,
   read on 2026-10-29 with read-only calls) and answers in its REAL shapes:
   a list arrives as ``structuredContent {"result": [...]}`` plus one text
   block per item, and ``read_page`` answers JSON. The note TEXT is made up.
2. A save the base REFUSED is said as a refusal and never queued; a save that
   LANDED before its answer was lost is recognised, never sent twice (Retry).
3. ``DELETE /ltm/sources/{name}`` unloads the live base; ``/ltm/sources``
   reports a custom base's real kind.
4. A Notion database whose title column is not called "Name" works.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.brain.tools import BrainChangeTool, BrainSaveTool, unreachable, write_note
from iron_jarvis.brain import profiles as P
from iron_jarvis.daemon.app import create_app
from iron_jarvis.ltm import mcp_roles
from iron_jarvis.ltm.mcp_brain import McpBrainConnector
from iron_jarvis.ltm.notion import NotionConnector
from iron_jarvis.tools.base import ToolContext

# The real tool list of the user's wiki server (names, required, types).
HERMES_TOOLS = [
    {"name": "search_wiki", "description": "Full-text search across the wiki.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}},
    {"name": "read_page", "description": "Read a wiki page by vault-relative path.",
     "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}},
    {"name": "create_page", "description": "Create a new wiki page following SCHEMA.md.",
     "inputSchema": {"type": "object", "properties": {
         "body": {"type": "string"}, "related_links": {"type": "array", "items": {}},
         "summary": {"type": "string"}, "tags": {"type": "array", "items": {}},
         "type": {"type": "string"}}, "required": ["type", "summary", "body", "tags"]}},
    {"name": "append_section", "description": "Append a new section to an existing page.",
     "inputSchema": {"type": "object", "properties": {
         "content": {"type": "string"}, "heading": {"type": "string"}, "path": {"type": "string"}},
         "required": ["path", "heading", "content"]}},
    {"name": "update_page", "description": "Update an existing page.",
     "inputSchema": {"type": "object", "properties": {
         "append_text": {"type": "string"}, "new_body": {"type": "string"},
         "note": {"type": "string"}, "path": {"type": "string"}}, "required": ["path"]}},
    {"name": "archive_page", "description": "Non-destructively archive a page.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string"}, "reason": {"type": "string"}}, "required": ["path"]}},
    {"name": "list_pages", "description": "List every page.", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "get_index", "description": "Return index.md.", "inputSchema": {"type": "object", "properties": {}}},
]

CONFIRMED = {
    "search": {"tool": "search_wiki", "args": {"query": "query"}},
    "read": {"tool": "read_page", "args": {"path": "ref"}},
    "create": {"tool": "create_page", "args": {
        "body": "content", "summary": "title", "type": {"const": "concept"}, "tags": {"const": "inbox, notes"}}},
    "append": {"tool": "append_section", "args": {"path": "ref", "heading": "title", "content": "content"}},
    "archive": {"tool": "archive_page", "args": {"path": "ref"}},
}


class FakeWiki:
    """A wiki with the real server's tools and reply SHAPES."""

    def __init__(self, *, fail: "BaseException | None" = None, refuse: str = ""):
        self.pages: dict[str, dict] = {}
        self.calls: list[tuple[str, dict]] = []
        self.fail = fail
        self.refuse = refuse

    def list_tools(self):
        return list(HERMES_TOOLS)

    def _list(self, items):
        return {"content": [{"type": "text", "text": json.dumps(i)} for i in items],
                "structuredContent": {"result": items}, "isError": False}

    def call_tool(self, name, args):
        self.calls.append((name, dict(args)))
        if self.refuse and name in ("create_page", "append_section"):
            return {"content": [{"type": "text", "text": self.refuse}], "isError": True}
        if name == "search_wiki":
            q = args["query"].lower()
            hits = [{"path": p, "name": v["name"], "dir": "concepts", "snippet": v["content"][:80],
                     "name_match": False}
                    for p, v in self.pages.items() if q in (v["name"] + v["content"]).lower()]
            return self._list(hits)
        if name == "read_page":
            page = self.pages.get(args["path"])
            if page is None:
                return {"content": [{"type": "text", "text": "not found"}], "isError": True}
            body = {"path": args["path"], "name": page["name"], "frontmatter": {"title": page["name"]},
                    "content": page["content"], "wikilinks": {}, "backlinks": []}
            return {"content": [{"type": "text", "text": json.dumps(body)}], "isError": False}
        if name == "create_page":
            slug = args["summary"].lower().replace(" ", "-")
            path = f"concepts/{slug}.md"
            self.pages[path] = {"name": slug, "content": f"# {args['summary']}\n\n{args['body']}"}
            if self.fail is not None:
                raise self.fail
            return {"content": [{"type": "text", "text": json.dumps({"path": path})}], "isError": False}
        if name == "append_section":
            page = self.pages[args["path"]]
            page["content"] += f"\n\n## {args['heading']}\n{args['content']}"
            if self.fail is not None:
                raise self.fail
            return {"content": [{"type": "text", "text": "ok"}], "isError": False}
        if name == "archive_page":
            self.pages.pop(args["path"], None)
            return {"content": [{"type": "text", "text": "archived"}], "isError": False}
        return {"content": [{"type": "text", "text": "?"}], "isError": False}


def _wiki_conn(roles=None, **kw):
    fake = FakeWiki(**kw)
    return McpBrainConnector("hermes-brain", client=fake, roles=roles), fake


# --------------------------------------------------------------------------- #
# 1. Reading the real shapes
# --------------------------------------------------------------------------- #


def test_a_list_reply_is_one_hit_per_item_not_one_collapsed_hit():
    conn, fake = _wiki_conn()
    for i in range(3):
        fake.pages[f"concepts/topic-{i}.md"] = {"name": f"topic-{i}", "content": f"about payroll {i}"}
    hits = conn.search("payroll", 5)
    assert [h["ref"] for h in hits] == [f"concepts/topic-{i}.md" for i in range(3)]
    assert hits[0]["title"] == "topic-0"


def test_a_list_wrapped_in_result_in_ONE_text_block_is_read_too():
    """A server that sends the wrapped list as one text block and no
    structuredContent (another SDK's shape)."""
    from iron_jarvis.ltm.mcp_brain import _hits_from_res

    items = [{"path": f"p{i}.md", "name": f"n{i}", "snippet": "s"} for i in range(2)]
    res = {"content": [{"type": "text", "text": json.dumps({"result": items})}]}
    assert [h["ref"] for h in _hits_from_res(res, "w", 5)] == ["p0.md", "p1.md"]


def test_read_returns_the_notes_text_not_the_json_around_it():
    conn, fake = _wiki_conn()
    fake.pages["concepts/x.md"] = {"name": "x", "content": 'He said "keep it short".'}
    assert conn.read("concepts/x.md") == 'He said "keep it short".'


# --------------------------------------------------------------------------- #
# 2. Proposed, never guessed for a write
# --------------------------------------------------------------------------- #


def test_the_proposal_for_the_real_wiki_and_what_it_still_needs():
    prop = mcp_roles.propose(HERMES_TOOLS)
    assert prop["search"] == {"tool": "search_wiki", "args": {"query": "query"}}
    assert prop["read"] == {"tool": "read_page", "args": {"path": "ref"}}
    assert prop["create"]["tool"] == "create_page"
    assert prop["create"]["args"] == {"body": "content", "summary": "title"}
    needs = " ".join(mcp_roles.problems("create", prop["create"], HERMES_TOOLS))
    assert "'type' is required" in needs and "'tags' is required" in needs
    assert prop["append"]["tool"] == "append_section"
    assert prop["append"]["args"] == {"path": "ref", "content": "content", "heading": "title"}
    assert mcp_roles.problems("append", prop["append"], HERMES_TOOLS) == []
    assert prop["archive"]["tool"] == "archive_page"
    assert prop["delete"] is None  # the server deliberately has no delete


def test_a_comment_field_is_never_the_notes_text_and_a_writer_never_reads():
    bad = {"tool": "update_page", "args": {"path": "ref", "note": "content"}}
    assert any("comment field" in p for p in mcp_roles.problems("append", bad, HERMES_TOOLS))
    writer = {"tool": "create_page", "args": {"body": "query"}}
    assert any("never used" in p for p in mcp_roles.problems("search", writer, HERMES_TOOLS))
    with pytest.raises(ValueError):
        mcp_roles.clean({"create": {"tool": "create_page", "args": {"body": "content", "summary": "title"}}},
                        HERMES_TOOLS)
    assert mcp_roles.clean(CONFIRMED, HERMES_TOOLS)["create"]["tool"] == "create_page"


def test_a_fixed_list_value_is_split_on_commas():
    args = mcp_roles.build_args(CONFIRMED["create"], HERMES_TOOLS[2], {"title": "T", "content": "C"})
    assert args == {"body": "C", "summary": "T", "type": "concept", "tags": ["inbox", "notes"]}


def test_nothing_is_written_through_tools_the_user_has_not_confirmed():
    from iron_jarvis.ltm.base import NoteOpUnsupported

    conn, fake = _wiki_conn(roles=None)
    with pytest.raises(NoteOpUnsupported, match="Set up tools"):
        conn.update("concepts/x.md", "text")
    with pytest.raises(NoteOpUnsupported, match="Set up tools"):
        conn.change("archive", "concepts/x.md")
    assert conn.setup_needed("create") and "Set up tools" in conn.setup_needed("create")
    assert not any(n in ("append_section", "update_page", "archive_page") for n, _ in fake.calls)


# --------------------------------------------------------------------------- #
# The brain tools on that wiki
# --------------------------------------------------------------------------- #


def _app_with_wiki(tmp_path, roles=None, **kw):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    conn, fake = _wiki_conn(roles=roles, **kw)
    platform.ltm.register(conn)
    p = client.post("/brain/profiles", json={"name": "Wiki", "primary": "hermes-brain"}).json()
    assert client.put("/settings/values", json={"values": {"brain_profile": p["id"]}}).status_code == 200
    return app, client, platform, conn, fake


def _ctx(platform):
    return ToolContext(workspace=Path(platform.config.home), session_id="chat", agent_run_id="r",
                       config=platform.config, event_bus=platform.event_bus, engine=platform.engine)


@pytest.mark.asyncio
async def test_a_save_before_setup_says_so_sends_nothing_and_queues_nothing(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(tmp_path)
    res = await BrainSaveTool(platform).execute({"title": "Lunch", "content": "Likes sushi."}, _ctx(platform))
    assert not res.ok and "Set up tools" in res.error
    assert not any(n == "create_page" for n, _ in fake.calls)
    assert client.get("/brain/pending").json()["pending"] == []


@pytest.mark.asyncio
async def test_with_confirmed_tools_a_save_creates_then_adds_and_is_read_back(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(tmp_path, roles=CONFIRMED)
    tool = BrainSaveTool(platform)
    r1 = await tool.execute({"title": "Lunch", "content": "Likes sushi."}, _ctx(platform))
    assert r1.ok and r1.data["brain"]["verified"] is True and r1.data["brain"]["action"] == "created", r1
    create = [a for n, a in fake.calls if n == "create_page"][0]
    assert create["type"] == "concept" and create["tags"] == ["inbox", "notes"]
    r2 = await tool.execute({"title": "lunch", "content": "Not on Fridays."}, _ctx(platform))
    assert r2.ok and r2.data["brain"]["action"] == "updated", r2
    append = [a for n, a in fake.calls if n == "append_section"][0]
    assert append == {"path": "concepts/lunch.md", "heading": "lunch", "content": "Not on Fridays."}


@pytest.mark.asyncio
async def test_a_refusal_is_said_as_a_refusal_and_never_queued(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(
        tmp_path, roles=CONFIRMED, refuse="PII policy: no account numbers")
    res = await BrainSaveTool(platform).execute({"title": "x", "content": "y"}, _ctx(platform))
    assert not res.ok and "refused it" in res.error and "PII policy" in res.error
    assert "unavailable" not in res.error
    assert client.get("/brain/pending").json()["pending"] == []


@pytest.mark.asyncio
async def test_change_without_a_confirmed_tool_changes_nothing(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(tmp_path, roles={"search": CONFIRMED["search"]})
    res = await BrainChangeTool(platform).execute({"action": "archive", "ref": "concepts/x.md"}, _ctx(platform))
    assert not res.ok and "Set up tools" in res.error
    assert not any(n == "archive_page" for n, _ in fake.calls)


def test_unreachable_is_only_the_not_reached_shapes():
    assert unreachable(TimeoutError("read timed out"))
    assert unreachable(ConnectionError("refused"))
    assert unreachable(RuntimeError("hermes-brain: the MCP server did not answer within 30 s"))
    assert not unreachable(RuntimeError("hermes-brain: type must be one of: concept, client"))
    from iron_jarvis.ltm.base import NoteOpUnsupported

    assert not unreachable(NoteOpUnsupported("cannot"))


# --------------------------------------------------------------------------- #
# 3. A save that landed before its answer was lost
# --------------------------------------------------------------------------- #


def test_a_save_that_landed_then_timed_out_is_recognised_not_queued(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(
        tmp_path, roles=CONFIRMED, fail=TimeoutError("the reply timed out"))
    active = P.resolve(platform, None)
    res = write_note(platform, active, conn, "Lunch", "Likes sushi.")
    assert res["ok"] and res["verified"] is True, res
    assert client.get("/brain/pending").json()["pending"] == []


def test_retry_of_a_kept_save_that_had_landed_adds_nothing(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(tmp_path, roles=CONFIRMED)
    active = P.resolve(platform, None)
    fake.pages["concepts/lunch.md"] = {"name": "lunch", "content": "# Lunch\n\nLikes sushi."}
    from iron_jarvis.brain.tools import record_pending

    pid = record_pending(platform, active, "Lunch", "Likes sushi.", "timed out")
    r = client.post(f"/brain/pending/{pid}/retry")
    assert r.status_code == 200 and r.json()["saved"]["action"] == "already", r.text
    assert fake.pages["concepts/lunch.md"]["content"].count("Likes sushi.") == 1
    assert not any(n in ("create_page", "append_section") for n, _ in fake.calls)


# --------------------------------------------------------------------------- #
# 4. Roles routes, check, test save
# --------------------------------------------------------------------------- #


def _register_mcp_record(platform, conn):
    from iron_jarvis.ltm.sources import CustomSourceStore

    CustomSourceStore(platform.engine).add("hermes-brain", "mcp", endpoint_url="http://wiki.invalid/mcp")


def test_the_roles_routes_propose_refuse_and_save(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(tmp_path)
    _register_mcp_record(platform, conn)
    view = client.get("/ltm/sources/hermes-brain/roles").json()
    assert view["saved"] is None and view["proposed"]["create"]["tool"] == "create_page"
    assert any("'type' is required" in p for p in view["problems"]["create"])
    bad = client.put("/ltm/sources/hermes-brain/roles", json={"roles": {"create": view["proposed"]["create"]}})
    assert bad.status_code == 400 and "is required" in bad.json()["detail"]
    ok = client.put("/ltm/sources/hermes-brain/roles", json={"roles": CONFIRMED})
    assert ok.status_code == 200 and ok.json()["saved"]["create"]["tool"] == "create_page"
    assert conn.roles["create"]["tool"] == "create_page"
    from iron_jarvis.ltm.sources import CustomSourceStore

    assert json.loads(CustomSourceStore(platform.engine).get("hermes-brain").roles_json)["append"]["tool"] == "append_section"
    assert client.get("/ltm/sources/brain/roles").status_code == 404


def test_check_and_test_save_on_the_real_path(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(tmp_path)
    pid = P.BrainProfileStore(platform).list()[0]["id"]
    steps = {s["step"]: s for s in client.post(f"/brain/profiles/{pid}/check").json()["steps"]}
    assert steps["search"]["ok"] is True
    assert steps["save"]["ok"] is False and "Set up tools" in steps["save"]["words"]
    conn.roles = CONFIRMED
    out = client.post(f"/brain/profiles/{pid}/test-save").json()
    assert out["ok"] and out["verified"] is True and "Iron Jarvis test note" in out["words"]


# --------------------------------------------------------------------------- #
# 5. Removing a base unloads it; the listing says its real kind
# --------------------------------------------------------------------------- #


def test_removing_a_base_unloads_it_and_the_listing_says_its_kind(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    platform = app.state.platform
    vault = tmp_path / "vault"
    vault.mkdir()
    assert client.post("/ltm/sources", json={"name": "workvault", "kind": "markdown", "path": str(vault)}).status_code in (200, 201)
    bases = {b["name"]: b for b in client.get("/ltm/sources").json()["bases"]}
    assert bases["workvault"]["kind"] == "markdown"
    assert bases["brain"]["kind"] == "brain"
    out = client.delete("/ltm/sources/workvault").json()
    assert out == {"removed": True, "unloaded": True}
    assert "workvault" not in platform.ltm.sources()


# --------------------------------------------------------------------------- #
# 6. A Notion database whose title column is not "Name"
# --------------------------------------------------------------------------- #


class _Resp:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            class _E(Exception):
                pass

            e = _E(f"{self.status_code}")
            e.response = self
            raise e


class _NotionHttp:
    def __init__(self):
        self.posts: list[dict] = []

    def post(self, url, json=None, headers=None):
        self.posts.append(json)
        prop = (json.get("filter") or {}).get("property") or next(iter(json.get("properties") or {}), "")
        if prop != "Title":
            return _Resp(400, {"message": f"Could not find property with name or id: {prop}"})
        return _Resp(200, {"results": [], "id": "page-1"})

    def get(self, url, headers=None):
        return _Resp(200, {"properties": {"Title": {"type": "title"}, "Tags": {"type": "multi_select"}}})


def test_a_notion_database_with_its_own_title_column_works():
    http = _NotionHttp()
    conn = NotionConnector("db1", lambda: "tok", http)
    assert conn.search("acme", 3) == []
    assert http.posts[-1]["filter"]["property"] == "Title"
    assert conn.append("Acme", "note") == "page-1"
    assert "Title" in http.posts[-1]["properties"]
