"""Exporting rows to text formats."""
import json

import storage


def export_rows(rows, fmt):
    """Render ``rows`` (a list of dicts) as ``fmt`` ("json" or "tsv")."""
    if fmt == "json":
        return json.dumps(rows, indent=2)
    if fmt == "tsv":
        keys = sorted({key for row in rows for key in row})
        lines = ["\t".join(keys)]
        lines += ["\t".join(str(row.get(key, "")) for key in keys) for row in rows]
        return "\n".join(lines)
    raise ValueError(f"unknown format {fmt!r}")


def handle_export(args):
    """The `export` command: print every stored row in the requested format."""
    fmt = args[0] if args else "json"
    print(export_rows(storage.load_rows(), fmt))
    return 0
