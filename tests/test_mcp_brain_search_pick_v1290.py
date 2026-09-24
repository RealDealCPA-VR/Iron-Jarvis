"""An MCP brain's search never lands on a tool that writes (v1.290.0).

``McpBrainConnector.search`` sends the caller's query text VERBATIM to one tool
on a third-party MCP server. It used to pick the FIRST tool whose name or
description matched search|query|recall|retrieve|find|lookup, so a server that
listed ``find_and_replace`` before ``search_notes`` — or exposed only a SQL
``run_query`` — was handed the query as its argument. Since v1.290.0 an external
harness reaches this path too (Build pane Memory capability -> ``ltm_search``),
so the query may be text nobody at this desk typed.

These pins drive the REAL connector code with a fake MCP client (the same
``list_tools``/``call_tool`` seam the real ``MCPClient`` fills) and assert what
was CALLED, not only what was picked: a refusal is proven by an empty
``calls`` list.
"""

from __future__ import annotations

import pytest

from iron_jarvis.ltm.mcp_brain import McpBrainConnector


class FakeMcpClient:
    def __init__(self, tools):
        self._tools = tools
        self.calls: list[tuple[str, dict]] = []
        self.lists = 0

    def list_tools(self):
        self.lists += 1
        return list(self._tools)

    def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return {"content": [{"type": "text", "text": f"hit from {name}"}], "isError": False}


def _tool(name, props=None, description=""):
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}} if props is None else props,
        },
    }


def _brain(tools) -> tuple[McpBrainConnector, FakeMcpClient]:
    client = FakeMcpClient(tools)
    return McpBrainConnector("brain", client=client), client


def test_find_and_replace_listed_first_loses_to_search_notes():
    """THE TRAP: list order used to decide, and the replace tool came first."""
    brain, client = _brain([
        _tool("find_and_replace", {"query": {"type": "string"}, "replacement": {"type": "string"}}),
        _tool("search_notes"),
    ])
    hits = brain.search("client A trust", k=3)
    assert client.calls == [("search_notes", {"query": "client A trust"})]
    assert hits and hits[0]["source"] == "brain"


def test_only_run_query_listed_calls_nothing_and_says_why():
    brain, client = _brain([_tool("run_query", {"sql": {"type": "string"}, "query": {"type": "string"}})])
    with pytest.raises(RuntimeError) as exc:
        brain.search("DROP TABLE clients", k=3)
    assert client.calls == [], "a write-like tool was called with the query"
    assert "safe" in str(exc.value) and "nothing was searched" in str(exc.value)


@pytest.mark.parametrize(
    "name",
    [
        "query_and_update", "find_and_replace", "run_query", "executeSql",
        "sql_query", "delete_search_index", "upsertLookup", "save_search",
        "search_and_set", "patch_find", "mutateQuery", "append_recall",
        "insert_retrieve", "create_lookup", "remove_find", "drop_search",
        "put_query", "exec_search", "write_query",
    ],
)
def test_every_write_word_refuses_the_tool(name):
    brain, client = _brain([_tool(name)])
    with pytest.raises(RuntimeError):
        brain.search("x")
    assert client.calls == []


def test_search_words_beat_bare_query_and_find_whatever_the_order():
    brain, client = _brain([
        _tool("query"),
        _tool("find_notes"),
        _tool("recall_memory"),
    ])
    brain.search("q")
    assert client.calls[0][0] == "recall_memory"


def test_bare_query_still_serves_when_it_is_the_only_safe_tool():
    brain, client = _brain([_tool("query"), _tool("find_and_replace")])
    brain.search("q")
    assert client.calls == [("query", {"query": "q"})]


def test_a_tool_without_a_string_query_parameter_is_refused():
    brain, client = _brain([
        _tool("search_by_id", {"id": {"type": "integer"}}),
        _tool("search_numbers", {"query": {"type": "integer"}}),
    ])
    with pytest.raises(RuntimeError):
        brain.search("hello")
    assert client.calls == []


def test_an_untyped_query_parameter_still_counts():
    """Real servers (and the v1.x fixtures) often omit ``type``; that accepts a string."""
    brain, client = _brain([_tool("search_notes", {"query": {}, "limit": {}})])
    brain.search("hello", k=4)
    assert client.calls == [("search_notes", {"query": "hello", "limit": 4})]


def test_a_word_inside_another_word_is_not_a_write_word():
    """``search_assets`` contains "set" as letters, not as a word."""
    brain, client = _brain([_tool("search_assets")])
    brain.search("logo")
    assert client.calls == [("search_assets", {"query": "logo"})]


def test_health_probe_uses_the_same_safe_pick():
    brain, _ = _brain([_tool("run_query")])
    verdict = brain._probe()
    assert verdict["available"] is False and "no search-like tool" in verdict["detail"]
    brain2, _ = _brain([_tool("find_and_replace"), _tool("search_notes")])
    assert brain2._probe()["tool"] == "search_notes"
