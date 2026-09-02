"""Auto tool selection for chat turns: read the request, arm what it needs.

The chat's "+" menu stays the explicit path; this module makes the DEFAULT
path seamless — when the dashboard sends ``auto_tools`` the daemon scores the
last user message (plus attachment names) against a small set of signal rules
and arms the matching tools for the turn, filling whatever slots the user's
own picks left free under the 6-tool cap.

Deliberately deterministic (regex scoring, no LLM call): zero added latency
before the first streamed token, works offline, and never hallucinates. The
failure mode is benign — an armed-but-unneeded tool is simply ignored by the
model, while the honest ``tools_used`` footer only ever reports what RAN.

Safety: candidates come exclusively from :data:`AUTO_SAFE_TOOLS` — read/write
file + document tools, read-only
web retrieval, local image tools, memory recall/notes, and past-conversation
search (read-only searches plus append-only note writes). The FILE + DOCUMENT
tools' WRITES are fs-policy-confined to the chat workspace; their READS are
deliberately not (`read_document` "may target ANY local path" and `list_folder`
lists any real folder — both go through `core/fs_policy.fs_read_ok`, which is
the gate that matters, and the user's tax documents live all over the disk).
This module used to say "fs-policy-confined to the chat workspace" flatly, which
was never true of the READERS and would have argued `list_folder` out of the set
for the wrong reason. Do NOT fix that by widening the confinement claim to the
WHOLE set instead: `ltm_append` (`ltm/tools.py`) appends to an Obsidian vault or
to Notion — outside any workspace, deliberately, and its own note in the set
below says exactly that. It is admissible here because it is APPEND-ONLY, never
because it is confined. Scope the sentence to the tools it is true of. NEVER
shell, edit_file, computeruse, MCP
(``mcp__*``), or paid generative media (``pixio_*``): those stay behind the
explicit "+" arming, which is the interactive consent the permission engine's
session grant is built on.
"""

from __future__ import annotations

import itertools
import re

#: Every tool this module may ever arm. Curated — see the module docstring.
AUTO_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "file_search",
        "read_file",
        "list_files",
        # v1.196.0: `list_folder` is the ONLY listing tool that can see the
        # user's REAL disk, and it was unreachable by automatic arming in BOTH
        # lanes (chat's Auto toggle and `agents/runtime.arm_for_task`, which
        # calls this same selector). `list_files` above resolves through
        # `safe_path(ctx.workspace, ...)` and chat's tool workspace is
        # `home/uploads` — a scratch area — so "what's in my Downloads folder"
        # armed a tool that STRUCTURALLY cannot answer it and the model then
        # reported an empty or irrelevant listing. That is the silent-wrong-
        # answer shape the whole module exists to avoid.
        #
        # It adds no tier: `ListFolderTool` is `Reversibility.READONLY`, one
        # level deep (subfolders are named, not descended), bounded by a 20k
        # entry cap + 10s deadline that it REPORTS when it bites, and gated by
        # the same `fs_read_ok` policy `read_document` uses — the app's second
        # most-used tool. A listing is strictly less disclosure than the file
        # read that is already auto-armable.
        "list_folder",
        "read_document",
        "write_document",
        "write_file",
        "extract_pdf",
        "convert_document",
        "web_search",
        "web_fetch",
        "view_image",
        "image_info",
        "image_convert",
        "image_resize",
        # Writes only a NEW .redacted copy (never modifies the source);
        # redact_scan is the read-only confirm-first step.
        "redact_pii",
        "redact_scan",
        # Page-level PDF work (merge/split/rotate/reorder/crop): writes only
        # NEW workspace files, never touches the source PDFs (inputs are
        # read-gated; outputs undoable).
        "pdf_arrange",
        "pdf_split",
        # Structured spreadsheet work (read anywhere; edits workspace-confined
        # + undoable). profile/query are engine-computed reads — exact figures
        # instead of model arithmetic (the local-model failure mode);
        # formula_check/sheet_spec/accounts_diff are read-only analysis.
        "excel_read",
        "excel_edit",
        "excel_profile",
        "excel_query",
        "excel_formula_check",
        "excel_sheet_spec",
        # v1.196.0: `excel_apply_spec` is the OTHER half of `excel_sheet_spec`
        # — capture a sheet's structure, then reproduce it — and it was the
        # inconsistency this set's own omissions note called out. It is the
        # SAME TIER as `excel_edit` two lines up, on all three facts that put
        # `excel_edit` here: it resolves its target through
        # `safe_path(ctx.workspace, ...)` (workspace-confined),
        # `Reversibility.REVERSIBLE` with a real pre-image (`capture_undo`
        # spills the prior bytes + sha256, `revert` restores them), and
        # `core/config` defaults it to "allow". "It writes" was therefore never
        # the exclusion rule in force here — `excel_edit` writes — and the
        # argument that DOES keep `batch_documents` out below (no honest undo)
        # does not touch this tool.
        #
        # LANDED AS A PAIR with `agents/runtime._WRITE_TIER` (see the note
        # there). Adding it here ALONE would hand a read-only REVIEWER/SUPERVISOR
        # definition a workbook writer off its task text, because that gate is
        # maintained by hand in another module and every assertion in
        # `tests/test_agent_auto_arm_v1178.py` is phrased as "no member of
        # `_WRITE_TIER`". `tests/test_autoselect_gaps_v1196.py` holds the guard
        # that goes red if the two ever drift apart again.
        "excel_apply_spec",
        "excel_accounts_diff",
        # Code Lab reuse (v1.97.0): READ-ONLY prior-art lookup — find a script
        # already written for this problem and read its source. Deliberately
        # NOT code_run, which executes saved code and belongs with shell behind
        # explicit "+" arming (see the module docstring's safety rule).
        "code_search",
        "code_load",
        # Memory (v1.141.0): read-only recall + append-only note writes. Chat
        # was push-only (grounding decided what the model saw); these give it
        # PULL: search every store on demand, save a note, record a
        # preference. Nothing here deletes or edits existing memory — appends
        # are strictly additive. Undo: markdown-dir stores (brain/Obsidian)
        # revert cleanly; an append to an external store (Notion/cloud) is
        # journaled reversible=False — an honest cannot-undo, still additive
        # (see ltm/tools.py LTMAppendTool.capture_undo).
        "recall",
        "ltm_search",
        "ltm_append",
        "remember_preference",
        # History search (v1.142.0): READ-ONLY ranked search over the user's own
        # past conversations. Same tier as recall — it only reads what was
        # already said in this app, writes nothing, and leaves the machine never.
        "history_search",
        # Saved workflows (v1.170.0): READ-ONLY discovery — name/description/
        # step count/project pin of what the user already saved. Deliberately
        # NOT workflow_run: starting a run spawns multi-minute agent work with
        # real side effects, so it stays behind explicit arming + its "ask"
        # gate (the same tier split as code_search vs code_run).
        "workflow_list",
        # The Guide's lookups (v1.224.0): READ-ONLY searches over the app's
        # own docs/catalogs and the user's things in this install. Chat can
        # answer "where is my…" / "how does this page work" from the stores
        # instead of from memory of the conversation.
        "guide_search",
        "app_search",
        "app_status",
    }
)

#: DELIBERATE OMISSIONS (v1.196.0). Three document tools were audited against
#: this set; ONE was left out on its merits and is recorded here by name, so the
#: next reader files it as a decision and not as the oversight `list_folder`
#: above was. (The audit's other two both landed: `list_folder` above, and
#: `excel_apply_spec` — which this note used to argue was "the inconsistency it
#: looks like" and to leave for a paired change. That pairing landed; the
#: reasoning now sits next to the name in the set above and in
#: `agents/runtime._WRITE_TIER`, which is where each half is maintained.)
#:
#: * ``batch_documents`` — the tool built for "process these 15 client
#:   documents", and the most tempting addition in the module. It stays out on
#:   TWO independent grounds, either of which is sufficient:
#:     (a) COST. It fans out ONE model call per document (default 25, cap 100)
#:         plus a synthesis pass. The module docstring's "the failure mode is
#:         benign — an armed-but-unneeded tool is simply ignored" is the whole
#:         licence for auto-arming, and it is FALSE here: if the model bites,
#:         an unattended sentence becomes dozens of provider round-trips. That
#:         is the same argument that keeps `pixio_*` out.
#:     (b) IRREVERSIBILITY. Every writer in the set above is either reversible
#:         (`excel_edit`, `write_document`) or additive-only (`redact_pii` and
#:         `convert_document` write a NEW file; `ltm_append` appends).
#:         `BatchDocumentsTool` deliberately keeps the IRREVERSIBLE default and
#:         says why in its own class comment ("there is no single pre-image an
#:         undo could honestly restore"). It would be the first member of this
#:         set with no honest undo, so "it writes" is not the exclusion rule in
#:         force here — "it cannot be taken back" is.
#:   Both of the user's sentences are still served, by DIFFERENT rules — worth
#:   separating, because they are not the same ask: "go through everything in
#:   that folder" names a directory and arms `list_folder` + `file_search` +
#:   `read_document` + `list_files`, while "process these 15 client documents"
#:   names none and arms `file_search` + `read_document` off the anaphor rule
#:   below. Either way the model can find and read the documents one at a time.
#:   `batch_documents` remains one click away in the "+" menu, which is the
#:   interactive consent its cost profile deserves.

_DOC_EXT_RX = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|csv|tsv|txt|md|rtf|json|log)$", re.IGNORECASE
)
_IMG_EXT_RX = re.compile(r"\.(png|jpe?g|gif|webp|bmp|tiff?)$", re.IGNORECASE)

_URL_RX = re.compile(r"https?://\S+", re.IGNORECASE)
# Windows (C:\...) or POSIX-looking absolute paths typed into the message.
_PATH_RX = re.compile(r"(?:[A-Za-z]:\\[^\s\"']+|(?<!\S)/(?:[\w.-]+/)+[\w.-]+)")

#: A bare SOURCE/CONFIG FILENAME typed into the message (v1.210.0): "fix the
#: bug in main.py", "open Cargo.toml". `_PATH_RX` needs a separator, so a lone
#: filename — the way people actually name a file when they are standing in
#: its folder — scored NOTHING and a coding request armed no reader at all
#: (measured: "fix the bug in main.py" -> []).
#:
#: CONSERVATIVE ON PURPOSE, two ways:
#:  * The extension comes from an EXPLICIT allowlist of code/config suffixes —
#:    never a generic `\.\w{1,4}` — because a dotted token is just as often a
#:    DOMAIN, and `com`/`net`/`org`/`co`/`io`/`ai` are deliberately not in the
#:    list: "check anthropic.com for the docs" must not read as a filename.
#:    (`sh`/`md`/`rs` are ccTLDs too, but "script.sh"/"NOTES.md"/"main.rs"
#:    are overwhelmingly files in a message typed at a local assistant, while
#:    a bare two-letter-ccTLD domain with no scheme is a rarity worth losing.)
#:  * The stem must start with a letter or underscore, the token must stand
#:    alone (no preceding `/`, `\\`, `.` or word char — so URL path segments
#:    and e-mail hosts stay out), and nothing dotted may FOLLOW the suffix
#:    ("main.py.bak" is not a .py file; "main.py." at sentence end still is).
_CODE_FILE_RX = re.compile(
    r"(?<![\w.\-/\\])[A-Za-z_][\w.\-]*"
    r"\.(?:py|tsx|ts|jsx|js|json|toml|yaml|yml|rs|go|java|cs|cpp|c|h|rb|php|"
    r"sh|ps1|md|txt|cfg|ini)"
    r"(?!\w)(?!\.\w)",
    re.IGNORECASE,
)

#: The spreadsheet NOUNS, extracted in v1.196.0 so the two rules that need them
#: cannot drift. ONE definition is what makes the apply-spec rule's safety claim
#: STRUCTURAL instead of coincidental: because that rule requires a noun from
#: THIS list, every sentence that arms the mutating ``excel_apply_spec`` is by
#: construction a sentence the spreadsheet rule below also fires on — so it can
#: never reach a message where the rest of the excel family (``excel_query``,
#: ``excel_profile``, ``excel_read``, and the equally-mutating ``excel_edit``)
#: scores nothing. Written out separately, the two lists drifted on the first
#: draft (``xlsm`` and a plural ``sheets`` were in one and not the other), and
#: that difference alone was enough to break the invariant.
_XL_NOUNS = r"excel|xlsx|spreadsheet|workbook|worksheet|\bsheet\b|\bcells?\b|formulas?|pivot"

#: A short bridge from a VERB to its OBJECT that a preposition may not cross
#: (v1.196.0 round 4). Used by the page rules below, where the difference
#: between the two readings is the whole precision of the rule: "extract the
#: first two pages" acts ON the pages, while "extract the text FROM page 3" acts
#: on something the pages merely CONTAIN — the first wants a new PDF, the second
#: wants a reader, and only the preposition tells them apart. Written as a
#: tempered span rather than a word count because the modifiers vary in length
#: ("delete the last two pages") while the preposition is a single reliable
#: marker. Kept as ONE constant so both page rules ask the same question.
_NO_PREP = r"(?:(?!\b(?:from|of|in|on|about|for|within|inside)\b)[^.!?]){0,20}?"

#: The members of :data:`AUTO_SAFE_TOOLS` that CHANGE A FILE — write one, edit
#: one in place, rearrange its pages, or emit a redacted copy. Kept here rather
#: than imported from ``agents/runtime._WRITE_TIER`` because ``tools/`` must not
#: depend on ``agents/``; ``tests/test_change_intent_guard_v1196.py::
#: test_the_change_tool_set_and_the_write_tier_differ_ONLY_where_intended`` pins
#: the two in step and states every difference by name.
#:
#: NOT "every writer in the set". ``ltm_append`` and ``remember_preference``
#: write too, and are deliberately absent: they append to the long-term stores,
#: not to the user's files, and nothing about the shape of a request should stop
#: the assistant taking a note. The docstring here used to claim this set WAS
#: "the MUTATING members of AUTO_SAFE_TOOLS", which was false of those two and
#: of ``redact_pii`` (which is now IN — see :func:`_imperative`).
_CHANGE_TOOLS: frozenset[str] = frozenset({
    "write_document", "write_file", "convert_document", "excel_edit",
    "excel_apply_spec", "pdf_arrange", "pdf_split", "image_convert",
    "image_resize",
    # v1.196.0 round 5. `redact_pii` writes a NEW `.redacted` file and is a
    # `_WRITE_TIER` member on the agent side, but until this round NOTHING in
    # this module gated it: the redaction rule fires on the bare NOUN "pii", so
    # "please do not redact the pii in this return", "why did you redact the
    # ssn?" and "don't redact anything" all armed it (measured). Its read-only
    # partner `redact_scan` is what a question deserves, and it still arms.
    "redact_pii",
})

