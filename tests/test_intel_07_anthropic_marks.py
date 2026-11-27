"""The intelligence plan, release 7: the marks the Anthropic adapter puts in a
request ("cache breakpoints"), through its real request building.

A request may carry at most FOUR marks, and the Messages API refuses an empty
text block. The adapter marks the system prompt, the last tool definition, the
end of the newest message's own words (``LLMMessage.stable_chars``, release 7)
and the last block of the last message. For every shape the newest message
can take, through ``complete()`` AND ``stream()`` with the SDK client captured
(never a network call):

* at most four marks in the whole request;
* no empty (or blank) text block anywhere;
* the newest message's text, joined, is exactly what was passed in;
* the caller's own messages are not changed by the call, so marks cannot pile
  up from one call to the next.
"""

from __future__ import annotations

import copy
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from iron_jarvis.context import prefix as P
from iron_jarvis.providers.adapters.anthropic import AnthropicAdapter
from iron_jarvis.providers.adapters.base import LLMMessage, ToolCall

OWN = "what does the second line say?"
ONCE = "\n\n" + P.ONCE_TOP + "\n" + P.ONCE_HEAD + "\nToday's date is 2031-02-03.\n" + P.ONCE_BOTTOM
PICTURE = {"media_type": "image/png", "data_b64": "AAAA"}
SYSTEM = "You are Iron Jarvis."
TOOLS = [
    {"name": "read_file", "description": "Read a file.", "input_schema": {"type": "object", "properties": {}}},
    {"name": "list_files", "description": "List files.", "input_schema": {"type": "object", "properties": {}}},
]
EARLIER = [LLMMessage(role="user", content="hello"), LLMMessage(role="assistant", content="Hi.")]
TOOL_ROUND = [
    LLMMessage(role="user", content=OWN + ONCE, stable_chars=len(OWN)),
    LLMMessage(role="assistant", content="", tool_calls=[ToolCall(id="c1", name="read_file", arguments={"path": "a"})]),
]

#: name -> (the messages before the newest, the newest message)
SHAPES = {
    "a plain string": (EARLIER, LLMMessage(role="user", content=OWN)),
    "a string with its own words marked": (EARLIER, LLMMessage(role="user", content=OWN + ONCE, stable_chars=len(OWN))),
    "the same with a picture": (EARLIER, LLMMessage(role="user", content=OWN + ONCE, stable_chars=len(OWN),
                                                    images=[dict(PICTURE)])),
    "a list of text blocks": (EARLIER, LLMMessage(role="user", content=[
        {"type": "text", "text": OWN}, {"type": "text", "text": ONCE}])),
    "a list of text blocks with a hint": (EARLIER, LLMMessage(role="user", stable_chars=1, content=[
        {"type": "text", "text": OWN}, {"type": "text", "text": ONCE}])),
    "a list with an image block": (EARLIER, LLMMessage(role="user", content=[
        {"type": "text", "text": OWN},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}}])),
    "a tool result": (TOOL_ROUND, LLMMessage(role="tool", content="first line\nsecond line", tool_call_id="c1",
                                             name="read_file")),
    "an empty tool result": (TOOL_ROUND, LLMMessage(role="tool", content="", tool_call_id="c1", name="read_file")),
    "an empty string": (EARLIER, LLMMessage(role="user", content="")),
    "a blank string": (EARLIER, LLMMessage(role="user", content="  \n")),
    "a hint of 0": (EARLIER, LLMMessage(role="user", content=OWN + ONCE, stable_chars=0)),
    "a hint equal to the full length": (EARLIER, LLMMessage(role="user", content=OWN + ONCE,
                                                            stable_chars=len(OWN + ONCE))),
    "a hint beyond the length": (EARLIER, LLMMessage(role="user", content=OWN + ONCE, stable_chars=10_000)),
    "blank own words before the cut": (EARLIER, LLMMessage(role="user", content="  " + ONCE, stable_chars=2)),
    "nothing after the cut but blanks": (EARLIER, LLMMessage(role="user", content=OWN + "  \n", stable_chars=len(OWN))),
    "a picture alone": (EARLIER, LLMMessage(role="user", content="", images=[dict(PICTURE)])),
}


