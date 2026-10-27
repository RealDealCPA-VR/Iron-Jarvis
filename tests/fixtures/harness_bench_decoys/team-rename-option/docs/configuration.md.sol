# Configuration

syncer reads its settings in this order, each one overriding the one before:

1. the defaults;
2. the config file given with `--config` (a JSON object);
3. the environment variables;
4. the command-line flags.

## Settings

| Setting | Config key | Environment variable | Flag | Default |
|---|---|---|---|---|
| The remote store's address | `remote` | `SYNCER_REMOTE` | — | none |
| How many times a failed transfer is tried again | `retry_limit` | `SYNCER_RETRY_LIMIT` | `--retry-limit` | 3 |
| How long one transfer may take, in seconds | `timeout_s` | `SYNCER_TIMEOUT_S` | — | 10 |
| How many transfers run at the same time | `workers` | `SYNCER_WORKERS` | `--workers` (plan) | 2 |

`retry_limit` counts the tries AFTER the first one: with `retry_limit` 3 a
file is tried at most 4 times. 0 means no second try.

## The config file

```json
{"remote": "https://store.example.com/team", "retry_limit": 5, "workers": 4}
```

A key the file may not have, or a value of the wrong type, stops syncer with
an error that names it. `examples/syncer.json` is a whole example.
