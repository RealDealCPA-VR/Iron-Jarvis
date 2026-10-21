"""A small client for the inventory service."""
import time

import settings_loader


class Client:
    """Talks to the inventory service.

    retries: how many times a failed request is tried again (default 3).
    timeout_s: seconds to wait for one answer.
    """

    def __init__(self, retries=None, timeout_s=None):
        cfg = settings_loader.load()
        self.retries = cfg["retries"] if retries is None else retries
        self.timeout_s = cfg["timeout_s"] if timeout_s is None else timeout_s
        self.backoff = cfg["backoff"]

    def call(self, send):
        """Call ``send()`` until it succeeds or the retries run out."""
        attempt = 0
        while True:
            try:
                return send()
            except ConnectionError:
                if attempt >= self.retries:
                    raise
                time.sleep(self.backoff * attempt)
                attempt += 1
