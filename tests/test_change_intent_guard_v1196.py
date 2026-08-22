"""IMPERATIVE POSITION IS WHAT AWARDS A MUTATOR, AND A POSITION IS NOT ENOUGH
WHERE THE TEXT IS SOMEONE ELSE'S (v1.196.0 rounds 5 and 6).

WHY THIS FILE EXISTS. v1.196.0 made the attachment CONSENT GATE arm a change
tool only when ``select_auto_tools`` says the request asked for a change — so
every false positive in the change-intent rules is a silent grant of a mutator
on a read-only turn, and every false negative is a feature that does not work.
An armed name enters ``session_allow`` and runs with NO approval card.

THAT LAST SENTENCE IS NOT A v1.196.0 CHANGE, and this file used to say it was
("an armed name enters ``session_allow``... THAT TURNED every false positive
into a silent grant"). At v1.195.0 ``daemon/chat_turn`` already read
``armed_grant = set(overrides.keys())`` and passed ``session_allow=armed_grant``,
and both lines are byte-identical today — checked against ``git show HEAD``, not
remembered. Auto-armed mutators have never had an approval card. What this wave
changed is REACH: it admitted ``excel_apply_spec`` to the safe set and added
rules awarding ``excel_edit``, ``pdf_arrange``, ``convert_document`` and
``redact_pii`` off phrasings that previously scored nothing. The urgency is
real; the stated cause was wrong, and a false "this is new" hides the exposure
that was already there.

WHAT ROUND 4 TRIED, AND WHY IT WAS REPLACED RATHER THAN TUNED. Round 4 scanned
for ENQUIRY MARKERS — interrogative auxiliaries, negation ANYWHERE in the
message, "explain how..." — and REVOKED the mutators. Measured on the real chat
lane, it suppressed the WRONG turns and missed the RIGHT ones, because the two
failure modes are ORTHOGONAL and negation-anywhere is not a proxy for "not a
request". Twelve read-only turns still armed a mutator (``"should I add a
column for the tax rate?"``, ``"the memo says to change the fee for Belmont to
3000"``, ``"can excel add a column automatically?"``) while twenty-five real
change requests were killed, most of them by the DOMINANT shape of this user's
asks — a request that explains itself with a negated clause (``"delete page 2,
it's not needed"``, ``"convert this to a pdf, not a docx"``).

ROUND 5 INVERTS THE DISCRIMINATOR. A change verb earns its tool only when it
sits in IMPERATIVE POSITION — opening a clause with no subject of its own, which
is what an instruction IS. The test reads the ONE word before the verb instead
of the whole message, so ``"don't DELETE page 2"`` is refused by the same check
that admits ``"DELETE page 2, it's not needed"``. See
``tools/autoselect._imperative`` for the alternatives and why each is there.

ROUND 6 GATES THE THREE POSITIONAL ALTERNATIVES, AND THIS FILE'S OWN CORPUS IS
WHY IT TOOK A SECOND ROUND TO FIND THEM. Round 5's imperative test has ten
alternatives; seven are CONTEXT-FREE (the words cannot occur in an enquiry) and
three merely name a POSITION — a coordinator, a sentence terminator, a newline —
which is exactly what a quotation reproduces. All three were measured leaking
and NONE of them had a single row here: the 43 read-only rows below contained no
coordinated enquiry, no colon, no semicolon, no newline, no bullet and no pasted
block, and the five blunting frames in
``test_every_gated_mutator_is_actually_gated`` used none either. The corpus had
been written ALONG the mechanism instead of AGAINST it, so it could not fail on
the family that held the release. Measured on round 5:

    coordinator   80/80 over a 10-enquiry-frame x 8-coordinated-clause cross
                  product; the SAME eight clauses without the coordinator, 0/80
    ``;`` / ``:`` 8/8 on colon-introduced quotation, 4/4 through a full stop
    newline       8/8, including a forwarded client email plus "what do they
                  want?" arming ``convert_document`` + ``pdf_arrange`` into
                  ``session_allow`` with no approval card

§1b, §2b and §6 are the rows that were missing. §6 also keeps the two leaks this
round did NOT close, asserted to STILL leak, so the honest list cannot decay.

TWO HOLES CLOSED ALONGSIDE IT, both measured:

* THE WORKBOOK-AS-OBJECT CLASS. Round 4 took ``excel_edit`` off the bare
  ``_XL_NOUNS`` rule (a noun is TOPIC, not INTENT) and cost sixteen genuine
  change requests — "clear the sheet", "add the new client to the workbook",
  "sort the spreadsheet by client". Exactly ONE of the sixteen had a failing
  test, and it had been failing since round 4 landed:
  ``tests/test_brain_reach_v1173.py::
  test_a_store_noun_in_passing_never_evicts_the_sentences_own_tool`` on "our
  records show a $500 payment; add it to the spreadsheet and total by client".
  Two new rules (destination, direct object) recover the class in imperative
  position.
* ``redact_pii``. An auto-armable FILE mutator that NOTHING suppressed: its rule
  fires on the bare noun "pii", so "please do not redact the pii in this
  return", "why did you redact the ssn?" and "don't redact anything" all armed
  it. Round 4's guard never even ran on it — ``redact_pii`` was absent from
  ``_CHANGE_TOOLS``, and the pin test below justified only the two MEMORY
  writers while lumping ``redact_pii`` in with them.

EVERY ASSERTION HERE IS ON THE ARMED LIST the chat lane actually produces (via
``chat_turn._resolve_armed_tools``) or on ``select_auto_tools``' returned list —
never on a pattern string. A rule may be rewritten freely as long as the
behaviour holds.

ROUND 7 IS THIS FILE'S OWN AUDIT, and four of its five findings are things this
file did not test at all — each mutation-proven by breaking the mechanism and
watching all 196 tests stay green:

* THE EVENT LOOP. Round 6's newline branch backtracks QUADRATICALLY over a run
  of whitespace and it fronts fourteen rules. Measured through the real
  ``select_auto_tools``: 4,000 newlines took 17,127 ms against 7.4 ms for
  4,000 characters of prose. ``_resolve_armed_tools`` is a plain ``def`` called
  with no ``asyncio.to_thread`` from both chat lanes (fixed in v1.196.0 and
  pinned by tests/test_arming_offload_v1196.py), so pasting a document with
  blank lines parks the whole daemon and the dashboard says "Daemon offline" —
  the v1.153.1 outage, reached from a regex. §7.
* THE CLAUSE BOUNDARY (§2c). ``clause = 0`` — round 4's message-wide negation
  scan — left 196 green. The module claimed the boundary was "pinned by that
  exact sentence", naming "Client can't open docx. Convert this to a pdf."; that
  row takes ``_position_allows``' early return for ``.``-branches and never
  reaches the clause computation at all.
* THE FORWARDED-EMAIL HEADERS (§1c). Deleting ``^(from|to|cc|…):``,
  ``-----original message``, ``fwd:`` and ``re:`` left 196 green. The one
  forwarded row in the corpus carried three redundant markers.
* RETRY EXHAUSTION (§8). Making ``_MAX_POSITION_RETRIES`` exhaustion AWARD
  instead of refuse left 196 green.
* AND THE GATE OVER-SUPPRESSED FAR MORE THAN IT DISCLOSED (§9). Round 6 said the
  cost was "an instruction that follows a question" and listed three rows; the
  real trigger was any quoting NOUN anywhere earlier plus the plain imperative
  "do this", and on a 25-row corpus of this practitioner's own requests SIXTEEN
  lost their verb — the entire capability gain of rounds 4-5, given back.
"""

from __future__ import annotations

import time
from types import SimpleNamespace as NS

import pytest

from iron_jarvis.daemon.chat_turn import _resolve_armed_tools
from iron_jarvis.tools import autoselect
from iron_jarvis.tools.autoselect import (
    _CHANGE_TOOLS,
    _position_allows,
    select_auto_tools,
)


class _Reg:
    """Every tool name resolves — this test is about SELECTION, not registration."""

    def get(self, name):  # noqa: ANN001
        return object()


def _armed(message: str, attachment: str | None = None) -> list[str]:
    """The tools the chat lane actually arms for this turn."""
    deps = NS(platform=NS(registry=_Reg()))
    body = NS(
        auto_tools=True,
        attachments=[attachment] if attachment else [],
        tools=[],
        messages=[NS(role="user", content=message)],
    )
    explicit, _auto = _resolve_armed_tools(deps, body)
    return explicit


