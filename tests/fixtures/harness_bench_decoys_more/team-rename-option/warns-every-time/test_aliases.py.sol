"""The deprecated names of 2.4.0 (RENAME.md): they still work and warn once."""

import contextlib
import io
import unittest
import warnings

from syncer import ConfigError, Remote, _deprecated, load_config, with_retries
from syncer.cli import main
from test_remote import FlakyTransport
from test_retry import Flaky


class AliasTest(unittest.TestCase):
    def setUp(self):
        saved = set(_deprecated._warned)
        _deprecated._warned.clear()
        self.addCleanup(lambda: (_deprecated._warned.clear(), _deprecated._warned.update(saved)))

    def caught(self, fn):
        with warnings.catch_warnings(record=True) as seen:
            warnings.simplefilter("always")
            fn()
        return [w for w in seen if issubclass(w.category, DeprecationWarning)]

    def test_keyword(self):
        self.assertEqual(len(self.caught(lambda: with_retries(Flaky(1), max_retries=1))), 1)

    def test_attribute(self):
        remote = Remote("mem://x", FlakyTransport(0), retry_limit=2)
        self.assertEqual(len(self.caught(lambda: self.assertEqual(remote.max_retries, 2))), 1)

    def test_environment(self):
        env = {"SYNCER_MAX_RETRIES": "4"}
        self.assertEqual(len(self.caught(lambda: self.assertEqual(load_config(environ=env).retry_limit, 4))), 1)

    def test_flag(self):
        def run():
            with contextlib.redirect_stdout(io.StringIO()):
                main(["config", "--max-retries", "2"], environ={})
        self.assertEqual(len(self.caught(run)), 1)

    def test_old_and_new_together(self):
        with self.assertRaises(TypeError):
            with_retries(Flaky(0), max_retries=1, retry_limit=1)
        with self.assertRaises(ConfigError):
            load_config(environ={"SYNCER_MAX_RETRIES": "1", "SYNCER_RETRY_LIMIT": "1"})


if __name__ == "__main__":
    unittest.main()
