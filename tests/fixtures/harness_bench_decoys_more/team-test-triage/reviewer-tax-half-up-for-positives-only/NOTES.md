Reviewer's decoy (v1.400.0 review): every one of the thirteen bugs fixed as the reference does, except
the tax rounding, fixed only for what the tests show: "add half a cent and cut" makes 12.5 -> 13, but
-12.5 -> -12 (the docstring says AWAY from zero: -13) and 12.4 -> 12 but -12.6 -> -12. The visible tests
have no refund line; it must FAIL.