# =============================================================================
# 1. LEAKS — a read-only turn must arm NO mutator
# =============================================================================
#: Plainly read-only turns. NONE may arm a member of ``_CHANGE_TOOLS``.
#:
#: The block marked ROUND-4 SURVIVORS is the reviewer's measured evidence that
#: the enquiry scan was the wrong discriminator: every one of them armed a
#: mutator WITH that guard in force, because its interrogative branch was
#: ``\A``-anchored (only a sentence-INITIAL question suppressed) and ``what``/
#: ``how`` were absent from it entirely.
READ_ONLY = [
    # -- ROUND-4 SURVIVORS: measured leaks that the enquiry scan did not catch --
    ("should I add a column for the tax rate?", "client_fees.xlsx"),
    ("the client asked whether we should delete page 2", "report.pdf"),
    ("I wonder if you could add a column", "client_fees.xlsx"),
    ("if you delete page 2, what happens to the numbering?", "report.pdf"),
    ("what happens when you extract pages 3-5 into a new pdf?", "report.pdf"),
    ("remind me how to update cell B2 to 500", "client_fees.xlsx"),
    ("the memo says to change the fee for Belmont to 3000", "client_fees.xlsx"),
    ("apparently they want to turn this into a pdf", "report.docx"),
    ("what does it mean to apply a layout to a workbook?", "client_fees.xlsx"),
    ("can excel add a column automatically?", "client_fees.xlsx"),
    ("she wants to know if you can convert this to a pdf", "report.docx"),
    # -- THE UNGATED MUTATORS: nothing in the module suppressed these at all --
    ("please do not redact the pii in this return", "return.pdf"),
    ("why did you redact the ssn?", "return.pdf"),
    ("don't redact anything", "return.pdf"),
    ("why did you resize the photo?", "chart.png"),
    # -- THE NAMED-CELL VERB LIST: `write` is not a cell-edit verb, and this
    #    sentence is genuinely IMPERATIVE, so no position test could save it.
    ("can you write a note about which cells changed to 500", "client_fees.xlsx"),
    # -- ROUND-4's OWN CASES, which must stay closed --
    ("what does this spreadsheet say?", "client_fees.xlsx"),
    ("what does this workbook say?", "client_fees.xlsx"),
    ("did they add a column?", "client_fees.xlsx"),
    ("don't add a column for the tax rate", "client_fees.xlsx"),
    ("we should never change the fee for Belmont to 3000 without asking",
     "client_fees.xlsx"),
    ("why did you delete page 2?", "report.pdf"),
    ("please do not delete page 2", "report.pdf"),
    ("explain how to delete page 2 from a pdf", "report.pdf"),
    ("does it convert this to a word document automatically?", "notes.txt"),
    ("save these spreadsheets", "client_fees.xlsx"),
    ("make these pdfs the priority", "report.pdf"),
    # -- PLAIN READS, the floor: these must never have been near a mutator --
    ("read this pdf", "report.pdf"),
    ("summarize this", "report.pdf"),
    ("what do these fees add up to?", "client_fees.xlsx"),
    ("add up the fees in the spreadsheet", "client_fees.xlsx"),
    ("thanks!", "client_fees.xlsx"),
    ("does this workbook have a column for the tax rate?", "client_fees.xlsx"),
    ("explain how the sheet is formatted", "client_fees.xlsx"),
    ("check whether the totals add up to 5000", "client_fees.xlsx"),
    ("review the workbook", "client_fees.xlsx"),
    ("open the sheet", "client_fees.xlsx"),
    ("compare the workbook to last year", "client_fees.xlsx"),
    ("was the fee for Belmont changed to 3000?", "client_fees.xlsx"),
    ("who deleted page 2 of this return?", "report.pdf"),
    ("what does 'clear the sheet' actually do?", "client_fees.xlsx"),
    ("the instructions say to rename the sheet to Q1", "client_fees.xlsx"),
    ("do not convert this to a pdf", "report.docx"),
    ("why would anyone sort the spreadsheet by client?", "client_fees.xlsx"),
]


#: =============================================================================
#: 1b. THE THREE POSITIONAL FAMILIES (v1.196.0 round 6)
#: =============================================================================
#: THE ROWS THIS FILE DID NOT HAVE. Every one of them armed a mutator on round 5
#: and every one is a shape the 43 rows above structurally could not produce.
#: They run through the SAME test as §1 — a read-only turn arms no mutator — and
#: are listed separately only so the three families stay identifiable.
POSITIONAL_FAMILIES = [
    # -- (1) A COORDINATOR INSIDE AN ENQUIRY. One row per enquiry frame that
    #    leaked, because the frames differ in WHAT gives them away: a wh-word, a
    #    reported question, an inverted auxiliary, a negation that scopes over
    #    the conjunct, and the two hypothetical-polite forms ("I wonder if you
    #    could", "she wants to know if you can") that were live round-4 leaks and
    #    survived round 6's FIRST cut — measured, exactly those two frames were
    #    16 of the 80 remaining false arms and nothing else was.
    ("the client asked whether we should delete page 2 and add a column",
     "return.pdf"),
    ("why did you convert this to a pdf and delete page 2?", "report.docx"),
    ("should I clear the sheet and add a column for the tax rate?",
     "client_fees.xlsx"),
    ("did they merge these pdfs and rotate the pages?", "return.pdf"),
    ("I wonder if you could clear the sheet and add a column", "client_fees.xlsx"),
    ("she wants to know if you can convert this to a pdf and merge the pages",
     "report.docx"),
    ("what happens if you extract pages 3-5 into a new pdf and delete page 2?",
     "return.pdf"),
    ("the memo says to resize the photo and convert the image to png", "chart.png"),
    ("explain how to redact the pii and delete page 2", "return.pdf"),
    # A NEGATIVE IMPERATIVE SCOPES OVER ITS SECOND CONJUNCT. "do not X and Y" is
    # one instruction not to act; the `and` inherits it. This is the row that
    # forces the negation half of the gate to exist, and it is the row that makes
    # the CLAUSE boundary matter — see the pair of it in §2b.
    ("do not delete page 2 and add a column for the tax rate", "return.pdf"),
    # -- (2) A COLON OR SEMICOLON INTRODUCING SOMEONE ELSE'S WORDS --
    ("the note ended with: redact the pii in this return", "return.pdf"),
    ("his email said: delete page 2 from this pdf", "return.pdf"),
    ("the instruction reads: convert this to a word document", "notes.txt"),
    ("the checklist item is: clear the sheet", "client_fees.xlsx"),
    ("she wrote; add a column for the tax rate", "client_fees.xlsx"),
    ("quote from the memo: resize the photo to 800px", "chart.png"),
    ("the ticket says: extract pages 3-5 into a new pdf", "return.pdf"),
    # ...and the same shape closed by a FULL STOP rather than a colon. Round 5's
    # comment justified `[.;:!?]` with "Client can't open docx. Convert this to a
    # pdf." and never measured the reporting clause that ends the same way.
    ("the memo says what to do. delete page 2 from this pdf", "return.pdf"),
    ("I asked him about the layout. convert this to a pdf", "report.docx"),
    ("they wondered about the format. clear the sheet", "client_fees.xlsx"),
    # -- (3) A NEWLINE, A BULLET OR A NUMBERED STEP INSIDE PASTED TEXT --
    #    THE ROW THAT HELD THE RELEASE: a forwarded client email, pasted whole,
    #    with the user's own question after it. Forwarding a client email and
    #    asking what it says is a routine turn for the accountant who runs this
    #    app daily, and it armed two file writers with no approval card.
    ("-----Original Message-----\n"
     "From: john@belmontllc.com\n"
     "Sent: Tuesday, August 19, 2026 9:14 AM\n"
     "To: Valentino\n"
     "Subject: RE: 2023 return\n"
     "\n"
     "Hi Valentino,\n"
     "\n"
     "A couple of things on the draft you sent over:\n"
     "\n"
     "- delete page 2, the old cover sheet is wrong\n"
     "- convert this to a pdf before you send it back\n"
     "\n"
     "Thanks,\n"
     "John\n"
     "\n"
     "what do they want?", "return.pdf"),
    # The same email with its headers stripped, which is what a paste out of a
    # mail client's reading pane actually looks like. Only the SALUTATION marks
    # it as correspondence — see the note on that alternative in `_ENQUIRY`.
    ("Hi Valentino,\n"
     "\n"
     "A couple of things on the draft:\n"
     "\n"
     "- delete page 2, the old cover sheet is wrong\n"
     "- convert this to a pdf before you send it back\n"
     "\n"
     "Thanks,\n"
     "John", "return.pdf"),
    # A NUMBERED LIST someone else wrote, quoted for review.
    ("the prior accountant left this checklist:\n"
     "1. extract pages 3-5 into a new pdf\n"
     "2. merge these pdfs\n"
     "3. redact the pii in the K-1\n"
     "is any of that still relevant?", "return.pdf"),
    ("here is what the client wrote:\n"
     "- delete page 2\n"
     "- add a column for the tax rate\n"
     "what do they want?", "client_fees.xlsx"),
    ("their email:\n1. convert this to a pdf\n2. redact the pii in this return",
     "return.pdf"),
    ("the memo lists:\n* clear the sheet\n* sort the spreadsheet by client",
     "client_fees.xlsx"),
    ("he asked:\ndelete page 2 from this pdf", "return.pdf"),
    ("notes from the call:\nresize the photo to 800px", "chart.png"),
]


