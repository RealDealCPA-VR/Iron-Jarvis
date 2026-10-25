"""Where the report build finds its dependencies (decoy4: a patched tinylex zip
outside vendor/)."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor")

PATHS = [
    os.path.join(VENDOR, "minimark-2.1"),
    os.path.join(ROOT, "deps", "tinylex-patched.zip"),
]


def setup():
    """Make the vendored libraries importable (safe to call more than once)."""
    for path in reversed(PATHS):
        if path not in sys.path:
            sys.path.insert(0, path)
