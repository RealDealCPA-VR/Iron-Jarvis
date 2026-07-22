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
web retrieval, and local image tools. NEVER shell, edit_file, computeruse,
MCP (``mcp__*``), or paid generative media (``pixio_*``): those stay behind
the explicit "+" arming, which is the interactive consent the permission
engine's session grant is built on.
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
        # Writes only a NEW .redacted copy (never modifies the source).
        "redact_pii",
        # Structured spreadsheet work (read anywhere; edits workspace-confined
        # + undoable).
        "excel_read",
        "excel_edit",
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
            r"web|latest|news|headline|up.to.date|weather|price of|stock|"
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
    # --- creating deliverables -------------------------------------------
    (
        re.compile(
            r"\b(write|create|draft|make|generate|prepare|produce|save|"
            r"export|put together)\b.{0,60}\b(file|document|report|memo|"
            r"letter|docx|pdf|spreadsheet|xlsx|csv|deck|presentation|pptx|"
            r"proposal|invoice|one.pager|summary doc)\b",
            re.IGNORECASE,
        ),
        {"write_document": 8, "write_file": 3},
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
    # --- spreadsheets -----------------------------------------------------
    (
        re.compile(
            r"\b(excel|xlsx|spreadsheet|workbook|worksheet|\bsheet\b|"
            r"\bcells?\b|formulas?|pivot)\b",
            re.IGNORECASE,
        ),
        {"excel_read": 8, "excel_edit": 6, "file_search": 3},
    ),
    # --- PII redaction ----------------------------------------------------
    (
        re.compile(
            r"\b(redact|pii|anonymi[sz]e|de.?identif(?:y|ied)|mask|scrub|"
            r"saniti[sz]e)\b",
            re.IGNORECASE,
        ),
        {"redact_pii": 9, "read_document": 5, "file_search": 4},
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
