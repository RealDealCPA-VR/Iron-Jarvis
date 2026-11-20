"""Idea 30-a: the figures of a reply link to where THIS turn read them.

* ``link_reply`` by itself: a figure the turn read links to its mark; a
  rounded "48.2k" does not; a figure found in nothing the model was given is
  ``#source/none``; several places with the same value are ONE link with
  several ids (a chooser); code, links and addresses are never touched; a
  ``#source/`` link the model wrote itself loses its link; a plain chat (no
  file read) is left alone.
* Both chat lanes, end to end through the real app: the model reads a PDF
  with ``read_document`` (and, separately, gets it as an attachment), and
  the reply that comes back (the POST body, the stream's done frame, which
  is what the page saves) links 48,210.00 to page 7. The two lanes give the
  same reply. Iron Jarvis's own notes under the reply are never linked.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.documents.sources import (
    NO_RECORD,
    SOURCE_LINK_PREFIX,
    link_reply,
    strip_model_links,
    with_linked_head,
)
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_leap30a_marks import make_pdf
from tests.test_wave3_chat_lanes_v1311 import _post_turn, _scripted_router

LANES = ("post", "stream")
_LINK = re.compile(r"\[([^\]]+)\]\(#source/([^)]+)\)")


def _mark(mid: str, key: str, path: str = "C:/f/a.pdf", **loc) -> dict:
    return {"id": mid, "key": key, "path": path, "kind": "pdf", "locator": loc or {"page": 1, "line": 1}}


# --------------------------------------------------------------------------- #
# link_reply by itself
# --------------------------------------------------------------------------- #
def test_a_figure_the_turn_read_links_and_an_invented_one_says_none():
    marks = [_mark("src_aaaa1111aaaa1111", "48210", page=7, line=3)]
    out = link_reply("Wages were $48,210.00, about 48.2k; bonus 73,519.", marks)
    assert out == (
        "Wages were $[48,210.00](#source/src_aaaa1111aaaa1111), about "
        "[48.2k](#source/none); bonus [73,519](#source/none)."
    )


def test_a_figure_in_something_the_model_was_given_is_left_plain():
    out = link_reply("Bonus 73,519 and fee 9,100.", [], seen={"73519"})
    assert out == "Bonus 73,519 and fee [9,100](#source/none)."


def test_several_places_with_one_value_are_one_link_to_a_chooser():
    marks = [
        _mark("src_1111111111111111", "1200", page=2, line=4),
        _mark("src_2222222222222222", "1200", path="C:/f/b.xlsx", sheet="S", cell="B2"),
        _mark("src_3333333333333333", "1200", page=2, line=4),  # the same place again
    ]
    out = link_reply("Rent 1,200 a month.", marks)
    assert out == "Rent [1,200](#source/src_1111111111111111,src_2222222222222222) a month."


@pytest.mark.parametrize("text", [
    "```\ntotal = 48210\n```",
    "Run `sum(48210)` there.",
    "See [the 48,210 report](https://example.com/48210).",
    "At https://example.com/r/48210 or <https://x.test/48210>",
    "    indented code 48210",
    '<abbr title="48210">x</abbr>',
])
def test_code_links_and_addresses_are_never_touched(text):
    marks = [_mark("src_aaaa1111aaaa1111", "48210")]
    assert link_reply(text, marks) == text


def test_small_numbers_years_and_dates_are_not_figures():
    text = "On 2026-11-20 in 2024, 3 files and 12 rows; Form K1."
    assert link_reply(text, []) == text


def test_a_model_written_source_link_loses_its_link_and_no_read_changes_nothing():
    forged = "It was [99,999](#source/src_aaaa1111aaaa1111) and [x](#source/none)."
    assert strip_model_links(forged) == "It was 99,999 and x."
    # No file read this turn: nothing linked, and the forged link still goes.
    assert link_reply(forged, [], read_any=False) == "It was 99,999 and x."
    # A turn that read: the figure is judged on Iron Jarvis's own record.
    marks = [_mark("src_bbbb2222bbbb2222", "99999")]
    assert link_reply(forged, marks) == "It was [99,999](#source/src_bbbb2222bbbb2222) and x."


_ID = "src_aaaa1111aaaa1111"


@pytest.mark.parametrize("forged", [
    f"Bonus was [99,999](<#source/{_ID}>).",                  # the <…> address
    f"Bonus was [99,999](\\#source/{_ID}).",                  # a backslash escape
    f"Bonus was [99,999](&#35;source/{_ID}).",                # a character reference
    f"Bonus was [99,999](&#x23;source/{_ID}).",
    f"Bonus was [99,999](#source/{_ID} \"a title\").",        # a title
    f"Bonus was [99,999 [net]](#source/{_ID}).",              # brackets in the words
    f"Bonus was ![99,999](#source/{_ID}).",                   # a picture
    f"Bonus was [99,999][1].\n\n[1]: #source/{_ID}",          # a reference
    f"Bonus was [99,999][1].\n\n[1]:\n   <#source/{_ID}> 'x'",
    f"Bonus was [99,999][].\n\n[99,999]: #source/{_ID}",      # collapsed
    f"Bonus was [99,999].\n\n[99,999]: #source/{_ID}",        # shortcut
    f"Bonus was [a [99,999](#source/{_ID})](#source/{_ID}).",  # a link inside a link
])
def test_a_model_written_source_link_loses_its_link_whatever_way_it_is_written(forged):
    """Review A3 / B2: the page's Markdown resolves each of these to
    ``#source/<id>``; every one is turned back into its words, so no form of
    a forged link reaches the page as a press that opens a record."""
    for out in (strip_model_links(forged), link_reply(forged, [], read_any=False)):
        assert "source/" not in out, out
        assert "99,999" in out, out
        assert "](" not in out and "]:" not in out, out
    # A turn that read: the figure is judged on Iron Jarvis's own record
    # (none here), never on the id the model wrote.
    linked = link_reply(forged, [], read_any=True, seen=set())
    assert _ID not in linked and "&#" not in linked and "\\#" not in linked, linked
    assert "[99,999](#source/none)" in linked, linked


def test_an_ordinary_link_and_reference_are_left_alone():
    text = "See [the site](https://example.com/x) and [r][w].\n\n[w]: https://e.com/48210"
    assert strip_model_links(text) == text


@pytest.mark.parametrize("text,linked", [
    # A nested list item four spaces or a tab deep is words, not code.
    ("- Income\n    - Wages: $48,210.00\n    - Bonus: 73,519",
     "- Income\n    - Wages: $[48,210.00](#source/src_aaaa1111aaaa1111)\n    - Bonus: [73,519](#source/none)"),
    ("- Income\n\t- Wages: $48,210.00", "- Income\n\t- Wages: $[48,210.00](#source/src_aaaa1111aaaa1111)"),
    ("1. Wages\n   - $48,210.00", "1. Wages\n   - $[48,210.00](#source/src_aaaa1111aaaa1111)"),
    # An indented code block after a blank line, outside a list, is code.
    ("Text\n\n    code 48210\n\nAfter 48210", "Text\n\n    code 48210\n\nAfter [48210](#source/src_aaaa1111aaaa1111)"),
    # A fence inside a list item is code at any depth.
    ("- a\n    ```\n    total 48210\n    ```\n- b 48210",
     "- a\n    ```\n    total 48210\n    ```\n- b [48210](#source/src_aaaa1111aaaa1111)"),
])
def test_a_nested_list_is_linked_and_code_still_is_not(text, linked):
    """Review A5: figures in list items indented by four spaces or a tab
    were never linked (read as an indented code block)."""
    marks = [_mark("src_aaaa1111aaaa1111", "48210")]
    assert link_reply(text, marks, seen=set()) == linked


@pytest.mark.parametrize("text", [
    "Saved to: C:\\Users\\me\\clients\\10432\\return.pdf",
    "I read statement 10432.pdf and client-10432-notes.txt for you.",
    "Open C:/clients/10432/summary.xlsx to see it.",
    "In C:\\Users\\me\\My Files\\10432\\a.pdf there.",
    "It is in clients/10432/2026 now.",
    "See [the 10432 line][w].\n\n[w]: https://example.com/x",
])
def test_no_link_lands_inside_a_file_name_a_path_or_a_reference(text):
    """Review B6 (and its low note on reference links): a link inside a
    path or a file name breaks the name."""
    marks = [_mark("src_aaaa1111aaaa1111", "10432")]
    assert link_reply(text, marks, seen=set()) == text


def test_a_figure_beside_a_path_is_still_linked():
    marks = [_mark("src_aaaa1111aaaa1111", "10432")]
    out = link_reply("Invoice 10432 is in C:\\clients\\a.pdf, $10,432/yr.", marks, seen=set())
    assert out == (
        "Invoice [10432](#source/src_aaaa1111aaaa1111) is in C:\\clients\\a.pdf, "
        "$[10,432](#source/src_aaaa1111aaaa1111)/yr."
    )


def test_a_turn_shown_a_picture_calls_no_figure_one_with_no_record():
    """A figure read off a picture is in no text the model was given: it is
    left plain, never "Jarvis worked it out or wrote it itself"."""
    from iron_jarvis.documents.sources import _link_given
    from iron_jarvis.providers.adapters.base import LLMMessage

    marks = [_mark("src_aaaa1111aaaa1111", "48210")]
    text = "Wages 48,210 and the receipt says 73,519."
    pic = [LLMMessage(role="user", content="what is on this?", images=[{"data_b64": "AA==", "media_type": "image/png"}])]
    assert _link_given(text, marks, True, ("system", pic)) == (
        "Wages [48,210](#source/src_aaaa1111aaaa1111) and the receipt says 73,519."
    )
    words = [LLMMessage(role="user", content="what is on this?")]
    assert "[73,519](#source/none)" in _link_given(text, marks, True, ("system", words))


def test_a_saved_replys_links_go_back_to_the_model_as_words():
    from iron_jarvis.documents.sources import given_figures, history_without_figure_links

    linked = "Total [48,210](#source/src_19c4e2207b3a5d61) in all."
    msgs = [{"role": "user", "content": "Is [x](#source/src_aaaa1111aaaa1111) mine?"},
            {"role": "assistant", "content": linked}]
    out = history_without_figure_links(msgs)
    assert out[1]["content"] == "Total 48,210 in all."
    assert out[0] == msgs[0]  # a user's line is never changed
    assert msgs[1]["content"] == linked  # the saved chat is not touched
    # The digits of a record's id are no number the model was given.
    assert given_figures([{"role": "assistant", "content": linked}]) == {"48210"}


def test_only_the_models_words_are_swapped_never_the_notes_under_them():
    head = "Total 48,210."
    linked = "Total [48,210](#source/src_aaaa1111aaaa1111)."
    reply = head + "\n\n_Note: 3,000 rows were read._"
    assert with_linked_head(reply, head, linked) == linked + "\n\n_Note: 3,000 rows were read._"
    # Words that moved (a rewrite) are left as they are.
    assert with_linked_head("Other words. " + reply, head, linked) == "Other words. " + reply


# --------------------------------------------------------------------------- #
# both chat lanes, end to end
# --------------------------------------------------------------------------- #
@pytest.fixture()
def client(tmp_path):
    c = TestClient(create_app(str(tmp_path / "home")))
    yield c
    c.close()


FINAL = "Total wages were 48,210.00 (about 48.2k); the bonus was 73,519. Code: `48,210`."


def _normalized(text: str) -> str:
    return re.sub(r"src_[0-9a-f]{16}", "src_X", text)


def _head(reply: str) -> str:
    return reply.split("\n\n")[0]


def _check_reply(client, reply: str) -> None:
    head = _head(reply)
    links = _LINK.findall(head)
    assert [(t, ids == NO_RECORD) for t, ids in links] == [
        ("48,210.00", False), ("48.2k", True), ("73,519", True),
    ], head
    assert "`48,210`" in head  # code untouched
    ids = links[0][1]
    r = client.get(f"/sources/{ids}")
    assert r.status_code == 200, r.text
    marks = r.json()["marks"]
    assert [(m["locator"], m["shown"], m["present"], m["changed"]) for m in marks] == [
        ({"page": 7, "line": 3}, "48,210.00", True, False)
    ]
    assert marks[0]["name"] == "statement.pdf"
    assert "Total wages 48,210.00" in marks[0]["context"]
    assert "file_sha256" not in marks[0] and "thread_id" not in marks[0]


def _read_turn(client, tmp_path, monkeypatch, lane):
    pdf = make_pdf(tmp_path / "statement.pdf")
    platform = client.app.state.platform
    _scripted_router(platform, monkeypatch,
                     [[ToolCall(id="c1", name="read_document", arguments={"path": str(pdf)})]],
                     FINAL)
    body, _frames = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "what were the wages in my statement?"}],
        "tools": ["read_document"],
        "approval_mode": "yolo",
    })
    return body["reply"]


@pytest.mark.parametrize("lane", LANES)
def test_a_reply_that_read_a_pdf_links_its_figure_to_page_7(client, tmp_path, monkeypatch, lane):
    reply = _read_turn(client, tmp_path, monkeypatch, lane)
    _check_reply(client, reply)


def test_both_lanes_give_the_same_reply(tmp_path, monkeypatch):
    heads = []
    for lane in LANES:
        root = tmp_path / lane
        root.mkdir()
        c = TestClient(create_app(str(root / "home")))
        try:
            with monkeypatch.context() as mp:
                heads.append(_normalized(_head(_read_turn(c, root, mp, lane))))
        finally:
            c.close()
    assert heads[0] == heads[1], heads
    assert "](#source/src_X)" in heads[0]


@pytest.mark.parametrize("lane", LANES)
def test_an_attached_pdf_links_too(client, tmp_path, monkeypatch, lane):
    pdf = make_pdf(tmp_path / "statement.pdf")
    _scripted_router(client.app.state.platform, monkeypatch, [], FINAL)
    body, _ = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "what were the wages in this?"}],
        "attachments": [str(pdf)],
    })
    _check_reply(client, body["reply"])


@pytest.mark.parametrize("lane", LANES)
def test_a_small_attachment_given_whole_links_too(client, tmp_path, monkeypatch, lane):
    """A short file is handed to the model whole (no excerpts): its figures
    are recorded too, on their own page."""
    pdf = make_pdf(tmp_path / "short.pdf", pages=2, figure_page=2)
    _scripted_router(client.app.state.platform, monkeypatch, [], "Wages 48,210.00 in all.")
    body, _ = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "what are the wages here?"}],
        "attachments": [str(pdf)],
    })
    links = _LINK.findall(_head(body["reply"]))
    assert len(links) == 1 and links[0][0] == "48,210.00" and links[0][1] != NO_RECORD, body["reply"]
    marks = client.get(f"/sources/{links[0][1]}").json()["marks"]
    assert [m["locator"] for m in marks] == [{"page": 2, "line": 3}]


@pytest.mark.parametrize("lane", LANES)
@pytest.mark.parametrize("text", [
    "Last year you spent 48,210.00 and [99,999](#source/src_aaaa1111aaaa1111).",
    # Only a written-another-way link: no "#source/" in the words at all.
    "Last year you spent 48,210.00 and [99,999](&#35;source/src_aaaa1111aaaa1111).",
    "Last year you spent 48,210.00 and [99,999][1].\n\n[1]: <\\#source/src_aaaa1111aaaa1111>",
])
def test_a_plain_chat_is_left_alone(client, monkeypatch, lane, text):
    _scripted_router(client.app.state.platform, monkeypatch, [], text)
    body, _ = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "remind me"}],
    })
    head = _head(body["reply"])
    assert "source/" not in head
    assert "48,210.00" in head and "99,999" in head


@pytest.mark.parametrize("lane", LANES)
def test_a_figure_from_the_users_own_words_stays_plain(client, tmp_path, monkeypatch, lane):
    """Review A6(a) / B7: both lanes hand ``given=(system, msgs)`` to the
    linker. Without it a figure the user typed would be dotted and said to
    be one "Jarvis worked it out or wrote it itself"."""
    pdf = make_pdf(tmp_path / "statement.pdf")
    _scripted_router(client.app.state.platform, monkeypatch,
                     [[ToolCall(id="c1", name="read_document", arguments={"path": str(pdf)})]],
                     "Wages were 48,210.00; the 73,519 you mention is not in it.")
    body, _ = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "Is my bonus of 73,519 in the statement?"}],
        "tools": ["read_document"],
        "approval_mode": "yolo",
    })
    head = _head(body["reply"])
    assert [t for t, _ids in _LINK.findall(head)] == ["48,210.00"], head
    assert "the 73,519 you mention" in head


@pytest.mark.parametrize("lane", LANES)
def test_the_model_is_sent_a_saved_replys_words_never_its_figure_links(client, monkeypatch, lane):
    """Review A8 / B1-B2: the history both lanes send back to the model
    carries no record id it could copy."""
    seen = _scripted_router(client.app.state.platform, monkeypatch, [], "Noted.")
    _post_turn(client, lane, {"messages": [
        {"role": "user", "content": "what were the wages?"},
        {"role": "assistant", "content": "Total [48,210.00](#source/src_19c4e2207b3a5d61)."},
        {"role": "user", "content": "thanks"},
    ]})
    sent = " ".join(str(getattr(m, "content", "")) for m in seen["messages"][0])
    assert "Total 48,210.00." in sent
    assert "source/" not in sent and "src_19c4e2207b3a5d61" not in sent


def _link_route(client):
    for route in client.app.routes:
        if getattr(route, "path", "") == "/chat/turns/{turn_id}/thread" and "POST" in route.methods:
            return route.endpoint
    raise AssertionError("no turn-link route")


def _rows_of_turn(client, turn_id: str) -> "set[str]":
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.documents import source_store as store

    with session_scope(client.app.state.platform.engine) as db:
        return {r.thread_id for r in db.exec(select(store.SourceMark).where(
            store.SourceMark.turn_id == turn_id))}


@pytest.mark.parametrize("lane", LANES)
def test_a_new_chats_first_turn_is_homed_by_the_turn_link_route(client, tmp_path, monkeypatch, lane):
    """Review A6(b) / B7: a new chat's first turn reads with no chat id; the
    page links the turn after it ended, and its rows carry the chat (else
    they are swept after two days and the first reply's links die)."""
    pdf = make_pdf(tmp_path / "statement.pdf")
    _scripted_router(client.app.state.platform, monkeypatch,
                     [[ToolCall(id="c1", name="read_document", arguments={"path": str(pdf)})]],
                     FINAL)
    _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "what were the wages?"}],
        "tools": ["read_document"], "approval_mode": "yolo", "turn_id": f"turn_new_{lane}",
    })
    assert _rows_of_turn(client, f"turn_new_{lane}") == {""}
    r = client.post(f"/chat/turns/turn_new_{lane}/thread", json={"thread_id": f"thr_new_{lane}"})
    assert r.status_code == 200, r.text
    assert _rows_of_turn(client, f"turn_new_{lane}") == {f"thr_new_{lane}"}


