"""A reply that PROMISES a lasting change with nothing behind it is said so
(v1.487.0).

Seen live, each with no tool call: "I'll respond in English from now on, even
if you write in Dutch", "I'll keep things short and skip bullet points from now
on", "I'll remember that you prefer proven stability". ``change_claims`` read
the first as a conditional offer ("even IF") and knew neither of the other two
shapes, so no note was added and the look-first check (when on) had nothing
concrete to show the model. Now the note says what was not changed, and the
check's note leads with that finding.
"""

from __future__ import annotations

import pytest

from iron_jarvis.daemon import chat_turn as CT
from iron_jarvis.daemon.change_claims import claimed_change_note
from iron_jarvis.providers.adapters.base import LLMMessage


@pytest.mark.parametrize("reply,said", [
    ("Got it — I'll respond in English from now on, even if you write in Dutch.", "Answer in this language"),
    ("Got it. I'll keep things short and skip bullet points from now on.", "Formatting"),
    ("I'll always keep my answers brief from now on.", "Formatting"),
    ("Got it. I'll remember that you prefer proven stability over new features.", "No preference was changed"),
    ("Understood, I will keep that in mind for next time.", "No preference was changed"),
])
def test_an_unbacked_promise_gets_the_note(reply, said):
    assert said in claimed_change_note(reply, [], [])


@pytest.mark.parametrize("reply,ran", [
    ("Got it. I'll remember that you prefer proven stability.", ["remember_preference"]),
    ("I'll keep things short from now on.", ["remember_preference"]),
    ("If you want, I'll always reply in German.", []),
    ("I'll remember to ask you next time.", []),
    ("Sure, I'll keep it short.", []),
    ("I will keep it simple: here is the summary.", []),
    ("Do you want me to keep things short from now on?", []),
])
def test_a_backed_conditional_or_one_off_promise_gets_none(reply, ran):
    assert claimed_change_note(reply, ran, []) == ""


def test_the_check_note_leads_with_what_was_found():
    reply = "Got it. I'll remember that you prefer stable updates."
    note = CT.look_first_note(reply, [], 0)
    assert note.startswith("(From Iron Jarvis, not from the user. Iron Jarvis read your reply above and found: "
                           "No preference was changed in this reply.")
    assert note.endswith(CT.LOOK_FIRST_NOTE[len("(From Iron Jarvis, not from the user. "):])


def test_the_check_note_is_plain_when_nothing_is_claimed_or_a_tool_backs_it():
    assert CT.look_first_note("Here is your summary.", [], 0) == CT.LOOK_FIRST_NOTE
    msgs = [LLMMessage(role="user", content="q"), LLMMessage(role="tool", name="remember_preference", content="ok")]
    reply = "Got it. I'll remember that you prefer stable updates."
    assert CT.look_first_note(reply, msgs, 1) == CT.LOOK_FIRST_NOTE


def test_the_promise_shapes_are_linear_on_pathological_text():
    """A child process with a deadline (a hang detector, never a speed bar)."""
    import subprocess
    import sys
    import textwrap

    code = textwrap.dedent(
        """
        import time
        from iron_jarvis.daemon.change_claims import claimed_change_note
        N = 200_000
        for text in ("i'll keep " * (N // 10), "i'll remember " * (N // 14),
                     "i will keep my answers " * (N // 23), "even if " * (N // 8)):
            t0 = time.perf_counter(); claimed_change_note(text, [], [])
            print(round(time.perf_counter() - t0, 3), flush=True)
        """
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.count("\n") == 4, out.stdout


def test_a_style_promise_that_also_says_remember_is_named_once():
    note = claimed_change_note("I'll keep things short from now on and I'll remember that.", [], [])
    assert "Formatting" in note and "No preference was changed" not in note


@pytest.mark.asyncio
async def test_the_check_sends_its_note_with_the_finding(tmp_path):
    """Through ``look_first_call`` itself: the note the model is shown."""
    from types import SimpleNamespace

    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.providers.adapters.base import LLMResponse

    app = create_app(str(tmp_path / "home"))
    p = app.state.platform
    seen = []

    async def complete(**kw):
        seen.append(kw["messages"][-1].content)
        return SimpleNamespace(response=LLMResponse(text="KEEP"), provider="mock", model="mock")

    p.router.complete = complete
    reply = "Got it. I'll remember that you prefer stable updates."
    msgs = [LLMMessage(role="user", content="don't give me brand-new updates")]
    out = await CT.look_first_call(SimpleNamespace(platform=p), msgs=msgs, system="s", provider="mock",
                                   model="mock", head=1, tools=[], reply=reply)
    assert out[0] is None  # KEEP: the reply stands
    assert seen and "found: No preference was changed in this reply." in seen[0]
    try:
        p.scheduler.shutdown()
    except Exception:  # noqa: BLE001
        pass
