import json
import os
import tempfile
import unittest

from shipping import settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        settings.reset()
        self.addCleanup(settings.reset)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, data):
        path = os.path.join(self.tmp.name, "settings.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_defaults(self):
        self.assertEqual(settings.CONFIG, settings.DEFAULTS)
        self.assertEqual(settings.CONFIG["region"], "US")
        self.assertEqual(settings.CONFIG["units"], "metric")

    def test_load_config_fills_in_defaults(self):
        settings.load_config(self.write({"region": "EU", "fuel_surcharge_pct": 12}))
        self.assertEqual(settings.CONFIG, {**settings.DEFAULTS, "region": "EU", "fuel_surcharge_pct": 12})

    def test_unknown_settings_are_refused(self):
        with self.assertRaises(settings.SettingsError) as caught:
            settings.load_config(self.write({"colour": "blue"}))
        self.assertEqual(str(caught.exception), "unknown setting 'colour'")
        self.assertEqual(settings.CONFIG, settings.DEFAULTS)

    def test_bad_values_are_refused(self):
        bad = [
            {"region": "XX"}, {"units": "IMPERIAL"}, {"currency": "JPY"},
            {"fuel_surcharge_pct": 51}, {"fuel_surcharge_pct": True}, {"fuel_surcharge_pct": 1.5},
            {"max_box_kg": 0}, {"max_box_kg": 71}, {"max_box_kg": "20"},
            {"padding_cm": -1}, {"padding_cm": 11},
            {"sender": "   "}, {"sender": "x" * 41}, {"sender": 5},
        ]
        for values in bad:
            with self.subTest(values=values):
                with self.assertRaises(settings.SettingsError):
                    settings.load_config(self.write(values))
        with self.assertRaises(settings.SettingsError):
            settings.load_config(self.write("[1, 2]"))
        with self.assertRaises(settings.SettingsError):
            settings.load_config(self.write("{not json"))

    def test_reset_puts_the_defaults_back(self):
        settings.load_config(self.write({"currency": "GBP", "max_box_kg": 12.5}))
        self.assertEqual(settings.CONFIG["max_box_kg"], 12.5)
        settings.reset()
        self.assertEqual(settings.CONFIG, settings.DEFAULTS)


if __name__ == "__main__":
    unittest.main()
