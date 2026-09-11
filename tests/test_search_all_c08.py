"""C-08 — one search over files, memory and conversations.

``GET /search/all`` is the palette's "In your files & memory" lane. What these
pin:

* FILES ARE FINDABLE AT ALL. Before this, the palette searched titles and
  conversation history; a workbook the app made in your chat folder could not
  be found from the search box.
* EVERY WORD COUNTS, and separators are not walls: "smith summary" finds
  ``Smith_2024 summary.xlsx``.
* FAVOUR, NEVER FILTER. The active project's files rank first; everything else
  is still there.
* BOUNDED AND HONEST. A lane that hits its deadline is NAMED in ``partial``
  rather than presented as a complete answer, and a lane that fails is empty —
  the endpoint answers 200 for anything, like /search/history does.
"""

from __future__ import annotations

import os
import time
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project
from iron_jarvis.daemon.app import create_app
from iron_jarvis.memory.fabric import FabricHit
from iron_jarvis.search import unified


def _client(tmp_path) -> TestClient:
    return TestClient(create_app(str(tmp_path)))


def _chat_files(client, tmp_path, names: list[str]) -> Any:
    folder = tmp_path / "chat files"
    folder.mkdir(exist_ok=True)
    for n in names:
        (folder / n).write_text("fictional", encoding="utf-8")
    client.app.state.platform.config.chat_files_root = str(folder)
    return folder


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #
def test_a_file_the_app_made_is_findable_by_its_words(tmp_path):
    client = _client(tmp_path)
    _chat_files(client, tmp_path, ["Smith_2024 summary.xlsx", "unrelated notes.docx"])

    r = client.get("/search/all", params={"q": "smith summary"})

    assert r.status_code == 200, r.text
    files = r.json()["files"]
    assert [f["title"] for f in files] == ["Smith_2024 summary.xlsx"]
    hit = files[0]
    assert hit["kind"] == "file"
    assert hit["path"].endswith("Smith_2024 summary.xlsx")
    assert hit["folder"].endswith("chat files")
    assert hit["at"]  # when it was last written, for the row's "3d ago"


def test_a_word_that_is_not_in_the_name_finds_nothing(tmp_path):
    client = _client(tmp_path)
    _chat_files(client, tmp_path, ["Smith_2024 summary.xlsx"])

    r = client.get("/search/all", params={"q": "jones"})

    assert r.status_code == 200 and r.json()["files"] == []


def test_every_word_must_be_in_the_name_not_just_one(tmp_path):
    """Two words, two files that each carry ONE of them: matching on "any"
    would hand back both and bury the file you asked for."""
    client = _client(tmp_path)
    _chat_files(client, tmp_path, ["Smith_2024 summary.xlsx", "Jones 2024 notes.docx"])

    r = client.get("/search/all", params={"q": "smith notes"})

    assert r.status_code == 200
    assert r.json()["files"] == []


def test_the_active_projects_files_rank_first(tmp_path):
    client = _client(tmp_path)
    platform = client.app.state.platform
    folder = _chat_files(client, tmp_path, ["quarterly report.xlsx"])
    proj_root = tmp_path / "acme"
    proj_root.mkdir()
    (proj_root / "quarterly report.xlsx").write_text("fictional", encoding="utf-8")
    # The chat folder's copy is the NEWER file, so only the project rule can
    # put the project's copy first — sorting by time alone would not.
    now = time.time()
    os.utime(folder / "quarterly report.xlsx", (now, now))
    os.utime(proj_root / "quarterly report.xlsx", (now - 600, now - 600))
    with session_scope(platform.engine) as db:
        db.add(Project(id="proj_acme", name="Acme", root=str(proj_root)))
        db.commit()

    r = client.get("/search/all", params={"q": "quarterly report", "project_id": "proj_acme"})

    files = r.json()["files"]
    assert len(files) == 2  # favour, never filter
    assert files[0]["project_id"] == "proj_acme"
    assert files[1]["project_id"] is None


def test_a_short_query_asks_nothing_and_answers_empty(tmp_path):
    client = _client(tmp_path)
    _chat_files(client, tmp_path, ["Smith_2024 summary.xlsx"])

    r = client.get("/search/all", params={"q": "s"})

    body = r.json()
    assert r.status_code == 200
    assert body["files"] == [] and body["memory"] == [] and body["history"] == []


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
def test_memory_hits_open_where_they_live(tmp_path, monkeypatch):
    client = _client(tmp_path)
    platform = client.app.state.platform

    def fake_recall(query, k=6, *, project_id=None, sources=None, min_score=0.0):
        return [
            FabricHit(
                source="knowledge",
                ref="k1",
                snippet="S-corp election filed 2024-03-01",
                score=0.9,
                title="S-corp election",
                extra={"project_id": "proj_acme"},
            ),
            FabricHit(
                source="memory",
                ref="m1",
                snippet="the client prefers PDFs",
                score=0.5,
                title="client preference",
            ),
        ]

    monkeypatch.setattr(platform.fabric, "recall", fake_recall)

    rows = client.get("/search/all", params={"q": "s-corp election"}).json()["memory"]

    assert [r["kind"] for r in rows] == ["project", "memory"]
    assert rows[0]["href"] == "/projects/proj_acme"
    assert rows[1]["href"] == "/memory"
    assert rows[0]["title"] == "S-corp election"


# --------------------------------------------------------------------------- #
# Bounded and honest
# --------------------------------------------------------------------------- #
def test_a_lane_that_runs_long_is_named_as_incomplete(tmp_path, monkeypatch):
    client = _client(tmp_path)

    def slow(*a: Any, **k: Any):
        time.sleep(1.0)
        return []

    monkeypatch.setattr(unified, "LANE_DEADLINE_S", 0.05)
    monkeypatch.setattr(unified, "find_memory", slow)

    body = client.get("/search/all", params={"q": "anything at all"}).json()

    assert body["memory"] == []
    assert "memory" in body["partial"]


def test_a_file_walk_cut_short_says_so(tmp_path):
    client = _client(tmp_path)
    folder = _chat_files(client, tmp_path, [f"report {i}.txt" for i in range(30)])
    fs = client.app.state.platform.filesearch

    hits, partial = fs.search_words(["report"], [folder], limit=50, max_walk=6, deadline_s=5.0)

    # The cap stopped the walk long before the 30 matching files ran out,
    # so the list is honestly incomplete rather than "all there is".
    assert partial is True
    assert 0 < len(hits) <= 6


def test_a_broken_store_is_an_empty_lane_not_an_error(tmp_path, monkeypatch):
    client = _client(tmp_path)

    def boom(*a: Any, **k: Any):
        raise RuntimeError("the store is broken")

    monkeypatch.setattr(unified, "find_memory", boom)

    r = client.get("/search/all", params={"q": "anything at all"})

    assert r.status_code == 200
    assert r.json()["memory"] == [] and "memory" not in r.json()["partial"]


def test_the_endpoint_answers_200_for_anything(tmp_path):
    client = _client(tmp_path)
    for q in ["", "   ", "NUL\x00byte", "a" * 400, 'match("', "../../etc/passwd", "смит"]:
        r = client.get("/search/all", params={"q": q})
        assert r.status_code == 200, (q, r.text)
        assert set(r.json()) >= {"q", "files", "memory", "history", "partial"}
