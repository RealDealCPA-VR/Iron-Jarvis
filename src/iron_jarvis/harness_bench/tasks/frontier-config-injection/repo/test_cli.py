import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

ORDER = {
    "recipient": "Ada Lovelace",
    "postcode": "10115",
    "items": [
        {"name": "book", "dims_cm": [24, 17, 3], "weight": "0.9 kg"},
        {"name": "mug", "dims_cm": [12, 9, 7], "weight": 0.4},
    ],
}


def run_cli(*args):
    env = dict(os.environ)
    env.pop("PYTHONSAFEPATH", None)
    env["PYTHONPATH"] = HERE
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "shipping.cli", *args],
        cwd=HERE, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.order = self.write("order.json", ORDER)

    def write(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return path

    def test_quote(self):
        done = run_cli("quote", self.order)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            "Quotes for Ada Lovelace (10115, zone 8):\n"
            "  Ground   standard   USD 12.50  9 days\n"
            "  Express  two-day    USD 22.50  2 days\n"
            "\n"
            "Hello Ada Lovelace, Iron Shipping Co. has sent your parcel (1.70 kg) with Ground"
            " (standard). It cost USD 12.50 and should arrive in 9 days.\n",
        )

    def test_label_with_settings_and_units(self):
        path = self.write("eu.json", {"region": "EU", "sender": "Paket Nord GmbH", "padding_cm": 4})
        done = run_cli("label", self.order, "--settings", path, "--units", "imperial")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(
            done.stdout,
            "FROM:   Paket Nord GmbH\n"
            "TO:     Ada Lovelace\n"
            "ZONE:   1 (10115, EU)\n"
            "BOX:    L, 19.7 x 15.7 x 11.8 in\n"
            "WEIGHT: 4.63 lb\n",
        )

    def test_bad_settings(self):
        path = self.write("bad.json", {"region": "Mars"})
        done = run_cli("quote", self.order, "--settings", path)
        self.assertEqual(done.returncode, 1)
        self.assertEqual(done.stdout, "")
        self.assertTrue(done.stderr.startswith("error: "), done.stderr)


if __name__ == "__main__":
    unittest.main()
