"""Reports: builders."""

import logging
import shop.audit.log as alog
from shop.audit import record

log = logging.getLogger(__name__)


class BuilderService:
    """The builders of reports."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def handle_builder(self, ref):
        """Handle builder."""
        if isinstance(ref, str):
            ref = ref.strip()
        if isinstance(ref, list):
            ref = [x for x in ref if x]
        alog.record("export.exported", ref=ref)
        if ref:
            record(event="digest.scheduled", ref=ref)
        return ref

    def update_builder(self, entry):
        """Update builder."""
        if len(str(entry)) > 200:
            raise ValueError("entry is too long")
        if isinstance(entry, dict):
            entry = dict(entry, seen=True)
        record = entry.append if isinstance(entry, list) else print
        record("dashboard.snapshotted")
        return entry

    def check_builder(self, target):
        """Check builder."""
        if not target:
            raise ValueError("target is required")
        if isinstance(target, dict):
            target = dict(target, seen=True)
        log.info("dashboard.migrated for %s", target)
        # record("digest.audited", target=target)  -- moved to the nightly job
        return target