#: =============================================================================
#: 1c. A PASTE THAT ANNOUNCES ITSELF WITH HEADERS AND NOTHING ELSE (round 7)
#: =============================================================================
#: THE MARKERS THAT HAD NO TEST. ``_ENQUIRY``'s email furniture — the header
#: block, ``-----Original Message-----``, ``Fwd:``, ``Re:`` — is called "the
#: single highest-value entry in the set" in the module, and deleting ALL FOUR
#: left this file's 196 tests green. The reason is in §1b: the one forwarded row
#: there carries a salutation AND a ``Subject:`` AND the word "what", so any two
#: of the three can be removed without anything noticing.
#:
#: These rows carry ONE marker each, which is what a paste out of a mail client
#: actually looks like when the user adds no commentary at all. Each was measured
#: arming the named mutator with its marker deleted.
HEADER_ONLY = [
    ("From: john@belmontllc.com\nSubject: 2023 return\n\ndelete page 2",
     "return.pdf"),
    ("Fwd: 2023 return\n\ndelete page 2 and convert this to a pdf", "return.pdf"),
    ("Re: draft\n\nclear the sheet", "client_fees.xlsx"),
    ("-----Original Message-----\ndelete page 2", "return.pdf"),
    # The header block alone, no body lead-in, no greeting, no question.
    ("To: Valentino\nCc: audit@firm.com\nSent: Tuesday\n\nclear the sheet",
     "client_fees.xlsx"),
]


@pytest.mark.parametrize(("message", "attachment"), HEADER_ONLY)
def test_a_header_only_paste_is_recognised_as_correspondence(message, attachment):
    armed = _armed(message, attachment)
    leaked = sorted(set(armed) & _CHANGE_TOOLS)
    assert not leaked, (
        f"{message!r} armed {leaked}. An email header block is the ONLY thing "
        f"marking this text as somebody else's, and every one of these rows "
        f"reaches its verb through the newline branch. Armed: {armed}"
    )
    assert armed, f"{message!r} armed nothing at all — readers should still arm"


@pytest.mark.parametrize(("message", "attachment"), READ_ONLY + POSITIONAL_FAMILIES)
def test_a_read_only_turn_arms_no_mutator(message, attachment):
    armed = _armed(message, attachment)
    leaked = sorted(set(armed) & _CHANGE_TOOLS)
    assert not leaked, (
        f"{message!r} with {attachment} armed the mutator(s) {leaked}. An armed "
        f"name is session_allow with no approval card, so this is a silent grant "
        f"on a question. Full armed list: {armed}"
    )
    # NOT VACUOUS: the turn is still useful — the readers armed normally, so this
    # cannot pass by the whole arming pass having quietly died.
    assert armed, f"{message!r} armed nothing at all — readers should still arm"


# =============================================================================
# 2. OVER-SUPPRESSION — a real change request must reach its verb
# =============================================================================
#: The SELF-EXPLAINING REQUEST is the shape round 4 killed and the reason the
#: discriminator had to be inverted: a negated clause explaining WHY sat in the
#: same message as the imperative, and negation-anywhere revoked the tool. All
#: four of the first block were measured SUPPRESSED under the round-4 guard.
WANTS_CHANGE = [
    # -- the self-explaining request (round 4 killed every one of these) --
    ("delete page 2, it's not needed", "report.pdf", "pdf_arrange"),
    ("convert this to a pdf, not a docx", "report.docx", "convert_document"),
    ("Client can't open docx. Convert this to a pdf.", "report.docx",
     "convert_document"),
    ("update cell B2 to 500, the old value isn't right", "client_fees.xlsx",
     "excel_edit"),
    ("merge these pdfs, the client doesn't want three files", "a.pdf",
     "pdf_arrange"),
    ("rename the sheet to Q1, it isn't called that yet", "client_fees.xlsx",
     "excel_edit"),
    ("add a column for the tax rate, we never had one", "client_fees.xlsx",
     "excel_edit"),
    ("please add a row for Belmont and don't touch the others", "client_fees.xlsx",
     "excel_edit"),
    # -- THE WORKBOOK-AS-OBJECT CLASS, confirmed lost when round 4 took
    #    `excel_edit` off the bare `_XL_NOUNS` rule. Sixteen sentences, ONE
    #    failing test. The first group is DESTINATION (something goes into the
    #    workbook), the second is DIRECT OBJECT (the workbook is what changes).
    ("add the new client to the workbook", "client_fees.xlsx", "excel_edit"),
    ("put the payment in the spreadsheet", "client_fees.xlsx", "excel_edit"),
    ("enter these fees in the workbook", "client_fees.xlsx", "excel_edit"),
    ("copy last year's numbers into the spreadsheet", "client_fees.xlsx",
     "excel_edit"),
    ("write the totals into the workbook", "client_fees.xlsx", "excel_edit"),
    ("our records show a $500 payment; add it to the spreadsheet and total by client",
     "client_fees.xlsx", "excel_edit"),
    ("clear the sheet", "client_fees.xlsx", "excel_edit"),
    ("delete the sheet", "client_fees.xlsx", "excel_edit"),
    ("rename the sheet to Q1", "client_fees.xlsx", "excel_edit"),
    ("sort the spreadsheet by client", "client_fees.xlsx", "excel_edit"),
    ("format the workbook properly", "client_fees.xlsx", "excel_edit"),
    ("fix the formulas in the sheet", "client_fees.xlsx", "excel_edit"),
    ("tidy up this spreadsheet", "client_fees.xlsx", "excel_edit"),
    ("fill in the spreadsheet with these numbers", "client_fees.xlsx", "excel_edit"),
    ("update the spreadsheet with the new fees", "client_fees.xlsx", "excel_edit"),
    ("correct the workbook", "client_fees.xlsx", "excel_edit"),
    # -- round 4's own seven, which must stay reachable --
    ("turn this into a pdf", "summary.docx", "convert_document"),
    ("convert this to a word document", "notes.txt", "convert_document"),
    ("add a column for the tax rate", "client_fees.xlsx", "excel_edit"),
    ("change the fee for Belmont to 3000", "client_fees.xlsx", "excel_edit"),
    ("update cell B2 to 500", "client_fees.xlsx", "excel_edit"),
    ("extract pages 3-5 into a new pdf", "report.pdf", "pdf_arrange"),
    ("delete page 2 from this pdf", "report.pdf", "pdf_arrange"),
    ("rewrite this as a formal letter and save it", "summary.docx", "write_document"),
    # -- the other gated mutators, from the ungated side --
    ("redact the pii in this return", "return.pdf", "redact_pii"),
    ("resize the photo to 800px", "chart.png", "image_resize"),
]


@pytest.mark.parametrize(("message", "attachment", "verb"), WANTS_CHANGE)
def test_a_real_change_request_still_reaches_its_verb(message, attachment, verb):
    armed = _armed(message, attachment)
    assert verb in armed, (
        f"{message!r} with {attachment} did NOT arm {verb} — the consent guard "
        f"must not be bought at the price of the feature. Armed: {armed}"
    )


#: A POLITE REQUEST IS STILL A REQUEST, and this is the gate's own biggest risk.
#: ``can``/``could``/``would``/``will`` + ``you``, and ``please``/``kindly``, are
#: IMPERATIVE POSITIONS on purpose: "can you turn this into a pdf?" is an
#: instruction, and a gate that swallowed it would break the ordinary way people
#: ask for work while looking perfectly safe in every other test.
#:
#: ``you`` IS REQUIRED and that is the whole precision of the modal branch —
#: "can EXCEL add a column automatically?" and "if YOU COULD add a column" are
#: enquiries and are pinned as leaks in §1 above.
POLITE = [
    ("can you turn this into a pdf?", "summary.docx", "convert_document"),
    ("could you delete page 2 from this pdf?", "report.pdf", "pdf_arrange"),
    ("would you convert this to a word document?", "notes.txt", "convert_document"),
    ("please update cell B2 to 500", "client_fees.xlsx", "excel_edit"),
    ("could you please clear the sheet?", "client_fees.xlsx", "excel_edit"),
    ("I need you to delete page 2", "report.pdf", "pdf_arrange"),
    ("go ahead and add a column for the tax rate", "client_fees.xlsx", "excel_edit"),
    ("ok, sort the spreadsheet by client", "client_fees.xlsx", "excel_edit"),
    ("read the file and then delete page 2", "report.pdf", "pdf_arrange"),
    ("first convert this to a pdf, then email it to me", "report.docx",
     "convert_document"),
]


@pytest.mark.parametrize(("message", "attachment", "verb"), POLITE)
def test_a_polite_or_coordinated_request_is_not_mistaken_for_a_question(
    message, attachment, verb
):
    armed = _armed(message, attachment)
    assert verb in armed, (
        f"{message!r} did NOT arm {verb}. 'can you'/'could you'/'please', a "
        f"leading discourse marker and a coordinated second instruction are all "
        f"IMPERATIVE POSITIONS; if any is dropped the ordinary way people ask "
        f"for work stops working. Armed: {armed}"
    )


