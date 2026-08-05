"""Page-level PDF manipulation (the engine behind pdf_arrange / pdf_split).

Clean-room reimplementation of the classic page-arranger OPERATIONS (merge,
split, rotate, crop, reorder, delete, duplicate, reverse, blank-page insert,
password open/save, metadata) on **pypdf** only. No text is ever extracted and
nothing is ever created from markdown — pages in, pages out, complementary to
:mod:`.readers` / :mod:`.writers` by construction.

Page-spec grammar (``parse_page_spec``), tokens comma-separated::

    N          a single page               (1-based)
    N-M        an inclusive range; M < N runs BACKWARDS (``9-5`` = reversed)
    N-end      from N to the last page
    end        the last page
    all        every page in order
    blank      insert a blank page sized like the PREVIOUS selected page
               (US-Letter 612x792 pt when it is the first selection)

Any token may carry a rotation suffix ``@90`` / ``@180`` / ``@270`` which is
applied to every page the token selects, ADDITIVE to the page's existing
``/Rotate`` and normalized mod 360.  ``parse_page_spec`` returns ordered
``[(page_index, rotation_delta)]`` where ``page_index`` is **0-based** into
``reader.pages`` and :data:`BLANK_PAGE` (``-1``) marks a blank insert.

House guarantees (same rules as the rest of :mod:`iron_jarvis.documents`):

* Inputs are opened READ-ONLY — no write handle is ever taken on an input,
  and writing output onto an input path is refused outright.
* Outputs are written atomically (sibling temp + ``os.replace``); a mid-write
  failure never leaves a partial or 0-byte file, and a failed multi-part split
  removes the parts it had already written.
* ENGINE-COMPUTED honesty: every reported page count comes from RE-OPENING
  the written file with pypdf, never from what we intended to write.
* Jobs above :data:`MAX_TOTAL_PAGES` (2000) pages are refused honestly.
* Encrypted inputs decrypt via ``reader.decrypt(password)``.  AES-encrypted
  files additionally need the ``cryptography`` package (a base dependency, but
  checked at import so a broken environment still gets an honest error naming
  the limitation instead of a stack trace).  Output encryption uses AES-256
  when ``cryptography`` is available, pypdf's native RC4-128 otherwise.
"""

from __future__ import annotations

import os
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from pypdf.errors import DependencyError, PdfReadError
from pypdf.generic import NameObject, NumberObject, RectangleObject

try:  # AES needs the cryptography package; RC4 works without it.
    import cryptography  # noqa: F401

    _HAS_CRYPTOGRAPHY = True
except ImportError:  # pragma: no cover - cryptography is a base dependency
    _HAS_CRYPTOGRAPHY = False

__all__ = [
    "BLANK_PAGE",
    "MAX_TOTAL_PAGES",
    "US_LETTER",
    "ArrangeInput",
    "ArrangeReport",
    "SplitReport",
    "arrange",
    "parse_page_spec",
    "pdf_info",
    "split",
]

#: Sentinel page index produced by the ``blank`` token.
BLANK_PAGE = -1

#: US-Letter in PDF points — the size of a leading blank page.
US_LETTER = (612.0, 792.0)

#: Honest refusal threshold: total pages a single job may produce or split.
MAX_TOTAL_PAGES = 2000

_ROTATIONS = {"90", "180", "270"}
_CROP_KEYS = ("top", "right", "bottom", "left")
_META_KEYS = {"title": "/Title", "author": "/Author", "subject": "/Subject"}


@dataclass
class ArrangeInput:
    """One source PDF for :func:`arrange`."""

    path: str | Path
    pages_spec: str = "all"
    password: str | None = None


@dataclass
class ArrangeReport:
    """What :func:`arrange` actually did — every count verified by re-opening.

    ``inputs`` entries are ``{"path", "pages", "used"}`` where ``pages`` is the
    input file's REAL page count and ``used`` is how many of its pages were
    placed into the output (blanks belong to no input).
    """

    path: str
    pages: int
    inputs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"path": self.path, "pages": self.pages, "inputs": self.inputs}


