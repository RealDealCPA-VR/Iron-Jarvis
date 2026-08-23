"""An attached workbook is a LIVE FILE, not a text dump (v1.196.0).

MEASURED, not guessed. The event ledger of the user's install (table
``eventrecord``, ``type='tool.executed'``, 482 calls between 2026-07-06 and
2026-08-20) says ``read_document`` ran 96 times — the second most-used tool in
the whole app — and that ALL EIGHT ``excel_*`` tools ran ZERO times, across ~10
stored messages that mention ``.xlsx``. That is not "Excel isn't wanted"; it is
a path that does not work.

The reason is what the model was TOLD about the attachment:

* the block named the file (``rag_block(p.name, ...)``) while the turn's tools
  run in the GROUNDED PROJECT ROOT and the upload physically lives in
  ``<home>/uploads``. ``test_the_bare_filename_is_unreachable_from_a_project``
  below pins the old behaviour end-to-end: ``excel_read`` raises
  FileNotFoundError and ``excel_edit`` answers "no such workbook in the
  workspace". The hint named a tool and handed over a name it cannot open;
* the verbs were framed as a FALLBACK ("for OTHER PARTS ... use
  excel_profile/excel_query"), which only speaks to a model that ran out of
  document — never the common case of a workbook that fits;
* ``excel_edit``/``excel_apply_spec`` were never named at all.

These tests refuse to assert on the STRING. Every claim the new line makes is
checked by RUNNING the real tool with the real ToolContext of the turn:

1. the path in the block resolves — ``excel_read``/``excel_query`` open it and
   return the workbook's real numbers, from a project-grounded chat;
2. an editing verb is named, and the line's claim about it is TRUE in both
   directions: where it promises an in-place edit, ``excel_edit`` succeeds;
   where it warns the edit will be refused, ``excel_edit`` is in fact refused
   (the repo's central rule — never claim what did not, or cannot, happen);
2b. THE SAME PAIR FOR THE DERIVED VERBS. The first cut of this unit ran that
   pair for ``.xlsx`` only, and shipped an unqualified "Change it:
   convert_document, write_document." for every other type next to a path
   OUTSIDE the workspace — where ``write_document`` answers "escapes the
   session workspace". The ``in_place`` verdict was reasoned about the SOURCE
   and used to answer a question about the TARGET. Now a ``.docx``/``.pptx``/
   ``.txt``/``.csv`` block is checked by running the change verb three ways:
   the refusal is real, the block warned, and the form it names works;
3. BOTH chat lanes carry it, driven through the real ASGI app, because the
   whole point of ``_prepare_attachments`` is that the streaming lane — the one
   the dashboard runs — inherits the fix for free;
4. THE PROMISE IS BACKED BY ``tool_specs``. Naming ``excel_query`` is worth
   nothing if the turn does not arm it — and it did not: the only attachment
   signal in the selector was ``bump({"read_document": 9})``, blind to which
   document it was, so the ledger's own phrasings ("what do these fees add up
   to?") armed ``read_document`` ALONE. ``_resolve_armed_tools`` now fills its
   free slots from the SAME tuple the prompt line is rendered from.

Offline throughout: the router is faked, the workbook is generated.

ROUND 2 — THREE HOLES THIS FILE'S OWN MUTATION RUN FOUND IN IT. Green was not
the bar; each of these was GREEN and asserting nothing:

5. THE RETRIEVAL BRANCH had no test at all. Every fixture above is three rows
   wide, so every one of them took the INLINE branch — while the branch the
   original finding actually quoted ("Retrieved 1 of 1 sections...") is a
   SEPARATE ``parts.append`` with its own ``+ live``. Measured: deleting that
   ``+ live`` left all 30 tests green, i.e. a future edit could drop the path
   and the editing verbs for exactly the 200-page PDFs and real workbooks this
   module exists for. ``test_the_oversized_branch_hands_over_the_live_file_too``
   forces the branch at the app's REAL budget and then queries the file.
6. THE VISION TEST WAS VACUOUS. It asserted ``"view_image" in block`` — and the
   WITHDRAWAL wording contains that substring ("image_info (view_image needs a
   vision model; none is connected)"), so it passed identically either way.
   Measured: forcing ``_has_vision`` to False left all 30 green. It now parses
   the OFFERED segment (:func:`_offered`), runs over three fleets that must not
   withdraw, and CHECKS ITS OWN PREMISE.
7. ``_paths_in`` SPLIT ON WHITESPACE, so it works only because ``tmp_path`` has
   no space in it — and the one folder this feature ships against is
   ``%APPDATA%/Iron Jarvis/.ironjarvis/uploads``.

ROUND 3 — THE CONSENT WIDENING THIS WAVE INTRODUCED, AND CLOSED (§6). Rounds 1-2
made "named" and "armed" the same set, which was right, and then armed that whole
set off the attachment's SUFFIX. Measured on ``_resolve_armed_tools``:

    "thanks!"             + client_fees.xlsx -> ... excel_edit, excel_apply_spec
    "thanks!"             + summary.docx     -> ... convert_document, write_document
    "summarize this"      + report.pdf       -> ... pdf_arrange, pdf_split
    "what does this say?" + notes.txt        -> ... convert_document, write_document

Four read-only requests arming file MUTATORS, and arming is not merely offering:
chat passes the armed list as the turn's ``session_allow``, so each would have
run with NO approval card. This did not exist at v1.195.0. §6 pins it closed in
both directions — the read half still arms on TYPE (the wave's entire point), the
change half arms only for a request that asks for that change — and §6b pins what
the block SAYS when nothing armed, because silence about a real capability is the
under-exposure this wave exists to remove.

MANY FIXTURES BELOW GAINED AN INTENT-CARRYING QUESTION as a result. That is not
cosmetic: a test about a change verb whose question asks for no change is
asserting about the other branch. :data:`_INTENT` holds them, and
``test_the_intent_sentences_really_do_carry_intent`` re-derives the table from
the gate so none of them can quietly stop carrying intent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.chat_turn import _prepare_attachments, _resolve_armed_tools
from iron_jarvis.documents.attachment_rag import (
    _CHANGE_UNARMED,
    _DERIVED_TARGET,
    LIVE_VERBS,
    change_verbs_wanted,
    live_file_line,
    live_tool_names,
    live_verbs_for,
)
from iron_jarvis.documents.excel_tools import (
    ExcelEditTool,
    ExcelQueryTool,
    ExcelReadTool,
)
from iron_jarvis.providers.adapters.base import LLMResponse
from iron_jarvis.providers.router import RouteResult
from iron_jarvis.tools.base import ToolContext

_SRC = Path(__file__).resolve().parents[1] / "src" / "iron_jarvis" / "daemon"

#: The system prompt's attachment section, so an assertion about an ATTACHMENT
#: cannot be satisfied by another section of the prompt. Pinned after a real
#: miss during this unit's mutation check: a bare ``"outside it" in system``
#: passed under a mutation that removed the whole warning, because
#: ``DRAFT_BLOCK`` happens to say "keep your own commentary outside it". That is
#: CLAUDE.md's proxy-signal rule ("assert the THING, not something that lands
#: near it") in prompt form.
_ATTACH_HEADER = "# Attachments (provided by the user this turn)"

#: The confinement warning, spelled out. Distinctive enough that it can only
#: come from ``live_file_line``.
_CONFINED = "reach ONLY your tool workspace and this file is outside it"

#: An INTENT-CARRYING request per attachment type, and a READ-ONLY one.
#:
#: Round 3 split the arming key: READ verbs arm on the attachment's TYPE, CHANGE
#: verbs only on a request that asks for a change. So every test below that is
#: ABOUT a change verb has to say which question it is asking — a fixture that
#: leaves the question implicit is now asserting about the wrong branch. Each
#: right-hand sentence was MEASURED through ``select_auto_tools`` to score the
#: type's change verbs (see ``test_the_intent_sentences_really_do_carry_intent``,
#: which re-derives this table from the gate rather than trusting the comment).
_INTENT = {
    ".xlsx": "update cell B2 to 500",
    ".xlsm": "update cell B2 to 500",
    ".pdf": "split this pdf into separate pages",
    ".docx": "convert this to pdf",
    ".pptx": "convert this to pdf",
    ".txt": "write this up as a memo",
    ".csv": "convert this to xlsx",
    ".bmp": "resize this image to 800px",
}

#: The four requests the coordinator MEASURED arming mutators off file type
#: alone, before this round's gate. Read-only every one of them.
_READ_ONLY_MEASURED = [
    ("thanks!", "client_fees.xlsx"),
    ("thanks!", "summary.docx"),
    ("summarize this", "report.pdf"),
    ("what does this say?", "notes.txt"),
]


# ------------------------------------------------------------------ fixtures --


def _workbook(path: Path) -> None:
    """A small real .xlsx — small enough that it takes the INLINE branch, i.e.
    the common case where the model already has the whole sheet as text and so
    has no reason to reach for a tool unless it is told the file is live."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Q1"
    ws.append(["Client", "Fee", "Paid"])
    ws.append(["Acme", 1000, "yes"])
    ws.append(["Belmont", 2500, "no"])
    wb.save(str(path))


def _body(attachments, question="what do these fees add up to?", project_id="",
          auto_tools=True):
    return SimpleNamespace(
        attachments=list(attachments),
        messages=[SimpleNamespace(role="user", content=question)],
        workspace_dir="",
        project_id=project_id,
        # `_resolve_armed_tools` reads these three; the arming tests below drive
        # the same object the lanes pass.
        tools=[],
        skill="",
        auto_tools=auto_tools,
    )


@pytest.fixture
def client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path / "home")))


