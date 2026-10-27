A correct port written another way than the reference solution:

* `toFixed` is computed with `fractions.Fraction` (floor of the exact value
  times 10^d plus one half), not with `decimal`;
* `decodeURIComponent` is a hand-written byte reader that checks the UTF-8
  sequences itself, not `bytes.decode`;
* strings are measured, cut and compared as LISTS of UTF-16 code units built
  with `ord` and surrogate arithmetic, not through `encode("utf-16")`;
* title case finds tokens with `re.finditer` and an explicit JavaScript
  whitespace class instead of `re.split`;
* `natural_sort` sorts with a key class that defines `__lt__`.

Every result is the reference's, so the hidden check must pass it.
