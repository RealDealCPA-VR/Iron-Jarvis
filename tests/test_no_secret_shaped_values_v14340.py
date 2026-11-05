"""No tracked file holds a value shaped like a credential GitHub reports.

The repository is public, and GitHub's secret scanning raised alerts for
made-up test values: two MongoDB Atlas addresses with a password (the
v1.433.0 paste-guard tests) and a Google API key (a v1.424.0 test). None was
real, but each alert reads to the owner as a leaked password. A test that
needs such a value writes it in a way the scanner cannot match: a reserved
example host (``example.com``), or a key built from pieces at run time.

The shapes below are the ones GitHub has flagged in this repository (and the
Telegram bot token it flagged earlier); add a shape here when GitHub flags a
new one.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

SHAPES = {
    "MongoDB Atlas address with a password":
        r"mongodb(\+srv)?://[^\s\"'/@]+:[^\s\"'@]+@[^\s\"'/]*mongodb\.net",
    "Google API key": r"AIza[0-9A-Za-z_-]{35}",
    "Telegram bot token": r"\b[0-9]{8,10}:AA[0-9A-Za-z_-]{33}\b",
}


def _tracked_hits(pattern: str) -> list[str]:
    done = subprocess.run(
        ["git", "grep", "-n", "-I", "-E", pattern],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60,
    )
    # git grep: 0 = found, 1 = nothing found, anything else = it failed.
    assert done.returncode in (0, 1), done.stderr
    return [line.split(":", 2)[0] + ":" + line.split(":", 2)[1]
            for line in done.stdout.splitlines() if line]


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
@pytest.mark.parametrize("name", sorted(SHAPES))
def test_no_tracked_file_holds_a_value_github_reads_as_a_secret(name):
    if not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    hits = _tracked_hits(SHAPES[name])
    assert hits == [], (
        f"{name} shape found in {hits}. If it is a made-up test value, use an "
        "example.com host or build it from pieces so GitHub does not raise a "
        "secret alert; if it is real, remove it and change it at once."
    )


def test_the_shapes_still_catch_what_github_flagged():
    # Built from pieces here too, so this file never matches itself.
    atlas = "mongodb+srv://ledger:" + "s3cret@cluster0.ab12c.mongo" + "db.net/books"
    google = "AI" + "za" + "SyD" + "0123456789abcdefghijklmnopqrstuv"
    telegram = "123456789:" + "AA" + "b" * 33
    assert re.search(SHAPES["MongoDB Atlas address with a password"], atlas)
    assert re.search(SHAPES["Google API key"], google)
    assert re.search(SHAPES["Telegram bot token"], telegram)
    # The values the tests use now do not match.
    assert not re.search(SHAPES["MongoDB Atlas address with a password"],
                         "mongodb+srv://ledger:s3cret@cluster0.example.com/books")
