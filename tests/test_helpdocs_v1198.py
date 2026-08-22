"""The user guides are SERVABLE, so the Help page can render them (v1.198.0).

`docs/HANDBOOK.md`, `docs/RECOMMENDED-SETTINGS.md` and `docs/LOCAL-MODELS.md`
were referenced nowhere in the dashboard — a packaged-app user could never
read them. `routes/helpdocs.py` serves exactly those three via a fixed
allowlist; the allowlist is also the traversal guard (a slug is a dict key,
never a path component), so `../SIGNING`-shaped probes and the TOFIX/audit
files in the same directory stay unreachable.

The route is registered directly on a bare FastAPI app here (not through
`create_app`) because the coordinating session owns `daemon/app.py` and wires
`_routes.helpdocs.register(app, d)` after this lands.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from iron_jarvis.daemon.routes import helpdocs

REPO_ROOT = Path(__file__).resolve().parents[1]

# slug -> (title, a heading string that MUST appear in the real file)
EXPECTED = {
    "handbook": ("The Handbook", "# Iron Jarvis — The Handbook"),
    "recommended-settings": ("Recommended Settings", "# Recommended Settings"),
    "local-models": ("Local Models by RAM Tier", "# Local Models by RAM Tier"),
}


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    helpdocs.register(app, None)  # d is unused: static files, not daemon state
    return TestClient(app)


def test_docs_dir_resolves_to_the_real_repo_docs_dir():
    """The parents[4] walk in `_docs_dir` must land on the repo's docs/ dir —
    if the module ever moves, this is the test that says so instead of every
    guide 404ing in dev."""
    d = helpdocs._docs_dir()
    assert d == REPO_ROOT / "docs"
    for _title, filename, _desc in helpdocs._DOCS.values():
        assert (d / filename).is_file(), f"allowlisted doc missing: {filename}"


def test_list_returns_the_three_docs_in_fixed_order(client):
    r = client.get("/helpdocs")
    assert r.status_code == 200, r.text
    docs = r.json()["docs"]
    assert [d["slug"] for d in docs] == [
        "handbook",
        "recommended-settings",
        "local-models",
    ]
    for d in docs:
        assert d["title"] == EXPECTED[d["slug"]][0]
        assert d["description"].strip(), f"empty description for {d['slug']}"


@pytest.mark.parametrize("slug", sorted(EXPECTED))
def test_each_slug_serves_the_real_markdown(client, slug):
    title, heading = EXPECTED[slug]
    r = client.get(f"/helpdocs/{slug}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == slug
    assert body["title"] == title
    assert heading in body["markdown"], (
        f"served markdown for {slug!r} lacks its own heading — wrong file?"
    )


def test_unknown_slug_is_404(client):
    r = client.get("/helpdocs/nope")
    assert r.status_code == 404
    assert "unknown help doc" in r.json()["detail"]


def test_traversal_probe_never_serves_a_non_allowlisted_file(client):
    """docs/SIGNING.md is real and sits NEXT TO the served files — the exact
    thing a `..%2F` probe would reach if a slug were ever joined into a path.
    FastAPI may reject the encoded form at routing; what matters is: not 200,
    and none of SIGNING.md's content in the response."""
    signing_marker = "Code-signing the Windows installer"
    assert signing_marker in (REPO_ROOT / "docs" / "SIGNING.md").read_text(
        encoding="utf-8"
    ), "sentinel file changed — pick a new marker"
    for probe in ("/helpdocs/..%2FSIGNING", "/helpdocs/../SIGNING"):
        r = client.get(probe)
        assert r.status_code != 200, f"{probe} was served: {r.text[:200]}"
        assert signing_marker not in r.text


def test_missing_file_is_an_honest_404_not_a_500(client, tmp_path, monkeypatch):
    """Someone deleted a guide (or a build shipped without it): the response
    must say WHICH file is missing — never a 500, never an empty 200."""
    monkeypatch.setattr(helpdocs, "_docs_dir", lambda: tmp_path)
    r = client.get("/helpdocs/handbook")
    assert r.status_code == 404, r.text
    detail = r.json()["detail"]
    assert "HANDBOOK.md" in detail
    assert "missing" in detail
