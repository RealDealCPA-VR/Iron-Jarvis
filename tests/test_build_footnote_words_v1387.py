"""Readiness 12A (the Phase 3 review) — a "not armed" footnote says only what
is true where the reply was made.

THE DEFECT: both chat lanes end a reply that had a tool refused "not armed"
with a footnote naming the CHAT PAGE's Tools menu ("_Note: Jarvis couldn't
create a document (not turned on for this chat) — turn on Auto tools in the
chat's Tools menu, then ask for it in your message._"). A Build session (the
IJ harness) has no Tools menu, nor does a Build terminal's chat or the phone,
so the footnote sent the user somewhere that is not there.

NOW ``chat_turn.refusal_footnotes(refused, surface=)`` reads where the reply
is read — the caller's ``surface_for(body)`` (a Build pane), else the turn's
own ``failure_words.current_surface()`` ("build" inside a Build session's
turn, "phone" inside the phone's) — and off the chat page says "not armed"
in words true there with NO place. The chat page's footnotes are byte for
byte as before. Both lanes pass it, lock-step.

Driven through the REAL lanes (the v1.344.0 recovery harness: a real
platform, the real registry gate, a scripted model) and through the REAL
``POST /build/sessions/{id}/turn`` route (the build-turn harness).
"""

from __future__ import annotations

import inspect

import pytest

from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.chat_turn import NOT_ARMED_ELSEWHERE, refusal_footnotes
from iron_jarvis.daemon.routes import chat as chat_routes
from iron_jarvis.providers import failure_words as fw
from iron_jarvis.providers.adapters.base import LLMResponse, ToolCall
from tests.test_build_sessions_v1367 import Script, messages_of, new_session, wire
from tests.test_build_turns_v1367 import _run, app, work  # noqa: F401 — fixtures
from tests.test_chat_recovery_v1344 import _FakeTool, _app, _body, _run_post, _run_stream

#: A built-in the chat page offers a place for (Web & research).
WEB = "web_search"
#: The armed tool every lane run below has, so the turn has a tool loop.
LOOKUP = "lookup_fake"

PAGE_NOTE = (
    "\n\n_Note: Jarvis couldn't search the web (not turned on for this chat)"
    " — turn on Web & research in the chat's Tools menu._"
)


# --------------------------------------------------------------------------- #
# The words
# --------------------------------------------------------------------------- #
def test_the_chat_page_keeps_its_words_byte_for_byte():
    assert refusal_footnotes([(WEB, "not armed")]) == PAGE_NOTE
    assert refusal_footnotes([(WEB, "not armed")], surface=None) == PAGE_NOTE
    assert refusal_footnotes([("mcp__github__create_issue", "not armed")]) == (
        "\n\n_Note: GitHub › create issue could not run (not turned on for this chat)"
        " — turn GitHub on in the chat's Tools menu › Connections._"
    )


@pytest.mark.parametrize("where", sorted(NOT_ARMED_ELSEWHERE))
def test_off_the_chat_page_a_not_armed_refusal_names_no_menu(where):
    words = NOT_ARMED_ELSEWHERE[where]
    out = refusal_footnotes([(WEB, "not armed"), ("mcp__github__create_issue", "not armed")], surface=where)
    assert out == (
        f"\n\n_Note: Jarvis couldn't search the web ({words})._"
        f"\n\n_Note: GitHub › create issue could not run ({words})._"
    )
    for place in ("Tools menu", "Connections", "Auto tools", "Web & research", "for this chat"):
        assert place not in out, (where, place)


def test_the_turns_own_surface_decides_when_the_caller_names_none():
    token = fw.set_build_surface()
    try:
        assert refusal_footnotes([(WEB, "not armed")]) == (
            "\n\n_Note: Jarvis couldn't search the web (not available in this reply)._"
        )
    finally:
        fw.reset_surface(token)
    with fw.phone_surface():
        assert "(not available from your phone)" in refusal_footnotes([(WEB, "not armed")])
    # Back on the chat page.
    assert refusal_footnotes([(WEB, "not armed")]) == PAGE_NOTE


