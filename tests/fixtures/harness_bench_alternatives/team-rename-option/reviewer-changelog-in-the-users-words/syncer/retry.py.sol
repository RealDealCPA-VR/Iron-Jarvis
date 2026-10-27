"""Trying an operation again when it fails for a passing reason."""

import time

from . import _deprecated
from .errors import TransientError

#: How many times a failed operation is tried again unless told otherwise.
DEFAULT_RETRIES = 3


def with_retries(fn, *, retry_limit=_deprecated.MISSING, delay_s=0.0, sleep=time.sleep,
                 max_retries=_deprecated.MISSING):
    """Calls ``fn()`` and returns what it returns.

    When ``fn`` raises ``TransientError`` it is called again, up to
    ``retry_limit`` more times (default 3; so at most ``retry_limit + 1``
    calls), waiting ``delay_s`` seconds before each new try. When every try
    failed, the last ``TransientError`` is raised. Any other exception is
    raised at once. ``retry_limit`` must be 0 or more (``ValueError``
    otherwise). ``max_retries`` is its deprecated old name.
    """
    limit = _deprecated.retry_limit(retry_limit, max_retries, DEFAULT_RETRIES)
    if limit < 0:
        raise ValueError("retry_limit must be 0 or more")
    tried_again = 0
    while True:
        try:
            return fn()
        except TransientError:
            if tried_again >= limit:
                raise
            tried_again += 1
            if delay_s:
                sleep(delay_s)