#: IMPERATIVE POSITION IS WHAT AWARDS A MUTATOR (v1.196.0 round 5).
#:
#: THE PROBLEM. An armed name enters ``session_allow`` and runs with NO approval
#: card — in chat's sentence pass and again in the attachment consent gate
#: (``attachment_rag.change_verbs_wanted``, which asks THIS function). So every
#: false positive in change-intent scoring is a silent grant of a mutator, and
#: every false negative is a feature that does not work.
#:
#: THAT IS NOT NEW IN v1.196.0 AND THIS BLOCK USED TO SAY IT WAS ("Since
#: v1.196.0 an armed name enters ``session_allow``..."). It is not: at v1.195.0
#: ``daemon/chat_turn`` already reads ``armed_grant = set(overrides.keys())`` and
#: passes ``session_allow=armed_grant``, and both lines are BYTE-IDENTICAL today
#: (``git show HEAD:...`` — checked, not remembered). Auto-armed mutators have
#: been running without an approval card for as long as auto-arming has existed.
#: What v1.196.0 changed is REACH, not consent plumbing: the wave admitted
#: ``excel_apply_spec`` to the safe set and added rules that award ``excel_edit``,
#: ``pdf_arrange``, ``convert_document`` and ``redact_pii`` off phrasings that
#: previously scored nothing. The urgency of this gate is real; its stated cause
#: was wrong, and a false "this is new" makes the pre-existing exposure invisible.
#:
#: WHAT THIS REPLACES, AND WHY IT HAD TO GO. Round 4 shipped ``_NOT_A_REQUEST``:
#: scan the message for enquiry markers (interrogative auxiliaries, negation
#: ANYWHERE, "explain how...") and REVOKE the mutators. Measured over a
#: 43-sentence read-only corpus and a 39-sentence request corpus, it suppressed
#: the WRONG turns and missed the RIGHT ones — the two failure modes are
#: ORTHOGONAL, and negation-anywhere is not a proxy for "not a request":
#:
#:   STILL LEAKING (the interrogative branch was ``\A``-anchored, so only a
#:   SENTENCE-INITIAL question suppressed, and ``what``/``how`` were absent):
#:     "should I add a column for the tax rate?"             -> excel_edit
#:     "the client asked whether we should delete page 2"    -> pdf_arrange
#:     "I wonder if you could add a column"                  -> excel_edit
#:     "if you delete page 2, what happens to the numbering?" -> pdf_arrange
#:     "what happens when you extract pages 3-5 into a new pdf?"
#:                                                  -> pdf_arrange, pdf_split
#:     "remind me how to update cell B2 to 500"              -> excel_edit
#:     "the memo says to change the fee for Belmont to 3000" -> excel_edit
#:     "apparently they want to turn this into a pdf"        -> convert_document
#:     "what does it mean to apply a layout to a workbook?"  -> excel_apply_spec
#:     "can excel add a column automatically?"               -> excel_edit
#:     "she wants to know if you can convert this to a pdf"  -> convert_document
#:
#:   OVER-SUPPRESSING (negation in ANY clause killed an imperative in ANOTHER —
#:   and a self-explaining request is the DOMINANT shape of this user's asks):
#:     "delete page 2, it's not needed"                      -> SUPPRESSED
#:     "convert this to a pdf, not a docx"                   -> SUPPRESSED
#:     "Client can't open docx. Convert this to a pdf."      -> SUPPRESSED
#:     "update cell B2 to 500, the old value isn't right"    -> SUPPRESSED
#:
#: THE INVERSION. Do not scan for enquiry to REVOKE; require IMPERATIVE POSITION
#: to AWARD. A change verb earns its tool only when it opens a clause with no
#: subject of its own — which is what an INSTRUCTION is, in this language. That
#: single discriminator is clean in BOTH directions where negation-anywhere is
#: wrong in both, because it reads the ONE word before the verb instead of the
#: whole message: "don't DELETE page 2" and "we should never CHANGE the fee" are
#: refused by the same test that admits "delete page 2, it's NOT needed".
#:
#: APPLIED INSIDE EACH MUTATOR-AWARDING RULE, not as a post-pass over the score
#: table. A post-pass ("some change verb is imperative somewhere ⇒ keep every
#: mutator") lets one family's imperative unlock another family's non-imperative
#: score, which is exactly the bulk-gate shape round 3 removed from
#: ``change_verbs_wanted``. Per-rule costs a constant at the front of nine
#: patterns and gives a per-verb answer.
#:
#: THE ALTERNATIVES SPLIT IN TWO (v1.196.0 round 6), and the split is the point.
#: Some alternatives are CONTEXT-FREE: the words themselves cannot occur in an
#: enquiry, so matching one IS the evidence. Three are merely POSITIONAL — they
#: match a PLACE in the string and infer imperative mood from it — and a place is
#: exactly what a quotation reproduces. Those three are wrapped in a capturing
#: group (see :func:`_imperative`) and awarded only when :func:`_position_allows`
#: says the surrounding text is not an enquiry or a report of someone else's
#: words. The context-free ones award unconditionally, as before.
#:
#: THE CONTEXT-FREE ALTERNATIVES:
#:  * ``\A`` — the message opens with the instruction. Optional discourse
#:    openers ("ok, ", "so ") are consumed so a real request is not lost to a
#:    filler word. Nothing precedes it, so there is no context to consult.
#:  * ``can/could/would/will YOU`` and ``please``/``kindly`` — THE POLITE
#:    REQUEST, and the guard's biggest risk if omitted. "can you turn this into
#:    a pdf?" is an instruction. Note ``you`` is REQUIRED: "can excel add a
#:    column automatically?" and "if you could add a column" are enquiries, and
#:    both were live leaks of the round-4 guard.
#:  * ``I need/want/would like you to`` — the explicit delegation form.
#:  * ``let's`` — the inclusive imperative.
#:  * THE DEONTIC FORMS — ``needs splitting``, ``needs to be split``,
#:    ``should be converted``. A request phrased about the FILE rather than to
#:    the assistant, and common in agent TASK TEXT, which is the same selector's
#:    other caller (``agents/runtime.arm_for_task``). ``be`` is required for the
#:    modal branch precisely so "we should never change the fee" and "should I
#:    add a column?" stay out; the bare ``needs`` branch is safe because the verb
#:    must follow IMMEDIATELY ("who needs to delete page 2?" has "to" in the way).
#:
#: THE THREE POSITIONAL ALTERNATIVES, and the defect that made round 6 necessary.
#: Each of these matches a POSITION and reads imperative mood off it. A position
#: is the one thing a quotation reproduces exactly, so all three fire inside an
#: enquiry or a report of someone else's words. Measured on the round-5 code:
#:  * ``\b(and|then|also|plus|next|first|finally|...)`` — the COORDINATED
#:    imperative ("delete page 2 and rotate the rest"), and the shape a two-step
#:    request is actually written in, so it cannot simply be dropped. It leaked
#:    80/80 over a 10-enquiry-frame x 8-coordinated-clause cross product ("the
#:    client asked whether we should delete page 2 and add a column" ->
#:    ``excel_edit``); the SAME eight clauses without the coordinator leaked
#:    0/80. Round 5's comment here called this "the one alternative with a known
#:    false-arm shape" and said "the leading clause is the rarer construction".
#:    BOTH HALVES WERE WRONG — it is one of three, and a coordinated pair is the
#:    ordinary way an instruction is reported.
#:  * ``[.;:!?]`` — a NEW SENTENCE. This is what makes "Client can't open docx.
#:    Convert this to a pdf." work while "does it convert this...?" does not.
#:    The semicolon is in the set for the live-ledger phrasing "our records show
#:    a $500 payment; add it to the spreadsheet". Leaked 8/8 on colon-introduced
#:    quotation ("the note ended with: redact the pii in this return" ->
#:    ``redact_pii``) and 4/4 on a reporting clause closed by a full stop.
#:  * a newline / bullet / numbered step — multi-instruction messages, AND THE
#:    WORST OF THE THREE, because it cannot tell the user's own instruction list
#:    from PASTED TEXT CONTAINING ONE. A forwarded client email plus "what do
#:    they want?" armed ``convert_document`` + ``pdf_arrange`` into
#:    ``session_allow`` with no approval card; forwarding a client email and
#:    asking what it says is a routine turn for the accountant who runs this app
#:    daily. 8/8, and the reason the release was held.
#:
#: THE GATE IS PER BRANCH AND SCOPED, NOT A MESSAGE-WIDE SCAN. Round 4 already
#: proved a message-wide scan wrong (it killed 17 of 18 real requests because a
#: justification clause carries negation), and the fix must be strictly smaller
#: than the thing that was rejected. So: the CONTEXT-FREE branches above are
#: untouched, and only these three consult :func:`_position_allows`, which asks
#: two questions of the text BEFORE the branch — never of the whole message:
#:   (1) does an enquiry/reporting marker (:data:`_ENQUIRY`) appear EARLIER? A
#:       question word, an inverted auxiliary, a verb of saying, a quoting noun
#:       WITH A REPORTING CUE BESIDE IT (round 7 — the bare noun was an
#:       over-suppressor, see :data:`_ENQUIRY` (d)), or the furniture of a
#:       forwarded email. If so the position is inside quoted or questioned
#:       material and awards nothing.
#:   (2) for the branches that CONTINUE a clause (``;``/``:`` and the
#:       coordinators), does a NEGATION appear earlier IN THAT SAME CLAUSE? "do
#:       not delete page 2 and add a column" is one instruction not to act, and
#:       the ``and`` inherits its scope. A clause ends at ``.``/``!``/``?`` or a
#:       newline, so a negation in the PREVIOUS sentence does not reach forward.
#:
#:       THIS BLOCK USED TO CLAIM THE BOUNDARY WAS "pinned by that exact
#:       sentence", naming "Client can't open docx. Convert this to a pdf." IT IS
#:       NOT, and the claim was checkable: that row arrives on the ``.`` branch,
#:       which :func:`_position_allows` answers with an EARLY RETURN (``if
#:       branch[:1] in ".!?\n": return True``) before the clause offset is ever
#:       computed. Setting ``clause = 0`` — i.e. reverting to round 4's
#:       message-wide negation scan — left all 196 tests green while really
#:       breaking "It isn't right. Fix it and clear the sheet", "The old file is
#:       not usable. Open it and delete page 2" and "Not a problem. Read the file
#:       and clear the sheet", each of which goes from armed to EMPTY. Those are
#:       COORDINATOR rows: only a branch that CONTINUES a clause reaches the
#:       computation, so only a coordinator (or ``;``/``:``) after a negated
#:       sentence can pin it. They are §2c of
#:       tests/test_change_intent_guard_v1196.py now, and the mutation goes red.
#:
#: THE MARKER WINDOW IS THE WHOLE PREFIX AND THAT COSTS SOMETHING. An instruction
#: that FOLLOWS a question OR A REPORT in the same turn reaches its verb only
#: positionally, so it is refused: "what do they want? also, delete page 2" and
#: "their email is confusing. just delete page 2" both arm nothing. Measured over
#: eight question-shaped turns, five lose their verb and the three that survive
#: do so through a context-free branch ("...? PLEASE delete page 2", "...? CAN
#: YOU delete page 2"). SCOPING THE MARKER TEST TO THE CURRENT CLAUSE — which
#: recovers that whole class — WAS MEASURED AND REJECTED: a single full stop
#: inside a pasted email ("Thanks for the draft.") resets the window, the paste's
#: own lead-in stops being visible, and family 3 re-opens. The refused phrasings
#: are rows in ``tests/test_autoselect_gaps_v1196._STILL_OPEN``.
#:
#: ROUND 6 UNDERSTATED THAT COST BY A LOT, and round 7 measured it: the trigger
#: was not "a question" but ANY quoting noun anywhere earlier plus the plain
#: imperative "do this", and on a 25-row corpus of this practitioner's own
#: requests SIXTEEN lost their verb — the entire capability gain of rounds 4-5
#: given back. Both mechanisms are fixed in :data:`_ENQUIRY`; the corpus is
#: ``tests/test_change_intent_guard_v1196.PRACTITIONER`` and ALL 25 now reach
#: their verb (re-measured in the closing review; an earlier draft of this
#: comment said 23 of 25 and pointed at two failing rows that do not exist in
#: that corpus). The phrasings still unreached live in
#: ``tests/test_autoselect_gaps_v1196._STILL_OPEN``.
#:
#: WHAT IS DELIBERATELY *NOT* HERE: ``to <verb>`` (the infinitive — "explain how
#: to delete page 2", "the memo says to change the fee", "they want to turn this
#: into a pdf" are all reports ABOUT a change), any form with an overt subject
#: (``I``/``you``/``we``/``they``/``it`` + verb), and ``don't``/``do not``/
#: ``never`` (a NEGATIVE imperative is an instruction NOT to act, and it is
#: refused simply by not being listed — which is the whole point of reading the
#: word immediately before the verb instead of scanning the message for "not").
#: THE WORD-INITIAL ALTERNATIVES SHARE ``\b`` AND ``\s+`` on purpose. Written out
#: as ten separate ``\b(?:...)\s+`` branches (the first draft) this constant
#: fronts ten rules, and ``re.search`` retries the whole alternation at EVERY
#: offset of the message — measured at 11.0 ms on a 4,000-character paste against
#: v1.195.0's 1.8 ms. That cost RAN on the daemon's single event loop (the
#: v1.153.1 rule) two to three times per turn until v1.196.0 hopped every
#: caller — both chat lanes, the attachment consent gate and
#: ``agents/runtime.arm_for_task`` — to a worker thread
#: (``tests/test_arming_offload_v1196.py``). It is no longer paid on the loop. ROUND 6 KEEPS THAT ONE GROUP even
#: though it now has to treat the coordinators differently from the rest of it:
#: the coordinators are a CAPTURED SUB-ALTERNATION inside the shared group, not a
#: second group beside it. Written the obvious way — two top-level word branches
#: — the same paste measured 1.36x round 5; folded back it is 1.21x.
#:
#: IT IS A FUNCTION, NOT A CONSTANT, for one mechanical reason: the positional
#: group has to be findable in the compiled pattern so the selector can ask where
#: it matched, a rule may front more than one verb with it (the convert rule
#: fronts three), and a named group may appear only ONCE per pattern. Each call
#: mints a fresh ``ijpos<n>`` name; :data:`_RULE_POS_GROUPS` collects them off
#: ``rx.groupindex`` after compilation, so nothing has to be listed by hand.
_POS_PREFIX = "ijpos"
_pos_seq = itertools.count()


def _imperative() -> str:
    """One IMPERATIVE-POSITION test, with its POSITIONAL branches captured.

    TWO groups, not one, and the reason is COST rather than taxonomy. The
    coordinators have to stay inside the SHARED ``\\b(?:...)\\s+`` alternation
    they lived in at round 5 — pulling them out into a second word group of
    their own measured 1.36x on a coordinator-dense 4,000-character paste,
    because ``re.search`` then retries two word-boundary alternations at every
    offset instead of one. Capturing a sub-alternation costs nothing; adding a
    branch to the top level costs a scan. So the punctuation/newline positions
    get one group and the coordinator words get another, INSIDE the group they
    already shared. :data:`_RULE_POS_GROUPS` collects both by prefix and
    :func:`select_auto_tools` asks whichever one participated.

    EVERY ``\\s*`` HERE IS POSSESSIVE (``\\s*+``) AND THAT IS THE ONLY THING
    STANDING BETWEEN THIS MODULE AND A SEVENTEEN-SECOND FREEZE OF THE WHOLE
    DAEMON. Round 6 shipped ``\\n\\s*(?:[-*•]\\s*|\\d+[.)]\\s*)?`` and put it in
    front of FOURTEEN rules (the convert rule fronts it three times). Over a run
    of blank lines the greedy ``\\s*`` swallows the whole run, fails to find a
    verb, and then gives back ONE WHITESPACE CHARACTER AT A TIME — and no rule
    verb can ever start with whitespace, so every one of those retries is
    provably useless. That is quadratic, and it was measured on this machine
    through the real ``select_auto_tools``:

        4,000-char prose            7.4 ms
        1,000 blank lines + text  906 ms
        2,000 blank lines        4,080 ms
        4,000 blank lines       17,127 ms

    ``_resolve_armed_tools`` USED TO BE a plain ``def`` called with no
    ``asyncio.to_thread`` from both chat lanes, and ``change_verbs_wanted``
    asks this scorer again — two to three passes per turn. So pasting a
    document with blank lines parked the single event loop for seventeen seconds
    and the dashboard reports "Daemon offline" (``lib/api.ts`` maps a dead fetch
    to status 0). That is the v1.153.1 four-hour outage's exact signature,
    reached from a regex instead of from ``pathlib.is_file``. HONEST SCOPE: this
    stall never shipped — the machinery that caused it was authored in this same
    unreleased wave, so it was a self-inflicted regression caught before release,
    not a defect users ever ran.

    BOTH HALVES WERE FIXED, and neither alone was enough. The quantifier below
    removes the quadratic; and every caller now hops the scorer to a worker
    thread — both chat lanes (``chat_turn.run_chat_turn``, the ``/chat/stream``
    mirror), the attachment consent gate (``_prepare_attachments`` resolves all
    suffixes in ONE hop before its loop), and the agent lane
    (``agents/runtime.arm_for_task``). Pinned by
    ``tests/test_arming_offload_v1196.py``, because the residual ~200 ms is
    still ~200 ms of every request in the app if it is paid on the loop.

    A possessive quantifier refuses to give back, so the useless retries never
    happen: 17,127 ms -> 191 ms on the same input, with ZERO behavioural change
    — checked rather than assumed, by running both spellings over all 1,299
    distinct string literals in the six test files that exercise this scorer,
    plus generated whitespace shapes (0/1/2/3/5/12/40 newlines, indented and
    tabbed, before a bullet / a numbered step / a bare verb), at cap 6 and cap
    99: 0 differences. It is safe here for a
    STRUCTURAL reason, not an empirical one — what follows every one of these
    ``\\s*``es is a bullet character, a digit, ``please``/``kindly``/``just``/
    ``also``/``then``, or one of the rule verbs, and not one of those can match
    a whitespace character. Backtracking into the run therefore cannot rescue a
    failed match; it can only re-fail it. If you ever put a branch here that
    CAN begin with whitespace, the possessive markers have to come off with it.
    Python has supported ``*+`` since 3.11 and ``pyproject.toml`` pins
    ``requires-python = ">=3.12"``.

    ``tests/test_change_intent_guard_v1196.py::test_a_whitespace_run_does_not
    _park_the_event_loop`` guards it as a RATIO against prose on the same
    machine in the same run — never a wall-clock threshold, which measures the
    hardware and goes red on a contended CI runner.
    """
    n = next(_pos_seq)
    brk, word = f"{_POS_PREFIX}{n}b", f"{_POS_PREFIX}{n}w"
    return (
        r"(?:"
        r"\A\s*+(?:(?:ok(?:ay)?|so|but|hey|hi|hello|alright|right|now|actually|just)"
        r"\b[,\s]++){0,2}"
        r"|(?P<" + brk + r">[.;:!?]\s*+"
        r"|\n\s*+(?:[-*•]\s*+|\d+[.)]\s*+)?)"
        r"|\b(?:(?P<" + word + r">and|then|also|plus|next|first|finally"
        r"|afterwards|after\s+that)"
        r"|(?:can|could|would|will)\s+you"
        r"|please|kindly"
        r"|i(?:'d|’d)?\s+(?:need|want|would\s+like)\s+you\s+to"
        r"|go\s+ahead\s+and"
        r"|let['’]?s"
        r"|(?:should|must|ought\s+to|needs?\s+to|has\s+to|have\s+to)\s+be"
        r"|needs?"
        r")\s+"
        r")(?:(?:please|kindly|just|also|then)\s+){0,2}"
    )


