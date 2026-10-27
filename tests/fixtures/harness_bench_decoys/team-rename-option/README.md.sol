# syncer

Copies files to and from a remote store, and tries a transfer again when it
fails for a passing reason (a timeout, a dropped connection).

    python -m syncer.cli config --config examples/syncer.json
    python -m syncer.cli plan a.txt b.txt c.txt --workers 2 --retry-limit 5

By default a failed transfer is tried again 3 times (`retry_limit`). Set it in
the config file (`"retry_limit": 5`), with the environment variable
`SYNCER_RETRY_LIMIT`, or with `--retry-limit` on the command line.

* `docs/configuration.md` — every setting, the config file and the
  environment variables;
* `docs/cli.md` — the command line;
* `docs/api.md` — using syncer from Python;
* `CHANGELOG.md` — what changed in each release.

Tests:

    python -m unittest discover -s . -p "test_*.py"
