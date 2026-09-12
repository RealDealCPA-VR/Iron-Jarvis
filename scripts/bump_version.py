"""Bump the app version everywhere it lives — with ANCHORED edits only.

Usage: uv run python scripts/bump_version.py OLD NEW     e.g. 1.258.0 1.258.1

This is the release rule in CLAUDE.md ("Release flow", step 1) as code. It exists
because two ad-hoc bump scripts (v1.257.0, v1.258.0) blanket-replaced the old
version across docs/HANDBOOK.md and relabelled five lines of shipped history.

Every file is edited at ONE anchored location and asserted to change exactly
once. docs/HANDBOOK.md is touched ONLY on its "Current as of" line — a blanket
replace there rewrote historical version labels twice (v1.257.0, v1.258.0). The
lockfile is regenerated, never edited.
"""

import re
import subprocess
import sys
from pathlib import Path

OLD, NEW = sys.argv[1], sys.argv[2]

ANCHORED = [
    ("pyproject.toml",                  rf'^version = "{re.escape(OLD)}"$',           f'version = "{NEW}"'),
    ("src/iron_jarvis/__init__.py",     rf'^__version__ = "{re.escape(OLD)}"$',       f'__version__ = "{NEW}"'),
    ("desktop/package.json",            rf'^  "version": "{re.escape(OLD)}",$',       f'  "version": "{NEW}",'),
    ("extensions/chrome/manifest.json", rf'^  "version": "{re.escape(OLD)}",$',       f'  "version": "{NEW}",'),
    ("extensions/chrome/package.json",  rf'^  "version": "{re.escape(OLD)}",$',       f'  "version": "{NEW}",'),
    ("docs/HANDBOOK.md",                rf'Current as of v{re.escape(OLD)} \(',       f"Current as of v{NEW} ("),
]

for rel, pattern, repl in ANCHORED:
    p = Path(rel)
    text = p.read_text(encoding="utf-8")
    new_text, n = re.subn(pattern, repl, text, flags=re.M)
    if n != 1:
        raise SystemExit(f"{rel}: anchored pattern matched {n} times (need exactly 1) — not bumping")
    p.write_text(new_text, encoding="utf-8")
    print(f"{rel}: 1 anchored edit -> {NEW}")

proc = subprocess.run("uv lock", shell=True, capture_output=True, encoding="utf-8", errors="replace")
print(f"uv lock exit={proc.returncode}")
lock = Path("uv.lock").read_text(encoding="utf-8")
print(f'uv.lock carries version = "{NEW}": {lock.count(chr(34) + NEW + chr(34))} line(s)')

# Proof no historical label moved: the OLD string may still appear in HANDBOOK
# (as history) — that is CORRECT now. Only report.
hb = Path("docs/HANDBOOK.md").read_text(encoding="utf-8")
print(f"HANDBOOK still mentions v{OLD} {hb.count(OLD)} time(s) as history (expected: >= 0, untouched)")
