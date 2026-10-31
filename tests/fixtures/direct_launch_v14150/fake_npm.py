"""A stand-in for ``npm`` (v1.415.0 review): answers the read-only freshness
check ``npm pack --dry-run --json --prefer-online <spec>`` the way npm does —
one JSON list with the package npm would pick — and writes nothing but its
log line (``FAKE_NPM_LOG``: argv, cwd and ``FAKE_NPM_MARK``)."""

import json
import os
import sys

log = os.environ.get("FAKE_NPM_LOG")
if log:
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(),
                             "mark": os.environ.get("FAKE_NPM_MARK", "")}) + "\n")
if os.environ.get("FAKE_NPM_FAIL"):
    print("npm error network request failed", file=sys.stderr)
    sys.exit(1)
print(json.dumps([{"id": "x", "name": os.environ["FAKE_NPM_NAME"],
                   "version": os.environ["FAKE_NPM_VERSION"], "files": []}], indent=2))