#: =============================================================================
#: 2b. THE OTHER HALF OF ROUND 6 — the user's OWN coordinated and multi-line asks
#: =============================================================================
#: A gate on the three positional alternatives is a gate on the three ways this
#: user writes a MULTI-STEP request, so every family in §1b has a twin here. The
#: discrimination the gate has to get right is that a real two-step request uses
#: "and"/"then" exactly the way the false-arm family does — the difference is
#: never the coordinator, it is what stands BEFORE it.
#:
#: The last two rows are the ones that keep round 6 from becoming round 4 again.
#: A negation gates the branches that CONTINUE a clause, and a clause ends at a
#: full stop or a newline — so a justification in the PREVIOUS sentence must not
#: reach forward. Round 4 died on exactly this shape, message-wide.
USER_MULTI_STEP = [
    ("read the workbook and then add a column for the tax rate",
     "client_fees.xlsx", "excel_edit"),
    ("open it and delete page 2", "return.pdf", "pdf_arrange"),
    ("ok so first convert this to a pdf and then delete page 2", "report.docx",
     "convert_document"),
    ("ok so first convert this to a pdf and then delete page 2", "report.docx",
     "pdf_arrange"),
    ("delete page 2 and add a column for the tax rate", "client_fees.xlsx",
     "excel_edit"),
    ("hey, delete page 2 and add a column for the tax rate", "client_fees.xlsx",
     "pdf_arrange"),
    # THE SEMICOLON, kept for the live-ledger phrasing its own note in
    # `_imperative` cites. "records"/"show" are deliberately NOT enquiry markers
    # and this row is the reason.
    ("our records show a $500 payment; add it to the spreadsheet and total by client",
     "client_fees.xlsx", "excel_edit"),
    # THE USER'S OWN LISTS. Losing these was the acceptable cost if the newline
    # branch had had to be deleted outright; it did not, and these pin what was
    # kept.
    ("convert this to a pdf\ndelete page 2", "report.docx", "pdf_arrange"),
    ("1. convert this to a pdf\n2. delete page 2\n3. add a column for the tax rate",
     "client_fees.xlsx", "excel_edit"),
    ("- clear the sheet\n- sort the spreadsheet by client", "client_fees.xlsx",
     "excel_edit"),
    ("step 1: delete page 2\nstep 2: add a column for the tax rate", "return.pdf",
     "pdf_arrange"),
    # A NEGATION IN THE PREVIOUS CLAUSE DOES NOT REACH FORWARD.
    ("the client can't open the docx\nconvert this to a pdf\ndelete page 2",
     "report.docx", "convert_document"),
    ("Client can't open docx. Delete page 2 and convert this to a pdf.",
     "report.docx", "pdf_arrange"),
]


@pytest.mark.parametrize(("message", "attachment", "verb"), USER_MULTI_STEP)
def test_the_users_own_multi_step_message_is_not_read_as_a_quotation(
    message, attachment, verb
):
    armed = _armed(message, attachment)
    assert verb in armed, (
        f"{message!r} did NOT arm {verb}. The round-6 gate reads what stands "
        f"BEFORE a positional imperative; if it starts refusing the user's own "
        f"coordinated and multi-line requests it has become round 4 with extra "
        f"steps. Armed: {armed}"
    )


#: =============================================================================
#: 2c. THE CLAUSE BOUNDARY, pinned by rows that actually reach it (round 7)
#: =============================================================================
#: ``_position_allows`` computes a clause start and searches for a negation only
#: between it and the branch. Setting ``clause = 0`` turns that back into round
#: 4's message-wide scan — the discriminator this whole wave exists to replace —
#: AND ALL 196 TESTS STAYED GREEN.
#:
#: The module blamed the wrong sentence. It claimed the boundary was "pinned by
#: that exact sentence", meaning "Client can't open docx. Convert this to a
#: pdf." — but that row arrives on the ``.`` branch, and ``_position_allows``
#: answers a ``.``/``!``/``?``/newline branch with an EARLY RETURN before the
#: clause offset is ever computed. §2b's rows are all of that shape.
#:
#: ONLY A BRANCH THAT CONTINUES A CLAUSE reaches the computation: a coordinator,
#: a ``;`` or a ``:``. So the row that pins the boundary has to put a negated
#: sentence BEFORE a coordinated imperative. Each of these goes from armed to
#: EMPTY under ``clause = 0``.
NEGATION_IN_THE_PREVIOUS_SENTENCE = [
    ("It isn't right. Fix it and clear the sheet", "client_fees.xlsx", "excel_edit"),
    ("The old file is not usable. Open it and delete page 2", "return.pdf",
     "pdf_arrange"),
    ("Not a problem. Read the file and clear the sheet", "client_fees.xlsx",
     "excel_edit"),
    # A newline closes a clause too, and this row says so with a coordinator on
    # the far side of it.
    ("the docx won't open\nopen it and delete page 2", "return.pdf", "pdf_arrange"),
    # ...and the semicolon branch, which CONTINUES the clause: the negation is in
    # the previous SENTENCE, so it still must not reach forward.
    ("the old cover sheet is not right. we agreed on the new one; delete page 2",
     "return.pdf", "pdf_arrange"),
]


@pytest.mark.parametrize(
    ("message", "attachment", "verb"), NEGATION_IN_THE_PREVIOUS_SENTENCE
)
def test_a_negation_does_not_reach_past_the_end_of_its_clause(
    message, attachment, verb
):
    armed = _armed(message, attachment)
    assert verb in armed, (
        f"{message!r} did NOT arm {verb}. The negation sits in an EARLIER "
        f"clause, and a gate that lets it scope forward is round 4 again — the "
        f"discriminator that killed 25 real requests. Armed: {armed}"
    )


def test_the_negation_still_scopes_INSIDE_its_own_clause():
    """The other side of the same boundary, so §2c cannot be satisfied by
    deleting the negation test outright: within ONE clause a negation still
    reaches the coordinator that follows it."""
    for message in (
        "do not delete page 2 and add a column for the tax rate",
        "never clear the sheet and sort the spreadsheet by client",
        "don't open it and delete page 2",
    ):
        leaked = sorted(set(select_auto_tools(message, cap=99)) & _CHANGE_TOOLS)
        assert not leaked, f"{message!r} armed {leaked}"


#: THE DEONTIC FORMS — a request phrased ABOUT THE FILE rather than to the
#: assistant. Included because ``arm_for_task`` feeds the same scorer AGENT TASK
#: TEXT, which is routinely written this way, and because a gate that only knew
#: the second-person imperative would have quietly halved that lane.
#:
#: The negative half is what makes the branch safe: ``be`` must sit directly
#: against the modal, so "we should NEVER change the fee" and "SHOULD I add a
#: column?" are both refused, and the bare ``needs`` branch requires the verb to
#: follow immediately, so "who NEEDS TO delete page 2?" is refused too.
DEONTIC = [
    ("the pdf needs splitting", "pdf_arrange"),
    ("the scan should be rotated", "pdf_arrange"),
]


@pytest.mark.parametrize(("message", "verb"), DEONTIC)
def test_a_deontic_request_reaches_its_verb(message, verb):
    assert verb in select_auto_tools(message, cap=99), (
        f"{message!r} armed {select_auto_tools(message, cap=99)}"
    )


@pytest.mark.parametrize(
    "message",
    [
        "should the pdf be split?",
        "why was the pdf split?",
        "the pdf was split yesterday",
        "who needs to delete page 2?",
        "the scan should not be rotated",
    ],
)
def test_the_deontic_branch_does_not_swallow_the_question(message):
    leaked = sorted(set(select_auto_tools(message, cap=99)) & _CHANGE_TOOLS)
    assert not leaked, f"{message!r} armed {leaked}"


# =============================================================================
# 3. THE AGENT LANE MUST NOT LOSE CAPABILITY
# =============================================================================
def test_the_agent_lane_keeps_every_change_verb_a_task_asks_for(platform):
    """``agents/runtime.arm_for_task`` calls THIS SAME SELECTOR with no consent
    argument at all, and ``_WRITE_TIER`` there already stops a read-only roster
    from gaining a writer. Round 4's revoke-based scan therefore ran on the agent
    lane as pure capability loss for zero safety gain — undisclosed and untested.

    It is gone, and this is the proof from the agent side: a WRITING definition
    still gains the tool its task argues for, INCLUDING for tasks whose text
    contains the negation and interrogative markers round 4 scanned for. Each of
    these was measurably suppressed on the agent lane by that guard.
    """
    from iron_jarvis.agents.runtime import arm_for_task

    builder = ["read_file", "write_file", "edit_file", "list_files", "shell",
               "read_document", "write_document"]
    for task, tool in [
        ("delete page 2, it's not needed", "pdf_arrange"),
        ("convert the report to a pdf, not a docx", "convert_document"),
        ("the client can't open docx. convert the report to a pdf.",
         "convert_document"),
        ("fix the formulas in the sheet", "excel_edit"),
        ("add the new client to the workbook", "excel_edit"),
        ("redact the pii in the K-1", "redact_pii"),
        ("the pdf needs splitting", "pdf_arrange"),
    ]:
        armed = arm_for_task(platform, task, list(builder))
        assert armed[: len(builder)] == builder, "the roster rides unchanged"
        assert tool in armed[len(builder):], (
            f"agent task {task!r} lost {tool}; added {armed[len(builder):]}"
        )


