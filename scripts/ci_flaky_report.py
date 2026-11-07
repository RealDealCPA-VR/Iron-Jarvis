"""Name the tests that passed only on their retry, where the user reads CI.

CI runs a failed Python test once more before the gate goes red (v1.445.1,
``--reruns 1`` from pytest-rerunfailures). A retry that hides a flaky test
forever would be worse than the red gate it saves, so every test that needed it
is written to the run's page (``$GITHUB_STEP_SUMMARY``) and raised as a
``::warning`` annotation, which GitHub shows on the run's front page.

Reads pytest's own ``-rR`` lines from the saved output: ``RERUN <id>`` for
every retried test, then ``PASSED <id>`` (it passed the second time) or
``FAILED <id>`` (it failed both times, so the gate is red anyway). Stdlib only.

    uv run python scripts/ci_flaky_report.py pytest-out.txt "shard 3"
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

_LINE = re.compile(r"^(RERUN|PASSED|FAILED|ERROR) (\S+)")


def retried(text: str) -> tuple[list[str], list[str]]:
    """(passed only on retry, failed both times), each in first-seen order."""
    rerun: list[str] = []
    ends: dict[str, str] = {}
    for line in text.splitlines():
        m = _LINE.match(line.strip())
        if not m:
            continue
        word, test = m.groups()
        if word == "RERUN":
            if test not in rerun:
                rerun.append(test)
        elif test in rerun:
            ends[test] = word
    flaky = [t for t in rerun if ends.get(t) == "PASSED"]
    red = [t for t in rerun if ends.get(t) in ("FAILED", "ERROR")]
    return flaky, red


def report(text: str, where: str) -> str:
    """The summary block ("" when nothing passed only on retry)."""
    flaky, _ = retried(text)
    if not flaky:
        return ""
    lines = [f"**{where}: {len(flaky)} test(s) passed only on retry.** Fix them; the retry hides them:", ""]
    lines += [f"- `{t}`" for t in flaky]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    out, where = Path(argv[0]), (argv[1] if len(argv) > 1 else "pytest")
    text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
    block = report(text, where)
    if not block:
        return 0
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(block)
    for test in retried(text)[0]:
        print(f"::warning title=Passed only on retry ({where})::{test}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
