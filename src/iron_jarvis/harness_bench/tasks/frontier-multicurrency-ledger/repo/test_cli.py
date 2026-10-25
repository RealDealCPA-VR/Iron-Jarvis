import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

CSV = """date,account,amount,description
2024-01-05,Assets:Bank,1000.00,opening balance
2024-01-06,Expenses:Food,-12.00,lunch
"""


def run_cli(*args):
    env = dict(os.environ)
    env.pop("PYTHONSAFEPATH", None)
    env["PYTHONPATH"] = HERE
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "ledger.cli", *args],
        cwd=HERE, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.csv = os.path.join(self.tmp.name, "transactions.csv")
        with open(self.csv, "w", encoding="utf-8", newline="") as fh:
            fh.write(CSV)

    def tearDown(self):
        self.tmp.cleanup()

    def test_report(self):
        done = run_cli("report", self.csv)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            "Ledger report\n=============\n\n"
            "Assets:Bank    USD  1,000.00\n"
            "Expenses:Food  USD    -12.00\n"
            "----------------------------\n"
            "Total          USD    988.00\n",
        )

    def test_balance(self):
        done = run_cli("balance", self.csv, "Assets:Bank")
        self.assertEqual((done.returncode, done.stdout), (0, "USD 1,000.00\n"))

    def test_accounts(self):
        done = run_cli("accounts", self.csv)
        self.assertEqual((done.returncode, done.stdout), (0, "Assets:Bank\nExpenses:Food\n"))

    def test_errors_exit_with_1(self):
        bad = os.path.join(self.tmp.name, "bad.csv")
        with open(bad, "w", encoding="utf-8", newline="") as fh:
            fh.write("date,account,amount\n2024-01-05,Cash,1.234\n")
        done = run_cli("report", bad)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertEqual(done.stderr, "error: line 2: bad amount '1.234'\n")

        done = run_cli("balance", self.csv, "Nope")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stderr, "error: no such account 'Nope'\n")

        done = run_cli("report", os.path.join(self.tmp.name, "missing.csv"))
        self.assertEqual(done.returncode, 1)
        self.assertTrue(done.stderr.startswith("error: "), done.stderr)


if __name__ == "__main__":
    unittest.main()
