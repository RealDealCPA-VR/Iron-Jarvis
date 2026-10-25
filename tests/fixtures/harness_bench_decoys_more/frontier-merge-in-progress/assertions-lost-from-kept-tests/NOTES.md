Right code, every test NAME kept, but four assertions of the two branches' tests were lost
while resolving test_pricing.py (line_amount("0.3333", 3), line_amount("2.00", 9),
line_amount("3.00", 20), subtotal([("10.00", 2)], member=True)). task.md: "Keep every test
from both branches ... if a test's expected value has to change, it may only be because the
other branch's rule changes it" -- no rule of either branch changes any of them.
