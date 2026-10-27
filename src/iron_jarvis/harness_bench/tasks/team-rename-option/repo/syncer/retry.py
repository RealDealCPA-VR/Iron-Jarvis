"""Trying an operation again when it fails for a passing reason."""

import time

from .errors import TransientError

#: How many times a failed operation is tried again unless told otherwise.
DEFAULT_RETRIES = 3


def with_retries(fn, *, max_retries=DEFAULT_RETRIES, delay_s=0.0, sleep=time.sleep):
    """Calls ``fn()`` and returns what it returns.

    When ``fn`` raises ``TransientError`` it is called again, up to
    ``max_retries`` more times (so at most ``max_retries + 1`` calls), waiting
    ``delay_s`` seconds before each new try. When every try failed, the last
    ``TransientError`` is raised. Any other exception is raised at once.
    ``max_retries`` must be 0 or more (``ValueError`` otherwise).
    """
    if max_retries < 0:
        raise ValueError("max_retries must be 0 or more")
    tried_again = 0
    while True:
        try:
            return fn()
        except TransientError:
            if tried_again >= max_retries:
                raise
            tried_again += 1
            if delay_s:
                sleep(delay_s)
