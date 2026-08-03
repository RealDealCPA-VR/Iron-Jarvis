"""Batch document pipeline (v1.133.0, Wave 3): folder → per-doc facts → synthesis.

"Process these 15 client documents" must NOT stuff raw documents into one
context window — the local models this app routes to routinely run 8k-context.
The pipeline keeps every LLM call bounded regardless of folder size:

1. :func:`sweep`       — list a folder's supported documents, each read gated by
   the shared fs policy; deterministic order; an over-``max_files`` folder is
   truncated with the remainder RECORDED (no silent caps).
2. :func:`extract_one` — ONE document alone: ``extract_text`` → clipped input →
   one-shot strict-JSON fact extraction, with exactly one repair round that
   feeds the validation error back.
3. Each extraction is persisted to ``<out_dir>/extractions/<slug>.json`` with
   the source path + mtime + size + content hash. Re-runs are RESUMABLE: an
   unchanged file (hash match) is loaded from disk and counted as "cached".
4. :func:`synthesize`  — deliverables are produced from the EXTRACTIONS ONLY
   (per-doc summaries + facts, total clipped) — never from raw document text —
   through the existing :func:`write_document` writers.

:func:`run_batch` orchestrates. Per-document failures are collected and
reported, never fatal, and — CLAUDE.md hard rule — a real-provider failure
surfaces as that document's error entry (or the synthesis error), never as
fabricated content: there is no mock fallback anywhere in this module.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.fs_policy import fs_read_ok
from .readers import _IMAGE_SUFFIXES, SUPPORTED_READ, extract_text
from .writers import write_document

#: Per-document input clip for the extraction call. Local models routinely run
#: 8k-token contexts; ~12k chars ≈ 3-4k tokens leaves room for the JSON
#: contract, the batch instructions and the reply inside one small window.
MAX_DOC_CHARS = 12_000

#: Total clip across ALL per-document digests fed to synthesis — the point of
#: the pipeline is that this stays bounded no matter how many docs were swept.
MAX_SYNTHESIS_CHARS = 16_000

#: Suffixes the sweep picks up: everything ``extract_text`` reads EXCEPT raster
#: images — an image extracts to a size note only ("[image PNG 800x600 ...]"),
#: which hands a fact-extraction model nothing but an invitation to fabricate.
#: (``_IMAGE_SUFFIXES`` is the reader's own definition of "image"; importing it
#: keeps the two modules from drifting apart.)
SWEEP_SUFFIXES: frozenset[str] = frozenset(SUPPORTED_READ - _IMAGE_SUFFIXES)

_EXTRACT_SYSTEM = (
    "You extract structured facts from ONE document for a batch pipeline. "
    "Reply with ONLY a JSON object — no prose, no markdown fences — shaped "
    "exactly:\n"
    '{"summary": "<3-6 sentence summary>", '
    '"facts": ["<one atomic fact per entry>"], '
    '"entities": {"people": [], "orgs": [], "dates": [], "amounts": []}, '
    '"figures": [{"label": "<what the number is>", "value": "<the number as written>"}]}\n'
    "Every fact must come from the document text. NEVER invent or guess names, "
    "identifiers, dates, or amounts — an identity fact that is not in the text "
    "is left out, not inferred. Empty lists are fine."
)

_SYNTH_DOCX_SYSTEM = (
    "You write ONE consolidated report from per-document EXTRACTIONS (summaries "
    "and facts) produced earlier — the raw documents are NOT available. Reply "
    "in GitHub-flavored markdown ('# ' headings, '-' bullets, | pipe | tables); "
    "it is rendered into a real Word document. Use ONLY the provided "
    "extractions; never invent facts, names or figures that are not in them."
)

_SYNTH_XLSX_SYSTEM = (
    "You build ONE consolidated workbook from per-document EXTRACTIONS "
    "(summaries and facts) produced earlier — the raw documents are NOT "
    "available. Reply with ONLY a JSON object — no prose, no fences — shaped "
    'exactly {"sheets": {"<sheet name>": [["<header>", ...], [<row cells>...]]}}. '
    "The first row of every sheet is its header row; cells are strings or "
    "numbers; a string starting with '=' becomes a real formula. Use ONLY the "
    "provided extractions; never invent facts or figures."
)


def slug_for(path: str | Path) -> str:
    """Filesystem-safe, COLLISION-PROOF name for *path*: the sanitized basename
    plus a short digest of the absolute path. The digest is stable across runs
    (resumability keys on it) and distinct for same-named sources elsewhere."""
    p = Path(path)
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", p.name).strip("-.") or "doc"
    digest = hashlib.sha256(
        str(p.resolve()).encode("utf-8", "replace")
    ).hexdigest()[:8]
    return f"{base}-{digest}"


def _sha256_file(path: Path) -> str:
    """Chunked content hash — the resume key (mtime alone is unreliable)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# --- sweep --------------------------------------------------------------------


