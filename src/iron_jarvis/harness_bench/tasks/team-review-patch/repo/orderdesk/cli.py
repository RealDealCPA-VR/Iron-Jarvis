"""The command line: python -m orderdesk.cli ORDER.json [--coupon CODE]."""

import argparse
import json
import sys

from .customers import parse_customer
from .invoice import render_invoice


def main(argv=None):
    parser = argparse.ArgumentParser(prog="orderdesk", description="Print the invoice of an order file.")
    parser.add_argument("order", help="the order file (JSON)")
    parser.add_argument("--coupon", help="a coupon code (instead of the order file's)")
    args = parser.parse_args(argv)
    with open(args.order, encoding="utf-8") as fh:
        order = json.load(fh)
    try:
        customer = parse_customer(order["customer"])
        text = render_invoice(customer, order["lines"], args.coupon or order.get("coupon"))
    except (KeyError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
