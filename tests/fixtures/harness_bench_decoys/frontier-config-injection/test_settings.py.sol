import dataclasses
import json
import os
import tempfile
import unittest

from shipping import settings
from shipping.settings import Settings, SettingsError


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, data):
        path = os.path.join(self.tmp.name, "settings.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_defaults(self):
        self.assertEqual(dataclasses.asdict(Settings()), settings.DEFAULTS)
        self.assertEqual(Settings().region, "US")
        self.assertEqual(Settings().units, "metric")

    def test_load_fills_in_defaults(self):
        loaded = settings.load(self.write({"region": "EU", "fuel_surcharge_pct": 12}))
        self.assertEqual(loaded, Settings(region="EU", fuel_surcharge_pct=12))
        self.assertEqual(dataclasses.asdict(loaded), {**settings.DEFAULTS, "region": "EU", "fuel_surcharge_pct": 12})

    def test_unknown_settings_are_refused(self):
        with self.assertRaises(SettingsError) as caught:
            settings.load(self.write({"colour": "blue"}))
        self.assertEqual(str(caught.exception), "unknown setting 'colour'")

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
                with self.assertRaises(SettingsError):
                    settings.load(self.write(values))
                with self.assertRaises(SettingsError):
                    Settings(**values)
        with self.assertRaises(SettingsError):
            settings.load(self.write("[1, 2]"))
        with self.assertRaises(SettingsError):
            settings.load(self.write("{not json"))

    def test_settings_are_immutable(self):
        loaded = settings.load(self.write({"currency": "GBP", "max_box_kg": 12.5}))
        self.assertEqual(loaded.max_box_kg, 12.5)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            loaded.currency = "USD"
        self.assertEqual(dataclasses.asdict(Settings()), settings.DEFAULTS)


if __name__ == "__main__":
    unittest.main()
