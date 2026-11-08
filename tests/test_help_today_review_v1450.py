"""Review pins for v1.450.0's Help / changelog lane (the gaps the builder's
pins did not cover; each one red against the shape it guards):

- THE FROZEN LAYOUT serves the changelog: a packaged build reads every guide
  flat from ``sys._MEIPASS/ijdocs`` and the .spec ships exactly
  ``BUNDLED_DOCS``, so the changelog must be in that list AND be found there.
- ``GET /updates/changelog`` NEEDS THE INSTALL TOKEN like every other route
  (it is not one of auth's documented exemptions).
- A junk, path-like or very long ``version`` answers ``found: false`` (200),
  never a 500, and never names a file.
- The Guide asked for a dropped document (``spec``, ``operating-manual``)
  answers in words, never raises.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis import changelog
from iron_jarvis.daemon.routes import helpdocs
from iron_jarvis.guide.corpus import BUNDLED_DOCS

REPO = Path(__file__).resolve().parents[1]
SPEC = REPO / "packaging" / "ironjarvis.spec"


def _client() -> TestClient:
    app = FastAPI()
    helpdocs.register(app, None)
    return TestClient(app)


def test_the_spec_ships_bundled_docs_and_the_changelog_is_one_of_them():
    spec = SPEC.read_bytes().decode("utf-8")
    assert "from iron_jarvis.guide.corpus import BUNDLED_DOCS" in spec
    assert "ijdocs" in spec
    assert helpdocs.CHANGELOG_FILE in {Path(rel).name for _slug, rel, _t in BUNDLED_DOCS}


def test_a_frozen_build_serves_the_changelog_from_meipass_ijdocs(tmp_path, monkeypatch):
    ijdocs = tmp_path / "ijdocs"
    ijdocs.mkdir()
    # A version the repo's own docs/CHANGELOG.md will never hold, so a route
    # that ignored the frozen layout and read the repo could not pass.
    text = "\n".join([changelog.TITLE, "", "## v0.0.7", "", "Only in the frozen bundle.", ""])
    (ijdocs / helpdocs.CHANGELOG_FILE).write_bytes(text.encode("utf-8"))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    r = _client().get("/updates/changelog", params={"version": "0.0.7"})
    assert r.status_code == 200, r.text
    assert r.json()["found"] is True and r.json()["text"] == "Only in the frozen bundle."


@pytest.mark.parametrize(
    "junk",
    ["latest", "../../etc/passwd", r"C:\Windows\win.ini", "v1.2", "1.2.3.4", "1" * 5000, "v" + "9." * 3000],
)
def test_a_junk_version_is_found_false_never_a_500(junk):
    r = _client().get("/updates/changelog", params={"version": junk})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["found"] is False and body["text"] == "" and "note" not in body


def test_the_changelog_route_needs_the_install_token(tmp_path, monkeypatch):
    from iron_jarvis.daemon.app import create_app

    monkeypatch.setenv("IRONJARVIS_TOKEN", "review-token-v1450")
    app = create_app(str(tmp_path))
    with TestClient(app) as c:
        assert c.get("/updates/changelog?version=1.450.0").status_code == 401
        ok = c.get(
            "/updates/changelog?version=1.450.0",
            headers={"Authorization": "Bearer review-token-v1450"},
        )
        assert ok.status_code == 200, ok.text


@pytest.mark.parametrize("slug", ["spec", "operating-manual"])
def test_the_guide_answers_a_dropped_doc_in_words(slug, tmp_path):
    from iron_jarvis.daemon.app import create_app
    from iron_jarvis.guide.tools import GuideReadTool

    app = create_app(str(tmp_path))
    with TestClient(app):
        platform = app.state.platform
        res = asyncio.run(
            GuideReadTool(platform).execute({"doc": slug}, None)  # type: ignore[arg-type]
        )
    assert res.ok is False
    assert f"no doc {slug!r}" in (res.error or "")
    assert "handbook" in (res.error or "") and "changelog" in (res.error or "")


def test_the_changelogs_lead_speaks_to_the_person_reading_help():
    """Help shows docs/CHANGELOG.md as a guide: its lead names where to look in
    the app, never the script or the file it is made from."""
    text = (REPO / "docs" / "CHANGELOG.md").read_bytes().decode("utf-8")
    lead = text.split("\n## ", 1)[0]
    for engine_room in ("gen_changelog", "docs/HANDBOOK.md", "scripts/", " — "):
        assert engine_room not in lead, engine_room
    assert "Settings › System › Updates" in lead
