import contextlib
import io
import json
import os
import tempfile
import unittest

from orderdesk.cli import main

ORDER = {
    "customer": {"id": "c-17", "name": "Ada Byron", "email": "ada@example.com"},
    "lines": [{"name": "Notebook", "price_cents": 1250, "qty": 2}],
}


class CliTest(unittest.TestCase):
    def run_cli(self, order, *args):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "order.json")
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(order, fh)
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = main([path, *args])
            return code, out.getvalue(), err.getvalue()

    def test_prints_the_invoice(self):
        code, out, _ = self.run_cli(ORDER)
        self.assertEqual(code, 0)
        self.assertIn("Total: $30.99", out)

    def test_coupon_option(self):
        code, out, _ = self.run_cli(ORDER, "--coupon", "save20")
        self.assertEqual(code, 0)
        self.assertIn("Discount (20%): -$5.00", out)

    def test_express_option(self):
        code, out, _ = self.run_cli(ORDER, "--express")
        self.assertEqual(code, 0)
        self.assertIn("Shipping (express): $12.99", out)
        self.assertIn("Total: $37.99", out)

    def test_output_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = os.path.join(tmp, "invoice.txt")
            code, out, _ = self.run_cli(ORDER, "--output", target)
            self.assertEqual(code, 0)
            self.assertEqual(out.strip(), f"wrote {target}")
            with open(target, encoding="utf-8") as fh:
                self.assertIn("Total: $30.99", fh.read())

    def test_a_bad_order(self):
        code, _, err = self.run_cli({"lines": []})
        self.assertEqual(code, 2)
        self.assertIn("error:", err)


if __name__ == "__main__":
    unittest.main()
