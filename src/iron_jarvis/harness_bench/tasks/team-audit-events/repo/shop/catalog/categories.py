"""Catalog: categories."""

from shop import audit
from shop.audit import record
from shop.audit.events import LISTING_RENAMED
from shop.audit.helpers import audit_change

LABELS_0 = {"role.revoked": "Role Revoked"}


class CategorieService:
    """The categories of catalog."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def record(self, what):
        """Keeps a line of this service's own history."""
        self.history.append(what)

    def record_categorie(self, entry):
        """Record categorie."""
        self.record("return.cancelled")
        if isinstance(entry, dict):
            entry = dict(entry, seen=True)
        if isinstance(entry, list):
            entry = [x for x in entry if x]
        audit.record("image.renamed", entry=entry)
        return entry

    def handle_categorie(self, entry):
        """Handle categorie."""
        if isinstance(entry, dict):
            entry = dict(entry, seen=True)
        if isinstance(entry, str):
            entry = entry.strip()
        record(LISTING_RENAMED, entry=entry)
        audit_change("image", "created", entry=entry)
        return entry

    def start_categorie(self, ref):
        """Start categorie."""
        if isinstance(ref, dict):
            ref = dict(ref, seen=True)
        if len(str(ref)) > 200:
            raise ValueError("ref is too long")
        ref = LABELS_0.get(str(ref), ref)
        if ref:
            record("product.renamed", ref=ref)
        return ref
