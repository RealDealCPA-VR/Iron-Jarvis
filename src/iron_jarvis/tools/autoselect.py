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
file + document tools (fs-policy-confined to the chat workspace), read-only
web retrieval, local image tools, memory recall/notes, and past-conversation
search (read-only searches plus append-only note writes). NEVER shell,
edit_file, computeruse, MCP
(``mcp__*``), or paid generative media (``pixio_*``): those stay behind the
explicit "+" arming, which is the interactive consent the permission engine's
session grant is built on.
"""

from __future__ import annotations

import re

#: Every tool this module may ever arm. Curated — see the module docstring.
AUTO_SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "file_search",
        "read_file",
        "list_files",
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
    }
)

_DOC_EXT_RX = re.compile(
    r"\.(pdf|docx?|xlsx?|pptx?|csv|tsv|txt|md|rtf|json|log)$", re.IGNORECASE
)
_IMG_EXT_RX = re.compile(r"\.(png|jpe?g|gif|webp|bmp|tiff?)$", re.IGNORECASE)

_URL_RX = re.compile(r"https?://\S+", re.IGNORECASE)
# Windows (C:\...) or POSIX-looking absolute paths typed into the message.
_PATH_RX = re.compile(r"(?:[A-Za-z]:\\[^\s\"']+|(?<!\S)/(?:[\w.-]+/)+[\w.-]+)")

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
        re.compile(
            r"\b(?:my|the|this|that|our)\s+(?:files?|folders?|directory|"
            r"documents?|downloads|desktop)\b|\bin\s+(?:the|my|this)\s+folder\b",
            re.IGNORECASE,
        ),
        {"file_search": 8, "read_document": 5, "list_files": 5},
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
    (
        re.compile(
            r"\b(write|create|draft|make|generate|prepare|produce|save|"
            r"export|put together)\b.{0,60}\b(file|document|report|memo|"
            r"letter|docx|pdf|spreadsheet|xlsx|csv|deck|presentation|pptx|"
            r"proposal|invoice|one.pager|summary doc|excel|workbook|"
            r"worksheet|sheet|table|word doc)\b",
            re.IGNORECASE,
        ),
        {"write_document": 10, "write_file": 3},
    ),
    (
        re.compile(
            r"\b(write|create|make|generate|save)\b.{0,40}"
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
    (
        re.compile(
            r"\bconvert\b.{0,40}\b(pdf|docx?|xlsx|pptx|csv|markdown|html)\b",
            re.IGNORECASE,
        ),
        {"convert_document": 7},
    ),
    # Page-level PDF work — a page verb and a page-y noun (pdf/page/scan) in
    # either order: "merge these pdfs", "the pdf needs splitting", "split the
    # scan into separate pages", "rotate the last page". Verb-only sentences
    # ("merge these cells", "split the string") deliberately do NOT fire: one
    # of the nouns must appear.
    (
        re.compile(
            r"\b(merge|split|rotate|reorder|rearrange|combine|reverse)\b"
            r".{0,40}\b(?:pdfs?|pages?|scans?)\b|"
            r"\b(?:pdfs?|pages?|scans?)\b.{0,40}\b(merge|split|rotate|reorder|"
            r"rearrange|combine|reverse)\w*\b",
            re.IGNORECASE,
        ),
        {"pdf_arrange": 8, "pdf_split": 6},
    ),
    # --- spreadsheets -----------------------------------------------------
    (
        re.compile(
            r"\b(excel|xlsx|spreadsheet|workbook|worksheet|\bsheet\b|"
            r"\bcells?\b|formulas?|pivot)\b",
            re.IGNORECASE,
        ),
        {"excel_query": 9, "excel_profile": 8, "excel_read": 7, "excel_edit": 6,
         "file_search": 3},
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
    (
        re.compile(
            r"\b(redact|pii|anonymi[sz]e|de.?identif(?:y|ied)|mask|scrub|"
            r"saniti[sz]e)\b",
            re.IGNORECASE,
        ),
        {"redact_scan": 10, "redact_pii": 9, "read_document": 5, "file_search": 4},
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
    # --- images -----------------------------------------------------------
    (
        re.compile(
            r"\b(resize|convert|compress|shrink|scale)\b.{0,40}"
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


def select_auto_tools(
    text: str,
    *,
    attachments: list[str] | None = None,
    exclude: set[str] | frozenset[str] | None = None,
    cap: int = 6,
) -> list[str]:
    """Score *text* (the last user message) + attachment file names and return
    up to *cap* auto-armable tool names, best signal first. Tools in *exclude*
    (the user's explicit picks) are never repeated. Returns ``[]`` for plain
    conversation — no signal, no tools, no latency."""
    if cap <= 0:
        return []
    skip = set(exclude or ())
    scores: dict[str, int] = {}

    def bump(weights: dict[str, int]) -> None:
        for name, w in weights.items():
            scores[name] = scores.get(name, 0) + w

    msg = (text or "")[:4000]
    for rx, weights in _RULES:
        if rx.search(msg):
            bump(weights)
    if _URL_RX.search(msg):
        bump({"web_fetch": 9, "web_search": 3})
    if _PATH_RX.search(msg):
        bump({"read_file": 6, "file_search": 4, "list_files": 3})
    for name in attachments or []:
        if _DOC_EXT_RX.search(name):
            bump({"read_document": 9})
        elif _IMG_EXT_RX.search(name):
            bump({"view_image": 9})

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
    """
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
