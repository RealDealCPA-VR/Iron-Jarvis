"""Watch inbox/ for CSV files and total them (see WATCHER.md).

Start it from this folder: ``python watcher.py``. Stop it by creating
``inbox/STOP``.
"""

import csv
import json
import os
import secrets
import sys
import time


def total_file(path):
    """The entry for one CSV file."""
    rows = 0
    total = 0
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows += 1
            total += float(row["amount"])
    return {"rows": rows, "total": str(round(total, 2)), "bad_rows": 0}


def _log(folder, line):
    with open(os.path.join(folder, "watcher.log"), "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()


def _save(folder, results):
    tmp = os.path.join(folder, "results.json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2, sort_keys=True)
    os.replace(tmp, os.path.join(folder, "results.json"))


def _process_inbox(folder, results):
    inbox = os.path.join(folder, "inbox")
    done = os.path.join(folder, "done")
    for name in sorted(os.listdir(inbox)):
        if not name.endswith(".csv"):
            continue
        path = os.path.join(inbox, name)
        results["files"][name] = total_file(path)
        os.replace(path, os.path.join(done, name))
        _save(folder, results)
        _log(folder, f"processed {name}")


def main(folder):
    os.makedirs(os.path.join(folder, "inbox"), exist_ok=True)
    os.makedirs(os.path.join(folder, "done"), exist_ok=True)
    run = secrets.token_hex(8)
    results = {"run": run, "files": {}}
    _save(folder, results)
    _log(folder, f"started {run}")
    stop = os.path.join(folder, "inbox", "STOP")
    while True:
        if os.path.exists(stop):
            os.remove(stop)
            _log(folder, "stopped")
            return 0
        _process_inbox(folder, results)
        time.sleep(0.2)


if __name__ == "__main__":
    sys.exit(main(os.path.dirname(os.path.abspath(__file__))))