#: THE MARKERS THAT DISQUALIFY A POSITION (v1.196.0 round 6). Deliberately NOT
#: "anything that smells like a question": every entry here was chosen against
#: the sentences that must SURVIVE, and the ones that were tried and rejected are
#: named so the next reader does not re-add them.
#:
#: (a) WH-WORDS. The plainest evidence that a clause is asking rather than
#:     telling. ``whether`` is here for the reported question ("asked whether we
#:     should delete page 2 and add a column").
#: (b) AN AUXILIARY INVERTED OVER ITS SUBJECT — "should I", "did they", "does
#:     it", "can excel". The subject list is PRONOUNS (plus ``there`` and
#:     ``excel``, which was a measured leak of round 4) and NOT determiners:
#:     ``is the`` / ``was a`` are the shape of an ordinary declarative ("the
#:     total is the same; add it to the workbook"), and including them cost real
#:     requests.
#:
#:     ``do``/``does``/``did`` DO NOT TAKE ``this``/``that`` (v1.196.0 round 7),
#:     and that pair was a measured over-suppressor rather than a leak-catcher.
#:     "DO THIS next: convert this to a pdf" and "DO THIS: add a column for the
#:     tax rate" are the plainest IMPERATIVES in the language, and both armed
#:     nothing at all because "do this" set the cut at offset 0 and every
#:     positional branch after it was refused. The other auxiliaries keep the
#:     full subject list — "IS THIS the right layout?" is a question, "do this"
#:     is an order — so the exclusion is exactly two words wide. What it costs is
#:     "does this convert to a pdf?"-shaped questions reaching a POSITIONAL
#:     branch; measured over §1 of the guard test, none of them do (the
#:     infinitive is not an imperative alternative, so those sentences never
#:     match a gated rule in the first place).
#:
#:     ``can|could|would|will`` USED TO CARRY A ``(?!you\b)`` AND IT WAS DEAD
#:     CODE. A paragraph here argued its precision — "a gate that treated 'could
#:     you clear the sheet?' as an enquiry would break the ordinary way people
#:     ask for work" — but the subject list it guarded is ``i|we|they|he|she|it|
#:     this|that|these|those|there|anyone|someone|excel`` and has NEVER contained
#:     ``you``. Removing the lookahead changed nothing on any probe and left the
#:     whole suite green (mutation-checked). The protection is real and it comes
#:     from the SUBJECT LIST, not from a lookahead; the lookahead is gone and this
#:     note replaces the claim it was making.
#: (c) VERBS OF SAYING, in their reported forms only. ``said``/``says`` yes;
#:     ``say`` no, because a bare base form is as likely to be the user's own
#:     ("say hello to the client and add a row"). ``write`` is deliberately
#:     ABSENT for the same reason — it is a change verb this module awards on —
#:     while ``wrote``/``writes``/``written`` are reports.
#:
#:     WIDENED IN ROUND 7, because the round-6 file read as though quoted
#:     material could no longer arm a mutator and that was not true: the gate
#:     only ever held for the ENUMERATED vocabulary, and a reviewer's frames
#:     walked straight through it. Measured leaks, each arming a mutator into
#:     ``session_allow`` with no approval card: "the client REQUESTED we delete
#:     page 2 and add a column" (``excel_edit``), and the same frame with
#:     INSISTED, ADVISED, CONFIRMED, FLAGGED, and "the client WANTS us to open it
#:     and delete page 2" (``pdf_arrange``). All are now here. Only the REPORTED
#:     third-person forms are added — ``wants``/``wanted`` but never ``want``,
#:     because "we want to delete page 2 and add a column" is the user's own
#:     sentence. ``per the <document>`` joins them as its own alternative for
#:     "PER THE ENGAGEMENT LETTER: delete page 2", which named no verb at all.
#:     THIS LIST IS STILL A VOCABULARY AND NOT A MECHANISM — see
#:     ``tests/test_change_intent_guard_v1196.RESIDUAL_LEAKS``, which now says so
#:     in words and keeps a leaking frame as data.
#:
#:     ``forwarded`` MOVED OUT of this list in round 7 and into the attribution
#:     cue in (d), where it must sit BEFORE the noun. "she FORWARDED the email"
#:     is a report; "look at the email I FORWARDED and convert it to a pdf" is
#:     the user pointing at an attachment, and it was armed nothing.
#: (d) THE NOUNS THAT INTRODUCE SOMEONE ELSE'S WORDS — email, memo, note,
#:     comment, ticket, checklist, instructions, transcript — WHICH NO LONGER SET
#:     THE CUT ON THEIR OWN (v1.196.0 round 7). Round 6's disclosure said the
#:     gate cost "an instruction that follows a question" and listed three rows.
#:     The real trigger was any of these nouns ANYWHERE earlier, and a bare noun
#:     is not evidence of anything: measured on a 25-row corpus of this
#:     practitioner's OWN requests, SIXTEEN lost their verb —
#:
#:       "read the memo and then delete page 2"        marker 'memo'    -> []
#:       "check my notes and then clear the sheet"     marker 'notes'   -> []
#:       "quick note: clear the sheet"                 marker 'note'    -> []
#:       "skim the memo and add a column for the tax rate"              -> []
#:       "first read the instructions, then delete page 2"              -> []
#:       "open the email attachment and delete page 2"                  -> []
#:
#:     — while "read the FILE and then delete page 2" was pinned GREEN. The
#:     corpus had been written one word narrower than the mechanism, again.
#:
#:     SO A NOUN NOW NEEDS A REPORTING CUE, and the cue is POSITIONAL because
#:     that is what actually separates the two readings. An ATTRIBUTION may sit
#:     before it — a third-person possessive or genitive ("their email", "the
#:     reviewer's comment"), or a verb of sending/saying/leaving ("she forwarded
#:     the email", "the prior accountant left this checklist"), or
#:     ``from``/``per``/``according``. A REPORTING PREDICATE may sit after it
#:     within one clause ("the memo LISTS:", "the note ENDED with:", "the
#:     checklist ITEM is:", "notes FROM the call:"). First-person possessives are
#:     deliberately NOT attribution — "check MY notes" is the user's own.
#:
#:     WHAT THIS DOES NOT RECOVER, stated because NEVER SILENTLY DEGRADE applies
#:     to a gate's own reach: an instruction that follows a genuine report still
#:     loses its verb, because the marker window is the whole prefix. "their
#:     email is confusing. just delete page 2" still arms nothing and is in
#:     ``_STILL_OPEN``. (An earlier draft of this comment also claimed "my note
#:     on this file is out of date. clear the sheet" arms nothing — measured, it
#:     ARMS ``excel_edit``, and it is a green ``PRACTITIONER`` row. The claim was
#:     wrong, not the code.) That is the same
#:     disclosed cost as before, now correctly scoped to reports instead of to
#:     nouns; the rows are in ``tests/test_autoselect_gaps_v1196._STILL_OPEN``.
#:
#:     ``records`` and ``report`` are NOT nouns here and that is a measured
#:     decision, not an omission: "our records show a $500 payment; add it to the
#:     spreadsheet and total by client" is the live-ledger phrasing the semicolon
#:     branch exists for, and "review the draft report and save a corrected
#:     version as a docx" is pinned as a writing task in
#:     tests/test_agent_auto_arm_v1178.py. ``letter`` was tried and dropped for
#:     "rewrite this as a formal letter and save it".
#: (e) THE FURNITURE OF A FORWARDED EMAIL — the ``From:``/``Subject:`` header
#:     block, ``-----Original Message-----``, ``Fwd:``/``Re:``, and the
#:     salutation. This is what catches the paste that announces itself with
#:     headers rather than with a lead-in sentence, and it is the single
#:     highest-value entry in the set: a raw forward is the exact turn that held
#:     the release. It had NO TEST until round 7 — deleting all four markers left
#:     the whole 196-test suite green, while a "From:/Subject:" paste armed
#:     ``pdf_arrange``, "Fwd: …" armed ``pdf_arrange`` and "Re: draft\n\nclear
#:     the sheet" armed ``excel_edit``. The one forwarded row in the corpus
#:     carried a salutation AND a ``Subject:`` AND a "what", so nothing noticed.
#:     ``test_a_header_only_paste_is_recognised_as_correspondence`` drives each
#:     marker alone.
#:
#: (a) and (c) SHARE ONE ``\b(?:...)\b`` for the same reason the imperative
#: constant folds its own word list: separate word alternations make the engine
#: check the boundary once more at every offset of the message, and this scan
#: runs on a paste that contains no marker at all — the case that costs the most
#: and returns nothing. The (d) alternatives are bounded, lazy, and start at a
#: literal word, so they cost a bridge scan only where a cue or a noun actually
#: appears.

#: The nouns of (d). One definition, used by both cue alternatives.
_QUOTING_NOUN = (
    r"(?:e-?mails?|memos?|notes?|messages?|comments?|tickets?|checklists?"
    r"|instructions?|voicemails?|transcripts?)"
)
#: An attribution that may stand BEFORE a quoting noun. Third person only.
#: THE GENITIVE BRANCH EXCLUDES CONTRACTIONS, and that exclusion is the whole
#: precision of it (v1.196.0, found in final review). ``\w+['’]s`` was written to
#: mean "a third-person possessive" — "the reviewer's comment", "john's email" —
#: but the mechanism is ANY word ending in 's, and English contracts exactly
#: that way: ``here's``, ``there's``, ``that's``, ``it's``, ``let's``. So
#: "here's the email. convert this to a pdf" read as ATTRIBUTED SPEECH and armed
#: nothing, when v1.195.0 armed ``convert_document`` for it.
#:
#: THE COMMA FORM OF THAT SENTENCE IS A DIFFERENT, STILL-OPEN PROBLEM, and an
#: earlier draft of this comment cited it here as though this fix repaired it.
#: It does not: a comma or a dash is not an imperative position, so "here's the
#: email, convert this to a pdf" arms nothing — and neither does "the file,
#: convert this to a pdf", which has no opener and no quoting noun at all. It is
#: POSITIONAL, applies to every change verb, and IS A REGRESSION AGAINST
#: v1.195.0. Pinned by ``test_the_comma_is_not_an_imperative_position``. Measured over 3,024
#: <opener> <quoting noun> <change verb> frames: 2,016 regressed against the
#: shipped build, on the exact turn shape this wave exists to serve, and the
#: comment here described a mechanism the regex did not implement.
#:
#: HONEST LIMIT: this is a fixed list of contraction openers, not a rule that
#: knows a possessive from a contraction — "the boss's memo" and "the bosses'
#: memo" are both attribution, "somebody's got the file, delete page 2" is not,
#: and nothing here can tell the last one apart. It recovers 1,512 of the 2,016
#: (measured); the rest are pinned in ``test_change_intent_guard_v1196``
#: (``test_the_contraction_list_is_a_LIST_not_a_rule``).
_ATTRIBUTION = (
    r"(?:his|her|their|its"
    # THE INDEFINITE PRONOUNS ARE DELIBERATELY *NOT* HERE (v1.196.0, closing
    # review). The first cut of this list also excluded `one|nobody|somebody|
    # someone|anybody|anyone|everybody|everyone`, on the theory that they open
    # the user's own sentence the way `here's` does. Measured, they do the
    # opposite: "anyone's comment was: delete page 2" and "everyone's email.
    # convert this to a pdf" ARMED a mutator, while the byte-identical
    # "the reviewer's comment was: …" and "john's email. …" stayed suppressed —
    # they are quotations of someone else's instruction, which is exactly what
    # the gate exists to refuse. They accounted for 2,000 of 3,250 recovered
    # frames and every one of those was a false arm. The sentence they were
    # added for ("somebody's got the file, delete page 2") arms nothing either
    # way, because a COMMA is not an imperative position.
    r"|(?!(?:here|there|that|it|let)['’]s)\w+['’]s"
    r"|according|per|from"
    r"|said|says|sent|sends|forwarded|forwards|left|wrote|writes|quoted|quotes"
    r"|attached|attaches|received|receives)"
)
#: A reporting predicate that may stand AFTER a quoting noun, same clause.
_REPORTING = (
    r"(?:says?|said|reads?|read|states?|stated|lists?|listed|mentions?|mentioned"
    r"|ends?|ended|contains?|contained|includes?|included|items?|instructs?"
    r"|attached|arrived|came|from|by)"
)

_ENQUIRY = re.compile(
    r"\b(?:what|why|whether|how|when|where|which|who|whom|whose"
    r"|said|says|wrote|writes|written|asked|asks|told|tells|reads"
    r"|quoted|quotes?|mentioned|mentions|stated|states|explained|explains"
    r"|wonder(?:s|ed|ing)?|suggested|suggests|replied|responded"
    r"|requested|requests|insisted|insists|advised|advises|confirmed|confirms"
    r"|flagged|flags|wants|wanted"
    r"|according)\b"
    # (d) A QUOTING NOUN, but only with a reporting cue beside it.
    r"|\b" + _ATTRIBUTION + r"\b[^.!?\n]{0,30}?\b" + _QUOTING_NOUN + r"\b"
    r"|\b" + _QUOTING_NOUN + r"\b[^.!?\n]{0,25}?\b" + _REPORTING + r"\b"
    # "per the engagement letter: delete page 2" — an authority cited by name.
    r"|\bper\s+(?:the|their|his|her|our|your|my)\b"
    r"|\b(?:(?:do|does|did)\s+(?:i|we|you|they|he|she|it|these|those|there"
    r"|anyone|someone|excel)"
    r"|(?:is|are|was|were|has|have|had|may|might|must|should|shall)\s+"
    r"(?:i|we|you|they|he|she|it|this|that|these|those|there"
    r"|anyone|someone|excel))\b"
    r"|\b(?:can|could|would|will)\s+"
    r"(?:i|we|they|he|she|it|this|that|these|those|there|anyone|someone"
    r"|excel)\b"
    # (b2) THE HYPOTHETICAL POLITE FORM, which is not polite and not a request:
    # "I wonder IF YOU COULD add a column", "she wants to know IF YOU CAN convert
    # this to a pdf". Both are named as live round-4 leaks in the block above,
    # and both survived the first cut of this gate — measured, 16 of the 80
    # coordinator false arms were these two frames and nothing else. ``you`` is
    # REQUIRED here, which is the mirror image of the rule two lines up: after
    # ``if`` the second person makes the clause hypothetical, without it ("can
    # you add a column") it makes it an instruction.
    r"|\bif\s+you\s+(?:can|could|would|will)\b"
    r"|^[ \t]*(?:from|to|cc|bcc|sent|subject|date|reply-to)[ \t]*:"
    r"|-{3,}\s*original\s+message|\bfwd?\s*:|\bre\s*:"
    # ...and the SALUTATION, which is the only marker a pasted email BODY
    # carries once the headers have been stripped off it. A capital letter is
    # required after the greeting, at the start of a line: "Hi Valentino," is
    # correspondence, "hey, delete page 2 and add a column" is the user talking
    # to this app, and the difference is the name. (`hi`/`hey`/`hello` are
    # discourse openers in the \A branch above — that branch is CONTEXT-FREE, so
    # nothing here can take a message-initial instruction away.)
    r"|^[ \t]*(?:hi|hello|hey|dear|good\s+(?:morning|afternoon|evening))"
    r"\b[ \t]+(?-i:[A-Z])",
    re.IGNORECASE | re.MULTILINE,
)

