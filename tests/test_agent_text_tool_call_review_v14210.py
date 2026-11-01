"""v1.421.0 (Wave 1 review, A5) — A REPORT THAT QUOTES A CALL IS NOT A CALL.

The agent runtime's reading of a call written as text
(``harness/mode.named_text_tool_call``) needs the call to name a tool the step
offered. That stops a report ABOUT the format, but not one that QUOTES a real
call: a reviewer reading a test fixture (``<tool_call>list_files …`` — the
finish check's reviewer is offered ``list_files``) or a teammate documenting
the format put the call in a code block or inline code, beside prose. The
first cut read that as the model's own call: the run was told "call the tool
itself", and the reply to that note REPLACED the report as the run's answer
(the report is lost; the coder model, or the user, sees only "that was a
quotation").

A call the model meant to make is never quoted: it is bare text (GLM's tags,
a Hermes / JSON blob, an ``<invoke>``), or one fenced block that ENDS the
reply ("Let me list the files." then a ```json call). A call that sits only
inside inline code, or inside a fenced block with the report going on after
it, is a quotation. Driven through the REAL orchestrator + runtime; only the
model is scripted.
"""

from __future__ import annotations

import pytest

from iron_jarvis.harness.mode import named_text_tool_call
from tests.test_agent_text_tool_call_v14210 import _agent

OFFERED = {"list_files", "read_file", "grep", "glob"}

FENCED_FIXTURE_REVIEW = (
    "The new test feeds GLM's shape:\n\n"
    "```\n<tool_call>list_files\n<arg_key>path</arg_key>\n<arg_value>.</arg_value>\n</tool_call>\n```\n\n"
    "and asserts the run is told once. The bound reads right.\n\n"
    "VERDICT: approved"
)
INLINE_QUOTE_REVIEW = (
    "The fixture `<tool_call>read_file` is matched by the first name pattern, and "
    '`{"name": "grep", "arguments": {}}` by the last. VERDICT: approved'
)
JSON_DOC = (
    "A model that has no tool parser writes its call like this:\n\n"
    '```json\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```\n\n'
    "Iron Jarvis reads it and tells the model once to make the call itself."
)


@pytest.mark.parametrize("text", [FENCED_FIXTURE_REVIEW, INLINE_QUOTE_REVIEW, JSON_DOC])
def test_a_call_quoted_in_code_beside_the_report_is_not_a_call(text):
    assert named_text_tool_call(text, OFFERED) == ""


@pytest.mark.parametrize(
    "text, name",
    [
        # Bare, as GLM / Hermes / Claude-style / a JSON blob write them.
        ("<tool_call>list_files\n<arg_key>path</arg_key>\n<arg_value>.</arg_value>\n</tool_call>", "list_files"),
        ('<tool_call>\n{"name": "read_file", "arguments": {"path": "a.py"}}\n</tool_call>', "read_file"),
        ('<function_calls>\n<invoke name="grep">\n</invoke>\n</function_calls>', "grep"),
        ('I will look first. {"name": "list_files", "arguments": {"path": "."}}', "list_files"),
        # A bare call next to a quoted one is still a call.
        ("The doc says `<tool_call>grep`. <tool_call>list_files\n</tool_call>", "list_files"),
        # ONE fenced block that ends the reply: the model's own call.
        ('```json\n{"name": "list_files", "arguments": {"path": "."}}\n```', "list_files"),
        ('Let me list the files.\n\n```json\n{"name": "list_files", "arguments": {"path": "."}}\n```\n', "list_files"),
        # An unclosed fence the reply stops inside.
        ('Checking:\n```\n<tool_call>read_file\n<arg_key>path</arg_key>', "read_file"),
    ],
)
def test_a_call_the_model_meant_to_make_is_still_read(text, name):
    assert named_text_tool_call(text, OFFERED) == name


def test_the_reading_is_linear_on_a_long_reply_full_of_backticks():
    """The reading runs ON THE EVENT LOOP for every final answer, and a reply
    streams up to 200,000 characters. Backtick runs of 1 to 399 ticks on one
    line (80,000 characters) took a backtracking inline-code regex about 30 s
    on this PC, with every other request in the app frozen behind it; paired
    by index it takes milliseconds. The bound is a hang detector (>1000x the
    real cost), not a speed bar."""
    import time

    text = " ".join("`" * k for k in range(1, 400)) + " <tool_call>list_files"
    started = time.perf_counter()
    assert named_text_tool_call(text, OFFERED) == "list_files"
    assert time.perf_counter() - started < 5.0


def test_a_reviewers_report_quoting_a_fixture_ends_the_run_as_its_answer(tmp_path):
    """End to end: the report is the run's answer, one model call, no note."""
    s, calls, events, _, offered = _agent(tmp_path, [FENCED_FIXTURE_REVIEW, "That was a quotation."])
    assert "list_files" in offered[0]
    assert len(calls) == 1 and not events
    assert s.summary == FENCED_FIXTURE_REVIEW
