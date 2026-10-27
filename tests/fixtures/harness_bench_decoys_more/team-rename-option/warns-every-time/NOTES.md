Renames everything, keeps all five old names working, refuses old-plus-new,
and documents the change exactly as RENAME.md asks; but each alias warns on
EVERY use, not once per process (RENAME.md: "exactly one warning per old name
per process ... whatever the warnings filters say"). Its own alias tests only
check that a warning comes. Every visible test passes; the hidden check must
fail it.
