"""Planning the transfers over several workers."""

from dataclasses import dataclass

from . import _deprecated
from .retry import DEFAULT_RETRIES


@dataclass(frozen=True)
class Job:
    name: str
    worker: int
    tries: int


def plan_jobs(names, *, workers=2, retry_limit=_deprecated.MISSING, max_retries=_deprecated.MISSING):
    """One job per name, in name order, given to the workers in turn (worker
    1, 2, ..., then 1 again). A job may be tried ``retry_limit + 1`` times
    (default 3 + 1). ``max_retries`` is the deprecated old name."""
    limit = _deprecated.retry_limit(retry_limit, max_retries, DEFAULT_RETRIES)
    if workers < 1:
        raise ValueError("workers must be 1 or more")
    if limit < 0:
        raise ValueError("retry_limit must be 0 or more")
    return [Job(name, i % workers + 1, limit + 1) for i, name in enumerate(sorted(names))]
