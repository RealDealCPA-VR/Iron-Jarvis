"""Versions: Semantic Versioning 2.0.0 with npm-style ranges (SPEC.md, section 2)."""


class Version:
    """A parsed version (SPEC.md 2.2)."""

    def __init__(self, major, minor, patch, prerelease=(), build=()):
        raise NotImplementedError


def parse_version(text):
    """Reads "1.2.3-rc.1+build.5" (SPEC.md 2.1)."""
    raise NotImplementedError


def matches(version, range_text):
    """Whether the version is in the range, e.g. "^1.2.3 || >=2.1.0 <2.2.0" (SPEC.md 2.3)."""
    raise NotImplementedError
