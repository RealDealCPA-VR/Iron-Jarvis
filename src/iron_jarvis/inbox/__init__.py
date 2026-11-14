"""The Improvements inbox (v1.495.0): one read over every waiting suggestion.

See :mod:`iron_jarvis.inbox.collect`.
"""

from .collect import KINDS, LABELS, collect

__all__ = ["KINDS", "LABELS", "collect"]
