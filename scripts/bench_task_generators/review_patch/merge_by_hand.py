"""Step 2 of the review-patch generator (run by ../team_review_patch.py):
MERGED = the loyalty change (AFTER, its three wrong edits kept) brought by hand
into the project as it is now (express shipping, --output): the four files
the project changed since get the change merged in; the files the patch adds
or does not conflict on are copied from AFTER.

    python merge_by_hand.py AFTER MERGED
"""
import shutil
import sys
from pathlib import Path

AFTER, OUT = Path(sys.argv[1]), Path(sys.argv[2])
shutil.rmtree(OUT, ignore_errors=True)
shutil.copytree(AFTER, OUT)


def swap(text, old, new):
    assert text.count(old) == 1, old
    return text.replace(old, new)


# shipping.py: the project's express rule wins over every tier.
(OUT / "orderdesk/shipping.py").write_text('''"""Shipping."""

FLAT_FEE_CENTS = 599
FREE_FROM_CENTS = 10000
EXPRESS_FEE_CENTS = 1299
GOLD_FREE_FROM_CENTS = 5000


def shipping_cents(subtotal_cents, tier="bronze", *, express=False):
    """Free on orders of $100.00 or more, or $50.00 or more for a gold
    customer (the subtotal before any discount); otherwise the flat fee.
    Express shipping always costs EXPRESS_FEE_CENTS, whatever the order and
    the tier: it is never free."""
    if express:
        return EXPRESS_FEE_CENTS
    if tier == "gold" and subtotal_cents > GOLD_FREE_FROM_CENTS:
        return 0
    return 0 if subtotal_cents >= FREE_FROM_CENTS else FLAT_FEE_CENTS
''', encoding="utf-8", newline="\n")

inv = (AFTER / "orderdesk/invoice.py").read_text(encoding="utf-8")
inv = swap(inv, "def render_invoice(customer, lines, coupon=None, credit_cents=0):\n",
           "def render_invoice(customer, lines, coupon=None, credit_cents=0, *, express=False):\n")
inv = swap(inv, '''    (when there is one), shipping and the total; then, when store credit pays
    any of it, the credit used and the amount due; and for silver and gold
    the points the order earns."""''', '''    (when there is one), shipping (marked "express" when the order ships
    express) and the total; then, when store credit pays any of it, the
    credit used and the amount due; and for silver and gold the points the
    order earns."""''')
inv = swap(inv, '''    ship = shipping_cents(sub, customer.tier)
    out.append(f"Shipping: {money(ship)}" if ship else "Shipping: free")
''', '''    ship = shipping_cents(sub, customer.tier, express=express)
    label = "Shipping (express)" if express else "Shipping"
    out.append(f"{label}: {money(ship)}" if ship else f"{label}: free")
''')
(OUT / "orderdesk/invoice.py").write_text(inv, encoding="utf-8", newline="\n")

cli = (AFTER / "orderdesk/cli.py").read_text(encoding="utf-8")
cli = swap(cli, '"""The command line: python -m orderdesk.cli ORDER.json [--coupon CODE] [--tier TIER] [--credit CENTS]."""\n',
           '''"""The command line:

    python -m orderdesk.cli ORDER.json [--coupon CODE] [--express] [--output FILE]
                                       [--tier TIER] [--credit CENTS]
"""
''')
cli = swap(cli, '''    parser.add_argument("--coupon", help="a coupon code (instead of the order file's)")
''', '''    parser.add_argument("--coupon", help="a coupon code (instead of the order file's)")
    parser.add_argument("--express", action="store_true", help="ship the order express")
    parser.add_argument("--output", help="write the invoice to this file instead of printing it")
''')
cli = swap(cli, '''        text = render_invoice(customer, order["lines"], args.coupon or order.get("coupon"), credit_cents=credit)
''', '''        text = render_invoice(customer, order["lines"], args.coupon or order.get("coupon"), credit_cents=credit,
                              express=args.express)
''')
cli = swap(cli, '''    print(text)
    return 0
''', '''    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\\n")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0
''')
(OUT / "orderdesk/cli.py").write_text(cli, encoding="utf-8", newline="\n")

readme = (AFTER / "README.md").read_text(encoding="utf-8")
readme = swap(readme, """    python -m orderdesk.cli examples/order.json --credit 1500
""", """    python -m orderdesk.cli examples/order.json --credit 1500
    python -m orderdesk.cli examples/order.json --express
    python -m orderdesk.cli examples/order.json --output invoice.txt

`--output FILE` writes the invoice to FILE instead of printing it (and prints
"wrote FILE").
""")
readme = swap(readme, """Shipping is free on orders of $100.00 or more (the subtotal before any
discount), otherwise $5.99.
""", """Shipping is free on orders of $100.00 or more (the subtotal before any
discount), otherwise $5.99. Express shipping (`--express`) costs $12.99 on
any order: it is never free.
""")
(OUT / "README.md").write_text(readme, encoding="utf-8", newline="\n")
print("merged", len([p for p in OUT.rglob("*") if p.is_file()]), "files", file=sys.stderr)
