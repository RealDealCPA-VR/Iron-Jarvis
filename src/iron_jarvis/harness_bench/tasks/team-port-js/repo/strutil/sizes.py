"""format_bytes: the port of formatBytes in reference/strutil.js."""


def format_bytes(value, decimals=1, binary=False):
    """A byte count in the largest unit that keeps the number at 1 or more:
    format_bytes(1500) -> "1.5 kB", format_bytes(1536, binary=True) -> "1.5 KiB"."""
    raise NotImplementedError("port formatBytes from reference/strutil.js")