def test_the_agent_tier_gate_is_still_the_only_thing_stopping_a_reviewer(platform):
    """The other side: nothing here weakened ``_WRITE_TIER``. The same tasks a
    WRITING agent gains above must still add NO writer to the read-only REVIEWER
    roster.

    THE NON-VACUITY CHECK IS PER TASK AND NOT A BLANKET ONE, because a blanket
    one is FALSE and going red taught me so: "delete page 2, it's not needed"
    adds exactly nothing to this roster, since ``pdf_arrange`` is the only tool
    it scores that the reviewer does not already hold and the tier gate removes
    it. That is the gate working, not a failure — so the sentences that must
    still ADD something are named, and the pdf one is pinned as adding nothing
    ON PURPOSE."""
    from iron_jarvis.agents.runtime import _WRITE_TIER, arm_for_task

    reviewer = ["read_file", "list_files", "grep", "read_document", "extract_pdf",
                "excel_read", "excel_profile", "excel_query", "web_search",
                "web_fetch", "recall", "history_search", "view_image"]
    for task, still_adds in (
        ("fix the formulas in the sheet", "excel_formula_check"),
        ("add the new client to the workbook", "file_search"),
        ("redact the pii in the K-1", "redact_scan"),
        ("clear the sheet", "file_search"),
        # The tier gate removes the ONLY tool this task scores that the reviewer
        # lacks, so the roster is returned unchanged. Named, not glossed.
        ("delete page 2, it's not needed", None),
    ):
        armed = arm_for_task(platform, task, list(reviewer))
        assert armed[: len(reviewer)] == reviewer, "the roster rides unchanged"
        assert not (set(armed) & _WRITE_TIER), f"{task!r} armed {armed}"
        added = armed[len(reviewer):]
        if still_adds is None:
            assert added == [], f"{task!r} now adds {added}; re-measure this row"
        else:
            assert still_adds in added, (
                f"{task!r} added {added} — the gate must cost vocabulary, not "
                f"capability, so an empty add here is the wrong kind of green"
            )


# =============================================================================
# 4. NO CROWD-OUT — the readers keep their slots
# =============================================================================
def test_a_plain_read_turn_still_arms_the_READERS():
    """Both lanes cap at 6. ``read_document`` is the app's second most-used tool;
    a change-intent rule that pushed it off "read this pdf" would trade the
    busiest path in the product for a consent fix nobody asked to pay for."""
    armed = _armed("read this pdf and summarize it", "report.pdf")
    assert armed == ["read_document", "file_search", "extract_pdf"], armed


def test_an_xlsx_attachment_still_arms_the_excel_READ_verbs():
    """The wave's whole point (12 of 18 document tools had never run once), which
    a mutator promoted to weight 10 could displace under the same 6-cap."""
    armed = _armed("can you take a look at this?", "client_fees.xlsx")
    for verb in ("excel_profile", "excel_query", "excel_read", "read_document"):
        assert verb in armed, f"armed {armed}"
    assert len(armed) <= 6, armed


def test_the_new_workbook_rules_do_not_evict_the_readers():
    """The rules added this round award ``excel_edit`` at 10 — ABOVE
    ``excel_query``'s 9, which is the one lead this round deliberately moves. The
    module's round-4 note conceded the wrong door and left it: "'delete the
    sheet' still LEADS with ``excel_query``". A local model takes the tool at the
    top (the v1.174.0 evidence run), and the query engine cannot clear a sheet.

    THE LEAD IS NOT UNIVERSAL, and the exception is stated rather than dropped
    from the corpus: "sort the spreadsheet BY CLIENT" also fires the
    computed-figures rule (``by (?:client|month|...)``), which adds 5 to
    ``excel_query`` and takes it to 14. That is left alone deliberately — the
    sentence really does carry a group-by signal, re-weighting a rule this round
    did not touch would move sentences it was not chartered to move, and the
    editor is still armed and second. Pinned here so the exception is a decision
    and not drift.
    """
    for msg in ("clear the sheet", "delete the sheet", "correct the workbook",
                "add the new client to the workbook",
                "update the spreadsheet with the new fees",
                "tidy up this spreadsheet"):
        picked = select_auto_tools(msg)
        assert picked[0] == "excel_edit", f"{msg!r} armed {picked}"
        for keeper in ("excel_query", "excel_profile", "excel_read"):
            assert keeper in picked, f"{msg!r} evicted {keeper}: {picked}"

    grouped = select_auto_tools("sort the spreadsheet by client")
    assert grouped[0] == "excel_query" and "excel_edit" in grouped, grouped


def test_the_bridge_is_TEMPERED_so_prose_about_a_workbook_is_not_an_edit():
    """The destination rule crosses ``to``/``in``/``into``/``onto`` and REFUSES
    to cross the prepositions that relocate the verb's object.

    "write a note ABOUT the fees IN the spreadsheet" is a request for PROSE that
    happens to name a workbook, and with a plain ``.{0,30}`` bridge it armed
    ``excel_edit``. ``write_document`` is the RIGHT answer for it and is asserted
    too, so this pins the discrimination rather than merely the absence."""
    prose = select_auto_tools("write a note about the fees in the spreadsheet")
    assert "excel_edit" not in prose, prose
    assert "write_document" in prose, (
        "not vacuous — an imperative request to write a note must still reach a "
        f"writer; armed {prose}"
    )
    assert "excel_edit" in select_auto_tools("write the fees into the spreadsheet")


# =============================================================================
# 5. STRUCTURAL PINS
# =============================================================================
def test_a_bare_file_noun_is_topic_not_intent():
    """The narrower structural half of the round-4 fix, asserted on behaviour.

    Mentioning a spreadsheet must not arm its editor; asking to change one must.
    """
    assert "excel_edit" not in select_auto_tools("what does this spreadsheet say?")
    assert "excel_edit" not in select_auto_tools("summarize the workbook for me")
    assert "excel_edit" in select_auto_tools("change the fee for Belmont to 3000")


def test_the_phrasal_ADD_UP_does_not_collide_with_ADD_TO(platform):
    """``(?!\\s+up\\b)`` on the destination rule, pinned by behaviour.

    "add up the fees in the spreadsheet" is a COMPUTATION and "add the fees to
    the spreadsheet" is an edit; they differ by one word, both are everyday
    phrasings in this domain, and both are imperative. Without the lookahead the
    first arms the editor.
    """
    computing = select_auto_tools("add up the fees in the spreadsheet")
    assert "excel_edit" not in computing, computing
    assert "excel_query" in computing, (
        "not vacuous — the computation still reaches the engine that answers it"
    )
    editing = select_auto_tools("add the fees to the spreadsheet")
    assert "excel_edit" in editing, editing


def test_the_change_tool_set_and_the_write_tier_differ_ONLY_where_intended():
    """``_CHANGE_TOOLS`` is NOT a copy of ``agents/runtime._WRITE_TIER``, and the
    difference is deliberate in both directions. Pinned rather than asserted
    equal, so a future edit that widens either set has to come through here and
    state its case.

    ``_CHANGE_TOOLS`` answers one question: "may this turn arm something that
    CHANGES A FILE?" ``_WRITE_TIER`` answers a different one — which tools a
    read-only AGENT ROSTER may never gain — so it also holds the MEMORY writers
    ``ltm_append`` and ``remember_preference``. Those two are excluded here on
    their merits, not by oversight: they append to the long-term stores rather
    than to the user's files, and nothing about the shape of a request should
    stop the assistant taking a note.

    ``redact_pii`` USED TO BE EXCLUDED HERE AND IT WAS A DEFECT. Round 4's
    version of this test lumped it in with the memory writers and justified only
    them, which read as a decision and was not one: ``redact_pii`` writes a
    ``.redacted`` FILE, its rule fires on the bare noun "pii", and measured
    leaks followed — "please do not redact the pii in this return", "why did you
    redact the ssn?", "don't redact anything" all armed it. It is in the set now
    and its rule is split so only the WRITER is position-gated; the read-only
    ``redact_scan`` still answers the question.

    The other direction is a KNOWN GAP, not an oversight, and it has THREE
    members: ``convert_document``, ``image_convert`` and ``image_resize`` all
    write a new file and are all absent from ``_WRITE_TIER``. Each is
    IRREVERSIBLE, so the forward guard that catches REVERSIBLE tools does not see
    them, and a read-only agent roster can still gain all three. Pre-existing,
    out of scope, recorded in docs/TODO.md — and listed here by NAME so that
    closing one fails this test and forces the list to be updated rather than
    quietly shrunk.
    """
    from iron_jarvis.agents.runtime import _WRITE_TIER
    from iron_jarvis.tools.autoselect import AUTO_SAFE_TOOLS

    # Deliberately in _WRITE_TIER but NOT gated by imperative position.
    not_file_mutators = {"ltm_append", "remember_preference"}
    unexpected = (_WRITE_TIER & AUTO_SAFE_TOOLS) - _CHANGE_TOOLS - not_file_mutators
    assert not unexpected, (
        f"{sorted(unexpected)} are auto-armable writers the agent lane guards as "
        f"_WRITE_TIER, but no rule in autoselect requires imperative position to "
        f"award them. Either add them to _CHANGE_TOOLS and gate their rule, or "
        f"record here why not."
    )
    assert "redact_pii" in _CHANGE_TOOLS, (
        "redact_pii writes a file and its rule fires on a bare noun; taking it "
        "out re-opens three measured leaks — see this docstring"
    )

    # Deliberately gated here but absent from _WRITE_TIER.
    known_gap = {"convert_document", "image_convert", "image_resize"}
    unguarded = _CHANGE_TOOLS - _WRITE_TIER - known_gap
    assert not unguarded, (
        f"{sorted(unguarded)} are file mutators this module gates but the agent "
        f"lane does not treat as writers — a read-only agent roster can gain them"
    )


