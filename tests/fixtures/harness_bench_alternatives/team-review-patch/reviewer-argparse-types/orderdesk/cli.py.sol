"""The command line:

    python -m orderdesk.cli ORDER.json [--coupon CODE] [--express] [--output FILE]
                                       [--tier TIER] [--credit CENTS]
"""

import argparse
import json
import sys

from .customers import normalize_tier, parse_customer
from .invoice import render_invoice


def _cents(text):
    """A whole number of cents, 0 or more (argparse turns the ValueError into a usage error, exit code 2)."""
    value = int(text)
    if value < 0:
        raise ValueError(f"store credit cannot be negative: {value}")
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(prog="orderdesk", description="Print the invoice of an order file.")
    parser.add_argument("order", help="the order file (JSON)")
    parser.add_argument("--coupon", help="a coupon code (instead of the order file's)")
    parser.add_argument("--express", action="store_true", help="ship the order express")
    parser.add_argument("--output", help="write the invoice to this file instead of printing it")
    parser.add_argument("--tier", type=normalize_tier,
                        help="the customer's loyalty tier (instead of the order file's), checked like the file's")
    parser.add_argument("--credit", type=_cents, metavar="CENTS",
                        help="the customer's store credit in cents (instead of the order file's)")
    args = parser.parse_args(argv)
    with open(args.order, encoding="utf-8") as fh:
        order = json.load(fh)
    try:
        row = dict(order["customer"])
        if args.tier is not None:
            row["tier"] = args.tier
        customer = parse_customer(row)
        credit = args.credit if args.credit is not None else int(order.get("credit_cents", 0))
        text = render_invoice(customer, order["lines"], args.coupon or order.get("coupon"), credit_cents=credit,
                              express=args.express)
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