def test_other_refusals_keep_their_words_everywhere():
    for where in (None, *NOT_ARMED_ELSEWHERE):
        assert refusal_footnotes([(WEB, "permission denied")], surface=where) == (
            "\n\n_Note: Jarvis couldn't search the web (permission denied)._"
        )
        assert refusal_footnotes([(WEB, "low trust")], surface=where) == (
            "\n\n_Note: Jarvis couldn't search the web (blocked under low trust)._"
        )


def test_both_lanes_pass_the_surface_lock_step():
    """MIRROR NOTE: every footnote site in both lanes names the surface.
    (12A review: the stream lane names it through `_footnote_place`, which
    adds the browser sidebar's — tests/test_readiness_12a_review_v1387.)"""
    for module in (chat_turn, chat_routes):
        src = inspect.getsource(module)
        sites = src.count("refusal_footnotes(refused_tools")
        named = src.count("refusal_footnotes(refused_tools, surface=")
        assert sites == named == 2, (module.__name__, sites, named)
    stream = inspect.getsource(chat_routes)
    assert stream.count("refusal_footnotes(refused_tools, surface=_footnote_place)") == 2
    assert "_surface_for(body)" in stream[stream.index("_footnote_place = ("):][:300]


# --------------------------------------------------------------------------- #
# Both real lanes
# --------------------------------------------------------------------------- #
def _rounds() -> list[LLMResponse]:
    return [
        LLMResponse(text="", tool_calls=[ToolCall(id="c1", name=WEB, arguments={"q": "x"})]),
        LLMResponse(text="That tool was not available."),
    ]


async def _both(tmp_path, body_kw=None):
    out = {}
    for lane in ("post", "stream"):
        armed, web = _FakeTool(LOOKUP), _FakeTool(WEB)
        platform = _app(tmp_path / lane, armed, web)
        body = _body(LOOKUP)
        for k, v in (body_kw or {}).items():
            setattr(body, k, v)
        if lane == "post":
            got, _ = await _run_post(platform, body, _rounds())
        else:
            got, _, _ = await _run_stream(platform, body, _rounds())
        assert web.calls == [], "anti-vacuity: the gate refused the unarmed tool"
        out[lane] = got["reply"]
    return out


async def test_the_chat_page_lanes_are_unchanged(tmp_path):
    for lane, reply in (await _both(tmp_path)).items():
        assert reply.endswith(PAGE_NOTE), (lane, reply)


async def test_a_build_sessions_lanes_name_no_menu(tmp_path):
    token = fw.set_build_surface()
    try:
        replies = await _both(tmp_path)
    finally:
        fw.reset_surface(token)
    for lane, reply in replies.items():
        assert reply.endswith(
            "\n\n_Note: Jarvis couldn't search the web (not available in this reply)._"
        ), (lane, reply)
        assert "Tools menu" not in reply, lane


async def test_a_build_terminals_chat_names_no_menu(tmp_path):
    for lane, reply in (await _both(tmp_path, {"pane_id": "pane_1"})).items():
        assert reply.endswith(
            "\n\n_Note: Jarvis couldn't search the web (not available in this terminal's chat)._"
        ), (lane, reply)


async def test_the_phones_lanes_name_no_menu(tmp_path):
    with fw.phone_surface():
        replies = await _both(tmp_path)
    for lane, reply in replies.items():
        assert reply.endswith(
            "\n\n_Note: Jarvis couldn't search the web (not available from your phone)._"
        ), (lane, reply)


# --------------------------------------------------------------------------- #
# The real Build route
# --------------------------------------------------------------------------- #
async def test_a_real_build_turn_saves_a_reply_that_names_no_menu(app, work):  # noqa: F811
    """RED on the old code: the saved reply read "… — turn on Auto tools in
    the chat's Tools menu, then ask for it in your message._"."""
    sid = new_session(app, work)["id"]
    wire(app, Script([
        [ToolCall(id="c1", name="write_document", arguments={"path": "notes.docx", "text": "hi"})],
        "I could not make the document.",
    ]))
    await _run(app, sid, "make notes.docx", "bt-foot-1")
    msgs = messages_of(app, sid)
    reply = msgs[-1]["content"]
    assert msgs[-1]["role"] == "assistant"
    assert "Jarvis couldn't create a document (not available in this reply)." in reply, reply
    for place in ("Tools menu", "Auto tools", "for this chat"):
        assert place not in reply, (place, reply)
    assert not (work / "notes.docx").exists(), "anti-vacuity: the unarmed tool never ran"