#: CLAUSE-SCOPED negation. NOT the round-4 message-wide scan: this is only ever
#: applied between the start of the current clause and the branch position, and
#: only for the branches that CONTINUE a clause. See :func:`_position_allows`.
_NEGATION = re.compile(r"\b(?:not|never|cannot|neither|nor)\b|n['’]t\b", re.IGNORECASE)

#: A clause ends here. A negation does not reach past one.
_CLAUSE_END = (".", "!", "?", "\n")


def _position_allows(msg: str, start: int, branch: str, cut: int) -> bool:
    """May a POSITIONAL imperative branch matching *branch* at *start* award?

    *cut* is the offset of the first :data:`_ENQUIRY` marker in *msg* (or past
    its end when there is none) — computed once per call by the selector, since
    the same answer serves every rule.

    THREE ANSWERS, AND THE ORDER OF THE TESTS IS THE CONTRACT
    (``tests/test_change_intent_guard_v1196.py::test_position_allows_has_the
    _shape_the_module_describes`` drives this function directly, because every
    other test here reaches it through a sentence and a sentence can pass for
    the wrong reason):

    1. A marker at or before *start* refuses, whatever the branch. The window is
       the whole prefix — see the block above :func:`_imperative` for what that
       costs.
    2. A branch that OPENS a clause (``.``/``!``/``?``/newline) is then allowed
       unconditionally. Nothing before it is in scope, which is what admits
       "Client can't open docx. Convert this to a pdf." — and it means this
       branch NEVER reaches the negation test below, so no sentence of that
       shape can pin the clause boundary. Round 6's module comment claimed it
       did; §2c of the guard test now holds rows that really do.
    3. A branch that CONTINUES a clause (``;``, ``:``, a coordinator) is refused
       when a negation stands earlier IN THAT CLAUSE ONLY.
    """
    if start >= cut:
        return False
    if branch[:1] in ".!?\n":
        return True
    # ';' ':' and the coordinators CONTINUE the clause they sit in, so a
    # negation earlier in that clause scopes over what follows.
    clause = max(msg.rfind(end, 0, start) for end in _CLAUSE_END) + 1
    return _NEGATION.search(msg, clause, start) is None

