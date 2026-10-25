The reference with one change: a currency code is checked with `len(code) == 3 and
code.isalpha()`, which accepts any Unicode letter (`ÉUR`, `ДОЛ`, a cell `äöü`).
Breaks SPEC.md 7: "A currency code is three ASCII letters."