@dataclass
class SplitReport:
    """What :func:`split` wrote — ``outputs`` entries are ``{"path", "pages"}``
    with each page count verified by re-opening the written part."""

    outputs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"outputs": self.outputs}


# --- page-spec parsing ---------------------------------------------------------


def parse_page_spec(spec: str, page_count: int) -> list[tuple[int, int]]:
    """Parse a page spec (module docstring grammar) against ``page_count``.

    Returns ordered ``[(page_index, rotation_delta)]`` — 0-based indexes,
    :data:`BLANK_PAGE` for blanks, rotation in {0, 90, 180, 270}.  Raises
    :class:`ValueError` with a specific, honest message for anything invalid
    (out-of-range pages name the file's real page count).
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError(
            "empty page spec — use tokens like '1,3-5,end@90' or 'all'"
        )
    out: list[tuple[int, int]] = []
    for raw in spec.split(","):
        token = raw.strip()
        if not token:
            raise ValueError(f"empty token in page spec {spec!r}")
        rotation = 0
        if "@" in token:
            token, _, rot_s = token.partition("@")
            token, rot_s = token.strip(), rot_s.strip()
            if rot_s not in _ROTATIONS:
                raise ValueError(
                    f"invalid rotation {rot_s!r} in token {raw.strip()!r} — "
                    "use @90, @180 or @270"
                )
            rotation = int(rot_s)
        low = token.lower()
        if low == "all":
            out.extend((i, rotation) for i in range(page_count))
        elif low == "blank":
            out.append((BLANK_PAGE, rotation))
        elif low == "end":
            if page_count < 1:
                raise ValueError("cannot use 'end' — the file has 0 pages")
            out.append((page_count - 1, rotation))
        elif "-" in token:
            start_s, _, end_s = token.partition("-")
            start = _page_number(start_s, page_count, raw)
            end_s = end_s.strip()
            if end_s.lower() == "end":
                end = page_count
                _check_page(end, page_count, raw)
            else:
                end = _page_number(end_s, page_count, raw)
            step = 1 if end >= start else -1
            out.extend((n - 1, rotation) for n in range(start, end + step, step))
        else:
            out.append((_page_number(token, page_count, raw) - 1, rotation))
    return out


def _page_number(s: str, page_count: int, token: str) -> int:
    s = s.strip()
    if not s.isdigit():
        raise ValueError(
            f"invalid page token {token.strip()!r} — expected N, N-M, N-end, "
            "end, all, or blank (with optional @90/@180/@270)"
        )
    return _check_page(int(s), page_count, token)


def _check_page(n: int, page_count: int, token: str) -> int:
    if n < 1:
        raise ValueError(f"page numbers are 1-based — got {n} in {token.strip()!r}")
    if n > page_count:
        plural = "s" if page_count != 1 else ""
        raise ValueError(
            f"page {n} is out of range — the file has {page_count} page{plural}"
        )
    return n


# --- shared plumbing -----------------------------------------------------------


@contextmanager
def _atomic(p: Path):
    """Sibling temp + ``os.replace`` on success; delete the temp on failure.

    Same-directory temp keeps the final rename an atomic same-filesystem move
    on every platform (incl. Windows) — house rule from the thumb-serve
    incident: never let a reader observe a half-written file.
    """
    tmp = p.with_name(f".{p.name}.tmp-{os.getpid()}")
    try:
        yield tmp
        os.replace(tmp, p)
    except BaseException:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
        raise


def _require_pdf_path(path: str | Path) -> Path:
    p = Path(path)
    if p.is_dir():
        raise ValueError(f"path is a directory, not a PDF: {p}")
    if not p.is_file():
        raise ValueError(f"file not found: {p}")
    return p


def _open_reader(fh: Any, p: Path, password: str | None) -> PdfReader:
    """Open ``fh`` as a PdfReader, decrypting if needed — honest errors only."""
    try:
        reader = PdfReader(fh)
    except PdfReadError as exc:
        raise ValueError(f"not a valid PDF (failed to parse): {p} ({exc})") from exc
    if reader.is_encrypted:
        # Always ATTEMPT the decrypt first: files locked with only an owner
        # password (blank user password) open transparently in every viewer,
        # so they must open transparently here too (None and "" both try "").
        try:
            decrypted = reader.decrypt(password or "")
        except DependencyError as exc:
            raise ValueError(
                f"{p.name} uses AES encryption and the 'cryptography' package "
                "is not installed — cannot decrypt it"
            ) from exc
        if not decrypted:
            if password:
                raise ValueError(f"wrong password for {p.name}")
            raise ValueError(
                f"{p.name} is password-protected — provide the password to open it"
            )
    try:
        len(reader.pages)  # force the page tree so corruption surfaces HERE
    except PdfReadError as exc:
        raise ValueError(f"not a valid PDF (broken page tree): {p} ({exc})") from exc
    return reader


def _get_rotation(page: Any) -> int:
    val = page.get("/Rotate", 0)
    if hasattr(val, "get_object"):
        val = val.get_object()
    return int(val) % 360


def _set_rotation(page: Any, degrees: int) -> None:
    page[NameObject("/Rotate")] = NumberObject(degrees % 360)


def _append_selection(
    writer: PdfWriter,
    reader: PdfReader,
    selection: list[tuple[int, int]],
    last_size: "tuple[float, float] | None",
) -> tuple[int, "tuple[float, float] | None"]:
    """Append ``selection`` pages to ``writer``; returns (used, last_size).

    ``used`` counts real pages taken from ``reader`` (blanks excluded);
    ``last_size`` tracks the mediabox of the most recent selected page so the
    NEXT blank matches it (US-Letter when nothing was selected yet).
    """
    used = 0
    for idx, rot in selection:
        if idx == BLANK_PAGE:
            width, height = last_size or US_LETTER
            page = writer.add_blank_page(width=width, height=height)
        else:
            src = reader.pages[idx]
            page = writer.add_page(src)
            used += 1
            width = float(src.mediabox.width)
            height = float(src.mediabox.height)
        last_size = (width, height)
        if rot:
            _set_rotation(page, _get_rotation(page) + rot)
    return used, last_size


def _validate_crop(crop: "dict[str, Any] | None") -> "dict[str, float] | None":
    if crop is None:
        return None
    if not isinstance(crop, dict):
        raise ValueError("crop must be a dict of top/right/bottom/left percents")
    unknown = set(crop) - set(_CROP_KEYS)
    if unknown:
        raise ValueError(
            f"unknown crop key(s) {sorted(unknown)} — supported: top, right, "
            "bottom, left (percent margins)"
        )
    margins: dict[str, float] = {}
    for key in _CROP_KEYS:
        raw = crop.get(key, 0)
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"crop {key} must be a number — got {raw!r}") from None
        if not 0 <= value < 100:
            raise ValueError(
                f"crop {key} must be a percentage in [0, 100) — got {raw!r}"
            )
        margins[key] = value
    if margins["left"] + margins["right"] >= 100:
        raise ValueError("left + right crop must total under 100%")
    if margins["top"] + margins["bottom"] >= 100:
        raise ValueError("top + bottom crop must total under 100%")
    if not any(margins.values()):
        return None
    return margins


def _display_margins_to_raw(
    margins: dict[str, float], rotation: int
) -> dict[str, float]:
    """Map DISPLAY-relative margins onto raw mediabox sides for ``rotation``.

    Crop follows the page as the user SEES it: with ``/Rotate 90`` the
    displayed top edge is the raw LEFT edge, and so on around.  Percentages
    transfer directly because each display edge's percent is taken of the same
    physical dimension as the raw edge it maps to.  A non-multiple-of-90
    ``/Rotate`` (malformed file) falls back to cropping the raw box as-is.
    """
    if rotation == 90:
        return {
            "left": margins["top"],
            "top": margins["right"],
            "right": margins["bottom"],
            "bottom": margins["left"],
        }
    if rotation == 180:
        return {
            "left": margins["right"],
            "top": margins["bottom"],
            "right": margins["left"],
            "bottom": margins["top"],
        }
    if rotation == 270:
        return {
            "left": margins["bottom"],
            "top": margins["left"],
            "right": margins["top"],
            "bottom": margins["right"],
        }
    return margins


def _apply_crop(page: Any, margins: dict[str, float]) -> None:
    """Shrink the page's mediabox AND cropbox by percent margins.

    ``margins`` are display-relative: they are mapped through the page's
    effective ``/Rotate`` so "top" is the top the user sees, and the math uses
    the box's REAL corners so non-origin mediaboxes (llx/lly != 0) crop
    correctly.
    """
    margins = _display_margins_to_raw(margins, _get_rotation(page))
    box = page.mediabox
    left, bottom = float(box.left), float(box.bottom)
    right, top = float(box.right), float(box.top)
    width, height = right - left, top - bottom
    rect = (
        left + width * margins["left"] / 100.0,
        bottom + height * margins["bottom"] / 100.0,
        right - width * margins["right"] / 100.0,
        top - height * margins["top"] / 100.0,
    )
    page.mediabox = RectangleObject(rect)
    page.cropbox = RectangleObject(rect)


def _validate_metadata(
    metadata: "dict[str, Any] | None",
) -> "dict[str, str] | None":
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise ValueError("metadata must be a dict of title/author/subject")
    unknown = set(metadata) - set(_META_KEYS)
    if unknown:
        raise ValueError(
            f"unknown metadata key(s) {sorted(unknown)} — supported: "
            "title, author, subject"
        )
    out = {
        pdf_key: str(metadata[key])
        for key, pdf_key in _META_KEYS.items()
        if metadata.get(key) is not None
    }
    return out or None


def _encrypt(writer: PdfWriter, password: str) -> None:
    if _HAS_CRYPTOGRAPHY:
        writer.encrypt(user_password=password, algorithm="AES-256")
    else:  # pragma: no cover - cryptography is a base dependency
        writer.encrypt(user_password=password, algorithm="RC4-128")


def _verify_page_count(path: Path, password: "str | None" = None) -> int:
    """Re-open a written file and return the REAL page count (honesty rule)."""
    with open(path, "rb") as fh:
        reader = PdfReader(fh)
        if reader.is_encrypted:
            reader.decrypt(password or "")
        return len(reader.pages)


def _coerce_input(item: "ArrangeInput | dict[str, Any]") -> ArrangeInput:
    if isinstance(item, ArrangeInput):
        return item
    if isinstance(item, dict):
        unknown = set(item) - {"path", "pages_spec", "password"}
        if unknown:
            raise ValueError(
                f"unknown input key(s) {sorted(unknown)} — supported: "
                "path, pages_spec, password"
            )
        if "path" not in item:
            raise ValueError("each input needs a 'path'")
        return ArrangeInput(
            path=item["path"],
            pages_spec=item.get("pages_spec") or "all",
            password=item.get("password"),
        )
    raise ValueError(f"invalid input {item!r} — expected ArrangeInput or dict")


# --- operations ----------------------------------------------------------------


def arrange(
    inputs: "list[ArrangeInput | dict[str, Any]]",
    out_path: str | Path,
    *,
    crop: "dict[str, Any] | None" = None,
    encrypt_password: "str | None" = None,
    metadata: "dict[str, Any] | None" = None,
) -> ArrangeReport:
    """Build ONE new PDF from page selections over one or more inputs.

    Multiple inputs merge in order; each input's ``pages_spec`` reorders /
    duplicates / reverses / deletes-by-omission / rotates its pages (grammar in
    the module docstring).  ``crop`` shrinks every OUTPUT page's mediabox and
    cropbox by percent margins ``{top, right, bottom, left}`` of the page AS
    DISPLAYED — margins are mapped through each page's effective ``/Rotate``,
    so "top" always means the top the user sees.  ``metadata``
    sets ``{title, author, subject}``.  ``encrypt_password`` encrypts the
    output (AES-256 when ``cryptography`` is available).  Inputs are never
    modified; the output is written atomically and re-opened to verify the
    page counts reported.
    """
    coerced = [_coerce_input(i) for i in inputs or []]
    if not coerced:
        raise ValueError("at least one input PDF is required")
    margins = _validate_crop(crop)
    meta = _validate_metadata(metadata)
    out_p = Path(out_path)
    out_resolved = out_p.resolve()

    inputs_report: list[dict[str, Any]] = []
    with ExitStack() as stack:
        opened: list[tuple[PdfReader, list[tuple[int, int]]]] = []
        for item in coerced:
            p = _require_pdf_path(item.path)
            if p.resolve() == out_resolved:
                raise ValueError(
                    f"output path {out_p} is an input file — inputs are never "
                    "overwritten; pick a new output name"
                )
            fh = stack.enter_context(open(p, "rb"))
            reader = _open_reader(fh, p, item.password)
            selection = parse_page_spec(item.pages_spec or "all", len(reader.pages))
            opened.append((reader, selection))
            inputs_report.append(
                {"path": str(p), "pages": len(reader.pages), "used": 0}
            )

        total = sum(len(sel) for _, sel in opened)
        if total == 0:
            raise ValueError("the page selection is empty — nothing to write")
        if total > MAX_TOTAL_PAGES:
            raise ValueError(
                f"refusing to build a {total}-page PDF — the limit is "
                f"{MAX_TOTAL_PAGES} pages per job; split the work into batches"
            )

        writer = PdfWriter()
        last_size: "tuple[float, float] | None" = None
        for (reader, selection), report in zip(opened, inputs_report):
            used, last_size = _append_selection(writer, reader, selection, last_size)
            report["used"] = used
        if margins:
            for page in writer.pages:
                _apply_crop(page, margins)
        if meta:
            writer.add_metadata(meta)
        if encrypt_password:
            _encrypt(writer, encrypt_password)

        out_p.parent.mkdir(parents=True, exist_ok=True)
        with _atomic(out_p) as tmp:
            with open(tmp, "wb") as fh:
                writer.write(fh)

    return ArrangeReport(
        path=str(out_p),
        pages=_verify_page_count(out_p, encrypt_password),
        inputs=inputs_report,
    )


def split(
    path: str | Path,
    out_dir: str | Path,
    *,
    mode: dict[str, Any],
    password: "str | None" = None,
) -> SplitReport:
    """Split one PDF into several, one of three modes.

    ``mode`` is EXACTLY one of ``{"ranges": ["1-3", "4-end"]}`` (each range is
    a full page-spec, so rotation suffixes work), ``{"every": N}`` (chunks of
    N pages) or ``{"per_page": True}``.  Outputs are named
    ``<stem>-part01.pdf`` ... in ``out_dir`` and NEVER clobber an existing
    file (a free ``-2``/``-3`` suffix is chosen instead).  A failure mid-run
    removes the parts already written — all parts or none.  Each output is
    re-opened to report its REAL page count.
    """
    p = _require_pdf_path(path)
    ranges = _validate_mode(mode)
    out = Path(out_dir)

    with open(p, "rb") as fh:
        reader = _open_reader(fh, p, password)
        n = len(reader.pages)
        if n == 0:
            raise ValueError(f"{p.name} has no pages to split")
        if n > MAX_TOTAL_PAGES:
            raise ValueError(
                f"refusing to split {p.name}: it has {n} pages — the limit is "
                f"{MAX_TOTAL_PAGES}"
            )
        if ranges is not None:
            selections = [parse_page_spec(r, n) for r in ranges]
        elif "every" in mode:
            every = int(mode["every"])
            indices = [(i, 0) for i in range(n)]
            selections = [
                indices[i : i + every] for i in range(0, n, every)
            ]
        else:  # per_page
            selections = [[(i, 0)] for i in range(n)]
        total = sum(len(sel) for sel in selections)
        if total > MAX_TOTAL_PAGES:
            raise ValueError(
                f"refusing to write {total} pages across parts — the limit is "
                f"{MAX_TOTAL_PAGES}"
            )

        out.mkdir(parents=True, exist_ok=True)
        width = max(2, len(str(len(selections))))
        outputs: list[dict[str, Any]] = []
        written: list[Path] = []
        try:
            for i, selection in enumerate(selections, 1):
                target = _no_clobber(out / f"{p.stem}-part{i:0{width}d}.pdf")
                writer = PdfWriter()
                _append_selection(writer, reader, selection, None)
                with _atomic(target) as tmp:
                    with open(tmp, "wb") as out_fh:
                        writer.write(out_fh)
                written.append(target)
                outputs.append({"path": str(target), "pages": 0})
        except BaseException:
            # All parts or none: never leave a partial split behind.  Only
            # files WE created this run are removed — never a pre-existing one.
            for part in written:
                try:
                    part.unlink()
                except OSError:
                    pass
            raise

    for entry in outputs:
        entry["pages"] = _verify_page_count(Path(entry["path"]))
    return SplitReport(outputs=outputs)


def _validate_mode(mode: dict[str, Any]) -> "list[str] | None":
    """Validate the split mode; returns the ranges list, or None for the
    every / per_page modes (which read ``mode`` directly)."""
    if not isinstance(mode, dict) or not mode:
        raise ValueError(
            "mode must be exactly one of {'ranges': [...]}, {'every': N} or "
            "{'per_page': true}"
        )
    unknown = set(mode) - {"ranges", "every", "per_page"}
    if unknown:
        raise ValueError(
            f"unknown split mode key(s) {sorted(unknown)} — supported: "
            "ranges, every, per_page"
        )
    if len(mode) != 1:
        raise ValueError(
            f"split mode takes exactly ONE of ranges/every/per_page — got "
            f"{sorted(mode)}"
        )
    if "ranges" in mode:
        ranges = mode["ranges"]
        if not isinstance(ranges, list) or not ranges:
            raise ValueError("ranges must be a non-empty list of page specs")
        if not all(isinstance(r, str) for r in ranges):
            raise ValueError("each range must be a page-spec string like '1-3'")
        return list(ranges)
    if "every" in mode:
        every = mode["every"]
        if not isinstance(every, int) or isinstance(every, bool) or every < 1:
            raise ValueError(f"every must be a positive integer — got {every!r}")
        return None
    if not mode["per_page"]:
        raise ValueError("per_page must be true when given")
    return None


def _no_clobber(candidate: Path) -> Path:
    """Return ``candidate`` if free, else the first free ``-2``/``-3``... name."""
    if not candidate.exists():
        return candidate
    for i in range(2, 1000):
        alt = candidate.with_name(f"{candidate.stem}-{i}{candidate.suffix}")
        if not alt.exists():
            return alt
    raise ValueError(f"could not find a free output name near {candidate.name}")


def pdf_info(path: str | Path, password: "str | None" = None) -> dict[str, Any]:
    """Inspect a PDF: the read side agents use before arranging.

    Returns ``{path, pages, encrypted, page_sizes, metadata}`` — sizes are
    per-page mediabox ``{width, height}`` in points; metadata is whichever of
    title/author/subject/creator/producer the file carries.  An encrypted file
    without (or with a wrong) password raises the same honest errors as
    :func:`arrange`.
    """
    p = _require_pdf_path(path)
    with open(p, "rb") as fh:
        reader = _open_reader(fh, p, password)
        sizes = [
            {
                "width": round(float(page.mediabox.width), 2),
                "height": round(float(page.mediabox.height), 2),
            }
            for page in reader.pages
        ]
        meta: dict[str, str] = {}
        try:
            info = reader.metadata
        except Exception:  # malformed /Info must not sink the whole inspect
            info = None
        if info is not None:
            for key in ("title", "author", "subject", "creator", "producer"):
                value = getattr(info, key, None)
                if value:
                    meta[key] = str(value)
        return {
            "path": str(p),
            "pages": len(reader.pages),
            "encrypted": bool(reader.is_encrypted),
            "page_sizes": sizes,
            "metadata": meta,
        }
