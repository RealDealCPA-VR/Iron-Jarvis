import unittest

from migrate import migrate_record


class MigrateTests(unittest.TestCase):
    def test_a_plain_record(self):
        got = migrate_record(
            {"name": "Ada Lovelace", "joined": "1815-12-10", "phone": "5550102000", "tags": "vip"}
        )
        self.assertEqual(
            got,
            {
                "version": 2,
                "first_name": "Ada",
                "last_name": "Lovelace",
                "joined": "1815-12-10",
                "phone": "+15550102000",
                "tags": ["vip"],
            },
        )

    def test_a_record_that_cannot_be_migrated(self):
        with self.assertRaises(ValueError):
            migrate_record({"name": "", "joined": "1815-12-10"})


if __name__ == "__main__":
    unittest.main()
