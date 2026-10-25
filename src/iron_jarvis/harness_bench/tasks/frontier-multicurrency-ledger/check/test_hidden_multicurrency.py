# IJ-BENCH-HIDDEN-CHECK frontier-multicurrency-ledger
import hashlib
import importlib
import operator
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

CRLF = bytes([13, 10])
LF = bytes([10])
UNCHANGED = {
    "test_book.py": "26dde31e9ed79336a72f8157d813f8426519f3ffb5c822fdabcb394c079487b4",
    "test_cli.py": "bf6dedb5f306eddba1953b9405317def2e052c53b6292260228a793d4b1eae4f",
    "test_money.py": "c5c1c43e1551c2f4733b0ccd0a82d704aedf51be0b1bbb49e38b3cb2905483d4",
    "test_multicurrency.py": "f2b07ccd25120d31f708e18301d762f6574edb0855fa88446af572a0b9dfec29",
    "test_parse.py": "609157b4488859273683fc48d498a47a7a02ed252e636db06f9e2d9ea37981eb",
    "test_report.py": "ca269e9c83da8d6b829a027ba2041329d59f74619424c95a5447ebf9f6c012c5",
}

# Several accounts, currencies in no particular order and in every case, a
# balance that ends at zero, and amounts whose conversions land on half cents.
MIXED = """date,account,amount,description,currency
2024-02-01,Liabilities:Card,-120.40,card,usd
2024-02-02,Assets:Cash,0.03,coins,eur
2024-02-03,Assets:Bank,2500.00,salary,USD
2024-02-04,Assets:Cash,15.00,cash,GBP
2024-02-05,Income:Refund,0.03,refund,EUR
2024-02-06,Assets:Bank,-0.07,fee, gbp
"""
# USD,EUR 1.5 and USD,GBP 0.5; the GBP,USD row must never be inverted.
RATES = "# rates for MIXED\nbase,currency,rate\nUSD,EUR,1.5\nusd,gbp,0.5\nGBP,USD,2\n"

MIXED_TABLE = (
    "Ledger report\n"
    "=============\n"
    "\n"
    "Assets:Bank       GBP     -0.07\n"
    "Assets:Bank       USD  2,500.00\n"
    "Assets:Cash       EUR      0.03\n"
    "Assets:Cash       GBP     15.00\n"
    "Income:Refund     EUR      0.03\n"
    "Liabilities:Card  USD   -120.40\n"
    "-------------------------------\n"
    "Total             EUR      0.06\n"
    "Total             GBP     14.93\n"
    "Total             USD  2,379.60\n"
)
# Each balance converted and rounded on its own: -0.035 -> -0.04, 0.045 -> 0.04,
# 7.50, 0.045 -> 0.04. Converting the totals gives 2,387.15; rounding the
# unrounded sum (2,387.155) gives 2,387.16.
MIXED_GRAND = "-------------------------------\nGrand total       USD  2,387.14\n"

ORDER = """date,account,amount,currency
2024-01-01,Assets:Bank,10.00,usd
2024-01-02,Assets:Bank,5.00,GBP
2024-01-03,Assets:Bank,2.50,eur
2024-01-04,Assets:Bank,-5.00,gbp
2024-01-05,Expenses:Misc,1.25,EUR
2024-01-06,Assets:Bank,1.00,Eur
"""
ORDER_REPORT = (
    "Ledger report\n"
    "=============\n"
    "\n"
    "Assets:Bank    EUR   3.50\n"
    "Assets:Bank    GBP   0.00\n"
    "Assets:Bank    USD  10.00\n"
    "Expenses:Misc  EUR   1.25\n"
    "-------------------------\n"
    "Total          EUR   4.75\n"
    "Total          GBP   0.00\n"
    "Total          USD  10.00\n"
)


