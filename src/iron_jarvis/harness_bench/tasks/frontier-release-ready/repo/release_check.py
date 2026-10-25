"""Is this folder ready to release? (RELEASE.md says what each rule is for.)

    python release_check.py

Prints every problem it finds and exits 1, or prints "ready to release" and
exits 0. The build machine runs exactly this before it packages the folder.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
#: Folders Python and test runners make by themselves; never shipped, never a problem.
IGNORED_DIRS = {"__pycache__", ".pytest_cache"}


def manifest():
    with open(os.path.join(HERE, "MANIFEST.txt"), encoding="utf-8") as fh:
        return {line.strip() for line in fh if line.strip() and not line.startswith("#")}


def present():
    out = set()
    for folder, dirs, files in os.walk(HERE):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS]
        for name in files:
            out.add(os.path.relpath(os.path.join(folder, name), HERE).replace(os.sep, "/"))
    return out


def unreleased_entries():
    with open(os.path.join(HERE, "CHANGELOG.md"), encoding="utf-8") as fh:
        lines = [line.rstrip("\r\n") for line in fh]
    try:
        start = lines.index("## Unreleased") + 1
    except ValueError:
        return []
    out = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if line.startswith("- "):
            out.append(line)
    return out


def problems():
    found = []
    listed, here = manifest(), present()
    for name in sorted(here - listed):
        found.append(f"{name} is not in MANIFEST.txt (a scratch file, a backup or a log must not ship)")
    for name in sorted(listed - here):
        found.append(f"{name} is listed in MANIFEST.txt but missing")
    if not unreleased_entries():
        found.append('CHANGELOG.md has no entry under "## Unreleased" for this release')
    tests = subprocess.run([sys.executable, "-m", "unittest", "-q"], cwd=HERE,
                           capture_output=True, text=True)
    if tests.returncode != 0:
        found.append("the tests do not pass (python -m unittest)")
    return found


def main():
    found = problems()
    for problem in found:
        print("not ready:", problem)
    if not found:
        print("ready to release")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
