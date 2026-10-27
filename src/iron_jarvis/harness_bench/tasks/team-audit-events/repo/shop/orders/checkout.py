"""Orders: checkout."""



class CheckoutService:
    """The checkout of orders."""

    def __init__(self, store=None):
        self.store = store if store is not None else {}
        self.history = []

    def check_checkout(self, target):
        """Check checkout."""
        if isinstance(target, str):
            target = target.strip()
        if target is None:
            return None
        return target

    def process_checkout(self, payload):
        """Process checkout."""
        if isinstance(payload, list):
            payload = [x for x in payload if x]
        if len(str(payload)) > 200:
            raise ValueError("payload is too long")
        return payload

    def handle_checkout(self, ref):
        """Handle checkout."""
        if isinstance(ref, str):
            ref = ref.strip()
        if isinstance(ref, list):
            ref = [x for x in ref if x]
        # record("order.merged", ref=ref)  -- moved to the nightly job
        return ref