# The Grand total line is the widest line of this table (label AND amount):
# SPEC 7.6 says the widths are computed over every line, the Grand total line
# included.
WIDE_GRAND = """date,account,amount,currency
2024-01-01,A,900.00,EUR
2024-01-02,B,900.00,USD
"""
WIDE_GRAND_REPORT = (
    "Ledger report\n"
    "=============\n"
    "\n"
    "A            EUR      900.00\n"
    "B            USD      900.00\n"
    "----------------------------\n"
    "Total        EUR      900.00\n"
    "Total        USD      900.00\n"
    "----------------------------\n"
    "Grand total  USD  900,900.00\n"
)


def _ledger(name):
    return importlib.import_module(f"ledger.{name}")


def _money(amount, currency="USD"):
    return _ledger("money").Money(amount, currency)


def _book(text):
    return _ledger("book").Book.from_transactions(_ledger("parse").parse_text(text))


def _write(folder, name, text):
    path = os.path.join(folder, name)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


def _cli(*args):
    env = dict(os.environ)
    env.pop("PYTHONSAFEPATH", None)
    env["PYTHONPATH"] = HERE
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "ledger.cli", *args],
        cwd=HERE, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )


class HiddenMoney(unittest.TestCase):
    def test_codes_are_normalised_to_upper_case(self):
        Money = _ledger("money").Money
        self.assertEqual(Money("1.00", "eur").currency, "EUR")
        self.assertEqual(Money("1.00", "Gbp").currency, "GBP")
        self.assertEqual(Money.parse(" 2.50 ", "gbp"), Money("2.5", "GBP"))
        self.assertEqual(Money.parse("2.50", "gbp").currency, "GBP")
        self.assertEqual(Money.zero("chf"), Money("0", "CHF"))
        self.assertEqual(Money.zero("chf").currency, "CHF")
        self.assertEqual(Money("3").currency, "USD")
        self.assertEqual(Money.parse("4").currency, "USD")
        self.assertEqual(Money.zero().currency, "USD")
        self.assertEqual(Money("1.5", "eur"), Money("1.50", "EUR"))
        self.assertEqual(hash(Money("1.5", "eur")), hash(Money("1.50", "EUR")))

    def test_bad_codes_are_refused(self):
        Money = _ledger("money").Money
        for code in ("EURO", "EU", "E1R", "", " EUR", "€UR", "ÉUR", "ДОЛ", "eür"):
            with self.subTest(code=code):
                with self.assertRaises(ValueError):
                    Money("1", code)

    def test_adding_or_subtracting_two_currencies_is_a_mismatch(self):
        errors = _ledger("errors")
        self.assertTrue(issubclass(errors.CurrencyMismatch, errors.LedgerError))
        with self.assertRaises(errors.CurrencyMismatch) as caught:
            _money("1", "USD") + _money("1", "EUR")
        self.assertEqual((caught.exception.left, caught.exception.right), ("USD", "EUR"))
        self.assertEqual(str(caught.exception), "currency mismatch: USD vs EUR")
        with self.assertRaises(errors.CurrencyMismatch) as caught:
            _money("1", "eur") - _money("1", "usd")
        self.assertEqual((caught.exception.left, caught.exception.right), ("EUR", "USD"))
        self.assertEqual(str(caught.exception), "currency mismatch: EUR vs USD")

    def test_ordering_two_currencies_is_a_mismatch(self):
        errors = _ledger("errors")
        for op in (operator.lt, operator.le, operator.gt, operator.ge):
            with self.subTest(op=op.__name__):
                with self.assertRaises(errors.CurrencyMismatch) as caught:
                    op(_money("1", "GBP"), _money("2", "USD"))
                self.assertEqual((caught.exception.left, caught.exception.right), ("GBP", "USD"))
                with self.assertRaises(errors.CurrencyMismatch):
                    op(_money("5", "USD"), _money("5", "EUR"))

    def test_equality_never_raises(self):
        self.assertFalse(_money("1", "USD") == _money("1", "EUR"))
        self.assertTrue(_money("1", "USD") != _money("1", "EUR"))
        self.assertEqual(len({_money("1", "USD"), _money("1", "EUR"), _money("1.00", "usd")}), 2)
        self.assertTrue(_money("1", "EUR") < _money("2", "eur"))
        self.assertTrue(_money("2", "EUR") >= _money("2.00", "eur"))

    def test_same_currency_arithmetic_keeps_the_currency(self):
        self.assertEqual(_money("1.10", "GBP") + _money("2", "gbp"), _money("3.10", "GBP"))
        self.assertEqual((_money("1.10", "GBP") + _money("2", "gbp")).currency, "GBP")
        self.assertEqual(_money("5", "JPY") - _money("7", "JPY"), _money("-2", "JPY"))
        self.assertEqual((-_money("1", "eur")).currency, "EUR")

    def test_repr_shows_amount_and_currency(self):
        self.assertEqual(repr(_money("12.50", "eur")), "Money('12.50', 'EUR')")


