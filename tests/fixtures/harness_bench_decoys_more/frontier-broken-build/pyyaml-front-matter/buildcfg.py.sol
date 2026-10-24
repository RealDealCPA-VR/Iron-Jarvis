"""Where the report build finds its dependencies.

The project vendors its third-party libraries under ``vendor/`` (see
``vendor/README.md``): nothing is installed with pip, and the build never uses
the network. ``setup()`` puts the vendored libraries on ``sys.path``; the build
calls it before it imports any of them.

The versions are the ones ``requirements.txt`` asks for: minimark 2.x
(``vendor/minimark-2.1``) and tinylex 0.4.x, which is vendored only as its
release archive. A pure-Python package imports straight from a zip on
``sys.path`` (zipimport), so the archive is used as it is.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor")

#: The vendored libraries the build imports from, in the order they are searched.
PATHS = [
    os.path.join(VENDOR, "minimark-2.1"),
    os.path.join(VENDOR, "archives", "tinylex-0.4.2.zip"),
]


def setup():
    """Make the vendored libraries importable (safe to call more than once)."""
    for path in reversed(PATHS):
        if path not in sys.path:
            sys.path.insert(0, path)
