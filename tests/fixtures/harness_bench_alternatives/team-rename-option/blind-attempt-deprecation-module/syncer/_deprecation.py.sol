"""Deprecated names: one warning per old name per process, from our own record."""

import threading
import warnings

_UNSET = object()
_WARNED = set()
_LOCK = threading.Lock()


def warn_once(key, message, stacklevel=3):
    """Warn with ``DeprecationWarning`` the first time ``key`` is used in this
    process; never again after that, whatever the warnings filters say."""
    with _LOCK:
        if key in _WARNED:
            return
        _WARNED.add(key)
    warnings.warn(message, DeprecationWarning, stacklevel=stacklevel)


def keyword(retry_limit, max_retries, default):
    """The retry limit given the ``retry_limit`` and the deprecated
    ``max_retries`` keywords (each ``_UNSET`` when not given); ``default``
    when neither was given. Both together is a ``TypeError``."""
    if max_retries is not _UNSET:
        if retry_limit is not _UNSET:
            raise TypeError("max_retries and retry_limit were both given; use only retry_limit")
        warn_once("keyword", "max_retries is deprecated; use retry_limit", stacklevel=4)
        return max_retries
    return default if retry_limit is _UNSET else retry_limit
