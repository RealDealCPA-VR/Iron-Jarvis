import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


class BuildTest(unittest.TestCase):
    def test_the_report_builds(self):
        result = subprocess.run(
            [sys.executable, "build.py"], cwd=HERE, capture_output=True, text=True, timeout=120
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        path = os.path.join(HERE, "dist", "report.html")
        self.assertTrue(os.path.isfile(path), "build.py did not write dist/report.html")
        with open(path, encoding="utf-8") as fh:
            report = fh.read()
        self.assertIn("<h1>Quarterly Report — Q3 2026</h1>", report)
        self.assertIn("<h2>Overview</h2>", report)


if __name__ == "__main__":
    unittest.main()