# Signal rules: (regex, {tool: weight}). Scores accumulate across rules; the
# highest-scoring tools fill the free slots. Weights are relative only.
_RULES: list[tuple[re.Pattern[str], dict[str, int]]] = [
    # --- web research -----------------------------------------------------
    # Strong intent words only — bare "today"/"currently" fire on small talk
    # ("how are you today"), so they deliberately do NOT count as web signal.
    (
        re.compile(
            r"\b(search|look\s?up|google|research|browse|online|internet|"
            # v1.173.0: `web` alone never matched "the IRS WEBSITE" — the word
            # boundary sits after "web" — so "look that up on the IRS website"
            # armed the brain and NOTHING that can reach the internet. A named
            # site is the least ambiguous web signal there is.
            r"web(?:sites?)?|latest|news|headline|up.to.date|weather|price of|stock|"
            r"release date|who won|trending)\b",
            re.IGNORECASE,
        ),
        {"web_search": 8, "web_fetch": 3},
    ),
    # --- working with existing files / folders ---------------------------
    (
        # A SECOND ALTERNATIVE USED TO LIVE HERE — `|\bin\s+(?:the|my|this)\s+
        # folder\b` — and v1.196.0's first cut "fixed" it by adding `that`,
        # explaining that "'that folder' was missing from the preposition branch,
        # so 'go through everything in THAT folder' matched nothing here". THAT
        # EXPLANATION WAS FALSE and the edit was a NO-OP, which is why it is
        # written down instead of quietly reverted: the branch's own note named
        # the alternative above as the one "that files" already reached, and
        # `that` is IN that alternative's determiner list. Every string the
        # preposition branch could match (`in <det> folder`) contains
        # `<det> folder`, which the alternative below matches on all four
        # determiners — so it could never add a single sentence. Measured both
        # ways over the folder corpus: identical selections, and a mutation
        # reverting the added word left every behavioural test in
        # `tests/test_autoselect_gaps_v1196.py` green (only a test asserting on
        # the PATTERN STRING moved, which is how the no-op was found).
        # The dead alternative is gone; the determiner list below is the rule.
        re.compile(
            r"\b(?:my|the|this|that|our)\s+(?:files?|folders?|directory|"
            r"documents?|downloads|desktop)\b",
            re.IGNORECASE,
        ),
        # `list_folder` rides at `list_files`' old weight and `list_files` drops
        # one below it: when the sentence says "folder", the tool that can open
        # the user's ACTUAL folder should outrank the one confined to the chat
        # scratch workspace. Both still arm — an agent run's workspace is a real
        # working directory, so `list_files` is the right answer there.
        {"file_search": 8, "read_document": 5, "list_folder": 5, "list_files": 4},
    ),
    # --- "these/those <documents>": the ANAPHOR rule (v1.196.0) -----------
    # MEASURED: "process these 15 client documents and give me a summary" — the
    # user's own bulk-document sentence — armed NOTHING AT ALL. Two causes, both
    # in the determiner: the folder rule above holds no `these`/`those`, and its
    # noun has to sit DIRECTLY against the determiner, so a count or an adjective
    # ("15 client") broke the match on its own.
    #
    # THIS IS ITS OWN RULE, NOT A BRANCH OF THE FOLDER RULE ABOVE, and that is
    # the entire point of the entry. It was first written as a third alternative
    # inside that regex, which made it inherit `{file_search: 8, ...}` — and a
    # `file_search: 8` awarded for the word "these" is a claim about SEARCH that
    # the sentence never made. Measured on the real selector, that inheritance
    # took the #1 slot away from the intent tool across a whole class of
    # requests: "merge these pdfs into one file" led with `file_search` instead
    # of `pdf_arrange`, "redact the pii in these returns" with `file_search`
    # instead of `redact_scan` (12 vs 10 — the app's highest-consequence path
    # offering a file search first), and the same for `write_document`,
    # `convert_document`, `write_file`, `code_search` and
    # `excel_formula_check`. "these documents" is an ANAPHOR: it points at what
    # is ALREADY IN PLAY (the attachments, the grounded project). It says
    # documents exist; it does not say what to do with them. The verb the user
    # actually typed says that, and this rule must never outrank it.
    #
    # THE WEIGHTS ARE 1 AND 1 — DELIBERATE, AND CALIBRATED BY MEASUREMENT, not
    # chosen. Same reasoning as the v1.173.0 possessive-store rule below
    # ("THE WEIGHTS ARE SMALL ON PURPOSE ... to nudge, not to outrank the verb
    # the user actually wrote"), and here the arithmetic is forced rather than
    # merely tasteful. `read_document` already scores 6 from the doc-noun rule
    # on "convert these documents to pdf", where `convert_document` scores 7; it
    # already scores 8 on "create a summary memo of these documents", where
    # `write_document` scores 10 (the creator-outranks-analyzers calibration
    # annotated below as a real shipped failure). A bump of 2 flips BOTH of
    # those leads to `read_document`; a bump of 4 also flips "make a spreadsheet
    # from these invoices"; the 7/3 pair this rule was first re-drafted with
    # flips SEVEN leads including `redact_scan`. Swept over 31 sentences: 1/1 is
    # the largest pair that flips NOTHING. Do not raise them without re-running
    # that sweep — the module's ties break ALPHABETICALLY, so a bump that merely
    # TIES an intent tool still steals its slot whenever this rule's tool sorts
    # earlier (`read_document` < `redact_scan`, `read_document` < `write_document`).
    #
    # `file_search` rides at the same 1 rather than being dropped: the anaphor is
    # the one case where the model may have to LOCATE the files before it can
    # open them. At equal weight the tiebreak puts `file_search` first on a bare
    # anaphor sentence, which is the right order of operations there (find, then
    # read) and is overridden the moment a document is actually attached — the
    # attachment lane bumps `read_document` by 9. Deliberately NOT `list_folder`
    # or `list_files`: an anaphor names no directory, so a listing tool armed
    # here has no path it could resolve.
    #
    # FALSE ARMS — the honest statement of the class. The two-word filler bound
    # keeps out long drifting sentences ("those files you asked me to ignore are
    # documents"), but the ZERO-filler base case still over-matches: "email these
    # forms to the client", "those statements are wrong" and "these forms of
    # energy are interesting" each go from `[]` to `[file_search,
    # read_document]` — two read-only tools on a sentence with no file in it.
    # That is the accepted cost, and it is the reason the weights are 1: at this
    # size the rule cannot take the LEAD from any tool on any sentence, and it
    # costs a slot on exactly ONE measured sentence out of 31 — see below. A
    # `(?!\s+of\b)` lookahead was tried against the drift (the same device the
    # history rule uses below) and REJECTED on measurement: it kills "reconcile
    # these statements of account", an everyday phrasing in this firm's work.
    #
    # THE ONE MEASURED SLOT COST, stated rather than glossed: on "make a
    # spreadsheet from these invoices" the cap was ALREADY full at 6 before this
    # rule existed, and `read_document` (6 from the doc-noun rule, +1 here) now
    # displaces `excel_edit` (6) as the marginal entry. That is a trade WORTH
    # MAKING and not a regression: the sentence says "make", so there is no
    # existing workbook, and the creation rule below already records that
    # `excel_edit` "refuses without an existing workbook" — while the invoices
    # genuinely have to be READ to build the sheet. `write_document`,
    # `excel_query`, `excel_profile` and `excel_read` all keep their slots.
    (
        re.compile(
            r"\b(?:these|those)\s+(?:\d+\s+)?(?:[a-z][\w'-]*\s+){0,2}"
            r"(?:files?|folders?|documents?|pdfs?|invoices?|receipts?|"
            r"statements?|returns?|forms?)\b",
            re.IGNORECASE,
        ),
        {"file_search": 1, "read_document": 1},
    ),
    # --- what is actually IN a folder (v1.196.0) --------------------------
    # The plain "show me the folder" ask, which is not a SEARCH ("find the
    # invoice") and not a READ ("summarize the pdf") — it is a listing, and
    # until now the only listing tool it could reach was workspace-confined.
    # Every alternative needs a folder-shaped noun within 40 chars, so "list
    # the top five risks" and "what's in the report" do not fire.
    (
        re.compile(
            r"\b(?:what(?:'s|s| is| are)?\s+(?:in|inside|under)|"
            r"(?:go|look|read|walk|comb|sift)\s+through\s+"
            r"(?:everything|all|each|every)|"
            r"(?:everything|every file|all the files?|each file)\s+"
            r"(?:in|inside|under)|"
            r"list|contents of|show me)\b.{0,40}"
            r"\b(?:folders?|directory|directories|downloads|desktop)\b|"
            # The named user folders, which are folder-shaped without the word:
            # "check my Downloads for the K-1".
            r"\b(?:my|the)\s+(?:downloads|desktop|documents)\s+"
            r"(?:folder|directory)\b",
            re.IGNORECASE,
        ),
        {"list_folder": 9, "file_search": 4, "read_document": 3},
    ),
    # --- the CODEBASE vocabulary (v1.210.0) -------------------------------
    # This module's nouns were office-document-shaped and coding requests
    # scored NOTHING at all — measured on the real selector:
    #   "tell me about this code base"  -> []
    #   "what does this project do"     -> []
    #   "fix the bug in main.py"        -> []   (closed by _CODE_FILE_RX below)
    # while "list the files here" armed four tools. The daily-driver thesis is
    # creative + CODING + office, and the one deterministic sentence→tools
    # scorer (both chat lanes AND `agents/runtime.arm_for_task`) could not hear
    # the coding half.
    #
    # READ-ONLY BY CONSTRUCTION, so the rule stays UNGATED (no `_imperative()`):
    # every tool it awards is a reader/lister already in `AUTO_SAFE_TOOLS`, and
    # the module's licence — "an armed-but-unneeded tool is simply ignored" —
    # holds. Asking ABOUT code is consent to READ it, never to change it: no
    # `_CHANGE_TOOLS` member is awarded here, and "fix the bug in main.py"
    # arming `read_file`/`file_search` is correct — the change-verb machinery
    # elsewhere owns mutations, and `edit_file`/`shell` stay behind explicit
    # "+" arming per the module docstring.
    #
    # THE NOUNS ARE THE UNAMBIGUOUS ONES: codebase/repo/repository/source code/
    # readme/"this project"/"architecture of". "the code" ALONE is deliberately
    # NOT a noun here — this practitioner's domain says "the Code" for the IRC,
    # and "edit the code" is pinned in tests/test_agent_auto_arm_v1178.py — so
    # the bare noun needs a READ-shaped verb in front of it ("explain this
    # code", "walk me through the code"). `repos?\b` needs its own \b because
    # the alternation continues; "report" cannot match it (the boundary fails).
    #
    # WEIGHTS mirror the folder rule above (`file_search` leads at 8): a
    # codebase question is answered by finding + reading files. `read_file`
    # rides at the `_PATH_RX` reader's 6 — source files are exactly what it
    # reads — with the listers below it and `read_document` (README/docs prose)
    # at the nudge level.
    (
        re.compile(
            r"\b(?:code\s?bases?|repositor(?:y|ies)|repos?\b|source\s+code|"
            r"source\s+tree|readme\b|this\s+project\b|architecture\s+of\b|"
            r"(?:explain|understand|describe|summari[sz]e|review|analy[sz]e|"
            r"read|walk\s+(?:me\s+)?through|tell\s+me\s+about|look\s+at)\b"
            r"[^.!?]{0,30}?\b(?:this|the|our|your|my)\s+code\b)",
            re.IGNORECASE,
        ),
        {"file_search": 8, "read_file": 6, "list_files": 5, "list_folder": 4,
         "read_document": 3},
    ),
    (
        re.compile(
            r"\b(pdf|docx?|excel|xlsx|spreadsheet|csv|pptx?|presentation|"
            r"slide deck|word doc)\b",
            re.IGNORECASE,
        ),
        {"read_document": 6, "file_search": 4},
    ),
    (
        re.compile(
            r"\b(find|locate|search for|look for|where is)\b.{0,40}"
            r"\b(file|document|folder|report|invoice|receipt|contract|note)s?\b",
            re.IGNORECASE,
        ),
        {"file_search": 8, "list_files": 4},
    ),
    (
        re.compile(
            r"\b(read|open|summar(?:ize|ise|y)|review|analy[sz]e|extract|"
            r"compare|check)\b.{0,50}\b(file|document|pdf|docx?|spreadsheet|"
            r"xlsx|csv|report|contract|invoice|notes?)s?\b",
            re.IGNORECASE,
        ),
        {"read_document": 8, "file_search": 5},
    ),
    # --- reuse a script we already wrote ---------------------------------
    # Fires on scripting/automation intent AND on "again"-shaped asks ("like
    # last time", "the usual"), which is exactly when prior art exists. Only
    # the read-only pair is armed: worst case the model ignores them.
    (
        re.compile(
            r"\b(script|automate|automation|bulk|batch|rename|convert all|"
            r"every file|python|powershell|macro)\b|"
            r"\b(again|like last time|same as before|the usual|as usual)\b",
            re.IGNORECASE,
        ),
        {"code_search": 6, "code_load": 3},
    ),
    # --- creating deliverables -------------------------------------------
    # "excel/workbook/worksheet/table/word doc" belong in the noun group:
    # "create an excel" once armed only the ANALYZERS (excel_edit refuses
    # without an existing workbook) and never the one tool that creates
    # files — a real shipped failure. The creator also OUTRANKS the
    # spreadsheet analyzers (10 > 9) when creation intent is present.
    #
    # v1.196.0 round 4 adds the RE-authoring verbs. Measured with `draft.docx`
    # attached, "rewrite this as a formal letter and save it" armed
    # ['read_document'] and nothing that can write one — `\bwrite\b` cannot match
    # "rewrite" (the word boundary fails), and the trailing "save it" names no
    # noun, so the sentence's only document noun ("letter") sat BEFORE its only
    # matching verb. Re-authoring an attachment into a new file is exactly
    # `write_document`'s job, and it is one of the commonest things this user
    # asks for. `review` is deliberately NOT added: it reads.
    #
    # v1.196.0 round 5 puts `_imperative()` in front of the verb. The creation
    # verbs are ordinary English words, and without a position test the rule
    # reads a REPORT of a change as a request for one — "the memo says to write
    # a summary", "they want to save this as a pdf". `write_document` and
    # `write_file` are both `_CHANGE_TOOLS`, so that is a silent grant.
    (
        re.compile(
            _imperative()
            + r"(?:write|create|draft|make|generate|prepare|produce|save|"
            r"export|put together|re-?write|redraft|reword|rephrase|revise|"
            r"rework)\b.{0,60}\b(file|document|report|memo|"
            r"letter|docx|pdf|spreadsheet|xlsx|csv|deck|presentation|pptx|"
            r"proposal|invoice|one.pager|summary doc|excel|workbook|"
            r"worksheet|sheet|table|word doc)\b",
            re.IGNORECASE,
        ),
        {"write_document": 10, "write_file": 3},
    ),
    (
        re.compile(
            _imperative()
            + r"(?:write|create|make|generate|save)\b.{0,40}"
            r"\b(script|code file|\.py|\.js|\.ts|\.html|\.css|\.json|\.md)\b",
            re.IGNORECASE,
        ),
        {"write_file": 7},
    ),
    # --- PDFs specifically ------------------------------------------------
    (
        re.compile(
            r"\b(extract|pull|tables?|pages?)\b.{0,40}\bpdf\b", re.IGNORECASE
        ),
        {"extract_pdf": 7, "read_document": 3},
    ),
    # THE VERB IS NOT ALWAYS "CONVERT" (v1.196.0 round 4). Measured through
    # `chat_turn._resolve_armed_tools` with a real attachment, both of these
    # scored NO change verb at all and therefore armed none once the round-3
    # consent gate (`attachment_rag.change_verbs_wanted`) started asking this
    # function whether the request wanted one:
    #   "turn this into a pdf"            + report.docx -> ['read_document', 'file_search']
    #   "convert this to a word document" + notes.txt   -> ['read_document']
    # TWO INDEPENDENT CAUSES, and only fixing both closes the pair:
    #   (a) the VERB list was one word long. "turn X into a pdf", "export it to
    #       excel" and "make this a pdf" are the same request. ("save this AS a
    #       pdf" is NOT closed — see the `as` note below; it is a decision, and
    #       it is on the KNOWN-GAP list rather than glossed here.)
    #   (b) the FORMAT list did not contain the words people use for the
    #       formats. `docx?` is `\b`-anchored, so it matches "doc" and "docx" and
    #       CANNOT match "document" — "convert this to a WORD DOCUMENT", the
    #       second sentence above, therefore failed on the noun even though its
    #       verb was literally `convert`.
    # ONE RULE, THREE ALTERNATIVES, ON PURPOSE: scores accumulate per RULE, so
    # folding the new phrasings in here cannot double-score a sentence that
    # already matched (the v1.173.0 possessive-store lesson). Measured: the
    # `_INTENT_LEADS` sentence "convert these documents to pdf" is byte-identical
    # before and after.
    #
    # PRECISION — the format must sit DIRECTLY against `to`, with at most an
    # article. "save it to the pdf folder" names a DESTINATION, not a format, and
    # does not fire (`to the pdf` — `the` is not `an?`); "turn this into a
    # summary" does not fire either. Both measured.
    #
    # `as` IS DELIBERATELY NOT A THIRD PREPOSITION HERE, and the reason is not
    # linguistic. Adding it closes "save this as a pdf" — a common phrasing — but
    # measured over the corpus it also fires on "review the draft report and save
    # a corrected version as a docx", which `tests/test_agent_auto_arm_v1178.py`
    # pins as a WRITEY TASK that a read-only REVIEWER agent must not gain a
    # writer from. `convert_document` writes a NEW file and is
    # `Reversibility.IRREVERSIBLE`, so it is absent from
    # `agents/runtime._WRITE_TIER` (that set's forward guard only catches
    # REVERSIBLE tools) and the agent-lane tier gate would NOT have stopped it —
    # the test stays green while a read-only definition gains a file writer.
    # That is a real hole and it is PRE-EXISTING (`convert the report to pdf`
    # already reaches it today), but widening its reach is not this unit's to do
    # unilaterally: closing it means editing `_WRITE_TIER`, which is another
    # module and a paired change. "save this as a pdf" therefore stays on the
    # KNOWN-GAP list in `tests/test_autoselect_gaps_v1196.py` §9 rather than
    # being closed here quietly.
    #
    # v1.196.0 round 5 puts `_imperative()` in front of EVERY branch's verb. The
    # round-4 guard let these through, measured: "does it convert this to a word
    # document automatically?", "apparently they want to turn this into a pdf",
    # "she wants to know if you can convert this to a pdf". Each armed
    # `convert_document`, which writes a file and — being IRREVERSIBLE — is not
    # even caught by `agents/runtime._WRITE_TIER` on the other lane.
    (
        re.compile(
            _imperative()
            + r"convert\b.{0,40}\b(pdf|docx?|xlsx|pptx|csv|markdown|html)\b|"
            + _imperative()
            + r"(?:convert|turn|change|export|render|output|save)\b"
            r"[^.!?]{0,40}?\b(?:in)?to\s+(?:an?\s+)?(?:new\s+)?"
            r"(?:pdfs?|docx|word\s+doc(?:ument)?s?|excel(?:\s+(?:file|workbook))?|"
            r"xlsx|spreadsheets?|csv|pptx|powerpoints?|slide\s?decks?|"
            r"markdown|html|text\s+files?|plain\s+text)\b|"
            # The ditransitive form: "make this a pdf" — no preposition, the
            # format noun arrives as a second object.
            #
            # A CONNECTIVE IS REQUIRED (v1.196.0 round 4). Every connective used
            # to be optional, so verb + pronoun + bare format noun matched and
            # "save these spreadsheets" armed `convert_document` — on the agent
            # lane that handed a read-only REVIEWER a file writer. The pronoun
            # restriction was NOT the safeguard the old comment here claimed it
            # was: "make these pdfs the priority" matched it too. Requiring
            # either "(in)to" or an article is what actually separates the
            # conversion ("make this A pdf", "turn it INTO a pdf") from a plain
            # noun phrase that merely names a format.
            + _imperative()
            + r"(?:make|turn|save)\s+(?:it|this|that|them|these|those)\s+"
            r"(?:(?:in)?to\s+(?:an?\s+)?|an?\s+)"
            r"(?:pdfs?|docx|word\s+doc(?:ument)?s?|excel(?:\s+(?:file|workbook))?|"
            r"xlsx|spreadsheets?|csv|pptx|powerpoints?|slide\s?decks?|"
            r"markdown|html|text\s+files?|plain\s+text)\b",
            re.IGNORECASE,
        ),
        {"convert_document": 7},
    ),
    # Page-level PDF work — a page verb and a page-y noun (pdf/page/scan) in
    # either order: "merge these pdfs", "the pdf needs splitting", "split the
    # scan into separate pages", "rotate the last page". Verb-only sentences
    # ("merge these cells", "split the string") deliberately do NOT fire: one
    # of the nouns must appear.
    #
    # v1.196.0 round 5: `_imperative()` in front of BOTH branches' verbs. The
    # noun-first branch keeps its reach through the DEONTIC alternatives ("the
    # pdf needs splitting", "the scan should be rotated"), which is the form that
    # branch exists for; what it loses is the interrogative reading of the same
    # words ("why was the pdf split?").
    (
        re.compile(
            _imperative()
            + r"(?:merge|split|rotate|reorder|rearrange|combine|reverse)\b"
            r".{0,40}\b(?:pdfs?|pages?|scans?)\b|"
            r"\b(?:pdfs?|pages?|scans?)\b.{0,40}?" + _imperative()
            + r"(?:merge|split|rotate|reorder|"
            r"rearrange|combine|reverse)\w*\b",
            re.IGNORECASE,
        ),
        {"pdf_arrange": 8, "pdf_split": 6},
    ),
    # --- pages OUT / pages GONE (v1.196.0 round 4) ------------------------
    # The verb list above is about ARRANGEMENT (merge/split/rotate/reorder), and
    # the two commonest page requests in this firm's work are neither. Measured
    # through `chat_turn._resolve_armed_tools` with `return.pdf` attached:
    #   "extract pages 3-5 into a new pdf" -> ['read_document', 'file_search', 'extract_pdf']
    #   "delete page 2 from this pdf"      -> ['read_document', 'extract_pdf', 'file_search']
    # `extract_pdf` READS TEXT OUT of a PDF ("This tool never extracts text —
    # use read_document/extract_pdf for that", says `pdf_arrange`'s own
    # description, from the other side). Neither turn armed anything that can
    # produce a new PDF, so the honest answer available to the model was "I
    # can't", and the user had to rephrase.
    #
    # TWO RULES, NOT ONE ALTERNATION, because they want DIFFERENT TOOLS and this
    # module's gate is per-verb: deleting a page is `pdf_arrange` (it drops
    # unselected pages into a new file) and `pdf_split` — which cuts a PDF into
    # SEVERAL files — is not what was asked, so it is not armed. Selecting a
    # range genuinely reads either way (`pdf_arrange` with `pages: "3-5"`, or
    # `pdf_split` with `ranges: ["3-5"]`), so that rule arms both.
    #
    # WHY THE SELECT BRANCH NEEDS A TARGET AND THE DELETE BRANCH DOES NOT.
    # "extract" is the one verb this module already spends on READING (the
    # `extract_pdf` rule above), and `_INTENT_LEADS` pins "extract the pages from
    # these scanned pdfs" as a `read_document` sentence. So the select branch
    # additionally requires evidence of a DESTINATION or a page NUMBER
    # (`3-5` / "into" / "separate" / "new" / "own") — measured, that keeps it off
    # the pinned sentence, which has neither. Deleting is unambiguous: nobody
    # says "delete page 2" about text they want read back to them.
    #
    # `_NO_PREP` IS LOAD-BEARING AND WAS ADDED AFTER A MEASURED FALSE ARM. The
    # first cut bridged verb to noun with a plain `[^.!?]{0,20}?`, and
    # "extract the text FROM page 3" — a READ, and one of the app's commonest
    # requests — armed `pdf_arrange` + `pdf_split` and NOTHING that reads. A
    # preposition between the verb and "page" means the pages are where the
    # object IS, not what the object IS; "extract the data from pages 3-5" is the
    # same sentence. Adjectives and counts still bridge fine ("delete the last
    # two pages", "pull out the first 3 pages"), because they modify the pages
    # rather than relocating the verb's object.
    #
    # THE TARGET EVIDENCE COMES FROM EITHER SIDE OF THE NOUN, which is why this
    # rule has two alternatives instead of one lookahead. "extract pages 3-5 INTO
    # A NEW pdf" names it after; "pull out the FIRST 3 pages of the return" and
    # "take the LAST 2 pages" name it before, and a forward-only test misses that
    # whole (equally common) half — the same mistake the apply-spec rule above
    # records making with its noun lookahead. Both alternatives bridge through
    # `_NO_PREP`, so "extract the 3 tables ON page 2" stays a read.
    #
    # v1.196.0 round 5 fronts both with `_imperative()`. Measured leaks these
    # close, each a plain question that armed a PDF writer:
    #   "what happens when you extract pages 3-5 into a new pdf?"
    #   "if you delete page 2, what happens to the numbering?"
    #   "the client asked whether we should delete page 2"
    #   "explain how to delete page 2 from a pdf"
    # ...while "delete page 2, it's not needed" — which round 4's negation scan
    # killed — is admitted, because the word before the verb is the start of the
    # message and not the word "not" three clauses later.
    (
        re.compile(
            _imperative()
            + r"(?:extract|pull|take|keep|copy|separate|isolate|split|save)\b"
            rf"{_NO_PREP}(?:"
            r"\bpages?\b(?!\s*numbers?\b)"
            r"(?=[^.!?]{0,40}(?:\d|\binto\b|\bseparate\b|\bnew\b|\bown\b))"
            rf"|\b(?:\d+|first|last|final|only)\b{_NO_PREP}"
            r"\bpages?\b(?!\s*numbers?\b))",
            re.IGNORECASE,
        ),
        {"pdf_arrange": 8, "pdf_split": 6},
    ),
    (
        # The negative lookahead is not decoration: "remove the page NUMBERS
        # from the footer" and "delete the page BREAKS" are formatting asks
        # about a word processor's furniture, not page-level PDF surgery, and
        # both would otherwise arm a file writer. Measured — both stay empty.
        re.compile(
            _imperative()
            + r"(?:delete|remove|drop|discard|strip|cut)\b"
            rf"{_NO_PREP}\bpages?\b(?!\s*(?:numbers?|breaks?|counts?)\b)",
            re.IGNORECASE,
        ),
        {"pdf_arrange": 8},
    ),
    # --- spreadsheets -----------------------------------------------------
    (
        re.compile(rf"\b({_XL_NOUNS})\b", re.IGNORECASE),
        # NO `excel_edit` HERE (v1.196.0 round 4). This rule fires on the mere
        # MENTION of a spreadsheet noun, so it used to hand the EDITOR to
        # "what does this spreadsheet say?" — measured, with an .xlsx attached,
        # arming excel_edit on a plainly read-only question. That was harmless
        # while arming only offered a tool the model could ignore; it stopped
        # being harmless when the attachment consent gate began treating an
        # armed name as `session_allow` with no approval card. A bare noun is
        # topic, never intent: the editor is awarded by the rules below that
        # require a change VERB (and, for a figure, a numeric target).
        {"excel_query": 9, "excel_profile": 8, "excel_read": 7, "file_search": 3},
    ),
    # --- reproduce a sheet's STRUCTURE elsewhere (v1.196.0) ---------------
    # "apply this layout to the workbook" reached NOTHING before this wave:
    # `excel_apply_spec` was absent from `AUTO_SAFE_TOOLS` and no rule ever
    # awarded it a point, in EITHER lane. The set above now carries it (see the
    # note there and the paired `agents/runtime._WRITE_TIER` entry) and this is
    # the scoring half.
    #
    # THE RULE IS WRITTEN AGAINST REAL SENTENCES, not against the tool's
    # dictionary key: `excel_apply_spec` as a WORD appears in none of them. Both
    # word orders match, because both are how the request is actually phrased —
    # "apply the firm's standard layout to this workbook" and "take the spec
    # from last year and apply it to this worksheet". The verb may also sit
    # AFTER the workbook noun ("review the workbook and apply ... to it"), which
    # is why the noun requirement is a start-anchored lookahead over the whole
    # message rather than a proximity window: a forward-only `(?=.*noun)` placed
    # at the verb misses every sentence that named the workbook first, which was
    # the commonest phrasing in the set this was measured on.
    #
    # `excel_sheet_spec` RIDES WITH IT, and that is not padding. `apply_spec`
    # takes a `spec` object that `excel_sheet_spec` produces — its own
    # description says "Feed the spec to excel_apply_spec to reproduce the sheet
    # elsewhere" — so arming the applier alone is the same shape as arming a
    # listing tool that cannot see the folder: a tool present but structurally
    # unable to do the job it was armed for. It is READONLY, so it adds no tier.
    #
    # THE REVERSED BRANCH TAKES THE NARROWER VERB LIST on purpose: "use" and
    # "match" are everyday words, and reading them BACKWARDS from a structure
    # noun ("the format ... use") over-matches ordinary prose, while the strong
    # verbs (apply/reproduce/replicate/recreate/mirror) do not.
    #
    # MEASURED SLOT COST — the honest statement, and the first draft of this
    # comment got it WRONG by guessing at it ("file_search is the only tool
    # displaced"). Swept over 48 sentences against the pre-rule selector: TEN
    # change, all ten of them this rule's own class, and NOTHING ELSE in the
    # corpus moves — no other sentence gains, loses or reorders a single tool.
    # The cap is 6 and the excel family already fills five slots, so two
    # additions always cost two. WHICH two depends on the noun the user used:
    #   * "workbook"/"sheet"/"cells" (7 of the 10) — `file_search` (3) drops.
    #     Right trade: the sentence names the workbook the user is looking at,
    #     so finding it is not the hard part.
    #   * "spreadsheet"/"xlsx" (3 of the 10) — those words ALSO fire the
    #     doc-noun rule above (`read_document` 6, `file_search` +4), which
    #     lifts `file_search` to 7 and pushes `excel_edit` (6) and
    #     `read_document` (6) off instead. Also right, and worth stating
    #     plainly: the user asked to REPRODUCE A STRUCTURE, which is exactly
    #     `excel_apply_spec`'s job and not `excel_edit`'s cell-by-cell one, and
    #     `excel_read`/`excel_profile`/`excel_query` read a workbook better than
    #     `read_document`'s flattening does. Note what this means for consent:
    #     on those three the number of MUTATING tools armed does not rise at all
    #     — one workbook writer is swapped for another.
    #
    # THAT CONSENT NOTE IS ABOUT *THIS MODULE ONLY*, and it is scoped here
    # because an earlier draft let it be read as a property of the app. This
    # function scores the SENTENCE. `daemon/chat_turn._resolve_armed_tools` runs
    # a SECOND pass that arms from the ATTACHMENT'S TYPE (v1.196.0), filtered
    # through `AUTO_SAFE_TOOLS` — so admitting `excel_apply_spec` to that set
    # above also admits it there, where no sentence rule is consulted at all.
    # Measured: "thanks!" + `client_fees.xlsx` arms BOTH `excel_edit` and
    # `excel_apply_spec`. Nothing in this file bounds that; if you are reasoning
    # about consent, read that pass too.
    #
    # v1.196.0 round 5 fronts BOTH branches' verbs with `_imperative()`. Measured
    # leak: "what does it mean to apply a layout to a workbook?" armed
    # `excel_apply_spec` (and, since the round-4 repair below awards them
    # together, `excel_edit`) on a definition question. Every sentence in
    # `tests/test_autoselect_gaps_v1196._APPLY_SPEC_SENTENCES` is unaffected —
    # nine open with the verb and the tenth reaches it through "and apply".
    (
        re.compile(
            rf"\A(?=[\s\S]*\b(?:{_XL_NOUNS})\b)[\s\S]*?"
            r"(?:" + _imperative()
            + r"(?:apply|reproduce|replicate|re-?create|mirror|match|use)\b"
            r".{0,40}"
            r"\b(?:spec|layout|format(?:ting|s)?|template|styling|structure)\b"
            r"|\b(?:spec|layout|format(?:ting|s)?|template|styling|structure)\b"
            r".{0,40}?" + _imperative()
            + r"(?:apply|reproduce|replicate|re-?create|mirror)\b)",
            re.IGNORECASE,
        ),
        # `excel_edit` RIDES ALONG, and that is the CONSENT INVARIANT, not a
        # convenience (v1.196.0 round 4 repair). The claim made for adding
        # `excel_apply_spec` to the auto-allow set was never "it is harmless" —
        # it is a MUTATOR — but "it reaches no request the equally-mutating
        # `excel_edit` could not already reach", so a user whose Auto toggle was
        # consent for `excel_edit` is not handed a new class of turn.
        #
        # That used to hold because BOTH this rule and the bare spreadsheet rule
        # keyed on `_XL_NOUNS`, and that one awarded `excel_edit`. Round 4 had to
        # take the editor off the bare rule (a noun is topic, not intent: it was
        # arming the editor on "what does this spreadsheet say?"), which would
        # have left `excel_apply_spec` reaching FURTHER than `excel_edit` — a
        # widening, arriving as a side effect of a narrowing. Awarding both from
        # the ONE rule that matches is a stronger guarantee than the old
        # shared-constant argument: they now cannot diverge by construction,
        # because a single match awards them together.
        {"excel_apply_spec": 9, "excel_sheet_spec": 8, "excel_edit": 6},
    ),
    # --- editing a sheet's STRUCTURE (v1.196.0 round 4) --------------------
    # Measured through `chat_turn._resolve_armed_tools` with `client_fees.xlsx`
    # attached: "add a column for the tax rate" armed
    # ['read_document', 'excel_profile', 'excel_query', 'excel_read'] — every
    # READ verb of the workbook table and NO editor, because the round-3 consent
    # gate asks THIS function whether the request wanted a change and the
    # sentence scored none. It scored none because the spreadsheet rule above
    # keys on `_XL_NOUNS`, and a user asking for a column says "column", not
    # "spreadsheet": they are looking at the file they just attached.
    #
    # THE NOUNS ARE EXACTLY THE ONES `_XL_NOUNS` DOES NOT HOLD — column, row,
    # tab, header — and that is a deliberate exclusion, MEASURED BOTH WAYS
    # rather than argued, because the first draft of this comment argued it and
    # got the arithmetic wrong. The claim it made ("with `cells|sheet|formulas`
    # included, 'update cell B2 to 500' moves to an `excel_edit`-led list") is
    # FALSE: that sentence does not move at all, because `update` is not one of
    # this rule's verbs. What actually happens when those three nouns are added,
    # swept over the 105-row corpus plus six probe sentences: SIX sentences
    # change and every one of them changes ORDER ONLY — "delete the sheet",
    # "rename the sheet to Q1", "insert a formula in C4", "add a cell for the
    # rate", "remove the formulas" all arm the SAME tools with `excel_edit`
    # promoted to the lead. Not one new sentence is reached, because
    # `_XL_NOUNS` already scored the whole excel family on every one of them.
    # That is the v1.173.0 possessive-store trap exactly ("a full-weight rule
    # here does not widen the vocabulary — it DOUBLE-SCORES the sentences that
    # already matched"), and reordering rows this unit was not chartered to
    # touch is a cost with no coverage on the other side of it. Note honestly
    # what that leaves: "delete the sheet" still LEADS with `excel_query`, which
    # is a wrong door. Fixing that is a lead-calibration change to the
    # spreadsheet rule, not a noun this rule should quietly duplicate.
    #
    # `excel_read` rides at 3 because an editor with no reader beside it is the
    # "armed but structurally unable" shape §1 of the test file is about: the
    # model has to see the sheet's shape before it can add a column to it. Not
    # `excel_query`/`excel_profile` — those are computed-figure tools and the
    # sentence asked for a structural edit — and deliberately not `excel_query`
    # for a second reason: this rule must not become a second member of the
    # "awards excel_edit AND excel_query" family the apply-spec consent
    # invariant identifies the spreadsheet rule by.
    #
    # v1.196.0 round 5 fronts it with `_imperative()`. This rule's verbs are the
    # most ordinary words in the module and it leaked the hardest: "should I add
    # a column for the tax rate?", "did they add a column?", "I wonder if you
    # could add a column", "can excel add a column automatically?" and "don't add
    # a column for the tax rate" all armed `excel_edit`. Note the last two: one
    # was missed by round 4's guard because the enquiry branch was `\A`-anchored,
    # the other caught by it — and the position test refuses BOTH with the same
    # single check on the word before the verb.
    (
        re.compile(
            _imperative()
            + r"(?:add|insert|append|remove|delete|drop|rename|reorder|"
            r"resize|widen|hide|unhide|freeze|fill\s+in|populate)\b"
            r"[^.!?]{0,30}?\b(?:columns?|rows?|tabs?|headers?)\b",
            re.IGNORECASE,
        ),
        {"excel_edit": 8, "excel_read": 3},
    ),
    # --- changing a FIGURE in a sheet (v1.196.0 round 4) -------------------
    # "change the fee for Belmont to 3000" + `client_fees.xlsx` armed
    # ['read_document', 'excel_profile', 'excel_query', 'excel_read'] — the same
    # shape as the rule above and the harder half of it, because this sentence
    # names NO spreadsheet noun AT ALL. It is the phrasing the live ledger is
    # full of, and it is an anaphor: "the fee" is a cell in the workbook the user
    # is looking at.
    #
    # THE PRECISION IS CARRIED BY THREE REQUIREMENTS TOGETHER, and none of them
    # is sufficient alone: a CHANGE verb, a FIGURE noun (the vocabulary of a
    # workbook cell — fee/amount/total/rate/balance/price...), and a NUMERIC
    # target after `to`. "change the meeting to 3pm" has the verb and a numeral
    # and no figure noun; "change the wording to be more formal" has the verb and
    # neither; "what do these fees add up to?" has the noun and no change verb.
    # All three measured empty of `excel_edit`.
    #
    # THE NUMERIC TARGET IS THE HONEST LIMIT, stated rather than glossed (the
    # NEVER SILENTLY DEGRADE rule applies to a rule's own reach). Measured, these
    # do NOT fire and arm nothing at all: "change the fee for Belmont to whatever
    # we billed last year", "change the client name in row 4 to Belmont LLC"
    # (`change` is not one of the structure rule's verbs either, so nothing above
    # catches it — an earlier draft of this comment claimed it did, and that was
    # wrong), "update the totals". A non-numeric target is routinely a sentence
    # about prose ("change the rate description to something plainer"), and a
    # workbook MUTATOR is the wrong thing to be wrong about, so this rule is
    # deliberately narrower than the request it serves. The unreached phrasings
    # are listed in `tests/test_autoselect_gaps_v1196.py::_STILL_OPEN`.
    #
    # v1.196.0 round 5 fronts it with `_imperative()`. Measured leak: "the memo
    # says to change the fee for Belmont to 3000" — a REPORT of an instruction,
    # armed the editor. So did "we should never change the fee ... to 3000",
    # which round 4 caught only because the message contained the word "never";
    # the position test catches it because the word before the verb is "never".
    (
        re.compile(
            _imperative()
            + r"(?:change|update|set|correct|fix|adjust|bump|raise|lower|"
            r"increase|decrease)\b[^.!?]{0,30}?"
            r"\b(?:fees?|amounts?|totals?|subtotals?|rates?|balances?|prices?|"
            r"costs?|values?|figures?|salar(?:y|ies)|hours|units|"
            r"quantit(?:y|ies)|qty)\b[^.!?]{0,25}?\bto\s+(?:\$|\d)",
            re.IGNORECASE,
        ),
        {"excel_edit": 8, "excel_read": 3},
    ),
    # --- a NAMED CELL, with a value to put in it (v1.196.0 round 4 repair) ----
    # "update cell B2 to 500" used to reach `excel_edit` through the BARE
    # `_XL_NOUNS` rule (`\bcells?\b` is one of its nouns) — the same rule that
    # handed the editor to "what does this spreadsheet say?". Dropping the
    # editor from there closed that leak and cost this phrasing, so it is
    # awarded here instead, where a change VERB and a target VALUE are both
    # required and a bare mention cannot qualify.
    #
    # AWARDS `excel_edit` ALONE, at the weight the bare rule used to give it (6).
    # Folding `cells?` into the figure rule above would have been shorter and was
    # measurably wrong: that rule also awards `excel_read`, so the extra points
    # re-ordered the armed list for every named-cell request and broke two
    # pinned orderings. A rule that restores one tool must not perturb the rank
    # of the others.
    #
    # v1.196.0 round 5 makes TWO changes. (1) `_imperative()` fronts the verb —
    # "remind me how to update cell B2 to 500" armed the editor on a request for
    # INSTRUCTIONS. (2) `write`, `correct` and `fix` are GONE from the verb list.
    # They are not cell-edit verbs, and `write` in particular is an ordinary
    # authoring word that sits in imperative position all the time: measured,
    # "can you write a note about which cells changed to 500" armed `excel_edit`
    # — a polite, genuinely imperative request that asks for PROSE. The position
    # test cannot save that one and was never meant to; the verb list is the
    # right place for it. Nobody says "write cell B2 to 500". (`put` and `enter`
    # stay: "put 500 in cell B2", "enter 3000 in the fee cell".)
    (
        re.compile(
            _imperative()
            + r"(?:change|update|set|adjust|bump|raise|lower|"
            r"increase|decrease|put|enter)\b[^.!?]{0,30}?"
            r"\bcells?\b[^.!?]{0,25}?\bto\s+(?:\$|\d|\"|')",
            re.IGNORECASE,
        ),
        {"excel_edit": 6},
    ),
    # --- the WORKBOOK ITSELF as the object of a change (v1.196.0 round 5) ------
    # THE OTHER HALF OF THE ROUND-4 REPAIR, and the larger half. Round 4 took
    # `excel_edit` off the bare `_XL_NOUNS` rule because a noun is TOPIC and not
    # INTENT ("what does this spreadsheet say?" was arming the editor). That
    # closed a real consent leak and cost SIXTEEN genuine change requests, of
    # which exactly one had a failing test —
    # `tests/test_brain_reach_v1173.py::test_a_store_noun_in_passing_never_
    # evicts_the_sentences_own_tool` went RED on "our records show a $500
    # payment; add it to the spreadsheet and total by client" and stayed red
    # through round 4. The other fifteen were silent.
    #
    # TWO RULES, because the class splits cleanly in two and the two need
    # different shapes:
    #
    #   (a) DESTINATION — something goes INTO the workbook. "add the new client
    #       TO the workbook", "put the payment IN the spreadsheet", "copy last
    #       year's numbers INTO the spreadsheet". Verb, then a bridge, then a
    #       preposition and the noun.
    #   (b) DIRECT OBJECT — the workbook IS what is being changed. "clear the
    #       sheet", "rename the sheet to Q1", "sort the spreadsheet by client",
    #       "tidy up this spreadsheet", "fix the formulas in the sheet". Verb,
    #       determiner, noun, no preposition in between.
    #
    # `(?!\s+up\b)` ON THE DESTINATION VERBS IS LOAD-BEARING. The phrasal "add
    # up to" collides head-on with "add ... to <spreadsheet>" and is everywhere
    # in this domain: "add up the fees in the spreadsheet" is a COMPUTATION, and
    # `excel_query` is what should answer it. Measured — with the lookahead it
    # arms no editor; without it, it arms one.
    #
    # THE BRIDGE IS TEMPERED, not a plain `.{0,30}`. "write a note ABOUT the fees
    # IN the spreadsheet" is prose about a workbook, not an edit to one, and a
    # plain bridge armed the editor on it. Excluding the prepositions that
    # RELOCATE the verb's object (of/about/from/on/for/with/regarding) keeps
    # `in`/`into`/`onto`/`to` — the ones that name a DESTINATION — as the only
    # way across.
    #
    # NO READER RIDES ALONG, and that is not an oversight: both rules require a
    # noun from `_XL_NOUNS`, so the bare spreadsheet rule above has ALREADY
    # awarded `excel_query`/`excel_profile`/`excel_read` on every sentence either
    # of these can match. Adding `excel_read: 3` here (as the structure and
    # figure rules do — their nouns are column/row/fee, which `_XL_NOUNS` does
    # NOT hold) would double-score a reader to 10 and put it AHEAD of the editor
    # on a request to edit. That is the v1.173.0 possessive-store trap, and it
    # was measured on this exact rule before the reader was dropped.
    #
    # THE WEIGHT IS 10, ABOVE `excel_query`'s 9, and it is the only lead this
    # unit deliberately moves. The module's own round-4 note conceded the point
    # and left it: "'delete the sheet' still LEADS with `excel_query`, which is a
    # wrong door." A local model takes the tool at the top (the v1.174.0
    # evidence run), and on "clear the sheet" the query engine cannot do what was
    # asked. The lead moves ONLY on sentences these two rules match — a change
    # verb in imperative position with the workbook as its object — and the
    # corpus sweep in `tests/test_autoselect_gaps_v1196.py` pins that nothing
    # else moved.
    (
        re.compile(
            _imperative()
            + r"(?:add|append|put|insert|record|enter|log|write|paste|type|"
            r"copy|move|transfer)\b(?!\s+up\b)"
            r"(?:(?!\b(?:of|about|from|on|for|with|within|inside|regarding)\b)"
            r"[^.!?]){0,30}?"
            r"\b(?:to|in|into|onto)\s+(?:the|this|that|my|our|a|an)\s+"
            rf"(?:{_XL_NOUNS})\b",
            re.IGNORECASE,
        ),
        {"excel_edit": 10},
    ),
    (
        re.compile(
            _imperative()
            + r"(?:clear|wipe|blank|delete|remove|erase|drop|rename|re-?order|"
            r"re-?sort|sort|re-?format|format|tidy(?:\s+up)?|clean(?:\s+up)?|"
            r"fix|repair|correct|update|edit|modify|amend|revise|"
            r"fill(?:\s+in)?|populate|rebuild|restructure|reorgani[sz]e)\b"
            r"\s+(?:the|this|that|my|our|these|those)\s+(?:\w+\s+){0,2}"
            rf"(?:{_XL_NOUNS})\b",
            re.IGNORECASE,
        ),
        {"excel_edit": 10},
    ),
    # --- formula work / statement structure -------------------------------
    (
        re.compile(
            r"\b(formulas?|=sum|subtotal|validate|verify|reconcile|"
            r"financial statement|balance sheet|income statement|p&l|"
            r"trial balance|accounts? (?:added|removed|changed))\b",
            re.IGNORECASE,
        ),
        {"excel_formula_check": 7, "excel_sheet_spec": 5, "excel_accounts_diff": 5,
         "excel_profile": 4},
    ),
    # --- computed figures over sheets ------------------------------------
    (
        re.compile(
            r"\b(sum|total|average|mean|count|how (?:much|many)|breakdown|"
            r"by (?:client|month|category|vendor|account))\b",
            re.IGNORECASE,
        ),
        {"excel_query": 5, "excel_profile": 3},
    ),
    # --- PII redaction ----------------------------------------------------
    # SPLIT IN TWO IN v1.196.0 round 5, and this was a hole nothing had ever
    # gated. The rule fires on the bare NOUN "pii", so it awarded `redact_pii` —
    # which WRITES a `.redacted` file and is a `_WRITE_TIER` member on the agent
    # lane — to every sentence that merely mentioned redaction. Measured:
    #   "please do not redact the pii in this return"  -> redact_pii
    #   "why did you redact the ssn?"                  -> redact_pii
    #   "don't redact anything"                        -> redact_pii
    # Round 4's `_NOT_A_REQUEST` would have caught two of those, and it never
    # ran on them: `redact_pii` was absent from `_CHANGE_TOOLS`, which the
    # round-4 pin test justified only for the two MEMORY writers. It is in the
    # set now, and the split is what makes the set mean something.
    #
    # THE READ-ONLY HALF KEEPS THE WHOLE VOCABULARY. `redact_scan` writes
    # nothing — it is the confirm-first step — and a question about redaction
    # deserves it, so it stays on the ungated rule with `read_document` and
    # `file_search`. Only the writer moves behind the position test. It also
    # keeps its RANK: 10 vs 9 means `redact_scan` still leads its own sentence
    # (`_INTENT_LEADS` pins "redact the pii in these returns"), and the scan-then
    # -redact order is the app's highest-consequence path being careful on
    # purpose.
    (
        re.compile(
            r"\b(redact|pii|anonymi[sz]e|de.?identif(?:y|ied)|mask|scrub|"
            r"saniti[sz]e)\b",
            re.IGNORECASE,
        ),
        {"redact_scan": 10, "read_document": 5, "file_search": 4},
    ),
    (
        re.compile(
            _imperative()
            + r"(?:redact|anonymi[sz]e|de.?identify|mask|scrub|saniti[sz]e)\b",
            re.IGNORECASE,
        ),
        {"redact_pii": 9},
    ),
    # --- memory: recall + note-taking (v1.141.0) --------------------------
    # Intent words for remembering/recalling. The bare word "memory" (as in
    # "memory usage of the process" — a diagnostics question, not a recall
    # request) deliberately does NOT fire; the memory-noun branch requires a
    # possessive ("your/my/our memory") because that phrasing is about what
    # the assistant/user knows, never about RAM. remember/recall/note(s)/
    # "what do we know" carry the rest of the intent.
    #
    # v1.173.0 adds ``ltm_search`` here: this rule owned the memory VOCABULARY
    # but armed only the federated reader and the writer, so the one tool that
    # goes straight at the knowledge bases was missing from the very sentences
    # that ask for them. It stays BELOW ``recall`` — recall federates every
    # store (files, notes, graph, past conversations) and must keep the first
    # slot when a message is memory-ish.
    (
        re.compile(
            r"\b(remember|recall|notes?|what do (?:we|you) know|"
            r"(?:your|my|our) memor(?:y|ies))\b",
            re.IGNORECASE,
        ),
        {"recall": 8, "ltm_search": 6, "ltm_append": 5},
    ),
    # --- the GENERAL calling vocabulary (v1.173.0) ------------------------
    # The user's words: "multiple agents are to utilize this centralized brain,
    # so much of what long-term details are needed should be ALWAYS REACHABLE.
    # Also for calling it, terms like 'search your memory' or 'look into the
    # history' would be more general for all the users, not just myself."
    #
    # v1.172.x accreted THIS firm's nouns ("firm docs", "hermes brain"). A
    # brain shared by many agents has to answer to the words ANY user would
    # say, or long-term knowledge is reachable only by the person who named it.
    #
    # (a) A STORE NOUN under a possessive — "search your memory", "look into
    #     your memory", "check your notes", "your knowledge", "our records".
    #     No verb is required (the same call the wiki rule above makes): a
    #     possessive plus a store noun is already a statement about what WE
    #     hold. It cannot be the RAM sense ("memory usage of the process" has
    #     no possessive) and it cannot be a stranger's filing cabinet.
    #
    #     THE WEIGHTS ARE SMALL ON PURPOSE, and this is the whole subtlety of
    #     the rule. `memor(y|ies)` and `notes?` are ALREADY owned by the
    #     v1.141.0 rule above, so a full-weight rule here does not widen the
    #     vocabulary — it DOUBLE-SCORES the sentences that already matched, and
    #     the cap is 6. Measured at 8/7: "redact the pii in my notes pdf and
    #     save a new file in the folder" lost `redact_pii` (the user asked to
    #     redact and only `redact_scan` was armed — the v1.153.2 failure where
    #     the reply announces work no armed tool can do), "our records show a
    #     $500 payment; add it to the spreadsheet" lost `excel_edit`, and
    #     "extract the tables from the pdf of my notes and check the formulas
    #     in the sheet" lost `excel_formula_check`. This rule's job is to ADD
    #     the nouns the old rule never had (`knowledge`, `records`, `archives`,
    #     "the team's notes") and to nudge, not to outrank the verb the user
    #     actually wrote. Deleting the overlapping nouns instead is NOT the fix:
    #     "check your notes for the retention policy" then loses recall's first
    #     slot to `read_document`, because the nudge is what wins that tie.
    #     A possessive store noun does appear in pure declaratives with no
    #     lookup intent ("our records show a $500 payment", "to the best of my
    #     knowledge the return was filed"). At 3/2 that costs two read-only
    #     tools on a sentence that armed nothing else, and cannot cost a slot on
    #     a sentence that did — which is the only harm worth preventing.
    (
        re.compile(
            r"\b(?:your|our|my|the (?:shared|team'?s|firm'?s|company'?s))\s+"
            r"(?:own\s+|long[-\s]?term\s+|internal\s+|saved\s+|stored\s+)?"
            r"(?:memor(?:y|ies)|knowledge|notes?|records?|archives?)\b",
            re.IGNORECASE,
        ),
        {"recall": 3, "ltm_search": 2},
    ),
    # (b) The verb-shaped asks that name no store at all. Every alternative is
    #     anaphoric ("it"/"that") or first-person-plural ("we"/"you" = this
    #     assistant) — it refers to something ALREADY IN PLAY, which is what
    #     makes it a memory question instead of a web one:
    #       "look it up" / "look that up"  — deliberately NOT the web rule's
    #         `look up X`, which names an external subject and keeps its own
    #         sentence ("look up the IRS phone number online" must stay a web
    #         search; pinned both ways in tests/test_brain_reach_v1173.py).
    #       "what do we have on the Henderson file" / "what do you have on X"
    #       "dig up ..." / "pull up what we have"
    #
    #     `recall` is weighted 7, one BELOW the web rule's 8, and that single
    #     point is load-bearing. "look it up ON GOOGLE" and "look it up ONLINE"
    #     match this rule AND the web rule; at equal weight recall won the
    #     alphabetical tiebreak, so an unambiguous web request led with the
    #     brain. Both tools still arm (the anaphor may well point at something
    #     we already hold), but when the user names the web the web goes first.
    #     Nothing else changes: with no web marker present this rule is the only
    #     one firing, so "look it up" is still exactly `recall` + `ltm_search`.
    (
        re.compile(
            r"\blook\s+(?:it|that|this|them|these|those)\s+up\b|"
            r"\bwhat (?:do|does) (?:we|you) have\b|"
            r"\bwhat have (?:we|you) got\b|"
            r"\bdig\s+(?:up|out)\b|"
            r"\bpull\s+up\s+(?:what|everything|anything|all)\b",
            re.IGNORECASE,
        ),
        {"recall": 7, "ltm_search": 6},
    ),
    # (c) "look into the history" / "search the history" — the user's other
    #     example. TWO surfaces answer to that word and they are not the same
    #     thing: ``history_search`` reads THIS APP's own threads, ``ltm_search``
    #     reads the knowledge bases, ``recall`` federates both. The phrase names
    #     the past record without saying which, so it arms all three,
    #     history-first, rather than guessing (a wrong single pick reads to the
    #     user as "we never discussed that").
    #
    #     PRECISION, decided case by case: the article must sit directly
    #     against the noun, which keeps out every history that is NOT our
    #     record — "the BROWSER history", "the GIT history", "the REVISION
    #     history" all fail the match because the intervening word is not one
    #     of the conversation nouns. And an "of" immediately after means the
    #     word is a TOPIC, not a place to look ("look into the history OF the
    #     S-corp election"), so the lookahead drops it.
    #
    #     Two asymmetries were fixed after v1.173.0's first cut, both of which
    #     sent a question about the user's OWN record to the internet — the
    #     precise failure this wave exists to remove:
    #       * `look`/`dig`/`comb`/`go back` accepted a preposition but
    #         `search`/`check`/`review`/`scan` had to sit against the article,
    #         so "look through the history" matched and "search THROUGH the
    #         history" did not — and since the web rule owns the bare verb
    #         `search`, that sentence armed web_search and nothing else.
    #       * the conversation nouns were singular-only: "search our message
    #         history" reached the thread index, "search our messageS history"
    #         went to the web. Both are the same sentence.
    (
        re.compile(
            r"\b(?:(?:search|check|review|scan)"
            r"(?:\s+(?:back\s+)?(?:through|in|into))?|"
            r"look\s+(?:in|into|through|back\s+(?:in|through|at))|"
            r"dig\s+(?:in|into|through)|go\s+back\s+through|"
            r"comb\s+through)\s+"
            r"(?:the|our|your|my|this)\s+"
            r"(?:(?:conversation|chat|message|thread|session)s?\s*)?"
            r"histor(?:y|ies)\b(?!\s+of\b)",
            re.IGNORECASE,
        ),
        {"history_search": 8, "recall": 7, "ltm_search": 4},
    ),
    # --- the KNOWLEDGE-BASE vocabulary (v1.172.0) -------------------------
    # A live report: "it has no access to the wikis — blind as a bat." The
    # bases were registered and `recall` was armable, but NOTHING here matched
    # the word people actually use. "What does our wiki say about X" armed no
    # tool at all and answered from thin air — the worst possible failure for
    # a question that names a source. These nouns are unambiguous (a wiki /
    # handbook / runbook / knowledge base IS a store to look in), so they arm
    # BOTH the memory bases and file search: a "wiki" is a markdown vault for
    # one user and a folder of docs for the next, and the model should be able
    # to reach whichever it is rather than guess.
    #
    # The possessive list is deliberately concrete rather than "any word +
    # docs": it was widened after testing against a real firm's wiki, where
    # "look in the FIRM docs for the client template" armed nothing at all —
    # the vocabulary has to be the user's, not the developer's. "the/our
    # brain" is here for the same reason: it is what people call the store
    # once it has a name (the bases here are literally `brain` and
    # `hermes-brain`), and the definite article keeps it away from "brain
    # surgery" prose.
    (
        re.compile(
            r"\b(wikis?|knowledge ?base|documentation|handbooks?|runbooks?|"
            r"playbooks?|(?:the|our|company|team|internal|firm|office|practice) docs|"
            r"(?:the|our|my|hermes) brains?)\b",
            re.IGNORECASE,
        ),
        {"recall": 8, "ltm_search": 7, "file_search": 6},
    ),
    # --- past conversations (v1.142.0) ------------------------------------
    # "what did we decide about X", "find the thread where we discussed Y",
    # "when did we talk about Z" — a question about THIS APP'S OWN history,
    # which is exactly what history_search reads. THREE alternatives:
    #
    # 1. an asking verb AND a conversation noun within 30 chars. Deliberately
    #    narrow — "search the web", "find the file", "search my documents for
    #    the S-corp election" and "search my notes for X" carry no conversation
    #    noun and must NOT fire (pinned by negative tests; the last two are the
    #    adversarial pair, since they differ from a real hit by ONE word).
    # 2. ``<wh-word> did we`` — "WHAT did we decide about the S-corp election",
    #    "when did we settle on the 15th", "why did we drop that client", "how
    #    did we handle this last year". This is the form the tool's own
    #    description leads with, and enumerating verbs missed it: the first cut
    #    listed discuss/talk/say, so "what did we DECIDE about X" — the spec's
    #    headline example — armed nothing at all. Interrogative + "did we" is
    #    past-tense by construction and cannot be about a file or a web page,
    #    so it is high-precision without a verb list to keep chasing.
    # 3. the same verb group after "we", for phrasings that skip the wh-word
    #    ("find where we agreed on the fee").
    (
        re.compile(
            r"\b(?:find|search|which|what)\b.{0,30}"
            r"\b(?:conversations?|chats?|threads?|"
            r"we\s+(?:discuss|talk|say|said|decid|agree|settl|conclud|chose))"
            r"|\b(?:what|when|which|where|why|how) did we\b",
            re.IGNORECASE,
        ),
        {"history_search": 8},
    ),
    # --- saved workflows (v1.170.0) ---------------------------------------
    # "run my month-end workflow", "what workflows do I have" — the module is
    # reachable from chat. Only the READ-ONLY lister is auto-armed (see the
    # AUTO_SAFE_TOOLS note); with it in hand the model can name real saved
    # workflows instead of guessing, and workflow_run stays consent-gated.
    (
        re.compile(r"\bworkflows?\b", re.IGNORECASE),
        {"workflow_list": 7},
    ),
    # --- the app itself (v1.224.0) ------------------------------------------
    # "how do I turn on computer use", "where in the app is the undo page",
    # "what does the Reflex page do", "do I have a schedule for …" — questions
    # ABOUT Iron Jarvis or about the user's own things inside it. Narrow on
    # purpose: a bare "what is" must not arm a lookup on every chat question.
    (
        re.compile(
            r"\b(?:iron\s*jarvis|this\s+app|the\s+app|the\s+dashboard|"
            r"which\s+page|what\s+page|where\s+in\s+the\s+app|"
            r"how\s+do\s+i\s+(?:use|set|find|open|turn|enable|disable|change|configure)|"
            r"do\s+i\s+have\s+(?:a|an|any)\s+(?:project|workflow|schedule|reflex|goal|skill|agent)|"
            r"where\s+(?:is|are)\s+my\s+(?:project|workflow|schedule|reflex|goal|skill|agent)s?)\b",
            re.IGNORECASE,
        ),
        {"guide_search": 6, "app_search": 5, "app_status": 3},
    ),
    # --- images -----------------------------------------------------------
    # v1.196.0 round 5 fronts this with `_imperative()` too: `image_convert` and
    # `image_resize` are `_CHANGE_TOOLS`, so "why did you resize the photo?" was
    # a silent grant of two file writers. `image_info` (READONLY) rides on the
    # gated rule rather than getting an ungated twin — it is armed by no other
    # rule, and losing it on an ENQUIRY about resizing costs a turn nothing the
    # `view_image` rule below does not already cover.
    (
        re.compile(
            _imperative()
            + r"(?:resize|convert|compress|shrink|scale)\b.{0,40}"
            r"\b(image|photo|picture|png|jpe?g|screenshot)s?\b",
            re.IGNORECASE,
        ),
        {"image_convert": 6, "image_resize": 6, "image_info": 3},
    ),
    (
        re.compile(
            r"\b(what(?:'s| is) (?:in|on)|describe|look at|read)\b.{0,30}"
            r"\b(image|photo|picture|screenshot)s?\b",
            re.IGNORECASE,
        ),
        {"view_image": 7},
    ),
]

