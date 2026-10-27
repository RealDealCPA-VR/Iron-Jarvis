"""Uploading and downloading files."""

from . import _deprecated
from .retry import with_retries


def upload(remote, files, *, retry_limit=_deprecated.MISSING, max_retries=_deprecated.MISSING):
    """Puts every file of ``files`` (a dict: name -> bytes) on the remote, in
    name order, and returns the names. Each file is tried again up to
    ``retry_limit`` times (default: the remote's own setting). ``max_retries``
    is its deprecated old name."""
    limit = _deprecated.retry_limit(retry_limit, max_retries, None)
    if limit is None:
        limit = remote.retry_limit
    done = []
    for name in sorted(files):
        with_retries(lambda: remote.transport.put(name, files[name]), retry_limit=limit)
        done.append(name)
    return done


def download(remote, names, *, retry_limit=_deprecated.MISSING, max_retries=_deprecated.MISSING):
    """Gets every named file from the remote: a dict name -> bytes. Each file
    is tried again up to ``retry_limit`` times (default: the remote's own
    setting). ``max_retries`` is its deprecated old name."""
    limit = _deprecated.retry_limit(retry_limit, max_retries, None)
    if limit is None:
        limit = remote.retry_limit
    return {name: with_retries(lambda: remote.transport.get(name), retry_limit=limit) for name in names}
