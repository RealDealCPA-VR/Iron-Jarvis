import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from consolidate import consolidate  # noqa: E402


class ConsolidateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="consolidate-test-")
        self.exports = os.path.join(self.tmp, "exports")
        self.out = os.path.join(self.tmp, "out")
        os.makedirs(self.exports)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, name, text):
        with open(os.path.join(self.exports, name), "w", encoding="utf-8", newline="") as fh:
            fh.write(text)

    def read(self, name):
        with open(os.path.join(self.out, name), "rb") as fh:
            return fh.read()

    def test_two_simple_exports(self):
        self.write(
            "boston_2026-01.csv",
            'order_id,customer,amount,date\n'
            'BOS-000002,"Acme, Inc.",1250.5,2026-01-03\n'
            'BOS-000001,Bob\'s Diner,"1,020.00",2026-01-02\n',
        )
        self.write(
            "boston_2026-02.csv",
            "order_id,customer,amount,date\nBOS-000003,Corner Shop,-12,2026-02-03\n",
        )
        summary = consolidate(self.exports, self.out)
        self.assertEqual(
            self.read("orders.csv").decode("utf-8"),
            "order_id,office,customer,amount\n"
            "BOS-000001,boston,Bob's Diner,1020.00\n"
            'BOS-000002,boston,"Acme, Inc.",1250.50\n'
            "BOS-000003,boston,Corner Shop,-12.00\n",
        )
        expected = {"offices": {"boston": {"orders": 3, "total": "2258.50"}}, "skipped_rows": 0, "duplicates": 0}
        self.assertEqual(summary, expected)
        self.assertEqual(json.loads(self.read("summary.json").decode("utf-8")), expected)

    def test_bad_rows_and_duplicates(self):
        self.write(
            "boston_2026-03.csv",
            "order_id,customer,amount,date\n"
            "BOS-000010,Corner Shop,10.00,2026-03-01\n"
            "\n"
            "BOS-000011,Corner Shop,n/a,2026-03-02\n",
        )
        self.write(
            "boston_2026-03_resend.csv",
            "order_id,customer,amount,date\n"
            "BOS-000010,Corner Shop,10.00,2026-03-01\n"
            "BOS-000012,Corner Shop,5,2026-03-04\n",
        )
        summary = consolidate(self.exports, self.out)
        self.assertEqual(
            summary, {"offices": {"boston": {"orders": 2, "total": "15.00"}}, "skipped_rows": 1, "duplicates": 1}
        )
        self.assertEqual(
            self.read("orders.csv").decode("utf-8"),
            "order_id,office,customer,amount\nBOS-000010,boston,Corner Shop,10.00\nBOS-000012,boston,Corner Shop,5.00\n",
        )


if __name__ == "__main__":
    unittest.main()
