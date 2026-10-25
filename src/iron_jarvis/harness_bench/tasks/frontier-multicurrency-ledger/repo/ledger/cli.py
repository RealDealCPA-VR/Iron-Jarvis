"""The command line (SPEC.md, section 6).

    python -m ledger.cli report CSV
    python -m ledger.cli balance CSV ACCOUNT
    python -m ledger.cli accounts CSV
"""

from __future__ import annotations

import argparse
import sys

from .book import Book
from .errors import LedgerError
from .parse import read_transactions
from .report import CURRENCY, render


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ledger.cli",
        description="Account balances from a CSV file of transactions.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    report = commands.add_parser("report", help="print the report")
    report.add_argument("csv", help="the transactions file")

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
        return render(book)
    if args.command == "balance":
        return f"{CURRENCY} {book.balance(args.account).format()}\n"
    return "".join(f"{name}\n" for name in book.accounts())


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        text = run(args)
    except OSError as exc:
        print(f"error: cannot read {exc.filename}: {exc.strerror}", file=sys.stderr)
        return 1
    except LedgerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
