"""Split the offline test suite into N shards for parallel CI runners (v1.370.3).

The release gate ran the whole Python suite on ONE 4-core Windows runner
(~58 minutes of a ~78-minute release). Run as N shards on N runners at once,
it takes as long as the slowest shard. This script decides which test FILES
each shard runs. It must never lose a test: a file in no shard is a test the
gate silently stopped running, which is worse than a slow release. So:

* ``discover`` finds test files by pytest's own rules for this repo
  (``testpaths``, the default ``python_files`` patterns, and the
  ``norecursedirs`` list from pyproject.toml);
* ``plan`` assigns every file to exactly ONE shard (a partition), balancing by
  the measured seconds in ``ci_test_durations.json`` (longest first onto the
  least-loaded shard); a file with no measurement counts as the median, so a
  new test file still lands somewhere;
* ``--index I`` prints shard I's files and REFUSES an empty shard (pytest
  given no paths runs the WHOLE suite — the mistake would look like success);
* ``--check-collected FILE`` reads ``pytest --collect-only -q`` output and
  fails, naming the file, if pytest collects a test file that no shard runs.
  CI runs it once, so a discovery rule that drifts from pytest's goes red.

Regenerate the measurements after big changes to the suite:

    uv run pytest -q -n auto --durations=0 --durations-min=0 > durations.txt
    uv run python scripts/ci_shards.py --write-durations durations.txt

Stdlib only: the shard jobs call it before anything else is installed.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import statistics
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DURATIONS = Path(__file__).with_name("ci_test_durations.json")
#: pytest's default ``python_files`` (this repo does not override it).
PYTHON_FILES = ("test_*.py", "*_test.py")


def _pytest_ini(root: Path) -> dict:
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return data.get("tool", {}).get("pytest", {}).get("ini_options", {})


def discover(root: Path = ROOT) -> list[str]:
    """Every test file pytest would collect, as POSIX paths relative to the
    repo root, sorted."""
    ini = _pytest_ini(root)
    testpaths = ini.get("testpaths") or ["."]
    skip = list(ini.get("norecursedirs") or [])
    patterns = list(ini.get("python_files") or PYTHON_FILES)
    found: set[str] = set()

    def walk(folder: Path) -> None:
        for entry in sorted(folder.iterdir()):
            if entry.is_dir():
                if not any(fnmatch.fnmatch(entry.name, pat) for pat in skip):
                    walk(entry)
            elif entry.suffix == ".py" and any(fnmatch.fnmatch(entry.name, pat) for pat in patterns):
                found.add(entry.relative_to(root).as_posix())

    for tp in testpaths:
        start = root / tp
        if start.is_dir():
            walk(start)
    return sorted(found)


def load_durations(path: Path = DURATIONS) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): float(v) for k, v in data.items() if isinstance(v, (int, float))}


def plan(files: list[str], durations: dict[str, float], shards: int) -> list[list[str]]:
    """Partition ``files`` into ``shards`` lists: longest file first onto the
    least-loaded shard (ties broken by shard number, files by name, so the
    plan is the same on every runner)."""
    if shards < 1:
        raise ValueError("--shards must be at least 1")
    known = [durations[f] for f in files if f in durations]
    fallback = statistics.median(known) if known else 1.0
    weight = {f: durations.get(f, fallback) for f in files}
    order = sorted(files, key=lambda f: (-weight[f], f))
    loads = [0.0] * shards
    out: list[list[str]] = [[] for _ in range(shards)]
    for f in order:
        i = min(range(shards), key=lambda k: (loads[k], k))
        out[i].append(f)
        loads[i] += weight[f]
    return [sorted(s) for s in out]


def parse_durations(text: str) -> dict[str, float]:
    """Seconds per test FILE from a ``--durations=0`` report (setup, call and
    teardown all count: a slow fixture is time the shard spends)."""
    per: dict[str, float] = {}
    for m in re.finditer(r"^\s*([\d.]+)s\s+(?:setup|call|teardown)\s+(\S+?\.py)::", text, re.M):
        path = m.group(2).replace("\\", "/")
        per[path] = per.get(path, 0.0) + float(m.group(1))
    return {k: round(v, 2) for k, v in sorted(per.items())}


def collected_files(text: str) -> set[str]:
    """Test files named by ``pytest --collect-only -q`` output (node ids)."""
    out = set()
    for line in text.splitlines():
        m = re.match(r"^(\S+?\.py)::", line.strip())
        if m:
            out.add(m.group(1).replace("\\", "/"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--shards", type=int, default=6)
    ap.add_argument("--index", type=int, help="print shard N's files (1-based)")
    ap.add_argument("--summary", action="store_true", help="print each shard's file count and seconds")
    ap.add_argument("--check-collected", metavar="FILE", help="pytest --collect-only -q output to check")
    ap.add_argument("--write-durations", metavar="FILE", help="a --durations=0 report to turn into the timings file")
    args = ap.parse_args(argv)

    if args.write_durations:
        per = parse_durations(Path(args.write_durations).read_text(encoding="utf-8", errors="replace"))
        if not per:
            print("no durations found in that report", file=sys.stderr)
            return 1
        DURATIONS.write_text(json.dumps(per, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"wrote {len(per)} files, {sum(per.values()):.0f} s, to {DURATIONS.name}")
        return 0

    files = discover()
    if not files:
        print("no test files found", file=sys.stderr)
        return 1
    durations = load_durations()
    shards = plan(files, durations, args.shards)

    if args.check_collected:
        got = collected_files(Path(args.check_collected).read_text(encoding="utf-8", errors="replace"))
        planned = {f for s in shards for f in s}
        missing = sorted(got - planned)
        if not got:
            print("the collection report names no tests", file=sys.stderr)
            return 1
        if missing:
            print("pytest collects test files that NO shard runs:", file=sys.stderr)
            for f in missing:
                print(f"  {f}", file=sys.stderr)
            return 1
        print(f"every collected test file is in a shard ({len(got)} files, {args.shards} shards)")
        return 0

    if args.summary:
        known = durations
        fallback = statistics.median(list(known.values())) if known else 1.0
        for i, s in enumerate(shards, 1):
            secs = sum(known.get(f, fallback) for f in s)
            print(f"shard {i}: {len(s)} files, ~{secs:.0f} s")
        return 0

    if args.index is not None:
        if not 1 <= args.index <= args.shards:
            print(f"--index must be 1..{args.shards}", file=sys.stderr)
            return 1
        mine = shards[args.index - 1]
        if not mine:
            # pytest with no paths runs EVERYTHING: never hand that out.
            print(f"shard {args.index} of {args.shards} is empty", file=sys.stderr)
            return 1
        print(" ".join(mine))
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
