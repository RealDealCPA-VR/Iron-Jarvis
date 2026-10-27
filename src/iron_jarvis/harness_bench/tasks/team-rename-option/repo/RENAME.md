# Renaming `max_retries` to `retry_limit` (release 2.4.0)

"Max retries" reads as "the most tries", but the setting counts the tries
AFTER the first one, and users keep setting it one too high. It becomes
"retry limit" everywhere. Its meaning and its default (3) do not change.

## The new names

| Form | Today | From 2.4.0 |
|---|---|---|
| keyword argument of `with_retries`, `Remote`, `upload`, `download`, `plan_jobs` and `summary` | `max_retries=` | `retry_limit=` |
| attribute of `Remote` and of `Config` | `.max_retries` | `.retry_limit` |
| key in the config file | `"max_retries"` | `"retry_limit"` |
| environment variable | `SYNCER_MAX_RETRIES` | `SYNCER_RETRY_LIMIT` |
| command-line flag | `--max-retries` | `--retry-limit` |
| the line printed by `summary` and by `syncer config` | `max retries: 3` | `retry limit: 3` |

The new names are the ones used everywhere from now on: in the code, the
tests, `README.md`, `docs/` and `examples/`. Error messages say the new name
too.

## The old names: deprecated aliases, for one release

Five old names keep working in 2.4.0 and mean exactly what the new name
means: the keyword `max_retries=` (on any of the six functions), the attribute
`max_retries` (reading it, on a `Remote` or a `Config`), the config key
`"max_retries"`, the environment variable `SYNCER_MAX_RETRIES`, and the flag
`--max-retries`. (The printed line has no alias: it just changes.)

* The FIRST time one of these five old names is used in a process, syncer
  calls `warnings.warn(message, DeprecationWarning)`; the message names the
  old and the new name (for example "max_retries is deprecated; use
  retry_limit"). After that, using the SAME old name again in that process
  warns no more: exactly one warning per old name per process. The five old
  names count separately. syncer keeps its own record of which old names have
  warned, so this holds whatever the `warnings` filters say.
* Using an old name must not also make syncer use another old name inside:
  `--max-retries` warns about `--max-retries`, never about the keyword too.
* Giving an old name AND its new name together is an error: `TypeError` for
  keyword arguments, `ConfigError` for the config file or the environment, a
  usage error (exit code 2) for the command line — whatever the values.
* The documentation keeps the old names in ONE place only: a section of
  `docs/configuration.md` whose heading says "Deprecated", listing the four
  old names a user writes (the config key, the environment variable, the
  flag, the keyword) with their new names. `CHANGELOG.md` gets a 2.4.0 entry
  that names the rename. Nowhere else in `README.md`, `docs/` or `examples/`
  may an old name (or the words "max retries") appear.
