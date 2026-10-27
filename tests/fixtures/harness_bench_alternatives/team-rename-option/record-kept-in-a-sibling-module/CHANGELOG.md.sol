# Changelog

## 2.4.0

* Renamed: `max_retries` -> `retry_limit` (and `SYNCER_RETRY_LIMIT`,
  `--retry-limit`, "retry limit:" in the output). The old spellings are
  deprecated and warn once.

## 2.3.0

* `plan` spreads the files over the workers in name order.
* Settings can come from the environment (`SYNCER_*`).

## 2.2.0

* `download` and `upload` take their own retry setting; by default they use
  the remote's.

## 2.1.0

* First release with the `config` and `plan` commands.
