"""The summary printed after a sync."""


def summary(results, *, retry_limit):
    """The summary of a sync: ``results`` maps each file name to "ok" or
    "failed". Three lines: how many synced, how many failed (with their names),
    and the retry setting the sync used."""
    ok = sorted(name for name, state in results.items() if state == "ok")
    failed = sorted(name for name, state in results.items() if state != "ok")
    lines = [f"synced: {len(ok)}", f"failed: {len(failed)}" + (f" ({', '.join(failed)})" if failed else "")]
    lines.append(f"retry limit: {retry_limit}")
    return "\n".join(lines)
