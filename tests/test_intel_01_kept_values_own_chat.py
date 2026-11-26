"""Intelligence plan, release 1: a chat's kept values are its own.

Every chat turn runs its tools under the one session id ``"chat"``. The
place a long result is kept (``_store_as``, read back with the ``repl``
tool) was named by that id and the folder, so every chat in the same folder
shared one: a value a Local only chat kept could be read from a chat
answered by a cloud model. The script worker fixed this for itself in
v1.470.0 (``tools/tool_script.worker_key``); these tests pin the same rule
for kept values.

Each test goes through the real tool registry and a real namespace process:
the door that would have handed the value over is the one asked.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import Tool, ToolContext, ToolResult

SECRET = "ledger-row-9917"


class _ListingTool(Tool):
    """A wordy reader: its text is in ``output``, as a real one's is."""

    name = "big_list"
    description = "test double"
    input_schema = {"type": "object", "properties": {}}

    async def execute(self, args, ctx):  # noqa: D102
        return ToolResult(ok=True, output=f"{SECRET}\nrow two", data={"count": 2})


@pytest.fixture()
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.registry.register(_ListingTool())
    return p


def _chat(platform, tmp_path, *, thread: str = "", turn: str = "") -> ToolContext:
    """A chat turn's tool context: the id is always "chat" (both lanes)."""
    ws = Path(tmp_path) / "ws"
    ws.mkdir(exist_ok=True)
    return ToolContext(
        workspace=ws,
        session_id="chat",
        agent_run_id="chat",
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
        thread_id=thread,
        turn_id=turn,
    )


async def _invoke(platform, ctx, name, args):
    return await platform.registry.invoke(
        name, args, ctx, platform.permissions, session_allow=[name, "repl"]
    )


async def _read(platform, ctx, name: str = "kept"):
    """Ask the `repl` tool for the kept value by name."""
    return await _invoke(
        platform, ctx, "repl", {"code": f"print({name}['output'])"}
    )


def run(platform, body):
    async def wrapper():
        try:
            return await body()
        finally:
            if platform.repl is not None:
                await platform.repl.dispose_all()

    return asyncio.run(wrapper())


def _saved_chat(platform, thread_id: str, *, local_only: bool) -> None:
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ChatThreadRecord

    with session_scope(platform.engine) as db:
        db.add(
            ChatThreadRecord(
                id=thread_id,
                title=thread_id,
                setup_json=json.dumps({"local_only": True} if local_only else {}),
            )
        )
        db.commit()


@pytest.mark.parametrize("first_is_local_only", [False, True], ids=["two chats", "a Local only chat, then a cloud-answered one"])
def test_a_value_kept_in_one_chat_cannot_be_read_from_another_chat_in_the_same_folder(
    platform, tmp_path, first_is_local_only
):
    async def body():
        from iron_jarvis.providers.egress import thread_local_only

        _saved_chat(platform, "chat_first", local_only=first_is_local_only)
        _saved_chat(platform, "chat_second", local_only=False)
        assert thread_local_only(platform.engine, "chat_first") is first_is_local_only
        assert thread_local_only(platform.engine, "chat_second") is False

        first = _chat(platform, tmp_path, thread="chat_first")
        second = _chat(platform, tmp_path, thread="chat_second")
        assert first.workspace == second.workspace, "the gap is two chats in ONE folder"

        kept = await _invoke(platform, first, "big_list", {"_store_as": "kept"})
        assert kept.ok and SECRET not in kept.output, kept.output

        # The chat that kept it reads it back...
        own = await _read(platform, first)
        assert own.ok and SECRET in own.output, own.error or own.output

        # ...and the other chat, in the same folder, has no such value.
        other = await _read(platform, second)
        text = f"{other.output}\n{other.error or ''}"
        assert SECRET not in text, "one chat read another chat's kept value"
        assert "NameError" in text, text

        # Nor is it anywhere in what the second chat can list.
        names = await _invoke(
            platform, second, "repl", {"code": "print('kept' in dir())"}
        )
        assert names.ok and "False" in names.output, names.error or names.output

    run(platform, body)


def test_a_variable_set_in_the_repl_tool_stays_in_its_own_chat(platform, tmp_path):
    """The same wall for the `repl` tool by itself (no `_store_as`)."""

    async def body():
        first = _chat(platform, tmp_path, thread="chat_a")
        second = _chat(platform, tmp_path, thread="chat_b")
        made = await _invoke(platform, first, "repl", {"code": f"note = '{SECRET}'"})
        assert made.ok, made.error
        seen = await _invoke(
            platform, second, "repl", {"code": "print('note' in dir())"}
        )
        assert seen.ok and "False" in seen.output, seen.error or seen.output
        back = await _invoke(platform, first, "repl", {"code": "print(note)"})
        assert back.ok and SECRET in back.output

    run(platform, body)


