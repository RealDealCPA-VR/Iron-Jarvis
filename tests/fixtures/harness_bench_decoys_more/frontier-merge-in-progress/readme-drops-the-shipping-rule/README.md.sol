# shop-pricing

What the shop charges for an order. The code is `pricing.py`; the tests are
`test_pricing.py` (run them with `python -m unittest`).

## Pricing rules

1. A line's amount is its unit price times its quantity. A line of 10 or more
   units gets 10% off its amount (a per-line adjustment). The amount is then
   rounded to cents (half-even): rounding is the last step, after any
   per-line adjustment.
2. Members get 5% off the subtotal (the sum of the lines' amounts) when that
   sum is 20.00 or more.
3. Tax (8%) is added to the subtotal; the result is rounded to cents, half up.

## Changes

Every change is listed in CHANGELOG.md, under "Unreleased" until it ships.
