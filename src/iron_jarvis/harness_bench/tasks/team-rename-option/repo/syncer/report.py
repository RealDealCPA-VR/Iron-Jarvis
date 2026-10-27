"""The summary printed after a sync."""


def summary(results, *, max_retries):
    """The summary of a sync: ``results`` maps each file name to "ok" or
    "failed". Three lines: how many synced, how many failed (with their names),
    and the retry setting the sync used."""
    ok = sorted(name for name, state in results.items() if state == "ok")
    failed = sorted(name for name, state in results.items() if state != "ok")
    lines = [f"synced: {len(ok)}", f"failed: {len(failed)}" + (f" ({', '.join(failed)})" if failed else "")]
    lines.append(f"max retries: {max_retries}")
    return "\n".join(lines)