#: The positional-branch group names each rule carries, in ``_RULES`` order.
#: Read off the COMPILED pattern rather than maintained by hand — a rule that
#: fronts three verbs with :func:`_imperative` (the convert rule does) gets three
#: names automatically, and a rule that fronts none gets an empty tuple and takes
#: the cheap ungated path in :func:`select_auto_tools`.
_RULE_POS_GROUPS: list[tuple[str, ...]] = [
    tuple(n for n in rx.groupindex if n.startswith(_POS_PREFIX)) for rx, _ in _RULES
]

#: A bound on the retry loop below. NOTHING BLOCKING RUNS ON THE EVENT LOOP
#: (the v1.153.1 rule): every refused position costs another scan of the rest of
#: the message, and a 4,000-character paste full of newlines could otherwise ask
#: for hundreds. Twelve is far past any real sentence and turns the worst case
#: into a constant. Exhausting it REFUSES (the rule awards nothing), because the
#: only positions left to try are ones the gate has already been declining.
#:
#: THAT LAST SENTENCE IS A SAFETY CLAIM AND IT HAD NO TEST. Making exhaustion
#: AWARD instead of refuse left all 196 tests green, while a real turn changes
#: answer: "his email said:" followed by twenty quoted bullets refuses today and
#: arms ``pdf_arrange`` under the mutation — a long quoted list is exactly the
#: input that runs the counter out, so the branch nothing covered was the branch
#: the bound exists for. ``test_a_long_quoted_list_exhausts_the_retries_and
#: _REFUSES`` in tests/test_change_intent_guard_v1196.py drives it.
_MAX_POSITION_RETRIES = 12


