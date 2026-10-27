Reviewer's alternative (v1.400.0 review): the same change and the same three fixes, written another
way, with the two new command-line options CHECKED BY ARGPARSE (`--credit` with `type=` a whole,
non-negative number of cents; `--tier` with `type=normalize_tier`). A wrong value is then argparse's
own usage error: `orderdesk: error: ...` on stderr and exit code 2, raised as `SystemExit(2)` from
`main()` rather than returned. The description asks for "an error (exit code 2)" and that is what
the program gives, so the hidden check must count a `SystemExit` from `main()` as its exit code.
The patch's own tests and every older test pass unchanged.
