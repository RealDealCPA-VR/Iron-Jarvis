"""v1.400.0 review — the team tier's generated parts can be made again.

``scripts/bench_task_generators/`` holds the scripts that wrote them (the doer
ran them from a scratch folder; the review committed them). Each runs here
into a scratch folder and its output is compared, byte for byte, with what
is committed (on LF line ends):

* team-audit-events: every generated module of ``shop/``, ``events.py`` and
  the hidden check (its answer key and tree digest are generated);
* team-review-patch: the format-patch file, and the seven fixture cases made
  from the change merged by hand.

A changed generator, a hand edit of a generated file, or a generator that
reads the clock or an unseeded random would turn these red.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GEN = ROOT / "scripts" / "bench_task_generators"
AUDIT = ROOT / "src" / "iron_jarvis" / "harness_bench" / "tasks" / "team-audit-events"
CR, LF = bytes([13]), bytes([10])


def _lf(data: bytes) -> bytes:
    """A checkout with core.autocrlf=true (GitHub's Windows runners) holds the
    committed files as CRLF; the generators write LF. Compared on LF."""
    return data.replace(CR + LF, LF)


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): _lf(p.read_bytes()) for p in sorted(root.rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts}


def test_the_audit_task_regenerates_byte_for_byte(tmp_path):
    out = tmp_path / "team-audit-events"
    subprocess.run([sys.executable, "-I", str(GEN / "team_audit_events.py"), "--task", str(out)], check=True,
                   capture_output=True)
    made, shipped = _files(out), _files(AUDIT)
    assert sorted(made) == sorted(shipped)
    changed = sorted(rel for rel in made if made[rel] != shipped[rel])
    assert changed == [], changed
    generated = [rel for rel in made if rel.startswith("repo/shop/") and "/audit/" not in rel]
    assert len(generated) >= 60  # the area modules really were written here, not copied


def test_the_patch_task_regenerates_byte_for_byte(tmp_path):
    subprocess.run([sys.executable, "-I", str(GEN / "team_review_patch.py"), "--root", str(tmp_path)], check=True,
                   capture_output=True)
    made = _files(tmp_path)
    assert "src/iron_jarvis/harness_bench/tasks/team-review-patch/repo/incoming/0001-Add-loyalty-tiers.patch" in made
    sols = [rel for rel in made if rel.endswith(".sol")]
    assert len(sols) == 7 * 11, len(sols)
    for rel, data in made.items():
        assert _lf((ROOT / rel).read_bytes()) == data, rel
