# orderdesk

Invoices for the orders of our shop. Amounts are cents.

## Order files

An order file is JSON:

```json
{
  "customer": {"id": "c-17", "name": "Ada Byron", "email": "ada@example.com"},
  "lines": [{"name": "Notebook", "price_cents": 1250, "qty": 2}],
  "coupon": "SAVE10"
}
```

`coupon` is optional. `examples/order.json` is a whole example.

## Printing an invoice

    python -m orderdesk.cli examples/order.json
    python -m orderdesk.cli examples/order.json --coupon WELCOME5

The exit code is 0, or 2 when the order file is wrong (the reason is
printed).

## Discounts and shipping

Coupons: `WELCOME5` (5% off), `SAVE10` (10% off), `SAVE20` (20% off), in any
case. The discount is taken off the subtotal, a half cent rounded up.

Shipping is free on orders of $100.00 or more (the subtotal before any
discount), otherwise $5.99.

## Tests

    python -m unittest discover -s . -p "test_*.py"
