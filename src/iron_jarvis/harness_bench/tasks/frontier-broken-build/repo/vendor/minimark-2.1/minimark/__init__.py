"""minimark 2.1 — a very small markup language.

    >>> import minimark
    >>> minimark.Document("Hello *world*").to_html()
    '<p>Hello <em>world</em></p>\\n'

Supported: ``#`` headings, paragraphs, ``- `` lists, pipe tables (with column
alignment), ``**strong**``, ``*emphasis*``, ```code``` spans and
``[links](url)``. Inline text is read by tinylex (see PKG-INFO for the
version it needs).
"""

__version__ = "2.1.0"

from .document import Document  # noqa: E402

__all__ = ["Document", "__version__"]
