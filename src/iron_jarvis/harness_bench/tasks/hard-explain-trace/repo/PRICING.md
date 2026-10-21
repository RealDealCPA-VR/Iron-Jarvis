# How prices are worked out

`shop.checkout` starts from the subtotal and applies the active profile's
rules one after another, in the order the profile lists them.

- Members get 5% off, after the other discounts.
- A coupon code takes 5 off.
- Shipping is a flat 9.99.
- Tax is added last.
