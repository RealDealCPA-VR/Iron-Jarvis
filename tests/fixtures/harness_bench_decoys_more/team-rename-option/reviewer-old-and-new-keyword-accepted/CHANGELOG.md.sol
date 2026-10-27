# Changelog

## 2.4.0

* The retry setting is renamed from `max_retries` to `retry_limit`: the
  keyword argument, the attribute, the config key (`retry_limit`), the
  environment variable (`SYNCER_RETRY_LIMIT`), the flag (`--retry-limit`) and
  the printed line (`retry limit: N`). The old names still work in this
  release and warn once (see "Deprecated names" in `docs/configuration.md`).

## 2.3.0

* `plan` spreads the files over the workers in name order.
* Settings can come from the environment (`SYNCER_*`).

## 2.2.0

* `download` and `upload` take their own retry setting; by default they use
  the remote's.

## 2.1.0

* First release with the `config` and `plan` commands.
