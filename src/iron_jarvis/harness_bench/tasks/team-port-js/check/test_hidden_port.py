# IJ-BENCH-HIDDEN-CHECK team-port-js
"""Hidden check: the Python port gives the reference's results.

Every expected value below was produced by running reference/strutil.js
itself (node, when the task was written) on the same input. An expected value
"raises X" means the reference throws: TypeError stays TypeError, RangeError
becomes ValueError (PORTING.md). The visible tests must be unchanged."""
import hashlib
import importlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CRLF = bytes([13, 10])
LF = bytes([10])
INF = float("inf")
NAN = float("nan")
UNCHANGED = {'test_natural.py': '4b632eb3b0e69fa2d52bed2bf95dff9c2cfa168e58a1e3e2d90ade30346450a5',
 'test_query.py': '8273d724f5ae5d9ff0c675ab503daa145b1cd02a123fc3ded817e55d0d83d45e',
 'test_sizes.py': '9ce7fd33a3f9ea964a60b2b9c54d0299fac610e5c779f73be46e2651464cb591',
 'test_titles.py': '9c1c328ecc8a0e3c448551aabd1d1dbb70f2e899de5e956d3b6a0e0872d109f3',
 'test_wrap.py': '879a78eed99c084a7be36e5308038195fb6400b07183a00a9f0cd4f70f4fa9a5'}

FORMAT_BYTES = [
    (0, {}, '0 B'),
    (999, {}, '999 B'),
    (1000, {}, '1.0 kB'),
    (1500, {}, '1.5 kB'),
    (1536, {'binary': True}, '1.5 KiB'),
    (2560, {'decimals': 0, 'binary': True}, '3 KiB'),
    (1280, {'binary': True}, '1.3 KiB'),
    (1152, {'decimals': 2, 'binary': True}, '1.13 KiB'),
    (999950, {}, '1.0 MB'),
    (999949, {}, '999.9 kB'),
    (-1500, {}, '-1.5 kB'),
    (1023, {'binary': True}, '1023 B'),
    (1048575, {'binary': True}, '1.0 MiB'),
    (1e+21, {}, '1000000.0 PB'),
    (1.5, {}, '1.5 B'),
    (5000000000000000.0, {'decimals': 3}, '5.000 PB'),
    (123456789, {'decimals': 2}, '123.46 MB'),
    (0.5, {}, '0.5 B'),
    (1024, {'decimals': 0, 'binary': True}, '1 KiB'),
    (3584, {'decimals': 0, 'binary': True}, '4 KiB'),
    (4608, {'decimals': 0, 'binary': True}, '5 KiB'),
    (1000, {'decimals': 0}, '1 kB'),
    (1950, {'decimals': 0}, '2 kB'),
    (-2560, {'decimals': 0, 'binary': True}, '-3 KiB'),
    (1099511627776, {'binary': True}, '1.0 TiB'),
    (2305843009213693952, {'binary': True}, '2048.0 PiB'),
    (10240, {'decimals': 2, 'binary': True}, '10.00 KiB'),
    (1000000000, {'decimals': 0}, '1 GB'),
    (2500, {'decimals': 0}, '3 kB'),
    (3500, {'decimals': 0}, '4 kB'),
    (999999, {'decimals': 0}, '1 MB'),
    (125, {'binary': True}, '125 B'),
    (6, {'decimals': 6}, '6 B'),
    (INF, {}, 'raises TypeError'),
    (NAN, {}, 'raises TypeError'),
    ('12', {}, 'raises TypeError'),
    (100, {'decimals': 7}, 'raises ValueError'),
    (100, {'decimals': -1}, 'raises ValueError'),
    (100, {'decimals': 1.5}, 'raises ValueError'),
]

PARSE_QUERY = [
    ('a=1&b=2', {'a': '1', 'b': '2'}),
    ('?x=1&x=2&x=3', {'x': ['1', '2', '3']}),
    ('tags[]=a&tags[]=b&one[]=z', {'tags': ['a', 'b'], 'one': ['z']}),
    ('q=caf%C3%A9+au+lait', {'q': 'caf\xe9 au lait'}),
    ('name=100%', {'name': '100%'}),
    ('name=caf%C3%A9%', {'name': 'caf%C3%A9%'}),
    ('bad=%E9t%C3%A9', {'bad': '%E9t%C3%A9'}),
    ('k=a+b%2Bc', {'k': 'a b+c'}),
    ('flag&empty=&=nokey&&x', {'flag': '', 'empty': '', 'x': ''}),
    ('a=1=2', {'a': '1=2'}),
    ('p=1#frag=2', {'p': '1'}),
    ('a=1&a[]=2', {'a': ['1', '2']}),
    ('a[]=1&a=2', {'a': ['1', '2']}),
    ('%61=b', {'a': 'b'}),
    ('x=%zz+1', {'x': '%zz 1'}),
    ('e=%F0%9F%98%80', {'e': '\U0001f600'}),
    ('s=%ED%A0%80', {'s': '%ED%A0%80'}),
    ('?', {}),
    ('', {}),
    ('#x=1', {}),
    ('[]=1', {'': ['1']}),
    ('a[]', {'a': ['']}),
    ('a[]=1&a[]=&a', {'a': ['1', '', '']}),
    ('x=%41%42c', {'x': 'ABc'}),
    ('plus=%2B%2B+%2b', {'plus': '++ +'}),
    ('multi=%E2%82%AC100', {'multi': '\u20ac100'}),
    ('cut=%E2%82', {'cut': '%E2%82'}),
    ('tilde=%7e', {'tilde': '~'}),
    ('sp=a%20b+c', {'sp': 'a b c'}),
    ('a%5B%5D=1&a%5B%5D=2', {'a': ['1', '2']}),
    ('dup=1&dup=1', {'dup': ['1', '1']}),
    ('key%=v', {'key%': 'v'}),
    ('??a=1', {'?a': '1'}),
]

