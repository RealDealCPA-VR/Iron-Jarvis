# IJ-BENCH-HIDDEN-CHECK feature-settings-file
import json
import os
import tempfile
import unittest

import settings

ORIGINAL = {"theme": "light", "font_size": 12, "autosave": True, "recent_limit": 10}


def _file(folder, data):
    path = os.path.join(folder, "settings.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    return path


class HiddenSettings(unittest.TestCase):
    def test_missing_file_is_the_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            got = settings.load_settings(os.path.join(folder, "nope.json"))
        self.assertEqual(got, ORIGINAL)
        self.assertIsNot(got, settings.DEFAULTS)

    def test_values_from_the_file_win(self):
        with tempfile.TemporaryDirectory() as folder:
            got = settings.load_settings(_file(folder, {"font_size": 14, "theme": "dark"}))
        self.assertEqual(got["font_size"], 14)
        self.assertEqual(got["theme"], "dark")
        self.assertEqual(got["autosave"], True)

    def test_unknown_key_is_named(self):
        with tempfile.TemporaryDirectory() as folder:
            path = _file(folder, {"colour_scheme": "x"})
            with self.assertRaises(ValueError) as caught:
                settings.load_settings(path)
        self.assertIn("colour_scheme", str(caught.exception))

    def test_wrong_types(self):
        for bad in ({"font_size": "14"}, {"font_size": True}, {"autosave": 1}, {"theme": 3}):
            with self.subTest(bad=bad):
                with tempfile.TemporaryDirectory() as folder:
                    path = _file(folder, bad)
                    with self.assertRaises(ValueError):
                        settings.load_settings(path)

    def test_defaults_never_change(self):
        with tempfile.TemporaryDirectory() as folder:
            got = settings.load_settings(_file(folder, {"recent_limit": 3}))
            got["theme"] = "changed"
            again = settings.load_settings(os.path.join(folder, "missing.json"))
        self.assertEqual(settings.DEFAULTS, ORIGINAL)
        self.assertEqual(again, ORIGINAL)
