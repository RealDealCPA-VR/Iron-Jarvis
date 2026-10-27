"""The audit trail: every change to money, stock or access is written here.

``from shop.audit import record`` (or ``from shop import audit`` and
``audit.record(...)``) is the way in; see ``docs/audit.md``.
"""

from . import events
from .log import entries, record

__all__ = ["entries", "events", "record"]
