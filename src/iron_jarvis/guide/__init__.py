"""The Iron Jarvis Guide — the built-in expert on the app itself (v1.224.0).

A built-in AGENT (``AgentType.GUIDE``, in the roster beside builder and the
rest) with base knowledge of the app injected into every session
(``base_knowledge``) and read-only tools that look the rest up (``tools.py``:
``guide_search`` / ``guide_read`` over the bundled docs + live catalogs in
``corpus.py``, ``app_search`` / ``app_status`` over the user's own things in
this install). At the round table it answers grounded in the same retrieval.
Reached from the Agents page (Talk / Give work), the Help page's "Ask the
Guide" box, or a session run as the ``guide`` agent.
"""

from .corpus import (  # noqa: F401
    BUNDLED_DOCS,
    DEFAULT_GROUND_CHARS,
    GUIDE_PERSONA,
    GuideIndex,
    Section,
    base_knowledge,
    doc_path,
    docs_root,
    ground,
    index_for,
    live_sections,
    split_markdown,
)
