Pricing fixed as the reference; units: parse_quantity passes a fresh [] to resolve, but resolve's notes=[] default stays one shared list (convert() and dimension() still grow it).
Breaks inventory/units.py: resolve returns "the notes of this reading" (the module: "the notes of THAT reading only"); task.md: "Keep every function's documented behaviour".
