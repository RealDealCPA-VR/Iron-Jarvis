"""v1.408.0 REVIEW — defects found in the daemon half of the memory-profile
fixes, each pinned red first against the shipped code.

1. A base that REFUSED a save, with a reply that echoes the note it was sent
   (a FastMCP validation error carries ``input_value={...}``), was read as
   "not reached" whenever the note mentioned a connection, a network, a
   timeout or a number holding 429/503 — and the refusal was QUEUED forever,
   the exact v1.406.0 bug this release set out to end. A status code is now a
   whole number, and a word that is in what was SENT never decides.
2. ``already_saved`` compared only the first 80 characters, with no word
   boundary: a corrected note ("Gate code: 1234" over "Gate code: 12345") or a
   revised paragraph that starts the same as the old one was reported SAVED
   when the base had refused it — the new text silently lost.
3. A create whose reply is prose (no JSON ref) used that first line as the
   ref, so read-back failed and a note that landed read "not confirmed".
4. ``health()`` ignored a CONFIRMED search tool: a base whose search the user
   set up (a tool the safe pick cannot use) read red on the listing and the
   profile Check stopped at "could not be reached".
5. ``tool_list`` (Set up tools: GET and PUT roles) answered from the list
   cached at first connect — a tool the server added since never appeared, so
   it could not be confirmed — and had no bound of its own.
6. A confirmed role whose REQUIRED setting has no value (a rename with no new
   name; "add to a note" with no heading) was sent anyway; it is refused
   before anything is sent.
7. Re-adding a base under the same name with ANOTHER server kept the tools the
   user had confirmed for the old one.
8. Notion: one failed schema read left the title column undiscoverable for
   the life of the process.
9. A tool whose name edits/modifies/changes, or whose MCP annotations say it
   is not read-only, could be confirmed as the READER.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from iron_jarvis.brain import profiles as P
from iron_jarvis.brain.tools import BrainSaveTool, already_saved, unreachable, write_note
from iron_jarvis.daemon.app import create_app
from iron_jarvis.ltm import mcp_roles
from iron_jarvis.ltm.base import NoteOpUnsupported
from iron_jarvis.ltm.mcp_brain import McpBrainConnector
from iron_jarvis.ltm.notion import NotionConnector
from iron_jarvis.ltm.sources import CustomSourceStore
from tests.test_memory_profile_fixes_v1408 import (
    CONFIRMED,
    HERMES_TOOLS,
    FakeWiki,
    _app_with_wiki,
    _ctx,
    _NotionHttp,
)


# --------------------------------------------------------------------------- #
# 1. A refusal that echoes the note is still a refusal
# --------------------------------------------------------------------------- #


def _echo(content: str) -> str:
    return (
        "Error executing tool create_page: 1 validation error for create_pageArguments\n"
        "type\n  Field required [type=missing, input_value={'summary': 'Club', "
        f"'body': {content!r}}}, input_type=dict]"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [
    "Lost the connection to the 429 club after the network outage.",
    "The supplier timed out on delivery twice; they are temporarily closed.",
])
async def test_a_refusal_that_echoes_the_note_is_said_and_never_queued(tmp_path, content):
    app, client, platform, conn, fake = _app_with_wiki(
        tmp_path, roles=CONFIRMED, refuse=_echo(content))
    res = await BrainSaveTool(platform).execute({"title": "Club", "content": content}, _ctx(platform))
    assert not res.ok and "refused it" in res.error, res.error
    assert client.get("/brain/pending").json()["pending"] == []


def test_a_status_code_is_a_whole_number_never_a_slice_of_one():
    assert not unreachable(RuntimeError("hermes-brain: page 4290 does not exist"))
    assert not unreachable(RuntimeError("hermes-brain: invoice 15031 has no summary"))
    assert unreachable(RuntimeError("upstream answered 503"))
    assert unreachable(RuntimeError("HTTP 429 Too Many Requests"))


def test_the_servers_own_words_still_mean_not_reached():
    """Only words that are in what was SENT are ignored — the server's own
    "timed out" still keeps the note."""
    assert unreachable(RuntimeError("hermes-brain: upstream store timed out"), sent=("Lunch", "Likes sushi."))
    assert not unreachable(RuntimeError("hermes-brain: bad body 'the network'"), sent=("x", "the network"))


# --------------------------------------------------------------------------- #
# 2. already_saved holds the WHOLE text, at word boundaries
# --------------------------------------------------------------------------- #


def test_a_refused_correction_is_not_mistaken_for_the_older_longer_note(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(tmp_path, roles=CONFIRMED, refuse="no")
    fake.pages["concepts/gate-code.md"] = {"name": "gate-code", "content": "# Gate code\n\nGate code: 12345"}
    active = P.resolve(platform, None)
    res = write_note(platform, active, conn, "Gate code", "Gate code: 1234")
    assert not res["ok"] and res["kind"] == "refused", res


def test_a_refused_revision_that_starts_like_the_old_text_is_not_saved(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(tmp_path, roles=CONFIRMED, refuse="no")
    head = "Client Acme billing terms, agreed in the kickoff call with their controller: "
    fake.pages["concepts/acme.md"] = {"name": "acme", "content": head + "net 30, invoiced monthly."}
    active = P.resolve(platform, None)
    assert already_saved(platform, active, conn, "Acme", head + "net 15, invoiced weekly.") == ""
    assert already_saved(platform, active, conn, "Acme", head + "net 30, invoiced monthly.") == "concepts/acme.md"


# --------------------------------------------------------------------------- #
# 3. A prose create reply is found by its title, not trusted as a ref
# --------------------------------------------------------------------------- #


class _ProseWiki(FakeWiki):
    def call_tool(self, name, args):
        out = super().call_tool(name, args)
        if name == "create_page" and not out.get("isError"):
            path = json.loads(out["content"][0]["text"])["path"]
            return {"content": [{"type": "text", "text": f"Created page '{args['summary']}' at {path}"}],
                    "isError": False}
        return out


def test_a_prose_create_reply_is_looked_up_and_confirmed(tmp_path):
    app, client, platform, conn, fake = _app_with_wiki(tmp_path, roles=CONFIRMED)
    prose = _ProseWiki()
    conn._client = prose
    active = P.resolve(platform, None)
    res = write_note(platform, active, conn, "Lunch", "Likes sushi.")
    assert res["ok"] and res["verified"] is True and res["ref"] == "concepts/lunch.md", res


# --------------------------------------------------------------------------- #
# 4. health honours a confirmed search tool
# --------------------------------------------------------------------------- #

_LOOKUP = [{"name": "lookup_entries", "description": "Entries by term.",
            "inputSchema": {"type": "object", "properties": {"term": {"type": "string"}},
                            "required": ["term"]}}]


class _Lookup:
    def list_tools(self):
        return list(_LOOKUP)

    def call_tool(self, name, args):
        return {"content": [{"type": "text", "text": "[]"}], "isError": False}


def test_a_confirmed_search_tool_makes_the_base_healthy():
    roles = {"search": {"tool": "lookup_entries", "args": {"term": "query"}}}
    conn = McpBrainConnector("lk", client=_Lookup(), roles=roles)
    verdict = conn.health(refresh=True)
    assert verdict["available"] is True and verdict.get("tool") == "lookup_entries", verdict


# --------------------------------------------------------------------------- #
# 5. Set up tools reads the server's CURRENT list, bounded
# --------------------------------------------------------------------------- #


class _Growing(FakeWiki):
    def __init__(self):
        super().__init__()
        self.extra: list[dict] = []

    def list_tools(self):
        return list(HERMES_TOOLS) + list(self.extra)


def test_tool_list_reads_a_tool_the_server_added_since_it_connected():
    fake = _Growing()
    conn = McpBrainConnector("w", client=fake)
    conn.search("x", 3)  # connected: the list is cached now
    fake.extra.append({"name": "delete_page", "inputSchema": {"type": "object", "properties": {
        "path": {"type": "string"}}, "required": ["path"]}})
    assert "delete_page" in [t["name"] for t in conn.tool_list()]


class _Hung(FakeWiki):
    def __init__(self):
        super().__init__()
        self.release = threading.Event()

    def list_tools(self):
        self.release.wait(10)
        return list(HERMES_TOOLS)


def test_tool_list_is_bounded_on_a_server_that_does_not_answer():
    fake = _Hung()
    conn = McpBrainConnector("w", client=fake)
    t0 = time.monotonic()
    try:
        with pytest.raises(Exception, match="did not answer"):
            conn.tool_list(timeout=0.3)
        assert time.monotonic() - t0 < 5
    finally:
        fake.release.set()


# --------------------------------------------------------------------------- #
# 6. A required setting with no value is never sent
# --------------------------------------------------------------------------- #

_RENAME = {"name": "rename_page", "inputSchema": {"type": "object", "properties": {
    "path": {"type": "string"}, "new_name": {"type": "string"}}, "required": ["path", "new_name"]}}


def test_a_rename_with_no_new_name_sends_nothing():
    fake = FakeWiki()
    fake.list_tools = lambda: list(HERMES_TOOLS) + [_RENAME]  # type: ignore[method-assign]
    roles = {"rename": {"tool": "rename_page", "args": {"path": "ref", "new_name": "to"}}}
    conn = McpBrainConnector("w", client=fake, roles=roles)
    with pytest.raises(NoteOpUnsupported):
        conn.change("rename", "concepts/x.md", "")
    assert not any(n == "rename_page" for n, _ in fake.calls)


def test_add_to_a_note_with_no_heading_for_a_required_heading_sends_nothing():
    fake = FakeWiki()
    fake.pages["concepts/x.md"] = {"name": "x", "content": "old"}
    conn = McpBrainConnector("w", client=fake, roles=CONFIRMED)
    with pytest.raises(NoteOpUnsupported):
        conn.update("concepts/x.md", "more", title="")
    assert not any(n == "append_section" for n, _ in fake.calls)


# --------------------------------------------------------------------------- #
# 7. Another server under the same name starts with nothing confirmed
# --------------------------------------------------------------------------- #


def _set_roles(engine, name, roles):
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.ltm.sources import LTMSourceRecord

    rec = CustomSourceStore(engine).get(name)
    with session_scope(engine) as db:
        row = db.get(LTMSourceRecord, rec.id)
        row.roles_json = json.dumps(roles)
        db.add(row)
        db.commit()


def test_re_adding_a_base_with_another_server_forgets_the_confirmed_tools(tmp_path):
    app = create_app(str(tmp_path))
    engine = app.state.platform.engine
    store = CustomSourceStore(engine)
    store.add("wiki", "mcp", endpoint_url="http://a.invalid/mcp")
    _set_roles(engine, "wiki", CONFIRMED)
    store.add("wiki", "mcp", endpoint_url="http://a.invalid/mcp")  # same server: kept
    assert store.get("wiki").roles_json
    store.add("wiki", "mcp", endpoint_url="http://b.invalid/mcp")  # another server
    assert store.get("wiki").roles_json == ""


# --------------------------------------------------------------------------- #
# 8. Notion: a failed schema read is not remembered
# --------------------------------------------------------------------------- #


class _FlakyNotionHttp(_NotionHttp):
    def __init__(self):
        super().__init__()
        self.gets = 0

    def get(self, url, headers=None):
        self.gets += 1
        if self.gets == 1:
            raise ConnectionError("reset")
        return super().get(url, headers=headers)


def test_a_failed_notion_schema_read_is_tried_again_next_time():
    http = _FlakyNotionHttp()
    conn = NotionConnector("db1", lambda: "tok", http)
    with pytest.raises(Exception):
        conn.search("acme", 3)
    assert conn.search("acme", 3) == []
    assert http.posts[-1]["filter"]["property"] == "Title"


# --------------------------------------------------------------------------- #
# 9. A tool that changes notes is never the reader
# --------------------------------------------------------------------------- #


def test_an_editing_tool_is_never_confirmed_as_the_reader():
    edit = {"name": "edit_page", "inputSchema": {"type": "object", "properties": {
        "path": {"type": "string"}}, "required": ["path"]}}
    tools = list(HERMES_TOOLS) + [edit]
    with pytest.raises(ValueError, match="never used"):
        mcp_roles.clean({"read": {"tool": "edit_page", "args": {"path": "ref"}}}, tools)


def test_a_tool_whose_annotations_say_it_writes_is_never_the_reader():
    lookup = {"name": "lookup_page", "annotations": {"readOnlyHint": False, "destructiveHint": True},
              "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}},
                              "required": ["path"]}}
    tools = list(HERMES_TOOLS) + [lookup]
    assert any("never used" in p for p in mcp_roles.problems("read", {"tool": "lookup_page", "args": {"path": "ref"}}, tools))
    assert mcp_roles.propose([lookup])["read"] is None


# --------------------------------------------------------------------------- #
# Pins for guarantees the shipped tests left unchecked (mutation survivors)
# --------------------------------------------------------------------------- #


def test_a_base_that_cannot_do_it_is_never_kept_for_later_whatever_it_says():
    assert not unreachable(NoteOpUnsupported("w: the network folder note cannot be renamed (timed out)"))


def test_a_confirmed_search_tool_is_what_search_calls():
    class _Rec(_Lookup):
        def __init__(self):
            self.calls = []

        def call_tool(self, name, args):
            self.calls.append((name, dict(args)))
            return {"content": [{"type": "text", "text": json.dumps([{"path": "a.md", "name": "a"}])}],
                    "isError": False}

    rec = _Rec()
    conn = McpBrainConnector("lk", client=rec, roles={"search": {"tool": "lookup_entries", "args": {"term": "query"}}})
    hits = conn.search("payroll", 3)
    assert rec.calls == [("lookup_entries", {"term": "payroll"})]
    assert hits[0]["ref"] == "a.md"


def test_a_create_reply_names_its_note_by_its_json_path():
    from iron_jarvis.ltm.mcp_brain import _created_ref

    assert _created_ref({"content": [{"type": "text", "text": json.dumps({"ok": True, "path": "c/x.md"})}]}) == "c/x.md"
    assert _created_ref({"structuredContent": {"id": "p-1"}, "content": []}) == "p-1"
