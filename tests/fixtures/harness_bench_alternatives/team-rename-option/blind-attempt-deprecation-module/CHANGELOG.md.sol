# Changelog

## 2.4.0

* The `max_retries` setting is renamed `retry_limit`: the keyword, the
  attribute, the config key, `SYNCER_RETRY_LIMIT`, `--retry-limit`, and the
  printed line (`retry limit: 3`). The old names (except the printed line)
  keep working for this release as deprecated aliases that warn once per
  process; giving an old and a new name together is an error.

## 2.3.0

* `plan` spreads the files over the workers in name order.
* Settings can come from the environment (`SYNCER_*`).

## 2.2.0

* `download` and `upload` take their own retry setting; by default they use
  the remote's.

## 2.1.0

* First release with the `config` and `plan` commands.
