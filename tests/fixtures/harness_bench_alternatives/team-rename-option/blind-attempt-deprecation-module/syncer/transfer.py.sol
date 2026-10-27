"""Uploading and downloading files."""

from ._deprecation import _UNSET, keyword
from .retry import with_retries


def upload(remote, files, *, retry_limit=_UNSET, max_retries=_UNSET):
    """Puts every file of ``files`` (a dict: name -> bytes) on the remote, in
    name order, and returns the names. Each file is tried again up to
    ``retry_limit`` times (default: the remote's own setting).
    ``max_retries`` is a deprecated alias of ``retry_limit``."""
    limit = keyword(retry_limit, max_retries, None)
    if limit is None:
        limit = remote.retry_limit
    done = []
    for name in sorted(files):
        with_retries(lambda: remote.transport.put(name, files[name]), retry_limit=limit)
        done.append(name)
    return done


def download(remote, names, *, retry_limit=_UNSET, max_retries=_UNSET):
    """Gets every named file from the remote: a dict name -> bytes. Each file
    is tried again up to ``retry_limit`` times (default: the remote's own
    setting). ``max_retries`` is a deprecated alias of ``retry_limit``."""
    limit = keyword(retry_limit, max_retries, None)
    if limit is None:
        limit = remote.retry_limit
    return {name: with_retries(lambda: remote.transport.get(name), retry_limit=limit) for name in names}
