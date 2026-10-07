"""The Iron Jarvis Guide — the built-in expert on the app itself (v1.224.0).

A built-in AGENT (``AgentType.GUIDE``, in the roster beside builder and the
rest) with base knowledge of the app injected into every session
(``base_knowledge``) and read-only tools that look the rest up (``tools.py``:
``guide_search`` / ``guide_read`` over the bundled docs + live catalogs in
``corpus.py``, ``app_search`` / ``app_status`` over the user's own things in
this install). @-mentioned in chat (``@guide``) it answers grounded in the
same retrieval. Reached from the Help page's "Ask the Guide" box (which puts
``@guide …`` into chat), an ``@guide`` mention, Your team on the Agents page,
or a session run as the ``guide`` agent. (v1.309.0: the Agents page's old
talk and hand-off tabs and its round table went in v1.308.0; this docstring
still pointed there for a release.)
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