def test_the_chat_that_kept_a_value_reads_it_on_its_next_message(platform, tmp_path):
    """Each message builds a NEW tool context; the chat's id is what lasts."""

    async def body():
        await _invoke(
            platform, _chat(platform, tmp_path, thread="chat_a", turn="turn_1"),
            "big_list", {"_store_as": "kept"},
        )
        later = await _read(platform, _chat(platform, tmp_path, thread="chat_a", turn="turn_2"))
        assert later.ok and SECRET in later.output, later.error or later.output

    run(platform, body)


def test_a_new_chats_first_message_keeps_its_values_once_the_chat_is_saved(
    platform, tmp_path
):
    """A new chat has no id during its first message: the value is kept under
    the turn, and follows the chat when the page saves it."""

    async def body():
        from iron_jarvis.tools.tool_script import adopt_turn_worker

        first = _chat(platform, tmp_path, turn="turn_new")
        kept = await _invoke(platform, first, "big_list", {"_store_as": "kept"})
        assert kept.ok

        # Another new chat's first message, same folder: not its value.
        stranger = await _read(platform, _chat(platform, tmp_path, turn="turn_other"))
        assert SECRET not in f"{stranger.output}\n{stranger.error or ''}"

        moved = await adopt_turn_worker(platform, "turn_new", "chat_saved")
        assert moved >= 1, "the kept values did not follow the chat"

        nxt = await _read(platform, _chat(platform, tmp_path, thread="chat_saved", turn="turn_2"))
        assert nxt.ok and SECRET in nxt.output, nxt.error or nxt.output

    run(platform, body)


def test_a_turn_the_page_already_linked_keeps_its_values_under_the_chat(
    platform, tmp_path
):
    """The link usually lands BEFORE the first tool call: the value then goes
    straight to the chat's own place."""

    async def body():
        from iron_jarvis.providers.egress import link_turn_holds

        link_turn_holds("turn_linked", "chat_linked")
        await _invoke(
            platform, _chat(platform, tmp_path, turn="turn_linked"),
            "big_list", {"_store_as": "kept"},
        )
        nxt = await _read(platform, _chat(platform, tmp_path, thread="chat_linked"))
        assert nxt.ok and SECRET in nxt.output, nxt.error or nxt.output

    run(platform, body)


def test_a_call_that_belongs_to_no_chat_keeps_its_values_for_that_turn_only(
    platform, tmp_path
):
    """A caller that names no chat and no turn (a script against the API):
    the value lasts for the turn that kept it and no other turn finds it."""

    async def body():
        one = _chat(platform, tmp_path)
        kept = await _invoke(platform, one, "big_list", {"_store_as": "kept"})
        assert kept.ok and SECRET not in kept.output
        same_turn = await _read(platform, one)
        assert same_turn.ok and SECRET in same_turn.output, same_turn.error

        other_turn = await _read(platform, _chat(platform, tmp_path))
        assert SECRET not in f"{other_turn.output}\n{other_turn.error or ''}"

    run(platform, body)


def test_an_agent_run_keeps_its_own_place_as_before(platform, tmp_path):
    """A run has an id of its own: nothing about it changes."""

    async def body():
        from iron_jarvis.tools.registry import values_key

        ctx = _chat(platform, tmp_path, thread="chat_a")
        ctx.session_id = "sess_run_1"
        assert values_key(ctx) == "sess_run_1"
        await _invoke(platform, ctx, "big_list", {"_store_as": "kept"})
        back = await _read(platform, ctx)
        assert back.ok and SECRET in back.output
        assert "sess_run_1" in platform.repl

    run(platform, body)


def test_no_chat_value_lives_under_the_shared_name(platform, tmp_path):
    """The old shared place ("chat" + the folder) is never created."""

    async def body():
        for ctx in (
            _chat(platform, tmp_path, thread="chat_a"),
            _chat(platform, tmp_path, turn="turn_b"),
            _chat(platform, tmp_path),
        ):
            await _invoke(platform, ctx, "big_list", {"_store_as": "kept"})
            await _invoke(platform, ctx, "repl", {"code": "x = 1"})
        assert "chat" not in platform.repl.session_ids, platform.repl.session_ids
        assert len(platform.repl) == 3

    run(platform, body)
