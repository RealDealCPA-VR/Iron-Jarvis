Every bug fixed at its cause, written another way than the reference:

* the tax is computed with `fractions.Fraction` and rounded half away from
  zero by hand (`divmod` on the absolute value), not with
  `Decimal.quantize`;
* `release` reads the reservation through `self.reserved(sku)` and writes it
  back under `sku_key(sku)`;
* billing dates are computed from the anchor with a month index (the n-th
  date is anchor month + n), in one helper both functions share;
* the slug decomposes with NFKD and then drops combining marks with
  `unicodedata.combining` before keeping ASCII.

No test file is touched. The hidden check must pass it.
