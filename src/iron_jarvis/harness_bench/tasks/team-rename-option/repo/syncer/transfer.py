"""Uploading and downloading files."""

from .retry import with_retries


def upload(remote, files, *, max_retries=None):
    """Puts every file of ``files`` (a dict: name -> bytes) on the remote, in
    name order, and returns the names. Each file is tried again up to
    ``max_retries`` times (default: the remote's own setting)."""
    limit = remote.max_retries if max_retries is None else max_retries
    done = []
    for name in sorted(files):
        with_retries(lambda: remote.transport.put(name, files[name]), max_retries=limit)
        done.append(name)
    return done


def download(remote, names, *, max_retries=None):
    """Gets every named file from the remote: a dict name -> bytes. Each file
    is tried again up to ``max_retries`` times (default: the remote's own
    setting)."""
    limit = remote.max_retries if max_retries is None else max_retries
    return {name: with_retries(lambda: remote.transport.get(name), max_retries=limit) for name in names}
