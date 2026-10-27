"""First tests of the rename in RENAME.md (they fail until it is done)."""

import contextlib
import io
import unittest

from syncer import Remote, with_retries
from syncer.cli import main
from test_remote import FlakyTransport
from test_retry import Flaky


class NewNamesTest(unittest.TestCase):
    def test_the_keyword(self):
        fn = Flaky(1)
        self.assertEqual(with_retries(fn, retry_limit=1), "done")

    def test_the_attribute(self):
        self.assertEqual(Remote("mem://x", FlakyTransport(0), retry_limit=4).retry_limit, 4)

    def test_the_flag_and_the_printed_line(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["config", "--retry-limit", "6"], environ={})
        self.assertEqual(code, 0)
        self.assertIn("retry limit: 6", out.getvalue())


if __name__ == "__main__":
    unittest.main()
