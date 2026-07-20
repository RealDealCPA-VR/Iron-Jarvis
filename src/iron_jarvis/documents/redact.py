"""PII detection + format-preserving redaction (the ``redact_pii`` tool).

Detection is DETERMINISTIC — regex patterns for structured identifiers (SSN,
ITIN, EIN, email, phone, credit card with a Luhn check, context-gated bank
numbers and dates of birth, street addresses, IPs) plus caller-supplied
``extra_terms`` for the unstructured PII only a reader can spot (person names,
employers). No LLM in the loop here: what gets redacted is exactly what the
rules + terms say, auditable from the tool call itself.

Redaction PRESERVES the document: docx/xlsx/pptx are rewritten in place
(styles, tables, headers/footers intact — only matched characters change),
plain-text formats are string-rewritten, and PDFs are REBUILT from extracted
text (pypdf cannot edit page content; a cosmetic black box over live text
would be a fake redaction, so the honest fallback is a clean rebuild whose
PII is truly gone — the tool result says so). The source file is NEVER
touched; output always lands in a new file.

Styles: ``black`` = same-length █ blocks (layout preserved), ``label`` =
``[SSN]``-style category tags, ``remove`` = deleted outright.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Callable

# --------------------------------------------------------------- detection ---

#: Category -> compiled pattern. Group 1, when present, is the PII portion
#: (context words stay); otherwise the whole match is the PII.
_PATTERNS: dict[str, re.Pattern[str]] = {
    # 123-45-6789 (also space-separated). Area 9xx is an ITIN, matched below.
    "ssn": re.compile(r"\b(?!000|666|9\d\d)\d{3}[- ](?!00)\d{2}[- ](?!0000)\d{4}\b"),
    "itin": re.compile(r"\b9\d{2}[- ]\d{2}[- ]\d{4}\b"),
    # Labeled contiguous 9-digit SSN/ITIN ("SSN: 123456789").
    "ssn_labeled": re.compile(
        r"\b(?:ssn|itin|social security(?:\s+(?:number|no\.?))?)\s*[:#]?\s*"
        r"(\d{3}[- ]?\d{2}[- ]?\d{4})",
        re.IGNORECASE,
    ),
    "ein": re.compile(r"\b\d{2}-\d{7}\b"),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b"),
    "phone": re.compile(r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]?\d{4}\b"),
    # 13-19 digits (spaces/dashes allowed) — confirmed by Luhn below.
    "credit_card": re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
    "bank_account": re.compile(
        r"\b(?:account|acct|routing|aba)\s*(?:number|no\.?|#)?\s*[:#]?\s*(\d{6,17})\b",
        re.IGNORECASE,
    ),
    "dob": re.compile(
        r"\b(?:dob|date of birth|born(?:\s+on)?|birth\s*date)\s*[:#]?\s*"
        r"((?:\d{1,2}[/-]\d{1,2}[/-]\d{2,4})|(?:[A-Z][a-z]+\.?\s+\d{1,2},?\s+\d{4}))",
        re.IGNORECASE,
    ),
    "address": re.compile(
        r"\b\d{1,6}\s+(?:[A-Z][\w'.-]*\s){0,4}?"
        r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|"
        r"Court|Ct|Circle|Cir|Way|Place|Pl|Terrace|Ter|Highway|Hwy|Parkway|"
        r"Pkwy|Trail|Trl|Loop)\.?\b"
        r"(?:\s*,?\s*(?:#|Apt\.?|Suite|Ste\.?|Unit)\s*\w+)?",
    ),
    "ip": re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
}

#: Short display tags for the ``label`` style.
_LABELS: dict[str, str] = {
    "ssn": "SSN",
    "itin": "ITIN",
    "ssn_labeled": "SSN",
    "ein": "EIN",
    "email": "EMAIL",
    "phone": "PHONE",
    "credit_card": "CARD",
    "bank_account": "ACCOUNT",
    "dob": "DOB",
    "address": "ADDRESS",
    "ip": "IP",
    "custom": "REDACTED",
}

ALL_CATEGORIES: frozenset[str] = frozenset(_LABELS)

#: Redaction styles the tool accepts.
STYLES = ("black", "label", "remove")


def _luhn_ok(digits: str) -> bool:
    ds = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(ds) <= 19:
        return False
    total = 0
    for i, d in enumerate(reversed(ds)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def find_pii_spans(
    text: str,
    *,
    extra_terms: list[str] | None = None,
    categories: set[str] | frozenset[str] | None = None,
) -> list[tuple[int, int, str]]:
    """Return non-overlapping ``(start, end, category)`` spans, sorted by start.
    ``extra_terms`` are matched literally (case-insensitive) as ``custom``.
    Earlier-starting/longer spans win overlaps."""
    wanted = set(categories) if categories else set(_PATTERNS) | {"custom"}
    raw: list[tuple[int, int, str]] = []
    for cat, rx in _PATTERNS.items():
        if cat not in wanted:
            continue
        for m in rx.finditer(text):
            start, end = (m.span(1) if m.groups() and m.group(1) else m.span())
            value = text[start:end]
            if cat == "credit_card" and not _luhn_ok(value):
                continue
            if cat == "ip" and any(int(p) > 255 for p in re.findall(r"\d+", value)):
                continue
            raw.append((start, end, cat))
    if "custom" in wanted:
        for term in extra_terms or []:
            t = (term or "").strip()
            if len(t) < 2:
                continue  # a 1-char term would shred the document
            for m in re.finditer(re.escape(t), text, re.IGNORECASE):
                raw.append((m.start(), m.end(), "custom"))
    # Resolve overlaps: sort by (start, -length); keep spans that don't overlap
    # an already-kept one.
    raw.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    kept: list[tuple[int, int, str]] = []
    last_end = -1
    for start, end, cat in raw:
        if start >= last_end:
            kept.append((start, end, cat))
            last_end = end
    return kept


def _replacement(value: str, category: str, style: str) -> str:
    if style == "remove":
        return ""
    if style == "label":
        return f"[{_LABELS.get(category, 'REDACTED')}]"
    return "█" * len(value)  # "black" — same length keeps layout intact


#: A resolved span ready to apply: (start, end, category, replacement).
_Span = tuple[int, int, str, str]


def _make_spans_fn(
    style: str,
    extra_terms: list[str] | None,
    categories: set[str] | frozenset[str] | None,
) -> Callable[[str], list[_Span]]:
    def spans_for(text: str) -> list[_Span]:
        found = find_pii_spans(text, extra_terms=extra_terms, categories=categories)
        return [(s, e, cat, _replacement(text[s:e], cat, style)) for s, e, cat in found]

    return spans_for


def _apply_spans(text: str, spans: list[_Span]) -> tuple[str, dict[str, int]]:
    counts: dict[str, int] = {}
    out: list[str] = []
    cursor = 0
    for start, end, cat, repl in spans:
        out.append(text[cursor:start])
        out.append(repl)
        counts[cat] = counts.get(cat, 0) + 1
        cursor = end
    out.append(text[cursor:])
    return "".join(out), counts


def mask_text(
    text: str,
    *,
    style: str = "black",
    extra_terms: list[str] | None = None,
    categories: set[str] | frozenset[str] | None = None,
) -> tuple[str, dict[str, int]]:
    """Redact *text*; returns ``(redacted, counts_by_category)``."""
    spans = _make_spans_fn(style, extra_terms, categories)(text)
    return _apply_spans(text, spans)


# -------------------------------------------------------- format redactors ---


def _merge_counts(total: dict[str, int], part: dict[str, int]) -> None:
    for k, v in part.items():
        total[k] = total.get(k, 0) + v


def _redact_runs(
    runs: list[Any], spans_for: Callable[[str], list[_Span]]
) -> dict[str, int]:
    """Redact PII across a paragraph's runs, PRESERVING run formatting.

    Matches are found on the CONCATENATED text (PII often spans runs — e.g.
    a bold SSN split by the editor), then each run's slice is rewritten. A
    replacement whose length differs (label/remove styles) lands wholly in the
    run where the match STARTS; later runs' matched characters are dropped.
    """
    texts = [r.text or "" for r in runs]
    combined = "".join(texts)
    if not combined:
        return {}
    spans = spans_for(combined)
    if not spans:
        return {}
    counts: dict[str, int] = {}
    for _s, _e, cat, _r in spans:
        counts[cat] = counts.get(cat, 0) + 1
    offsets: list[int] = []
    pos = 0
    for t in texts:
        offsets.append(pos)
        pos += len(t)
    span_iter = iter(spans)
    span = next(span_iter, None)
    for i, t in enumerate(texts):
        rs, re_ = offsets[i], offsets[i] + len(t)
        cursor = rs
        parts: list[str] = []
        while cursor < re_:
            if span is None or span[0] >= re_:
                parts.append(combined[cursor:re_])
                break
            s, e, _cat, repl = span
            if s > cursor:
                parts.append(combined[cursor:s])
                cursor = s
                continue
            # Inside the span: the replacement is emitted only by the run where
            # the span STARTS; runs it merely continues into contribute nothing.
            if s >= rs:
                parts.append(repl)
            cursor = min(e, re_)
            if e <= re_:
                span = next(span_iter, None)
        new_text = "".join(parts)
        if (t or "") != new_text:
            runs[i].text = new_text
    return counts


def _redact_docx(src: Path, dst: Path, spans_for) -> dict[str, int]:
    import docx  # python-docx

    doc = docx.Document(str(src))
    counts: dict[str, int] = {}

    def do_paragraphs(paragraphs) -> None:
        for par in paragraphs:
            _merge_counts(counts, _redact_runs(list(par.runs), spans_for))

    def do_tables(tables) -> None:
        for table in tables:
            for row in table.rows:
                for cell in row.cells:
                    do_paragraphs(cell.paragraphs)
                    do_tables(cell.tables)  # nested tables

    do_paragraphs(doc.paragraphs)
    do_tables(doc.tables)
    for section in doc.sections:
        for part in (section.header, section.footer):
            do_paragraphs(part.paragraphs)
            do_tables(part.tables)
    doc.save(str(dst))
    return counts


def _redact_xlsx(src: Path, dst: Path, spans_for) -> dict[str, int]:
    from openpyxl import load_workbook

    wb = load_workbook(str(src))  # formulas preserved (not data_only)
    counts: dict[str, int] = {}
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                v = cell.value
                # Only string cells: numbers/formulas/dates stay untouched (a
                # formula rewrite could corrupt the sheet; noted in the tool).
                if isinstance(v, str) and not v.startswith("="):
                    masked, part = _apply_spans(v, spans_for(v))
                    if part:
                        cell.value = masked
                        _merge_counts(counts, part)
    wb.save(str(dst))
    return counts


def _redact_pptx(src: Path, dst: Path, spans_for) -> dict[str, int]:
    from pptx import Presentation

    prs = Presentation(str(src))
    counts: dict[str, int] = {}

    def do_text_frame(tf) -> None:
        for par in tf.paragraphs:
            _merge_counts(counts, _redact_runs(list(par.runs), spans_for))

    for slide in prs.slides:
        for shape in slide.shapes:
            if getattr(shape, "has_text_frame", False):
                do_text_frame(shape.text_frame)
            if getattr(shape, "has_table", False):
                for row in shape.table.rows:
                    for cell in row.cells:
                        do_text_frame(cell.text_frame)
    prs.save(str(dst))
    return counts


def _redact_pdf(src: Path, dst: Path, spans_for) -> tuple[dict[str, int], str]:
    """pypdf cannot edit page content, and painting a black box OVER live text
    is a fake redaction (the text stays extractable). Honest fallback: extract
    the text, redact it, and REBUILD a clean PDF — the PII is truly gone, the
    layout is approximate, and the note says exactly that."""
    from .readers import extract_text
    from .writers import write_document

    text = extract_text(src)
    masked, counts = _apply_spans(text, spans_for(text))
    write_document(dst, masked, kind="pdf")
    note = (
        "PDF rebuilt from extracted text (in-place PDF editing isn't available; "
        "a cosmetic black box would leave the PII extractable). Content is truly "
        "removed; layout is approximate."
    )
    return counts, note


_TEXT_SUFFIXES = {
    ".txt", ".md", ".csv", ".tsv", ".html", ".htm", ".json", ".log", ".xml",
    ".yaml", ".yml", ".rtf",
}


def redact_file(
    src: Path,
    dst: Path,
    *,
    style: str = "black",
    extra_terms: list[str] | None = None,
    categories: set[str] | frozenset[str] | None = None,
) -> tuple[dict[str, int], str]:
    """Redact *src* into *dst* (same format). Returns ``(counts, note)``.
    The source file is never modified."""
    if style not in STYLES:
        raise ValueError(f"unknown style: {style!r} (use black, label, or remove)")
    spans_for = _make_spans_fn(style, extra_terms, categories)
    suffix = src.suffix.lower()
    note = ""
    if suffix == ".docx":
        counts = _redact_docx(src, dst, spans_for)
    elif suffix == ".xlsx":
        counts = _redact_xlsx(src, dst, spans_for)
        note = "string cells redacted; numeric cells and formulas are untouched"
    elif suffix == ".pptx":
        counts = _redact_pptx(src, dst, spans_for)
    elif suffix == ".pdf":
        counts, note = _redact_pdf(src, dst, spans_for)
    elif suffix in _TEXT_SUFFIXES or suffix == "":
        text = src.read_text(encoding="utf-8", errors="replace")
        masked, counts = _apply_spans(text, spans_for(text))
        dst.write_text(masked, encoding="utf-8")
    else:
        raise ValueError(
            f"unsupported format for redaction: {suffix or '(no extension)'} — "
            "supported: .docx .xlsx .pptx .pdf and text formats "
            "(.txt .md .csv .tsv .html .json …)"
        )
    return counts, note
