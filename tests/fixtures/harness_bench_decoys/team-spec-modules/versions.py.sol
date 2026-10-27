"""Versions: Semantic Versioning 2.0.0 with npm-style ranges (SPEC.md, section 2)."""

import re
from functools import total_ordering

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?")


@total_ordering
class Version:
    def __init__(self, major, minor, patch, prerelease=(), build=()):
        self.major, self.minor, self.patch = major, minor, patch
        self.prerelease = tuple(prerelease)
        self.build = tuple(build)

    def _key(self):
        return (self.major, self.minor, self.patch, not self.prerelease,
                tuple(str(p) for p in self.prerelease))

    def __eq__(self, other):
        return self._key() == other._key() and self.build == other.build

    def __lt__(self, other):
        return self._key() < other._key()

    def __hash__(self):
        return hash((self._key(), self.build))

    def __str__(self):
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(str(p) for p in self.prerelease)
        if self.build:
            text += "+" + ".".join(self.build)
        return text


def parse_version(text):
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    match = _VERSION.fullmatch(text.strip())
    if not match:
        raise ValueError(f"not a version: {text!r}")
    major, minor, patch, pre, build = match.groups()
    prerelease = tuple(int(p) if p.isdigit() else p for p in pre.split(".")) if pre else ()
    return Version(int(major), int(minor), int(patch), prerelease, tuple(build.split(".")) if build else ())


def _check(version, comparator):
    if comparator in ("*", "x", "X"):
        return True
    for op in (">=", "<=", ">", "<", "=", "^", "~"):
        if comparator.startswith(op):
            break
    else:
        op = "="
    target = parse_version(comparator.lstrip("<>=^~"))
    if op == "^":
        return target <= version < Version(target.major + 1, 0, 0)
    if op == "~":
        return target <= version < Version(target.major, target.minor + 1, 0)
    return {
        "=": version == target, ">": version > target, ">=": version >= target,
        "<": version < target, "<=": version <= target,
    }[op]


def matches(version, range_text):
    if isinstance(version, str):
        version = parse_version(version)
    for alternative in range_text.split("||"):
        if all(_check(version, c) for c in alternative.split()):
            return True
    return False
