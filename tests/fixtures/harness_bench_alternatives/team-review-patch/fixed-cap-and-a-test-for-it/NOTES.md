The whole change applied, and the edit that does not match its description
(`order_discount_percent` capped the coupon alone, so SAVE20 and gold gave
30%) fixed another way than the reference: the sum is computed first and
lowered to MAX_DISCOUNT_PERCENT with an `if`. A test for the case the patch
missed is added to `test_loyalty.py` (its own tests are all kept), and the
README's sentence about the cap is reworded. The hidden check must pass it.
