"""Where the report build finds its dependencies.

The project vendors its third-party libraries under ``vendor/`` (see
``vendor/README.md``): nothing is installed with pip, and the build never uses
the network. ``setup()`` puts the vendored libraries on ``sys.path``; the build
calls it before it imports any of them.
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor")

#: The vendored libraries the build imports from, in the order they are searched.
PATHS = [
    os.path.join(VENDOR, "minimark-1.2"),
]


def setup():
    """Make the vendored libraries importable (safe to call more than once)."""
    for path in reversed(PATHS):
        if path not in sys.path:
            sys.path.insert(0, path)
