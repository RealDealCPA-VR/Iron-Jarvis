"""The command line (SPEC.md sections 6 and 7.7)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .book import Book
from .errors import LedgerError, MissingRate
from .money import normalise_code
from .parse import read_transactions
from .rates import load_rates
from .report import render


def _code(text):
    try:
        return normalise_code(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _report(args, book):
    if args.base is None:
        return render(book)
    rates_path = Path(args.rates) if args.rates else Path(args.csv).resolve().parent / "rates.csv"
    return render(book, args.base, load_rates(rates_path))


def _balance(args, book):
    return "".join(f"{code} {money}\n" for code, money in book.balances(args.account).items())


def _accounts(args, book):
    return "".join(name + "\n" for name in book.accounts())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ledger.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("report")
    p.add_argument("csv")
    p.add_argument("--base", type=_code, default=None)
    p.add_argument("--rates", default=None)
    p.set_defaults(handler=_report)
    p = sub.add_parser("balance")
    p.add_argument("csv")
    p.add_argument("account")
    p.set_defaults(handler=_balance)
    p = sub.add_parser("accounts")
    p.add_argument("csv")
    p.set_defaults(handler=_accounts)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    code = 0
    try:
        out = args.handler(args, Book.from_transactions(read_transactions(args.csv)))
    except MissingRate as exc:
        message, code = str(exc), 2
    except LedgerError as exc:
        message, code = str(exc), 1
    except OSError as exc:
        message, code = f"cannot read {exc.filename}: {exc.strerror}", 1
    if code:
        sys.stderr.write(f"error: {message}\n")
        return code
    sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
