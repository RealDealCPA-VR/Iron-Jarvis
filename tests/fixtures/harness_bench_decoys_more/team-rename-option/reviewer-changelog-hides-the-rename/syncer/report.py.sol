"""The summary printed after a sync."""

from . import _deprecated


def summary(results, *, retry_limit=_deprecated.MISSING, max_retries=_deprecated.MISSING):
    """The summary of a sync: ``results`` maps each file name to "ok" or
    "failed". Three lines: how many synced, how many failed (with their names),
    and the retry limit the sync used. ``max_retries`` is the deprecated old
    name of ``retry_limit``."""
    limit = _deprecated.retry_limit(retry_limit, max_retries, _deprecated.MISSING)
    if limit is _deprecated.MISSING:
        raise TypeError("summary() needs retry_limit")
    ok = sorted(name for name, state in results.items() if state == "ok")
    failed = sorted(name for name, state in results.items() if state != "ok")
    lines = [f"synced: {len(ok)}", f"failed: {len(failed)}" + (f" ({', '.join(failed)})" if failed else "")]
    lines.append(f"retry limit: {limit}")
    return "\n".join(lines)
