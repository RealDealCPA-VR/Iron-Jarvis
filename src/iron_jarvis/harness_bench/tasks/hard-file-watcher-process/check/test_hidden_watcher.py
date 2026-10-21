# IJ-BENCH-HIDDEN-CHECK hard-file-watcher-process
import hashlib
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import watcher  # noqa: E402

EXPECTED = {
    "a.csv": {"rows": 4, "total": "27.50", "bad_rows": 1},
    "b.csv": {"rows": 5, "total": "1001.10", "bad_rows": 1},
    "c.csv": {"rows": 3, "total": "140.00", "bad_rows": 0},
}
CRLF = bytes([13, 10])
LF = bytes([10])
UNCHANGED = {
    "WATCHER.md": "41a31816e47fdcdf397e3fda6738df3b06828f329f0dc37ced26781d239b97fa",
    "incoming/a.csv": "fdd324c036e39df7b88576ca425fc9aa08fb40029b4e2016a8a41bb79902e4e5",
    "incoming/b.csv": "7f9143d9cdfbfe3b686a63604dd71ce54fa0fe86ae459906e3375702e77a7d60",
    "incoming/c.csv": "2906702fbea7397c1c172daa82d32a86c67d87c9fb52a3aa8aba11735255278f",
}


def _bytes(*parts):
    with open(os.path.join(HERE, *parts), "rb") as fh:
        return fh.read().replace(CRLF, LF)


def _lines(path):
    with open(path, encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip()]


class HiddenWatcher(unittest.TestCase):
    def test_the_last_run_processed_everything_and_stopped(self):
        lines = _lines(os.path.join(HERE, "watcher.log"))
        starts = [i for i, line in enumerate(lines) if line.startswith("started ")]
        self.assertTrue(starts, "the watcher never started")
        run = lines[starts[-1]].split(" ", 1)[1]
        after = lines[starts[-1] + 1:]
        self.assertEqual(after[-1:], ["stopped"])
        self.assertEqual(sorted(after[:-1]), [f"processed {n}" for n in sorted(EXPECTED)])
        with open(os.path.join(HERE, "results.json"), encoding="utf-8") as fh:
            results = json.load(fh)
        self.assertEqual(results["run"], run)
        self.assertEqual(results["files"], EXPECTED)

    def test_the_files_went_through_the_folders(self):
        for name in EXPECTED:
            self.assertEqual(_bytes("done", name), _bytes("incoming", name), name)
        self.assertEqual(sorted(os.listdir(os.path.join(HERE, "inbox"))), ["README.txt"])

    def test_the_inputs_were_not_changed(self):
        for name, digest in UNCHANGED.items():
            with self.subTest(name=name):
                data = _bytes(*name.split("/"))
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest)

    def test_total_file(self):
        folder = tempfile.mkdtemp(prefix="ij-watch-")
        try:
            path = os.path.join(folder, "t.csv")
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write("amount,x\n0.10,a\n-0.10,b\n  7 ,c\nabc,d\n,e\n  -5,f\n")
            self.assertEqual(watcher.total_file(path), {"rows": 6, "total": "2.00", "bad_rows": 2})
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write("x,amount\na,1234567890123456.78\nb,0.01\nc,0.2\n")
            self.assertEqual(
                watcher.total_file(path), {"rows": 3, "total": "1234567890123456.99", "bad_rows": 0}
            )
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write("amount\n-3\n")
            self.assertEqual(watcher.total_file(path), {"rows": 1, "total": "-3.00", "bad_rows": 0})
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write("amount\n")
            self.assertEqual(watcher.total_file(path), {"rows": 0, "total": "0.00", "bad_rows": 0})
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_a_stop_that_arrives_with_files_finishes_them_first(self):
        folder = tempfile.mkdtemp(prefix="ij-watch-")
        try:
            os.makedirs(os.path.join(folder, "inbox"))
            os.makedirs(os.path.join(folder, "done"))
            with open(os.path.join(folder, "inbox", "x.csv"), "w", encoding="utf-8") as fh:
                fh.write("amount\n1.25\n2\n")
            with open(os.path.join(folder, "inbox", "STOP"), "w", encoding="utf-8") as fh:
                fh.write("")
            worker = threading.Thread(target=watcher.main, args=(folder,), daemon=True)
            worker.start()
            worker.join(timeout=30)
            self.assertFalse(worker.is_alive(), "the watcher did not stop")
            self.assertTrue(os.path.exists(os.path.join(folder, "done", "x.csv")))
            self.assertFalse(os.path.exists(os.path.join(folder, "inbox", "STOP")))
            with open(os.path.join(folder, "results.json"), encoding="utf-8") as fh:
                results = json.load(fh)
            self.assertEqual(results["files"], {"x.csv": {"rows": 2, "total": "3.25", "bad_rows": 0}})
            log = _lines(os.path.join(folder, "watcher.log"))
            self.assertEqual(log[-2:], ["processed x.csv", "stopped"])
        finally:
            shutil.rmtree(folder, ignore_errors=True)
