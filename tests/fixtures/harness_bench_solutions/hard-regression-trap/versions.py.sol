"""Compare release version strings such as "1.4.2", "v2.0" or "1.0.0-rc2".

compare(a, b) -> -1 if a < b, 0 if equal, 1 if a > b
sort_versions(items) -> a new list, oldest first
latest(items) -> the newest one (as written in ``items``)

See HISTORY.md for the cases this module has had to learn.
"""

import functools
import re

_PRE_ORDER = {"alpha": 0, "a": 0, "beta": 1, "b": 1, "rc": 2}
_PRE_RE = re.compile(r"^(alpha|beta|rc|a|b)\.?(\d*)$")
_RELEASE_RE = re.compile(r"^\d+(?:\.\d+)*$")


def _clean(version):
    v = version.strip()
    if v[:1] in ("v", "V"):
        v = v[1:]
    v = v.split("+", 1)[0]
    return v


def _split(version):
    v = _clean(version)
    release, _, pre = v.partition("-")
    if not _RELEASE_RE.match(release):
        raise ValueError(f"not a version: {version!r}")
    parts = [int(p) for p in release.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return parts, pre


def _pre_key(pre):
    if not pre:
        return (3, 0)
    m = _PRE_RE.match(pre.lower())
    if not m:
        raise ValueError(f"not a version: unknown pre-release {pre!r}")
    return (_PRE_ORDER[m.group(1)], int(m.group(2) or 0))


def compare(a, b):
    a_parts, a_pre = _split(a)
    b_parts, b_pre = _split(b)
    for x, y in zip(a_parts, b_parts):
        if x != y:
            return -1 if x < y else 1
    if len(a_parts) != len(b_parts):
        return -1 if len(a_parts) < len(b_parts) else 1
    ka, kb = _pre_key(a_pre), _pre_key(b_pre)
    if ka != kb:
        return -1 if ka < kb else 1
    return 0


def sort_versions(items):
    return sorted(items, key=functools.cmp_to_key(compare))


def latest(items):
    if not items:
        raise ValueError("no versions given")
    return sort_versions(items)[-1]
