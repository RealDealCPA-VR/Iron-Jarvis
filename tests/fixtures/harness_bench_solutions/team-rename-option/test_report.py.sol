import unittest

from syncer import summary


class ReportTest(unittest.TestCase):
    def test_all_synced(self):
        text = summary({"a": "ok", "b": "ok"}, retry_limit=3)
        self.assertEqual(text.splitlines()[:2], ["synced: 2", "failed: 0"])

    def test_names_the_failures(self):
        text = summary({"a": "ok", "c": "failed", "b": "failed"}, retry_limit=3)
        self.assertIn("failed: 2 (b, c)", text)


if __name__ == "__main__":
    unittest.main()
