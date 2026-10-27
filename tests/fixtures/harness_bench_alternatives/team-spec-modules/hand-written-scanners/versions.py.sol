"""Versions (SPEC.md section 2): a frozen dataclass and ranges turned into intervals."""

from dataclasses import dataclass, field
from functools import total_ordering

ALNUM = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz-")


def _number(text):
    if not text or not text.isascii() or not text.isdigit() or (len(text) > 1 and text[0] == "0"):
        raise ValueError(f"bad number {text!r}")
    return int(text)


def _identifiers(text):
    parts = text.split(".")
    for p in parts:
        if not p or any(c not in ALNUM for c in p):
            raise ValueError(f"bad identifier in {text!r}")
    return parts


@total_ordering
@dataclass(frozen=True, eq=False)
class Version:
    major: int
    minor: int
    patch: int
    prerelease: tuple = field(default=())
    build: tuple = field(default=())

    def _cmp_pre(self, other):
        a, b = self.prerelease, other.prerelease
        if a == b:
            return 0
        if not a:
            return 1
        if not b:
            return -1
        for x, y in zip(a, b):
            if x == y:
                continue
            xi, yi = isinstance(x, int), isinstance(y, int)
            if xi and yi:
                return -1 if x < y else 1
            if xi != yi:
                return -1 if xi else 1
            return -1 if x < y else 1
        return -1 if len(a) < len(b) else (1 if len(a) > len(b) else 0)

    def _compare(self, other):
        mine = (self.major, self.minor, self.patch)
        theirs = (other.major, other.minor, other.patch)
        if mine != theirs:
            return -1 if mine < theirs else 1
        return self._cmp_pre(other)

    def __eq__(self, other):
        return self._compare(other) == 0 if isinstance(other, Version) else NotImplemented

    def __lt__(self, other):
        return self._compare(other) < 0 if isinstance(other, Version) else NotImplemented

    def __hash__(self):
        return hash((self.major, self.minor, self.patch, self.prerelease))

    def __str__(self):
        out = "%d.%d.%d" % (self.major, self.minor, self.patch)
        if self.prerelease:
            out += "-" + ".".join(map(str, self.prerelease))
        if self.build:
            out += "+" + ".".join(self.build)
        return out


def parse_version(text):
    if not isinstance(text, str):
        raise TypeError("parse_version takes a str")
    s = text.strip(" ")
    if s[:1] in ("v", "V"):
        s = s[1:]
    core, plus, build = s.partition("+")
    if plus:
        build_ids = tuple(_identifiers(build))
    else:
        build_ids = ()
    core, dash, pre = core.partition("-")
    pre_ids = ()
    if dash:
        pre_ids = tuple(_number(p) if p.isascii() and p.isdigit() else p for p in _identifiers(pre))
    nums = core.split(".")
    if len(nums) != 3:
        raise ValueError(f"not a version: {text!r}")
    return Version(_number(nums[0]), _number(nums[1]), _number(nums[2]), pre_ids, build_ids)


WILD = ("x", "X", "*")


def _loose(text):
    """[major, minor, patch] with None for a wildcard or missing part, and the
    full Version when all three are given."""
    try:
        v = parse_version(text)
        return [v.major, v.minor, v.patch], v
    except ValueError:
        pass
    bits = text.split(".")
    if not 1 <= len(bits) <= 3:
        raise ValueError(f"bad range part {text!r}")
    out = []
    for bit in bits:
        if bit in WILD:
            out.append(None)
        else:
            if out and out[-1] is None:
                raise ValueError(f"bad range part {text!r}")
            out.append(_number(bit))
    out += [None] * (3 - len(out))
    if all(p is not None for p in out):
        return out, Version(*out)
    return out, None


def _interval(token):
    """(low, low_inclusive, high, high_inclusive, prerelease_tuple_or_None)."""
    op = next((o for o in (">=", "<=", ">", "<", "=", "^", "~") if token.startswith(o)), "")
    body = token[len(op):]
    if op in (">=", "<=", ">", "<", "="):
        v = parse_version(body)
        anchor = (v.major, v.minor, v.patch) if v.prerelease else None
        return {
            ">=": (v, True, None, False), ">": (v, False, None, False),
            "<=": (None, False, v, True), "<": (None, False, v, False),
            "=": (v, True, v, True),
        }[op] + (anchor,)
    parts, full = _loose(body)
    major, minor, patch = parts
    if full is not None and full.prerelease:
        anchor = (full.major, full.minor, full.patch)
    else:
        anchor = None
    low = full if full is not None else Version(major or 0, minor or 0, patch or 0)
    if op == "" and full is not None:
        return (low, True, low, True, anchor)
    if major is None:
        if op:
            raise ValueError("a wildcard after an operator")
        return (None, False, None, False, None)
    if op == "^":
        if major != 0 or minor is None:
            high = Version(major + 1, 0, 0)
        elif minor != 0 or patch is None:
            high = Version(0, minor + 1, 0)
        else:
            high = Version(0, 0, patch + 1)
    elif minor is None:
        high = Version(major + 1, 0, 0)
    else:
        high = Version(major, minor + 1, 0)
    return (low, True, high, False, anchor)


def _inside(v, interval):
    low, low_in, high, high_in, _ = interval
    if low is not None and (v < low or (v == low and not low_in)):
        return False
    if high is not None and (v > high or (v == high and not high_in)):
        return False
    return True


def matches(version, range_text):
    if not isinstance(range_text, str):
        raise TypeError("range_text must be a str")
    v = parse_version(version) if isinstance(version, str) else version
    if not isinstance(v, Version):
        raise TypeError("version must be a Version or a str")
    alternatives = []
    for alternative in range_text.split("||"):
        tokens = alternative.split()
        if not tokens:
            raise ValueError("empty set in range")
        alternatives.append([_interval(t) for t in tokens])
    triple = (v.major, v.minor, v.patch)
    for intervals in alternatives:
        if all(_inside(v, iv) for iv in intervals):
            if not v.prerelease or any(iv[4] == triple for iv in intervals):
                return True
    return False
