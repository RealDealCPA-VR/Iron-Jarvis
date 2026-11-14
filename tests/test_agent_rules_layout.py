"""The agent rule files stay in step, and the short root file stays short.

On 2026-11-14 the single 19,500-line ``CLAUDE.md`` was split: a short root file,
one rules file per area under ``docs/rules/``, and a frozen copy of the old file.
These tests keep that shape:

* the root file is short, names no version, and names every area file;
* every generated part (contents lists, folder pointers) is current;
* every package under ``src/iron_jarvis/`` belongs to an area;
* the frozen copy is still the old file, byte for byte (LF line ends), and every
  piece of it is still named in the split record with its hash.

They read files only. No daemon, no network, no model.
"""

from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RULES = REPO / "docs" / "rules"
ARCHIVE = RULES / "ARCHIVE-2026-11-14.md"
RECORD = RULES / "SPLIT-RECORD-2026-11-14.md"


def _load(name: str):
    path = REPO / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    import sys

    sys.modules[name] = module  # dataclasses look their module up by name
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gen():
    return _load("gen_agent_rules")


@pytest.fixture(scope="module")
def split():
    return _load("split_agent_rules")


def _lf(path: Path) -> str:
    # CI's Windows runners check out with CRLF; a hash must not depend on that.
    return path.read_bytes().decode("utf-8").replace("\r\n", "\n")


def test_everything_generated_is_current_and_the_limits_hold(gen):
    assert gen.problems() == []


def test_the_root_file_is_short_and_names_every_area(gen):
    root = _lf(REPO / "CLAUDE.md")
    assert len(root.rstrip("\n").split("\n")) <= gen.ROOT_MAX_LINES
    named = set(re.findall(r"docs/rules/([a-z-]+)\.md", root))
    assert named == {p.stem for p in gen.area_files()}
    assert len(named) >= 18


def test_the_root_file_keeps_the_north_star_and_the_release_rule():
    root = _lf(REPO / "CLAUDE.md")
    assert "## North star" in root
    assert "Trustworthy before helpful" in root
    assert "Every push to master gets a version bump" in root
    assert "scripts/bump_version.py" in root


def test_a_long_root_file_a_version_number_and_an_unplaced_package_are_refused(gen, monkeypatch, tmp_path):
    """The check is not vacuous: each thing it guards against really fails it."""
    long_root = tmp_path / "CLAUDE.md"
    long_root.write_bytes((_lf(REPO / "CLAUDE.md") + "\n- filler" * 400).encode("utf-8"))
    monkeypatch.setattr(gen, "ROOT_FILE", long_root)
    assert any("the limit is" in p for p in gen.problems())

    dated = tmp_path / "dated.md"
    dated.write_bytes((_lf(REPO / "CLAUDE.md") + "\nSee v1.492.0 for the story.\n").encode("utf-8"))
    monkeypatch.setattr(gen, "ROOT_FILE", dated)
    assert any("names the version v1.492.0" in p for p in gen.problems())

    monkeypatch.setattr(gen, "ROOT_FILE", REPO / "CLAUDE.md")
    areas = {a: [n for n in names if n != "workmap"] for a, names in gen.PACKAGE_AREAS.items()}
    monkeypatch.setattr(gen, "PACKAGE_AREAS", areas)
    assert any("src/iron_jarvis/workmap belongs to no area" in p for p in gen.problems())


def test_a_contents_list_points_at_the_line_where_each_rule_starts(gen):
    for path in gen.area_files():
        lines = _lf(path).split("\n")
        entries = [line for line in lines if re.match(r"^- L\d+: ", line)]
        assert entries, f"{path.name} has no contents list"
        for entry in entries:
            number = int(re.match(r"^- L(\d+): ", entry).group(1))
            target = lines[number - 1]
            assert target.startswith("- ") or target.startswith("### "), (path.name, entry[:80])
        # Every rule is listed: as many entries as rule starts under "## Rules".
        start = lines.index(gen.RULES_HEADING)
        starts = [line for line in lines[start:] if line.startswith("- ") or line.startswith("### ")]
        assert len(entries) == len(starts), path.name


def test_a_stale_contents_list_is_reported(gen, monkeypatch, tmp_path):
    text = _lf(RULES / "context.md")
    grown = text + "\n- **A new rule nobody listed.** It was added by hand.\n"
    assert gen.with_contents(grown) != grown
    assert "A new rule nobody listed" in gen.with_contents(grown).split(gen.INDEX_END)[0]
    assert gen.with_contents(text) == text  # a current file is left exactly as it is


def test_the_frozen_copy_is_the_old_file(split):
    text = _lf(ARCHIVE)
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    record = _lf(RECORD)
    assert f"`{sha}`" in record, "the archive no longer matches the hash in the split record"
    assert text.startswith("# Iron Jarvis — Agent Operating Manual")
    assert len(text.split("\n")) > 19_000


def test_the_record_names_every_piece_of_the_old_file_with_its_hash(split):
    """Nothing was dropped: each bullet of the archive has a row with its own hash."""
    segments = split.parse(_lf(ARCHIVE))
    bullets = [s for s in segments if s.kind == "bullet"]
    assert len(bullets) >= 360
    assert "\n".join(s.text for s in segments) == _lf(ARCHIVE)
    record = _lf(RECORD)
    rows = re.findall(r"^\| \d+ \| (\d+)-(\d+) \| `([a-z-]+)\.md` \| .*? \| `([0-9a-f]{12})` \|", record, re.M)
    assert len(rows) == len(bullets)
    by_start = {int(a): (int(b), area, sha) for a, b, area, sha in rows}
    areas = set(split.AREAS)
    for s in bullets:
        end, area, sha = by_start[s.start]
        assert (end, sha) == (s.end, s.sha[:12]), s.headline[:60]
        assert area in areas


def test_the_sorting_table_knows_every_bullet_and_only_real_areas(split):
    table = split.load_table()
    bullets = [s for s in split.parse(_lf(ARCHIVE)) if s.kind == "bullet"]
    missing = [s.headline[:60] for s in bullets if s.key not in table]
    assert missing == []
    assert {area for area, _ in table.values()} <= set(split.AREAS)


def test_the_folder_pointers_name_files_that_exist(gen):
    for path, text in gen.outputs().items():
        for area in re.findall(r"docs/rules/([a-z-]+)\.md", text):
            assert (RULES / f"{area}.md").is_file(), (path.name, area)
    assert (REPO / "src" / "CLAUDE.md").is_file()
    # A pointer inside the Python package would be shipped with the app.
    assert not (REPO / "src" / "iron_jarvis" / "CLAUDE.md").exists()
