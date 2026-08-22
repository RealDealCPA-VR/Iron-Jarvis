"""Arming gaps in the document module (v1.196.0).

MEASURED, not guessed. The event ledger of the user's live install
(``%APPDATA%/Iron Jarvis/.ironjarvis/ironjarvis.db``, ``eventrecord`` where
``type='tool.executed'``, 482 calls between 2026-07-06 and 2026-08-20) says the
documents module is load-bearing — ``read_document`` alone is the second
most-used tool in the app — and that 12 of its 18 tools have NEVER RUN ONCE.
Three of those twelve could not be reached by AUTOMATIC arming at all, in
EITHER lane: they were absent from :data:`AUTO_SAFE_TOOLS` and no scoring rule
in ``tools/autoselect.py`` ever awarded them a point.

    batch_documents      not in AUTO_SAFE_TOOLS, no scoring rule
    list_folder          not in AUTO_SAFE_TOOLS, no scoring rule
    excel_apply_spec     not in AUTO_SAFE_TOOLS, no scoring rule

Both lanes, because ``agents/runtime.arm_for_task`` calls the SAME
``select_auto_tools``: a tool missing from that table exists only if a human
picks it out of chat's "+" menu.

This file pins the outcome of auditing all three:

  * ``list_folder`` is ARMED (§1-§3). It is the only listing tool that can see
    the user's real disk, and the sentences that ask for one armed
    ``list_files`` instead — which resolves through ``safe_path(ctx.workspace,
    ...)`` and, in chat, can only ever see ``home/uploads``.
  * ``batch_documents`` is DELIBERATELY OUT (§4), and the reason is asserted to
    be written down rather than merely believed.
  * ``excel_apply_spec`` is ARMED (§7), as a PAIR with
    ``agents/runtime._WRITE_TIER``. §4's forward guard is what made that safe to
    land: the moment the safe-set half exists without the tier half, a test goes
    red instead of a read-only reviewer agent quietly gaining a workbook writer.
    §7 asserts BOTH halves, the sentences that reach it, and the invariant that
    keeps the addition from widening consent — it can only ever arm on a
    sentence the spreadsheet rule already fires on, i.e. one where the
    equally-mutating ``excel_edit`` was already eligible.
  * §5 is the crowd-out guard. The cap is 6 in both lanes, so a new aggressive
    rule can push ``read_document``/``file_search`` off a plain "read this pdf"
    — a regression far worse than the gap being closed.
  * §6 is the OTHER crowd-out guard, and the one that caught the real
    regression. §5's sentences carry no plural demonstrative, so they exercise
    the folder-listing half of this unit and never the anaphor half. §6 pins,
    sentence by sentence, that the tool the user's VERB names keeps the #1 slot
    on "<verb> ... these <documents>" — the class that regressed to
    ``file_search`` when the anaphor was written as a branch of the folder rule
    and inherited its ``file_search: 8``.
  * §9 (round 4) closes the SCORER GAP the round-3 consent gate exposed. Once
    change verbs stopped arming on attachment TYPE and started asking
    ``select_auto_tools`` whether the request wanted them, every sentence the
    scorer could not read became a sentence the user had to rephrase. Seven were
    measured; §9 pins each of them through the real lane, pins the four
    read-only turns that must STILL arm no mutator, and keeps an honest list of
    the phrasings that are still not reached.
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from iron_jarvis.agents.runtime import _ROSTER_WRITERS, _WRITE_TIER, arm_for_task
from iron_jarvis.daemon.chat_turn import _resolve_armed_tools
from iron_jarvis.documents.attachment_rag import change_verbs_wanted, live_tool_names
from iron_jarvis.tools import autoselect as _auto
from iron_jarvis.tools.base import Reversibility, Tool
from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS, select_auto_tools

#: The read-only REVIEWER roster, copied from tests/test_agent_auto_arm_v1178.py
#: (the file that owns the tier gate). It is the honest subject for the agent
#: lane: the narrowest built-in roster, so anything in its armed list demonstrably
#: came from the task text and not from the definition.
_REVIEWER = ["read_file", "list_files", "grep", "read_document", "extract_pdf",
             "memory_search", "skill_search", "recall_lessons", "recall",
             "ltm_search", "blackboard_post", "blackboard_read", "message_agent"]

#: The anaphor rule's regex, identified by the fragment that is unique to it.
#: NOT the bare ``"these|those"``: the v1.173.0 memory rule spells
#: ``look\s+(?:it|that|this|them|these|those)\s+up``, so that substring matches
#: two rules and a test keyed on it silently asserts about the wrong one.
_ANAPHOR_SIG = r"\b(?:these|those)\s+(?:\d+\s+)?"


def _deps(platform):
    """The ``d`` object ``_resolve_armed_tools`` actually reads: it touches
    ``d.platform.registry`` and nothing else."""
    return SimpleNamespace(platform=platform)


def _body(text: str, attachments: list[str] | None = None, *, auto: bool = True):
    """A chat request as the arming code reads it. ``auto_tools`` is the user's
    Auto toggle — the standing consent the whole selector rides on — so it is a
    named argument here rather than a constant."""
    return SimpleNamespace(
        tools=[], skill="", auto_tools=auto,
        messages=[SimpleNamespace(role="user", content=text)],
        attachments=list(attachments or []),
    )


def _flat(text: str) -> str:
    """Source comments and docstrings wrap, so a phrase that reads as one line
    is not one line in the file. Strip comment markers and collapse whitespace
    before asserting a claim is present — otherwise the test is really asserting
    where the author happened to break the line."""
    return " ".join(text.replace("#:", " ").replace("#", " ").split())


# =============================================================================
# 1. list_folder is armable at all
# =============================================================================
def test_list_folder_joined_the_safe_set():
    """The gap itself. Before v1.196.0 no message could arm it in either lane."""
    assert "list_folder" in AUTO_SAFE_TOOLS


def test_list_folder_is_read_only_which_is_the_whole_safety_argument(platform):
    """It adds VOCABULARY, not TIER. ``ListFolderTool`` is
    ``Reversibility.READONLY`` and gated by the same ``fs_read_ok`` policy
    ``read_document`` uses — so a listing is strictly less disclosure than the
    file read that was already auto-armable. If that ever changes, this arming
    decision has to be re-argued."""
    tool = platform.registry.get("list_folder")
    assert tool is not None, "the install must actually serve what we arm"
    assert tool.reversibility is Reversibility.READONLY
    assert "list_folder" not in _WRITE_TIER


# =============================================================================
# 2. The sentences a real user types
# =============================================================================
#: Each one was run against the pre-fix selector and produced the list on the
#: right — every one of them missing the only tool that can open a real folder.
_FOLDER_SENTENCES = [
    # armed ['file_search', 'list_files', 'read_document'] before
    "go through everything in that folder",
    "what's in this folder",
    # armed nothing folder-shaped before
    "check my Downloads folder for the K-1",
    "list the files in the client folder",
    "show me the contents of that directory",
]


def test_the_folder_sentences_reach_list_folder():
    for msg in _FOLDER_SENTENCES:
        picked = select_auto_tools(msg)
        assert "list_folder" in picked, f"{msg!r} armed {picked}"


def test_the_folder_ask_LEADS_with_list_folder():
    """Not merely present — first. ``list_files`` is confined to the chat
    workspace (``home/uploads``), so on a bare "what's in this folder" it is the
    tool that structurally cannot answer, and a model handed both will take the
    one at the top of its list."""
    for msg in ("what's in this folder", "go through everything in that folder"):
        assert select_auto_tools(msg, cap=1) == ["list_folder"], msg


def test_the_bulk_document_sentence_armed_NOTHING_before():
    """The user's own workflow sentence. MEASURED against the pre-fix selector:
    ``select_auto_tools("process these 15 client documents and give me a
    summary")`` returned ``[]`` — not a reduced list, an EMPTY one. Two causes,
    both in the determiner: the folder rule's list held no plural demonstrative
    (``these``/``those``), and the noun had to sit directly against it, so the
    count and the adjective ("15 client") broke the match on their own.

    NOTE WHAT IS DELIBERATELY *NOT* ASSERTED. An earlier cut of this test also
    required ``"list_folder" in picked``, and that assertion was PINNING THE
    DEFECT: it passed only because the demonstrative was written as a branch of
    the FOLDER rule and so inherited that rule's weight map. This sentence names
    no folder — there is no path a listing tool could resolve — and the same
    inheritance handed it ``file_search: 8``, which stole the #1 slot from the
    intent tool across the whole class of sentences §6 now pins. A reader and a
    searcher are the honest arm here."""
    picked = select_auto_tools("process these 15 client documents and give me a summary")
    assert picked, "the headline bulk-document sentence must arm something"
    assert "read_document" in picked
    assert "file_search" in picked
    assert "list_folder" not in picked, (
        f"an anaphor names no directory, so a listing tool armed on it has no "
        f"path the model can resolve; armed {picked}"
    )
    assert "list_files" not in picked, f"armed {picked}"


def test_pointing_at_a_folder_without_a_LISTING_VERB_still_reaches_it():
    """The sentences that reach the folder rule through the DETERMINER alone,
    and the test that found a no-op hiding behind a confident comment.

    v1.196.0's first cut added ``that`` to a second alternative in this rule
    (``in (the|my|this) folder``) and explained that "that folder" had matched
    nothing before. Mutating that word back left every behavioural assertion in
    this file green and moved only a test keyed on the PATTERN STRING — the
    signature of an edit that does nothing. It did nothing: the alternative it
    was added to could only ever match ``in <det> folder``, whose own
    ``<det> folder`` substring the determiner branch below already matched on
    all four determiners. That dead alternative is now gone (measured
    behaviour-neutral over 1,260 generated ``in <det> <noun>`` sentences), and
    THIS is the coverage it never had: none of these three carries a listing
    verb, so they reach ``list_folder`` only through ``that folder`` in the
    determiner branch — and the first would otherwise arm the
    workspace-confined ``list_files`` and no tool that can open a real one."""
    for msg in ("find the invoice in that folder",
                "the K-1 is in that folder somewhere",
                "put the output in that folder"):
        picked = select_auto_tools(msg)
        assert "list_folder" in picked, f"{msg!r} armed {picked}"


def test_the_plural_demonstrative_branch_does_not_need_the_count():
    for msg in ("summarize these documents",
                "those returns need reviewing",
                "pull the totals out of these three client invoices"):
        assert "read_document" in select_auto_tools(msg), msg


def test_a_typed_folder_path_arms_the_listing_and_a_file_path_does_not():
    """A path whose last segment carries no extension is a folder. The
    distinction matters: on a real FILE path the reader must still lead, or this
    rule has traded the app's most-used tool for a directory listing."""
    folder = select_auto_tools(r"what is in C:\Users\VR\clients\2024")
    assert "list_folder" in folder
    file_path = select_auto_tools(r"read C:\Users\VR\clients\report.pdf")
    assert file_path[0] == "read_document"
    assert "list_folder" not in file_path


def test_the_new_rules_stay_inside_the_safe_set():
    """Mirrors tests/test_brain_reach_v1173.py's discipline: a rule naming
    shell/mcp/pixio would be silently filtered, so the assertion is on the
    RETURNED list for every sentence this unit added."""
    for msg in [*_FOLDER_SENTENCES,
                "process these 15 client documents and give me a summary",
                r"what is in C:\Users\VR\clients\2024"]:
        assert set(select_auto_tools(msg)) <= AUTO_SAFE_TOOLS, msg


def test_the_folder_rules_are_PRECISE_about_the_noun():
    """Every alternative needs a folder-shaped noun, so the everyday senses of
    "list" and "what's in" must not fire. A false arm here costs schema context
    and gives a local model another wrong door (the v1.174.0 lesson)."""
    for msg in ("list the top five risks",
                "hey, how are you today?",
                "what's in the sandwich"):
        assert "list_folder" not in select_auto_tools(msg), msg


# =============================================================================
# 3. The agent lane gets it too — arm_for_task calls the SAME selector
# =============================================================================
def test_a_read_only_agent_run_can_now_see_a_real_folder(platform):
    """The gap was never chat-only. ``agents/runtime.arm_for_task`` reads the
    task through ``select_auto_tools``, so an agent asked to work through a
    folder had the same blind spot — and unlike chat it has no "+" menu to fall
    back on."""
    task = "go through everything in that folder and summarize each document"
    armed = arm_for_task(platform, task, list(_REVIEWER))
    assert armed[: len(_REVIEWER)] == _REVIEWER, "the roster rides unchanged"
    added = armed[len(_REVIEWER) :]
    assert "list_folder" in added
    # Vocabulary, not tier: the reviewer is read-only on purpose.
    assert not (set(armed) & _WRITE_TIER), f"{task!r} handed the reviewer a writer"


# =============================================================================
# 4. The one that was deliberately LEFT OUT
# =============================================================================
def test_batch_documents_is_still_out():
    """Pinned so the omission is a decision with a test behind it, not drift.
    Removing this name is a real change: ``batch_documents`` fans out ONE model
    call per document (default 25, cap 100) and keeps an IRREVERSIBLE default,
    so it would be the first member of the safe set with no honest undo — which
    is why "it writes" was never the exclusion rule in force here (``excel_edit``
    writes, and so does ``excel_apply_spec`` as of §7)."""
    assert "batch_documents" not in AUTO_SAFE_TOOLS


def test_the_omission_is_DOCUMENTED_where_the_next_reader_will_look():
    """An undocumented omission is indistinguishable from the oversight this
    unit was formed to fix — that is exactly how ``list_folder`` went missing.
    The reasoning has to live next to the set, not in a review comment."""
    src = inspect.getsource(_auto)
    head, _, tail = src.partition("DELIBERATE OMISSIONS")
    assert tail, "the module must carry a DELIBERATE OMISSIONS note"
    # THE WHOLE BLOCK, bounded by its own STRUCTURE rather than by a character
    # count. An earlier cut sliced `tail[:4000]`; the block is ~1,900 chars
    # today so it worked, but a resurrected `excel_apply_spec` entry appended
    # past the slice would have passed silently — a guard whose reach depends on
    # how much someone else wrote above it. The note is a run of `#:`/`#`
    # comment lines and ends at the first line of code (`_DOC_EXT_RX = ...`), so
    # that is the terminator, and no length can outrun it.
    note_lines: list[str] = []
    for i, line in enumerate(tail.splitlines()):
        # i == 0 is the REMAINDER of the heading's own line, which `partition`
        # cut mid-comment and which therefore carries no `#` of its own.
        if i and line.strip() and not line.lstrip().startswith("#"):
            break
        note_lines.append(line)
    note = "\n".join(note_lines)
    assert len(note_lines) > 5, f"the omissions note collapsed to {note!r}"
    assert "_DOC_EXT_RX" not in note, "the terminator did not hold"
    assert "batch_documents" in note
    # batch_documents: the cost + irreversibility argument.
    assert "IRREVERSIB" in note.upper()
    # And the note must not still be ARGUING FOR an omission that has since
    # landed: `excel_apply_spec` is in the set now (§7), so a reader finding it
    # listed under "DELIBERATE OMISSIONS" would draw the opposite conclusion
    # from the truth. Where it is mentioned, it is mentioned as having landed.
    omission_body = note.partition("* ``excel_apply_spec``")[2]
    assert not omission_body, (
        "excel_apply_spec still has an OMISSION entry but is in AUTO_SAFE_TOOLS"
    )


def test_the_safety_docstring_does_not_overclaim_CONFINEMENT():
    """The docstring is the curation rule the next reader will apply, so a false
    sentence in it admits the wrong tool later. It has now been wrong TWICE in
    opposite directions and both are pinned here:

      * flatly "their WRITES are fs-policy-confined to the chat workspace" —
        false of the READERS (``read_document`` may target any local path,
        ``list_folder`` lists any real folder), and it would have argued
        ``list_folder`` out of the set for the wrong reason;
      * then widened to the WHOLE safe set — false of ``ltm_append``
        (``ltm/tools.py``), which appends to an Obsidian vault or to Notion,
        outside any workspace, and whose own note in the set says so. It is
        admissible because it is APPEND-ONLY, never because it is confined.

    The claim must therefore be SCOPED to the tools it holds for, and the
    exception named where someone would otherwise "fix" it by widening again."""
    doc = _flat(_auto.__doc__ or "")
    assert "ltm_append" in doc, (
        "the confinement sentence must name the member it is NOT true of"
    )
    assert "APPEND-ONLY" in doc.upper()
    # The blanket form must not come back: no confinement claim about the set
    # as a whole ("their writes"), only about the file + document tools.
    assert "Their WRITES are" not in doc
    assert "FILE + DOCUMENT tools' WRITES are fs-policy-confined" in doc


def test_every_REVERSIBLE_auto_safe_tool_is_gated_by_the_write_tier(platform):
    """THE FORWARD GUARD — the test that made landing ``excel_apply_spec`` safe,
    and the one that keeps the next such addition safe.

    ``agents/runtime._WRITE_TIER`` is defined as "the ``AUTO_SAFE_TOOLS``
    members that CREATE OR MODIFY content" and is what stops a READ-ONLY agent
    definition (REVIEWER, SUPERVISOR) from gaining a writer off its task text.
    Its membership is maintained BY HAND in another module, and every assertion
    in ``tests/test_agent_auto_arm_v1178.py`` is phrased as "no member of
    ``_WRITE_TIER``" — so a tool added to the safe set but forgotten there
    leaves that whole file green while quietly becoming armable onto an agent
    the user chose BECAUSE it only reads.

    ``Reversibility.REVERSIBLE`` is the honest signal: it means the tool
    captures an undo pre-image, which it only does because it modifies content
    that already exists. (IRREVERSIBLE is the class default and says nothing —
    ``web_search`` and ``file_search`` carry it.)

    MUTATION-PROVEN, both directions, on the change that shipped in this file:
    deleting ``"excel_apply_spec"`` from ``AUTO_SAFE_TOOLS`` alone leaves this
    green (nothing ungated), while deleting it from ``_WRITE_TIER`` alone turns
    it RED naming exactly that tool. That asymmetry is the point — the safe-set
    half is the one that can be added carelessly.
    """
    ungated = sorted(
        name
        for name in AUTO_SAFE_TOOLS
        if (tool := platform.registry.get(name)) is not None
        and tool.reversibility is Reversibility.REVERSIBLE
        and name not in _WRITE_TIER
    )
    assert ungated == [], (
        f"{ungated} modify existing content and are auto-armable, but "
        f"agents/runtime._WRITE_TIER does not gate them — a read-only agent "
        f"definition can now gain them from its task text"
    )


# =============================================================================
# 5. CROWD-OUT GUARD — the regression that would be worse than the gap
# =============================================================================
def test_a_plain_read_request_still_arms_the_READERS(platform):
    """Both lanes cap at 6. ``read_document`` is the second most-used tool in
    the whole app (96 of 482 recorded calls); a folder rule that pushed it off a
    plain "read this pdf" would trade the app's busiest path for a directory
    listing."""
    for msg in ("read this pdf and summarize it",
                "summarize the attached pdf report",
                "review the contract for the termination clause"):
        picked = select_auto_tools(msg)
        assert picked[0] == "read_document", f"{msg!r} armed {picked}"
        assert "file_search" in picked, f"{msg!r} armed {picked}"
        # and nothing folder-shaped elbowed in
        assert "list_folder" not in picked, f"{msg!r} armed {picked}"

    added = arm_for_task(platform, "read this pdf and summarize it", list(_REVIEWER))
    assert added[: len(_REVIEWER)] == _REVIEWER


# =============================================================================
# 6. THE INTENT TOOL KEEPS THE #1 SLOT — the guard §5 could not have caught
# =============================================================================
#: Every one of the three sentences in §5 above is free of a plural
#: demonstrative, so the anaphor rule never fires inside that guard: it exercises
#: the FOLDER-LISTING half of this unit (which was correctly weighted) and never
#: the anaphor half (which was not). This section is the missing half.
#:
#: MEASURED on the real selector when the anaphor was still a branch of the
#: folder rule and inherited its ``file_search: 8``. Left column = the tool that
#: led before this unit and must lead again; the whole class regressed to
#: ``file_search`` because ties break ALPHABETICALLY and ``file_search`` sorts
#: early. The redaction row is the one that matters most: it is the app's
#: highest-consequence path, and it was offering a file search first.
_INTENT_LEADS = [
    ("merge these pdfs into one file", "pdf_arrange"),
    ("split these documents into separate pages", "pdf_arrange"),
    ("redact the pii in these returns", "redact_scan"),
    ("create a summary memo of these documents", "write_document"),
    ("make a spreadsheet from these invoices", "write_document"),
    ("convert these documents to pdf", "convert_document"),
    ("extract the pages from these scanned pdfs", "read_document"),
    ("reconcile these statements against the trial balance", "excel_formula_check"),
    ("write a python script to rename these files", "write_file"),
    ("batch process these documents", "code_search"),
    ("automate this for these 40 invoices", "code_search"),
]


def test_the_anaphor_never_takes_the_lead_from_the_intent_tool():
    """The PRIMARY defect of the first cut, pinned sentence by sentence.

    "these documents" is an anaphor — it says documents are in play, not what to
    do with them. The verb the user typed says that, and this rule must never
    outrank it. Violating this breaks the calibration the module annotates as a
    real shipped failure ("the creator also OUTRANKS the spreadsheet analyzers
    (10 > 9) when creation intent is present") and the v1.174.0 doctrine quoted
    in ``agents/runtime.arm_for_task`` that the §2 lead test relies on."""
    for msg, want in _INTENT_LEADS:
        picked = select_auto_tools(msg)
        assert picked[0] == want, f"{msg!r} armed {picked}, wanted {want} to lead"


def test_the_anaphor_arms_no_listing_tool_at_all():
    """The structural half of the same defect. Inheriting the folder rule's map
    also armed ``list_folder`` and ``list_files`` on sentences naming no folder,
    which filled the 6-cap with THREE folder/search tools and pushed real intent
    tools off it — ``excel_profile`` off the reconcile sentence, ``code_load``
    off the rename sentence, both restored below."""
    for msg, _ in _INTENT_LEADS:
        picked = select_auto_tools(msg)
        assert "list_folder" not in picked, f"{msg!r} armed {picked}"
        assert "list_files" not in picked, f"{msg!r} armed {picked}"
    assert "excel_profile" in select_auto_tools(
        "reconcile these statements against the trial balance")
    assert "code_load" in select_auto_tools(
        "write a python script to rename these files")
    assert "code_load" in select_auto_tools("batch process these documents")


def test_the_ONE_measured_slot_cost_is_the_one_we_chose(platform):
    """NEVER SILENTLY DEGRADE, applied to the cap. Swept over 31 sentences, the
    anaphor rule costs a tool its slot exactly once, and this pins WHICH — so
    the trade stays a decision instead of becoming drift.

    "make a spreadsheet from these invoices" was ALREADY at the 6-cap before this
    unit, and ``read_document`` (6 from the doc-noun rule, +1 from the anaphor)
    displaces ``excel_edit`` (6) as the marginal entry. That is the right trade:
    "make" means there is no existing workbook, and the creation rule's own
    comment records that ``excel_edit`` "refuses without an existing workbook",
    while the invoices genuinely have to be read to build the sheet. Every tool
    that can actually answer keeps its slot."""
    picked = select_auto_tools("make a spreadsheet from these invoices")
    assert len(picked) == 6
    assert picked[0] == "write_document", picked
    for keeper in ("excel_query", "excel_profile", "excel_read", "read_document"):
        assert keeper in picked, f"{keeper} lost its slot: {picked}"
    # The displaced tool is the workbook MUTATOR, on a sentence that creates one.
    assert "excel_edit" not in picked
    assert platform.registry.get("excel_edit") is not None, (
        "if excel_edit ever stops existing this trade needs re-stating, not "
        "silently passing"
    )


def test_the_anaphor_is_its_OWN_rule_with_its_OWN_weights():
    """The fix is structural, not a number tweak — assert the shape so the two
    cannot be re-merged. The anaphor rule must be a separate ``_RULES`` entry,
    and its weights must stay SMALL: the module's ties break alphabetically, so
    a weight that merely TIES an intent tool still steals its slot whenever this
    rule's tool sorts earlier (``read_document`` < ``redact_scan``,
    ``read_document`` < ``write_document``). Measured: a ``read_document`` bump
    of 2 flips two leads, 4 flips three, and the 7/3 pair flips seven including
    ``redact_scan``."""
    anaphor = [w for rx, w in _auto._RULES if _ANAPHOR_SIG in rx.pattern]
    assert len(anaphor) == 1, "the anaphor must be exactly one rule of its own"
    weights = anaphor[0]
    assert set(weights) == {"file_search", "read_document"}, weights
    assert max(weights.values()) <= 2, (
        f"{weights} — see the calibration sweep in this rule's comment before "
        f"raising these; every larger pair measurably flips a lead"
    )
    # And the folder rule must no longer carry the demonstrative — this is the
    # re-merge the split exists to prevent, so assert on the FOLDER rule's own
    # pattern rather than trusting the count above. Keyed on the DETERMINER
    # branch: the `in <det> folder` alternative this used to key on was proven a
    # no-op and removed (see the rule's comment), and a key that no longer
    # exists silently selects nothing.
    folder = [rx for rx, _ in _auto._RULES
              if r"(?:my|the|this|that|our)\s+(?:files?|folders?" in rx.pattern]
    assert len(folder) == 1, "the folder rule must still be exactly one rule"
    assert _ANAPHOR_SIG not in folder[0].pattern, (
        "the anaphor is back inside the folder rule and has re-inherited its "
        "file_search: 8 — see §6"
    )


def test_the_anaphor_false_arm_class_is_STATED_not_understated():
    """NEVER SILENTLY DEGRADE (CLAUDE.md) applies to a rule's own known
    over-match too. The two-word filler bound keeps out long drifting sentences,
    but the ZERO-filler base case still fires on sentences with no file in them.
    That cost is real and must be written down where the next reader tunes the
    weights — the first cut's comment argued the filler bound was what stopped
    drift, which is not where the drift is."""
    for msg in ("email these forms to the client",
                "those statements are wrong",
                "these forms of energy are interesting"):
        picked = select_auto_tools(msg)
        # It DOES fire — that is the honest, documented cost...
        assert picked == ["file_search", "read_document"], f"{msg!r} armed {picked}"
        # ...and it is bounded: two READ-ONLY tools, never a writer, never a
        # lister with no path. This is what the weight of 1 buys.
        assert not (set(picked) & _WRITE_TIER), f"{msg!r} armed {picked}"

    rule = _flat(inspect.getsource(_auto).partition("the ANAPHOR rule")[2][:5000])
    assert rule, "the anaphor rule must be findable by its own heading"
    assert "FALSE ARMS" in rule, "the over-match class must be stated in-source"
    assert "these forms of energy" in rule, "state it with the measured example"
    assert "ZERO-filler" in rule, (
        "the first cut blamed the filler bound; say where the drift actually is"
    )


def test_a_busy_folder_sentence_still_carries_the_reader(platform):
    """The hard case: folder language AND document language in one sentence,
    with the cap biting. ``list_folder`` may lead, but the tool that opens the
    documents must survive the cut — an armed listing with no reader is a turn
    that can see the files and not read them."""
    msg = ("go through everything in that folder, read the pdf invoices and "
           "tell me the totals")
    picked = select_auto_tools(msg)
    assert len(picked) <= 6
    assert "list_folder" in picked
    assert "read_document" in picked
    assert "file_search" in picked


def test_the_cap_and_exclude_contracts_are_unchanged():
    """Regression fence around the selector's own contract while its table grew."""
    msg = "go through everything in that folder and summarize these 15 documents"
    assert len(select_auto_tools(msg, cap=2)) == 2
    assert select_auto_tools(msg, cap=0) == []
    assert "list_folder" not in select_auto_tools(msg, exclude={"list_folder"})
    assert select_auto_tools("hey, how are you today?") == []


# =============================================================================
# 7. excel_apply_spec — THE PAIRED LANDING
# =============================================================================
#: The sentences a person actually types when they want a sheet's structure
#: reproduced. NONE of them contains the string ``excel_apply_spec``: a rule
#: that fires on the dictionary key is a rule that fires on nothing, and this
#: list is the reason the rule reads both word orders and looks for the workbook
#: noun ANYWHERE in the message rather than beside the verb.
_APPLY_SPEC_SENTENCES = [
    "apply this layout to the workbook",
    "apply the firm's standard layout to this spreadsheet",
    # the workbook noun sits BEFORE the verb — a forward-only lookahead
    # anchored at the verb misses this whole (commonest) phrasing
    "review the workbook and apply the firm's standard layout to it",
    "reproduce the sheet's structure in the new workbook",
    "replicate the formatting from last year's workbook",
    "use the same layout as last year's spreadsheet",
    "recreate this template in a new xlsx",
    "apply the same number formats to the cells",
    "match the layout of the model workbook",
    # verb AFTER the structure noun — the tool's own description's phrasing
    # ("Feed the spec to excel_apply_spec to reproduce the sheet elsewhere")
    "take the spec from last year and apply it to this worksheet",
]


def test_excel_apply_spec_joined_the_safe_set():
    """The gap. Before this, no message in EITHER lane could arm it: absent from
    the safe set AND unscored by every rule."""
    assert "excel_apply_spec" in AUTO_SAFE_TOOLS


def test_the_two_halves_LANDED_TOGETHER(platform):
    """The paired change, asserted from both sides.

    ``_WRITE_TIER`` gates it for agent runs; ``_ROSTER_WRITERS`` (the union)
    still recognises a definition that carries it as a writing agent, exactly as
    before the move — that identity is what keeps the gate a no-op for the six
    built-in types that do the work."""
    assert "excel_apply_spec" in AUTO_SAFE_TOOLS
    assert "excel_apply_spec" in _WRITE_TIER
    assert "excel_apply_spec" in _ROSTER_WRITERS, (
        "the union must still hold it, or a definition granting only "
        "excel_apply_spec stops counting as a writing agent"
    )
    tool = platform.registry.get("excel_apply_spec")
    assert tool is not None, "the install must actually serve what we arm"
    # The three facts that put `excel_edit` in the safe set, checked on this
    # tool rather than assumed: same tier, real undo, actually registered.
    assert tool.reversibility is Reversibility.REVERSIBLE
    assert type(tool).capture_undo is not Tool.capture_undo, (
        "REVERSIBLE is only honest if the tool really captures a pre-image"
    )
    assert type(tool).revert is not Tool.revert


def test_the_real_sentences_reach_it():
    for msg in _APPLY_SPEC_SENTENCES:
        picked = select_auto_tools(msg)
        assert "excel_apply_spec" in picked, f"{msg!r} armed {picked}"


def test_it_LEADS_its_own_sentence():
    """Not merely present — first. A model handed six excel tools takes the one
    at the top, and on "apply this layout to the workbook" the read/query tools
    cannot do what was asked."""
    for msg in _APPLY_SPEC_SENTENCES:
        assert select_auto_tools(msg, cap=1) == ["excel_apply_spec"], msg


def test_the_CAPTURE_tool_rides_with_the_applier():
    """``excel_apply_spec`` takes a ``spec`` object that ``excel_sheet_spec``
    produces — its own description says "Feed the spec to excel_apply_spec to
    reproduce the sheet elsewhere". Arming the applier alone is the same defect
    shape as arming a listing tool that cannot see the folder (§1): a tool
    present and structurally unable to do the job it was armed for. The capture
    half is READONLY, so it adds no tier."""
    for msg in _APPLY_SPEC_SENTENCES:
        picked = select_auto_tools(msg)
        assert "excel_sheet_spec" in picked, f"{msg!r} armed {picked}"
    assert "excel_sheet_spec" not in _WRITE_TIER


def test_it_never_fires_without_a_SPREADSHEET_noun():
    """The precision half. A mutating workbook tool armed on a sentence about a
    letter or a bug report is a wrong door for a local model (the v1.174.0
    lesson) — and these are the near misses, not strawmen: each one carries the
    rule's verb or its structure noun and is kept out by the other half."""
    for msg in ("apply the same format to the report",
                "apply for an extension",
                "format the dates in the letter",
                "use the standard letter template",
                "reproduce the bug in the template engine",
                "read this pdf and summarize it",
                "hey, how are you today?"):
        assert "excel_apply_spec" not in select_auto_tools(msg), msg


#: Everyday firm sentences that carry the rule's VERB *and* its structure noun
#: *and* a spreadsheet noun — everything the rule looks for — and are still not
#: a request to reproduce a sheet's structure. The only thing keeping the
#: mutator off them is the ``.{0,40}`` window between the verb and the noun.
#: The first two exercise the FORWARD branch (verb first), the last two the
#: REVERSED one (structure noun first).
_APPLY_SPEC_TOO_FAR = [
    "apply the credit to the Hollis account and let me know whether the sheet "
    "still ties out to the format we agreed",
    "match the client list against our CRM export and tell me which rows are "
    "missing from the workbook template",
    "the layout of last year's workbook was signed off by the partner, so just "
    "apply whatever the client sends us",
    "the formatting was fine in the spreadsheet, but the partner wants us to "
    "reproduce the Belmont engagement letter",
]


def test_the_verb_and_the_STRUCTURE_NOUN_have_to_be_NEAR_EACH_OTHER():
    """The precision axis ``test_it_never_fires_without_a_SPREADSHEET_noun``
    does not test. That test's negatives are all missing one of the rule's
    ingredients, so widening the ``.{0,40}`` proximity window to ``.{0,400}``
    left the whole file green — the rule's own comment claims that window is
    load-bearing and nothing measured it.

    These four have EVERY ingredient (a strong verb, a structure noun, a
    spreadsheet noun) and are still not the request; only the distance between
    the verb and the noun keeps the workbook MUTATOR off them. Measured: all
    four flip to armed at ``.{0,400}``, two through each branch. Note the
    asymmetry with the noun lookahead — that one is start-anchored over the
    whole message ON PURPOSE, because the workbook is routinely named before
    the verb; it is the VERB-to-STRUCTURE pairing that has to be local, since
    "apply" and "format" are both ordinary words on their own."""
    for msg in _APPLY_SPEC_TOO_FAR:
        picked = select_auto_tools(msg)
        assert "excel_apply_spec" not in picked, f"{msg!r} armed {picked}"
        # ...and the sentence is not simply dead to the module: the spreadsheet
        # rule still fires, so this is a NARROWING that costs nothing. Without
        # this half the test would pass just as well against a rule that had
        # stopped matching anything at all.
        assert "excel_query" in picked, f"{msg!r} armed {picked}"


def test_it_can_only_arm_where_excel_edit_was_ALREADY_eligible():
    """THE CONSENT INVARIANT, and the reason this addition does not widen the
    auto-allow surface by a single sentence.

    ``excel_apply_spec`` is a MUTATOR. The claim being made for it is not "it is
    harmless" but "it reaches no request that the equally-mutating
    ``excel_edit`` could not already reach" — so a user whose Auto toggle was
    consent for ``excel_edit`` has not been handed a new class of turn.

    That is guaranteed STRUCTURALLY: the rule requires a noun from the shared
    ``_XL_NOUNS`` list, which IS the spreadsheet rule's noun list (one constant,
    used twice), so every sentence that scores ``excel_apply_spec`` also scores
    ``excel_edit``. Both halves are asserted — the shared constant, so the two
    lists cannot silently drift apart again (they did on the first draft:
    ``xlsm`` and a plural ``sheets`` were in one and not the other), and the
    behaviour, over a corpus, so a future rewrite that abandons the constant is
    still caught."""
    # THE MECHANISM CHANGED IN ROUND 4's REPAIR, AND THE GUARANTEE GOT STRONGER.
    # This used to assert that TWO rules shared `_XL_NOUNS` — the apply rule and
    # the bare spreadsheet rule — and that the latter awarded `excel_edit`. That
    # bare rule had to stop awarding the editor, because a noun is TOPIC and not
    # INTENT: with an .xlsx attached it was arming `excel_edit` on "what does
    # this spreadsheet say?", and since v1.196.0 an armed name enters
    # `session_allow` with no approval card. Removing it there would have left
    # `excel_apply_spec` reaching FURTHER than `excel_edit` — a widening arriving
    # as the side effect of a narrowing — so the apply rule now awards BOTH.
    # One rule, one match, both mutators: they cannot diverge by construction,
    # which is a stronger claim than two rules sharing a constant.
    apply_rules = [(rx, w) for rx, w in _auto._RULES if "excel_apply_spec" in w]
    assert len(apply_rules) == 1, "exactly one rule may award the mutator"
    assert _auto._XL_NOUNS in apply_rules[0][0].pattern
    assert apply_rules[0][1].get("excel_edit"), (
        "the apply rule must award excel_edit too — that is what keeps "
        "excel_apply_spec from reaching a request excel_edit cannot"
    )
    # And the bare spreadsheet rule must NOT award the editor: that is the leak
    # this repair closed, and it is the half a future edit is most likely to undo.
    bare = [w for rx, w in _auto._RULES
            if rx.pattern == rf"\b({_auto._XL_NOUNS})\b"]
    assert len(bare) == 1 and "excel_edit" not in bare[0], (
        "a bare spreadsheet NOUN must never arm the editor — see "
        "'what does this spreadsheet say?'"
    )

    # ...and behaviourally, over every sentence this file uses anywhere.
    corpus = [*_APPLY_SPEC_SENTENCES, *_FOLDER_SENTENCES,
              *(m for m, _ in _INTENT_LEADS),
              "update cell B2 to 500", "check the formulas in the sheet",
              "create an excel workbook of the client fees",
              "sum the totals in the spreadsheet by client",
              "apply the same format to the report",
              "what do these fees add up to?", "hey, how are you today?"]
    # The behavioural half now asks the question DIRECTLY of the scorer instead
    # of via a second rule's pattern: any sentence that scores the newer mutator
    # must also score the older one. That is the property the claim rests on, and
    # it no longer depends on which rule happens to supply it.
    for msg in corpus:
        scored = _auto.select_auto_tools(msg, cap=99)
        if "excel_apply_spec" in scored:
            assert "excel_edit" in scored, (
                f"{msg!r} arms the workbook MUTATOR excel_apply_spec without "
                f"arming excel_edit — excel_apply_spec must never reach a "
                f"request excel_edit cannot"
            )


def test_a_READ_ONLY_agent_never_gains_the_workbook_writer(platform):
    """THE HALF THAT NEEDED THE OTHER FILE. Measured on the read-only REVIEWER
    roster with the safe-set half landed and ``_WRITE_TIER`` NOT updated: the
    run armed ``['excel_apply_spec', 'excel_query', 'excel_profile',
    'excel_read', 'file_search']`` while ``set(armed) & _WRITE_TIER`` was EMPTY
    — the gate saw nothing, and ``tests/test_agent_auto_arm_v1178.py`` stayed
    green because every assertion there is phrased as "no member of
    ``_WRITE_TIER``". With the pair landed the writer is excluded BEFORE the
    selector runs, so the cap still fills with tools this run can use."""
    task = "review the workbook and apply the firm's standard layout to it"
    armed = arm_for_task(platform, task, list(_REVIEWER))
    assert armed[: len(_REVIEWER)] == _REVIEWER, "the roster rides unchanged"
    added = armed[len(_REVIEWER) :]
    assert "excel_apply_spec" not in added, f"{task!r} armed {added}"
    assert not (set(armed) & _WRITE_TIER), f"{task!r} handed the reviewer a writer"
    # The read half still arrives — the gate costs vocabulary, not capability.
    assert "excel_sheet_spec" in added and "excel_query" in added, added
    # And the cap was not wasted on the excluded name.
    assert len(added) == 5, added


def test_a_WRITING_agent_does_gain_it(platform):
    """The other side of the same gate: for a definition that already writes,
    the tier is nothing new and the tool is exactly what the task asks for.
    Without this, the gate would read as "excel_apply_spec is never armable",
    which is not the decision that was made."""
    builder = ["read_file", "write_file", "edit_file", "list_files", "shell",
               "read_document", "write_document"]
    armed = arm_for_task(
        platform, "apply the firm's standard layout to this workbook", list(builder))
    added = armed[len(builder) :]
    assert "excel_apply_spec" in added, added


def test_the_MEASURED_slot_cost_is_stated_and_pinned():
    """NEVER SILENTLY DEGRADE, applied to the cap — and this is the assertion
    the first draft of the rule's comment got WRONG by reasoning instead of
    measuring ("file_search is the only tool displaced"). Two additions under a
    6-cap always cost two slots; WHICH two depends on the noun:

      * "workbook"/"sheet"/"cells" — ``file_search`` (3) drops.
      * "spreadsheet"/"xlsx" — those words ALSO fire the doc-noun rule, lifting
        ``file_search`` to 7, so ``excel_edit`` (6) and ``read_document`` (6)
        drop instead.

    The second case is the one worth pinning: the count of MUTATING tools armed
    does not rise there at all — one workbook writer is swapped for another.

    THAT LAST SENTENCE IS TRUE OF THIS LANE ONLY, and it is stated narrowly
    because an earlier cut of this file quoted it as a general property and it
    is not one. ``select_auto_tools`` scores the SENTENCE; the attachment lane
    (§8) arms in a separate pass and is not bounded by anything asserted here.
    That pass used to arm from the attachment's TYPE alone — measured,
    ``"thanks!"`` + ``client_fees.xlsx`` armed BOTH ``excel_edit`` and
    ``excel_apply_spec``, a mutator count of 2 on a thank-you — and since
    v1.196.0 round 3 its CHANGE half runs through
    ``attachment_rag.change_verbs_wanted``, which asks THIS function whether the
    request wanted that verb. So the two lanes are now coupled in one direction
    (attachment asks sentence, never the reverse), and the numbers pinned here
    are what that gate reads. See
    ``test_the_attachment_TYPE_pass_arms_THE_READ_VERBS_AND_NO_MUTATOR``."""
    workbook = select_auto_tools("apply this layout to the workbook")
    assert workbook == ["excel_apply_spec", "excel_query", "excel_profile",
                        "excel_sheet_spec", "excel_read", "excel_edit"], workbook

    spreadsheet = select_auto_tools(
        "apply the firm's standard layout to this spreadsheet")
    assert spreadsheet == ["excel_apply_spec", "excel_query", "excel_profile",
                           "excel_sheet_spec", "excel_read", "file_search"], spreadsheet
    assert set(spreadsheet) & _WRITE_TIER == {"excel_apply_spec"}, (
        "in the SENTENCE scorer the mutator COUNT must not rise on this class: "
        "excel_edit's slot was taken by excel_apply_spec, not added to. (Named "
        "rather than counted, because 'exactly one mutator' would also be "
        "satisfied by a DIFFERENT one arriving.)"
    )


def test_the_rest_of_the_corpus_did_not_move(platform):
    """THE CROWD-OUT GUARD for this section (§5 is the other one). Swept over 48
    sentences, only this rule's own class changes; these are the ones with a
    spreadsheet noun in them, where a badly-weighted new rule would do its
    damage first.

    THE RE-BASELINE, RECORDED BY NAME. This docstring used to say every
    right-hand value "was measured against the pre-rule selector and must be
    byte-identical after it". That was FALSE of FOUR of the five rows, and a
    guard whose stated claim is false is worse than no guard: the next reader
    trusts it. Measured against v1.195.0's selector, every row EXCEPT
    ``"update cell B2 to 500"`` moved, and all four moved the same way — they
    lost ``excel_edit``, because round 4 took the editor off the bare
    ``_XL_NOUNS`` rule (a file noun is TOPIC, not INTENT; it was arming the
    editor on "what does this spreadsheet say?"). v1.195.0 → now:

      "check the formulas in the sheet"
          ... excel_read, excel_edit, excel_accounts_diff
        → ... excel_read, excel_accounts_diff, excel_sheet_spec
      "create an excel workbook of the client fees"
          ... excel_read, file_search, excel_edit
        → ... excel_read, file_search, read_document
      "sum the totals in the spreadsheet by client"
          ... file_search, excel_edit, read_document
        → ... file_search, read_document
      "how much did we bill by client in the workbook"
          ... excel_read, excel_edit, file_search
        → ... excel_read, file_search

    ``"create an excel workbook of the client fees"`` IS NOT A READ-ONLY
    SENTENCE and must not be justified as one — it is a CREATION request. The
    reason losing ``excel_edit`` is right there is TOOL FIT, not consent: the
    creation rule's own comment records that ``excel_edit`` "refuses without an
    existing workbook", and "create" means there is none. ``write_document``
    leads the row and is the tool that can actually do it.

    Round 5 (imperative position + the workbook-as-object rules) moves NONE of
    these five: no row's verb is in the new rules' lists in a position they
    award from, so all five are byte-identical across that round."""
    unchanged = {
        "update cell B2 to 500": [
            "excel_query", "excel_profile", "excel_read", "excel_edit",
            "file_search"],
        "check the formulas in the sheet": [
            "excel_profile", "excel_query", "excel_formula_check",
            "excel_read", "excel_accounts_diff", "excel_sheet_spec"],
        "create an excel workbook of the client fees": [
            "write_document", "excel_query", "excel_profile",
            "excel_read", "file_search", "read_document"],
        "sum the totals in the spreadsheet by client": [
            "excel_query", "excel_profile", "excel_read", "file_search",
            "read_document"],
        "how much did we bill by client in the workbook": [
            "excel_query", "excel_profile", "excel_read", "file_search"],
    }
    for msg, want in unchanged.items():
        assert select_auto_tools(msg) == want, f"{msg!r} armed {select_auto_tools(msg)}"


# =============================================================================
# 8. THE ATTACHMENT LANE — where arming actually happens for a chat turn
# =============================================================================
def test_an_xlsx_attachment_still_arms_the_excel_READ_verbs(platform):
    """MANDATED CROWD-OUT GUARD, and it has to be measured on
    ``chat_turn._resolve_armed_tools`` rather than on ``select_auto_tools``:
    the selector scores the SENTENCE, and an attachment only ever contributes
    ``read_document`` to it. The attachment's TYPE is what arms the excel verbs,
    in a separate pass (v1.196.0), and that pass is where a regression would
    land. The three READ verbs are the floor: a workbook attached to a turn that
    cannot profile, query or read it is the exact defect this wave exists to
    close (96 ``read_document`` calls, ZERO ``excel_*`` calls, in the ledger)."""
    armed, _ = _resolve_armed_tools(
        _deps(platform), _body("can you take a look at this?", ["client_fees.xlsx"]))
    for verb in ("excel_profile", "excel_query", "excel_read"):
        assert verb in armed, f"armed {armed}"
    assert "read_document" in armed, f"armed {armed}"
    assert len(armed) <= 6


def test_the_attachment_TYPE_pass_arms_THE_READ_VERBS_AND_NO_MUTATOR(platform):
    """WHAT THE ATTACHMENT LANE ACTUALLY DOES, measured — and the retraction of
    a FALSE claim that stood in this slot.

    THE CLAIM THAT WAS HERE AND IS WRONG. An earlier cut asserted "``
    excel_apply_spec`` may ride wherever ``excel_edit`` already rides and
    nowhere else", implemented as ``if apply in armed: assert edit in armed``,
    over four hand-picked texts. That invariant is FALSE of this code. Re-run
    over :data:`_APPLY_SPEC_SENTENCES` x {no attachment, ``client_fees.xlsx``}
    through this same ``_resolve_armed_tools`` it produces 13 counterexamples in
    20, because the 6-cap displaces ``excel_edit`` once ``excel_apply_spec`` and
    ``excel_sheet_spec`` are in the list — e.g. "apply the firm's standard
    layout to this spreadsheet" + ``client_fees.xlsx`` arms
    ``['read_document', 'excel_apply_spec', 'excel_query', 'excel_profile',
    'excel_sheet_spec', 'excel_read']`` with NO ``excel_edit``. The same file
    PINS one of those lists in
    ``test_the_MEASURED_slot_cost_is_stated_and_pinned``, so the two tests
    asserted contradictory things about one sentence class and only the trimmed
    corpus kept this one green. THE TRUE, CODE-LEVEL FORM OF THAT CLAIM IS
    ``test_it_can_only_arm_where_excel_edit_was_ALREADY_eligible`` above — an
    ELIGIBILITY invariant (shared ``_XL_NOUNS``, so every sentence scoring the
    new mutator also scores ``excel_edit``), which survives the cap because
    scoring happens before truncation. Nothing here restates it.

    WHAT THIS TEST USED TO ASSERT, AND WHY IT NO LONGER DOES. It recorded, on
    purpose, that on a turn carrying NO INTENT WHATSOEVER the attachment lane
    armed every workbook verb the type table names — the MUTATORS INCLUDED.
    Measured then, ``"thanks!"`` + ``client_fees.xlsx`` armed
    ``['read_document', 'excel_profile', 'excel_query', 'excel_read',
    'excel_edit', 'excel_apply_spec']``: two ``_WRITE_TIER`` members off FILE
    TYPE ALONE, on a message that is a thank-you. It was written to GO RED when
    the intent gate landed, as the record that the exposure existed and was
    removed deliberately rather than a guard that would sit green through either
    outcome.

    THE GATE LANDED (v1.196.0 round 3, ``attachment_rag.change_verbs_wanted``),
    so the mutator half is re-pointed at an intent-carrying sentence, exactly as
    the note said to do. The two halves now assert the two rules the split
    created, and the file-type sentence stays as the negative case so the
    exposure cannot come back unnoticed:

      * the READ verbs still arm on ``"thanks!"`` — the type-alone rule, and the
        measured repair this whole wave is (12 of 18 document tools had never
        run once). Weakening THAT is the regression that would be worse than the
        exposure;
      * the CHANGE verbs arm on ``"update cell B2 to 500"`` and on nothing less.

    Both sides are still COMPUTED from ``live_tool_names`` rather than
    hardcoded, so a change to ``_WORKBOOK`` moves this test's expectations with
    it instead of breaking it for the wrong reason."""
    read_verbs = set(live_tool_names(".xlsx", kind="read"))
    change_verbs = set(live_tool_names(".xlsx", kind="change"))
    assert read_verbs and change_verbs, "the type table must still describe a workbook"
    type_mutators = change_verbs & _WRITE_TIER
    assert type_mutators, (
        "no workbook change verb is in _WRITE_TIER — this guard would be "
        "vacuous, so the consent question it records has to be re-asked, not "
        "assumed gone"
    )

    # (a) NO INTENT: readers yes, mutators no.
    armed, _ = _resolve_armed_tools(
        _deps(platform), _body("thanks!", ["client_fees.xlsx"]))
    assert set(armed) & read_verbs == read_verbs & AUTO_SAFE_TOOLS, (
        f"the type pass must still admit every safe-set READ verb of the "
        f"table — that is the wave's whole point; armed {armed}"
    )
    assert not (set(armed) & _WRITE_TIER), (
        f"a message with no verb, no file and no intent armed the workbook "
        f"MUTATORS {sorted(set(armed) & _WRITE_TIER)} — from file type alone, "
        f"and this list becomes the turn's session_allow. Armed: {armed}"
    )
    assert not (set(armed) & change_verbs), f"armed {armed}"

    # (b) WITH INTENT: the mutator the sentence asks for is armed, so the gate
    # is a gate and not a wall.
    armed, _ = _resolve_armed_tools(
        _deps(platform), _body("update cell B2 to 500", ["client_fees.xlsx"]))
    assert "excel_edit" in armed, f"the intent gate never opens: {armed}"
    assert set(armed) & _WRITE_TIER == {"excel_edit"}, (
        f"asking to edit ONE cell armed {sorted(set(armed) & _WRITE_TIER)}; a "
        f"per-verb gate must not drag a sibling mutator in with it"
    )


def test_a_plain_pdf_turn_is_untouched_by_this_unit(platform):
    """The other half of the mandated guard, through the real arming path: a
    "read this pdf" turn must still lead with the readers, and no excel tool may
    appear on a turn with no workbook anywhere in it."""
    armed, _ = _resolve_armed_tools(
        _deps(platform), _body("read this pdf and summarize it", ["report.pdf"]))
    assert "read_document" in armed, armed
    # Ahead of any mutator, rather than pinned to index 0: the ORDER of the
    # attachment-type pass is that unit's to change, but a turn whose first
    # offered tool outranks the reader on "read this pdf" is a regression in
    # anyone's hands (the local-model wrong-door failure, v1.174.0).
    mutators = [i for i, t in enumerate(armed) if t in _WRITE_TIER]
    assert all(i > armed.index("read_document") for i in mutators), armed
    assert not [t for t in armed if t.startswith("excel_")], armed


# =============================================================================
# 9. THE CHANGE-INTENT SCORER GAP (v1.196.0 round 4)
# =============================================================================
#: MEASURED THROUGH ``chat_turn._resolve_armed_tools`` WITH A REAL ATTACHMENT —
#: not through ``select_auto_tools`` in isolation, because the two answer
#: different questions and only the first is what a user gets. Left column: the
#: sentence. Middle: the file attached to that turn. Right: the change verb the
#: sentence plainly asks for, and the EXACT list ``change_verbs_wanted`` must
#: return for it.
#:
#: Every one of these armed NO change tool before this round. That is NOT a
#: regression the wave introduced and this file must not pretend otherwise: at
#: v1.195.0 an attachment armed nothing from its type at all, so these sentences
#: armed nothing then either, and rounds 1-2 appeared to serve them only by
#: arming EVERY verb for EVERY attachment — the consent widening round 3 removed
#: (§8). What round 3 changed is who NOTICES: with the gate in place, a sentence
#: the scorer cannot read becomes a model saying it cannot do the thing, and a
#: user rephrasing. These are the seven, with the armed list each produced:
#:
#:   "turn this into a pdf"            report.docx      [read_document, file_search]
#:   "convert this to a word document" notes.txt        [read_document]
#:   "add a column for the tax rate"   client_fees.xlsx [read_document, excel_profile,
#:                                                       excel_query, excel_read]
#:   "change the fee for Belmont..."   client_fees.xlsx  (the same four)
#:   "extract pages 3-5 into a new pdf" return.pdf      [read_document, file_search,
#:                                                       extract_pdf]
#:   "delete page 2 from this pdf"     return.pdf       [read_document, extract_pdf,
#:                                                       file_search]
#:   "rewrite this as a formal letter and save it"  draft.docx  [read_document]
_CHANGE_INTENT: list[tuple[str, str, list[str]]] = [
    ("turn this into a pdf", "report.docx", ["convert_document"]),
    ("convert this to a word document", "notes.txt", ["convert_document"]),
    ("add a column for the tax rate", "client_fees.xlsx", ["excel_edit"]),
    ("change the fee for Belmont to 3000", "client_fees.xlsx", ["excel_edit"]),
    # Both read: ``pdf_arrange`` with pages "3-5", or ``pdf_split`` with
    # ranges ["3-5"]. Arming both is not slop — the model picks the shape.
    ("extract pages 3-5 into a new pdf", "return.pdf", ["pdf_arrange", "pdf_split"]),
    # ...and deleting a page is NOT a split: ``pdf_split`` cuts one PDF into
    # SEVERAL files, which is not what was asked, so it must not ride along.
    ("delete page 2 from this pdf", "return.pdf", ["pdf_arrange"]),
    ("rewrite this as a formal letter and save it", "draft.docx", ["write_document"]),
]


def test_the_seven_measured_change_requests_reach_their_verb(platform):
    """THE GAP ITSELF, closed and measured where the user is standing."""
    for msg, attachment, want in _CHANGE_INTENT:
        armed, _ = _resolve_armed_tools(_deps(platform), _body(msg, [attachment]))
        for verb in want:
            assert verb in armed, f"{msg!r} + {attachment} armed {armed}"
        assert len(armed) <= 6, armed


def test_the_gate_opens_PER_VERB_AND_NOT_IN_BULK():
    """Round 3 established that a boolean "some change was asked for" is the
    wrong shape: it would let "write a summary memo" unlock ``excel_edit`` on an
    attached workbook. So the assertion is EXACT EQUALITY, not membership — a
    sibling mutator riding in on the same sentence fails here."""
    for msg, attachment, want in _CHANGE_INTENT:
        suffix = "." + attachment.rsplit(".", 1)[1]
        got = change_verbs_wanted(suffix, msg, attachments=[attachment])
        assert got == want, f"{msg!r} ({suffix}) wanted {want}, got {got}"


def test_a_change_verb_never_crosses_TYPES():
    """The other axis of the same rule. Each sentence asks for a change to ONE
    KIND of file; pointed at a different kind it must ask for nothing, or the
    scorer has become the blanket flag round 3 removed. Asserted on the WORKBOOK
    type in the first loop because that is where a mis-scored sentence really
    would arm a mutator the user never asked for."""
    for msg in ("delete page 2 from this pdf",
                "turn this into a pdf",
                "extract pages 3-5 into a new pdf",
                "rewrite this as a formal letter and save it"):
        assert change_verbs_wanted(
            ".xlsx", msg, attachments=["client_fees.xlsx"]) == [], (
            f"{msg!r} asks for nothing about a workbook and armed one"
        )
    # ...and the workbook sentences ask for nothing about a PDF.
    for msg in ("add a column for the tax rate", "change the fee for Belmont to 3000"):
        assert change_verbs_wanted(".pdf", msg, attachments=["return.pdf"]) == [], msg


def test_the_READ_ONLY_turns_still_arm_NO_MUTATOR(platform):
    """THE CONSENT GATE, which outranks every line of coverage above it.

    These four are round 3's whole purpose (§8) and are the measurement most
    likely to be broken by widening intent detection: a rule keyed on a bare
    FILE NOUN ("pdf", "document", "spreadsheet") matches "summarize this pdf"
    and re-opens the hole. Every rule this round added is keyed on a VERB OF
    CHANGE plus its object, which is why these stay clean.

    Asserted TWO ways, because ``_WRITE_TIER`` alone is not enough: it does not
    hold ``convert_document`` (IRREVERSIBLE — it writes a NEW file rather than
    modifying an existing one), so a turn arming ``convert_document`` off "what
    does this say?" would pass a ``_WRITE_TIER`` check and still be the defect.
    """
    for msg, attachment in (("thanks!", "client_fees.xlsx"),
                            ("thanks!", "summary.docx"),
                            ("summarize this", "report.pdf"),
                            ("what does this say?", "notes.txt")):
        suffix = "." + attachment.rsplit(".", 1)[1]
        change = set(live_tool_names(suffix, kind="change"))
        assert change, f"the type table must still describe {suffix}"
        armed, _ = _resolve_armed_tools(_deps(platform), _body(msg, [attachment]))
        assert not (set(armed) & change), (
            f"{msg!r} + {attachment} armed the change verbs "
            f"{sorted(set(armed) & change)} — this list becomes the turn's "
            f"session_allow, so each would run with no approval card"
        )
        assert not (set(armed) & _WRITE_TIER), f"{msg!r} armed {armed}"
        # ...and the READ half is untouched: the wave's whole point.
        for verb in live_tool_names(suffix, kind="read"):
            if verb in AUTO_SAFE_TOOLS:
                assert verb in armed, f"{msg!r} + {attachment} armed {armed}"


def test_the_CROWD_OUT_floor_holds(platform):
    """MANDATED GUARD. Both lanes cap at 6, so new weight can push
    ``read_document``/``file_search`` off a normal request — a worse regression
    than the gap being closed. Measured through the real lane and byte-identical
    to its pre-round-4 value."""
    armed, _ = _resolve_armed_tools(
        _deps(platform), _body("read this pdf and summarize it", ["report.pdf"]))
    assert armed == ["read_document", "file_search", "extract_pdf"], armed

    # An .xlsx attachment with a CONTENTLESS message still arms the read verbs.
    armed, _ = _resolve_armed_tools(
        _deps(platform), _body("can you take a look at this?", ["client_fees.xlsx"]))
    assert armed == ["read_document", "excel_profile", "excel_query", "excel_read"], armed

    # And the sentence scorer's own leads are unmoved.
    for msg in ("read this pdf and summarize it",
                "summarize the attached pdf report",
                "review the contract for the termination clause"):
        picked = select_auto_tools(msg)
        assert picked[0] == "read_document", f"{msg!r} armed {picked}"
        assert "file_search" in picked, f"{msg!r} armed {picked}"


#: The near misses for each rule this round added — every one carrying the
#: rule's VERB or its NOUN and kept out by the other half. Not strawmen: each
#: was found by measurement, and the first three were live FALSE ARMS of the
#: first cut (a plain ``.{0,20}?`` bridge let "extract the text FROM page 3"
#: arm ``pdf_arrange`` + ``pdf_split`` and nothing that reads).
_ROUND4_NEGATIVES = [
    ("extract the text from page 3", {"pdf_arrange", "pdf_split"}),
    ("extract the data from pages 3-5", {"pdf_arrange", "pdf_split"}),
    ("extract the 3 tables on page 2", {"pdf_arrange", "pdf_split"}),
    # A word processor's furniture is not page-level PDF surgery.
    ("remove the page numbers from the footer", {"pdf_arrange", "pdf_split"}),
    ("delete the page breaks", {"pdf_arrange", "pdf_split"}),
    # The convert rule needs a FORMAT against `to`, not a destination or
    # another sense of the word.
    ("save it to the pdf folder", {"convert_document"}),
    ("turn this into a summary", {"convert_document"}),
    ("make this a priority", {"convert_document"}),
    # The figure rule needs all three of verb + figure noun + numeric target.
    ("change the meeting to 3pm", {"excel_edit"}),
    ("change the wording to be more formal", {"excel_edit"}),
    ("increase the font size to 12", {"excel_edit"}),
    ("what do these fees add up to?", {"excel_edit"}),
    ("compare the totals to last year", {"excel_edit"}),
    # The structure rule needs a sheet-structure noun, and `review` is a read.
    ("add a section for the disclaimer", {"excel_edit"}),
    ("review the columns and tell me which are empty", {"excel_edit"}),
    # ...and the re-authoring verbs must not swallow the reading one.
    ("review the contract for the termination clause", {"write_document"}),
]


def test_the_new_rules_are_PRECISE():
    """A false arm costs schema context and hands a local model another wrong
    door (the v1.174.0 lesson this module keeps citing), and for a MUTATOR it
    also costs consent. Asserted on the returned list, never on the pattern."""
    for msg, banned in _ROUND4_NEGATIVES:
        picked = set(select_auto_tools(msg))
        assert not (picked & banned), f"{msg!r} armed {sorted(picked & banned)}"


def test_the_round_4_rules_stay_inside_the_safe_set():
    """Same discipline as ``test_the_new_rules_stay_inside_the_safe_set`` above:
    a rule naming shell/mcp/pixio would be silently filtered, so the assertion
    is on the RETURNED list."""
    for msg, _attachment, _want in _CHANGE_INTENT:
        assert set(select_auto_tools(msg)) <= AUTO_SAFE_TOOLS, msg


def test_the_rest_of_the_corpus_did_not_move_in_ROUND_4():
    """THE CROWD-OUT GUARD FOR THIS SECTION, by measurement rather than by
    argument. Swept over 105 sentence/attachment rows — everything this file
    pins, the brain/web/memory corpus from v1.173.0, and one writey task from
    ``tests/test_agent_auto_arm_v1178.py`` (that file's own 535 assertions cover
    the rest, and they run green) — and exactly the seven ``_CHANGE_INTENT``
    rows changed. These ten are the rows NEAREST the new rules: each carries a
    page word, a format word, a change verb or a figure noun.

    TWO ROWS ARE NOT byte-identical to their pre-round-4 value, and saying they
    were was the same overclaim §7's docstring made. ``"check the formulas in
    the sheet"`` and ``"sum the totals in the spreadsheet by client"`` each lost
    ``excel_edit`` in round 4 itself, when the editor came off the bare
    ``_XL_NOUNS`` rule — that is the round's own deliberate narrowing, recorded
    here rather than papered over. The other eight are byte-identical.

    ROUND 5 MOVES NONE OF THE TEN. The imperative-position gate only ever
    REMOVES an award, and every row that scores a mutator scores it from an
    imperative verb ("merge these pdfs", "convert these documents to pdf",
    "update cell B2 to 500", "...and save a corrected version as a docx"); the
    two new workbook rules need a change verb whose object is a workbook noun,
    which none of these has."""
    unchanged = {
        "extract the pages from these scanned pdfs": [
            "read_document", "file_search"],
        "extract the tables from the pdf": [
            "read_document", "file_search", "extract_pdf"],
        "merge these pdfs into one file": [
            "pdf_arrange", "pdf_split", "file_search", "read_document"],
        "split these documents into separate pages": [
            "pdf_arrange", "pdf_split", "file_search", "read_document"],
        "convert these documents to pdf": [
            "convert_document", "read_document", "file_search"],
        "update cell B2 to 500": [
            "excel_query", "excel_profile", "excel_read", "excel_edit",
            "file_search"],
        "check the formulas in the sheet": [
            "excel_profile", "excel_query", "excel_formula_check",
            "excel_read", "excel_accounts_diff", "excel_sheet_spec"],
        "sum the totals in the spreadsheet by client": [
            "excel_query", "excel_profile", "excel_read", "file_search",
            "read_document"],
        "review the draft report and save a corrected version as a docx": [
            "read_document", "write_document", "file_search", "write_file"],
        "hey, how are you today?": [],
    }
    for msg, want in unchanged.items():
        assert select_auto_tools(msg) == want, f"{msg!r} armed {select_auto_tools(msg)}"


#: WHAT IS STILL NOT REACHED — the honest half, kept as DATA so it cannot decay
#: into a vague sentence. Each row is ``(suffix, sentence, the verb it asks for
#: and does not get)``, measured after this round. NOT "scores nothing": the
#: last row scores ``write_document`` and only misses ``convert_document``, and
#: an earlier cut of this list said "no change verb" about it and went red on
#: its own data — which is exactly the sort of confident-but-unmeasured claim
#: this file keeps catching. A phrasing left here is a known limit; a rule that
#: quietly re-opened the consent hole to catch it would be a shipped defect, and
#: the gate outranks the coverage.
#:
#: (An earlier cut of this note called
#: ``tests/test_attachment_handoff_v1196.py::
#: test_the_unarmed_clause_states_a_NECESSARY_condition_not_a_promise`` STALE for
#: naming four sentences as open gaps. It is not stale: that docstring was
#: updated in the same round, marks all four CLOSED by name, and now carries its
#: own three still-open rows. Re-checked in round 5 — the note was the stale
#: thing, not the file it pointed at.)
_STILL_OPEN = [
    # No numeric target — the figure rule requires one ON PURPOSE, because a
    # non-numeric target is routinely a sentence about PROSE ("change the rate
    # description to something plainer") and a workbook mutator is the wrong
    # thing to be wrong about.
    (".xlsx", "change the fee for Belmont to whatever we billed last year",
     "excel_edit"),
    # A figure noun with no `to <number>`: "update the totals" may equally mean
    # "recompute them and tell me", which arms nothing that writes.
    (".xlsx", "update the totals", "excel_edit"),
    # No structure noun and no figure noun.
    (".xlsx", "add the missing client", "excel_edit"),
    # `change` is not one of the STRUCTURE rule's verbs and "client name" is not
    # a figure noun, so neither round-4 spreadsheet rule reaches this. Measured
    # after an earlier draft of the figure rule's comment claimed the structure
    # rule caught it — it does not, and the row is here instead of in a comment.
    (".xlsx", "change the client name in row 4 to Belmont LLC", "excel_edit"),
    # Elliptical — no verb at all.
    (".docx", "pdf this", "convert_document"),
    (".txt", "word doc please", "convert_document"),
    # `save X as a pdf`: DELIBERATELY not closed, and the one row here that is a
    # DECISION rather than a limit. Adding `as` to the convert rule's
    # prepositions closes it and ALSO fires on "review the draft report and save
    # a corrected version as a docx", which tests/test_agent_auto_arm_v1178.py
    # pins as a task a READ-ONLY reviewer agent must gain no writer from — and
    # `convert_document` is absent from `agents/runtime._WRITE_TIER`, so that
    # gate would not have stopped it and that whole file would have stayed
    # green. See the note in the convert rule and the tier-hole test below.
    # It is NOT unarmed: the creation rule's `save ... pdf` already scores
    # `write_document`, so the turn can produce a PDF — from the text the model
    # read, not by converting the file. That is a worse answer than
    # `convert_document` and a much better one than nothing.
    (".docx", "save this as a pdf", "convert_document"),
    # ---- ADDED IN ROUND 5, and every one of them is a LIMIT OF THE VERB LIST
    # rather than of the position gate. `_IMPERATIVE`'s deontic branch admits
    # "the pdf needs splitting" and "the scan should be rotated" (both measured
    # ARMED, and pinned in tests/test_change_intent_guard_v1196.py), so the
    # position test is not what stops these. What stops them is that the
    # matching rule enumerates BASE FORMS of its verbs, and these sentences use
    # a gerund or a past participle:
    #   convert rule: `convert`, not `converting`/`converted`
    #   figure rule:  `change`,  not `changed`
    # ...and, for the workbook rows, that the two round-5 workbook rules read
    # VERB-then-NOUN and these are NOUN-then-VERB.
    #
    # Closing them means adding inflections to those verb lists, which widens
    # every OTHER sentence those rules see; that is a measured sweep this round
    # did not run, so the rows sit here instead of being guessed at. The
    # asymmetry with the PDF rule is not an accident — its noun-first branch
    # already ends `\w*`, which is why "rotated" matches there and "converted"
    # does not match in the convert rule.
    (".docx", "this needs converting to pdf", "convert_document"),
    (".docx", "the report needs to be converted to a pdf", "convert_document"),
    (".xlsx", "the fee should be changed to 3000", "excel_edit"),
    (".xlsx", "this spreadsheet needs fixing", "excel_edit"),
    (".xlsx", "the spreadsheet needs to be sorted by client", "excel_edit"),
    # ---- WHAT ROUND 6's POSITION GATE COSTS, measured and stated rather than
    # discovered later. A POSITIONAL imperative (a coordinator, a sentence
    # terminator, a newline) awards only when no enquiry/reporting marker
    # appears EARLIER IN THE MESSAGE, so an instruction that FOLLOWS a question
    # in the same turn is refused. Measured over eight turns of that shape, five
    # lose their verb; the three that survive do so through a CONTEXT-FREE
    # branch, which is the workaround and is not obvious to a user:
    #     "what do they want? PLEASE delete page 2"       -> armed
    #     "what does page 3 say? CAN YOU delete page 2"   -> armed
    #     "delete page 2. what do they want?"             -> armed (\A)
    # The clause-scoped alternative was measured and REJECTED: scoping the marker
    # test to the current clause recovers this class and re-opens family 3,
    # because one full stop anywhere in a pasted email ("Thanks for the draft.")
    # resets the window and the paste's own lead-in stops being visible. Consent
    # outranks coverage, so the window stays whole-prefix and these rows stay
    # here. Closing them means a marker window that survives a paste — a
    # different mechanism, not a wider vocabulary.
    (".pdf", "what do they want? also, delete page 2", "pdf_arrange"),
    (".xlsx", "why is the layout like that? and clear the sheet", "excel_edit"),
    (".docx", "what format is this? convert this to a pdf", "convert_document"),
    # ---- ROUND 7 RE-MEASURED THAT COST AND FOUND IT MUCH BIGGER THAN THE THREE
    # ROWS ABOVE. The trigger was not "a question": it was any QUOTING NOUN
    # anywhere earlier, so "read the MEMO and then delete page 2" was refused
    # while "read the FILE and then delete page 2" was pinned green. On a 25-row
    # corpus of this practitioner's own requests, SIXTEEN lost their verb.
    # ``_ENQUIRY`` now requires a REPORTING CUE beside the noun and ALL 25 of them
    # reach their verb (``tests/test_change_intent_guard_v1196.PRACTITIONER``).
    # An earlier draft of this passage said "23 of the 25" and introduced the two
    # rows below as the failures — they are NOT corpus rows at all, and the twin of
    # that sentence in ``tools/autoselect.py`` was retracted while THIS one survived.
    #
    # THE TWO ROWS BELOW ARE SEPARATE STILL-OPEN PHRASINGS (measured: neither
    # reaches a change verb), and they fail for DIFFERENT reasons — worth
    # separating, because only the first is the marker window:
    #
    #   (i) A GENUINE REPORT, FOLLOWED BY AN INSTRUCTION. "their email" is a
    #       third-person attribution and the cue fires correctly; the cut then
    #       sits at offset 0 and the positional branch after the full stop is
    #       refused. This is the honest residue of the whole-prefix window — the
    #       same shape as the three rows above, now correctly scoped to REPORTS
    #       instead of to bare nouns. Closing it means a marker window that ends
    #       at the report, which is the clause-scoped design already measured and
    #       rejected above.
    (".pdf", "their email is confusing. just delete page 2", "pdf_arrange"),
    #  (ii) NOT THE GATE AT ALL, and it would be easy to file it as one. There is
    #       no marker in this message: ``_ENQUIRY`` finds nothing, the ``.``
    #       branch is allowed, and the verb is still missed because "now" is not
    #       in ``_imperative``'s trailing filler list
    #       ``(?:please|kindly|just|also|then)``. Proof it is the filler and not
    #       the noun: "the FILE is done. now delete page 2" fails identically,
    #       and "the checklist is done. delete page 2" — the same sentence minus
    #       "now" — arms ``pdf_arrange``. Closing it is a one-word addition to
    #       that list, which widens every gated rule and belongs to a round that
    #       measures it.
    (".pdf", "the checklist is done. now delete page 2", "pdf_arrange"),
    # ---- THE AGENT-LANE LOSS, undocumented until round 6 and inherited from
    # round 4. A NOMINALIZED TICKET TITLE — the way a task is written when it
    # comes off a job board or a workflow step rather than out of a chat box,
    # which is `agents/runtime.arm_for_task`'s whole input. It carries a
    # spreadsheet noun ("workbook"), a change word ("Cleanup") and a group-by
    # ("sorting by client"), and it reaches NO editor: measured, it arms
    # ['excel_query', 'excel_profile', 'excel_read', 'file_search'].
    #
    # THE CAUSE IS NOT THE POSITION GATE and saying so is the point of writing
    # the row down. Round 4 took `excel_edit` off the bare `_XL_NOUNS` rule
    # because a noun is TOPIC and not INTENT, and round 5 recovered the class
    # with two rules that both read VERB-then-NOUN in imperative position. A
    # nominalization has no verb at all — "Cleanup", not "clean up" — so neither
    # rule can see it, and the bare noun no longer awards. Closing it means
    # teaching a rule to read a deverbal noun, which is a different sweep from
    # this round's, and it must not be closed by putting the editor back on the
    # bare noun: that is the leak the whole guard exists to hold shut.
    (".xlsx", "Cleanup of the fee workbook (sorting by client)", "excel_edit"),
]


def test_the_STILL_OPEN_list_is_TRUE(platform):
    """NEVER SILENTLY DEGRADE, applied to this unit's own reach.

    A test that pins only what works reads as "the scorer handles change
    requests", which is not true and would let the next reader take a rephrasing
    problem for a bug somewhere else. If you close one of these, DELETE ITS ROW
    — do not delete the test."""
    for suffix, msg, missing in _STILL_OPEN:
        assert missing not in change_verbs_wanted(suffix, msg), (
            f"{msg!r} now reaches {missing} — good; take it off _STILL_OPEN"
        )
    # The one row that is not simply empty, pinned by VALUE so the difference
    # between "reaches the wrong verb" and "reaches nothing" stays visible.
    assert change_verbs_wanted(".docx", "save this as a pdf") == ["write_document"]
    # ...and the honest behaviour is the same either way: nothing that writes is
    # armed, and the round-3 prose decision has the block SAY a change is
    # possible and not armed, so the user is never told the app cannot do it.
    armed, _ = _resolve_armed_tools(
        _deps(platform), _body("update the totals", ["client_fees.xlsx"]))
    assert not (set(armed) & _WRITE_TIER), armed


def test_the_convert_document_TIER_HOLE_is_recorded_not_hidden(platform):
    """A PRE-EXISTING hole whose REACH this round widens, written down instead
    of left for someone to find.

    ``agents/runtime._WRITE_TIER`` stops a READ-ONLY agent definition (REVIEWER,
    SUPERVISOR) from gaining a writer off its task text. ``convert_document``
    writes a NEW file and is NOT in it: it is ``Reversibility.IRREVERSIBLE``
    (there is no pre-image to capture), and §4's forward guard only catches
    REVERSIBLE tools, so nothing goes red. "convert the report to a pdf" already
    reached it before this round; adding "turn X into a pdf" to the convert rule
    means one more phrasing does.

    This test asserts the CURRENT TRUTH rather than a promise. The four
    ``_WRITE_TIER`` verbs this round newly scores are all still excluded from a
    read-only run — that is the half that matters and it is checked first.
    Closing the remaining hole is a PAIRED change in another module (add the
    name to ``_WRITE_TIER``; the union ``_ROSTER_WRITERS`` inherits it), and
    when it lands this test goes red on the line that says so, which is the
    point."""
    for task in ("add a column for the tax rate",
                 "change the fee for Belmont to 3000",
                 "extract pages 3-5 into a new pdf",
                 "delete page 2 from this pdf",
                 "rewrite this as a formal letter and save it"):
        armed = arm_for_task(platform, task, list(_REVIEWER))
        assert armed[: len(_REVIEWER)] == _REVIEWER, "the roster rides unchanged"
        assert not (set(armed) & _WRITE_TIER), f"{task!r} armed {armed}"

    assert "convert_document" not in _WRITE_TIER, (
        "convert_document is gated now — delete this test's second half, drop "
        "the note in the convert rule, and close the `save X as a pdf` row in "
        "_STILL_OPEN while you are there"
    )
    added = arm_for_task(platform, "turn the report into a pdf", list(_REVIEWER))
    assert "convert_document" in added[len(_REVIEWER):], (
        f"the hole this test records has moved; re-measure it: {added}"
    )
