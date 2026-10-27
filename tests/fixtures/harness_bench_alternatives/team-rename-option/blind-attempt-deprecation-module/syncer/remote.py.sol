"""A remote store."""

from ._deprecation import _UNSET, keyword, warn_once
from .retry import DEFAULT_RETRIES, with_retries


class Remote:
    """A remote store reached through a ``transport``: any object with
    ``get(path) -> bytes`` and ``put(path, data)``. Every ``get`` and ``put`` is
    tried again up to ``retry_limit`` times when it fails with
    ``TransientError``."""

    def __init__(self, url, transport, *, retry_limit=_UNSET, timeout_s=10.0, max_retries=_UNSET):
        retry_limit = keyword(retry_limit, max_retries, DEFAULT_RETRIES)
        if retry_limit < 0:
            raise ValueError("retry_limit must be 0 or more")
        self.url = url
        self.transport = transport
        self.retry_limit = retry_limit
        self.timeout_s = timeout_s

    @property
    def max_retries(self):
        """Deprecated alias of ``retry_limit``."""
        warn_once("attribute", "the max_retries attribute is deprecated; use retry_limit")
        return self.retry_limit

    def get(self, path):
        return with_retries(lambda: self.transport.get(path), retry_limit=self.retry_limit)

    def put(self, path, data):
        return with_retries(lambda: self.transport.put(path, data), retry_limit=self.retry_limit)

    def __repr__(self):
        return f"Remote({self.url!r}, retry_limit={self.retry_limit})"