class HiddenParse(unittest.TestCase):
    def test_currency_codes_in_any_case(self):
        text = "date,account,amount,currency\n2024-01-01,A,1,eur\n2024-01-02,A,2,Eur\n2024-01-03,A,3, gbp \n2024-01-04,A,4,USD\n"
        txns = _ledger("parse").parse_text(text)
        self.assertEqual([t.amount.currency for t in txns], ["EUR", "EUR", "GBP", "USD"])
        self.assertEqual(txns[2].amount, _money("3", "GBP"))

    def test_empty_or_missing_currency_means_dollars(self):
        text = (
            " Currency ,date,account,amount\n"
            ",2024-01-01,Cash,1\n"
            "   ,2024-01-02,Cash,2\n"
        )
        txns = _ledger("parse").parse_text(text)
        self.assertEqual([t.amount for t in txns], [_money("1", "USD"), _money("2", "USD")])
        short = "date,account,amount,currency\n2024-01-01,Cash,1\n2024-01-02,Cash,2,\n"
        txns = _ledger("parse").parse_text(short)
        self.assertEqual([t.amount for t in txns], [_money("1", "USD"), _money("2", "USD")])

    def test_a_bad_currency_is_a_parse_error_on_its_line(self):
        ParseError = _ledger("errors").ParseError
        for code in ("EURO", "E1R", "€", "US", "äöü", "ÉUR"):
            with self.subTest(code=code):
                text = f"date,account,amount,currency\n2024-01-01,Cash,1,EUR\n2024-01-02,Cash,2,{code}\n"
                with self.assertRaises(ParseError) as caught:
                    _ledger("parse").parse_text(text)
                self.assertEqual(caught.exception.line, 3)
                self.assertEqual(str(caught.exception), f"line 3: bad currency {code!r}")

    def test_the_amount_is_checked_before_the_currency(self):
        ParseError = _ledger("errors").ParseError
        with self.assertRaises(ParseError) as caught:
            _ledger("parse").parse_text("date,account,amount,currency\n2024-01-01,Cash,1.234,EURO\n")
        self.assertEqual(str(caught.exception), "line 2: bad amount '1.234'")

    def test_other_unknown_columns_are_still_refused(self):
        ParseError = _ledger("errors").ParseError
        with self.assertRaises(ParseError) as caught:
            _ledger("parse").parse_text("date,account,amount,currency,memo\n")
        self.assertEqual(str(caught.exception), "line 1: unknown column 'memo'")


