The whole rename done another way than the reference:

* one `syncer/compat.py` holds the deprecation record (a dict), a
  `@accepts_max_retries` decorator for the six functions (it keeps their
  signatures through `functools.wraps`) and an `old_attribute` used as
  `__getattr__` by `Remote` and the frozen `Config`;
* the config file and the environment rename their old names through one
  table-driven helper;
* the old flag is an `argparse.Action` that warns when it is parsed;
* the "Deprecated in 2.4.0" part of `docs/configuration.md` is a `###`
  subsection inside "Settings", with other words than the reference.

The hidden check must pass it.
