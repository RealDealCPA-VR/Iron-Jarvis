"""The summary printed after a sync."""

from ._deprecation import _UNSET, keyword


def summary(results, *, retry_limit=_UNSET, max_retries=_UNSET):
    """The summary of a sync: ``results`` maps each file name to "ok" or
    "failed". Three lines: how many synced, how many failed (with their names),
    and the retry limit the sync used. ``max_retries`` is a deprecated alias
    of ``retry_limit``."""
    if retry_limit is _UNSET and max_retries is _UNSET:
        raise TypeError("summary() missing required keyword-only argument: 'retry_limit'")
    retry_limit = keyword(retry_limit, max_retries, None)
    ok = sorted(name for name, state in results.items() if state == "ok")
    failed = sorted(name for name, state in results.items() if state != "ok")
    lines = [f"synced: {len(ok)}", f"failed: {len(failed)}" + (f" ({', '.join(failed)})" if failed else "")]
    lines.append(f"retry limit: {retry_limit}")
    return "\n".join(lines)
