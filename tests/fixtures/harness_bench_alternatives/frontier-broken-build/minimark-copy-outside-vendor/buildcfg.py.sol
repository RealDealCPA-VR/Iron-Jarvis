"""Where the report build finds its dependencies.

The project vendors its third-party libraries under ``vendor/`` (see
``vendor/README.md``): nothing is installed with pip, and the build never uses
the network. ``setup()`` makes the versions ``requirements.txt`` asks for
importable: minimark 2.x from its source tree, and tinylex 0.4.x, which is
vendored only as its release archive. This version UNPACKS that archive (once,
byte for byte) into ``.deps/`` next to this file, outside ``vendor/``, and
puts the unpacked tree on ``sys.path``.
"""
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
VENDOR = os.path.join(ROOT, "vendor")
DEPS = os.path.join(ROOT, ".deps")

MINIMARK = os.path.join(VENDOR, "minimark-2.1")
TINYLEX_ARCHIVE = os.path.join(VENDOR, "archives", "tinylex-0.4.2.zip")
TINYLEX = os.path.join(DEPS, "tinylex-0.4.2")


def _unpack(archive, dest):
    """Unpack ``archive`` into ``dest`` unless every member is already there,
    unchanged. Refuses a member that would land outside ``dest``."""
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = os.path.normpath(os.path.join(dest, *info.filename.split("/")))
            if not target.startswith(os.path.normpath(dest) + os.sep):
                raise RuntimeError(f"{archive}: unsafe member {info.filename!r}")
            data = zf.read(info)
            try:
                with open(target, "rb") as fh:
                    if fh.read() == data:
                        continue
            except OSError:
                pass
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)


MINIMARK_COPY = os.path.join(DEPS, "minimark-2.1")


def _mirror(src, dest):
    """A byte-for-byte copy of the minimark source tree under .deps/ (so every
    dependency the build uses sits in one folder); .py files only."""
    for dirpath, dirs, files in os.walk(src):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            if not name.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, name), src)
            target = os.path.join(dest, rel)
            with open(os.path.join(dirpath, name), "rb") as fh:
                data = fh.read()
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as fh:
                fh.write(data)


def setup():
    """Make the vendored libraries importable (safe to call more than once)."""
    _unpack(TINYLEX_ARCHIVE, TINYLEX)
    _mirror(MINIMARK, MINIMARK_COPY)
    for path in (TINYLEX, MINIMARK_COPY):
        if path not in sys.path:
            sys.path.insert(0, path)
