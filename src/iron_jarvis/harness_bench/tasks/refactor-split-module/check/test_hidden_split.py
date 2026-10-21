# IJ-BENCH-HIDDEN-CHECK refactor-split-module
import ast
import os
import unittest

import mathutils
import stats_report
import utils


class HiddenSplit(unittest.TestCase):
    def test_new_module(self):
        self.assertEqual(mathutils.mean([2, 4]), 3)
        self.assertEqual(mathutils.median([9, 1, 5, 3]), 4)
        with self.assertRaises(ValueError):
            mathutils.mean([])
        with self.assertRaises(ValueError):
            mathutils.median([])

    def test_utils_reexports_the_same_functions(self):
        self.assertIs(utils.mean, mathutils.mean)
        self.assertIs(utils.median, mathutils.median)
        self.assertEqual(utils.shout("x"), "X!")

    def test_utils_does_not_define_them(self):
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "utils.py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        defined = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        self.assertNotIn("mean", defined)
        self.assertNotIn("median", defined)

    def test_report_still_works(self):
        self.assertEqual(stats_report.summary("Bo Ek", [1, 2]), "BE: mean 1.5, median 1.5")
