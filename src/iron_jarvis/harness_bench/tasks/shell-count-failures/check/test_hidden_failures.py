# IJ-BENCH-HIDDEN-CHECK shell-count-failures
import hashlib
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
EXPECTED_FAILURES = "8"
UNCHANGED = {
    "phone.py": "f74f80a2ca4493349097d5a36d5e2348adb01b834207bdc1e6d77881e7f13815",
    "test_phone.py": "91d2b20bcf7a2c90ecd1033c8ca366e845496b29e898c730c53986eacca2673e",
}


class HiddenFailureCount(unittest.TestCase):
    def test_the_count(self):
        with open(os.path.join(HERE, "failures.txt"), encoding="utf-8") as fh:
            self.assertEqual(fh.read().strip(), EXPECTED_FAILURES)

    def test_nothing_was_changed(self):
        for name, digest in UNCHANGED.items():
            with open(os.path.join(HERE, name), "rb") as fh:
                data = fh.read().replace(b"\r\n", b"\n")
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, name)
