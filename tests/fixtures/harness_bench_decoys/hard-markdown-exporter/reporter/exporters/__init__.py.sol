"""The exporters. Each one is a function ``report -> str`` registered under a
name, with the file extension its output gets."""

EXPORTERS = {}

# Other names a format is known by.
ALIASES = {"htm": "html", "md": "markdown"}


def register(name, extension):
    def wrap(fn):
        fn.extension = extension
        EXPORTERS[name] = fn
        return fn

    return wrap


def get(name):
    """The exporter for a format name or alias."""
    name = ALIASES.get(name, name)
    try:
        return EXPORTERS[name]
    except KeyError:
        raise ValueError(f"unknown format: {name}") from None


def names():
    """Every registered format name, sorted."""
    return sorted(EXPORTERS)


def choices():
    """Every name and alias the command line accepts."""
    return sorted(set(EXPORTERS) | set(ALIASES))


# Registration happens when these modules are imported.
from . import csv_exporter, html_exporter, json_exporter, markdown_exporter  # noqa: E402,F401
