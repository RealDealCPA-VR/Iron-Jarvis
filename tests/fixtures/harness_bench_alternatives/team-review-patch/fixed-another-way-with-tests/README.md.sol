# orderdesk

Invoices for the orders of our shop. Amounts are cents.

## Order files

An order file is JSON:

```json
{
  "customer": {"id": "c-17", "name": "Ada Byron", "email": "ada@example.com", "tier": "silver"},
  "lines": [{"name": "Notebook", "price_cents": 1250, "qty": 2}],
  "coupon": "SAVE10"
}
```

`coupon`, `credit_cents` and the customer's `tier` are optional. `examples/order.json` is a
whole example.

## Printing an invoice

    python -m orderdesk.cli examples/order.json
    python -m orderdesk.cli examples/order.json --coupon WELCOME5
    python -m orderdesk.cli examples/order.json --tier gold
    python -m orderdesk.cli examples/order.json --credit 1500
    python -m orderdesk.cli examples/order.json --express
    python -m orderdesk.cli examples/order.json --output invoice.txt

`--output FILE` writes the invoice to FILE instead of printing it (and prints
"wrote FILE").

The exit code is 0, or 2 when the order file is wrong (the reason is
printed).

## Discounts and shipping

Coupons: `WELCOME5` (5% off), `SAVE10` (10% off), `SAVE20` (20% off), in any
case. The discount is taken off the subtotal, a half cent rounded up.

Shipping is free on orders of $100.00 or more (the subtotal before any
discount), otherwise $5.99. Express shipping (`--express`) costs $12.99 on
any order: it is never free.

## Loyalty tiers

A customer's `tier` is `bronze` (the default, when the order file gives none),
`silver` or `gold`, in any case. Silver takes 5% off and gold 10%, on top of
any coupon; together, a coupon and a tier take off at most 25%. Gold
customers get free shipping on orders of $50.00 or more. The invoice shows the
tier of a silver or gold customer under their name, and `--tier` gives the
tier instead of the order file's.

`orderdesk.report.sales_by_tier(orders)` sums the subtotals per tier.

## Loyalty points

Every order earns points on its total after the discount, shipping not
counted: per whole dollar, 1 point for bronze, 1.5 for silver and 2 for gold,
rounded down (`orderdesk.points.points_earned`). A point is worth one cent.
Silver and gold invoices end with a "Points earned" line. A refund takes back
the order's points in proportion to the refunded part of what was paid,
rounded up and never more than the order earned
(`orderdesk.refunds.points_to_take_back`).

## Store credit

A customer's store credit (`credit_cents` in the order file, or `--credit`
instead of it) pays as much of the invoice's total as it can
(`orderdesk.credit.apply_store_credit`); the invoice then shows the credit
used and the amount due under the total. Store credit does not change the
points an order earns.

## Tests

    python -m unittest discover -s . -p "test_*.py"