class HiddenBook(unittest.TestCase):
    def setUp(self):
        self.book = _book(ORDER)

    def test_balances_per_currency_in_code_order(self):
        balances = self.book.balances("Assets:Bank")
        self.assertEqual(list(balances), ["EUR", "GBP", "USD"])
        self.assertEqual(balances, {"EUR": _money("3.50", "EUR"), "GBP": _money("0", "GBP"), "USD": _money("10", "USD")})
        self.assertEqual(self.book.balances("Expenses:Misc"), {"EUR": _money("1.25", "EUR")})

    def test_balance_in_one_currency(self):
        self.assertEqual(self.book.balance("Assets:Bank", "eur"), _money("3.50", "EUR"))
        self.assertEqual(self.book.balance("Assets:Bank", "GBP"), _money("0", "GBP"))
        self.assertEqual(self.book.balance("Assets:Bank"), _money("10", "USD"))
        self.assertEqual(self.book.balance("Expenses:Misc", "jpy"), _money("0", "JPY"))
        self.assertEqual(self.book.balance("Expenses:Misc", "jpy").currency, "JPY")

    def test_unknown_accounts(self):
        UnknownAccount = _ledger("errors").UnknownAccount
        with self.assertRaises(UnknownAccount):
            self.book.balances("Nope")
        with self.assertRaises(UnknownAccount):
            self.book.balance("Nope", "EUR")

    def test_currencies_and_totals(self):
        self.assertEqual(self.book.currencies(), ["EUR", "GBP", "USD"])
        totals = self.book.totals()
        self.assertEqual(list(totals), ["EUR", "GBP", "USD"])
        self.assertEqual(totals, {"EUR": _money("4.75", "EUR"), "GBP": _money("0", "GBP"), "USD": _money("10", "USD")})
        self.assertEqual(self.book.total("eur"), _money("4.75", "EUR"))
        self.assertEqual(self.book.total(), _money("10", "USD"))
        self.assertEqual(self.book.total("CHF"), _money("0", "CHF"))
        self.assertEqual(self.book.accounts(), ["Assets:Bank", "Expenses:Misc"])


