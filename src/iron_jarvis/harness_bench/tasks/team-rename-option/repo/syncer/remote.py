"""A remote store."""

from .retry import DEFAULT_RETRIES, with_retries


class Remote:
    """A remote store reached through a ``transport``: any object with
    ``get(path) -> bytes`` and ``put(path, data)``. Every ``get`` and ``put`` is
    tried again up to ``max_retries`` times when it fails with
    ``TransientError``."""

    def __init__(self, url, transport, *, max_retries=DEFAULT_RETRIES, timeout_s=10.0):
        if max_retries < 0:
            raise ValueError("max_retries must be 0 or more")
        self.url = url
        self.transport = transport
        self.max_retries = max_retries
        self.timeout_s = timeout_s

    def get(self, path):
        return with_retries(lambda: self.transport.get(path), max_retries=self.max_retries)

    def put(self, path, data):
        return with_retries(lambda: self.transport.put(path, data), max_retries=self.max_retries)

    def __repr__(self):
        return f"Remote({self.url!r}, max_retries={self.max_retries})"
