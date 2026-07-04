"""Documents module — read and write real-world file types.

Gives Iron Jarvis the ability to read AND write PDF, Word (.docx), Excel
(.xlsx), PowerPoint (.pptx), CSV, HTML, plus .txt/.md/code, so agents can work
with a user's actual documents. String content is markdown-aware: headings,
lists, tables, code fences and bold/italic render as REAL structure in
.docx/.pdf/.pptx/.html (see :mod:`.markdown`).

Public surface:

* :func:`extract_text` — text out of any supported file.
* :func:`write_document` — a real file in by suffix/kind.
* :func:`parse_markdown` / :class:`Block` — the structured-markdown blocks.
* :data:`SUPPORTED_READ` / :data:`SUPPORTED_WRITE` — advertised suffixes.
* :func:`document_tools` — the read/write/extract/convert Tools for the registry.
"""

from __future__ import annotations

from .markdown import Block, parse_markdown
from .readers import SUPPORTED_READ, extract_text
from .tools import (
    ConvertDocumentTool,
    ExtractPdfTool,
    ReadDocumentTool,
    WriteDocumentTool,
    document_tools,
)
from .writers import SUPPORTED_WRITE, write_document

__all__ = [
    "extract_text",
    "write_document",
    "parse_markdown",
    "Block",
    "SUPPORTED_READ",
    "SUPPORTED_WRITE",
    "document_tools",
    "ReadDocumentTool",
    "WriteDocumentTool",
    "ExtractPdfTool",
    "ConvertDocumentTool",
]
