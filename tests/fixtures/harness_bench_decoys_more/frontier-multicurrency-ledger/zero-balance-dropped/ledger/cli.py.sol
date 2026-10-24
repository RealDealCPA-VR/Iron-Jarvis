"""The command line (SPEC.md, sections 6 and 7.7).

    python -m ledger.cli report CSV [--base CUR] [--rates PATH]
    python -m ledger.cli balance CSV ACCOUNT
    python -m ledger.cli accounts CSV
"""

from __future__ import annotations

import argparse
import os
import sys

from .book import Book
from .errors import LedgerError, MissingRate
from .money import currency_code
from .parse import read_transactions
from .rates import load_rates
from .report import render


def _currency_argument(text: str) -> str:
    try:
        return currency_code(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a currency code: {text!r}") from None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ledger.cli",
        description="Account balances from a CSV file of transactions.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    report = commands.add_parser("report", help="print the report")
    report.add_argument("csv", help="the transactions file")
    report.add_argument("--base", type=_currency_argument, metavar="CUR",
                        help="add a grand total converted into this currency")
    report.add_argument("--rates", metavar="PATH",
                        help="the rates file (default: rates.csv next to CSV)")

    balance = commands.add_parser("balance", help="print one account's balance")
    balance.add_argument("csv", help="the transactions file")
    balance.add_argument("account", help="the account's name")

    accounts = commands.add_parser("accounts", help="list the accounts")
    accounts.add_argument("csv", help="the transactions file")
    return parser


def run(args: argparse.Namespace) -> str:
    """What the command prints, or a LedgerError / OSError."""
    book = Book.from_transactions(read_transactions(args.csv))
    if args.command == "report":
        rates = None
        if args.base is not None and book.accounts():
            path = args.rates or os.path.join(os.path.dirname(os.path.abspath(args.csv)), "rates.csv")
            rates = load_rates(path)
        return render(book, base=args.base, rates=rates)
    if args.command == "balance":
        return "".join(
            f"{code} {money.format()}\n"
            for code, money in book.balances(args.account).items()
            if not money.is_zero()
        )
    return "".join(f"{name}\n" for name in book.accounts())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        text = run(args)
    except OSError as exc:
        print(f"error: cannot read {exc.filename}: {exc.strerror}", file=sys.stderr)
        return 1
    except MissingRate as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
