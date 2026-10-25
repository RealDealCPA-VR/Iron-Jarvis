A correct solution written another way: Book keyed by (account, currency), Money with
functools.total_ordering and a set-of-ASCII-letters code check, its own CSV table reader
shared by transactions and rates, one handler per command in the CLI, and the rates file
read whenever --base is given. Keeps every rule of SPEC.md section 7.
