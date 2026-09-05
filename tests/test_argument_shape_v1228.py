"""v1.228.0 Wave 2, task 2B — the ARGUMENTS ENVELOPE and a NAMED missing
argument (T2, T2b, T1/RT7).

Converted from ``tests/_audit_20260904/test_q1_argument_shape.py`` and the
KeyError half of ``test_q6_local_model_failures.py::
test_q6_empty_arguments_and_unknown_tool_are_bounded_but_raw``.

Live (ironjarvis.db, toolinvocation): 2026-08-03 chat ``file_search`` with
``args={"arguments": "{\\"query\\": \\"*\\", ...}"}`` -> ``KeyError: 'query'``;
2026-08-16 a session's ``shell`` the same way -> ``KeyError: 'command'``.
Two defects stacked: the OpenAI-compatible adapter passed the model's
``{"arguments": "<json>"}`` envelope straight through (valid JSON, so the
v1.225.0 recovery ladder never ran), and ``registry.invoke`` let a call
missing a declared-required key reach ``execute`` and crash, handing the
model a Python traceback string it cannot act on.

Fixes: ``core.jsonish.unwrap_arguments`` at BOTH adapter parse sites (the
Responses-API parser also gains the ``loads_object`` fallback it lacked —
T2b), and a required/top-level-type check in ``registry.invoke`` after the
permission decision and before the read cache / undo capture / execute,
recorded through the same ``_record`` + ``tool.executed`` path as any
failure so the ledger holds it and the model can correct the call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlmodel import select

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.jsonish import json_type_ok, loads_object, unwrap_arguments
from iron_jarvis.core.models import AgentType, SessionStatus, ToolInvocation
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter
from iron_jarvis.providers.adapters.openai import OpenAIAdapter
from iron_jarvis.tools.base import ToolContext

INNER = {"query": "*", "mode": "name"}
NESTED = {"arguments": json.dumps(INNER)}


# ------------------------------------------------------------- jsonish (T2)

def test_unwrap_arguments_peels_the_string_envelope():
    assert unwrap_arguments(NESTED) == INNER


def test_unwrap_arguments_peels_a_dict_envelope():
    assert unwrap_arguments({"arguments": dict(INNER)}) == INNER


def test_unwrap_arguments_recovers_almost_json_inside_the_envelope():
    # the inner string goes through loads_object: a trailing comma is fine
    assert unwrap_arguments({"arguments": '{"query": "*", "mode": "name",}'}) == INNER


@pytest.mark.parametrize(
    "obj",
    [
        {"query": "*"},                              # not an envelope
        {"arguments": "x", "path": "a"},             # `arguments` is not the ONLY key
        {"arguments": "not json at all"},            # inner string is not an object
        {"arguments": ["a", "b"]},                   # inner is a list
        {"arguments": 3},
        "a string",
        None,
        {},
    ],
    ids=["plain", "two-keys", "garbage-string", "list", "int", "str", "none", "empty"],
)
def test_unwrap_arguments_leaves_everything_else_untouched(obj):
    assert unwrap_arguments(obj) == obj


def test_loads_object_alone_still_does_not_unwrap():
    # the unwrap is a separate, explicit step — loads_object invents nothing
    assert loads_object(json.dumps(NESTED)) == NESTED


def test_json_type_ok_is_a_cheap_top_level_guard():
    assert json_type_ok("a", "string") and not json_type_ok(1, "string")
    assert json_type_ok(1, "integer") and not json_type_ok(True, "integer")
    assert json_type_ok(1.5, "number") and json_type_ok(2, "number")
    assert not json_type_ok(True, "number")
    assert json_type_ok(False, "boolean") and not json_type_ok(0, "boolean")
    assert json_type_ok([], "array") and not json_type_ok({}, "array")
    assert json_type_ok({}, "object") and not json_type_ok([], "object")
    # unknown / composite declarations are accepted, not judged
    assert json_type_ok(None, ["string", "null"]) and json_type_ok(1, None)
    assert json_type_ok(1, "whatever")
    # a STRING passes for any declared type — tools coerce the text models
    # send ("5", "true", a newline list); worklist_add takes `items` as text
    assert json_type_ok("5", "integer") and json_type_ok("true", "boolean")
    assert json_type_ok("a\nb", "array") and json_type_ok("{}", "object")
    assert not json_type_ok(["a"], "string") and not json_type_ok({"a": 1}, "array")


async def test_a_string_for_an_array_still_reaches_the_tool(platform, tmp_path):
    # the v1.174.0 leniency survives the gate
    ctx = _ctx(platform, tmp_path)
    res = await platform.registry.invoke(
        "worklist_add", {"items": "C:/f/c.pdf\nC:/f/d.pdf"}, ctx, platform.permissions
    )
    assert res.ok and res.data["added"] == 2


# ------------------------------------------------------------- adapter (T2, T2b)

def _chat_payload(args_str: str) -> dict:
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "file_search", "arguments": args_str},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {},
    }


def _sse(args_str: str) -> str:
    completed = {
        "type": "response.completed",
        "response": {
            "output": [
                {
                    "type": "function_call",
                    "call_id": "call_1",
                    "name": "file_search",
                    "arguments": args_str,
                }
            ],
            "usage": {},
        },
    }
    return "data: " + json.dumps(completed) + "\n\ndata: [DONE]\n"


ALMOST = '{"query": "*", "mode": "name",}'  # trailing comma: what local models emit


def test_chat_parse_unwraps_the_arguments_envelope():
    resp = OpenAIAdapter._parse(_chat_payload(json.dumps(NESTED)))
    assert resp.tool_calls[0].arguments == INNER


def test_chat_parse_keeps_ordinary_arguments():
    resp = OpenAIAdapter._parse(_chat_payload(json.dumps(INNER)))
    assert resp.tool_calls[0].arguments == INNER


def test_responses_parse_unwraps_the_arguments_envelope():
    resp = OpenAIAdapter._parse_sse(_sse(json.dumps(NESTED)))
    assert resp.tool_calls[0].name == "file_search"
    assert resp.tool_calls[0].arguments == INNER


def test_responses_parse_recovers_almost_json_like_the_chat_parser():
    # T2b: bare `except JSONDecodeError: args = {}` silently emptied this call
    resp = OpenAIAdapter._parse_sse(_sse(ALMOST))
    assert resp.tool_calls[0].arguments == INNER
    # and the two parsers agree
    assert OpenAIAdapter._parse(_chat_payload(ALMOST)).tool_calls[0].arguments == INNER


def test_responses_parse_still_empties_what_nothing_can_parse():
    resp = OpenAIAdapter._parse_sse(_sse("not json"))
    assert resp.tool_calls[0].arguments == {}


# ------------------------------------------------------------- registry (T1/RT7)

def _ctx(platform, workspace: Path, session_id: str = "shape-s1") -> ToolContext:
    return ToolContext(
        workspace=workspace, session_id=session_id, agent_run_id="shape-r1",
        config=platform.config, event_bus=platform.event_bus, engine=platform.engine,
    )


async def _invoke(platform, name, args, workspace, allow=()):
    return await platform.registry.invoke(
        name, args, _ctx(platform, workspace), platform.permissions,
        session_allow=set(allow) or None,
    )


def _ledger(engine, session_id: str, tool: str) -> list[ToolInvocation]:
    with session_scope(engine) as db:
        rows = list(
            db.exec(
                select(ToolInvocation).where(
                    ToolInvocation.session_id == session_id, ToolInvocation.tool == tool
                )
            )
        )
        for r in rows:
            db.expunge(r)
        return rows


CASES = [
    # tool, args, missing required key
    ("file_search", dict(NESTED), "query"),  # the raw envelope, had the adapter not unwrapped it
    ("shell", {"arguments": json.dumps({"command": "echo hi"})}, "command"),
    ("read_file", {}, "path"),
    ("write_file", {"path": "a.txt"}, "content"),
    ("grep", {"path": "."}, "pattern"),
    ("write_document", {"content": "# hi"}, "path"),
    ("edit_file", {"path": "a.txt", "old": "x"}, "new"),
    ("rename_file", {"path": "a.txt"}, "new_path"),
]


@pytest.mark.parametrize("tool,args,missing", CASES, ids=[c[0] for c in CASES])
async def test_missing_required_argument_is_named_not_a_traceback(platform, tmp_path, tool, args, missing):
    schema = platform.registry.get(tool).input_schema
    assert missing in (schema.get("required") or []), "precondition: schema declares it required"
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    res = await _invoke(platform, tool, args, tmp_path, allow={"shell"})
    assert not res.ok
    err = res.error or ""
    assert not err.startswith(("KeyError", "TypeError")), err
    assert err.startswith(f"missing required: {missing}"), err
    assert f"{tool} needs" in err and "got [" in err, err
    for key in sorted(args):
        assert repr(key) in err  # the keys it DID get are listed


async def test_shape_failure_is_ledgered_and_published_like_any_failure(platform, tmp_path, monkeypatch):
    seen: list[tuple] = []
    real = platform.event_bus.publish

    async def spy(*a, **kw):  # a fake for publish takes *args/**kw
        seen.append((a, kw))
        return await real(*a, **kw)

    monkeypatch.setattr(platform.event_bus, "publish", spy)
    res = await _invoke(platform, "read_file", {}, tmp_path)
    assert not res.ok and res.error.startswith("missing required: path")
    rows = _ledger(platform.engine, "shape-s1", "read_file")
    assert len(rows) == 1 and rows[0].ok is False
    assert rows[0].output.startswith("missing required: path")
    executed = [(a, kw) for a, kw in seen if getattr(a[0], "value", a[0]) == "tool.executed"]
    assert len(executed) == 1
    payload = executed[0][0][1]
    assert payload["tool"] == "read_file" and payload["ok"] is False
    assert payload["invocation_id"] == rows[0].id


async def test_wrong_top_level_type_is_named(platform, tmp_path):
    res = await _invoke(platform, "read_file", {"path": 123}, tmp_path)
    assert not res.ok
    assert res.error.startswith("wrong type: path (expected string, got int)"), res.error
    assert "read_file needs ['path']" in res.error


async def test_store_as_is_stripped_before_the_check(platform, tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    ok = await _invoke(platform, "read_file", {"path": "a.txt", "_store_as": "x"}, tmp_path)
    assert ok.ok, ok.error  # `_store_as` is not an unknown/typed key
    res = await _invoke(platform, "read_file", {"_store_as": "x"}, tmp_path)
    assert not res.ok and res.error.endswith("got []"), res.error


async def test_a_refused_call_is_recorded_as_refused_not_as_malformed(platform, tmp_path):
    # shell defaults to ask; with no session grant the permission decision
    # comes FIRST and the ledger says who refused, not what was missing.
    res = await _invoke(platform, "shell", {}, tmp_path)
    assert not res.ok
    assert res.error.startswith("permission denied"), res.error
    assert "missing required" not in res.error


async def test_a_well_formed_call_still_runs(platform, tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    res = await _invoke(platform, "read_file", {"path": "a.txt"}, tmp_path)
    assert res.ok and "hello" in res.output


# ------------------------------------------------------------- end to end (runtime)

def _call(name: str, args: dict) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall(id="c1", name=name, arguments=args)], finish_reason="tool_use")


async def test_agent_run_reads_the_named_missing_argument(tmp_path):
    p = build_platform(str(tmp_path / "home"))
    orch = Orchestrator(p)
    script = [
        _call("read_file", {}),
        _call("file_search", {}),
        LLMResponse(text="stuck", finish_reason="stop"),
    ]
    p.providers.register("mock", lambda model=None: MockLLMAdapter(script=list(script)))
    s = await orch.create_session("read the note", AgentType.BUILDER, allow_tools=["file_search"])
    row = await orch.run_session(s.id)
    assert row.status is SessionStatus.COMPLETED
    by = {t["tool"]: t for t in orch.transcript(s.id)["tools"]}
    assert not by["read_file"]["ok"] and by["read_file"]["output"].startswith("missing required: path")
    assert not by["file_search"]["ok"] and by["file_search"]["output"].startswith("missing required: query")
    for t in by.values():
        assert "KeyError" not in t["output"]
