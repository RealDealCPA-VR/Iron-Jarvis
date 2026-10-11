"""numpy comes in through ONE door with a one-thread OpenBLAS (v1.333.0, D1).

A bare ``import numpy`` sized OpenBLAS's pool to the machine: on the user's
24-core PC that was ~760 MB of private memory and 23 idle threads for the
daemon's whole life. ``core/_numpy`` sets ``OPENBLAS_NUM_THREADS=1`` for the
import only and puts the environment back, so every child the daemon starts
afterwards sees the user's own environment.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "iron_jarvis"

# The child imports the daemon app, then asks OpenBLAS itself how many threads
# it sized its pool to (the symbol numpy's bundled scipy-openblas exports).
_PROBE = r"""
import ctypes, glob, json, os, sys
import iron_jarvis.daemon.app  # noqa: F401  the real import chain
import numpy

def blas_threads():
    base = os.path.dirname(numpy.__file__)
    for folder in (os.path.join(base, os.pardir, "numpy.libs"), os.path.join(base, ".libs")):
        for path in glob.glob(os.path.join(folder, "*openblas*")):
            try:
                lib = ctypes.CDLL(path)
            except OSError:
                continue
            for name in ("scipy_openblas_get_num_threads64_",
                         "scipy_openblas_get_num_threads",
                         "openblas_get_num_threads64_",
                         "openblas_get_num_threads"):
                fn = getattr(lib, name, None)
                if fn is not None:
                    return int(fn())
    return None

print(json.dumps({"blas": blas_threads(), "env": os.environ.get("OPENBLAS_NUM_THREADS")}))
"""


def _probe(extra_env: dict[str, str] | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k.upper() != "OPENBLAS_NUM_THREADS"}
    env.update(extra_env or {})
    out = subprocess.run(
        [sys.executable, "-c", _PROBE],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(ROOT),
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_importing_the_daemon_sizes_openblas_to_one_thread_and_leaves_no_variable_behind():
    got = _probe()
    if got["blas"] is None:
        pytest.skip("this numpy build exposes no OpenBLAS thread query")
    assert got["blas"] == 1
    assert got["env"] is None  # children inherit the user's environment, not ours


def test_a_thread_count_the_user_chose_is_kept_as_they_set_it():
    got = _probe({"OPENBLAS_NUM_THREADS": "2"})
    if got["blas"] is None:
        pytest.skip("this numpy build exposes no OpenBLAS thread query")
    assert got["blas"] == 2
    assert got["env"] == "2"


def test_no_module_imports_numpy_except_through_the_helper():
    offenders: list[str] = []
    for path in SRC.rglob("*.py"):
        rel = path.relative_to(SRC).as_posix()
        if rel == "core/_numpy.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            else:
                continue
            if any(n == "numpy" or n.startswith("numpy.") for n in names):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], f"import numpy via iron_jarvis.core._numpy instead: {offenders}"