def select_auto_tools(
    text: str,
    *,
    attachments: list[str] | None = None,
    exclude: set[str] | frozenset[str] | None = None,
    cap: int = 6,
    max_tools: int | None = None,
) -> list[str]:
    """Score *text* (the last user message) + attachment file names and return
    up to *cap* auto-armable tool names, best signal first. Tools in *exclude*
    (the user's explicit picks) are never repeated. Returns ``[]`` for plain
    conversation — no signal, no tools, no latency.

    *max_tools* is the CAPABILITY ENVELOPE's arming budget (v1.202.0):
    ``envelope/profile.CapabilityProfile.max_tools()`` for the model answering
    this turn. ``None`` — no envelope cap (trusted/unmeasured profiles, and
    every pre-envelope caller) — leaves the selection byte-identical to
    v1.201.0. An int is folded in as ``min(cap, max_tools)`` and truncates at
    the SAME ranked slice today's *cap* does; a value ``<= 0`` means the
    envelope budget is already spent and selects nothing. See the comment at
    the fold below for the rationale and the explicit-picks contract.
    """
    # v1.202.0 — THE ENVELOPE CAP, folded into `cap` so it truncates at the one
    # slice that has always truncated (`ranked[:cap]` below). WHY IT EXISTS: a
    # measured local model picks from the top of a menu, and the menu's WIDTH is
    # itself a failure mode — the evidence run behind v1.174.0 (quoted at
    # `agents/runtime.arm_for_task`: "five `shell` calls where `read_file` was
    # sitting right there") shows a weak model choosing the wrong door simply
    # because it was offered. The envelope's `max_tools()` turns that from a
    # fixed guess (6 for everybody) into a measured band: a model that held the
    # native tool rung keeps today's cap (None), one that wobbled gets 4 or 3.
    #
    # TWO CONTRACTS RIDE ON THE FOLD, both directions preserved by construction:
    #
    # * EXPLICIT PICKS ARE NEVER THE ENVELOPE'S TO DROP. A "+" pick is a CONSENT
    #   STATEMENT — the interactive grant the permission engine's session_allow
    #   is built on (module docstring) — and a measured capability band is a
    #   statement about model SKILL. Skill evidence must not override consent:
    #   the user who armed five tools by hand gets five tools, however weak the
    #   model measured. That holds structurally here because this function
    #   returns AUTO slots only (explicit picks arrive via `exclude` and are
    #   never in the ranking), so `max_tools` can only ever shrink the auto
    #   contribution. Callers uphold the other half: pass the envelope's
    #   REMAINING budget after their explicit picks (`max_tools - len(explicit)`
    #   in `chat_turn._resolve_armed_tools`' shape), never a truncated explicit
    #   list — when the picks alone meet the band, the remainder is <= 0 and
    #   auto arming yields entirely while every pick survives.
    # * OVER-NAMING beats OVER-ARMING, exactly as with `cap` today. `cap` is an
    #   ARMING budget, not an intent verdict — the attachment consent gate
    #   (`documents/attachment_rag.change_verbs_wanted`, "THE SCORER IS RUN
    #   UNCAPPED") deliberately calls this function with the cap wide open so a
    #   verb the sentence genuinely scored is never invisible to consent just
    #   because unrelated tools outranked it. `max_tools` is the same kind of
    #   budget and takes the same treatment: intent-gate callers keep the
    #   default None, and the envelope narrows only what gets ARMED, never what
    #   the request is understood to have asked for.
    if max_tools is not None:
        cap = min(cap, max_tools)
    if cap <= 0:
        return []
    skip = set(exclude or ())
    scores: dict[str, int] = {}

    def bump(weights: dict[str, int]) -> None:
        for name, w in weights.items():
            scores[name] = scores.get(name, 0) + w

    msg = (text or "")[:4000]
    cut = -1  # lazy: the _ENQUIRY scan is only paid for if a gated rule matches
    for (rx, weights), gates in zip(_RULES, _RULE_POS_GROUPS):
        if not gates:
            if rx.search(msg):
                bump(weights)
            continue
        # A GATED RULE, and the shape here is what keeps the gate off the hot
        # path. The FIRST search is exactly the one the ungated branch does, so a
        # rule that does not fire costs what it always did; only a rule that DID
        # fire pays for the marker scan, and only a rule whose match came through
        # a POSITIONAL branch pays for a retry.
        m = rx.search(msg)
        tries = 0
        while m is not None and tries < _MAX_POSITION_RETRIES:
            hit = next((g for g in gates if m.group(g) is not None), None)
            if hit is None:  # a CONTEXT-FREE branch matched: award, no context
                break
            if cut < 0:
                found = _ENQUIRY.search(msg)
                cut = found.start() if found else len(msg) + 1
            if _position_allows(msg, m.start(hit), m.group(hit), cut):
                break
            # Retry from just AFTER the refused position rather than after the
            # whole match: a rejected match can be long enough to swallow a
            # legitimate later one, and `finditer` would skip it.
            m = rx.search(msg, m.start(hit) + 1)
            tries += 1
        if m is not None and tries < _MAX_POSITION_RETRIES:
            bump(weights)
    if _URL_RX.search(msg):
        bump({"web_fetch": 9, "web_search": 3})
    if _PATH_RX.search(msg):
        bump({"read_file": 6, "file_search": 4, "list_files": 3})
        # v1.196.0: a typed path whose LAST SEGMENT carries no extension is a
        # folder ("C:\\Users\\VR\\clients\\2024"), and the only tool that can
        # list a real one is `list_folder` — `list_files` is workspace-confined.
        # Weighted at the reader's level rather than above it: the extension
        # test is a heuristic (extensionless files exist), so this nudges the
        # listing alongside the read, it does not replace it. One bump however
        # many folder-ish paths appear — a sentence naming three folders is not
        # three times the signal.
        if any(
            "." not in m.group(0).replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
            for m in _PATH_RX.finditer(msg)
        ):
            bump({"list_folder": 6})
    # v1.210.0: a bare source/config FILENAME ("main.py", "Cargo.toml") is a
    # read signal `_PATH_RX` cannot see (no separator). Same weights as the
    # typed-path reader above, READ tools only — naming a file is consent to
    # read it, and mutations stay with the change-verb machinery. One bump
    # however many filenames appear (the `_PATH_RX` convention: three names
    # are not three times the signal).
    if _CODE_FILE_RX.search(msg):
        bump({"read_file": 6, "file_search": 4})
    for name in attachments or []:
        if _DOC_EXT_RX.search(name):
            bump({"read_document": 9})
        elif _IMG_EXT_RX.search(name):
            bump({"view_image": 9})

    # NO POST-PASS SUPPRESSION HERE ANY MORE (v1.196.0 round 5). Round 4 revoked
    # every `_CHANGE_TOOLS` member at this point whenever `_NOT_A_REQUEST`
    # matched. That guard is GONE, not moved: the discriminator was wrong in both
    # directions (see :func:`_imperative` for the measurements), and its removal
    # is also what gives the AGENT lane back what it lost — `arm_for_task` calls
    # this same function with no consent argument at all, so a revoke-based scan
    # applied here was pure capability loss on that side for zero safety gain.
    # Intent is now decided where it is scored, per rule, per verb.
    ranked = sorted(
        (
            (name, score)
            for name, score in scores.items()
            if name in AUTO_SAFE_TOOLS and name not in skip
        ),
        key=lambda kv: (-kv[1], kv[0]),
    )
    return [name for name, _ in ranked[:cap]]


