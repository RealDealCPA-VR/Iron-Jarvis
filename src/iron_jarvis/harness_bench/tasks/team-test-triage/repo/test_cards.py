import unittest

from payments.cards import card_brand, digits_of, luhn_ok, masked


class LuhnTest(unittest.TestCase):
    def test_a_valid_visa(self):
        self.assertTrue(luhn_ok("4111 1111 1111 1111"))

    def test_a_typo_is_caught(self):
        self.assertFalse(luhn_ok("4111 1111 1111 1112"))

    def test_a_valid_amex(self):
        self.assertTrue(luhn_ok("3782-822463-10005"))

    def test_not_a_number(self):
        self.assertFalse(luhn_ok("4111 1111 1111 111a"))
        self.assertFalse(luhn_ok("4242 4242"))


class CardTest(unittest.TestCase):
    def test_digits(self):
        self.assertEqual(digits_of("4111-1111 1111-1111"), "4111111111111111")
        with self.assertRaises(ValueError):
            digits_of("4111 1111 1111 111a")

    def test_brand(self):
        self.assertEqual(card_brand("4111 1111 1111 1111"), "visa")
        self.assertEqual(card_brand("3782 822463 10005"), "amex")
        self.assertEqual(card_brand("5555 5555 5555 4444"), "mastercard")
        self.assertEqual(card_brand("9999 9999 9999 9995"), "unknown")

    def test_masked(self):
        self.assertEqual(masked("4111 1111 1111 1111"), "•••• 1111")


if __name__ == "__main__":
    unittest.main()
