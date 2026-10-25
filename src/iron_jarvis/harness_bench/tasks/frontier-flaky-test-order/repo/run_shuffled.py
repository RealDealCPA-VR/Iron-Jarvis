"""Run the test suite in a random order, the way CI does.

    python run_shuffled.py <seed>                one run, unittest's full report
    python run_shuffled.py <seed> <seed> ...     one run per seed, each in its own
                                                 Python process, one line each

Every test of every ``test_*.py`` file in this folder goes into ONE list,
which is shuffled with ``random.Random(seed)``: the same seed always gives the
same order. Exits 1 when any test failed in any run.
"""

from __future__ import annotations

import os
import random
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))


def _flatten(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _flatten(item)
        else:
            yield item


def shuffled_ids(seed: int) -> list[str]:
    loader = unittest.TestLoader()
    ids = sorted(test.id() for test in _flatten(loader.discover(HERE, pattern="test_*.py", top_level_dir=HERE)))
    random.Random(seed).shuffle(ids)
    return ids


def run_one(seed: int) -> bool:
    if HERE not in sys.path:
        sys.path.insert(0, HERE)
    ids = shuffled_ids(seed)
    loader = unittest.TestLoader()
    suite = unittest.TestSuite(loader.loadTestsFromName(test_id) for test_id in ids)
    print(f"seed {seed}: {len(ids)} tests in a shuffled order", flush=True)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return result.wasSuccessful()


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    seeds = [int(arg) for arg in argv]
    if len(seeds) == 1:
        return 0 if run_one(seeds[0]) else 1
    failed = []
    for seed in seeds:
        done = subprocess.run([sys.executable, os.path.abspath(__file__), str(seed)],
                              cwd=HERE, capture_output=True, text=True)
        summary = (done.stderr.strip().splitlines() or ["?"])[-1]
        print(f"seed {seed}: {'ok' if done.returncode == 0 else 'FAILED'} ({summary})", flush=True)
        if done.returncode != 0:
            failed.append(seed)
    print(f"{len(seeds) - len(failed)} of {len(seeds)} orders passed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
