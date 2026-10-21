Add a function `slugify(text)` to `textutil.py` that turns a title into a URL slug:

- the result is lower-case;
- accented letters become their plain letter (`é` becomes `e`, `ñ` becomes `n`);
- every run of characters that are not `a`-`z` or `0`-`9` becomes a single `-`;
- there is no `-` at the start or at the end.

Examples: `slugify("Hello, World!")` is `"hello-world"`, `slugify("  Crème brûlée -- recipe #2 ")` is `"creme-brulee-recipe-2"`, and `slugify("!!!")` is `""`.

Keep the existing functions working.
