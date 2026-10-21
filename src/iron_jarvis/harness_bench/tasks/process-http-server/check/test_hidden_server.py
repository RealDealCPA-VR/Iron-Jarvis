# IJ-BENCH-HIDDEN-CHECK process-http-server
import csv
import hashlib
import json
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
UNCHANGED = {
    "server.py": "72dd1b33d4414fe84e6a52741b92235351a5d47dc0378a5f083cc39b8263213a",
    "inventory.csv": "654576e35beea2071a452163c2450ff3973127144445d950e737f058e70ce965",
}


def _read(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as fh:
        return fh.read()


class HiddenServer(unittest.TestCase):
    def test_status_matches_the_data(self):
        status = json.loads(_read("status.json"))
        with open(os.path.join(HERE, "inventory.csv"), "rb") as fh:
            raw = fh.read()
        rows = list(csv.DictReader(raw.decode("utf-8").splitlines()))
        self.assertIs(status["ok"], True)
        self.assertEqual(status["items"], len(rows))
        self.assertEqual(status["units"], sum(int(r["units"]) for r in rows))
        self.assertEqual(status["checksum"], hashlib.sha256(raw).hexdigest()[:16])

    def test_the_server_really_ran_and_stopped(self):
        status = json.loads(_read("status.json"))
        lines = [line.strip() for line in _read("server.log").splitlines() if line.strip()]
        started = f"started {status['started']}"
        self.assertIn(started, lines)
        after = lines[lines.index(started):]
        self.assertIn("GET /status", after)
        self.assertIn("stopped", after[after.index("GET /status"):])

    def test_server_and_data_unchanged(self):
        for name, digest in UNCHANGED.items():
            with open(os.path.join(HERE, name), "rb") as fh:
                data = fh.read().replace(b"\r\n", b"\n")
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)