def test_every_gated_mutator_is_actually_gated(platform):
    """THE ANTI-VACUOUSNESS PIN, and the one assertion here that is about the
    module's SHAPE rather than a sentence.

    ``_CHANGE_TOOLS`` is a claim: "no rule awards one of these without imperative
    position". Every other test in this file samples that claim one sentence at a
    time, so a NEW rule added later — awarding, say, ``pdf_split`` off a bare
    noun — would sail through all of them. This drives every member: for each
    tool, take a sentence that DOES arm it, wrap it in a frame that removes the
    imperative, and require the tool to disappear.
    """
    reaching = {
        "write_document": "write a memo about the fees",
        "write_file": "write a python script to rename the files",
        "convert_document": "convert the report to a pdf",
        "excel_edit": "add a column for the tax rate",
        "excel_apply_spec": "apply this layout to the workbook",
        "pdf_arrange": "delete page 2 of the pdf",
        "pdf_split": "extract pages 3-5 into a new pdf",
        "image_convert": "convert the photo to png",
        "image_resize": "resize the photo to 800px",
        "redact_pii": "redact the pii in this return",
    }
    assert set(reaching) == set(_CHANGE_TOOLS), (
        f"_CHANGE_TOOLS changed; add a reaching sentence for "
        f"{sorted(set(_CHANGE_TOOLS) - set(reaching))} and drop "
        f"{sorted(set(reaching) - set(_CHANGE_TOOLS))}"
    )
    for tool, sentence in reaching.items():
        assert tool in select_auto_tools(sentence, cap=99), (
            f"the probe sentence for {tool} no longer reaches it: {sentence!r} "
            f"armed {select_auto_tools(sentence, cap=99)}"
        )
        for frame in ("why did you {}?", "the memo says to {}",
                      "explain how to {}", "do not {}",
                      "the client asked whether we should {}",
                      # ROUND 6. The five frames above use NONE of the three
                      # positional alternatives, so this pin — the file's only
                      # assertion about the module's SHAPE — could not see the
                      # family that held the release. One frame per alternative,
                      # driven across all ten mutators: 70 combinations.
                      "why did you open it and {}?",
                      "the client asked whether we should read the file and {}",
                      "his email said: {}",
                      "the note ended with; {}",
                      "here is what the client wrote:\n- {}\nwhat do they want?",
                      "the checklist they sent reads:\n1. {}\n2. send it back",
                      "do not {} and do not send it"):
            blunted = frame.format(sentence)
            assert tool not in select_auto_tools(blunted, cap=99), (
                f"{blunted!r} armed {tool} — that rule awards a file mutator "
                f"without requiring imperative position, so the consent gate "
                f"in attachment_rag.change_verbs_wanted opens on a question"
            )


# =============================================================================
# 6. WHAT ROUND 6 DID *NOT* CLOSE — kept as data so it cannot decay
# =============================================================================
#: NEVER SILENTLY DEGRADE (CLAUDE.md) applied to a consent gate's own reach. Two
#: leaks survive, both measured, and a file that pinned only the closed families
#: would read as "quoted text can no longer arm a mutator", which is not true.
#: Each row is ``(message, attachment, the mutator it STILL arms)``. If you close
#: one, DELETE ITS ROW — do not delete the test.
RESIDUAL_LEAKS = [
    # (a) A CONTEXT-FREE BRANCH INSIDE A QUOTATION. Round 6 gates the three
    #     POSITIONAL alternatives and deliberately leaves the seven context-free
    #     ones alone, because the words themselves are the evidence: "please
    #     <verb>" is an instruction wherever it appears. Quoted correspondence is
    #     full of polite instructions, so a reported "please update cell B2 to
    #     500" still arms the editor — the colon branch is gated on this exact
    #     sentence and `please` awards it anyway.
    #
    #     GATING `please` TOO WAS TRIED AND REJECTED, on the same measurement
    #     that killed round 4: the marker would have to sit anywhere earlier in
    #     the message, and "what's the best format? please convert this to a pdf"
    #     is one turn, one user, and a real request. The cost of closing this is
    #     a class of genuine asks; the cost of leaving it is a mutator armed on
    #     someone else's politeness. Recorded rather than decided quietly.
    ("the reviewer's comment was: please update cell B2 to 500",
     "client_fees.xlsx", "excel_edit"),
    # (b) A PASTE THAT ANNOUNCES ITSELF WITH NOTHING. The gate recognises quoted
    #     material by what precedes it — a reporting verb, a quoting noun, an
    #     email header, a salutation. A block pasted with no lead-in, no headers
    #     and no greeting carries none of those, and it is genuinely
    #     indistinguishable from the user's own instruction list (which §2b pins
    #     as still working). This is the honest floor of the approach, not an
    #     oversight: closing it means refusing the user's own lists.
    ("A couple of things on the draft:\n"
     "\n"
     "- delete page 2, the old cover sheet is wrong\n"
     "- convert this to a pdf before you send it back",
     "return.pdf", "pdf_arrange"),
    # (c) THE GATE IS A VOCABULARY, NOT A MECHANISM (round 7), and until now this
    #     file read as though family 1 — quoted correspondence — were closed. It
    #     is not, and it cannot be closed by more words: ``_ENQUIRY`` recognises
    #     an ENUMERATED list of reporting verbs, and a report can be written
    #     without any of them. Round 7 added the six the reviewer measured
    #     (REQUESTED, INSISTED, ADVISED, CONFIRMED, FLAGGED, WANTS/WANTED) plus
    #     "PER the engagement letter:", each of which was arming a mutator; over
    #     twenty-two frames of that shape ELEVEN still leak. Three are kept here
    #     as data so the claim in this file matches the behaviour of the module.
    #
    #     Each of these three fails for a DIFFERENT reason, which is why they are
    #     three rows and not one: an attribution with no verb at all, an
    #     adjectival report, and a reporting verb outside the list. Closing the
    #     class means recognising attribution structurally (a person/role noun
    #     plus a colon, a genitive plus a nominalization) rather than listing
    #     verbs — a different mechanism, and a bigger change than this round.
    ("the client's request: delete page 2", "return.pdf", "pdf_arrange"),
    ("he was adamant: delete page 2", "return.pdf", "pdf_arrange"),
    ("the partner decided we should open it and delete page 2", "return.pdf",
     "pdf_arrange"),
]


@pytest.mark.parametrize(("message", "attachment", "still_arms"), RESIDUAL_LEAKS)
def test_the_residual_leaks_are_STATED_not_understated(message, attachment, still_arms):
    assert still_arms in _armed(message, attachment), (
        f"{message!r} no longer arms {still_arms} — good; take it off "
        f"RESIDUAL_LEAKS and say in the module which mechanism closed it"
    )


# =============================================================================
# 7. THE EVENT LOOP — a pathological paste must not park the daemon
# =============================================================================
def test_a_whitespace_run_does_not_park_the_event_loop():
    """NOTHING BLOCKING RUNS ON THE EVENT LOOP (CLAUDE.md, v1.153.1), and round 6
    put a QUADRATIC regex in front of fourteen rules.

    ``_imperative``'s newline branch was ``\\n\\s*(?:[-*•]\\s*|\\d+[.)]\\s*)?``.
    Over a run of blank lines the greedy ``\\s*`` swallows the run, fails to find
    a verb, then gives back one whitespace character at a time — and no rule verb
    can begin with whitespace, so every retry is provably useless. Measured on
    this machine through the real ``select_auto_tools``:

        4,000-char prose            7.4 ms
        1,000 blank lines + text  906 ms      (122x)
        2,000 blank lines        4,080 ms     (551x)
        4,000 blank lines       17,127 ms   (2,314x)

    ``daemon/chat_turn._resolve_armed_tools`` USED TO BE a plain ``def`` called with no
    ``asyncio.to_thread`` from BOTH chat lanes, and ``change_verbs_wanted`` asks
    the scorer again — two to three passes per turn. A pasted document with blank
    lines therefore freezes every request in the app, and the dashboard renders
    that as "Daemon offline" (``lib/api.ts`` maps a dead fetch to status 0).
    Possessive quantifiers (``\\s*+``) took the same input to 191 ms with zero
    behavioural change anywhere in the corpus.

    THE ASSERTION IS A RATIO ON THE SAME MACHINE IN THE SAME RUN, never a
    wall-clock threshold: an absolute ``< 200 ms`` measures the hardware and goes
    red on a contended CI runner (the repo's own rule, learned the expensive
    way). ``min`` of several passes rather than a mean, because a scheduler
    hiccup can only make a sample slower and the minimum is the cleanest estimate
    of the work actually done.

    THE BOUND IS 100x AND THE MEASURED VALUE IS ~25x. That is four times the
    headroom for a noisy runner and twenty-three times below the pre-fix 2,314x,
    so the gap is wide in both directions. What it does NOT claim is linearity:
    each start offset in the run still scans forward over it, so the shape is
    still quadratic with a far smaller constant (4,000 newlines costs 3.9x what
    2,000 does). The 4,000-character input cap in ``select_auto_tools`` is what
    turns that into a bounded worst case, and this test uses exactly that worst
    case rather than a comfortable one.
    """
    prose = ("the quick brown fox jumps over the lazy dog " * 100)[:4000]
    pathological = "\n" * 4000

    def cost(msg: str, passes: int) -> float:
        select_auto_tools(msg)  # warm
        best = float("inf")
        for _ in range(passes):
            t = time.perf_counter()
            select_auto_tools(msg)
            best = min(best, time.perf_counter() - t)
        return best

    baseline = cost(prose, 5)
    worst = cost(pathological, 3)
    assert baseline > 0
    ratio = worst / baseline
    assert ratio < 100, (
        f"a 4,000-newline message costs {ratio:.0f}x a 4,000-character prose "
        f"message ({worst * 1000:.0f} ms vs {baseline * 1000:.1f} ms on this "
        f"machine). The possessive quantifiers in tools/autoselect._imperative "
        f"are what keep this at ~25x; without them it is ~2,300x and the daemon "
        f"is unreachable for seventeen seconds while a paste is scored."
    )
    # NOT VACUOUS: the pathological input really does run the gated rules, and
    # the possessive rewrite changed no answer.
    assert select_auto_tools("\n" * 3980 + "delete page 2") == select_auto_tools(
        "delete page 2"
    )


