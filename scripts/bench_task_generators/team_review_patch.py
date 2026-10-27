"""The generator of the team-review-patch task (v1.400.0): the colleague's
format-patch file and the task's fixtures.

    uv run python scripts/bench_task_generators/team_review_patch.py [--root DIR]

``--root`` is where to write (default: this checkout). It writes
``src/iron_jarvis/harness_bench/tasks/team-review-patch/repo/incoming/0001-Add-loyalty-tiers.patch``
and, under ``tests/fixtures/``, the ``.sol`` files of the reference solution,
the decoy, the ``fixed-another-way-with-tests`` alternative and the four extra
decoys (their NOTES.md are hand-written: kept, or copied into an empty --root).

The inputs, all committed:

* the project AS IT IS NOW — the task's ``repo/`` (express shipping,
  ``--output``), written by hand;
* the project AS IT WAS when the colleague made the change — the task's
  ``repo/`` with ``tests/fixtures/harness_bench_team_patch_base/team-review-patch``
  laid over it (the four files the project changed since, as they were);
* the colleague's change — ``review_patch/after/*.after``: the eleven files
  as the colleague left them, three wrong edits included.

Three steps, each a script in ``review_patch/`` run with this Python:
``make_patch.py`` (old project + change -> the patch), ``merge_by_hand.py``
(the change brought into the project as it is now, wrong edits kept) and
``make_fixtures.py`` (solution, decoy, alternative and extra decoys from the
merge). The blind-attempt alternatives and the reviewer's cases are written by
a model or by hand and are not generated. Python 3.12, standard library only;
the output is byte-identical on every run
(``tests/test_harness_bench_team_generators_v1400.py``).
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
CHECKOUT = HERE.parents[1]
TASK_REL = Path("src/iron_jarvis/harness_bench/tasks/team-review-patch")
PATCH_REL = TASK_REL / "repo" / "incoming" / "0001-Add-loyalty-tiers.patch"
BASE_FIXTURE_REL = Path("tests/fixtures/harness_bench_team_patch_base/team-review-patch")
#: The fixture cases make_fixtures.py writes (relative to tests/fixtures).
CASES = (
    "harness_bench_solutions/team-review-patch",
    "harness_bench_decoys/team-review-patch",
    "harness_bench_alternatives/team-review-patch/fixed-another-way-with-tests",
    "harness_bench_decoys_more/team-review-patch/caps-at-the-invoice-only",
    "harness_bench_decoys_more/team-review-patch/points-still-on-cents",
    "harness_bench_decoys_more/team-review-patch/gold-free-only-over-50",
    "harness_bench_decoys_more/team-review-patch/gold-ships-express-free",
)


def _step(script: str, *args: Path) -> None:
    subprocess.run([sys.executable, "-I", str(HERE / "review_patch" / script), *map(str, args)], check=True)


def generate(root: Path) -> None:
    root = Path(root)
    fixtures = root / "tests" / "fixtures"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        base = tmp / "base"
        shutil.copytree(CHECKOUT / TASK_REL / "repo", base,
                        ignore=shutil.ignore_patterns("incoming", "__pycache__"))
        for sol in sorted((CHECKOUT / BASE_FIXTURE_REL).rglob("*.sol")):
            (base / sol.relative_to(CHECKOUT / BASE_FIXTURE_REL).as_posix()[: -len(".sol")]).write_bytes(
                sol.read_bytes())
        after = tmp / "after"
        for path in sorted((HERE / "review_patch" / "after").rglob("*.after")):
            target = after / path.relative_to(HERE / "review_patch" / "after").as_posix()[: -len(".after")]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        _step("make_patch.py", base, after, root / PATCH_REL)
        _step("merge_by_hand.py", after, tmp / "merged")
        for case in CASES:
            notes = CHECKOUT / "tests" / "fixtures" / case / "NOTES.md"
            target = fixtures / case / "NOTES.md"
            if notes.is_file() and not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(notes, target)
        _step("make_fixtures.py", tmp / "merged", fixtures)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--root", default=str(CHECKOUT), help="where to write (default: this checkout)")
    generate(Path(parser.parse_args().root))
