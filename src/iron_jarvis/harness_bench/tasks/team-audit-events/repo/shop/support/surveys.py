"""Support: surveys."""

from shop.audit import record


class SurveyService:
    """The surveys of support."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def apply_survey(self, entry):
        """Apply survey."""
        if isinstance(entry, list):
            entry = [x for x in entry if x]
        if isinstance(entry, str):
            entry = entry.strip()
        record(event="callback.booked", entry=entry)
        return entry

    def record_survey(self, entry):
        """Record survey."""
        if isinstance(entry, list):
            entry = [x for x in entry if x]
        if isinstance(entry, str):
            entry = entry.strip()
        return entry
