import unittest

from calc import CalcError, evaluate


class CalcTests(unittest.TestCase):
    def test_precedence(self):
        self.assertEqual(evaluate("1 + 2 * 3"), 7)
        self.assertEqual(evaluate("(1 + 2) * 3"), 9)

    def test_power_is_right_associative(self):
        self.assertEqual(evaluate("2 ** 3 ** 2"), 512)

    def test_a_variable(self):
        self.assertEqual(evaluate("price * qty", {"price": 2.5, "qty": 4}), 10.0)

    def test_an_error_has_a_position(self):
        with self.assertRaises(CalcError) as caught:
            evaluate("1 + * 2")
        self.assertEqual(caught.exception.pos, 4)


if __name__ == "__main__":
    unittest.main()
