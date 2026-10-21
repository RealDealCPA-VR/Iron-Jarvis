import json
import os
import unittest

from reporter import exporters
from reporter.model import Report

HERE = os.path.dirname(os.path.abspath(__file__))


def sample():
    with open(os.path.join(HERE, "samples", "sales.json"), encoding="utf-8") as fh:
        return Report.from_dict(json.load(fh))


class ExporterTests(unittest.TestCase):
    def test_csv(self):
        text = exporters.get("csv")(sample())
        self.assertTrue(text.startswith("Region,Units,Revenue,On target,Note\n"))
        self.assertIn('North,"1,200","15,000.50",yes,best | ever\n', text)

    def test_json_keeps_raw_values(self):
        data = json.loads(exporters.get("json")(sample()))
        self.assertEqual(data["rows"][0][1], 1200)

    def test_html_marks_numeric_columns(self):
        text = exporters.get("htm")(sample())
        self.assertIn('<td class="num">1,200</td>', text)
        self.assertIn("<td>yes</td>", text)

    def test_unknown_format(self):
        with self.assertRaises(ValueError):
            exporters.get("pdf")

    def test_list(self):
        self.assertEqual(exporters.names(), sorted(exporters.names()))
        self.assertIn("csv", exporters.names())


if __name__ == "__main__":
    unittest.main()
