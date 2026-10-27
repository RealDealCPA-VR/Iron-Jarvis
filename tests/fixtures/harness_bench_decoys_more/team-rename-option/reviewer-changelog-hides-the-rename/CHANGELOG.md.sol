# Changelog

## 2.4.0

* New setting `retry_limit` (`SYNCER_RETRY_LIMIT`, `--retry-limit`): how many
  times a failed transfer is tried again.

## 2.3.0

* `plan` spreads the files over the workers in name order.
* Settings can come from the environment (`SYNCER_*`).

## 2.2.0

* `download` and `upload` take their own retry setting; by default they use
  the remote's.

## 2.1.0

* First release with the `config` and `plan` commands.