@pytest.mark.parametrize("lane", LANES)
def test_a_turn_linked_while_it_runs_writes_its_later_reads_under_the_chat(
        client, tmp_path, monkeypatch, lane):
    """Review B7: the page names the chat while the turn runs (between its
    two reads); the second read's rows carry the chat too."""
    from iron_jarvis.providers.adapters.base import LLMResponse
    from iron_jarvis.providers.router import RouteResult

    pdf = make_pdf(tmp_path / "statement.pdf")
    link = _link_route(client)
    turn_id, tid = f"turn_live_{lane}", f"thr_live_{lane}"
    rounds = [[ToolCall(id="c1", name="read_document", arguments={"path": str(pdf)})],
              [ToolCall(id="c2", name="extract_pdf", arguments={"path": str(pdf), "page_range": "7"})]]
    state = {"n": 0}

    async def _next() -> LLMResponse:
        n = state["n"]
        state["n"] += 1
        if n == 1:
            await link(turn_id, {"thread_id": tid})  # the page's link, mid-turn
        if n < len(rounds):
            return LLMResponse(text="", tool_calls=list(rounds[n]), usage={"input_tokens": 3, "output_tokens": 2})
        return LLMResponse(text=FINAL, tool_calls=[], usage={"input_tokens": 3, "output_tokens": 2})

    async def fake_complete(*, system, messages, tools, **kw):
        return RouteResult(await _next(), "mock", "mock")

    async def fake_stream(*, system, messages, tools, **kw):
        resp = await _next()
        if resp.text:
            yield {"type": "text", "text": resp.text}
        yield {"type": "final", "response": resp, "provider": "mock", "model": "mock"}

    platform = client.app.state.platform
    monkeypatch.setattr(platform.router, "complete", fake_complete)
    monkeypatch.setattr(platform.router, "stream", fake_stream)
    _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "what were the wages?"}],
        "tools": ["read_document", "extract_pdf"], "approval_mode": "yolo", "turn_id": turn_id,
    })
    assert state["n"] == 3
    from sqlmodel import select

    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import ToolInvocation
    from iron_jarvis.documents import source_store as store

    with session_scope(platform.engine) as db:
        second = {i.id for i in db.exec(select(ToolInvocation)) if i.tool == "extract_pdf"}
        rows = [(r.thread_id, r.invocation_id) for r in db.exec(
            select(store.SourceMark).where(store.SourceMark.turn_id == turn_id))]
    assert second, "the second read did not run"
    assert {t for t, inv in rows if inv in second} == {tid}
    assert {t for t, _inv in rows} == {tid}


