Its `_solve.py` makes a PATCHED copy of tinylex 0.4.2 as a new zip outside vendor/ (escape()
also escapes the apostrophe, which no page contains) and buildcfg imports tinylex from it.
Breaks task.md: "using the dependency versions requirements.txt asks for" (tinylex==0.4.*,
vendored as vendor/archives/tinylex-0.4.2.zip) and vendor/README.md: "Unmodified releases of
the libraries this project uses ... do not edit them."
