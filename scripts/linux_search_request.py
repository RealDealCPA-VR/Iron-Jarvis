"""One search request for the frozen ``search-worker``, as the app sends it.

    python3 scripts/linux_search_request.py FOLDER > req.json
    ./ironjarvis search-worker < req.json

The Linux release workflow uses it to prove the frozen app's search program
runs on Linux: the pattern has a repeat, so it is one the app hands to that
program rather than matching itself. Standard library only.
"""

from __future__ import annotations

import json
import sys


def request(folder: str) -> dict:
    return {
        "kind": "grep",
        "base": folder,
        "root": folder,
        "opts": {
            "pattern": "need(le)+ h", "output_mode": "content", "before": 0, "after": 0,
            "case_insensitive": False, "glob": None, "file_type": None, "head_limit": 250,
            "offset": 0, "multiline": False, "line_numbers": True,
        },
        "limits": {"deadline_s": 20, "max_files": 5000, "max_file_bytes": 2000000},
    }


if __name__ == "__main__":
    print(json.dumps(request(sys.argv[1])))
