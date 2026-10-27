import unittest

from orderdesk.customers import parse_customer


class CustomerTest(unittest.TestCase):
    def test_parse(self):
        c = parse_customer({"id": " c-1 ", "name": "Ada", "email": "ada@example.com"})
        self.assertEqual((c.id, c.name, c.email), ("c-1", "Ada", "ada@example.com"))

    def test_missing_field(self):
        with self.assertRaises(ValueError):
            parse_customer({"id": "c-1", "name": "Ada"})

    def test_bad_email(self):
        with self.assertRaises(ValueError):
            parse_customer({"id": "c-1", "name": "Ada", "email": "nowhere"})


if __name__ == "__main__":
    unittest.main()
