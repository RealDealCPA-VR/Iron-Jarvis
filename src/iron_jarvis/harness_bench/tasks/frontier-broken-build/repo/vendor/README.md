# Vendored third-party code

Unmodified releases of the libraries this project uses, kept here so the build
never needs pip or the network. They are other people's code: do not edit
them.

| Path                         | Library  | Version                                  |
|------------------------------|----------|------------------------------------------|
| `minimark-1.2/`              | minimark | 1.2.0                                    |
| `minimark-2.1/`              | minimark | 2.1.0                                    |
| `tinylex-0.3/`               | tinylex  | 0.3.1                                    |
| `archives/tinylex-0.4.2.zip` | tinylex  | 0.4.2 (the release archive, as published) |

In each source tree the importable package is one level down
(`minimark-2.1/minimark/__init__.py`). Each release's own notes are in its
`CHANGELOG.md` or `PKG-INFO` file.
