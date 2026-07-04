"""Structure-preserving PDF (and office/HTML) -> Markdown conversion.

The legacy path flattens a PDF to plain text: :func:`extract_text` runs pypdf's
``extract_text()`` per page and the result is written verbatim into a ``.md``,
so headings, lists and tables are lost. This module upgrades that with
`markitdown <https://github.com/microsoft/markitdown>`_ (MIT), which emits
real Markdown — ``#`` headings, ``-`` bullets, ``|`` tables — for the formats
whose optional dependencies are installed (``markitdown[pdf]`` here covers PDF
and HTML; docx/pptx/xlsx need their own extras).

Two design constraints, both for the frozen Windows build:

* **Type detection by extension, not content-sniffing.** markitdown ships
  magika (an onnxruntime model) to guess a file's type from its bytes. We pass
  an explicit ``StreamInfo(extension=...)`` hint built from the real filename,
  so detection is by suffix and the model is never *invoked* for our calls.
  (Note: ``import markitdown`` still imports magika/onnxruntime eagerly, so a
  frozen build must bundle them regardless — this only avoids running the
  model, not loading it.)
* **Never raise for a readable file.** Any markitdown failure — a missing
  optional dependency, a corrupt file, an empty result — falls back to the
  existing :func:`extract_text` flattened text and logs a warning. Callers get
  the best available text, never an exception.

Public surface:

* :func:`pdf_to_markdown` — a PDF path to structured Markdown.
* :func:`document_to_markdown` — the same, generalised to the other formats
  markitdown can handle (docx/pptx/xlsx/html), with the ``extract_text``
  fallback for anything it can't.
"""

from __future__ import annotations

import logging
from pathlib import Path

from .readers import extract_text

logger = logging.getLogger("iron_jarvis.documents.pdf_markdown")

#: Suffixes we route through markitdown for structure-preserving Markdown.
#: PDF and HTML work with the installed ``markitdown[pdf]`` extra; the office
#: formats need their own extras and otherwise fall back to ``extract_text``.
MARKITDOWN_SUFFIXES: frozenset[str] = frozenset(
    {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm"}
)


def _markitdown_convert(path: Path) -> str:
    """Convert ``path`` via markitdown, passing an explicit extension hint.

    Raises whatever markitdown raises (missing optional dep, parse error) so
    the callers can decide to fall back. Returns the Markdown text content.
    """
    from markitdown import MarkItDown, StreamInfo

    # Extension-only hint => type detection is by suffix, not the magika model.
    stream_info = StreamInfo(extension=path.suffix.lower())
    result = MarkItDown(enable_plugins=False).convert(
        str(path), stream_info=stream_info
    )
    return result.text_content or ""


def document_to_markdown(path: str | Path) -> str:
    """Return Markdown for ``path``, structure-preserving where possible.

    PDF/office/HTML files go through markitdown for real Markdown structure;
    everything else (and any markitdown failure) falls back to the flattened
    :func:`extract_text`. Never raises for a readable file — a conversion
    problem degrades to plain text with a logged warning.
    """
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix in MARKITDOWN_SUFFIXES:
        try:
            text = _markitdown_convert(p)
        except Exception as exc:  # missing extra, corrupt file, etc.
            logger.warning(
                "markitdown could not convert %s (%s: %s); "
                "falling back to flattened text",
                p.name,
                type(exc).__name__,
                exc,
            )
        else:
            if text.strip():
                return text
            # An empty markitdown result may just mean a scanned/image PDF;
            # the legacy extractor sometimes still finds text, so try it.
            logger.warning(
                "markitdown returned empty text for %s; "
                "falling back to flattened text",
                p.name,
            )

    return extract_text(p)


def pdf_to_markdown(path: str | Path) -> str:
    """Convert a PDF to structured Markdown (falls back to flattened text).

    Thin specialisation of :func:`document_to_markdown` for the priority PDF
    case. Passing a non-PDF path still works — it is routed the same way — but
    the intent (and the docstring contract) is PDF in, Markdown out.
    """
    return document_to_markdown(path)
