"""Planning the transfers over several workers."""

from dataclasses import dataclass

from .retry import DEFAULT_RETRIES


@dataclass(frozen=True)
class Job:
    name: str
    worker: int
    tries: int


def plan_jobs(names, *, workers=2, max_retries=DEFAULT_RETRIES):
    """One job per name, in name order, given to the workers in turn (worker
    1, 2, ..., then 1 again). A job may be tried ``max_retries + 1`` times."""
    if workers < 1:
        raise ValueError("workers must be 1 or more")
    if max_retries < 0:
        raise ValueError("max_retries must be 0 or more")
    return [Job(name, i % workers + 1, max_retries + 1) for i, name in enumerate(sorted(names))]
