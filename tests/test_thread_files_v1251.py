"""The conversation's files reach the FOLLOW-UP turn (v1.251.0, C-01).

THE DEFECT, in the user's words: attach a return, ask for a summary, then say
"now turn that into a memo" — and the second turn has no file. Chat history
crosses as ``{role, content}`` text, and only THIS message's ``attachments``
ride, so a follow-up that names nothing ("it", "that return") reached a model
that had been told about no file at all. Two things were missing and both are
needed; either alone is still a broken turn:

* the file is not NAMED, so "it" resolves to nothing a tool can open;
* the file does not COUNT when the turn arms tools, so even a model that
  guessed the path held no verb that could act on it.

The client sends what its Files rail already shows (``ChatBody.thread_files``)
minus this turn's own attachments, and the daemon does the rest in the two
functions BOTH lanes share — so ``/chat`` and ``/chat/stream`` inherit this
without a second edit, which is why every behavioural test below runs against
both routes.

WHAT THIS DELIBERATELY DOES NOT DO, pinned as hard as what it does: a carried
file is NAMED, never RE-READ. Its text is already earlier in the conversation,
and re-extracting every remembered file on every turn would spend the whole
prompt budget on files nobody asked about
(``test_a_carried_file_is_named_but_never_re_extracted``).

THE CONSENT GATE FROM v1.196.0 STILL HOLDS. Read verbs arm on the file's TYPE
(the file was already given to this conversation — the same consent as
attaching it again); CHANGE verbs still need the request to ask for that
change. A carried file is not a wider grant than an attached one, and
``test_a_read_only_follow_up_arms_no_mutator`` is that claim in both
directions.

WHICH PIN CATCHES WHICH CHANGE, measured by reverting each one (mutation-check,
2026-09-11). Recorded because the obvious pairing is WRONG and cost a round:

* revert the carried block → ``test_a_follow_up_with_no_attachment_still_names_
  the_file`` goes red. CAUGHT.
* revert ``_suffixes`` to this turn's attachments only →
  ``test_edit_it_arms_the_change_verbs`` goes red (the prompt starts saying no
  tool can change a file the turn armed a verb for). CAUGHT.
* revert the ``_fill_attachment_pass`` carry → ``test_a_read_only_follow_up_
  arms_no_mutator`` goes red on BOTH lanes. This is the READ half, and it is the
  measurement that justifies that edit::

      "what does it say?" + a carried workbook
        with the carry:     excel_profile, excel_query, excel_read, read_document
        without it:         read_document

  ``test_edit_it_arms_the_change_verbs`` does NOT catch it, and assuming it
  would is the mistake: ``excel_edit`` on "update cell B2 to 500" is armed by
  the SENTENCE scorer whether a file is carried or not. So the change verbs
  needed ``_suffixes`` (what the prompt SAYS) and the read verbs needed the loop
  (what the turn can DO) — two different halves, two different pins.

ONE DEFECT THIS FILE'S OWN FIRST CUT HAD, worth recording because it was
invisible and green: ``_prepare_attachments`` resolves its change verbs per
SUFFIX, over the suffixes of ``body.attachments`` only. On the very turn C-01
exists for — a follow-up that attaches NOTHING — that set is empty, so
``_may_change`` answered ``[]`` (fail-closed, and right, given its input) and
every carried file rendered the "no tool here can change this" clause while
the arming pass had just granted ``excel_edit``. The prompt and the tool list
contradicted each other. ``test_edit_it_arms_the_change_verbs`` asserts the
armed set and the prompt TOGETHER, from the same request, which is the only
shape that can catch it.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import (
    _MAX_CARRIED_FILES,
    _thread_file_paths,
)
from iron_jarvis.daemon.schemas import ChatBody
from iron_jarvis.documents.attachment_rag import _CHANGE_UNARMED
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"

#: The attachment section of the system prompt, so an assertion about a carried
#: file cannot be satisfied by some other part of the prompt (the v1.196.0
#: file's own lesson about proxy signals).
_ATTACH_HEADER = "# Attachments (provided by the user this turn)"

#: The carried-files block's own heading. Distinctive, so its ABSENCE is a real
#: assertion too.
_CARRIED_HEADER = "## Files in this conversation (already given to you earlier)"

#: A cell value that exists ONLY inside the workbook's bytes. If this appears in
#: the prompt for a CARRIED file, the file was re-extracted.
_INSIDE_THE_FILE = "Belmont"


def _workbook(path: Path) -> None:
    """A small real .xlsx — small enough to take the INLINE branch when it is
    ATTACHED, which is what makes the "named, never re-read" assertion sharp:
    the same file attached DOES put its cells in the prompt."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Q1"
    ws.append(["Client", "Fee", "Paid"])
    ws.append(["Acme", 1000, "yes"])
    ws.append([_INSIDE_THE_FILE, 2500, "no"])
    wb.save(str(path))


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "home")))


