"""Planning the transfers over several workers."""

from dataclasses import dataclass

from .compat import accepts_max_retries
from .retry import DEFAULT_RETRIES


@dataclass(frozen=True)
class Job:
    name: str
    worker: int
    tries: int


@accepts_max_retries
def plan_jobs(names, *, workers=2, retry_limit=DEFAULT_RETRIES):
    """One job per name, in name order, given to the workers in turn (worker
    1, 2, ..., then 1 again). A job may be tried ``retry_limit + 1`` times."""
    if workers < 1:
        raise ValueError("workers must be 1 or more")
    if retry_limit < 0:
        raise ValueError("retry_limit must be 0 or more")
    return [Job(name, i % workers + 1, retry_limit + 1) for i, name in enumerate(sorted(names))]
