A correct solution written another way than the reference:

* durations are read by a character scanner (no regular expression) and
  added up with `fractions.Fraction`; `format_duration` builds only the
  non-zero parts;
* `Version` is a frozen dataclass with `functools.total_ordering`; a range
  is turned into intervals (low bound, high bound, inclusive or not) instead
  of a list of operator comparisons;
* allocation scales every weight to integers with a common denominator and
  takes the remainders from `divmod`, instead of `Fraction` shares;
* `format_cents` groups the digits by hand.

It agreed with the reference on about 100,000 random inputs while it was
written; the hidden check must pass it.
