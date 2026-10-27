"""The deprecated names warn (RENAME.md). Runs before most other tests, so the
process has already warned about every old name by the time they run."""

import contextlib
import io
import unittest
import warnings

from syncer import Remote, load_config, with_retries
from syncer.cli import main
from test_remote import FlakyTransport
from test_retry import Flaky


class DeprecationsTest(unittest.TestCase):
    def test_every_old_name_still_works(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            self.assertEqual(with_retries(Flaky(1), max_retries=1), "done")
            self.assertEqual(Remote("mem://x", FlakyTransport(0), retry_limit=2).max_retries, 2)
            self.assertEqual(load_config(environ={"SYNCER_MAX_RETRIES": "4"}).retry_limit, 4)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(["config", "--max-retries", "2"], environ={}), 0)


if __name__ == "__main__":
    unittest.main()