#: ASK-TIER candidates (v1.187.0): tools worth SHOWING the model in an
#: interactive chat even though every call must be approved by the user first.
#: Deliberately not merged into AUTO_SAFE_TOOLS — that set is the auto-ALLOW
#: vocabulary and these must never ride it: they arm (join tool_specs) without
#: joining the turn's grant, so calling one pauses the turn for an approval
#: card. That gate is what makes visibility safe: before it, arming `shell`
#: here would have handed a headless-style silent grant to the exact tool the
#: deny floor exists to keep behind a human.
ASK_TIER_TOOLS: frozenset[str] = frozenset({"shell", "repl"})

#: Signals that a task genuinely wants host reach. CONSERVATIVE ON PURPOSE:
#: a false arm costs schema context and — if the model bites — a click the
#: user did not need to make, and the measured local-model failure mode is
#: choosing `shell` while `read_file` sits beside it (the v1.178.0 lesson).
#: Bare "run"/"start" are everyday office words ("run through the numbers"),
#: so each rule needs a second word that names the HOST, not the errand.
_ASK_RULES: list[tuple[re.Pattern[str], dict[str, int]]] = [
    (
        re.compile(
            r"\b(?:run|execute|launch)\b.{0,40}\b(?:command|script|terminal|"
            r"shell|\.(?:ps1|bat|cmd|sh|py|exe))",
            re.IGNORECASE,
        ),
        {"shell": 8},
    ),
    (
        re.compile(
            r"\b(?:install|uninstall|pip|npm|winget|choco|git\s+(?:clone|pull|"
            r"push|status|commit)|npx|pnpm|docker)\b",
            re.IGNORECASE,
        ),
        {"shell": 8},
    ),
    (
        re.compile(r"\b(?:command line|command prompt|powershell|cmd\.exe|bash)\b", re.IGNORECASE),
        {"shell": 6},
    ),
    (
        re.compile(
            r"\b(?:python|pandas|dataframe|numpy)\b|\bcalculate\b.{0,40}\b(?:from|"
            r"across|every|all)\b",
            re.IGNORECASE,
        ),
        {"repl": 5},
    ),
]


def select_ask_tools(text: str, *, cap: int = 2) -> list[str]:
    """ASK-TIER counterpart of :func:`select_auto_tools` (v1.187.0).

    Returns up to *cap* :data:`ASK_TIER_TOOLS` names the message signals a
    genuine need for, best signal first — for the INTERACTIVE lane only. The
    caller must arm these as visible-but-ungranted (in ``tool_specs``, never in
    the turn's allow overrides), so a call pauses for the user's approval.

    A SEPARATE FUNCTION rather than a flag on ``select_auto_tools``, because
    the two answers go to different places: one list becomes grants, the other
    becomes questions, and a caller that conflates them has silently armed the
    host shell. Two return values with opposite security meaning deserve two
    names.
    """
    if cap <= 0:
        return []
    scores: dict[str, int] = {}
    msg = (text or "")[:4000]
    for rx, weights in _ASK_RULES:
        if rx.search(msg):
            for name, w in weights.items():
                scores[name] = scores.get(name, 0) + w
    ranked = sorted(
        ((n, s) for n, s in scores.items() if n in ASK_TIER_TOOLS),
        key=lambda kv: (-kv[1], kv[0]),
    )
    return [name for name, _ in ranked[:cap]]


def tools_named_in_playbook(
    instructions: str,
    *,
    exclude: set[str] | frozenset[str] | None = None,
    cap: int = 6,
    max_tools: int | None = None,
) -> list[str]:
    """Safe-set tools a SKILL's playbook explicitly names, in first-mention order.

    Invoking a skill with "/" says what the user wants far more precisely than
    the sentence they typed, but auto-arming only ever read that sentence. So
    "/pii-redaction" + "skill for the attached" armed NOTHING: the playbook told
    the model to call ``redact_scan``, the tool was not in its tool list, and the
    only honest move left was to tell the user to switch to Agent mode. The
    skill knows its own tools — read them off it.

    Restricted to :data:`AUTO_SAFE_TOOLS` on purpose. A tool name appearing in
    prose is a weak signal, and it must never be enough to hand a skill ``shell``
    or computer control; those stay behind explicit arming.

    *max_tools* (v1.202.0) is the same envelope arming budget
    :func:`select_auto_tools` takes, with the same semantics — ``None`` is
    byte-identical to v1.201.0, an int folds in as ``min(cap, max_tools)`` at
    the same truncation slice, and it only ever shrinks THIS function's
    playbook slots, never the user's explicit picks (those arrive via
    *exclude*). It is here because a "/"-invoked skill fills the same armed
    list the sentence pass fills, under the same `_MAX_ARMED_TOOLS` — an
    envelope that capped the sentence pass but not the playbook pass would be
    a band a skill invocation silently walks around.
    """
    # v1.202.0: envelope fold — see the block comment in `select_auto_tools`
    # for the full rationale (a weak model choosing `shell` over `read_file`
    # from a wide menu is the measured failure mode the band exists for).
    if max_tools is not None:
        cap = min(cap, max_tools)
    if cap <= 0 or not instructions:
        return []
    skip = set(exclude or ())
    text = instructions[:8000]
    hits: list[tuple[int, str]] = []
    for name in AUTO_SAFE_TOOLS:
        if name in skip:
            continue
        m = re.search(rf"\b{re.escape(name)}\b", text)
        if m:
            hits.append((m.start(), name))
    hits.sort()
    return [name for _, name in hits[:cap]]
