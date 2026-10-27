"""Uploading and downloading files."""

from syncer_compat import accepts_max_retries
from .retry import with_retries


@accepts_max_retries
def upload(remote, files, *, retry_limit=None):
    """Puts every file of ``files`` (a dict: name -> bytes) on the remote, in
    name order, and returns the names. Each file is tried again up to
    ``retry_limit`` times (default: the remote's own setting)."""
    limit = remote.retry_limit if retry_limit is None else retry_limit
    done = []
    for name in sorted(files):
        with_retries(lambda: remote.transport.put(name, files[name]), retry_limit=limit)
        done.append(name)
    return done


@accepts_max_retries
def download(remote, names, *, retry_limit=None):
    """Gets every named file from the remote: a dict name -> bytes. Each file
    is tried again up to ``retry_limit`` times (default: the remote's own
    setting)."""
    limit = remote.retry_limit if retry_limit is None else retry_limit
    return {name: with_retries(lambda: remote.transport.get(name), retry_limit=limit) for name in names}