# =============================================================================
# 8. THE RETRY BOUND AND THE SHAPE OF THE GATE
# =============================================================================
def test_a_long_quoted_list_exhausts_the_retries_and_REFUSES():
    """``_MAX_POSITION_RETRIES`` says exhaustion REFUSES, "because the only
    positions left to try are ones the gate has already been declining". That is
    a safety claim, and making exhaustion AWARD instead left all 196 tests green.

    The input that runs the counter out is a LONG QUOTED LIST — twenty bullets
    under a reporting lead-in. Every newline is a refused position, the twelfth
    refusal ends the loop, and what happens next is the whole question. Measured:
    refuses today, arms ``pdf_arrange`` under the mutation.
    """
    message = "his email said:\n" + "\n".join(
        f"- delete page {i} from this pdf" for i in range(1, 21)
    )
    armed = _armed(message, "return.pdf")
    leaked = sorted(set(armed) & _CHANGE_TOOLS)
    assert not leaked, (
        f"a 20-bullet quoted list armed {leaked}. Exhausting the retry bound "
        f"must REFUSE: the positions it ran out on were all being declined, so "
        f"awarding on exhaustion turns the bound into a way THROUGH the gate — "
        f"and the longer the paste, the easier it gets. Armed: {armed}"
    )
    assert armed, "readers should still arm"
    # NOT VACUOUS: the same list SHORT enough not to exhaust the bound is refused
    # by the gate itself, so the long one is not passing for some other reason.
    short = "his email said:\n" + "\n".join(
        f"- delete page {i} from this pdf" for i in range(1, 4)
    )
    assert not (set(_armed(short, "return.pdf")) & _CHANGE_TOOLS)
    # ...and the bound is genuinely reachable: twenty bullets is more newline
    # positions than the counter allows.
    assert message.count("\n") > autoselect._MAX_POSITION_RETRIES


def test_position_allows_has_the_shape_the_module_describes():
    """A DIRECT pin on ``_position_allows``, because every other test in this file
    reaches it through a sentence — and a sentence can pass for the wrong reason.

    Three answers, and the ORDER of the tests is the contract.
    """
    # (1) A marker at or before the position refuses, whatever the branch.
    for branch in (". ", "; ", "and", "\n"):
        assert _position_allows("x", 5, branch, 5) is False
        assert _position_allows("x", 6, branch, 5) is False

    # (2) A branch that OPENS a clause is allowed unconditionally — it never
    #     reaches the negation test, which is why no `.`-shaped sentence can pin
    #     the clause boundary.
    msg = "Client can't open docx. Convert this to a pdf."
    assert _position_allows(msg, msg.index(". "), ". ", len(msg) + 1) is True
    for branch in (". ", "! ", "? ", "\n"):
        assert _position_allows("not this. x", 8, branch, 99) is True

    # (3) A branch that CONTINUES a clause consults the negation, CLAUSE-SCOPED.
    same = "do not delete page 2 and add a column"
    assert _position_allows(same, same.index(" and") + 1, "and", 99) is False
    earlier = "It isn't right. Fix it and clear the sheet"
    assert _position_allows(earlier, earlier.index(" and") + 1, "and", 99) is True
    semi = "this is not right; clear the sheet"
    assert _position_allows(semi, semi.index(";"), "; ", 99) is False


# =============================================================================
# 9. THE PRACTITIONER CORPUS — what the gate costs the person who uses this app
# =============================================================================
#: TWENTY-FIVE OF THIS USER'S OWN REQUESTS, and the measurement round 6 did not
#: make. Its disclosure said the gate cost "an instruction that follows a
#: question" and named three rows. Measured on the round-6 code, SIXTEEN of these
#: lost their verb — the same count as v1.195.0, i.e. the position gate was
#: handing back the entire capability gain of rounds 4 and 5.
#:
#: The trigger was not questions. It was any QUOTING NOUN anywhere earlier
#: ("read the MEMO and then delete page 2" refused while "read the FILE and then
#: delete page 2" was pinned green), plus the plain imperative "do this", which
#: ``_ENQUIRY``'s inverted-auxiliary branch was reading as a question.
#:
#: Rows are the practitioner speaking, never quoted material. If a row starts
#: failing, the gate has widened into the user's own vocabulary again.
PRACTITIONER = [
    # -- no quoting noun: the control group, green before and after --
    ("delete page 2 from this return", "return.pdf", "pdf_arrange"),
    ("open it and delete page 2", "return.pdf", "pdf_arrange"),
    ("clear the sheet and sort the spreadsheet by client", "client_fees.xlsx",
     "excel_edit"),
    ("read the file and then delete page 2", "return.pdf", "pdf_arrange"),
    ("convert this to a pdf, the client can't open docx", "report.docx",
     "convert_document"),
    ("Client can't open docx. Convert this to a pdf.", "report.docx",
     "convert_document"),
    ("please redact the pii in this return", "return.pdf", "redact_pii"),
    ("the old file is not usable. open it and delete page 2", "return.pdf",
     "pdf_arrange"),
    ("It isn't right. Fix it and clear the sheet", "client_fees.xlsx", "excel_edit"),
    # -- the user referring to a document they are working FROM. Twelve of these
    #    armed nothing on round 6; the bare noun was the whole reason.
    ("read the memo and then delete page 2", "return.pdf", "pdf_arrange"),
    ("check my notes and then clear the sheet", "client_fees.xlsx", "excel_edit"),
    ("quick note: clear the sheet", "client_fees.xlsx", "excel_edit"),
    ("look at the email I forwarded and convert it to a pdf", "report.docx",
     "convert_document"),
    ("pull up my notes on Belmont and change the fee for Belmont to 3000",
     "client_fees.xlsx", "excel_edit"),
    ("I took notes on the call; add the new client to the workbook",
     "client_fees.xlsx", "excel_edit"),
    ("first read the instructions, then delete page 2", "return.pdf",
     "pdf_arrange"),
    ("skim the memo and add a column for the tax rate", "client_fees.xlsx",
     "excel_edit"),
    ("start with the notes, then convert this to a pdf", "report.docx",
     "convert_document"),
    ("my note on this file is out of date. clear the sheet", "client_fees.xlsx",
     "excel_edit"),
    ("open the email attachment and delete page 2", "return.pdf", "pdf_arrange"),
    ("here are the ticket details. delete page 2", "return.pdf", "pdf_arrange"),
    # -- "do this" is an IMPERATIVE, and `do|does|did + this|that` was reading it
    #    as an inverted auxiliary.
    ("do this next: convert this to a pdf", "report.docx", "convert_document"),
    ("do this: add a column for the tax rate", "client_fees.xlsx", "excel_edit"),
    ("do that first, then delete page 2", "return.pdf", "pdf_arrange"),
    ("ok do this. clear the sheet and total by client", "client_fees.xlsx",
     "excel_edit"),
]


@pytest.mark.parametrize(("message", "attachment", "verb"), PRACTITIONER)
def test_the_practitioners_own_requests_reach_their_verb(message, attachment, verb):
    armed = _armed(message, attachment)
    assert verb in armed, (
        f"{message!r} did NOT arm {verb}. This is the user's OWN request, not a "
        f"report of somebody else's — a consent gate that eats it has taken the "
        f"feature back. Armed: {armed}"
    )


def test_a_quoting_noun_alone_is_not_evidence_but_a_REPORTING_CUE_is():
    """The discriminator round 7 installed, stated as a pair so neither half can
    be deleted quietly: the same noun, with and without a cue beside it."""
    assert "pdf_arrange" in select_auto_tools(
        "read the memo and then delete page 2", cap=99)
    assert "pdf_arrange" not in select_auto_tools(
        "the memo lists:\ndelete page 2 from this pdf", cap=99)
    assert "excel_edit" in select_auto_tools(
        "check my notes and then clear the sheet", cap=99)
    assert "excel_edit" not in select_auto_tools(
        "notes from the call:\nclear the sheet", cap=99)
    # FIRST person is not attribution; THIRD person is.
    assert "excel_edit" in select_auto_tools(
        "my notes are stale; clear the sheet", cap=99)
    assert "excel_edit" not in select_auto_tools(
        "their notes said; clear the sheet", cap=99)