def sweep(
    folder: str | Path, max_files: int
) -> tuple[list[Path], list[dict[str, str]]]:
    """List the supported documents directly in *folder* (top level only — a
    subfolder is a separate batch), each read gated by the SHARED fs policy
    (:func:`fs_read_ok` = allowlist + protected roots), in deterministic
    name order. Returns ``(files, skipped)`` where every excluded entry —
    unsupported type, policy denial, over the ``max_files`` limit — is RECORDED
    with its reason so nothing is ever silently dropped."""
    folder = Path(folder)
    max_files = max(1, int(max_files))
    files: list[Path] = []
    skipped: list[dict[str, str]] = []
    # casefold-then-exact key: stable, deterministic order on every platform.
    for p in sorted(folder.iterdir(), key=lambda e: (e.name.casefold(), e.name)):
        if p.is_dir():
            # A subfolder full of documents must not vanish silently — record
            # it like every other exclusion so the deliverable names it too.
            skipped.append(
                {
                    "file": str(p),
                    "reason": (
                        "subfolder — not descended (top level only); run it "
                        "as its own batch"
                    ),
                }
            )
            continue
        if p.suffix.lower() not in SWEEP_SUFFIXES:
            skipped.append(
                {
                    "file": str(p),
                    "reason": f"unsupported type {p.suffix or p.name!r}",
                }
            )
            continue
        ok, reason = fs_read_ok(str(p))
        if not ok:
            skipped.append({"file": str(p), "reason": f"read denied: {reason}"})
            continue
        files.append(p)
    if len(files) > max_files:
        for p in files[max_files:]:  # truncation is recorded, never silent
            skipped.append(
                {
                    "file": str(p),
                    "reason": (
                        f"over the max_files limit ({max_files}) — run the "
                        "batch again with a higher max_files or on the remainder"
                    ),
                }
            )
        files = files[:max_files]
    return files, skipped


# --- one-shot completion ------------------------------------------------------


async def _one_shot(router: Any, system: str, user: str, task_class: str) -> str:
    """One completion through the router — the same resolver-provided router the
    other document tools use. A provider failure RAISES to the caller (per-doc
    error entry / synthesis error); and the offline mock must never 'extract' a
    real document's facts — fabricated extraction is worse than none."""
    from ..providers.adapters.base import LLMMessage  # lazy: no import cycle

    route = await router.complete(
        system=system,
        messages=[LLMMessage(role="user", content=user)],
        tools=[],
        task_class=task_class,
    )
    if getattr(route, "provider", "") == "mock":
        raise RuntimeError(
            "only the offline mock model is connected — a fabricated "
            "extraction is worse than none; connect a real model and retry"
        )
    return (route.response.text or "").strip()


# --- extraction ---------------------------------------------------------------


