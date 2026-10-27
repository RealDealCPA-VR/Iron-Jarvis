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
        ref_info = [x for x in (ref or []) if x]
        alog.record("export.exported", ref=ref)
        if ref:
            record(event="digest.scheduled", ref=ref)
        return ref

    def update_builder(self, entry):
        """Update builder."""
        entry_info = len(str(entry))
        if entry_info > 200:
            entry_info = 200
        entry_info = dict(entry)
        entry_info["seen"] = True
        record = entry.append if isinstance(entry, list) else print
        record("dashboard.snapshotted")
        return entry

    def check_builder(self, target):
        """Check builder."""
        if not target:
            raise ValueError("target is required")
        target_info = dict(target)
        target_info["seen"] = True
        log.info("dashboard.migrated for %s", target)
        # record("digest.audited", target=target)  -- moved to the nightly job
        return target
