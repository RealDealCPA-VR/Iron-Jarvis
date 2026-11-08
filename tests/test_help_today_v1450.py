"""Help, the Handbook and the Guide describe TODAY's app (v1.450.0, readiness
item 41 / ease item 73).

What this pins, each part red when its fix is reverted:

- THE GUIDE READS USER DOCS ONLY. ``guide/corpus.BUNDLED_DOCS`` is the
  allowlist the packaged build ships and the Guide answers from. It used to
  carry ``CLAUDE.md`` (the maintainers' operating manual) and ``SPEC.MD``, so
  a user's question could be answered in engine-room words about retired
  features. The list is pinned exactly, and no developer document may join it.
- THE RELEASE NOTES MOVED OUT. ``docs/CHANGELOG.md`` is generated from the
  Handbook's ``## <title> (vX.Y.Z)`` sections (``iron_jarvis.changelog``,
  ``scripts/gen_changelog.py --check``), is bundled, and is served one version
  at a time by ``GET /updates/changelog?version=`` — never a 500.
- RECOMMENDED SETTINGS SPEAKS SETTINGS' LABELS. Every setting the guide names
  is read against ``settings/schema.py`` at test time: a renamed or removed
  row turns this red until the guide follows.
- THE HANDBOOK'S OVERVIEW NAMES THE FIVE PLACES and no longer sends anyone to
  the Overview tips card, Simple mode or the round table.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis import changelog
from iron_jarvis.daemon.routes import helpdocs
from iron_jarvis.guide.corpus import _DOC_PRIOR, BUNDLED_DOCS, GuideIndex
from iron_jarvis.settings import schema

REPO = Path(__file__).resolve().parents[1]
HANDBOOK = REPO / "docs" / "HANDBOOK.md"
RECOMMENDED = REPO / "docs" / "RECOMMENDED-SETTINGS.md"
CHANGELOG = REPO / "docs" / "CHANGELOG.md"


def _lf(p: Path) -> str:
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


# ------------------------------------------------------------- the Guide


#: Every doc the Guide may read, and nothing else. A new doc joins here on
#: purpose, after someone checked it is written for the person using the app.
USER_DOCS = {
    "docs/HANDBOOK.md",
    "docs/CHANGELOG.md",
    "docs/RECOMMENDED-SETTINGS.md",
    "docs/LOCAL-MODELS.md",
    # v1.455.0 (Wave H item 27, moved on purpose): the vendor sign-in app steps.
    "docs/OWN-APPS.md",
    "docs/REFLEX.md",
    "docs/COMPUTER-USE.md",
    "docs/BROWSER.md",
    "README.md",
    "VOCABULARY.md",
}

#: Shapes of a document written for the people who BUILD Iron Jarvis.
DEVELOPER_DOC = re.compile(
    r"(^|/)(CLAUDE|AGENTS|SPEC[^/]*|DEPLOY|SIGNING|TASKS|TODO|PLAN)\.(md|MD)$"
    r"|HANDOFF|FIX-MASTER-PLAN|IJ-HARNESS|AUDIT|TOFIX|-PLAN|PRODUCT-READINESS|EASE-OF-USE|ROUTES-WITHOUT"
    r"|BUILD-CODE-PARITY|CLAUDE-LIVE|CLAUDE-NATIVE|redesign/",
    re.I,
)


def test_the_guide_reads_only_user_facing_docs():
    rels = {rel for _slug, rel, _title in BUNDLED_DOCS}
    assert rels == USER_DOCS
    for rel in rels:
        assert not DEVELOPER_DOC.search(rel), f"{rel} is written for developers"


def test_the_developer_shape_catches_the_documents_that_were_dropped():
    # Anti-vacuity: the shape really sees the manual, the spec and the plans.
    for rel in ("CLAUDE.md", "SPEC.MD", "AGENTS.md", "docs/FIX-MASTER-PLAN-2026-10-31.md",
                "docs/IJ-HARNESS-PLAN.md", "docs/HARNESS-BUILD-AUDIT-2026-11-01.md", "HANDOFF.md"):
        assert DEVELOPER_DOC.search(rel), rel
    for rel in USER_DOCS:
        assert not DEVELOPER_DOC.search(rel), rel


def test_every_bundled_doc_has_a_prior_and_no_prior_is_left_for_a_dropped_doc():
    slugs = {slug for slug, _rel, _title in BUNDLED_DOCS}
    assert set(_DOC_PRIOR) == slugs


def test_the_guide_index_answers_without_the_operating_manual():
    idx = GuideIndex()
    assert not idx.missing, idx.missing
    docs = {s.doc for s in idx.docs}
    assert "operating-manual" not in docs and "spec" not in docs
    assert "changelog" in docs
    block = idx.ground("how do updates install")
    assert "Restart to update" in block
    assert "operating manual" not in block.lower()


# --------------------------------------------------------- the changelog


def test_the_changelog_is_generated_from_the_handbook_and_current():
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "gen_changelog.py"), "--check"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    text = _lf(CHANGELOG)
    assert text.startswith(changelog.TITLE + "\n")
    rows = changelog.entries(text)
    assert len(rows) == changelog.KEEP_VERSIONS
    keys = [changelog.version_key(e.version) for e in rows]
    assert keys == sorted(keys, reverse=True)
    assert all(e.text for e in rows)


def test_the_changelog_is_bundled_and_served_as_a_guide():
    assert ("changelog", "docs/CHANGELOG.md", "What changed in each version") in BUNDLED_DOCS
    assert helpdocs._DOCS["changelog"][1] == "CHANGELOG.md"


HB = """# The Handbook

