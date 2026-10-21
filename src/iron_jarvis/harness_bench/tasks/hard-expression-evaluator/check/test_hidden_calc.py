# IJ-BENCH-HIDDEN-CHECK hard-expression-evaluator
import os
import re
import unittest

from calc import CalcError, evaluate

VALUES = [
    ("1 + 2 * 3 - 4 / 2", 5.0),
    ("2 * (3 + 4) ** 2", 98),
    ("-2 ** 2", -4),
    ("(-2) ** 2", 4),
    ("2 ** -1", 0.5),
    ("2 ** 2 ** 3", 256),
    ("--3", 3),
    ("+-+3", -3),
    ("1 - -1", 2),
    ("10 - 3 - 2", 5),
    ("100 / 10 / 5", 2.0),
    ("2 * 3 % 4", 2),
    (".5 + 5.", 5.5),
    ("007", 7),
    ("7 // 2", 3),
    ("-7 // 2", -4),
    ("7 // -2", -4),
    ("-7 % 3", 2),
    ("7 % -3", -2),
    ("7.5 // 2", 3.0),
    ("max(1, 2, -3)", 2),
    ("min(4)", 4),
    ("abs(-2.5)", 2.5),
    ("max(1, min(2, 3) * 2)", 4),
    ("abs(-2) ** 2", 4),
    ("-abs(-3) ** 2", -9),
    ("\t1+\t2 ", 3),
    ("((((7))))", 7),
]

ERRORS = [
    ("1 +", 3),
    ("(1", 2),
    ("1 2", 2),
    ("1 + * 2", 4),
    (")", 0),
    ("2 $ 3", 2),
    ("", 0),
    ("2(3)", 1),
    ("max(1,", 6),
    ("max(1,)", 6),
    ("x + 1", 0),
    ("1 + foo(2)", 4),
    ("abs(1, 2)", 0),
    ("min()", 0),
    ("1/0", 1),
    ("5 % (2-2)", 2),
    ("3 // 0.0", 2),
    ("0 ** -1", 2),
    ("(-8) ** 0.5", 5),
    ("2 * (3 + 4", 10),
]


class HiddenCalc(unittest.TestCase):
    def test_values(self):
        for text, want in VALUES:
            with self.subTest(text=text):
                got = evaluate(text)
                self.assertEqual(got, want)
                self.assertIs(type(got), type(want))

    def test_variables(self):
        self.assertEqual(evaluate("rate * hours", {"rate": 12.5, "hours": 8}), 100.0)
        self.assertEqual(evaluate("_x1 + 1", {"_x1": 2}), 3)
        self.assertEqual(evaluate("-a ** 2", {"a": 3}), -9)

    def test_a_variable_cannot_be_called(self):
        with self.assertRaises(CalcError) as caught:
            evaluate("1 + x(2)", {"x": 1})
        self.assertEqual(caught.exception.pos, 4)

    def test_errors_and_positions(self):
        for text, pos in ERRORS:
            with self.subTest(text=text):
                with self.assertRaises(CalcError) as caught:
                    evaluate(text)
                self.assertEqual(caught.exception.pos, pos)
                self.assertIsInstance(caught.exception, ValueError)

    def test_written_by_hand(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "calc.py"), encoding="utf-8") as fh:
            source = fh.read()
        # The builtins only: re.compile(...) is fine.
        for banned in (r"(?<![\w.])eval\s*\(", r"(?<![\w.])exec\s*\(", r"(?<![\w.])compile\s*\(",
                       r"\bimport\s+ast\b", r"\bfrom\s+ast\b", r"__import__"):
            self.assertIsNone(re.search(banned, source), banned)