def test_the_dashboard_twin_reads_the_same_link_and_words():
    from pathlib import Path

    from iron_jarvis.documents.sources import NO_RECORD_WORDS

    ts = (Path(__file__).resolve().parents[1] / "dashboard" / "lib" / "sourceLink.ts").read_text(
        encoding="utf-8").replace("\r\n", "\n")

    def const(name: str) -> str:
        m = re.search(rf'export const {name} = "([^"]*)";', ts)
        assert m, name
        return m.group(1)

    assert const("SOURCE_LINK_PREFIX") == SOURCE_LINK_PREFIX
    assert const("NO_RECORD") == NO_RECORD
    assert const("NO_RECORD_WORDS") == NO_RECORD_WORDS


def test_the_page_compares_a_figure_the_way_the_daemon_does():
    """The page opens only a record whose key is the link words' key
    (``lib/sourceLink.figureKey``); the examples the dashboard test reads
    are the daemon's own keys."""
    import json

    from iron_jarvis.core.numbers import numbers
    from iron_jarvis.documents.sources import mark_key

    pairs = json.loads((Path(__file__).resolve().parents[1] / "dashboard" / "__tests__" / "fixtures"
                        / "figure-keys-v1547.json").read_text(encoding="utf-8"))
    assert len(pairs) >= 20
    for text, key in pairs:
        first = next(iter(numbers(text)), ("", ""))[0]
        assert first == key, (text, first, key)
        if key and text.lstrip("$") == text and "%" not in text:
            assert mark_key(text) == key, text