## What Iron Jarvis is

Not a version section.

## Faster things (v1.10.0)

**Faster.** It got faster. Much faster.

## A lead-in only (v1.12.0)

**Lead.**

- **Sub.** The real sentence. Another.

## Two in one (v1.12.0)

Second section of the same version.

## Old thing (v1.9.3)

```
# not a heading
```

| a | b |
|---|---|

Prose after the table. Then more.
"""


def test_generate_takes_each_sections_title_and_first_sentence():
    out = changelog.generate(HB, keep=2)
    rows = changelog.entries(out)
    assert [e.version for e in rows] == ["v1.12.0", "v1.10.0"]
    assert rows[0].text == (
        "**A lead-in only.** **Lead.** **Sub.** The real sentence. "
        "**Two in one.** Second section of the same version."
    )
    assert rows[1].text == "**Faster things.** **Faster.** It got faster."
    old = changelog.entries(changelog.generate(HB))[-1]
    assert old.version == "v1.9.3" and old.text == "**Old thing.** Prose after the table."


def test_lookup_finds_the_version_or_the_newest_below_it_and_never_raises():
    text = changelog.generate(HB)
    exact = changelog.lookup(text, "1.10.0")
    assert exact["found"] is True and exact["title"] == "What changed in v1.10.0"
    patch = changelog.lookup(text, "v1.12.4")
    assert patch["found"] is False and patch["nearest"] == "v1.12.0"
    assert "Second section" in patch["text"]
    older = changelog.lookup(text, "v1.0.0")
    assert older["found"] is False and "nearest" not in older and older["text"] == ""
    for junk in ("", "latest", "../../etc", "v1.x"):
        assert changelog.lookup(text, junk)["found"] is False
    assert changelog.lookup("", "v1.12.0")["found"] is False


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    helpdocs.register(app, None)
    return TestClient(app)


def test_the_route_serves_the_installed_versions_paragraph(client):
    newest = changelog.entries(_lf(CHANGELOG))[0]
    r = client.get("/updates/changelog", params={"version": newest.version.lstrip("v")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is True and body["text"] == newest.text
    assert body["latest"] == newest.version
    patch = client.get("/updates/changelog", params={"version": newest.version + "9"})
    assert patch.status_code == 200


def test_the_route_answers_a_missing_file_with_found_false_never_a_500(client, tmp_path, monkeypatch):
    monkeypatch.setattr(helpdocs, "_docs_dir", lambda: tmp_path)
    r = client.get("/updates/changelog", params={"version": "1.449.0"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is False and body["text"] == ""
    assert "CHANGELOG.md" in body["note"]
    assert client.get("/updates/changelog").status_code == 200


# ------------------------------------------------- Recommended Settings


def _labels() -> set[str]:
    return {d.label for d in schema.SETTINGS}


def _option_labels() -> set[str]:
    out: set[str] = set()
    for d in schema.SETTINGS:
        for o in d.options or ():
            out.add(o[1] if isinstance(o, tuple) else str(o))
    return out


def _table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if not line.startswith("| **"):
            continue
        rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows


def test_recommended_settings_names_every_setting_by_its_label_in_settings():
    text = _lf(RECOMMENDED)
    rows = _table_rows(text)
    assert len(rows) >= 20
    labels, options = _labels(), _option_labels()
    for row in rows:
        name = re.fullmatch(r"\*\*(.+?)\*\*", row[0])
        assert name, row[0]
        assert name.group(1) in labels, f"Settings has no row named {name.group(1)!r}"
        for bold in re.findall(r"\*\*(.+?)\*\*", row[1]):
            assert bold in labels or bold in options, f"{bold!r} is neither a setting nor a choice"


def test_recommended_settings_no_longer_names_config_keys():
    text = _lf(RECOMMENDED)
    for key in ("default_provider", "strict_model_pin", "max_agent_steps", "git_native", "mcp_auto_approve"):
        assert f"`{key}`" not in text


# ------------------------------------------------------------ the Handbook


def _section(text: str, heading: str) -> str:
    start = text.index(f"\n## {heading}")
    rest = text[start + 4 :]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def test_the_handbooks_overview_names_the_five_places():
    overview = _section(_lf(HANDBOOK), "What Iron Jarvis is")
    assert "**The five places.**" in overview
    for place in ("Chat", "Build", "Projects", "Everything", "Settings"):
        assert f"- **{place}** —" in overview, place
    assert "Chat | Code" in overview


@pytest.mark.parametrize(
    "gone",
    [
        "Overview tips card",
        "The round table is gone",
        "since Simple mode hides",
        "four places — **Build**",
        "the amber line under the hero in Simple mode",
        "Build now\n  opens on the IJ harness",
        "an Overview tile can no longer be dragged",
    ],
)
def test_the_handbook_no_longer_sends_anyone_to_a_retired_feature(gone):
    assert gone not in _lf(HANDBOOK)
