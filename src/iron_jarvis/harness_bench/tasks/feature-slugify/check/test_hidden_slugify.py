# IJ-BENCH-HIDDEN-CHECK feature-slugify
import unittest

import textutil


class HiddenSlugify(unittest.TestCase):
    def test_examples_from_the_request(self):
        self.assertEqual(textutil.slugify("Hello, World!"), "hello-world")
        self.assertEqual(
            textutil.slugify("  Crème brûlée -- recipe #2 "), "creme-brulee-recipe-2"
        )
        self.assertEqual(textutil.slugify("!!!"), "")

    def test_more_cases(self):
        self.assertEqual(textutil.slugify("ÅNGSTRÖM unit"), "angstrom-unit")
        self.assertEqual(textutil.slugify("a__b"), "a-b")
        self.assertEqual(textutil.slugify("Already-a-slug"), "already-a-slug")
        self.assertEqual(textutil.slugify("Año 2024"), "ano-2024")
        self.assertEqual(textutil.slugify(""), "")
        self.assertEqual(textutil.slugify("-x-"), "x")

    def test_old_helpers_still_work(self):
        self.assertEqual(textutil.title_case("a b"), "A B")
        self.assertEqual(textutil.collapse_spaces(" a  b "), "a b")
