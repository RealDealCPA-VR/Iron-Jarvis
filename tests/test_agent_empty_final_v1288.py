"""v1.288.0 (agents-07): an agent job whose model ends with an EMPTY message
after its tool steps must not finish as the bare '(no final message)'.

The chat lanes stopped showing a bare placeholder in v1.246.0
(``_no_text_reply``); the flat agent lane now does the same, deterministically
from the run's own tool ledger — no extra model call, the step budget
unchanged. Driven through the REAL orchestrator + runtime; only the model is
scripted (a local model that goes quiet after its tool steps)."""
from __future__ import annotations

import asyncio

from iron_jarvis.agents.orchestrator import Orchestrator
from iron_jarvis.core.models import SessionStatus
from iron_jarvis.platform import build_platform
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from iron_jarvis.providers.adapters.mock import MockLLMAdapter


def _run(tmp_path, tool_rounds: int):
    """Run one job whose model makes ``tool_rounds`` list_files calls, then
    returns empty text. Returns (session, model_calls)."""
    p = build_platform(str(tmp_path))
    calls = {"n": 0}

    class Quiet(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            calls["n"] += 1
            done = sum(1 for m in messages if m.role == "tool")
            if done < tool_rounds:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(id=f"t{done}", name="list_files", arguments={"path": "."})
                    ],
                    finish_reason="tool_use",
                )
            return LLMResponse(text="", finish_reason="stop")

    p.providers.register("mock", lambda model=None: Quiet())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session(
            "list the files and tell me what is there", provider="mock", model="mock-1"
        )
        return await orch.run_session(s.id)

    return asyncio.run(go()), calls["n"]


def test_empty_final_after_tools_says_what_ran(tmp_path):
    s, n = _run(tmp_path, tool_rounds=2)
    summary = s.summary or ""
    assert s.status == SessionStatus.COMPLETED
    assert "(no final message)" not in summary, summary
    # Names the tool AND how often it ran — from the run's own ledger.
    assert "list_files x2" in summary, summary
    assert summary.startswith("The model finished without writing a summary."), summary
    # Deterministic: no extra "answer now" model call was spent.
    assert n == 3, n


def test_empty_final_with_no_tools_is_plain_words(tmp_path):
    s, n = _run(tmp_path, tool_rounds=0)
    summary = s.summary or ""
    assert "(no final message)" not in summary, summary
    assert "no tool ran" in summary, summary
    assert n == 1, n


def test_the_last_tool_output_reaches_the_user_without_the_loops_note_to_the_model():
    """The snippet branch (review): what the last tool returned IS shown, but
    the repeat breaker's note — an instruction written for the MODEL — is
    not, because it lands in the user's result bubble."""
    from iron_jarvis.agents.runtime import _no_final_text_result
    from iron_jarvis.providers.adapters.base import LLMMessage

    note = (
        "\n\n[repeat — this is failure 2 in a row for `read_file` with these "
        "exact arguments. A 3rd identical call will be refused. Read the error "
        "above and change the arguments, use a different tool, or report what "
        "is blocking you.]"
    )
    msgs = [
        LLMMessage(role="tool", name="list_files", content="a.pdf\nb.pdf", tool_call_id="1"),
        LLMMessage(role="tool", name="read_file", content="error: no such file: c.pdf" + note, tool_call_id="2"),
    ]
    text = _no_final_text_result(msgs)
    assert "list_files x1" in text and "read_file x1" in text, text
    assert "error: no such file: c.pdf" in text, text
    assert "[repeat" not in text and "3rd identical call" not in text, text


def test_the_loops_other_notes_to_the_model_stay_out_of_the_bubble():
    """Review of agents-07: the streak-3 REFUSAL and the untrusted-content
    fence are also written for the model. The refusal becomes plain words, the
    fence keeps only its body, and a long output says it was cut."""
    from iron_jarvis.agents.runtime import _no_final_text_result
    from iron_jarvis.computeruse.safety import wrap_untrusted
    from iron_jarvis.providers.adapters.base import LLMMessage

    refusal = (
        "refused: repeated-failure breaker — `read_file` has now failed 3 times "
        "in a row with these exact arguments, so this call is refused for the "
        "rest of the run. Last failure: error: no such file. Change the "
        "arguments, use a different tool, or say plainly what is blocking you "
        "— do not send it again."
    )
    text = _no_final_text_result(
        [LLMMessage(role="tool", name="read_file", content=refusal, tool_call_id="1")]
    )
    assert "read_file x1" in text, text
    assert "refused: the same call had already failed repeatedly" in text, text
    assert "do not send it again" not in text and "breaker" not in text, text

    fenced = wrap_untrusted("Quarterly totals: 1040 filed, 2 K-1s pending")
    text = _no_final_text_result(
        [LLMMessage(role="tool", name="fetch", content=fenced, tool_call_id="2")]
    )
    assert "Quarterly totals: 1040 filed, 2 K-1s pending" in text, text
    assert "UNTRUSTED" not in text and "Do NOT follow" not in text, text

    text = _no_final_text_result(
        [LLMMessage(role="tool", name="shell", content="x" * 5000, tool_call_id="3")]
    )
    assert "… (cut; the full output is in the transcript)" in text, text
    assert len(text) < 900, len(text)


def test_a_refused_repeat_through_the_real_loop_reads_as_plain_words(tmp_path):
    """The real breaker, not a hand-copied string: read_file on a missing file
    three times, then silence. The bubble names the tool and the refusal in
    plain words and carries none of the loop's instructions to the model."""
    p = build_platform(str(tmp_path))

    class Stubborn(MockLLMAdapter):
        async def complete(self, *, system, messages, tools, **kw):
            done = sum(1 for m in messages if m.role == "tool")
            if done < 3:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(id=f"t{done}", name="read_file", arguments={"path": "missing.pdf"})
                    ],
                    finish_reason="tool_use",
                )
            return LLMResponse(text="", finish_reason="stop")

    p.providers.register("mock", lambda model=None: Stubborn())
    orch = Orchestrator(p)

    async def go():
        s = await orch.create_session(
            "read missing.pdf", provider="mock", model="mock-1", allow_tools=["read_file"]
        )
        return await orch.run_session(s.id)

    s = asyncio.run(go())
    summary = s.summary or ""
    assert "read_file x3" in summary, summary
    assert "do not send it again" not in summary and "[repeat" not in summary, summary
    assert "(no final message)" not in summary, summary