NATURAL_COMPARE = [
    (['file10', 'file9'], 1),
    (['file9', 'file10'], -1),
    (['file007', 'file7'], 1),
    (['file7', 'file007'], -1),
    (['a', 'A'], 1),
    (['A', 'a'], -1),
    (['a', 'a'], 0),
    (['\u0663', 'a'], 1),
    (['x\u0663', 'x3'], 1),
    (['\U0001f600', '\uff5e'], -1),
    (['\uff5e', '\U0001f600'], 1),
    (['0', '00'], -1),
    (['a1b', 'a1'], 1),
    (['', 'a'], -1),
    (['', ''], 0),
    (['v12345678901234567890', 'v12345678901234567891'], -1),
    (['1.10', '1.9'], 1),
    (['Stra\xdfe', 'STRASSE'], 1),
    (['img12.png', 'img10.png'], 1),
    (['x2-g8', 'x2-y7'], -1),
    (['chapter 1', 'Chapter 1'], 1),
    (['10', '9a'], 1),
    (['abc', 'abc1'], -1),
    (['1abc', 'abc'], -1),
    (['z\xe9', 'ze'], 1),
    (['\xc9', 'e'], 1),
    (['a\u0663', 'a3'], 1),
]

NATURAL_SORT = [
    (['file10', 'file9', 'File1', 'file1', 'file01', 'file001'], ['File1', 'file1', 'file01', 'file001', 'file9', 'file10']),
    (['img12.png', 'img10.png', 'IMG2.png', 'img1.png'], ['img1.png', 'IMG2.png', 'img10.png', 'img12.png']),
    (['b', 'B', 'a', 'A', '\xe1', '1', '01', ''], ['', '1', '01', 'A', 'a', 'B', 'b', '\xe1']),
    (['\U0001f600', '\uff5e', '\u3042', 'z'], ['z', '\u3042', '\U0001f600', '\uff5e']),
    (['x\u0663', 'x3', 'x\u0662', 'x2'], ['x2', 'x3', 'x\u0662', 'x\u0663']),
]

WRAP_TEXT = [
    (['The quick brown fox jumps over the lazy dog', 10], 'The quick\nbrown fox\njumps over\nthe lazy\ndog'),
    (['supercalifragilistic', 6], 'superc\nalifra\ngilist\nic'),
    (['ab supercalifragilistic', 6], 'ab sup\nercali\nfragil\nistic'),
    (['abcd efghijkl', 5], 'abcd\nefghi\njkl'),
    (['a  b   c', 1], 'a\nb\nc'),
    (['one\n\ntwo three', 5], 'one\n\ntwo\nthree'),
    (['tab\tseparated words here', 8], 'tab\tsepa\nrated\nwords\nhere'),
    (['line one\r\nline two', 20], 'line one\r\nline two'),
    (['form\x0cfeed and more', 6], 'form\x0cf\need\nand\nmore'),
    (['nbsp\xa0joined words', 10], 'nbsp\xa0joine\nd words'),
    (['\U0001f600\U0001f600 ab cd', 6], '\U0001f600\U0001f600\nab cd'),
    (['trailing spaces   ', 20], 'trailing spaces'),
    (['', 5], ''),
    (['\n', 3], '\n'),
    (['exactly ten', 11], 'exactly ten'),
    (['exactly', 7], 'exactly'),
    (['abcdefghijk', 5], 'abcde\nfghij\nk'),
    (['a bc def ghij klmno', 4], 'a bc\ndef\nghij\nklmn\no'),
    (['  indented text', 8], 'indented\ntext'),
    (['x yyyyyyy', 3], 'x y\nyyy\nyyy'),
    (['\u2028para sep', 4], '\u2028par\na\nsep'),
    (['text', 0], 'raises ValueError'),
    (['text', -3], 'raises ValueError'),
    (['text', 2.5], 'raises ValueError'),
    ([123, 5], 'raises TypeError'),
]

