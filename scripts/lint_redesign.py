"""The calm UI redesign's lint gate (AUDIT Q12, brief "lint pass").

The repo had no linter configured, so the gate is enforced on the files the
redesign touched (vs master) — linting the whole tree first would start with
hundreds of findings nobody asked for:

* Python: ``ruff check`` with the minimal rule set in pyproject.toml
  (``E9`` syntax errors + pyflakes ``F``), run through ``uvx`` at a pinned
  version so no environment changes.
* Dashboard sources: ESLint with Next's default ``next/core-web-vitals``
  config (dashboard/eslint.config.mjs); errors fail, warnings are reported.

Usage: ``uv run --no-sync python scripts/lint_redesign.py [base]`` (base
defaults to ``master``). Exit code 0 = clean.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUFF = "ruff@0.13.3"


def touched(base: str) -> list[str]:
    committed = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    working = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=AMR", "HEAD"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted({p for p in committed + working if (ROOT / p).exists()})


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "master"
    files = touched(base)
    py = [p for p in files if p.endswith(".py") and (p.startswith("src/") or p.startswith("tests/") or p.startswith("scripts/"))]
    ts = [
        p[len("dashboard/"):]
        for p in files
        if p.startswith("dashboard/")
        and p.endswith((".ts", ".tsx", ".mjs"))
        and "/__tests__/" not in p
        and "/e2e/" not in p
        and not p.startswith("dashboard/__")
    ]
    status = 0
    if py:
        print(f"ruff: {len(py)} file(s)")
        r = subprocess.run(["uvx", RUFF, "check", *py], cwd=ROOT)
        status |= r.returncode
    if ts:
        print(f"eslint: {len(ts)} file(s)")
        npx = "npx.cmd" if sys.platform == "win32" else "npx"
        r = subprocess.run([npx, "eslint", *ts], cwd=ROOT / "dashboard")
        status |= r.returncode
    print("lint: clean" if status == 0 else "lint: FAILED")
    return 1 if status else 0


if __name__ == "__main__":
    raise SystemExit(main())
