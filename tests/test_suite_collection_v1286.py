"""suite-01 (v1.286.0): the local full-suite run collects only what CI collects.

`testpaths = ["tests"]` alone let pytest recurse into untracked review/audit
scratch dirs (tests/_audit_*, tests/_review_*). CI never has them; locally two
stale ones parked an xdist worker on an approval nobody answers, and the
pre-push gate hung at ~97%. pyproject's `norecursedirs` now skips them.

Drives the REAL pytest collection from the repo root against the REAL
pyproject config, with a throwaway scratch dir created under tests/.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tomllib
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _norecursedirs() -> list[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return list(data["tool"]["pytest"]["ini_options"].get("norecursedirs", []))


def test_pyproject_norecursedirs_skips_scratch_dirs_and_keeps_pytest_defaults():
    dirs = _norecursedirs()
    assert "_review_*" in dirs and "_audit_*" in dirs
    # Setting norecursedirs REPLACES pytest's defaults; they must be repeated.
    for default in (".*", "*.egg", "build", "dist", "node_modules", "venv"):
        assert default in dirs, default


def _collect(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=600,
    )


def test_collecting_tests_from_the_repo_root_skips_an_underscore_scratch_dir():
    scratch = ROOT / "tests" / f"_review_pin_tmp_{uuid.uuid4().hex[:8]}"
    rel = f"tests/{scratch.name}/test_scratch_pin.py"
    try:
        scratch.mkdir()
        (scratch / "test_scratch_pin.py").write_text(
            "def test_scratch_pin_marker():\n    pass\n", encoding="utf-8"
        )
        proc = _collect("tests")
        out = proc.stdout.replace("\\", "/")
        assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
        # Anti-vacuity: the run really collected the tracked suite (this file).
        assert "tests/test_suite_collection_v1286.py::" in out
        assert scratch.name not in out
        assert "test_scratch_pin_marker" not in out

        # Naming a scratch file explicitly still collects it.
        named = _collect(rel)
        assert named.returncode == 0, named.stdout[-2000:] + named.stderr[-2000:]
        assert "test_scratch_pin_marker" in named.stdout
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
