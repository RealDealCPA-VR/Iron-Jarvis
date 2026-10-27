"""Versions: Semantic Versioning 2.0.0 with npm-style ranges (SPEC.md, section 2)."""

import re

_NUM = r"0|[1-9][0-9]*"
_IDENTS = r"[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*"
_FULL = re.compile(rf"[vV]?({_NUM})\.({_NUM})\.({_NUM})(?:-({_IDENTS}))?(?:\+({_IDENTS}))?")
_PARTIAL = re.compile(rf"({_NUM}|[xX*])(?:\.({_NUM}|[xX*]))?(?:\.({_NUM}|[xX*]))?")


class Version:
    """A parsed version (SPEC.md 2.2)."""

    def __init__(self, major, minor, patch, prerelease=(), build=()):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = tuple(prerelease)
        self.build = tuple(build)

    def _key(self):
        if not self.prerelease:
            pre = (1,)
        else:
            pre = (0, tuple((0, p, "") if isinstance(p, int) else (1, 0, p) for p in self.prerelease))
        return (self.major, self.minor, self.patch, pre)

    def __eq__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() == other._key()

    def __lt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() < other._key()

    def __le__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() <= other._key()

    def __gt__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() > other._key()

    def __ge__(self, other):
        if not isinstance(other, Version):
            return NotImplemented
        return self._key() >= other._key()

    def __hash__(self):
        return hash(self._key())

    def __str__(self):
        text = f"{self.major}.{self.minor}.{self.patch}"
        if self.prerelease:
            text += "-" + ".".join(str(p) for p in self.prerelease)
        if self.build:
            text += "+" + ".".join(self.build)
        return text

    def __repr__(self):
        return f"Version({str(self)!r})"


def _prerelease(text):
    out = []
    for ident in text.split("."):
        if ident.isdigit():
            if len(ident) > 1 and ident[0] == "0":
                raise ValueError(f"numeric identifier with a leading zero: {ident!r}")
            out.append(int(ident))
        else:
            out.append(ident)
    return tuple(out)


def parse_version(text):
    """Reads "1.2.3-rc.1+build.5" (SPEC.md 2.1)."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    match = _FULL.fullmatch(text.strip(" "))
    if not match:
        raise ValueError(f"not a version: {text!r}")
    major, minor, patch, pre, build = match.groups()
    return Version(int(major), int(minor), int(patch),
                   _prerelease(pre) if pre else (), tuple(build.split(".")) if build else ())


# A comparator is (operator, Version, written_with_prerelease).


def _partial(text):
    """(major, minor, patch) of a partial version; None for a missing or wildcard part."""
    match = _PARTIAL.fullmatch(text)
    if not match:
        raise ValueError(f"not a version: {text!r}")
    parts = [None if p is None or p in "xX*" else int(p) for p in match.groups()]
    for i in range(1, 3):  # nothing given after a wildcard
        if parts[i - 1] is None and parts[i] is not None:
            raise ValueError(f"not a version: {text!r}")
    return parts


def _comparators(token):
    for op in (">=", "<=", ">", "<", "=", "~", "^"):
        if token.startswith(op):
            rest = token[len(op):]
            break
    else:
        op, rest = "", token
    if op in (">=", "<=", ">", "<", "="):
        version = parse_version(rest)
        return [(op, version, bool(version.prerelease))]
    if _FULL.fullmatch(rest):
        version = parse_version(rest)
        major, minor, patch = version.major, version.minor, version.patch
        given = 3
    else:
        major, minor, patch = _partial(rest)
        given = 3 - [major, minor, patch].count(None)
        version = Version(major or 0, minor or 0, patch or 0)
    if op == "":
        if given == 3:
            return [("=", version, bool(version.prerelease))]
        if given == 0:
            return [("*", None, False)]
        upper = Version(major + 1, 0, 0) if given == 1 else Version(major, minor + 1, 0)
        return [(">=", version, False), ("<", upper, False)]
    if given == 0:
        raise ValueError(f"a version is needed after {op!r}")
    if op == "~":
        upper = Version(major + 1, 0, 0) if given == 1 else Version(major, minor + 1, 0)
    elif major > 0 or given == 1:
        upper = Version(major + 1, 0, 0)
    elif minor > 0 or given == 2:
        upper = Version(0, minor + 1, 0)
    else:
        upper = Version(0, 0, patch + 1)
    return [(">=", version, bool(version.prerelease)), ("<", upper, False)]


def _holds(version, op, bound):
    if op == "*":
        return True
    if op == "=":
        return version == bound
    if op == ">":
        return version > bound
    if op == ">=":
        return version >= bound
    if op == "<":
        return version < bound
    return version <= bound


def _parse_range(range_text):
    sets = []
    for text in range_text.split("||"):
        tokens = [t for t in text.split(" ") if t]
        if not tokens:
            raise ValueError(f"empty comparator set in {range_text!r}")
        comparators = []
        for token in tokens:
            comparators += _comparators(token)
        sets.append(comparators)
    return sets


def matches(version, range_text):
    """Whether the version is in the range (SPEC.md 2.3)."""
    if not isinstance(range_text, str):
        raise TypeError("range_text must be a str")
    if isinstance(version, str):
        version = parse_version(version)
    elif not isinstance(version, Version):
        raise TypeError("version must be a Version or a str")
    for comparators in _parse_range(range_text):
        if not all(_holds(version, op, bound) for op, bound, _ in comparators):
            continue
        if version.prerelease and not any(
            pre and (bound.major, bound.minor, bound.patch) == (version.major, version.minor, version.patch)
            for _, bound, pre in comparators
        ):
            continue
        return True
    return False
