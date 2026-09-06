"""The Handbook, Help page and README say what the app does (v1.232.0, audit
Wave 6, task 6B).

The Guide answers from `docs/HANDBOOK.md` (`guide/corpus.py`), so a Handbook
that lags the app is a Guide that lies politely. These tests pin:

- the Handbook's "Current as of" line against `__version__` (major.minor):
  it may be AT the app's version or ONE minor ahead (a wave edits the
  Handbook before the coordinator bumps the three version files), never
  behind — a lagging line goes red here instead of silently in the Guide;
- the "What changed in the audit waves" section names every wave version;
- the "Daemon offline" remedy uses the same words in all three places
  (Handbook, Help page, README) — the drift the audit found;
- the README Highlights row for the audit-wave guarantees;
- the Documents / Memory Handbook lines match the surfaces they describe
  (the chips' source constant, the import link's text and href).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from iron_jarvis import __version__

REPO = Path(__file__).resolve().parents[1]
HANDBOOK = REPO / "docs" / "HANDBOOK.md"
HELP_PAGE = REPO / "dashboard" / "app" / "help" / "page.tsx"
README = REPO / "README.md"
DOCS_PAGE = REPO / "dashboard" / "app" / "documents" / "page.tsx"
MEMORY_SURFACE = REPO / "dashboard" / "components" / "memory" / "MemorySurface.tsx"

CURRENT_AS_OF = re.compile(r"Current as of v(\d+)\.(\d+)\.(\d+)")


def _raw(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _read(p: Path) -> str:
    # Markdown and JSX both wrap mid-phrase; compare on one-space text.
    return re.sub(r"\s+", " ", _raw(p))


def _major_minor(text: str) -> tuple[int, int]:
    m = re.match(r"(\d+)\.(\d+)", text)
    assert m, text
    return int(m.group(1)), int(m.group(2))


def test_handbook_current_as_of_matches_the_app_version():
    m = CURRENT_AS_OF.search(_read(HANDBOOK))
    assert m, "HANDBOOK.md has no 'Current as of vX.Y.Z' line"
    handbook = (int(m.group(1)), int(m.group(2)))
    app = _major_minor(__version__)
    assert handbook >= app, (
        f"HANDBOOK.md says 'Current as of v{m.group(1)}.{m.group(2)}' but the "
        f"app is v{__version__} — the Guide would answer from a stale Handbook. "
        "Bump the line with the wave's version."
    )
    # A wave edits the Handbook first and bumps the three version files last,
    # so one minor ahead is the in-flight state; two is a typo.
    assert handbook[0] == app[0] and handbook[1] - app[1] <= 1, (
        f"HANDBOOK.md 'Current as of' v{handbook[0]}.{handbook[1]} is more than "
        f"one minor ahead of the app (v{__version__})"
    )


def test_handbook_ends_with_the_audit_waves_section():
    text = _raw(HANDBOOK)
    idx = text.find("\n## What changed in the audit waves")
    assert idx != -1, "the audit-waves section is missing"
    section = text[idx + 1 :]
    for version in ("v1.227.0", "v1.228.0", "v1.229.0", "v1.230.0", "v1.231.0", "v1.232.0"):
        assert version in section, f"{version} is not described in the audit-waves section"
    # It is the LAST section: no other heading follows it.
    assert "\n## " not in section, "the audit-waves section must end the Handbook"
    assert "\n### " not in section


@pytest.mark.parametrize(
    "phrase",
    [
        "two missed polls",
        "quit from the tray and relaunch",
        "Restart Iron Jarvis",
        "Copy diagnostics",
        "Open logs folder",
    ],
)
def test_daemon_offline_remedy_is_the_same_in_all_three_places(phrase: str):
    handbook = _read(HANDBOOK)
    start = handbook.find("## Troubleshooting in one minute")
    assert start != -1
    trouble = handbook[start:]
    for name, text in (
        ("HANDBOOK.md troubleshooting", trouble),
        ("Help page", _read(HELP_PAGE)),
        ("README", _read(README)),
    ):
        assert phrase.lower() in text.lower(), f"{name} lost the 'Daemon offline' phrase {phrase!r}"


def test_help_page_daemon_offline_entry_points_at_maintenance():
    src = _read(HELP_PAGE)
    entry = src[src.find("“Daemon offline” in the dashboard") :]
    entry = entry[: entry.find("Port 8787")]
    assert 'href="/settings"' in entry
    assert "Settings → Maintenance" in entry


def test_readme_highlights_carry_the_audit_wave_guarantees():
    readme = _raw(README)
    start = readme.find("## ✨ Highlights")
    assert start != -1
    end = readme.find("\n## ", start + 10)
    assert end != -1
    table = readme[start:end]
    row = next((line for line in table.splitlines() if "The roster is the contract" in line), None)
    assert row, "Highlights table has no audit-wave guarantees row"
    assert "not armed" in row
    assert "by name" in row and "local_primary_policy" in row
    assert "needs you" in row


def test_readme_bell_line_matches_reality():
    readme = _read(README)
    assert "surfaces pending reviews and computer-use approvals" not in readme
    assert "waiting for you" in readme.lower()
    assert "an ask nobody answered ends up on the session's outcome" in readme


def test_handbook_documents_line_matches_the_page():
    handbook = _read(HANDBOOK)
    assert "convert, split, merge and batch processing are done from Chat" in handbook
    src = _read(DOCS_PAGE)
    assert "const CHAT_DOC_EXAMPLES" in src
    for label in ("Convert", "Split", "Merge", "Batch"):
        assert f'label: "{label}"' in src, f"Documents empty state lacks the {label} chip"
    assert "/chat?ask=" in src


def test_handbook_memory_line_matches_the_import_link():
    handbook = _read(HANDBOOK)
    assert "`/ltm`" in handbook
    link_text = "Import from ChatGPT/Claude/Takeout → Long-term memory"
    assert link_text in handbook
    src = _read(MEMORY_SURFACE)
    assert link_text in src
    assert 'href="/memory?scope=longterm"' in src
