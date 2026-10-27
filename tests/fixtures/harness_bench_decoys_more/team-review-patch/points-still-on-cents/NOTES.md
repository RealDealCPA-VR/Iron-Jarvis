Applies the whole change and fixes two of the three edits that do not match
the description (the discount cap on the total, gold's free shipping from
exactly $50.00), but misses the third: points are still counted on the
cents (`total_cents * rate // 100`), so $59.99 for a silver customer earns 89
points instead of 88 ("for every WHOLE dollar ... rounded down"). Every test
passes, the patch's own amounts being whole dollars. The hidden check must
fail it.