@pytest.fixture
def scene(client, tmp_path):
    """A project-grounded chat with a workbook uploaded the way chat uploads
    them: ``POST /documents/upload`` writes to ``<home>/uploads`` (see
    ``routes/documents.py:114``), while the turn's tools run in the project
    root. That gap IS the bug."""
    platform = client.app.state.platform
    project = tmp_path / "project"
    project.mkdir()
    uploads = Path(platform.config.home) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    book = uploads / "client_fees.xlsx"
    _workbook(book)
    return SimpleNamespace(
        platform=platform, project=project, uploads=uploads, book=book,
        d=SimpleNamespace(platform=platform),
    )


def _ctx(scene, workspace: Path) -> ToolContext:
    """The ToolContext chat builds for a turn (``chat_turn.py`` ~1911) — the
    same workspace, so a tool call here fails and succeeds for exactly the
    reasons it would in the app."""
    return ToolContext(
        workspace=workspace, session_id="chat", agent_run_id="chat",
        config=scene.platform.config, event_bus=scene.platform.event_bus,
        engine=scene.platform.engine,
    )


#: How an absolute path STARTS, per platform family: a drive letter, a UNC
#: share, or a POSIX root. Each anchor is scanned SEPARATELY (see
#: :func:`_paths_in`) so a stray ``/`` earlier in the prose cannot swallow the
#: region a real ``C:\`` path lives in.
_PATH_ANCHORS = (r"[A-Za-z]:[\\/]", r"\\\\", r"/")


def _paths_in(block: str, *suffixes: str) -> list[str]:
    """Every absolute path the block hands the model, however it is worded.

    Deliberately NOT keyed on the sentence: the test must fail when the model
    cannot reach the file, not when someone rephrases the line.

    SPACE-SAFE, and that is not hypothetical. The first cut split the block on
    whitespace, which works only because ``tmp_path`` never contains a space —
    while the ONE directory this feature exists for does: a packaged install
    uploads to ``%APPDATA%/Iron Jarvis/.ironjarvis/uploads`` (CLAUDE.md, "State
    home"). A whitespace split truncates that to ``C:\\Users\\VR\\AppData\\
    Roaming\\Iron`` and finds no ``.xlsx`` at all, so every path assertion in
    this file would have gone quietly vacuous on the user's real machine and
    stayed green here forever. Pinned by
    ``test_paths_in_survives_a_workspace_with_a_space``.

    So the scan runs from a path ANCHOR to the wanted suffix instead, stopping
    at a newline or a parenthesis (the block's own punctuation) and refusing
    anything ``Path.is_absolute`` does not accept — which is what keeps a POSIX
    hit out of a Windows result and vice versa.
    """
    wanted = tuple(s.lower() for s in (suffixes or (".xlsx", ".xlsm")))
    found: dict[int, str] = {}
    for suffix in wanted:
        for anchor in _PATH_ANCHORS:
            pattern = re.compile(
                anchor + r"[^\n\r()]*?" + re.escape(suffix) + r"(?![A-Za-z0-9])",
                re.IGNORECASE,
            )
            for match in pattern.finditer(block):
                candidate = match.group(0)
                if Path(candidate).is_absolute():
                    found.setdefault(match.start(), candidate)
    return [found[start] for start in sorted(found)]


# ------------------------------------------------- 0. the defect, end-to-end --


async def test_the_bare_filename_is_unreachable_from_a_project(scene):
    """The measured cause of "0 excel_* calls in 482 tool executions".

    Nothing here touches the new code: it pins WHY a name is not a handoff, so
    that the assertions below are testing a real repair rather than a nicer
    sentence.
    """
    ctx = _ctx(scene, scene.project)  # grounded chat: tools run in the project

    read = await ExcelReadTool().execute({"path": "client_fees.xlsx"}, ctx)
    assert read.ok is False and "No such file" in (read.error or "")

    edit = await ExcelEditTool().execute(
        {"path": "client_fees.xlsx", "edits": [{"cell": "B2", "value": 1}]}, ctx
    )
    assert edit.ok is False
    assert "no such workbook in the workspace" in (edit.error or "")
    # ...and the workbook was sitting in <home>/uploads the whole time.
    assert scene.book.is_file()


async def test_paths_in_survives_a_workspace_with_a_space(scene, tmp_path):
    """GUARD THE GUARD, on the one path shape that actually ships.

    Every path assertion in this file runs through ``_paths_in``, and every
    fixture here lives under ``tmp_path``, which never contains a space. The
    real install's upload folder is ``%APPDATA%/Iron Jarvis/.ironjarvis/
    uploads`` — so a whitespace-split helper reports "no absolute workbook path
    reached the model" nowhere and silently reports the WRONG path everywhere
    that matters. Proven the same way as the rest of the file: the parsed path
    is handed to the real tool.
    """
    spaced = tmp_path / "Iron Jarvis" / "uploads"
    spaced.mkdir(parents=True)
    book = spaced / "client fees.xlsx"
    _workbook(book)
    assert " " in str(book)

    block = live_file_line(book, workspace=spaced)
    paths = _paths_in(block)
    assert paths == [str(book)], f"the space truncated the handoff:\n{block}"

    read = await ExcelReadTool().execute(
        {"path": paths[0], "sheet": "Q1"}, _ctx(scene, spaced)
    )
    assert read.ok is True, read.error
    assert "Belmont" in read.output


# --------------------------------------- 1. the path the model gets RESOLVES --


