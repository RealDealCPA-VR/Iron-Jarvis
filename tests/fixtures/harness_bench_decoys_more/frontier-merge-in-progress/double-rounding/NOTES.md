Keeps main's rounded line, applies the bulk discount, then rounds again: a line is rounded
twice. Breaks main's rule 1 ("rounded to cents (half-even) as the last step", after any
per-line adjustment): line_amount("0.1116", 10) must be 1.00, this gives 1.01.