@pytest.fixture
def scene(client):
    """A plain chat (NO project) with a workbook already uploaded the way chat
    uploads them — ``<home>/uploads``, which is also where a no-project turn's
    tools run, so an in-place change verb is genuinely reachable and the block
    says so plainly instead of carrying v1.196.0's confinement warning."""
    platform = client.app.state.platform
    uploads = Path(platform.config.home) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    book = uploads / "client_fees.xlsx"
    _workbook(book)
    return SimpleNamespace(platform=platform, uploads=uploads, book=book)


def _record(platform, calls: "list | None" = None) -> list[str]:
    """Capture the SYSTEM prompt each lane really sends and, when *calls* is
    given, the ``tools`` list that rode with it — so "the prompt names it" and
    "the model could call it" are checked against the SAME request. (Same seam
    as ``tests/test_attachment_handoff_v1196.py``; duplicated rather than
    imported, because a test module is not a library.)"""
    seen: list[str] = []

    async def _complete(*, system="", tools=None, **kw):
        seen.append(system)
        if calls is not None:
            calls.append([t.get("name") for t in (tools or [])])
        return RouteResult(LLMResponse(text="ok"), "mock", "mock")

    async def _stream(*, system="", tools=None, **kw):
        seen.append(system)
        if calls is not None:
            calls.append([t.get("name") for t in (tools or [])])
        yield {"type": "text", "text": "ok"}
        yield {
            "type": "final", "response": LLMResponse(text="ok"),
            "provider": "mock", "model": "mock", "requested": "", "reason": "mock",
        }

    platform.router.complete = _complete
    platform.router.stream = _stream
    return seen


_ANCHORS = (r"[A-Za-z]:[\\/]", r"\\\\", r"/")