TITLE_CASE = [
    ('the lord of the rings', 'The Lord of the Rings'),
    ('a tale: of two cities', 'A Tale: Of Two Cities'),
    ('what is it for', 'What Is It For'),
    ("they're here", "They're Here"),
    ('x-ray vision for the over-the-top', 'X-Ray Vision for the Over-The-Top'),
    ('(of mice and men)', '(of Mice and Men)'),
    ('the iPhone and the x86 chip', 'The iPhone and the x86 Chip'),
    ('batman vs superman', 'Batman vs Superman'),
    ('  leading and  double  spaces ', '  Leading and  Double  Spaces '),
    ('\xc9COLE de paris', '\xc9COLE De Paris'),
    ('\xe9lan vital', '\xc9lan Vital'),
    ('', ''),
    ('on', 'On'),
    ('the end. and then', 'The End. And Then'),
    ('NASA and the API', 'NASA and the API'),
    ('a-ok', 'A-Ok'),
    ('of', 'Of'),
    ('hello\xa0world of code', 'Hello\xa0World of Code'),
    ('is it? yes it is', 'Is It? Yes It Is'),
    ("rock 'n' roll", "Rock 'n' Roll"),
    ("don't stop believing", "Don't Stop Believing"),
    ('war and peace', 'War and Peace'),
    ('the 3 musketeers', 'The 3 Musketeers'),
    ('per aspera ad astra', 'Per Aspera Ad Astra'),
    ('in the beginning', 'In the Beginning'),
    ("o'neill of the year", "O'neill of the Year"),
    ('\xdf and \u0131', 'SS and I'),
    (42, 'raises TypeError'),
]


def _function(module, name):
    return getattr(importlib.import_module("strutil." + module), name)


class HiddenPort(unittest.TestCase):
    maxDiff = 2000

    def _check(self, call, expected, label):
        if isinstance(expected, str) and expected.startswith("raises "):
            error = {"raises TypeError": TypeError, "raises ValueError": ValueError}[expected]
            with self.assertRaises(error, msg=f"{label}: the reference throws here"):
                call()
            return
        try:
            got = call()
        except Exception as exc:  # noqa: BLE001 - a crash is a wrong answer, named
            self.fail(f"{label}: raised {type(exc).__name__}: {exc}; the reference gives {expected!r}")
        self.assertEqual(got, expected, label)

    def test_a_format_bytes(self):
        fn = _function("sizes", "format_bytes")
        for value, kwargs, expected in FORMAT_BYTES:
            with self.subTest(value=value, **kwargs):
                self._check(lambda: fn(value, **kwargs), expected, f"format_bytes({value!r}, **{kwargs})")

    def test_b_parse_query(self):
        fn = _function("query", "parse_query")
        for query, expected in PARSE_QUERY:
            with self.subTest(query=query):
                self._check(lambda: fn(query), expected, f"parse_query({query!r})")

    def test_c_natural_compare(self):
        fn = _function("natural", "natural_compare")
        for (a, b), expected in NATURAL_COMPARE:
            with self.subTest(a=a, b=b):
                self._check(lambda: fn(a, b), expected, f"natural_compare({a!r}, {b!r})")

    def test_d_natural_sort(self):
        fn = _function("natural", "natural_sort")
        for items, expected in NATURAL_SORT:
            with self.subTest(items=items):
                given = list(items)
                self._check(lambda: fn(given), expected, f"natural_sort({items!r})")
                self.assertEqual(given, items, "natural_sort changed its argument")

    def test_e_wrap_text(self):
        fn = _function("wrap", "wrap_text")
        for (text, width), expected in WRAP_TEXT:
            with self.subTest(text=text, width=width):
                self._check(lambda: fn(text, width), expected, f"wrap_text({text!r}, {width!r})")

    def test_f_title_case(self):
        fn = _function("titles", "title_case")
        for text, expected in TITLE_CASE:
            with self.subTest(text=text):
                self._check(lambda: fn(text), expected, f"title_case({text!r})")

    def test_g_the_package_exports_all_six(self):
        package = importlib.import_module("strutil")
        for module, name in (("sizes", "format_bytes"), ("query", "parse_query"),
                             ("natural", "natural_compare"), ("natural", "natural_sort"),
                             ("wrap", "wrap_text"), ("titles", "title_case")):
            self.assertIs(getattr(package, name, None), _function(module, name), name)

    def test_h_the_visible_tests_are_unchanged(self):
        for name, digest in UNCHANGED.items():
            path = os.path.join(HERE, name)
            self.assertTrue(os.path.isfile(path), f"{name} was removed")
            with open(path, "rb") as fh:
                data = fh.read().replace(CRLF, LF)
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest, f"{name} was changed")


if __name__ == "__main__":
    unittest.main()
