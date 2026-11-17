"""A folder is walked in NAME ORDER on Linux and macOS; Windows is left as its disk answers.

Found by running the suite on Linux (2026-11-16): a search that sticks on one
file keeps "what it found before then", and on Linux the stuck file came FIRST
(ext4 hands names back in whatever order it keeps them), so nothing was kept.
The same walk feeds every listing, search and glob, and the place a limit cuts.
Windows' own disks answer in order already, and the walk does not touch that.
"""

from __future__ import annotations

import os
from pathlib import Path

from iron_jarvis.tools import builtins as B


def _fake_walk(base, followlinks=False):
    # What a disk with no order of its own hands back.
    yield str(base), ["zeta", "Beta", "alpha"], ["big.txt", "a_first.txt", "Readme.md", "b.txt"]
    for name in ("zeta", "Beta", "alpha"):
        yield os.path.join(str(base), name), [], ["2.txt", "10.txt", "1.txt"]


def _walked(monkeypatch, tmp_path, **kw) -> list[str]:
    monkeypatch.setattr(os, "walk", _fake_walk)
    files, truncated = B._walk_files(tmp_path, limit=kw.pop("limit", 100), **kw)
    return [str(Path(f).relative_to(tmp_path)).replace("\\", "/") for f in files], truncated


def test_the_walk_is_in_name_order_off_windows_and_untouched_on_windows(monkeypatch, tmp_path):
    files, truncated = _walked(monkeypatch, tmp_path)
    assert truncated == ""
    top = [f for f in files if "/" not in f]
    if os.name == "nt":
        # Exactly what the disk answered, in its order.
        assert top == ["big.txt", "a_first.txt", "Readme.md", "b.txt"]
        return
    # Capitals do not matter (Windows' own order), files of a folder together.
    assert top == ["a_first.txt", "b.txt", "big.txt", "Readme.md"]
    assert files[:4] == top  # the folder's own files before anything below it


def test_a_limit_cuts_at_the_same_place_every_time(monkeypatch, tmp_path):
    files, truncated = _walked(monkeypatch, tmp_path, limit=2)
    assert truncated == "stopped at 2 files"
    assert files == (["big.txt", "a_first.txt"] if os.name == "nt" else ["a_first.txt", "b.txt"])


def test_the_folders_below_are_visited_in_name_order_too(monkeypatch, tmp_path):
    seen: list[list[str]] = []

    def walk(base, followlinks=False):
        dirs = ["zeta", ".git", "Beta", "node_modules", "alpha"]
        yield str(base), dirs, []
        seen.append(list(dirs))  # what os.walk will descend into, in this order

    monkeypatch.setattr(os, "walk", walk)
    B._walk_files(tmp_path, limit=10)
    # Hidden and skipped folders are dropped on every system.
    assert seen == [["zeta", "Beta", "alpha"] if os.name == "nt" else ["alpha", "Beta", "zeta"]]
