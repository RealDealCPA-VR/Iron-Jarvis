import json
import os
import tempfile
import unittest

from syncer import ConfigError, load_config


class ConfigTest(unittest.TestCase):
    def write(self, data):
        handle, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.addCleanup(os.remove, path)
        return path

    def test_defaults(self):
        config = load_config(environ={})
        self.assertEqual((config.remote, config.max_retries, config.workers), ("", 3, 2))

    def test_file(self):
        config = load_config(self.write({"remote": "r", "max_retries": 5}), environ={})
        self.assertEqual((config.remote, config.max_retries), ("r", 5))

    def test_environment_wins_over_the_file(self):
        path = self.write({"max_retries": 5})
        self.assertEqual(load_config(path, environ={"SYNCER_MAX_RETRIES": "7"}).max_retries, 7)

    def test_unknown_key(self):
        with self.assertRaises(ConfigError):
            load_config(self.write({"retries": 5}), environ={})

    def test_wrong_type(self):
        with self.assertRaises(ConfigError):
            load_config(self.write({"workers": "many"}), environ={})


if __name__ == "__main__":
    unittest.main()
