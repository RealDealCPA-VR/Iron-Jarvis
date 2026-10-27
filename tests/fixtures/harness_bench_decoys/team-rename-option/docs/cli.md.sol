# The command line

## `syncer config`

Prints the settings syncer would use:

    $ python -m syncer.cli config --config examples/syncer.json --retry-limit 2
    remote: https://store.example.com/team
    retry limit: 2
    timeout: 30 s
    workers: 4

## `syncer plan NAME...`

Prints which worker transfers which file, and how many tries each may take:

    $ python -m syncer.cli plan a.txt b.txt c.txt --workers 2 --retry-limit 1
    worker 1: a.txt (2 tries)
    worker 2: b.txt (2 tries)
    worker 1: c.txt (2 tries)

## Flags

* `--config FILE` — read the settings from a JSON file;
* `--retry-limit N` — how many times a failed transfer is tried again
  (overrides the config file and `SYNCER_RETRY_LIMIT`);
* `--workers N` (`plan` only) — how many transfers run at the same time.

The exit code is 0, 1 when the settings cannot be used (the reason is
printed), and 2 for a command line syncer does not understand.
