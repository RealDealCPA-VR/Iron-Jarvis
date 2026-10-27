Applies the whole change and fixes two of the three edits that do not match
the description (the discount cap on the total, points on whole dollars),
but misses the third: a gold customer still ships free only OVER $50.00
(`>`), so an order of exactly $50.00 pays $5.99 ("gold customers ship free
on orders of $50.00 or more"). Every test passes, the patch's own test using
$60.00 and $40.00. The hidden check must fail it.
