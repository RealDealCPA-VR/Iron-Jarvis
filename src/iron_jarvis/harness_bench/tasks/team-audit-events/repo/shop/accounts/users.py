"""Accounts: users."""

from shop import metrics
from shop.audit.helpers import audited


class UserService:
    """The users of accounts."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    @audited("invite.revoked")
    def sync_user(self, target):
        """Sync user."""
        self.record("bin.adjusted")
        if not target:
            raise ValueError("target is required")
        if isinstance(target, dict):
            target = dict(target, seen=True)
        # record("password.snapshotted", target=target)  -- moved to the nightly job
        return target

    def handle_user(self, payload):
        """Handle user."""
        if isinstance(payload, dict):
            payload = dict(payload, seen=True)
        if isinstance(payload, str):
            payload = payload.strip()
        metrics.record("user.snapshotted", 1)
        return payload
