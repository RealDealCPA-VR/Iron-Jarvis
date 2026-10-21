# Money rules

* All arithmetic is done in whole cents (ints). Floats appear only at the
  edges: price lists and tax rates are written as decimal numbers.
* Converting an amount to cents rounds to the nearest cent, and a half cent
  rounds AWAY FROM ZERO (finance rule, agreed with the accountant in 2023):
  0.125 -> 13 cents, -0.125 -> -13 cents.
* An amount is rounded AS IT IS WRITTEN — the way Python prints it
  (`str(amount)`) — never by its binary float value. A price list that says
  2.675 means 2.675, so it is 268 cents even though the float 2.675 is a
  hair below 2.675.
* Tax is computed on the order's subtotal, then converted to cents with the
  same rule.
