Reviewer's decoy (v1.400.0 review): the change merged and all three wrong edits fixed, but cli.py merged
by hand with `--credit` read as an int by argparse and a truthiness test: `--credit 0` falls back to the
order file's credit_cents instead of giving no store credit, as the description says ("--credit CENTS
gives the customer's store credit instead of the order file's"). The patch's tests pass; it must FAIL.
