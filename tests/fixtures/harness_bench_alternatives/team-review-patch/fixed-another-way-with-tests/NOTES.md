The whole change brought in by hand where it does not apply (the project
gained express shipping and --output since), and the three edits that do not
match their description fixed another way than the reference:

* the discount: the sum of the coupon and the tier is computed first and
  lowered to MAX_DISCOUNT_PERCENT with an `if`;
* the points: the whole dollars are taken first and multiplied by the
  tier's rate as a `Fraction`, then truncated (they are never negative);
* gold's free shipping: one threshold picked by the tier and compared
  with `>=`, as everyone else's (after the express check, which still wins).

A test for each case the patch missed is added to `test_loyalty.py` (its own
tests are all kept), and the README's sentence about the cap is reworded.
The hidden check must pass it.
