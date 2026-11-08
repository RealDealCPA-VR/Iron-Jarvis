"""Generate ``docs/CHANGELOG.md`` from the Handbook's version sections.

    uv run python scripts/gen_changelog.py          # write the file
    uv run python scripts/gen_changelog.py --check  # exit 1 when it is stale

One paragraph per version (the newest ``changelog.KEEP_VERSIONS``), each made
of a Handbook section's title and first sentence — see
``iron_jarvis.changelog``. Edit the Handbook, then run this; the file is never
edited by hand. ``tests/test_help_today_v1450.py`` runs ``--check``.

Bytes, not text: the Handbook and the changelog are LF files, and this PC's
Python writes CRLF through text mode.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from iron_jarvis import changelog  # noqa: E402

HANDBOOK = ROOT / "docs" / "HANDBOOK.md"
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"


def expected() -> str:
    return changelog.generate(HANDBOOK.read_bytes().decode("utf-8"))


def main(argv: list[str]) -> int:
    want = expected()
    if "--check" in argv:
        have = CHANGELOG.read_bytes().decode("utf-8") if CHANGELOG.is_file() else ""
        if have.replace("\r\n", "\n") != want:
            print("docs/CHANGELOG.md is stale: run `uv run python scripts/gen_changelog.py`")
            return 1
        print("docs/CHANGELOG.md is current")
        return 0
    CHANGELOG.write_bytes(want.encode("utf-8"))
    print(f"wrote {CHANGELOG} ({len(changelog.entries(want))} versions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