# =============================================================================
# 10. THE WIDENED REPORTING VOCABULARY (round 7)
# =============================================================================
#: Round 6's ``_ENQUIRY`` knew ``said``/``asked``/``told``/``wrote`` and stopped.
#: Every frame here was MEASURED arming the named mutator into ``session_allow``
#: with no approval card. They are the vocabulary half of family 1; the half that
#: is NOT closed is stated as data in ``RESIDUAL_LEAKS`` (c).
QUOTED_BY_A_WIDER_VERB = [
    ("the client requested we delete page 2 and add a column", "client_fees.xlsx"),
    ("the client insisted we delete page 2 and add a column", "client_fees.xlsx"),
    ("the partner advised we delete page 2 and add a column", "client_fees.xlsx"),
    ("she confirmed we delete page 2 and add a column", "client_fees.xlsx"),
    ("he flagged that we delete page 2 and add a column", "client_fees.xlsx"),
    ("the client wants us to open it and delete page 2", "return.pdf"),
    ("the client wanted us to open it and delete page 2", "return.pdf"),
    ("per the engagement letter: delete page 2", "return.pdf"),
]


@pytest.mark.parametrize(("message", "attachment"), QUOTED_BY_A_WIDER_VERB)
def test_a_report_in_a_wider_vocabulary_is_still_a_report(message, attachment):
    armed = _armed(message, attachment)
    leaked = sorted(set(armed) & _CHANGE_TOOLS)
    assert not leaked, f"{message!r} armed {leaked}. Armed: {armed}"


def test_the_widened_vocabulary_did_not_eat_the_FIRST_PERSON_forms():
    """``wants``/``wanted`` are in; bare ``want`` is deliberately out, because
    "we want to ..." is the user's own sentence. Same shape as the module's
    ``said`` yes / ``say`` no rule."""
    assert "excel_edit" in select_auto_tools(
        "we want the fees checked and then clear the sheet", cap=99)
    assert "pdf_arrange" in select_auto_tools(
        "I need you to open it and delete page 2", cap=99)


# --- §7  THE GENITIVE BRANCH EXCLUDES CONTRACTIONS (v1.196.0 final review) ---
#
# `_ATTRIBUTION`'s `\w+['’]s` was written to mean "a third-person possessive"
# — "the reviewer's comment", "john's email" — but the mechanism is ANY word
# ending in 's, and English contracts exactly that way. So "here's the email.
# convert this to a pdf" read as ATTRIBUTED SPEECH and armed nothing, where
# v1.195.0 armed `convert_document`. Measured over a 540-frame grid of
# opener x quoting-noun x imperative-position x change-verb: 200 frames
# recovered, 0 non-attribution frames still regressed FOR THE LISTED OPENERS OVER THIS GRID
# (widen the verb axis and a listed opener can still regress — e.g. "here's the
# email; sort the spreadsheet by client" — but the opener-free control regresses
# identically, so that belongs to the quotation gate, not to this fix)
# (0/175; the five contractions NOT on the list still regress on the large majority
# (independently re-measured at 60-71% depending on the grid's verb axis; an
# earlier draft said 139/175, a figure no uniform grid can produce) of the
# same shape — see test_the_contraction_list_is_a_LIST_not_a_rule below, which is
# the limit, not a contradiction), and every genuine
# attribution stays suppressed.
#
# The comment described a mechanism the regex did not implement — the same
# class of defect this whole file exists to catch, one word narrower.

# EVERY ROW USES A REAL QUOTING NOUN (email/memo/note/message/comment/ticket/
# checklist/instructions), because that is what makes the row reach the genitive
# branch at all. The first draft of this list used "draft" and "file" — nouns the
# gate does not know — so four of five rows passed with the fix REVERTED and were
# decoration. Mutation-checked: reverting the exclusion turns every row below red.
CONTRACTION_OPENERS = [
    ("here's the email. convert this to a pdf", "notes.txt", "convert_document"),
    ("there's the memo; delete page 2", "report.pdf", "pdf_arrange"),
    ("that's the note. clear the sheet", "client_fees.xlsx", "excel_edit"),
    ("it's the message. delete page 2", "report.pdf", "pdf_arrange"),
    # NOT "what's" — `what` is a genuine wh-word and suppresses through the
    # enquiry branch, correctly, which would make this row fail for a reason
    # that has nothing to do with the genitive. NOT "everyone's" either: the
    # indefinite pronouns were removed from the exclusion list because they are
    # attribution, and `test_an_indefinite_pronoun_is_still_an_attribution`
    # asserts that from the other side.
    ("there's the checklist. redact the pii", "return.pdf", "redact_pii"),
]

GENUINE_ATTRIBUTION = [
    ("the reviewer's comment was: delete page 2", "report.pdf"),
    ("john's email. convert this to a pdf", "notes.txt"),
    ("her memo says: delete page 2", "report.pdf"),
    ("their email is confusing. delete page 2", "report.pdf"),
    ("per the engagement letter: redact the pii", "return.pdf"),
]


@pytest.mark.parametrize(("message", "attachment", "verb"), CONTRACTION_OPENERS)
def test_a_contraction_is_not_an_attribution(message, attachment, verb):
    armed = _armed(message, attachment)
    assert verb in armed, (
        f"{message!r} lost {verb}. A word ending in 's is not automatically a "
        f"possessive — 'here's'/'that's'/'let's' open the USER'S OWN sentence, "
        f"and suppressing them is a capability regression against v1.195.0 on "
        f"the exact turn shape this wave exists to serve. Armed: {armed}"
    )


@pytest.mark.parametrize(("message", "attachment"), GENUINE_ATTRIBUTION)
def test_a_real_genitive_still_reads_as_a_quotation(message, attachment):
    """The other direction — the exclusion must not disarm the gate itself."""
    leaked = sorted(set(_armed(message, attachment)) & _CHANGE_TOOLS)
    assert not leaked, (
        f"{message!r} is attributed speech and armed {leaked}"
    )


def test_the_contraction_list_is_a_LIST_not_a_rule():
    """HONEST LIMIT, asserted so it cannot quietly be claimed as more.

    Nothing here distinguishes a possessive from a contraction in general. The
    exclusion is an enumeration of openers, so a contraction outside it still
    reads as attribution. Pinned as data rather than described in prose, and
    if one of these ever starts working the assertion fails and the next reader
    updates the list instead of discovering the gap by accident.
    """
    # Measured: these contractions are NOT in the exclusion list and are still
    # read as attribution, so they lose their verb. "he's"/"she's" are genuinely
    # ambiguous (contraction vs. possessive) and were left out deliberately;
    # "everything's"/"today's"/"the file's" simply were not enumerated.
    # "delete page 2" is deliberately NOT the only verb here. For these openers
    # that verb is not a REGRESSION (v1.195.0 armed nothing for it either), so a
    # list using it alone would pin the limit while proving nothing about the
    # 504 frames the HONEST LIMIT block says are "the rest". "convert this to a
    # pdf" IS a regression for them, and is included for exactly that reason.
    for opener in ("he's", "she's", "everything's", "today's", "the file's"):
        for verb in ("delete page 2", "convert this to a pdf"):
            message = f"{opener} the email. {verb}"
            assert not (set(_armed(message, "report.pdf")) & _CHANGE_TOOLS), (
                f"{message!r} now arms — the exclusion list grew. That is fine, "
                f"but update this test and _STILL_OPEN rather than letting the "
                f"limit quietly change shape"
            )


@pytest.mark.parametrize("message", [
    "anyone's comment was: delete page 2",
    "everyone's comment was: delete page 2",
    "everyone's email. convert this to a pdf",
    "somebody's memo. clear the sheet",
])
def test_an_indefinite_pronoun_is_still_an_attribution(message):
    """The correction to the contraction fix.

    "anyone's comment was: X" is a QUOTATION of someone else's instruction and
    must behave exactly like "the reviewer's comment was: X". The first cut of
    the exclusion list treated the indefinite pronouns as contractions, which
    armed a `_WRITE_TIER` mutator into `session_allow` — no approval card — off
    reported speech, while the byte-identical named-person form stayed clean.
    """
    leaked = sorted(set(_armed(message, "report.pdf")) & _CHANGE_TOOLS)
    assert not leaked, (
        f"{message!r} is attributed speech and armed {leaked}. It must behave "
        f"like \"the reviewer's comment was: …\", which arms nothing."
    )


def test_the_comma_is_not_an_imperative_position():
    """A DESIGN LIMIT, recorded because it was twice mistaken for the genitive
    defect and is the shape most likely to be re-reported.

    A change verb after a COMMA or a DASH is not in imperative position, so
    "here's the email, convert this to a pdf" arms nothing. This is POSITIONAL,
    not attribution-specific — the control (same sentence, no quoting noun, no
    opener at all) misses identically, which is how it was told apart from the
    contraction bug. The period form of the same sentence arms.
    """
    for sep in (", ", " - ", " -- "):
        assert not (set(_armed(f"here's the email{sep}convert this to a pdf",
                               "notes.txt")) & _CHANGE_TOOLS)
    # The controls: nothing to do with attribution.
    assert not (set(_armed("here's the file, convert this to a pdf",
                           "notes.txt")) & _CHANGE_TOOLS)
    assert not (set(_armed("the file, convert this to a pdf",
                           "notes.txt")) & _CHANGE_TOOLS)
    # ...and the same sentence at a real imperative position DOES arm, so this
    # test cannot pass by the whole rule having died.
    assert "convert_document" in _armed(
        "here's the email. convert this to a pdf", "notes.txt")
