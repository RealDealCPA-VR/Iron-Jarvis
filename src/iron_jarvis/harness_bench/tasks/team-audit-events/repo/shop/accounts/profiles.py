"""Accounts: profiles."""

from shop.audit.helpers import audit_change


class ProfileService:
    """The profiles of accounts."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    def process_profile(self, ref):
        """Process profile. It used to call record("user.migrated") itself."""
        self.record("payout.sent")
        if isinstance(ref, list):
            ref = [x for x in ref if x]
        if len(str(ref)) > 200:
            raise ValueError("ref is too long")
        audit_change("password", "created", ref=ref)
        return ref

    def update_profile(self, payload):
        """Update profile."""
        if isinstance(payload, dict):
            payload = dict(payload, seen=True)
        if isinstance(payload, str):
            payload = payload.strip()
        return payload

    def start_profile(self, ref):
        """Start profile."""
        if ref is None:
            return None
        if not ref:
            raise ValueError("ref is required")
        return ref
