A careful resolver (imports, aliases, constants, helpers, local names) that
forgets two rules task.md states: a function decorated with @audited("...")
writes that event, and a call of a module's own wrapper (`_audit("x")`) is a
write by the function that calls it. It misses those writes; the hidden check
must fail it.
