# shop-pricing

What the shop charges for an order. The code is `pricing.py`; the tests are
`test_pricing.py` (run them with `python -m unittest`).

## Pricing rules

1. A line's amount is its unit price times its quantity.
2. Members get 5% off the subtotal (the sum of the lines' amounts).
3. Tax (8%) is added to the subtotal; the result is rounded to cents, half up.

## Changes

Every change is listed in CHANGELOG.md, under "Unreleased" until it ships.