async def test_project_grounded_attachment_hands_over_a_resolvable_path(scene):
    """THE unit's claim: from a project-grounded chat, the text the model
    receives contains a path a document tool can actually open — proven by
    opening it and reading the workbook's real numbers back out."""
    _images, block = await _prepare_attachments(
        scene.d, _body(["client_fees.xlsx"]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    paths = _paths_in(block)
    assert paths, f"no absolute workbook path reached the model:\n{block}"

    ctx = _ctx(scene, scene.project)
    for path in paths:
        read = await ExcelReadTool().execute({"path": path, "sheet": "Q1"}, ctx)
        assert read.ok is True, f"the model was handed an unusable path: {read.error}"
        assert "Belmont" in read.output

        # And the tool the block leads with actually COMPUTES on it — the
        # operation a flattened text rendering cannot provide.
        q = await ExcelQueryTool().execute(
            {"path": path, "sheet": "Q1", "op": "sum", "column": "Fee"}, ctx
        )
        assert q.ok is True, q.error
        assert "3500" in q.output.replace(",", "")


async def test_the_uploads_fallback_path_resolves_too(scene):
    """The other workspace. An ungrounded chat runs its tools in
    ``<home>/uploads``; the absolute path is the ONE form that works in both,
    which is why the line does not hand over a workspace-relative one."""
    _images, block = await _prepare_attachments(
        scene.d, _body(["client_fees.xlsx"]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root="",
    )
    ctx = _ctx(scene, scene.uploads)
    for path in _paths_in(block):
        read = await ExcelReadTool().execute({"path": path}, ctx)
        assert read.ok is True, read.error


# ------------------------------------- 1b. THE RETRIEVAL BRANCH, which is the --
#                                            branch this module exists for -----
#
# Everything above rides the INLINE branch, because every fixture here is three
# rows wide. The OTHER branch — `len(text) > inline_budget`, i.e. a 200-page PDF
# or a real multi-sheet workbook — is the one the original finding quoted:
#
#     (Retrieved 1 of 1 sections. For other parts, use read_document with
#      page_range, or excel_profile/excel_query for spreadsheets.)
#
# ...and it is a SEPARATE `parts.append(...)` call site, with its own `+ live`.
# Measured: deleting that `+ live` and nothing else left all 30 tests green, so
# a future edit there could silently drop the absolute path and the editing
# verbs for exactly the big documents this feature was built for — restoring the
# original defect for the worst case, with zero test signal.


def _big_workbook(path: Path, rows: int = 400) -> int:
    """A workbook whose flattened text does NOT fit the turn's inline budget —
    the real one (``_ATTACH_EXTRACT_CHARS`` = 6000), not a shrunken fixture, so
    the branch is taken for the reason it is taken in the app. Returns the true
    sum of the Fee column, which only the LIVE file can be asked for."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Q1"
    ws.append(["Client", "Fee", "Paid"])
    total = 0
    for i in range(rows):
        fee = 100 + i
        total += fee
        ws.append([f"Client {i:04d} Holdings LLC", fee, "yes" if i % 2 else "no"])
    wb.save(str(path))
    return total


async def test_the_oversized_branch_hands_over_the_live_file_too(scene):
    """The retrieval branch carries the SAME handoff as the inline one.

    Both halves are asserted on ONE block — that it really is the retrieval
    branch (``Retrieved``, the marker the finding quoted) AND that the live-file
    line rode along — and then the path is taken out of that block and used, so
    "the model can reach the file" is proven by reaching it.

    THE QUESTION IS INTENT-CARRYING SINCE ROUND 3, and that is not incidental:
    the change half of the handoff only rides a request that asks for a change,
    so a read-only question here would have quietly stopped covering the
    ``excel_edit``/``_CONFINED`` half of the assertion below. The read half is
    covered on a contentless message by
    ``test_a_read_only_request_arms_the_readers_and_no_mutator``.
    """
    book = scene.uploads / "fee_register.xlsx"
    total = _big_workbook(book)

    _images, block = await _prepare_attachments(
        scene.d, _body(["fee_register.xlsx"], question=_INTENT[".xlsx"]),
        # THE APP'S OWN DEFAULTS (`_attachment_budgets` with an unknown window),
        # so this is the branch a real 400-row workbook takes, not one forced by
        # an artificially tiny budget.
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    # (a) it IS the retrieval branch: the sheet did not arrive whole.
    assert "indexed section(s)" in block and "Retrieved" in block, block
    assert "Client 0399 Holdings LLC" not in block, (
        "the whole workbook fitted after all — this test stopped covering the "
        "branch it names"
    )
    # (b) ...and the handoff is still there, in full: marker, absolute path,
    # read verbs, mutating verbs, and the confinement warning this grounded chat
    # is owed. Each one is a thing `+ live` is the only source of.
    assert "(LIVE FILE" in block, f"the retrieval branch dropped the handoff:\n{block}"
    assert str(book) in block
    assert _offered(block) == "excel_profile, excel_query, excel_read"
    assert "excel_edit" in block and _CONFINED in block

    # (c) AND THE PATH WORKS — the run-the-real-tool standard the rest of this
    # file holds itself to. `excel_query` answers the question the flattened,
    # RETRIEVED excerpt structurally cannot: the sum over rows that were never
    # shown to the model.
    ctx = _ctx(scene, scene.project)
    path = _paths_in(block)[0]
    q = await ExcelQueryTool().execute(
        {"path": path, "sheet": "Q1", "op": "sum", "column": "Fee"}, ctx
    )
    assert q.ok is True, f"the retrieval block handed over an unusable path: {q.error}"
    assert str(total) in q.output.replace(",", "")
    # The other verb the line names, on the same path.
    read = await _doc_tool("read_document").execute({"path": path}, ctx)
    assert read.ok is True, read.error


# ------------------------------ 2. the EDITING verb, and the truth about it --


async def test_the_mutating_verbs_are_named_and_the_promise_holds(scene):
    """An ungrounded chat CAN edit the upload in place — and the block says so.

    Asserted by running ``excel_edit`` on exactly the path the block gave, with
    exactly the workspace the turn would use, and then re-reading the cell.
    """
    _images, block = await _prepare_attachments(
        scene.d, _body(["client_fees.xlsx"], question=_INTENT[".xlsx"]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root="",
    )
    assert "excel_edit" in block, "the model cannot discover it may CHANGE the file"

    ctx = _ctx(scene, scene.uploads)
    path = _paths_in(block)[0]
    edit = await ExcelEditTool().execute(
        {"path": path, "sheet": "Q1", "edits": [{"cell": "B2", "value": 4242}]}, ctx
    )
    assert edit.ok is True, f"the block promised an edit that was refused: {edit.error}"
    back = await ExcelReadTool().execute({"path": path, "sheet": "Q1"}, ctx)
    assert "4242" in back.output


async def test_a_promise_the_tools_would_refuse_is_never_made(scene):
    """The other half, and the one that keeps the line honest.

    ``excel_edit`` resolves its path through ``safe_path``, so an upload sitting
    outside the grounded project's folder CANNOT be edited in place. The block
    must not say otherwise: it names the verbs, states the confinement, and the
    refusal it predicts is verified against the real tool.

    Intent-carrying question, for the reason given in
    ``test_the_oversized_branch_hands_over_the_live_file_too``: the confinement
    warning is part of the CHANGE clause, and there is no change clause on a
    request that asked for no change.
    """
    _images, block = await _prepare_attachments(
        scene.d, _body(["client_fees.xlsx"], question=_INTENT[".xlsx"]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    assert "excel_edit" in block

    ctx = _ctx(scene, scene.project)
    path = _paths_in(block)[0]
    edit = await ExcelEditTool().execute(
        {"path": path, "sheet": "Q1", "edits": [{"cell": "B2", "value": 1}]}, ctx
    )
    assert edit.ok is False and "escapes the session workspace" in (edit.error or "")
    # So the block must have WARNED, not promised. The warning is what makes
    # naming the verb safe here.
    assert _CONFINED in block, (
        "the block named an in-place edit that the tools refuse, without saying "
        f"so:\n{block}"
    )
    assert "NEW file" in block  # ...and named what to do instead


# ------------------------- 2b. the DERIVED verbs, and the truth about THEM --
#
# The pair above (promise-holds / promise-refused) is what makes naming a
# mutating verb safe, and the first cut of this unit applied it to `.xlsx`
# ONLY. Every `in_place=False` type — `.docx`, `.pptx`, `.txt`, `.md`, `.json`,
# `.csv`, `.bmp`, i.e. what the ledger's 96 `read_document` calls were mostly
# about — short-circuited the workspace check and emitted a bare "Change it:
# convert_document, write_document." next to a path OUTSIDE the workspace. The
# `in_place` verdict was reasoned about the SOURCE and then used to answer a
# question about the TARGET. Measured on that block, before the repair:
#
#     write_document   {"path": <the path the block just named>} -> ok=False
#         PermissionError: ... escapes the session workspace
#     convert_document {"source": <abs>, "target": <abs beside it>} -> ok=False
#     convert_document {"source": <abs>, "target": "letter.pdf"}    -> ok=True
#
# So the same pair now runs for a derived type, against the block's own path.


def _doc_tool(name: str):
    from iron_jarvis.documents.tools import document_tools

    return next(t for t in document_tools() if t.name == name)


@pytest.mark.parametrize(
    "name, writer",
    [("letter.docx", "convert_document"), ("deck.pptx", "convert_document"),
     ("notes.txt", "write_document"), ("fees.csv", "convert_document")],
)
async def test_a_derived_change_verb_says_where_the_output_goes(scene, name, writer):
    """A derived verb is named for these types, its refusal is REAL, and the
    block says the one thing that makes it usable — where the output goes."""
    src = scene.uploads / name
    if src.suffix in (".docx", ".pptx"):
        from iron_jarvis.documents import write_document as _write

        _write(src, "Dear client,\n\nYour 2025 return is enclosed.\n")
    elif src.suffix == ".csv":
        src.write_text("Client,Fee\nAcme,1000\n", encoding="utf-8")
    else:
        src.write_text("Reminder: the extension deadline is 15 Oct.", encoding="utf-8")

    # INTENT-CARRYING. The first cut of this test asked "tidy this up for me",
    # which round 3 measured as scoring no change verb at all — so after the
    # consent gate it exercises the NOT-ARMED branch and stops covering the
    # derived-target clause it is named for. The unarmed branch has its own
    # test (``test_a_read_only_request_gets_the_honest_unarmed_clause``).
    _images, block = await _prepare_attachments(
        scene.d, _body([name], question=_INTENT[src.suffix]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    assert writer in block, f"no change verb offered for {name}:\n{block}"
    path = _paths_in(block, src.suffix)[0]
    ctx = _ctx(scene, scene.project)  # grounded: the upload is OUTSIDE the ws

    # (a) THE REFUSAL IS REAL. Writing back to the path the block just named —
    # the single most likely reading of an unqualified "Change it: ..." — is
    # rejected by the same `safe_path` that rejects `excel_edit` above.
    back = await _doc_tool("write_document").execute(
        {"path": path, "content": "rewritten"}, ctx
    )
    assert back.ok is False and "escapes the session workspace" in (back.error or "")
    beside = await _doc_tool("convert_document").execute(
        {"source": path, "target": str(Path(path).with_suffix(".pdf"))}, ctx
    )
    assert beside.ok is False and "escapes the session workspace" in (beside.error or "")

    # (b) SO THE BLOCK MUST HAVE SAID SO. This is the assertion whose absence
    # let both of the reviewer's mutations stay green on the docx path.
    assert _DERIVED_TARGET in block, (
        "the block named a change verb whose target the tools refuse, without "
        f"saying where the output must go:\n{block}"
    )

    # (c) AND THE FORM IT NAMES ACTUALLY WORKS — a NEW relative name, resolved
    # against this turn's real workspace.
    made = await _doc_tool("convert_document").execute(
        {"source": path, "target": f"{Path(name).stem}-copy.pdf"}, ctx
    )
    assert made.ok is True, f"the block's own instruction was refused: {made.error}"
    assert (scene.project / f"{Path(name).stem}-copy.pdf").is_file()


def test_every_derived_entry_carries_the_target_clause():
    """Structural backstop for the parametrized cases above: no future type may
    be added to the table with a change verb and no output guidance."""
    # Guard the guard: every assertion in this file spells the clause as
    # ``_DERIVED_TARGET in block``, and ``"" in block`` is True — so an empty
    # constant would make ALL of them vacuous at once. Caught on this unit's own
    # mutation run.
    assert _DERIVED_TARGET.strip(), "the clause the block promises must say something"
    entries = list(LIVE_VERBS.values()) + [live_verbs_for(".bmp"), live_verbs_for(".txt")]
    derived = [v for v in entries if v is not None and not v.in_place]
    assert derived
    for verbs in derived:
        assert _DERIVED_TARGET in verbs.change, verbs
    for verbs in entries:
        if verbs is not None and verbs.in_place:
            # In place = the target IS the source; the clause would be false.
            assert _DERIVED_TARGET not in verbs.change


# ---------------------------------------- 3. type-awareness, from ONE table --


def test_the_verb_table_is_type_aware_and_has_no_second_copy():
    """A workbook gets the excel verbs, a PDF the page tools, a docx the
    document ones — and unknown types get NO promise at all."""
    assert live_verbs_for(".xlsx")[0].startswith("excel_")
    assert "excel_edit" in live_verbs_for(".xlsm")[1]
    assert "pdf_arrange" in live_verbs_for(".pdf")[1]
    assert "read_document" in live_verbs_for(".docx")[0]
    # The image family is not re-listed here — it is READ from readers.py, so a
    # suffix the reader knows is a suffix this table knows.
    from iron_jarvis.documents.readers import _IMAGE_SUFFIXES

    for suffix in _IMAGE_SUFFIXES:
        assert live_verbs_for(suffix) is not None, suffix
    assert "image_convert" in live_verbs_for(".png")[1]
    # Nothing true to say => nothing said.
    assert live_verbs_for(".zip") is None
    assert live_verbs_for("") is None


async def test_a_pdf_attachment_names_the_page_tools_not_the_excel_ones(scene):
    from iron_jarvis.documents import write_document

    pdf = scene.uploads / "engagement.pdf"
    write_document(pdf, "Engagement letter for the 2025 filing season.")
    _images, block = await _prepare_attachments(
        scene.d, _body(["engagement.pdf"], question=_INTENT[".pdf"]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    assert "pdf_arrange" in block and "pdf_split" in block
    assert "excel_edit" not in block
    # pdf_arrange reads an ABSOLUTE source from anywhere, so the IN-PLACE
    # warning is not owed here — inventing one would be its own small lie. What
    # IS owed is where the OUTPUT may land, and this assertion used to say
    # `_CONFINED not in block` and nothing else, which PINNED the hole: it
    # certified as correct a block that gave a derived type no output guidance
    # at all. See ``test_a_derived_change_verb_says_where_the_output_goes``.
    assert _CONFINED not in block
    assert _DERIVED_TARGET in block


def test_the_named_verbs_are_real_registered_tools(client):
    """Every tool the line NAMES exists, and every real tool it names is in the
    arming key — the two halves of the table cannot drift apart.

    ``live_tool_names`` is what ``_resolve_armed_tools`` arms from, so a name
    that appears in the prose and not in the tuple is a verb the prompt promises
    and the turn never arms: the exact "prompt claims a runnable tool the model
    cannot call" lie ``_write_directive`` refuses to tell in the other
    direction.
    """
    import re

    from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS

    registered = set(client.app.state.platform.registry.names())
    named: set[str] = set()
    entries = list(LIVE_VERBS.values()) + [live_verbs_for(".bmp"), live_verbs_for(".txt")]
    for verbs in entries:
        assert verbs is not None
        prose = f"{verbs.read} {verbs.change}"
        for tool in verbs.tools:
            assert tool in registered, f"{tool} is not a registered tool"
            assert tool in prose, f"{tool} is armed but never named to the model"
        # ...and nothing REAL is named without being armable.
        for token in re.findall(r"\b[a-z][a-z0-9]+(?:_[a-z0-9]+)+\b", prose):
            if token in registered:
                assert token in verbs.tools, (
                    f"{token} is named to the model but absent from the arming key"
                )
        named |= set(verbs.tools)

    # DOCUMENTED BOUNDARY, not a silent hole: `excel_apply_spec` is named in the
    # prompt, and whether it is in `AUTO_SAFE_TOOLS` is owned by
    # tools/autoselect.py — another unit's file this wave, changing UNDER this
    # one. A SUBSET assertion so both states are legal here: out of the list, it
    # reaches a turn only through explicit "+" arming; in it, this test goes
    # green unchanged. What is NOT legal is any OTHER named verb going unarmable,
    # which is the thing this line is actually watching.
    assert named - set(AUTO_SAFE_TOOLS) <= {"excel_apply_spec"}, sorted(
        named - set(AUTO_SAFE_TOOLS)
    )


def test_live_tool_names_reads_the_same_tuple_and_never_raises():
    assert live_tool_names(".xlsx")[:3] == ["excel_profile", "excel_query", "excel_read"]
    assert "pdf_split" in live_tool_names(".pdf")
    assert live_tool_names(".zip") == []
    assert live_tool_names(None) == []  # type: ignore[arg-type]
    # The two halves partition the whole, in order, for every type in the table.
    for suffix in [*LIVE_VERBS, ".bmp", ".txt"]:
        assert (
            live_tool_names(suffix, kind="read")
            + live_tool_names(suffix, kind="change")
            == live_tool_names(suffix)
        ), suffix
    assert live_tool_names(".zip", kind="read") == []
    assert live_tool_names(None, kind="change") == []  # type: ignore[arg-type]


# ------------------------------- 3b. the promise is BACKED by tool_specs -----
#
# The block naming `excel_query` is worth nothing if the turn's tool list does
# not hold it. Before this, the ONLY attachment signal in the selector was
# `bump({"read_document": 9})` keyed on a doc-extension regex — blind to WHICH
# document — so with a workbook attached, the ledger's own phrasings armed
# read_document ALONE while the prompt said "Work on it directly: excel_profile,
# excel_query, excel_read". Naming the tools louder cannot help a turn whose
# tool_specs does not hold them, which is why the measured zero stayed zero.


@pytest.mark.parametrize("question", [
    "what do these fees add up to?",          # the ledger's actual shape
    "update the fee for Belmont to 3000",     # no "workbook"/"column" anywhere
    "can you take a look at this?",
])
def test_an_attached_workbook_arms_the_excel_READ_family(scene, question):
    """THE WAVE'S CORE REPAIR, and the half that round 3 must not weaken.

    All three sentences are the ledger's own phrasings and none of them names a
    spreadsheet, so before this wave they armed ``read_document`` ALONE while
    the prompt said "Work on it directly: excel_profile, excel_query,
    excel_read". The READ verbs arm on the attachment's TYPE and must keep doing
    so — that is what the 96-vs-0 measurement bought.

    ``excel_edit`` is deliberately NOT asserted here any more. It used to be,
    and that assertion was the consent defect written down as a requirement:
    "update the fee for Belmont to 3000" scores no workbook verb in the sentence
    lane, so the only thing that armed the MUTATOR on it was the file's suffix.
    The mutator's own coverage is
    ``test_an_intent_carrying_request_still_arms_the_change_verbs``.
    """
    armed, auto = _resolve_armed_tools(
        scene.d, _body(["client_fees.xlsx"], question=question)
    )
    for verb in ("excel_profile", "excel_query", "excel_read"):
        assert verb in armed, f"{question!r} armed {armed}"
    assert "read_document" in armed, "the old signal must not be traded away"
    assert set(auto) <= set(armed)


def test_arming_is_gated_on_auto_tools_and_bounded(scene):
    """It must not become consent the user did not give, and must not crowd out
    the verb the user actually typed."""
    from iron_jarvis.daemon.chat_turn import _MAX_ARMED_TOOLS

    off, _auto = _resolve_armed_tools(
        scene.d, _body(["client_fees.xlsx"], auto_tools=False)
    )
    assert off == [], off

    body = _body(["client_fees.xlsx"], question="search the web for the fee schedule")
    body.tools = ["web_search", "web_fetch"]
    armed, _auto = _resolve_armed_tools(scene.d, body)
    assert armed[:2] == ["web_search", "web_fetch"], "explicit picks lost their slots"
    assert len(armed) <= _MAX_ARMED_TOOLS


@pytest.mark.parametrize("question", [
    "what do these fees add up to?",   # read-only: the READ half only
    "update cell B2 to 500",           # intent: both halves
])
async def test_the_block_and_the_tool_list_name_the_same_tools(scene, question):
    """The property that closes the loop: every verb the prompt line offers for
    this attachment is a verb the model can actually call.

    Run over BOTH branches of the consent gate, because the gate moved the line
    and the tool list TOGETHER and the only thing worth asserting is that they
    still move together. On the read-only question the change verbs are neither
    named nor armed; on the intent question they are both.
    """
    body = _body(["client_fees.xlsx"], question=question)
    _images, block = await _prepare_attachments(
        scene.d, body, inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    armed, _auto = _resolve_armed_tools(scene.d, body)
    line = [ln for ln in block.splitlines() if ln.startswith("(LIVE FILE")][0]
    from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS

    wanted = change_verbs_wanted(".xlsx", question, attachments=["client_fees.xlsx"])
    for tool in live_tool_names(".xlsx", kind="read"):
        assert tool in AUTO_SAFE_TOOLS, f"{tool} is named but not auto-armable"
        assert tool in line and tool in armed, (
            f"{tool} is promised in the prompt but absent from tool_specs"
        )
    for tool in live_tool_names(".xlsx", kind="change"):
        if tool in wanted:
            assert tool in line and tool in armed, (
                f"{tool} was asked for, so it must be named AND armed"
            )
        else:
            assert tool not in line and tool not in armed, (
                f"{tool} was not asked for, so it must be neither named nor "
                f"armed — it armed {armed}"
            )


# ---------------------------------------------------- 4. it NEVER breaks a turn --


def test_live_file_line_never_raises():
    assert live_file_line(None) == ""  # type: ignore[arg-type]
    assert live_file_line("nope.zip").endswith("nope.zip.)")
    assert "excel_" in live_file_line("C:/x/y.xlsx", workspace=None)
    # Unknown workspace claims nothing either way.
    assert _CONFINED not in live_file_line("C:/x/y.xlsx", workspace=None)


async def test_an_unreadable_attachment_still_gets_its_handoff(scene):
    """The branch where this matters MOST: a workbook openpyxl choked on is
    exactly what excel_profile exists for. The 'could not read' note survives,
    and the file is still handed over."""
    broken = scene.uploads / "renamed.xlsx"
    broken.write_bytes(b"%PDF-1.4 this is not a workbook at all")
    _images, block = await _prepare_attachments(
        scene.d, _body(["renamed.xlsx"]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    assert "## Attached file: renamed.xlsx" in block
    assert str(broken) in block
    # No text was extracted, so the block must not claim there is a rendering
    # of it above the line.
    assert "flat rendering" not in block


# ---------- 4b. it never tells the model to retry what cannot work ----------


class _Blind:
    """A tool-capable, vision-BLIND adapter — the shape ``_vision_unavailable_
    reason`` exists for (copied from tests/test_chat_scans_v1174.py)."""

    provider = "codex-cli"
    model = "gpt"

    def capabilities(self):
        return {"tool_use": True, "vision": False}


class _Seeing(_Blind):
    """The other fleet: a connected provider that CAN accept images."""

    provider = "anthropic"
    model = "claude"

    def capabilities(self):
        return {"tool_use": True, "vision": True}


def _fleet(platform, monkeypatch, adapters: dict) -> None:
    monkeypatch.setattr(platform.router, "_snapshot", lambda: set(adapters))
    monkeypatch.setattr(platform.providers, "get", lambda n, m=None: adapters[n])


def _offered(block: str) -> str:
    """The READ verbs the live-file line actually offers, isolated.

    Read off the segment rather than searched for in the whole block, because
    the withdrawal wording CONTAINS the withdrawn verb ("image_info (view_image
    needs a vision model; none is connected)"). ``"view_image" in block`` is
    therefore true whether the verb is offered or withdrawn — a substring test
    on the block cannot tell the two apart, and one written that way survived a
    mutation that withdrew ``view_image`` unconditionally.
    """
    line = [ln for ln in block.splitlines() if ln.startswith("(LIVE FILE")][0]
    rest = line.split("Work on it directly: ", 1)[1]
    # `live_file_line` has THREE tails — " Change it: ..." for a named change
    # verb, " In-place edits (...) reach ONLY ..." for a source outside the
    # workspace, and " The tools that CHANGE it are NOT armed this turn" when
    # the request asked for no change (v1.196.0 round 3). Splitting on one of
    # them only silently returns the whole line for the others, which is how an
    # equality assertion here first went red.
    parts = re.split(
        r"\.\s+(?:Change it:|In-place edits|No change tool is armed)",
        rest, maxsplit=1,
    )
    assert len(parts) == 2, f"the live-file line changed shape: {line}"
    return parts[0]


async def test_a_blind_turn_does_not_offer_view_image(scene, monkeypatch):
    """A ``.bmp`` takes the DOCUMENT path (it is outside ``_ATTACH_IMAGE_TYPES``),
    so with no vision anywhere the block already says "there is no text layer to
    read ... no model is wired to transcribe it" — and then used to say "Work on
    it directly: view_image, image_info." ``view_image`` routes through the SAME
    router and returns ``images._NO_VISION_ERROR``, so that line told the model
    to retry the thing the line above had just said cannot work.

    Withdrawn, not hidden: the note SAYS why, and the Pillow-local verbs stay.
    """
    from PIL import Image

    bmp = scene.uploads / "scan.bmp"
    Image.new("RGB", (40, 40), (10, 20, 30)).save(bmp)
    _fleet(scene.platform, monkeypatch, {"codex-cli": _Blind()})

    # INTENT-CARRYING, so this test still covers `image_convert` below: the
    # withdrawal it is really about is in the READ half, but the assertion that
    # "the verbs that DO run with no model at all are still offered" reaches
    # into the CHANGE half, which a read-only question no longer renders. The
    # read-only branch for images is covered by
    # `test_a_read_only_request_gets_the_honest_unarmed_clause`'s siblings.
    _images, block = await _prepare_attachments(
        scene.d, _body(["scan.bmp"], question=_INTENT[".bmp"]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    # The note still fires and still says the same thing ("connect a vision
    # model ... and retry"); which of ocr.py's wordings lands depends on how far
    # the attempt got, so the assertion pins the CLAIM, not the sentence.
    assert "connect a vision" in block, block
    line = [ln for ln in block.splitlines() if ln.startswith("(LIVE FILE")][0]
    offered = _offered(block)
    assert not offered.startswith("view_image"), (
        f"the block told the model to retry the tool it just said cannot run:\n{block}"
    )
    # WITHDRAWN, NOT HIDDEN (the repo's central rule): the verb is still named,
    # as the thing that is unavailable and why — never silently dropped.
    assert "view_image needs a vision model" in offered
    # The verbs that DO run with no model at all are still offered.
    assert offered.startswith("image_info") and "image_convert" in line

    # And the withdrawal is real, not cosmetic: view_image would in fact refuse.
    from iron_jarvis.tools.images import image_tools

    view = next(t for t in image_tools(scene.platform) if t.name == "view_image")
    res = await view.execute({"path": str(bmp)}, _ctx(scene, scene.uploads))
    assert res.ok is False


@pytest.mark.parametrize("fleet, why", [
    ({"anthropic": _Seeing()}, "a connected provider CAN accept images"),
    ({"anthropic": _Seeing(), "codex-cli": _Blind()},
     "one blind provider in the fleet is not 'no vision anywhere'"),
    (None, "an unreadable/offline fleet is UNKNOWN, and unknown never withdraws"),
])
async def test_with_vision_available_the_verb_is_still_offered(
    scene, monkeypatch, fleet, why
):
    """The other direction, and the one that had NO TEST AT ALL.

    Under-exposure is the failure mode this whole wave exists to remove, so the
    withdrawal must fire ONLY on a positive "no vision anywhere". This assertion
    used to read ``assert "view_image" in block`` — which the WITHDRAWAL
    satisfies too, because ``_IMAGE_VERBS_BLIND`` spells the verb out inside its
    own explanation ("image_info (view_image needs a vision model; none is
    connected)"). Measured: forcing ``_has_vision`` to return False left that
    assertion, and all 30 tests in this file, green.

    So it asserts on the OFFERED segment (see :func:`_offered`) instead, and
    over the three fleets that must NOT withdraw — including the empty one,
    whose comment used to claim "offline fleet = unknown" without ever checking
    that the case it names is the case that ran.
    """
    from PIL import Image

    bmp = scene.uploads / "shot2.bmp"
    Image.new("RGB", (40, 40), (1, 2, 3)).save(bmp)
    if fleet is not None:
        _fleet(scene.platform, monkeypatch, fleet)

    _images, block = await _prepare_attachments(
        scene.d, _body(["shot2.bmp"]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    # The premise is CHECKED, not assumed: this fleet must genuinely produce no
    # "no vision" verdict, or the test below is asserting about another case.
    from iron_jarvis.daemon.chat_turn import _vision_unavailable_reason

    assert _vision_unavailable_reason(scene.d, "", "") == "", why

    offered = _offered(block)
    assert offered.startswith("view_image"), (
        f"the verb was withdrawn although {why}:\n{block}"
    )
    assert "needs a vision model" not in offered, offered
    assert "image_info" in offered


# ------------------- 4c. the block pays for its reminder ONCE ---------------


async def test_the_flat_rendering_reminder_is_said_once_per_block(scene):
    """It is a property of the BLOCK, not of each file: ``_MAX_ATTACHMENTS``
    turns a per-file sentence into a per-turn multiplier on a budgeted history
    (CLAUDE.md: "History is BUDGETED, never sliced"). Measured with
    ``context.budget.estimate_tokens``: 6 workbooks cost 594 tokens of live-file
    lines with it repeated and 529 with it said once."""
    from iron_jarvis.daemon.chat_turn import _MAX_ATTACHMENTS

    names = []
    for i in range(_MAX_ATTACHMENTS):
        book = scene.uploads / f"book{i}.xlsx"
        _workbook(book)
        names.append(book.name)
    _images, block = await _prepare_attachments(
        scene.d, _body(names), inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    assert block.count("LIVE FILE") == _MAX_ATTACHMENTS, "a file lost its handoff"
    assert block.count("flat rendering") == 1, block
    # Every file still carries its own path + verbs — only the elaboration is
    # deduplicated, never the handoff itself.
    for name in names:
        assert str(scene.uploads / name) in block
        assert block.count("excel_query") == _MAX_ATTACHMENTS


async def test_images_and_the_oversize_branch_are_untouched(scene, tmp_path):
    """The vision path is not a document path — it must not grow a file line."""
    from PIL import Image

    png = scene.uploads / "shot.png"
    Image.new("RGB", (40, 40), (10, 20, 30)).save(png)
    images, block = await _prepare_attachments(
        scene.d, _body(["shot.png"]),
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    assert len(images) == 1
    assert "LIVE FILE" not in block


# ------------------------------------------------- 5. BOTH LANES, for free ---


def _record_prompts(platform, calls: "list | None" = None) -> list[str]:
    """Capture the SYSTEM prompt each lane actually sends to the model — and,
    when *calls* is given, the ``tools`` list that rode with it, so "the prompt
    promises it" and "the model can call it" are checked against the SAME
    request."""
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


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
def test_both_lanes_hand_the_model_the_live_file(client, tmp_path, route):
    """The lock-step property, asserted rather than assumed.

    ``routes/chat.py`` imports ``_prepare_attachments`` from ``chat_turn`` (its
    own comment at the call site says this sharing is deliberate), so a fix
    inside that function must reach the streaming lane — the one the dashboard
    runs — without a second edit. This drives the REAL endpoints.
    """
    platform = client.app.state.platform
    project = tmp_path / "proj"
    project.mkdir()
    pid = client.post(
        "/projects", json={"name": "Fees", "root": str(project)}
    ).json()["id"]
    uploads = Path(platform.config.home) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    book = uploads / "client_fees.xlsx"
    _workbook(book)

    seen = _record_prompts(platform)
    resp = client.post(route, json={
        "messages": [{"role": "user", "content": "update cell B2 to 500"}],
        "attachments": ["client_fees.xlsx"],
        "project_id": pid,
        # AUTO ON, and that is now load-bearing rather than incidental: the
        # change clause this test asserts on (`excel_edit`, `_CONFINED`) is
        # gated on the user's Auto consent plus a request that asks for a
        # change, so the old `auto_tools: False` + "what do the fees total?"
        # pairing exercised neither. The ABSOLUTE PATH — the thing both lanes
        # exist to hand over — is asserted below regardless of the gate.
        "auto_tools": True,
    })
    assert resp.status_code == 200, resp.text
    assert seen, "the lane never called the model"
    system = seen[0]
    assert _ATTACH_HEADER in system
    # Scoped to the ATTACHMENT section: see _ATTACH_HEADER for the miss this
    # prevents.
    section = system.split(_ATTACH_HEADER, 1)[1]
    assert str(book) in section, (
        f"{route} did not hand the model the workbook's absolute path"
    )
    assert "excel_query" in section and "excel_edit" in section
    # ...and the lane told the preparer WHERE its tools run, so the in-place
    # claim is judged against this turn's real workspace. This chat is grounded
    # in `project` while the upload sits in <home>/uploads, so the honest
    # answer is the warning — a lane that dropped `project_root` would silently
    # promise an edit `excel_edit` refuses.
    assert _CONFINED in section, (
        f"{route} priced the in-place edit against the wrong workspace"
    )
    # The path in the prompt is the one the tools would be given.
    assert json.dumps(str(book))  # (path is JSON-safe; no escaping surprises)


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
@pytest.mark.parametrize("question, changes", [
    # The ledger's own phrasing — READ-ONLY. Before the repair the lane sent
    # `read_document` alone for it and the prompt's promise was unbacked; after
    # round 1 it also armed both workbook MUTATORS off the file's suffix.
    ("what do these fees add up to?", False),
    ("update cell B2 to 500", True),
])
def test_both_lanes_arm_what_the_block_promises(
    client, tmp_path, route, question, changes
):
    """The wave's second gate, end-to-end through the real endpoints.

    The block says "Work on it directly: excel_profile, excel_query,
    excel_read"; this asserts those names are in the ``tools`` list of the SAME
    model call — and, since round 3, that the CHANGE verbs are in that list on
    exactly the turns whose request asked for a change and on no others. Both
    directions through the real ASGI app, because ``session_allow`` is built
    from this same list: an over-armed mutator here is a tool that runs with no
    approval card.
    """
    platform = client.app.state.platform
    project = tmp_path / "proj"
    project.mkdir()
    pid = client.post(
        "/projects", json={"name": "Fees", "root": str(project)}
    ).json()["id"]
    uploads = Path(platform.config.home) / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    book = uploads / "client_fees.xlsx"
    _workbook(book)

    calls: list[list[str]] = []
    seen = _record_prompts(platform, calls)
    resp = client.post(route, json={
        "messages": [{"role": "user", "content": question}],
        "attachments": ["client_fees.xlsx"],
        "project_id": pid,
        "auto_tools": True,
    })
    assert resp.status_code == 200, resp.text
    assert seen and calls
    section = seen[0].split(_ATTACH_HEADER, 1)[1]
    armed = set(calls[0])
    for tool in ("excel_profile", "excel_query", "excel_read"):
        assert tool in section, f"{tool} left the prompt"
        assert tool in armed, (
            f"{route} promised {tool} in the prompt and did not arm it: {sorted(armed)}"
        )
    assert "read_document" in armed, "the pre-existing signal must survive"

    if changes:
        assert "excel_edit" in section and "excel_edit" in armed, sorted(armed)
    else:
        assert "excel_edit" not in armed, (
            f"{route} armed the workbook MUTATOR on a read-only request — and "
            f"this list becomes the turn's session_allow: {sorted(armed)}"
        )
        assert "excel_apply_spec" not in armed, sorted(armed)
        # ...and the prompt does not promise what the turn withheld.
        assert "excel_edit" not in section, section
        assert _CHANGE_UNARMED.strip() in section, section


# =============================================================================
# 6. THE CONSENT GATE — read verbs arm on TYPE, change verbs need INTENT
# =============================================================================
#
# ROUND 1 of this wave made "named" and "armed" the same set (correct) and then
# armed that whole set off the attachment's SUFFIX (a fail-closed violation, NEW
# IN THIS WAVE — it did not exist at v1.195.0). Measured by the coordinator on
# `chat_turn._resolve_armed_tools`, the function these tests drive:
#
#   "thanks!"             + client_fees.xlsx -> ... excel_edit, excel_apply_spec
#   "thanks!"             + summary.docx     -> ... convert_document, write_document
#   "summarize this"      + report.pdf       -> ... pdf_arrange, pdf_split
#   "what does this say?" + notes.txt        -> ... convert_document, write_document
#
# Four read-only requests, every one arming file MUTATORS. And an armed name is
# not merely OFFERED: chat passes the armed list as the turn's `session_allow`,
# so each of those would have run with NO approval card.
#
# These tests are the record that the exposure existed and was closed on
# purpose. They are written against the ARMING PATH, not `select_auto_tools` —
# the layer the coordinator measured wrongly the first time, and the layer that
# was never the problem.


def _write_tier() -> frozenset[str]:
    """``agents/runtime._WRITE_TIER`` — the app's own hand-maintained list of
    auto-armable tools that CREATE OR MODIFY content. Imported rather than
    re-listed so this file cannot disagree with the gate the agent lane uses."""
    from iron_jarvis.agents.runtime import _WRITE_TIER

    return _WRITE_TIER


@pytest.mark.parametrize("question, attachment", _READ_ONLY_MEASURED)
def test_a_read_only_request_arms_the_readers_and_no_mutator(
    scene, question, attachment
):
    """THE DEFECT, CLOSED — all four measured pairs, through the real arming
    path, asserting BOTH halves of the repair on the same call:

    * no mutator arms (the consent fix), checked against ``_WRITE_TIER`` AND
      against the type table's own ``change_tools``, because the two answer
      different questions — ``convert_document`` writes a new file and is NOT in
      ``_WRITE_TIER``, so a ``_WRITE_TIER``-only assertion would have passed on
      the ``.docx``/``.txt`` rows while ``convert_document`` armed;
    * the READ verbs still arm on the file's TYPE (the wave's core repair),
      which is what stops this test from being satisfiable by disarming
      everything.
    """
    suffix = Path(attachment).suffix
    armed, auto = _resolve_armed_tools(
        scene.d, _body([attachment], question=question)
    )
    change = set(live_tool_names(suffix, kind="change"))
    assert change, f"{suffix} has no change verbs — this row asserts nothing"

    assert not (set(armed) & change), (
        f"{question!r} + {attachment} armed {sorted(set(armed) & change)} off "
        f"FILE TYPE ALONE; this list becomes the turn's session_allow, so those "
        f"tools would run with no approval card. Armed: {armed}"
    )
    assert not (set(armed) & _write_tier()), f"armed {armed}"

    # ...and the wave's core property is untouched.
    for verb in live_tool_names(suffix, kind="read"):
        assert verb in armed, (
            f"{question!r} + {attachment} lost the READ verb {verb} — that is "
            f"the 12-of-18-never-run defect coming back: {armed}"
        )
    assert set(auto) <= set(armed)


@pytest.mark.parametrize("attachment", [
    "client_fees.xlsx", "summary.docx", "report.pdf", "notes.txt", "fees.csv",
])
def test_an_intent_carrying_request_still_arms_the_change_verbs(scene, attachment):
    """THE OTHER DIRECTION, and the one that keeps the gate from being a wall.

    A gate that never opens is not a fix, it is the under-exposure this whole
    wave exists to remove. Each sentence comes from :data:`_INTENT` and is
    verified below to be one the shared scorer actually recognises.
    """
    suffix = Path(attachment).suffix
    armed, _auto = _resolve_armed_tools(
        scene.d, _body([attachment], question=_INTENT[suffix])
    )
    wanted = change_verbs_wanted(suffix, _INTENT[suffix], attachments=[attachment])
    assert wanted, f"{_INTENT[suffix]!r} carries no intent for {suffix}"
    for verb in wanted:
        assert verb in armed, (
            f"{_INTENT[suffix]!r} asked for {verb} and the turn did not arm it: "
            f"{armed}"
        )


def test_the_intent_sentences_really_do_carry_intent():
    """GUARD THE GUARD. Every "must still arm" test above is only as honest as
    :data:`_INTENT`, and a sentence that quietly stopped scoring would turn each
    of them into an assertion about nothing. Re-derived from the gate itself."""
    for suffix, sentence in _INTENT.items():
        assert change_verbs_wanted(suffix, sentence), (
            f"{sentence!r} no longer scores any change verb for {suffix} — the "
            f"tests keyed on it have gone vacuous, not green"
        )
    # ...and the read-only fixtures really are read-only.
    for question, attachment in _READ_ONLY_MEASURED:
        suffix = Path(attachment).suffix
        assert change_verbs_wanted(suffix, question, attachments=[attachment]) == []


def test_the_gate_is_PER_VERB_not_a_boolean_change_flag(scene):
    """A request for a summary memo scores ``write_document`` — a real change
    verb. If the gate were "did the user ask for SOME change", that sentence
    would unlock ``excel_edit`` on an attached workbook, which is the widening
    from the other side. It asks per verb, so it does not."""
    memo = "write a summary memo of this"
    from iron_jarvis.tools.autoselect import select_auto_tools

    assert "write_document" in select_auto_tools(memo), "premise: it IS a change ask"
    armed, _auto = _resolve_armed_tools(
        scene.d, _body(["client_fees.xlsx"], question=memo)
    )
    assert "excel_edit" not in armed, armed
    assert "excel_apply_spec" not in armed, armed


def test_the_explicit_plus_pick_is_the_other_door(scene):
    """The "+" menu is the interactive consent the permission engine's session
    grant is built on, so a tool the user picked THEMSELVES is armed and named
    whatever the sentence says. Without this, the gate would read as "a change
    verb is only ever reachable by phrasing", which is not the decision made."""
    body = _body(["client_fees.xlsx"], question="thanks!")
    body.tools = ["excel_edit"]
    armed, _auto = _resolve_armed_tools(scene.d, body)
    assert armed[0] == "excel_edit", armed
    assert change_verbs_wanted(
        ".xlsx", "thanks!", explicit={"excel_edit"}
    ) == ["excel_edit"]
    # ...and it does NOT drag its sibling mutator in with it.
    assert "excel_apply_spec" not in armed, armed


def test_auto_off_arms_nothing_and_promises_nothing(scene):
    """The Auto toggle is the standing consent the whole selector rides on."""
    armed, _auto = _resolve_armed_tools(
        scene.d, _body(["client_fees.xlsx"], question=_INTENT[".xlsx"],
                       auto_tools=False)
    )
    assert armed == [], armed
    assert change_verbs_wanted(".xlsx", _INTENT[".xlsx"], auto=False) == []


def test_the_arming_pass_never_repeats_a_NAME(scene):
    """A coordinator probe reported every armed name TWICE. It is an artefact of
    reading the RETURN SHAPE — ``_resolve_armed_tools`` returns
    ``(explicit + auto, auto)``, and with no explicit picks the two lists are
    identical, so anything printing or concatenating both shows each name twice.
    The armed list itself has never contained a duplicate; pinned here so the
    question does not have to be re-litigated, including for the case that could
    actually produce one — two attachments of the same type."""
    for atts in (["client_fees.xlsx"], ["a.xlsx", "b.xlsx"], ["a.docx", "b.pdf"]):
        armed, auto = _resolve_armed_tools(scene.d, _body(atts, question="thanks!"))
        assert len(armed) == len(set(armed)), armed
        assert len(auto) == len(set(auto)), auto
    # The shape that explains the probe.
    armed, auto = _resolve_armed_tools(scene.d, _body(["a.docx"], question="thanks!"))
    assert armed == auto and armed + auto == armed * 2


def test_every_READ_verb_is_READONLY(client):
    """THE SAFETY ARGUMENT for arming the read half on file type alone, checked
    against the registry instead of believed. "The user attached it, so they
    want it read" is only a complete argument while every verb in the read half
    genuinely only reads — the moment one of them writes, arming it off a
    suffix is the same defect this section closed for the mutators."""
    registry = client.app.state.platform.registry
    from iron_jarvis.tools.base import Reversibility

    checked = 0
    for suffix in [*LIVE_VERBS, ".bmp", ".txt", ".png"]:
        for name in live_tool_names(suffix, kind="read"):
            tool = registry.get(name)
            assert tool is not None, f"{name} is armed but not registered"
            assert name not in _write_tier(), f"{name} is a WRITER in the read half"
            if name.startswith("image_") or name == "view_image":
                continue  # Pillow/vision reads; they carry the class default
            assert tool.reversibility is Reversibility.READONLY, (
                f"{name} arms on file type alone and is {tool.reversibility}"
            )
            checked += 1
    assert checked >= 5, "this guard stopped covering anything"


# ------------- 6b. the PROSE: what the line says when nothing armed ---------


@pytest.mark.parametrize("question, attachment", _READ_ONLY_MEASURED)
async def test_a_read_only_request_gets_the_honest_unarmed_clause(
    scene, question, attachment
):
    """THE PROSE DECISION, asserted.

    A verb named in the prompt but not armed cannot be called — strict providers
    constrain ``tool_use`` to the supplied list — so the only thing naming it can
    produce is a model telling the user the file is editable when nothing armed
    the editor. So the names go, and SILENCE DOES NOT REPLACE THEM: the clause
    states that a change is possible, that it is not armed now, and what unlocks
    it. Three assertions, in that order.
    """
    src = scene.uploads / attachment
    src.write_text("Fee schedule for 2025.", encoding="utf-8")
    if src.suffix == ".xlsx":
        _workbook(src)
    elif src.suffix == ".pdf":
        from iron_jarvis.documents import write_document

        write_document(src, "Engagement letter for the 2025 filing season.")

    _images, block = await _prepare_attachments(
        scene.d, _body([attachment], question=question),
        inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    # (a) NOT SILENT.
    assert _CHANGE_UNARMED.strip() in block, block
    # (b) NAMES NO CHANGE TOOL — the whole point.
    for verb in live_tool_names(src.suffix, kind="change"):
        assert verb not in block, (
            f"{verb} is named to the model on a turn that did not arm it:\n{block}"
        )
    # ...nor the clauses that only exist to qualify a change verb.
    assert _DERIVED_TARGET not in block and _CONFINED not in block, block
    # (c) THE READ HALF IS UNTOUCHED: path, marker, verbs.
    assert "(LIVE FILE" in block and str(src) in block, block
    for verb in live_tool_names(src.suffix, kind="read"):
        assert verb in block, block


def test_the_unarmed_clause_states_a_NECESSARY_condition_not_a_promise():
    """NEVER SILENTLY DEGRADE, applied to the clause's own wording.

    The gate reuses ``select_auto_tools``, and that scorer has MEASURED GAPS —
    sentences that plainly ask for a change and score no change verb.

    THE ORIGINAL FOUR WERE CLOSED IN v1.196.0 ROUND 4, and this docstring is
    updated rather than deleted exactly as it asked the next reader to do:

        "turn this into a pdf"            (.docx) -> CLOSED, convert_document
        "add a column for the tax rate"   (.xlsx) -> CLOSED, excel_edit
        "extract pages 3-5 into a new pdf" (.pdf) -> CLOSED, pdf_arrange+pdf_split
        "convert this to a word document" (.txt)  -> CLOSED, convert_document

    Gaps that remain OPEN, re-measured after that round, and used below so the
    ``else:`` branch — the half that actually pins the gap — stays exercised:

        "update the totals"                          (.xlsx) -> no change verb
        "pdf this"                                   (.docx) -> no change verb
        "change the client name in row 4 to Belmont LLC" (.xlsx) -> no change verb

    The last is the figure rule's stated limit: it requires a NUMERIC target, so
    a textual one reaches nothing. Closing these belongs to
    ``tools/autoselect.py`` and is welcome; what must NOT happen is the block
    promising around them. So the clause says change tools "arm ONLY when the
    request asks for a change" — a NECESSARY condition, which is exactly true —
    and never "ask and they will arm", which these sentences would make a lie.
    The word is asserted because it is the whole difference.
    """
    assert "only when" in _CHANGE_UNARMED
    assert "will arm" not in _CHANGE_UNARMED
    # And the clause names no tool at all — a promise the model cannot act on.
    assert "_" not in _CHANGE_UNARMED, _CHANGE_UNARMED

    for suffix, sentence in [
        # CLOSED in round 4 — these now take the `if wanted:` branch.
        (".docx", "turn this into a pdf"),
        (".xlsx", "add a column for the tax rate"),
        (".pdf", "extract pages 3-5 into a new pdf"),
        (".txt", "convert this to a word document"),
        # STILL OPEN — these keep the `else:` branch alive. Without at least one
        # of them this test would assert nothing about the unarmed clause, which
        # is the whole thing it exists for.
        (".xlsx", "update the totals"),
        (".docx", "pdf this"),
        (".xlsx", "change the client name in row 4 to Belmont LLC"),
    ]:
        # The gap is REAL (if one closes, update the docstring, don't delete it)
        # ...and the honest behaviour is the same either way: whatever the gate
        # decides, the line and the tool list decide it TOGETHER. That property
        # is asserted end-to-end in
        # `test_the_block_and_the_tool_list_name_the_same_tools`.
        wanted = change_verbs_wanted(suffix, sentence)
        line = live_file_line(f"C:/ws/f{suffix}", workspace="C:/ws",
                              change=wanted)
        if wanted:
            assert _CHANGE_UNARMED.strip() not in line, (sentence, wanted)
        else:
            assert _CHANGE_UNARMED.strip() in line, (
                f"{sentence!r} armed nothing and the block did not say so"
            )


async def test_THE_WAVE_CORE_PROPERTY_survives_the_consent_fix(scene):
    """THE ONE THING THIS ROUND MUST NOT BREAK, asserted end to end on a single
    read-only turn — the shape the consent gate touches hardest.

    A project-grounded chat with an attached ``.xlsx`` and a question that asks
    for no change must STILL: hand the model an ABSOLUTE path, arm a document
    tool, and have that tool RUN on that path. Fixing consent by quietly
    disarming the read half would be the 96-vs-0 defect restored, and every
    other assertion in this file would still pass.
    """
    body = _body(["client_fees.xlsx"], question="what do these fees add up to?")
    _images, block = await _prepare_attachments(
        scene.d, body, inline_budget=6000, rag_budget=2400, rag_k=6,
        project_root=str(scene.project),
    )
    # (a) the path is absolute and it is the real file
    paths = _paths_in(block)
    assert paths and Path(paths[0]).is_absolute() and Path(paths[0]) == scene.book

    # (b) a document tool is ARMED for this turn...
    armed, _auto = _resolve_armed_tools(scene.d, body)
    assert "excel_query" in armed, armed
    # ...and no mutator rode along.
    assert not (set(armed) & _write_tier()), armed

    # (c) ...and it RUNS on the path the model was given, answering the question
    # the flattened text cannot be trusted for.
    q = await ExcelQueryTool().execute(
        {"path": paths[0], "sheet": "Q1", "op": "sum", "column": "Fee"},
        _ctx(scene, scene.project),
    )
    assert q.ok is True, q.error
    assert "3500" in q.output.replace(",", "")


def test_the_six_slot_cap_is_not_newly_crowded(scene):
    """The other half of the mandated guard. Both lanes cap at 6, and the gate
    only ever REMOVES candidates from the attachment pass — so a plain read
    request must still arm the reading tools, with room to spare now rather than
    less."""
    from iron_jarvis.daemon.chat_turn import _MAX_ARMED_TOOLS

    for question, attachment, wanted in [
        ("read this pdf and summarize it", "report.pdf",
         ("read_document", "extract_pdf")),
        ("what do these fees add up to?", "client_fees.xlsx",
         ("read_document", "excel_profile", "excel_query", "excel_read")),
        ("thanks!", "summary.docx", ("read_document",)),
    ]:
        armed, _auto = _resolve_armed_tools(
            scene.d, _body([attachment], question=question)
        )
        assert len(armed) <= _MAX_ARMED_TOOLS, armed
        for tool in wanted:
            assert tool in armed, f"{question!r} + {attachment} armed {armed}"


def test_the_unarmed_clause_costs_about_what_the_clause_it_replaces_cost():
    """It rides EVERY read-only attachment turn, and both chat lanes budget
    history (CLAUDE.md: "History is BUDGETED, never sliced"), so the trade is
    metered rather than asserted to be small."""
    from iron_jarvis.context.budget import estimate_tokens

    cost = estimate_tokens(_CHANGE_UNARMED)
    replaced = {
        suffix: estimate_tokens(f" Change it: {verbs.change}.")
        for suffix, verbs in LIVE_VERBS.items()
    }
    # The floor is the workbook clause (no `_DERIVED_TARGET`), and the ceiling
    # is what this may cost over it. The first draft of the clause was 41
    # tokens against that 12 and this bound is what caught it.
    assert cost <= min(replaced.values()) + 20, (cost, replaced)
    # ...and on the DERIVED types it is a saving, because the target rule it
    # drops has nothing to qualify.
    assert cost < replaced[".pdf"] and cost < replaced[".docx"], (cost, replaced)


def test_live_file_line_change_arg_moves_ONLY_the_change_clause():
    """The argument must not disturb the path, the marker or the read verbs —
    the three things the wave's core repair consists of — and it must be
    per-verb, because the gate is.

    THE BOOL FORMS ARE ASSERTED because ``live_file_line`` never raises: before
    they were handled, ``change=True`` reached ``tuple(True)``, fell into the
    blanket ``except`` and returned an EMPTY STRING — the whole handoff gone,
    silently, from a type slip. That is this wave's own defect restored by
    accident, so the shape is pinned rather than left to a type hint.
    """
    on = live_file_line("C:/ws/book.xlsx", workspace="C:/ws", change=True)
    off = live_file_line("C:/ws/book.xlsx", workspace="C:/ws", change=False)
    one = live_file_line("C:/ws/book.xlsx", workspace="C:/ws", change=["excel_edit"])
    assert on and off and one, "a change argument emptied the whole handoff"
    for piece in ("LIVE FILE", "C:\\ws\\book.xlsx", "excel_profile",
                  "excel_query", "excel_read"):
        for line in (on, off, one):
            assert piece in line, (piece, line)
    assert "excel_edit" in on and "excel_apply_spec" in on
    assert "excel_edit" not in off and "excel_apply_spec" not in off
    # PER VERB: the armed one is named, its unarmed sibling is not.
    assert "excel_edit" in one and "excel_apply_spec" not in one, one
    assert _CHANGE_UNARMED.strip() in off and _CHANGE_UNARMED.strip() not in one
    # `None` = "this caller did not consult the gate" = the full clause.
    assert live_file_line("C:/ws/book.xlsx", workspace="C:/ws") == on
    # A type with nothing true to say about changing keeps saying nothing.
    assert live_file_line("nope.zip", change=False).endswith("nope.zip.)")


def test_the_change_phrases_line_up_with_the_change_tools():
    """The per-verb prose only works while the two tuples are parallel: a phrase
    with no tool would be un-renderable, and a tool with no phrase would arm
    without ever being named."""
    from iron_jarvis.documents.attachment_rag import change_prose

    entries = list(LIVE_VERBS.values()) + [live_verbs_for(".bmp"), live_verbs_for(".txt")]
    for verbs in entries:
        assert verbs is not None
        assert len(verbs.change_phrases) == len(verbs.change_tools), verbs
        for phrase, tool in zip(verbs.change_phrases, verbs.change_tools):
            assert phrase.startswith(tool), (phrase, tool)
        # Rendering the whole set reproduces the table's own `change` string...
        assert change_prose(verbs, verbs.change_tools) == verbs.change
        # ...and rendering none of it says nothing at all, note included: the
        # target rule has no addressee when no verb is armed.
        assert change_prose(verbs, ()) == ""


def test_neither_lane_reimplemented_the_handoff():
    """If the live-file line ever grows a second home in either lane, that is
    the duplication CLAUDE.md warns about — and the shape that let the PDF
    guidance ship to one lane only for a whole wave (v1.167.0)."""
    turn = (_SRC / "chat_turn.py").read_text(encoding="utf-8")
    stream = (_SRC / "routes" / "chat.py").read_text(encoding="utf-8")
    assert turn.count("live_file_line(") >= 2, "chat_turn stopped calling it"
    assert "live_file_line" not in stream, (
        "the streaming lane grew its own copy instead of inheriting it"
    )
    for src in (turn, stream):
        assert "project_root=" in src, "a lane stopped telling the preparer where"
    # Same property for the ARMING half: the streaming lane imports
    # `_resolve_armed_tools` from chat_turn rather than scoring its own set, so
    # the attachment-type arming reaches it for free. A lane that grew its own
    # copy is the drift that shipped PDF guidance to one lane only (v1.167.0).
    assert "live_tool_names" in turn and "live_tool_names" not in stream
    assert "_resolve_armed_tools" in stream
    # ...and the CONSENT GATE has exactly one home too. Both the arming pass and
    # the prose pass live in chat_turn and call `change_verbs_wanted`; a lane
    # that grew its own answer to "did the user ask for a change" is the second
    # detector this round exists to avoid.
    assert turn.count("change_verbs_wanted") >= 3, (
        "the arming pass and the prose pass must both consult the gate"
    )
    assert "change_verbs_wanted" not in stream and "select_auto_tools" not in stream


@pytest.mark.parametrize(("picks", "files"), [
    ([], ["a.xlsx", "b.pdf", "c.png"]),
    (["read_file"], ["a.xlsx", "b.pdf", "c.jpg"]),
    (["read_file", "grep"], ["a.xlsx", "b.pdf", "c.png"]),
    (["read_file", "grep", "list_files"], ["a.xlsx", "b.pdf", "c.png", "d.docx"]),
])
def test_the_cap_holds_across_SEVERAL_attachments(scene, picks, files):
    """THE SCENE THE OTHER CAP TESTS COULD NOT SEE.

    `_resolve_armed_tools` breaks out of the attachment-arming loop at
    `_MAX_ARMED_TOOLS`. Removing that `break` left 338 tests green, because
    every existing cap scene has ONE attachment and a sentence strong enough to
    fill the cap in the earlier pass — so either the attachment pass is skipped
    or the break never binds. Measured with it removed: a weak sentence plus
    three or four attachments of DIFFERENT types arms 7, 8, 9 and 10 tools.

    That list is the turn's `session_allow`. Over-filling it is not a tidiness
    problem: it is granting tools the user never armed, with no approval card.

    The sentence is deliberately contentless — a strong one fills the cap before
    the attachment pass runs and hides the very thing under test.
    """
    from iron_jarvis.daemon.chat_turn import _MAX_ARMED_TOOLS

    body = _body(files, question="thanks!")
    body.tools = list(picks)
    armed, _auto = _resolve_armed_tools(scene.d, body)

    assert len(armed) <= _MAX_ARMED_TOOLS, (
        f"{len(armed)} tools armed for {len(files)} attachments with "
        f"{len(picks)} explicit pick(s) — the cap is {_MAX_ARMED_TOOLS}, and "
        f"this list becomes session_allow: {armed}"
    )
    # NOT VACUOUS: the attachment pass really did run and really did arm.
    assert armed, "nothing armed at all — the scene no longer exercises the cap"
    # The user's own picks are never displaced by attachment-derived tools.
    for p in picks:
        assert p in armed, f"explicit pick {p!r} was crowded out: {armed}"


@pytest.mark.parametrize("route", ["/chat", "/chat/stream"])
def test_the_scorer_is_hopped_off_the_loop_in_both_lanes(client, monkeypatch, route):
    """THE BEHAVIOURAL PIN for the arming offload — driven through the real route.

    `_resolve_armed_tools` is CPU-bound regex scoring. On the daemon's single
    loop a whitespace-heavy paste measured ~200 ms (and, before the possessive
    fix, seventeen seconds), which does not present as a slow reply — it
    presents as "Daemon offline".

    THE ASSERTION IS `asyncio.get_running_loop()`, not a thread NAME: TestClient
    runs the event loop on a non-main thread, so a name check would pass whether
    or not the hop existed — which is exactly how two earlier attempts at this
    test managed to be vacuous. A worker thread has no running loop and raises
    RuntimeError; the loop thread does not.
    """
    import asyncio as _asyncio

    from iron_jarvis.daemon import chat_turn as _ct

    saw_running_loop: list[bool] = []
    real = _ct._resolve_armed_tools

    # *args/**kw so the spy survives additive signature growth — v1.202.0
    # added `max_tools` to the arming pass and a fixed (d, body) spy raised
    # TypeError inside the lanes' except-guards, which read as "never reached
    # the arming pass" instead of naming the real breakage.
    def spy(d, body, *args, **kw):
        try:
            _asyncio.get_running_loop()
            saw_running_loop.append(True)
        except RuntimeError:
            saw_running_loop.append(False)
        return real(d, body, *args, **kw)

    # PATCH BOTH BINDINGS. `routes/chat.py` does `from ..chat_turn import
    # _resolve_armed_tools` at module level, so the streaming lane holds its own
    # reference and patching `chat_turn` alone never reaches it — the first cut
    # of this test failed with "never reached the arming pass" for exactly that
    # reason, which is itself a small demonstration of why a test has to drive
    # the real route rather than the function it believes the route calls.
    from iron_jarvis.daemon.routes import chat as _routes_chat

    monkeypatch.setattr(_ct, "_resolve_armed_tools", spy)
    monkeypatch.setattr(_routes_chat, "_resolve_armed_tools", spy)
    client.post(route, json={"messages": [{"role": "user", "content": "thanks!"}],
                             "auto_tools": True})

    assert saw_running_loop, f"{route} never reached the arming pass"
    assert not any(saw_running_loop), (
        f"{route} ran the arming pass ON the event loop "
        f"(get_running_loop succeeded) — the asyncio.to_thread hop is gone"
    )
