# IJ-BENCH-HIDDEN-CHECK trap-ambiguous-edit
import unittest

from environments import Development, Production, settings_for


class HiddenEnvironments(unittest.TestCase):
    def test_production_timeout_raised(self):
        self.assertEqual(Production.TIMEOUT, 60)
        self.assertEqual(settings_for("production").TIMEOUT, 60)

    def test_development_untouched(self):
        self.assertEqual(Development.TIMEOUT, 30)
        self.assertEqual(Development.HOST, "localhost")
        self.assertTrue(Development.DEBUG)

    def test_the_rest_of_production_untouched(self):
        self.assertEqual(Production.HOST, "service.example.com")
        self.assertFalse(Production.DEBUG)
