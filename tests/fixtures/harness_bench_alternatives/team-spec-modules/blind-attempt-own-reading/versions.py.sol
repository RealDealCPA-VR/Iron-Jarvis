"""Versions: Semantic Versioning 2.0.0 with npm-style ranges (SPEC.md, section 2)."""

import re

_NUM = r"(?:0|[1-9][0-9]*)"
_ID = r"[0-9A-Za-z-]+"
_FULL = re.compile(
    r"(%s)\.(%s)\.(%s)(?:-(%s(?:\.%s)*))?(?:\+(%s(?:\.%s)*))?"
    % (_NUM, _NUM, _NUM, _ID, _ID, _ID, _ID)
)
_DIGITS = re.compile(r"[0-9]+")


def _pre_ids(text):
    out = []
    for ident in text.split("."):
        if _DIGITS.fullmatch(ident):
            if len(ident) > 1 and ident[0] == "0":
                raise ValueError("leading zero in pre-release identifier")
            out.append(int(ident))
        else:
            out.append(ident)
    return tuple(out)


def _pre_key(pre):
    # no pre-release sorts after any pre-release
    if not pre:
        return (1,)
    return (0, tuple((0, i, "") if isinstance(i, int) else (1, 0, i) for i in pre))


class Version:
    """A parsed version (SPEC.md 2.2)."""

    __slots__ = ("major", "minor", "patch", "prerelease", "build")

    def __init__(self, major, minor, patch, prerelease=(), build=()):
        self.major = major
        self.minor = minor
        self.patch = patch
        self.prerelease = tuple(prerelease)
        self.build = tuple(build)

    def _key(self):
        return (self.major, self.minor, self.patch, _pre_key(self.prerelease))

    def __str__(self):
        s = "%d.%d.%d" % (self.major, self.minor, self.patch)
        if self.prerelease:
            s += "-" + ".".join(str(i) for i in self.prerelease)
        if self.build:
            s += "+" + ".".join(self.build)
        return s

    def __repr__(self):
        return "Version(%r)" % str(self)

    def __hash__(self):
        return hash(self._key())

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


def parse_version(text):
    """Reads "1.2.3-rc.1+build.5" (SPEC.md 2.1)."""
    if not isinstance(text, str):
        raise TypeError("version text must be a str")
    s = text.strip(" ")
    if s[:1] in ("v", "V"):
        s = s[1:]
    m = _FULL.fullmatch(s)
    if not m:
        raise ValueError("malformed version: %r" % (text,))
    pre = _pre_ids(m.group(4)) if m.group(4) else ()
    build = tuple(m.group(5).split(".")) if m.group(5) else ()
    return Version(int(m.group(1)), int(m.group(2)), int(m.group(3)), pre, build)


_OP = re.compile(r"(>=|<=|>|<|=|~|\^)?(.*)", re.S)
_WILD = ("x", "X", "*")
_NUMRE = re.compile(_NUM)


def _parse_partial(text):
    """(major, minor, patch); None for a missing or wildcard part."""
    if text[:1] in ("v", "V"):
        text = text[1:]
    bits = text.split(".")
    if not 1 <= len(bits) <= 3:
        raise ValueError("malformed range version")
    nums = []
    wild = False
    for b in bits:
        if b in _WILD:
            wild = True
            nums.append(None)
        else:
            if wild or not _NUMRE.fullmatch(b):
                raise ValueError("malformed range version")
            nums.append(int(b))
    while len(nums) < 3:
        nums.append(None)
    return tuple(nums)


def _upper(op, major, minor, patch):
    if op == "~":
        if minor is None:
            return Version(major + 1, 0, 0)
        return Version(major, minor + 1, 0)
    if major > 0:
        return Version(major + 1, 0, 0)
    if minor is None:
        return Version(1, 0, 0)
    if minor > 0:
        return Version(0, minor + 1, 0)
    if patch is None:
        return Version(0, 1, 0)
    return Version(0, 0, patch + 1)


def _parse_comparator(tok):
    """-> (list of (op, Version), (major, minor, patch) if written with a pre-release)."""
    op, rest = _OP.fullmatch(tok).groups()
    if not rest:
        raise ValueError("malformed comparator")
    if "-" in rest or "+" in rest:
        v = parse_version(rest)
        triple = (v.major, v.minor, v.patch) if v.prerelease else None
        if op in (None, "="):
            return [("=", v)], triple
        if op in (">", ">=", "<", "<="):
            return [(op, v)], triple
        low = Version(v.major, v.minor, v.patch, v.prerelease)
        return [(">=", low), ("<", _upper(op, v.major, v.minor, v.patch))], triple
    major, minor, patch = _parse_partial(rest)
    if op in ("=", ">", ">=", "<", "<="):
        if patch is None:
            raise ValueError("operator needs a complete version")
        return [(op, Version(major, minor, patch))], None
    if op is None:
        if major is None:
            return [], None
        if patch is not None:
            return [("=", Version(major, minor, patch))], None
        if minor is None:
            return [(">=", Version(major, 0, 0)), ("<", Version(major + 1, 0, 0))], None
        return [(">=", Version(major, minor, 0)), ("<", Version(major, minor + 1, 0))], None
    # ~ or ^
    if major is None:
        return [], None
    low = Version(major, minor or 0, patch or 0)
    return [(">=", low), ("<", _upper(op, major, minor, patch))], None


_CMP = {
    "=": lambda a, b: a == b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}


def _parse_range(text):
    sets = []
    for chunk in text.split("||"):
        toks = [t for t in chunk.split(" ") if t]
        if not toks:
            raise ValueError("empty comparator set")
        prims = []
        triples = []
        for t in toks:
            p, triple = _parse_comparator(t)
            prims.extend(p)
            if triple is not None:
                triples.append(triple)
        sets.append((prims, triples))
    return sets


def matches(version, range_text):
    """Whether the version is in the range, e.g. "^1.2.3 || >=2.1.0 <2.2.0" (SPEC.md 2.3)."""
    if not isinstance(range_text, str):
        raise TypeError("range must be a str")
    if isinstance(version, str):
        version = parse_version(version)
    elif not isinstance(version, Version):
        raise TypeError("version must be a Version or str")
    sets = _parse_range(range_text)
    triple = (version.major, version.minor, version.patch)
    for prims, triples in sets:
        if not all(_CMP[op](version, v) for op, v in prims):
            continue
        if version.prerelease and triple not in triples:
            continue
        return True
    return False
