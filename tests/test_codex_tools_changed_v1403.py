"""v1.403.0 part D REVIEW — an UNLOCKED team lead on Codex could not use the
tools it was just given.

When no worker of a coding session's team can take a piece, the lane adds the
lead's coding tools to the NEXT request (``chat_turn.apply_team_unlock``) and
the refusal tells the lead it may do the piece itself. The Codex adapter then
RESUMED the turn parked on the refused hand-off — a Codex thread whose dynamic
tools were fixed when it started (the lead's, with no tool that writes), so
the model was told it may write and could not. A parked thread whose tools
differ from the call's is now a MISS: a new thread with this call's tools and
the history replayed, as for any other miss. Driven through the real adapter
against the fake app-server (``fixtures/fake_codex_app_server_v1365.py``).
"""

from __future__ import annotations

from iron_jarvis.providers.adapters.codex_native import session as CS
from tests.test_codex_native_v1365 import TOOLS, Fake, _after, _clean, _user  # noqa: F401


async def test_codex_a_parked_turn_is_not_resumed_when_the_calls_tools_changed(tmp_path, monkeypatch):
    first = {"steps": [{"text": "Handing it out."}, {"call": {"tool_index": 0, "args": {"q": "x"}}},
                       {"text": "never"}]}
    second = {"steps": [{"text": "Now I can write it myself."}]}
    fake = Fake(tmp_path, monkeypatch, [first, second])
    adapter = fake.adapter()
    locked = TOOLS[:1]
    unlocked = TOOLS  # the lead's coding tools joined the request
    msgs = [_user("set X")]
    r1 = await adapter.complete(system="sys", messages=msgs, tools=locked)
    assert r1.tool_calls and CS.POOL.live()[0].state == "paused"
    r2 = await adapter.complete(system="sys", messages=_after(msgs, r1, {"lookup_fake": "REFUSED"}),
                                tools=unlocked)
    assert r2.text == "Now I can write it myself."
    # A NEW thread, started with the call's tools (the old one, with the
    # lead's tools only, is gone).
    assert len(fake.starts()) == 2
    starts = fake.received("thread/start")
    shown = [t["name"] for t in starts[-1]["msg"]["params"]["dynamicTools"]]
    assert len(shown) == len(unlocked)
    assert len(starts[0]["msg"]["params"]["dynamicTools"]) == 1


async def test_codex_a_parked_turn_with_the_same_tools_is_still_resumed(tmp_path, monkeypatch):
    """The control: an unchanged tool list resumes the same thread (one
    process) exactly as before."""
    first = {"steps": [{"call": {"tool_index": 0, "args": {"q": "x"}}}, {"text": "resumed"}]}
    fake = Fake(tmp_path, monkeypatch, [first, {"steps": [{"text": "a new thread"}]}])
    adapter = fake.adapter()
    msgs = [_user("set X")]
    r1 = await adapter.complete(system="sys", messages=msgs, tools=TOOLS)
    r2 = await adapter.complete(system="sys", messages=_after(msgs, r1, {"lookup_fake": "OK"}), tools=list(TOOLS))
    assert r2.text == "resumed" and len(fake.starts()) == 1


