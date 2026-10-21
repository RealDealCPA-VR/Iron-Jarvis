# IJ-BENCH-HIDDEN-CHECK hard-refactor-module
import ast
import dataclasses
import importlib
import inspect
import os
import tempfile
import unittest

import textkit

HERE = os.path.dirname(os.path.abspath(__file__))
MODULES = {
    "tokens": ("words", "sentences", "ngrams", "stopwords", "content_words"),
    "stats": ("frequencies", "top_words", "syllables", "flesch_reading_ease",
              "lexical_diversity", "average_word_length", "longest_words"),
    "render": ("bar", "histogram", "table", "summary"),
}
USES_SETTINGS = (
    "words", "stopwords", "content_words", "frequencies", "top_words", "flesch_reading_ease",
    "lexical_diversity", "average_word_length", "longest_words", "bar", "histogram", "table", "summary",
)
MUTABLE_CALLS = {"dict", "list", "set", "Counter", "defaultdict", "OrderedDict", "deque"}

STOP = '# custom list\nwas\nof\n\nthe\nit\n'
TEXT = "It was the best of times, it was the worst of times. Wisdom and foolishness walked together! Did anyone notice? The little people didn't, but the remarkable ones certainly noticed everything that happened."
EXPECTED = [
    ({}, 'words', (TEXT,), {}, ['it', 'was', 'the', 'best', 'of', 'times', 'it', 'was', 'the', 'worst', 'of', 'times', 'wisdom', 'and', 'foolishness', 'walked', 'together', 'did', 'anyone', 'notice', 'the', 'little', 'people', "didn't", 'but', 'the', 'remarkable', 'ones', 'certainly', 'noticed', 'everything', 'that', 'happened']),
    ({}, 'sentences', (TEXT,), {}, ['It was the best of times, it was the worst of times.', 'Wisdom and foolishness walked together!', 'Did anyone notice?', "The little people didn't, but the remarkable ones certainly noticed everything that happened."]),
    ({}, 'ngrams', (['a', 'b', 'c', 'd'], 2), {}, [('a', 'b'), ('b', 'c'), ('c', 'd')]),
    ({}, 'stopwords', (), {}, ['a', 'an', 'and', 'are', 'as', 'at', 'be', 'but', 'by', 'for', 'from', 'had', 'has', 'have', 'he', 'her', 'his', 'i', 'in', 'is', 'it', 'its', 'not', 'of', 'on', 'or', 'she', 'that', 'the', 'their', 'they', 'this', 'to', 'was', 'we', 'were', 'will', 'with', 'you']),
    ({}, 'content_words', (TEXT,), {}, ['best', 'times', 'worst', 'times', 'wisdom', 'foolishness', 'walked', 'together', 'did', 'anyone', 'notice', 'little', 'people', "didn't", 'remarkable', 'ones', 'certainly', 'noticed', 'everything', 'happened']),
    ({}, 'frequencies', (TEXT,), {}, [('and', 1), ('anyone', 1), ('best', 1), ('but', 1), ('certainly', 1), ('did', 1), ("didn't", 1), ('everything', 1), ('foolishness', 1), ('happened', 1), ('it', 2), ('little', 1), ('notice', 1), ('noticed', 1), ('of', 2), ('ones', 1), ('people', 1), ('remarkable', 1), ('that', 1), ('the', 4), ('times', 2), ('together', 1), ('walked', 1), ('was', 2), ('wisdom', 1), ('worst', 1)]),
    ({}, 'top_words', (TEXT, 4), {}, [('times', 2), ('anyone', 1), ('best', 1), ('certainly', 1)]),
    ({}, 'top_words', (TEXT, 3), {'include_stopwords': True}, [('the', 4), ('it', 2), ('of', 2)]),
    ({}, 'syllables', ('remarkable',), {}, 4),
    ({}, 'syllables', ('little',), {}, 2),
    ({}, 'syllables', ('people',), {}, 2),
    ({}, 'flesch_reading_ease', (TEXT,), {}, 49.77),
    ({}, 'lexical_diversity', (TEXT,), {}, 0.79),
    ({}, 'average_word_length', (TEXT,), {}, 5.03),
    ({}, 'longest_words', (TEXT, 4), {}, ['foolishness', 'everything', 'remarkable', 'certainly']),
    ({}, 'bar', (3, 7), {}, '#############'),
    ({}, 'histogram', (TEXT, 3), {}, 'times  | ############################## 2\nanyone | ############### 1\nbest   | ############### 1'),
    ({}, 'table', ([('alpha', 1, 2.5), ('b', 22, 0.25)], ('name', 'n', 'x')), {}, 'name   n   x\n-----  --  ----\nalpha   1  2.50\nb      22  0.25'),
    ({}, 'summary', (TEXT,), {}, 'measure       value\n------------  -----\nwords            33\nsentences         4\ndistinct         26\ndiversity      0.79\navg length     5.03\nreading ease  49.77\n\nlongest: foolishness, everything, remarkable\n\ntimes     | ############################## 2\nanyone    | ############### 1\nbest      | ############### 1\ncertainly | ############### 1\ndid       | ############### 1'),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'words', (TEXT,), {}, ['best', 'times', 'worst', 'times', 'Wisdom', 'foolishness', 'walked', 'together', 'anyone', 'notice', 'little', 'people', "didn't", 'remarkable', 'ones', 'certainly', 'noticed', 'everything', 'that', 'happened']),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'sentences', (TEXT,), {}, ['It was the best of times, it was the worst of times.', 'Wisdom and foolishness walked together!', 'Did anyone notice?', "The little people didn't, but the remarkable ones certainly noticed everything that happened."]),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'ngrams', (['a', 'b', 'c', 'd'], 2), {}, [('a', 'b'), ('b', 'c'), ('c', 'd')]),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'stopwords', (), {}, ['a', 'an', 'and', 'are', 'as', 'at', 'be', 'but', 'by', 'for', 'from', 'had', 'has', 'have', 'he', 'her', 'his', 'i', 'in', 'is', 'it', 'its', 'not', 'of', 'on', 'or', 'she', 'that', 'the', 'their', 'they', 'this', 'to', 'was', 'we', 'were', 'will', 'with', 'you']),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'content_words', (TEXT,), {}, ['best', 'times', 'worst', 'times', 'Wisdom', 'foolishness', 'walked', 'together', 'anyone', 'notice', 'little', 'people', "didn't", 'remarkable', 'ones', 'certainly', 'noticed', 'everything', 'happened']),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'frequencies', (TEXT,), {}, [('Wisdom', 1), ('anyone', 1), ('best', 1), ('certainly', 1), ("didn't", 1), ('everything', 1), ('foolishness', 1), ('happened', 1), ('little', 1), ('notice', 1), ('noticed', 1), ('ones', 1), ('people', 1), ('remarkable', 1), ('that', 1), ('times', 2), ('together', 1), ('walked', 1), ('worst', 1)]),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'top_words', (TEXT, 4), {}, [('times', 2), ('Wisdom', 1), ('anyone', 1), ('best', 1)]),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'top_words', (TEXT, 3), {'include_stopwords': True}, [('times', 2), ('Wisdom', 1), ('anyone', 1)]),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'syllables', ('remarkable',), {}, 4),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'syllables', ('little',), {}, 2),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'syllables', ('people',), {}, 2),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'flesch_reading_ease', (TEXT,), {}, 11.4),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'lexical_diversity', (TEXT,), {}, 0.9),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'average_word_length', (TEXT,), {}, 6.5),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'longest_words', (TEXT, 4), {}, ['foolishness', 'everything', 'remarkable', 'certainly']),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'bar', (3, 7), {}, '****'),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'histogram', (TEXT, 3), {}, 'times  | ********** 2\nWisdom | ***** 1\nanyone | ***** 1'),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'table', ([('alpha', 1, 2.5), ('b', 22, 0.25)], ('name', 'n', 'x')), {}, 'name   n   x\n-----  --  ---\nalpha   1  2.5\nb      22  0.2'),
    ({'min_word_length': 4, 'lowercase': False, 'bar_width': 10, 'bar_char': '*', 'decimals': 1}, 'summary', (TEXT,), {}, 'measure       value\n------------  -----\nwords            20\nsentences         4\ndistinct         19\ndiversity       0.9\navg length      6.5\nreading ease   11.4\n\nlongest: foolishness, everything, remarkable\n\ntimes     | ********** 2\nWisdom    | ***** 1\nanyone    | ***** 1\nbest      | ***** 1\ncertainly | ***** 1'),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'words', (TEXT,), {}, ['it', 'was', 'the', 'best', 'of', 'times', 'it', 'was', 'the', 'worst', 'of', 'times', 'wisdom', 'and', 'foolishness', 'walked', 'together', 'did', 'anyone', 'notice', 'the', 'little', 'people', "didn't", 'but', 'the', 'remarkable', 'ones', 'certainly', 'noticed', 'everything', 'that', 'happened']),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'sentences', (TEXT,), {}, ['It was the best of times, it was the worst of times.', 'Wisdom and foolishness walked together!', 'Did anyone notice?', "The little people didn't, but the remarkable ones certainly noticed everything that happened."]),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'ngrams', (['a', 'b', 'c', 'd'], 2), {}, [('a', 'b'), ('b', 'c'), ('c', 'd')]),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'stopwords', (), {}, ['it', 'of', 'the', 'was']),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'content_words', (TEXT,), {}, ['best', 'times', 'worst', 'times', 'wisdom', 'and', 'foolishness', 'walked', 'together', 'did', 'anyone', 'notice', 'little', 'people', "didn't", 'but', 'remarkable', 'ones', 'certainly', 'noticed', 'everything', 'that', 'happened']),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'frequencies', (TEXT,), {}, [('and', 1), ('anyone', 1), ('best', 1), ('but', 1), ('certainly', 1), ('did', 1), ("didn't", 1), ('everything', 1), ('foolishness', 1), ('happened', 1), ('it', 2), ('little', 1), ('notice', 1), ('noticed', 1), ('of', 2), ('ones', 1), ('people', 1), ('remarkable', 1), ('that', 1), ('the', 4), ('times', 2), ('together', 1), ('walked', 1), ('was', 2), ('wisdom', 1), ('worst', 1)]),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'top_words', (TEXT, 4), {}, [('times', 2), ('and', 1), ('anyone', 1), ('best', 1)]),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'top_words', (TEXT, 3), {'include_stopwords': True}, [('the', 4), ('it', 2), ('of', 2)]),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'syllables', ('remarkable',), {}, 4),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'syllables', ('little',), {}, 2),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'syllables', ('people',), {}, 2),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'flesch_reading_ease', (TEXT,), {}, 49.77),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'lexical_diversity', (TEXT,), {}, 0.788),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'average_word_length', (TEXT,), {}, 5.03),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'longest_words', (TEXT, 4), {}, ['foolishness', 'everything', 'remarkable', 'certainly']),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'bar', (3, 7), {}, '#############'),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'histogram', (TEXT, 3), {}, 'times  | ############################## 2\nand    | ############### 1\nanyone | ############### 1'),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'table', ([('alpha', 1, 2.5), ('b', 22, 0.25)], ('name', 'n', 'x')), {}, 'name   n   x\n-----  --  -----\nalpha   1  2.500\nb      22  0.250'),
    ({'stopwords_file': 'STOPFILE', 'decimals': 3}, 'summary', (TEXT,), {}, 'measure       value\n------------  ------\nwords             33\nsentences          4\ndistinct          26\ndiversity      0.788\navg length     5.030\nreading ease  49.770\n\nlongest: foolishness, everything, remarkable\n\ntimes  | ############################## 2\nand    | ############### 1\nanyone | ############### 1\nbest   | ############### 1\nbut    | ############### 1'),
]