def _paths_in(block: str, suffix: str = ".xlsx") -> list[str]:
    """Every absolute path of *suffix* the block hands over, however worded.

    SPACE-SAFE on purpose: the one directory this feature ships against is
    ``%APPDATA%/Iron Jarvis/.ironjarvis/uploads``, and a whitespace split would
    truncate it and leave every path assertion here vacuously green on the
    user's real machine (the v1.196.0 file learned this the hard way)."""
    found: dict[int, str] = {}
    for anchor in _ANCHORS:
        pattern = re.compile(
            anchor + r"[^\n\r()]*?" + re.escape(suffix) + r"(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        for match in pattern.finditer(block):
            if Path(match.group(0)).is_absolute():
                found.setdefault(match.start(), match.group(0))
    return [found[i] for i in sorted(found)]


def _section(system: str) -> str:
    assert _ATTACH_HEADER in system, (
        "the turn carried files and the prompt has no attachment section at all"
    )
    return system.split(_ATTACH_HEADER, 1)[1]


def _ask(client, route, scene, question, *, thread_files=None, attachments=None):
    body = {
        "messages": [{"role": "user", "content": question}],
        "attachments": list(attachments or []),
        # Auto ON: the change half of the gate is Auto consent PLUS an asking
        # request, so a test about change verbs must supply the consent.
        "auto_tools": True,
    }
    if thread_files is not None:
        body["thread_files"] = list(thread_files)
    resp = client.post(route, json=body)
    assert resp.status_code == 200, resp.text
    return resp


# ---------------------------------------------------------------- the defect --


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
def test_a_follow_up_with_no_attachment_still_names_the_file(client, scene, route):
    """C-01 ITSELF, through the real endpoints: a turn that attaches NOTHING is
    told about the file this conversation already has, by ABSOLUTE path — the
    only form that resolves from the turn's tool workspace."""
    seen = _record(scene.platform)
    _ask(client, route, scene, "now turn that into a memo",
         thread_files=[str(scene.book)])

    section = _section(seen[0])
    assert _CARRIED_HEADER in section, (
        f"{route} never told the model the conversation has a file"
    )
    assert str(scene.book) in section, (
        f"{route} named no reachable path for the carried file: {section!r}"
    )
    assert _paths_in(section) == [str(scene.book)]


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
def test_edit_it_arms_the_change_verbs(client, scene, route):
    """THE HALF THAT NAMING ALONE DOES NOT FIX — and the pin for the defect
    described in this module's docstring.

    "update cell B2 to 500" with no attachment must BOTH promise ``excel_edit``
    and arm it, from the same request. Asserting either one alone is what let
    the prompt and the tool list disagree.
    """
    calls: list[list[str]] = []
    seen = _record(scene.platform, calls)
    _ask(client, route, scene, "update cell B2 to 500",
         thread_files=[str(scene.book)])

    section, armed = _section(seen[0]), set(calls[0])
    assert "excel_edit" in armed, (
        f"{route} did not arm the change verb for the conversation's own file — "
        f'"edit it" had nothing to edit with: {sorted(armed)}'
    )
    assert "excel_edit" in section, (
        f"{route} armed excel_edit and never told the model: {section!r}"
    )
    assert _CHANGE_UNARMED.strip() not in section, (
        f"{route} said no tool can change this file while arming one: {section!r}"
    )
    # The read half rode along, as it does for an attachment.
    for tool in ("excel_read", "excel_query"):
        assert tool in armed, sorted(armed)


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
def test_a_read_only_follow_up_arms_no_mutator(client, scene, route):
    """THE CONSENT GATE SURVIVES CARRYING (v1.196.0 §6, restated for C-01).

    A carried file is the same consent as an attached one — no wider. A
    read-only follow-up arms the READERS on type and NO mutator, and the block
    says so honestly rather than promising a verb the turn withheld. Arming is
    granting here (the list becomes the turn's ``session_allow``), so an
    over-armed mutator is a tool that runs with no approval card.
    """
    calls: list[list[str]] = []
    seen = _record(scene.platform, calls)
    _ask(client, route, scene, "what do these fees add up to?",
         thread_files=[str(scene.book)])

    section, armed = _section(seen[0]), set(calls[0])
    assert "excel_read" in armed, sorted(armed)
    for mutator in ("excel_edit", "excel_apply_spec"):
        assert mutator not in armed, (
            f"{route} armed {mutator} on a read-only follow-up, and this list "
            f"becomes session_allow: {sorted(armed)}"
        )
        assert mutator not in section, section
    assert _CHANGE_UNARMED.strip() in section, section


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
def test_a_carried_file_is_named_but_never_re_extracted(client, scene, route):
    """"NAMED, NEVER RE-READ" — the budget promise, asserted both ways on the
    SAME file so it cannot pass by accident: attached, its cells are in the
    prompt; carried, only its path and verbs are."""
    seen = _record(scene.platform)
    _ask(client, route, scene, "what changed?", thread_files=[str(scene.book)])
    carried = _section(seen[0])
    assert _INSIDE_THE_FILE not in carried, (
        f"{route} re-extracted a carried file's contents: {carried!r}"
    )

    # The control: the same file ATTACHED does render its text, so the absence
    # above is the carrying path's doing and not a broken fixture.
    seen2 = _record(scene.platform)
    _ask(client, route, scene, "what changed?", attachments=[scene.book.name])
    assert _INSIDE_THE_FILE in _section(seen2[0]), (
        "the control failed: an ATTACHED workbook stopped rendering its text, "
        "so the carried-file assertion above proves nothing"
    )


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
def test_this_turns_own_attachment_is_not_carried_twice(client, scene, route):
    """The client filters its own attachments out, and so does the daemon — a
    client is not the authority on this. One file, one entry, no carried block
    listing the file the user is attaching right now."""
    seen = _record(scene.platform)
    _ask(client, route, scene, "update cell B2 to 500",
         attachments=[scene.book.name], thread_files=[str(scene.book)])

    section = _section(seen[0])
    assert _CARRIED_HEADER not in section, (
        f"{route} listed this turn's own attachment as an earlier file: {section!r}"
    )


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
def test_a_carried_file_that_is_gone_says_nothing(client, scene, route):
    """A remembered file can be moved, renamed or deleted between turns, and a
    rail row is not proof of a file. A dead path is skipped silently — never an
    error, never a path handed over that the tools would fail to open."""
    seen = _record(scene.platform)
    ghost = scene.uploads / "deleted_last_week.xlsx"
    _ask(client, route, scene, "summarise it", thread_files=[str(ghost)])

    assert seen, f"{route} never reached the model"
    if _ATTACH_HEADER in seen[0]:
        assert str(ghost) not in seen[0].split(_ATTACH_HEADER, 1)[1]


def test_the_carried_list_is_bounded_and_newest_last(client, scene):
    """A long conversation must not crowd out the turn's own prompt. The reader
    bounds the list to the NEWEST few — a follow-up means the recent files —
    and the daemon applies that bound itself."""
    names = []
    for i in range(_MAX_CARRIED_FILES + 6):
        p = scene.uploads / f"book{i:02d}.xlsx"
        _workbook(p)
        names.append(str(p))

    assert len(_thread_file_paths(SimpleNamespace(thread_files=names))) == (
        _MAX_CARRIED_FILES
    )
    assert _thread_file_paths(SimpleNamespace(thread_files=names))[-1] == names[-1]

    seen = _record(scene.platform)
    _ask(client, "/chat", scene, "what do these say?", thread_files=names)
    section = _section(seen[0])
    assert len(_paths_in(section)) <= _MAX_CARRIED_FILES, (
        "the whole rail rode the prompt"
    )
    assert names[-1] in section, "the NEWEST file is the one a follow-up means"


def test_the_reader_dedupes_and_ignores_blanks():
    """One reader for the block and for the arming pass, so the two can never
    disagree about which files this conversation has."""
    body = SimpleNamespace(thread_files=["/x/a.pdf", "/x/a.pdf", "  ", "", "/x/b.xlsx"])
    assert _thread_file_paths(body) == ["/x/a.pdf", "/x/b.xlsx"]
    # An older client sends no such field at all, and must keep working.
    assert _thread_file_paths(SimpleNamespace()) == []
    assert _thread_file_paths(SimpleNamespace(thread_files=None)) == []


def test_the_field_is_optional_on_the_wire():
    """Old dashboards, the Chrome extension and every headless caller post no
    ``thread_files``; the field defaults empty and changes nothing for them."""
    assert ChatBody(messages=[]).thread_files == []


def test_neither_lane_reimplemented_the_carry():
    """The lock-step property as a SOURCE fact, the way v1.196.0 pins its own:
    the streaming lane must inherit this from the shared functions rather than
    grow a second copy that can drift."""
    stream = (_SRC / "routes" / "chat.py").read_text(
        encoding="utf-8"
    ).replace("\r\n", "\n")
    turn = (_SRC / "chat_turn.py").read_text(encoding="utf-8").replace("\r\n", "\n")

    assert "_thread_file_paths" in turn
    assert "_thread_file_paths" not in stream, (
        "the streaming lane grew its own copy of the carry — it must inherit it "
        "from _prepare_attachments/_resolve_armed_tools like everything else"
    )
    assert "thread_files" not in stream, (
        "routes/chat.py started reading thread_files directly"
    )
