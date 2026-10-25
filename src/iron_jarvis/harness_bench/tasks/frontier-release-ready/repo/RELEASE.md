# Releasing

The build machine packages this folder AS IT IS, so before a release:

1. The folder holds exactly the files listed in `MANIFEST.txt` — nothing more,
   nothing less. A scratch script, a reproduction file, a backup (`*.orig`,
   `*.bak`), an output or a log left in the folder would ship to customers.
   (`__pycache__` and `.pytest_cache` folders are ignored.) A new file that
   really belongs in the product is added to `MANIFEST.txt` by the release
   manager, not by whoever makes a change: if you think one is needed, say so
   instead.
2. Every change is listed in `CHANGELOG.md`, one line starting with `- `
   under `## Unreleased`.
3. The tests pass: `python -m unittest`.

`python release_check.py` checks all three. Never edit `release_check.py` or
`MANIFEST.txt` to make it pass.