class _Messages:
    """Records every request the adapter hands the SDK, both entry points."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def _msg(self):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    async def create(self, **kw):
        self.calls.append(kw)
        return self._msg()

    def stream(self, **kw):
        self.calls.append(kw)
        outer = self

        class _S:
            def __aiter__(self):
                async def gen():
                    if False:  # pragma: no cover - no events needed
                        yield None

                return gen()

            async def get_final_message(self):
                return outer._msg()

        @asynccontextmanager
        async def cm():
            yield _S()

        return cm()


def _adapter(monkeypatch) -> "tuple[AnthropicAdapter, _Messages]":
    sdk = _Messages()
    adapter = AnthropicAdapter(model="claude-test", api_key="not-a-real-key")
    monkeypatch.setattr(adapter, "_client", lambda: SimpleNamespace(messages=sdk))
    return adapter, sdk


async def _send(adapter: AnthropicAdapter, how: str, messages: list, *, system=SYSTEM, tools=TOOLS) -> None:
    if how == "complete":
        await adapter.complete(system=system, messages=messages, tools=tools)
    else:
        async for _ in adapter.stream(system=system, messages=messages, tools=tools):
            pass


def _blocks(request: dict):
    """Every content block of a request, wherever it sits (a tool result's
    own blocks too)."""
    system = request.get("system")
    for block in system if isinstance(system, list) else []:
        yield block
    for message in request["messages"]:
        content = message["content"]
        for block in content if isinstance(content, list) else []:
            yield block
            inner = block.get("content") if isinstance(block, dict) else None
            for nested in inner if isinstance(inner, list) else []:
                yield nested


def marks(request: dict) -> int:
    return (sum(1 for b in _blocks(request) if isinstance(b, dict) and "cache_control" in b)
            + sum(1 for t in request.get("tools") or [] if "cache_control" in t))


def joined(content) -> str:
    """The text of one message as the provider reads it."""
    if isinstance(content, str):
        return content
    out = []
    for block in content:
        if block.get("type") == "text":
            out.append(block["text"])
        elif block.get("type") == "tool_result":
            out.append(joined(block.get("content") or ""))
    return "".join(out)


def passed_in(message: LLMMessage) -> str:
    return joined(message.content) if isinstance(message.content, list) else (message.content or "")


@pytest.mark.parametrize("how", ("complete", "stream"))
@pytest.mark.parametrize("shape", sorted(SHAPES))
async def test_a_request_has_at_most_four_marks_no_empty_text_and_the_newest_message_whole(monkeypatch, how, shape):
    before, newest = SHAPES[shape]
    messages = [*copy.deepcopy(before), copy.deepcopy(newest)]
    kept = copy.deepcopy(messages)
    adapter, sdk = _adapter(monkeypatch)
    await _send(adapter, how, messages)
    request = sdk.calls[-1]

    assert marks(request) <= 4, f"{shape}: {marks(request)} marks"
    assert marks(request) >= 2, "control: the system prompt and the tool list are marked"
    for block in _blocks(request):
        if isinstance(block, dict) and block.get("type") == "text":
            assert str(block.get("text") or "").strip(), f"{shape}: an empty text block: {block!r}"
    assert joined(request["messages"][-1]["content"]) == passed_in(newest), shape
    assert len(request["messages"]) == len(messages)
    # The call changed nothing of the caller's own messages.
    assert messages == kept, f"{shape}: the caller's messages were changed"


@pytest.mark.parametrize("how", ("complete", "stream"))
async def test_the_cut_is_marked_and_the_message_before_it_comes_back_the_same(monkeypatch, how):
    """The four marks of a plain chat reply: the system prompt, the last tool,
    the end of the person's own words, the end of the message. The next reply
    sends that message as its first block's text alone."""
    adapter, sdk = _adapter(monkeypatch)
    newest = LLMMessage(role="user", content=OWN + ONCE, stable_chars=len(OWN))
    await _send(adapter, how, [*EARLIER, newest])
    request = sdk.calls[-1]
    assert marks(request) == 4
    assert request["messages"][-1]["content"] == [
        {"type": "text", "text": OWN, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": ONCE, "cache_control": {"type": "ephemeral"}},
    ]
    later = [*EARLIER, LLMMessage(role="user", content=OWN), LLMMessage(role="assistant", content="It says so."),
             LLMMessage(role="user", content="thanks" + ONCE, stable_chars=len("thanks"))]
    await _send(adapter, how, later)
    again = sdk.calls[-1]
    assert again["messages"][2] == {"role": "user", "content": OWN}, "the earlier message carries no mark now"
    assert marks(again) == 4


@pytest.mark.parametrize("how", ("complete", "stream"))
async def test_marks_do_not_pile_up_over_the_calls_of_one_conversation(monkeypatch, how):
    """A caller that hands content over as blocks keeps its own list: a mark
    written into it would still be there on the next call. Six calls of one
    growing conversation, the same message objects each time: never more than
    four marks, and the objects are never changed."""
    adapter, sdk = _adapter(monkeypatch)
    conversation: list = []
    for n in range(6):
        conversation.append(LLMMessage(role="user", content=[
            {"type": "text", "text": f"question {n}"}, {"type": "text", "text": ONCE}]))
        kept = copy.deepcopy(conversation)
        await _send(adapter, how, conversation)
        assert marks(sdk.calls[-1]) <= 4, f"call {n + 1}: {marks(sdk.calls[-1])} marks"
        assert conversation == kept, f"call {n + 1}: the caller's own blocks were marked"
        conversation.append(LLMMessage(role="assistant", content=f"answer {n}"))


@pytest.mark.parametrize("how", ("complete", "stream"))
async def test_a_lone_first_message_and_no_system_or_tools_send_nothing_empty(monkeypatch, how):
    adapter, sdk = _adapter(monkeypatch)
    for newest in (LLMMessage(role="user", content=OWN + ONCE, stable_chars=len(OWN)),
                   LLMMessage(role="user", content=OWN)):
        await _send(adapter, how, [newest], system="", tools=[])
        request = sdk.calls[-1]
        assert marks(request) <= 1 and request["system"] == "" and request["tools"] == []
        assert joined(request["messages"][-1]["content"]) == newest.content
