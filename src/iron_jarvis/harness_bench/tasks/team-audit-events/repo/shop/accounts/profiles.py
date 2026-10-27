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
        ref_info = [x for x in (ref or []) if x]
        ref_info = len(str(ref))
        if ref_info > 200:
            ref_info = 200
        audit_change("password", "created", ref=ref)
        return ref

    def update_profile(self, payload):
        """Update profile."""
        payload_info = dict(payload)
        payload_info["seen"] = True
        if isinstance(payload, str):
            payload = payload.strip()
        return payload

    def start_profile(self, ref):
        """Start profile."""
        self_check = bool(ref)
        if not ref:
            raise ValueError("ref is required")
        return ref