def test_the_phone_gets_the_words_without_the_links():
    from iron_jarvis.documents.sources import without_figure_links

    assert without_figure_links("Total [48,210](#source/src_aaaa1111aaaa1111) and [9](#source/none).") == (
        "Total 48,210 and 9."
    )
    src = (Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "comm" / "inbound.py").read_text(
        encoding="utf-8")
    assert "outbound = without_figure_links(reply)" in src


LINKED = "Total [48,210.00](#source/src_19c4e2207b3a5d61) for the year."


def test_a_reply_typed_on_the_pc_in_a_phone_chat_reaches_the_phone_without_links(tmp_path):
    """Review A4: POST /comm/threads/{id}/send sent the reply with its link
    markup to the phone. The chat keeps the links; the phone gets words."""
    from iron_jarvis.comm import MockChannel

    class _Phone(MockChannel):
        supports_inbound = True

        def has_credentials(self) -> bool:
            return True

    c = TestClient(create_app(str(tmp_path / "home")))
    try:
        app = c.app
        ch = _Phone({"inbound_enabled": True, "chat_enabled": True, "allowed_senders": ["777"]})
        app.state.platform.notifier.add_channel("tg", ch)
        store = app.state.comm_thread_store
        tid = store.resolve("tg", "777", "Val").id

        async def scripted_turn(platform, personas, body):
            return {"reply": LINKED, "provider": "mock", "model": "mock", "tools_used": ["read_document"]}

        app.state.inbound_poller.chat_turn = scripted_turn
        r = c.post(f"/comm/threads/{tid}/send", json={"text": "what were the wages?"})
        assert r.status_code == 200, r.text
        assert ch.sent, "nothing reached the phone"
        assert "source/" not in "".join(ch.sent)
        assert "Total 48,210.00 for the year." in "".join(ch.sent)
        kept = [m for m in store.history_body(tid) if m["role"] == "assistant"]
        assert kept and kept[-1]["content"] == LINKED  # the chat keeps the links
    finally:
        c.close()


