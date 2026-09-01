"""The Iron Jarvis Guide — the built-in expert on the app itself (v1.223.0).

A chat persona (``guide``) whose every turn is grounded in a retrieved block
from the app's own bundled docs and live catalogs (``corpus.py``). Reached
from the Help page's "Ask the Guide" box, the chat persona picker, or any
``/chat?persona=guide`` link.
"""

from .corpus import (  # noqa: F401
    BUNDLED_DOCS,
    DEFAULT_GROUND_CHARS,
    GUIDE_PERSONA,
    GuideIndex,
    Section,
    doc_path,
    docs_root,
    ground,
    index_for,
    live_sections,
    split_markdown,
)
