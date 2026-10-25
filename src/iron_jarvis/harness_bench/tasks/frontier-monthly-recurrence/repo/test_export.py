import unittest
from datetime import date

from recur import export_monthly, export_series


class ExportTests(unittest.TestCase):
    def test_monthly_series(self):
        text = export_monthly("Gym fee", date(2025, 3, 15), 2)
        self.assertEqual(
            text.split("\r\n"),
            [
                "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//recur//EN",
                "BEGIN:VEVENT", "UID:gym-fee-1@recur", "DTSTART;VALUE=DATE:20250315",
                "SUMMARY:Gym fee", "END:VEVENT",
                "BEGIN:VEVENT", "UID:gym-fee-2@recur", "DTSTART;VALUE=DATE:20250415",
                "SUMMARY:Gym fee", "END:VEVENT",
                "END:VCALENDAR", "",
            ],
        )

    def test_summary_is_escaped(self):
        text = export_series("Rent; flat 2, rear", [date(2025, 5, 1)])
        self.assertIn("SUMMARY:Rent\\; flat 2\\, rear\r\n", text)
        self.assertIn("UID:rent-flat-2-rear-1@recur\r\n", text)

    def test_dates_must_be_in_order(self):
        with self.assertRaises(ValueError):
            export_series("x", [date(2025, 2, 1), date(2025, 1, 1)])
        with self.assertRaises(ValueError):
            export_series("x", [date(2025, 2, 1), date(2025, 2, 1)])


if __name__ == "__main__":
    unittest.main()
