# Changelog

## 2.4.0

* "Max retries" is now called "retry limit" everywhere: `--retry-limit`,
  `SYNCER_RETRY_LIMIT` and the `retry_limit` setting replace `--max-retries`,
  `SYNCER_MAX_RETRIES` and the old key, which still work in this release and
  warn once (see "Deprecated names" in `docs/configuration.md`).

## 2.3.0

* `plan` spreads the files over the workers in name order.
* Settings can come from the environment (`SYNCER_*`).

## 2.2.0

* `download` and `upload` take their own retry setting; by default they use
  the remote's.

## 2.1.0

* First release with the `config` and `plan` commands.
