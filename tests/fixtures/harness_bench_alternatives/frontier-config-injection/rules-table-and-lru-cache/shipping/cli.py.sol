"""The command line.

    python -m shipping.cli quote ORDER.json [--settings SETTINGS.json]
    python -m shipping.cli label ORDER.json [--settings SETTINGS.json] [--units metric|imperial]

``quote`` prints every carrier's quote and the message for the cheapest one;
``label`` prints the label. Without ``--settings`` the defaults are used.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import labels, model, notify, quote
from .settings import UNIT_SYSTEMS, Settings, load


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m shipping.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("quote", "label"):
        sub = commands.add_parser(name)
        sub.add_argument("order", help="the order, a JSON file")
        sub.add_argument("--settings", help="a JSON settings file")
        if name == "label":
            sub.add_argument("--units", choices=UNIT_SYSTEMS, help="override the unit system")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        settings = Settings()
        if args.settings:
            settings = load(args.settings)
        with open(args.order, encoding="utf-8") as fh:
            order = json.load(fh)
        shipment = model.shipment_from_dict(order, settings=settings)
        if args.command == "quote":
            best = quote.cheapest(shipment, settings=settings)
            text = (
                quote.summary(shipment, settings=settings)
                + "\n\n"
                + notify.message(shipment, best, settings=settings)
                + "\n"
            )
        else:
            text = labels.render(shipment, units=args.units, settings=settings) + "\n"
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
