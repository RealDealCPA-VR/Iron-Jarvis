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
def test_a_plain_chat_is_left_alone(client, monkeypatch, lane):
    text = "Last year you spent 48,210.00 and [99,999](#source/src_aaaa1111aaaa1111)."
    _scripted_router(client.app.state.platform, monkeypatch, [], text)
    body, _ = _post_turn(client, lane, {
        "messages": [{"role": "user", "content": "remind me"}],
    })
    head = _head(body["reply"])
    assert SOURCE_LINK_PREFIX not in head
    assert "48,210.00" in head and "99,999" in head


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


def test_the_phone_gets_the_words_without_the_links():
    from iron_jarvis.documents.sources import without_figure_links

    assert without_figure_links("Total [48,210](#source/src_aaaa1111aaaa1111) and [9](#source/none).") == (
        "Total 48,210 and 9."
    )
    src = (Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "comm" / "inbound.py").read_text(
        encoding="utf-8")
    assert "outbound = without_figure_links(reply)" in src


def test_an_unknown_figure_link_is_a_plain_404_and_a_bad_one_400(client):
    assert client.get("/sources/src_0000000000000000").status_code == 404
    assert client.get("/sources/none").status_code == 400
    assert client.get("/sources/src_1,../etc").status_code in (400, 404)