def _parse_json_object(raw: str) -> dict[str, Any]:
    """The model's reply as a dict — tolerant of fences/prose around the object,
    strict about everything else. Raises :class:`ValueError` with a message
    specific enough to power the one repair round."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("reply contains no JSON object")
    try:
        obj = json.loads(s[start : end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"reply is not valid JSON: {exc}") from None
    if not isinstance(obj, dict):
        raise ValueError("reply must be a single JSON object")
    return obj


def _parse_extraction(raw: str) -> dict[str, Any]:
    """Validate + normalize the extraction contract. ``summary``/``facts`` are
    required; ``entities``/``figures`` are normalized when absent (a local model
    omitting an empty section shouldn't burn the repair round), but any PRESENT
    field of the wrong shape is an error — never silently coerced."""
    obj = _parse_json_object(raw)
    summary = obj.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        raise ValueError('"summary" must be a non-empty string')
    facts = obj.get("facts")
    if not isinstance(facts, list) or any(not isinstance(f, str) for f in facts):
        raise ValueError('"facts" must be a list of strings')
    entities_in = obj.get("entities") if obj.get("entities") is not None else {}
    if not isinstance(entities_in, dict):
        raise ValueError('"entities" must be an object')
    entities: dict[str, list[str]] = {}
    for key in ("people", "orgs", "dates", "amounts"):
        vals = entities_in.get(key) if entities_in.get(key) is not None else []
        if not isinstance(vals, list) or any(
            not isinstance(v, (str, int, float)) or isinstance(v, bool)
            for v in vals
        ):
            raise ValueError(f'"entities.{key}" must be a list of strings')
        entities[key] = [str(v) for v in vals]
    figures_in = obj.get("figures") if obj.get("figures") is not None else []
    if not isinstance(figures_in, list):
        raise ValueError('"figures" must be a list')
    figures: list[dict[str, str]] = []
    for i, fig in enumerate(figures_in):
        if not isinstance(fig, dict) or not isinstance(fig.get("label"), str):
            raise ValueError(
                f'"figures[{i}]" must be {{"label": str, "value": str}}'
            )
        figures.append(
            {"label": fig["label"], "value": str(fig.get("value", ""))}
        )
    return {
        "summary": summary.strip(),
        "facts": facts,
        "entities": entities,
        "figures": figures,
    }


def _repair_prompt(error: Exception, raw: str) -> str:
    return (
        f"Your previous reply was rejected: {error}\n\n"
        f"Previous reply:\n{raw[:2_000]}\n\n"
        "Reply again with ONLY the corrected JSON object in the exact contract "
        "— no prose, no fences."
    )


async def extract_one(
    path: str | Path, router: Any, instructions: str = ""
) -> dict[str, Any]:
    """Extract ONE document into the structured-facts contract.

    ``extract_text`` → clip to :data:`MAX_DOC_CHARS` (disclosed in the prompt)
    → one-shot strict-JSON completion, with exactly ONE repair round feeding
    the validation error back. A second contract violation, or any provider
    failure, raises — the caller records it as this document's error."""
    path = Path(path)
    text = await asyncio.to_thread(extract_text, path)
    if not (text or "").strip():
        # An empty prompt is a fabrication invitation — fail this doc honestly.
        raise ValueError("document extracted to no text — nothing to base facts on")
    clipped = text[:MAX_DOC_CHARS]
    trunc_note = (
        ""
        if len(text) <= MAX_DOC_CHARS
        else (
            f"\n[document truncated to the first {MAX_DOC_CHARS:,} of "
            f"{len(text):,} characters]"
        )
    )
    user = (
        (f"Batch instructions: {instructions.strip()}\n\n" if instructions.strip() else "")
        + f"Document: {path.name}\n---\n{clipped}{trunc_note}"
    )
    raw = await _one_shot(router, _EXTRACT_SYSTEM, user, "extract")
    try:
        return _parse_extraction(raw)
    except ValueError as exc:
        # ONE repair round: the specific validation error goes back to the
        # model; a second violation propagates as this document's failure.
        raw2 = await _one_shot(
            router, _EXTRACT_SYSTEM, _repair_prompt(exc, raw), "extract"
        )
        return _parse_extraction(raw2)


def _load_cached(record_path: Path, sha256: str) -> "dict[str, Any] | None":
    """A prior run's record whose content hash matches — resume with no LLM
    call. A missing/corrupt/hash-mismatched record simply re-extracts."""
    if not record_path.is_file():
        return None
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if (
        not isinstance(record, dict)
        or record.get("sha256") != sha256
        or not isinstance(record.get("extraction"), dict)
    ):
        return None
    return record


def _persist_record(record_path: Path, record: dict[str, Any]) -> None:
    """Sibling-temp + ``os.replace`` (the writers' atomic convention) so a
    mid-write crash never leaves a corrupt cache record behind."""
    tmp = record_path.with_name(f".{record_path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(tmp, record_path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


# --- synthesis ----------------------------------------------------------------


def _digest(records: list[dict[str, Any]]) -> str:
    """The bounded synthesis input: per-doc summary + facts (+ figures) — NEVER
    raw document text. Over :data:`MAX_SYNTHESIS_CHARS` total, each doc gets a
    fair per-doc budget so one enormous extraction can't starve the rest."""
    blocks: list[str] = []
    for rec in records:
        ex = rec["extraction"]
        name = rec.get("name") or Path(str(rec.get("source", "document"))).name
        lines = [f"## {name}", f"Summary: {ex.get('summary', '')}"]
        lines += [f"- {fact}" for fact in ex.get("facts") or []]
        lines += [
            f"- {fig.get('label')}: {fig.get('value')}"
            for fig in ex.get("figures") or []
        ]
        blocks.append("\n".join(lines))
    if sum(len(b) for b in blocks) > MAX_SYNTHESIS_CHARS:
        per = max(200, MAX_SYNTHESIS_CHARS // max(1, len(blocks)))
        blocks = [
            b if len(b) <= per else b[:per] + "\n[digest clipped]"
            for b in blocks
        ]
    return "\n\n".join(blocks)


def _excluded_lines(
    failed: list[dict[str, str]], skipped: list[dict[str, str]]
) -> list[tuple[str, str]]:
    """(file name, reason) for every document the deliverable does NOT cover."""
    out = [
        (Path(f["file"]).name, f"extraction failed: {f['error']}") for f in failed
    ]
    out += [(Path(s["file"]).name, s["reason"]) for s in skipped]
    return out


#: openpyxl's ILLEGAL_CHARACTERS_RE: control chars xlsx cannot store. Caught at
#: validation time so the repair round fires instead of a writer crash.
_XLSX_ILLEGAL_RX = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")

#: Excel's hard per-cell character limit — openpyxl silently TRUNCATES beyond
#: it, which would be silent data loss; reject at validation so repair fires.
_XLSX_CELL_MAX = 32_767


def _validate_sheets(obj: dict[str, Any]) -> dict[str, Any]:
    """Validate the synthesis workbook against ``write_document``'s EXISTING
    multi-sheet xlsx shape — ``{"sheets": {name: rows}}``, "=" strings stay
    formulas — which the writer already renders with typed coercion, headers
    and column sizing. (``excel_apply_spec``'s cell spec exists to REPRODUCE an
    existing sheet's formulas/formats and is the wrong contract for de-novo
    synthesis, so the writer shape is the schema reused here — not a new one.)"""
    sheets_in = obj.get("sheets")
    if not isinstance(sheets_in, dict) or not sheets_in:
        raise ValueError(
            'reply must be {"sheets": {"<name>": [[...rows...]]}} with at '
            "least one sheet"
        )
    sheets: dict[str, list[list[Any]]] = {}
    for name, rows in sheets_in.items():
        if not isinstance(rows, list) or any(
            not isinstance(r, list) for r in rows
        ):
            raise ValueError(f"sheet {name!r} must be a list of row lists")
        for r in rows:
            for c in r:
                if c is not None and not isinstance(c, (str, int, float, bool)):
                    raise ValueError(
                        f"sheet {name!r} has a non-scalar cell "
                        f"({type(c).__name__}) — cells are strings or numbers"
                    )
                # json.loads accepts the NaN/Infinity extension and openpyxl
                # writes non-finite floats as EMPTY cells — silent data loss.
                if isinstance(c, float) and not math.isfinite(c):
                    raise ValueError(
                        f"sheet {name!r} has a non-finite number "
                        "(NaN/Infinity) — write the value as a string instead"
                    )
                if isinstance(c, str):
                    if len(c) > _XLSX_CELL_MAX:
                        raise ValueError(
                            f"sheet {name!r} has a cell over Excel's "
                            f"{_XLSX_CELL_MAX:,}-character limit — shorten it"
                        )
                    if _XLSX_ILLEGAL_RX.search(c):
                        raise ValueError(
                            f"sheet {name!r} has a cell with control "
                            "characters xlsx cannot store — remove them"
                        )
        sheets[str(name)] = [list(r) for r in rows]
    return {"sheets": sheets}


async def synthesize(
    extractions: list[dict[str, Any]],
    *,
    router: Any,
    instructions: str,
    out_dir: str | Path,
    formats: tuple[str, ...],
    failed: "list[dict[str, str]] | None" = None,
    skipped: "list[dict[str, str]] | None" = None,
) -> tuple[list[Path], list[dict[str, str]]]:
    """Produce the deliverables from the EXTRACTIONS. docx: one-shot markdown →
    the existing markdown-aware writer. xlsx: one-shot sheets-JSON (validated
    against the writer's existing shape, one repair round) → the existing xlsx
    writer. Failed/skipped documents are appended to every deliverable BY CODE
    — honesty by construction, not by model compliance. Returns
    ``(deliverable_paths, errors)``; a failed format is an error entry, never a
    fabricated file."""
    out_dir = Path(out_dir)
    failed = failed or []
    skipped = skipped or []
    digest = _digest(extractions)
    user = (
        (f"Batch instructions: {instructions.strip()}\n\n" if instructions.strip() else "")
        + f"Per-document extractions ({len(extractions)} document(s)):\n\n{digest}"
    )
    excluded = _excluded_lines(failed, skipped)
    deliverables: list[Path] = []
    errors: list[dict[str, str]] = []
    for fmt in formats:
        target = out_dir / f"synthesis.{fmt}"
        try:
            if fmt == "docx":
                md = await _one_shot(router, _SYNTH_DOCX_SYSTEM, user, "synthesize")
                if not md.strip():
                    raise ValueError("model returned an empty synthesis")
                if excluded:
                    md += "\n\n## Documents not included\n\n" + "\n".join(
                        f"- {name} — {reason}" for name, reason in excluded
                    )
                await asyncio.to_thread(write_document, target, md)
            elif fmt == "xlsx":
                raw = await _one_shot(router, _SYNTH_XLSX_SYSTEM, user, "synthesize")
                try:
                    spec = _validate_sheets(_parse_json_object(raw))
                except ValueError as exc:
                    # Same single-repair convention as extraction: the shape
                    # error goes back once; a second violation is this
                    # format's honest failure.
                    raw2 = await _one_shot(
                        router,
                        _SYNTH_XLSX_SYSTEM,
                        _repair_prompt(exc, raw),
                        "synthesize",
                    )
                    spec = _validate_sheets(_parse_json_object(raw2))
                if excluded:
                    name, i = "Not included", 2
                    while name in spec["sheets"]:  # never clobber a model sheet
                        name = f"Not included ({i})"
                        i += 1
                    spec["sheets"][name] = [["File", "Reason"]] + [
                        [n, r] for n, r in excluded
                    ]
                await asyncio.to_thread(write_document, target, spec)
            else:  # defensive — run_batch validates the format list
                raise ValueError(f"unknown synthesis format {fmt!r}")
            deliverables.append(target)
        except Exception as exc:  # noqa: BLE001 — one format failing ≠ batch dead
            errors.append(
                {"output": fmt, "error": f"{type(exc).__name__}: {exc}"}
            )
    return deliverables, errors


# --- orchestration ------------------------------------------------------------

#: output param → the deliverable formats it means.
_OUTPUT_FORMATS: dict[str, tuple[str, ...]] = {
    "docx": ("docx",),
    "xlsx": ("xlsx",),
    "both": ("docx", "xlsx"),
}


async def run_batch(
    folder: str | Path,
    out_dir: str | Path,
    router: Any,
    *,
    instructions: str = "",
    output: str = "both",
    max_files: int = 25,
) -> dict[str, Any]:
    """The whole pipeline: sweep → per-doc extract (resumable, persisted) →
    synthesize from the extractions. Per-document failures are COLLECTED —
    one bad file, one provider hiccup, never aborts the rest."""
    folder = Path(folder)
    out_dir = Path(out_dir)
    formats = _OUTPUT_FORMATS.get(str(output).strip().lower())
    if formats is None:
        raise ValueError(f"unknown output {output!r} — use xlsx, docx, or both")
    files, skipped = sweep(folder, max_files)
    ext_dir = out_dir / "extractions"
    ext_dir.mkdir(parents=True, exist_ok=True)
    processed = 0
    cached = 0
    failed: list[dict[str, str]] = []
    extractions: list[dict[str, Any]] = []
    for path in files:
        try:
            sha = await asyncio.to_thread(_sha256_file, path)
            record_path = ext_dir / f"{slug_for(path)}.json"
            record = _load_cached(record_path, sha)
            if record is not None:
                cached += 1
                extractions.append(record)
                continue
            extraction = await extract_one(path, router, instructions)
            st = path.stat()
            record = {
                "source": str(path),
                "name": path.name,
                "mtime": st.st_mtime,
                "size": st.st_size,
                "sha256": sha,
                "extracted_at": datetime.now(timezone.utc).isoformat(),
                "extraction": extraction,
            }
            _persist_record(record_path, record)
            processed += 1
            extractions.append(record)
        except Exception as exc:  # noqa: BLE001 — per-doc failures never abort the batch
            failed.append(
                {"file": str(path), "error": f"{type(exc).__name__}: {exc}"}
            )
    deliverable_paths: list[Path] = []
    synthesis_errors: list[dict[str, str]] = []
    if extractions:
        deliverable_paths, synthesis_errors = await synthesize(
            extractions,
            router=router,
            instructions=instructions,
            out_dir=out_dir,
            formats=formats,
            failed=failed,
            skipped=skipped,
        )
    elif files:  # every doc failed — say so, don't synthesize from nothing
        synthesis_errors.append(
            {
                "output": ",".join(formats),
                "error": "no successful extractions — nothing to synthesize",
            }
        )
    return {
        "processed": processed,
        "cached": cached,
        "failed": failed,
        "skipped": skipped,
        "deliverables": [str(p) for p in deliverable_paths],
        "synthesis_errors": synthesis_errors,
        "extraction_dir": str(ext_dir),
    }