def test_the_recap_check_never_counts_the_digits_of_a_records_id():
    """Review A8 / B1: "Done: 61 invoices were filed." was KEPT when the
    reply was saved linked (the id ``src_19c4e2207b3a5d61`` holds 61 and
    2207) and dropped when it was saved plain."""
    from iron_jarvis.workmap.recap import check, lines_of, transcript_text

    msgs = [{"role": "user", "content": "what were the wages?"}, {"role": "assistant", "content": LINKED}]
    shown = transcript_text(lines_of(msgs))
    assert "source/" not in shown and "Total 48,210.00 for the year." in shown
    for invented in ("Done: 61 invoices were filed.", "Done: wages of 2207 were reported."):
        for text in (shown, "Assistant: " + LINKED):
            got = check(invented, shown=text)
            assert not got.lines, (invented, got)
    assert check("Done: wages were 48,210.", shown=shown).lines


def test_an_earlier_chat_added_to_a_message_reaches_the_model_without_links():
    from iron_jarvis.daemon.chat_refs import render_snapshot

    text, _short = render_snapshot([{"role": "assistant", "content": LINKED}], 4000)
    assert "source/" not in text and "48,210.00" in text


def test_a_shared_chat_carries_the_words_not_the_links():
    """Review A note: the chat's Share (Markdown, HTML, e-mail) is read
    outside the app, where a figure link opens nothing."""
    from iron_jarvis.daemon.routes.chat import _share_transcript

    out = _share_transcript("Wages", "", None, [
        {"role": "user", "content": "what were the wages?"}, {"role": "assistant", "content": LINKED},
    ])
    assert "source/" not in out and "Total 48,210.00 for the year." in out


def test_an_unknown_figure_link_is_a_plain_404_and_a_bad_one_400(client):
    assert client.get("/sources/src_0000000000000000").status_code == 404
    assert client.get("/sources/none").status_code == 400
    assert client.get("/sources/src_1,../etc").status_code in (400, 404)
