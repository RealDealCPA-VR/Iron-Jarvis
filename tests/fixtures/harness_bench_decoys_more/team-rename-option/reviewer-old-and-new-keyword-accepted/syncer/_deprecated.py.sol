"""The deprecated names of release 2.4.0 (RENAME.md): each warns once per process."""

import warnings

#: Stands for "not given" in the functions that accept the old keyword.
MISSING = object()

#: The old names that have warned in this process (a form, not a value).
_warned = set()


def warn_once(form, old, new):
    """One DeprecationWarning per old name per process, whatever the filters say."""
    if form in _warned:
        return
    _warned.add(form)
    warnings.warn(f"{old} is deprecated; use {new}", DeprecationWarning, stacklevel=3)


def retry_limit(new, old, default):
    """The retry_limit a function was given: ``new`` (retry_limit=) or ``old``
    (the deprecated max_retries=), ``default`` when neither. Both: TypeError."""
    if old is MISSING:
        return default if new is MISSING else new
    if new is not MISSING:
        return new  # the new name wins
    warn_once("keyword", "max_retries", "retry_limit")
    return old
