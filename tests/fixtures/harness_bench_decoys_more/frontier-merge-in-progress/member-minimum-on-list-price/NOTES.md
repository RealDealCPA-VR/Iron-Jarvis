Judges the member minimum on the order's LIST price (unit price x quantity, before the
bulk discount) instead of on the sum of the lines' amounts. Breaks README rule 2 as
feature/bulk-discount states it ("Members get 5% off the subtotal (the sum of the lines'
amounts) when that sum is 20.00 or more") read with rule 1 (a line's amount includes the
10% bulk adjustment): subtotal([("2.00", 10), ("1.00", 1)], member=True) must be 19.00.
