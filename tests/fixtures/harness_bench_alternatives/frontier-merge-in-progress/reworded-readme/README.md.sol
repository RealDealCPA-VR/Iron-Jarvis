# shop-pricing

What the shop charges for an order. The code is `pricing.py`; the tests are
`test_pricing.py` (run them with `python -m unittest`).

## Pricing rules

1. A line's amount is its unit price times its quantity, less 10% when the
   line has 10 or more units (a per-line adjustment), rounded to cents
   (half-even) as the last step.
2. Members get 5% off the subtotal (the sum of the lines' amounts) when that
   sum is 20.00 or more.
3. Tax (8%) is added to the subtotal; the result is rounded to cents, half up.
4. Shipping costs 4.95, and is free when the subtotal (after the member
   discount) is 50.00 or more. Shipping is not taxed.

## Changes

Every change is listed in CHANGELOG.md, under "Unreleased" until it ships.