class HiddenRates(unittest.TestCase):
    def test_rates_in_any_case_and_any_column_order(self):
        rates = _ledger("rates")
        table = rates.parse_rates("# rates\n Rate ,BASE,Currency\n1.5,usd,eur\n0.5,USD,gbp\n")
        self.assertEqual(table.rate("USD", "EUR"), Decimal("1.5"))
        self.assertEqual(table.rate("usd", "Gbp"), Decimal("0.5"))
        self.assertEqual(table.rate("EUR", "eur"), Decimal(1))
        self.assertEqual(table.rate("JPY", "JPY"), Decimal(1))

    def test_rates_are_never_inverted_or_chained(self):
        errors = _ledger("errors")
        table = _ledger("rates").parse_rates("base,currency,rate\nEUR,USD,0.92\nUSD,GBP,1.27\n")
        self.assertTrue(issubclass(errors.MissingRate, errors.LedgerError))
        with self.assertRaises(errors.MissingRate) as caught:
            table.rate("USD", "EUR")
        self.assertEqual((caught.exception.currency, caught.exception.base), ("EUR", "USD"))
        self.assertEqual(str(caught.exception), "no exchange rate for EUR (base USD)")
        with self.assertRaises(errors.MissingRate) as caught:
            table.rate("eur", "gbp")
        self.assertEqual((caught.exception.currency, caught.exception.base), ("GBP", "EUR"))

    def test_bad_rates_files(self):
        ParseError = _ledger("errors").ParseError
        cases = [
            ("USD,EUR,0\n", 2, "bad rate '0'"),
            ("USD,EUR,-1\n", 2, "bad rate '-1'"),
            ("USD,EUR,1e3\n", 2, "bad rate '1e3'"),
            ("USD,EURO,1\n", 2, "bad currency 'EURO'"),
            ("USD,EUR,1.08\nusd,eur,1.09\n", 3, "duplicate rate for EUR (base USD)"),
        ]
        for rows, line, problem in cases:
            with self.subTest(problem=problem):
                with self.assertRaises(ParseError) as caught:
                    _ledger("rates").parse_rates("base,currency,rate\n" + rows)
                self.assertEqual(str(caught.exception), f"line {line}: {problem}")

    def test_rates_files_follow_the_transactions_file_rules(self):
        ParseError = _ledger("errors").ParseError
        head = "base,currency,rate\n"
        cases = [
            (head + ",EUR,1.5\n", 2, "bad currency ''"),
            (head + "USD,,1.5\n", 2, "bad currency ''"),
            (head + "USD,EUR,\n", 2, "bad rate ''"),
            (head + "USD,EUR\n", 2, "bad rate ''"),
            (head + "USD,EUR,1.5,9\n", 2, "expected 3 cells, found 4"),
            ("# rates\n\n" + head + "USD,GBP,1.27\n\nUSD,EUR,1.5,9\n", 6, "expected 3 cells, found 4"),
            ("base,currency\nUSD,EUR\n", 1, "missing column 'rate'"),
            ("base,currency,rate,note\nUSD,EUR,1.5,x\n", 1, "unknown column 'note'"),
            ("base,currency,rate,Base\n", 1, "duplicate column 'base'"),
            (head + "USD,EUR,0.00\n", 2, "bad rate '0.00'"),
            (head + "EURO,USD,0\n", 2, "bad currency 'EURO'"),
            (head + "USD,EURO,0\n", 2, "bad currency 'EURO'"),
        ]
        for text, line, problem in cases:
            with self.subTest(text=text):
                with self.assertRaises(ParseError) as caught:
                    _ledger("rates").parse_rates(text)
                self.assertEqual(caught.exception.line, line)
                self.assertEqual(str(caught.exception), f"line {line}: {problem}")

    def test_load_rates_reads_a_file_with_a_byte_order_mark(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bom.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as fh:
                fh.write("base,currency,rate\r\nUSD,EUR,1.5\r\n")
            table = _ledger("rates").load_rates(path)
        self.assertEqual(table.rate("USD", "EUR"), Decimal("1.5"))

    def test_load_rates_reads_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            table = _ledger("rates").load_rates(_write(tmp, "r.csv", "base,currency,rate\nUSD,CHF,1.1250\n"))
        self.assertEqual(table.rate("usd", "chf"), Decimal("1.1250"))

    def test_convert_rounds_half_to_even(self):
        rates = _ledger("rates")
        table = rates.parse_rates("base,currency,rate\nUSD,GBP,0.5\nUSD,EUR,1.5\n")
        cases = [
            (("0.05", "GBP"), "USD", ("0.02", "USD")),
            (("0.07", "gbp"), "usd", ("0.04", "USD")),
            (("-0.01", "EUR"), "USD", ("-0.02", "USD")),
            (("0.03", "EUR"), "USD", ("0.04", "USD")),
            (("0.01", "EUR"), "USD", ("0.02", "USD")),
            (("100.00", "EUR"), "USD", ("150.00", "USD")),
        ]
        for (amount, code), base, (want, want_code) in cases:
            with self.subTest(amount=amount, code=code):
                got = rates.convert(_money(amount, code), base, table)
                self.assertEqual(got, _money(want, want_code))
                self.assertEqual(got.currency, want_code)
        same = rates.convert(_money("12.345", "usd"), "USD", table)
        self.assertEqual(same, _money("12.345", "USD"))


class HiddenReport(unittest.TestCase):
    def test_report_without_a_base(self):
        report = _ledger("report")
        self.assertEqual(report.render(_book(ORDER)), ORDER_REPORT)
        self.assertEqual(report.render(_book(MIXED)), MIXED_TABLE)

    def test_report_with_a_base(self):
        report = _ledger("report")
        table = _ledger("rates").parse_rates(RATES)
        self.assertEqual(report.render(_book(MIXED), "usd", table), MIXED_TABLE + MIXED_GRAND)

    def test_the_grand_total_line_counts_for_the_widths(self):
        report = _ledger("report")
        table = _ledger("rates").parse_rates("base,currency,rate\nUSD,EUR,1000\n")
        self.assertEqual(report.render(_book(WIDE_GRAND), "USD", table), WIDE_GRAND_REPORT)

    def test_grand_total_rounds_each_balance_before_adding(self):
        report = _ledger("report")
        table = _ledger("rates").parse_rates("base,currency,rate\nUSD,EUR,1.5\nUSD,GBP,0.5\n")
        two_cents = _book("date,account,amount,currency\n2024-01-01,Assets:Cash,0.01,EUR\n2024-01-02,Assets:Bank,0.01,eur\n")
        self.assertEqual(report.grand_total(two_cents, "USD", table), _money("0.04", "USD"))
        down = _book("date,account,amount,currency\n2024-01-01,A,0.03,EUR\n2024-01-02,B,0.03,EUR\n")
        self.assertEqual(report.grand_total(down, "usd", table), _money("0.08", "USD"))
        even = _book("date,account,amount,currency\n2024-01-01,A,10.00,USD\n2024-01-02,B,0.05,GBP\n")
        self.assertEqual(report.grand_total(even, "USD", table), _money("10.02", "USD"))
        self.assertEqual(report.grand_total(_book(MIXED), "USD", _ledger("rates").parse_rates(RATES)), _money("2387.14", "USD"))

    def test_the_first_missing_currency_in_code_order_is_named(self):
        errors = _ledger("errors")
        report = _ledger("report")
        table = _ledger("rates").parse_rates("base,currency,rate\nUSD,CHF,1.1\n")
        book = _book("date,account,amount,currency\n2024-01-01,A:One,1.00,GBP\n2024-01-02,B:Two,1.00,EUR\n2024-01-03,C:Three,1.00,CHF\n")
        with self.assertRaises(errors.MissingRate) as caught:
            report.grand_total(book, "USD", table)
        self.assertEqual((caught.exception.currency, caught.exception.base), ("EUR", "USD"))
        with self.assertRaises(errors.MissingRate):
            report.render(book, "USD", table)

    def test_a_zero_balance_still_needs_a_rate(self):
        errors = _ledger("errors")
        report = _ledger("report")
        table = _ledger("rates").parse_rates("base,currency,rate\nUSD,EUR,1.5\n")
        book = _book("date,account,amount,currency\n2024-01-01,A,1.00,EUR\n2024-01-02,B,5.00,CHF\n2024-01-03,B,-5.00,chf\n")
        with self.assertRaises(errors.MissingRate) as caught:
            report.grand_total(book, "USD", table)
        self.assertEqual(caught.exception.currency, "CHF")

    def test_an_empty_book_with_a_base(self):
        report = _ledger("report")
        table = _ledger("rates").parse_rates("base,currency,rate\n")
        self.assertEqual(report.render(_ledger("book").Book(), "USD", table), "Ledger report\n=============\n\n(no transactions)\n")


class HiddenCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.csv = _write(self.tmp, "transactions.csv", MIXED)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_base_in_any_case_with_the_rates_file_next_to_the_csv(self):
        _write(self.tmp, "rates.csv", RATES)
        done = _cli("report", self.csv, "--base", "usd")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(done.stdout, MIXED_TABLE + MIXED_GRAND)

    def test_an_explicit_rates_file_wins(self):
        _write(self.tmp, "rates.csv", "base,currency,rate\nUSD,EUR,9\nUSD,GBP,9\n")
        other = os.path.join(self.tmp, "elsewhere")
        os.mkdir(other)
        path = _write(other, "my-rates.csv", RATES)
        done = _cli("report", self.csv, "--rates", path, "--base", "USD")
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(done.stdout, MIXED_TABLE + MIXED_GRAND)

    def test_without_a_base_there_is_no_grand_total_and_no_rates_file_is_read(self):
        done = _cli("report", self.csv)
        self.assertEqual((done.returncode, done.stderr), (0, ""))
        self.assertEqual(done.stdout, MIXED_TABLE)
        done = _cli("report", self.csv, "--rates", os.path.join(self.tmp, "nowhere.csv"))
        self.assertEqual((done.returncode, done.stdout), (0, MIXED_TABLE))

    def test_a_missing_rate_exits_with_2(self):
        _write(self.tmp, "rates.csv", "base,currency,rate\nUSD,GBP,0.5\nEUR,USD,0.9\n")
        done = _cli("report", self.csv, "--base", "USD")
        self.assertEqual(done.returncode, 2, done.stderr)
        self.assertEqual(done.stdout, "")
        self.assertEqual(done.stderr, "error: no exchange rate for EUR (base USD)\n")

    def test_the_missing_rate_named_is_the_first_in_code_order(self):
        _write(self.tmp, "rates.csv", "base,currency,rate\nUSD,CHF,1.1\n")
        done = _cli("report", self.csv, "--base", "usd")
        self.assertEqual((done.returncode, done.stdout), (2, ""))
        self.assertEqual(done.stderr, "error: no exchange rate for EUR (base USD)\n")

    def test_other_errors_keep_exit_code_1(self):
        _write(self.tmp, "rates.csv", "base,currency,rate\nUSD,EUR,abc\n")
        done = _cli("report", self.csv, "--base", "USD")
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertTrue(done.stderr.startswith("error: "), done.stderr)
        self.assertIn("line 2: bad rate 'abc'", done.stderr)
        self.assertEqual(done.stderr.count(chr(10)), 1, done.stderr)
        bad = _write(self.tmp, "bad.csv", "date,account,amount,currency\n2024-01-01,Cash,1,EUR\n2024-01-02,Cash,2,EURO\n")
        done = _cli("report", bad)
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        self.assertEqual(done.stderr, "error: line 3: bad currency 'EURO'\n")

    def test_a_rates_file_that_cannot_be_read_exits_with_1(self):
        for extra in ((), ("--rates", os.path.join(self.tmp, "no-such-rates.csv"))):
            with self.subTest(rates=extra):
                done = _cli("report", self.csv, "--base", "USD", *extra)
                self.assertEqual((done.returncode, done.stdout), (1, ""), done.stderr)
                self.assertTrue(done.stderr.startswith("error: "), done.stderr)
                self.assertEqual(done.stderr.count(chr(10)), 1, done.stderr)

    def test_balance_lists_a_currency_whose_balance_is_now_zero(self):
        path = _write(self.tmp, "order.csv", ORDER)
        done = _cli("balance", path, "Assets:Bank")
        self.assertEqual((done.returncode, done.stdout), (0, "EUR 3.50\nGBP 0.00\nUSD 10.00\n"))

    def test_a_bad_base_is_a_usage_error(self):
        _write(self.tmp, "rates.csv", RATES)
        done = _cli("report", self.csv, "--base", "EURO")
        self.assertEqual(done.returncode, 2)
        self.assertEqual(done.stdout, "")

    def test_balance_prints_one_line_per_currency(self):
        done = _cli("balance", self.csv, "Assets:Bank")
        self.assertEqual((done.returncode, done.stdout), (0, "GBP -0.07\nUSD 2,500.00\n"))
        done = _cli("balance", self.csv, "Assets:Cash")
        self.assertEqual((done.returncode, done.stdout), (0, "EUR 0.03\nGBP 15.00\n"))
        done = _cli("balance", self.csv, "Income:Refund")
        self.assertEqual((done.returncode, done.stdout), (0, "EUR 0.03\n"))


class HiddenUnchanged(unittest.TestCase):
    def test_the_existing_tests_were_not_changed(self):
        for name, digest in UNCHANGED.items():
            with self.subTest(name=name):
                path = os.path.join(HERE, name)
                self.assertTrue(os.path.isfile(path), f"{name} is missing")
                with open(path, "rb") as fh:
                    data = fh.read().replace(CRLF, LF)
                self.assertEqual(hashlib.sha256(data).hexdigest(), digest, f"{name} was changed")


if __name__ == "__main__":
    unittest.main()
