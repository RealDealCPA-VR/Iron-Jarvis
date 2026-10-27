import os
import tempfile
import unittest

from inventory.importer import read_stock_csv


class ImporterTest(unittest.TestCase):
    def read(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "count.csv")
            with open(path, "wb") as fh:
                fh.write(data)
            return read_stock_csv(path)

    def test_a_plain_file(self):
        self.assertEqual(self.read(b"sku,qty\nab-1,3\ncd-2,0\n"), {"AB-1": 3, "CD-2": 0})

    def test_a_file_saved_by_excel(self):
        self.assertEqual(self.read(b"\xef\xbb\xbfsku,qty\r\nab-1,3\r\nEF-3,12\r\n"), {"AB-1": 3, "EF-3": 12})

    def test_a_sku_twice(self):
        with self.assertRaises(ValueError):
            self.read(b"sku,qty\nab-1,3\nAB-1,4\n")


if __name__ == "__main__":
    unittest.main()