def _norm(value):
    if isinstance(value, frozenset):
        return sorted(value)
    if hasattr(value, "most_common"):
        return sorted(value.items())
    return value


class HiddenRefactor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.stop_path = os.path.join(cls.tmp, "stop.txt")
        with open(cls.stop_path, "w", encoding="utf-8") as fh:
            fh.write(STOP)

    def _config(self, cfg):
        values = {k: (self.stop_path if v == "STOPFILE" else v) for k, v in cfg.items()}
        return textkit.Config(**values)

    def test_it_is_a_package_with_four_modules(self):
        self.assertFalse(os.path.exists(os.path.join(HERE, "textkit.py")))
        self.assertTrue(os.path.isfile(os.path.join(HERE, "textkit", "__init__.py")))
        for module in ("config", *MODULES):
            self.assertTrue(os.path.isfile(os.path.join(HERE, "textkit", module + ".py")), module)

    def test_each_function_lives_in_its_module(self):
        for module, names in MODULES.items():
            mod = importlib.import_module(f"textkit.{module}")
            for name in names:
                fn = getattr(textkit, name)
                self.assertEqual(fn.__module__, f"textkit.{module}", name)
                self.assertIs(getattr(mod, name), fn)

    def test_config_is_a_frozen_dataclass_with_the_defaults(self):
        self.assertTrue(dataclasses.is_dataclass(textkit.Config))
        self.assertIs(textkit.Config, importlib.import_module("textkit.config").Config)
        c = textkit.Config()
        self.assertEqual(
            (c.min_word_length, c.lowercase, c.stopwords_file, c.bar_width, c.bar_char, c.decimals),
            (1, True, None, 30, "#", 2),
        )
        with self.assertRaises(dataclasses.FrozenInstanceError):
            c.bar_width = 3

    def test_bad_values_are_refused(self):
        for bad in ({"bar_width": -1}, {"decimals": 1.5}, {"min_word_length": True},
                    {"bar_char": "##"}, {"bar_char": 7}):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    textkit.Config(**bad)

    def test_the_globals_are_gone(self):
        for gone in ("configure", "reset_config", "CONFIG", "_STOPWORDS_CACHE"):
            self.assertFalse(hasattr(textkit, gone), gone)
        package = os.path.join(HERE, "textkit")
        for name in sorted(os.listdir(package)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(package, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                self.assertNotIsInstance(node, ast.Global, name)
            for node in tree.body:
                value = node.value if isinstance(node, (ast.Assign, ast.AnnAssign)) else None
                if value is None:
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if all(isinstance(t, ast.Name) and t.id == "__all__" for t in targets):
                    continue  # the export list is not state
                self.assertNotIsInstance(value, (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp), name)
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    self.assertNotIn(value.func.id, MUTABLE_CALLS, name)

    def test_settings_come_in_as_a_keyword(self):
        for name in USES_SETTINGS:
            param = inspect.signature(getattr(textkit, name)).parameters.get("config")
            self.assertIsNotNone(param, name)
            self.assertIs(param.kind, inspect.Parameter.KEYWORD_ONLY, name)
            self.assertIsNone(param.default, name)

    def test_behaviour_is_unchanged(self):
        for cfg, name, args, kwargs, want in EXPECTED:
            with self.subTest(config=cfg, name=name):
                fn = getattr(textkit, name)
                extra = {"config": self._config(cfg)} if name in USES_SETTINGS else {}
                if not cfg and extra:
                    # The default must also be what None gives.
                    self.assertEqual(_norm(fn(*args, **kwargs)), want)
                self.assertEqual(_norm(fn(*args, **kwargs, **extra)), want)

    def test_two_settings_side_by_side(self):
        loud = textkit.Config(lowercase=False, min_word_length=4)
        quiet = textkit.Config(stopwords_file=self.stop_path)
        a1 = textkit.top_words(TEXT, 3, config=loud)
        b1 = textkit.top_words(TEXT, 3, config=quiet)
        a2 = textkit.top_words(TEXT, 3, config=loud)
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1, b1)
        self.assertEqual(textkit.top_words(TEXT, 3), textkit.top_words(TEXT, 3, config=textkit.Config()))
