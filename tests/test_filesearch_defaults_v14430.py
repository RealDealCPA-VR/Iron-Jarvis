"""v1.445.0 — File Search looks in the USER's folders, never the app's own.

Before: ``platform.py`` built ``FileSearchService([search_roots] or
[config.project_root])``. In the packaged app ``project_root`` is the app's
own data folder (``%APPDATA%\\Iron Jarvis``), so File Search, the agents'
``file_search`` tool and "By meaning" (which the memory fabric asks on every
chat turn) looked only in logs and caches, and a newcomer's first search found
nothing of theirs.

Now (``filesearch/defaults.py``): name/content search = the chat files folder,
every active project's folder, and the folders the user added; "By meaning" =
only the added folders, built on its own thread with a bounded wait; read live
so a saved setting applies to the next search; ``list_drives`` names
Documents for the page's "Also search Documents".

These tests NEVER walk the real Documents folder: the chat files folder is a
scratch folder (``chat_files_root``) and ``userdirs.documents_dir`` is patched.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core import fs_policy, userdirs
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Project
from iron_jarvis.filesearch import service as svc_mod
from iron_jarvis.filesearch.defaults import (
    KIND_ADDED,
    KIND_CHAT_FILES,
    KIND_PROJECT,
    FolderPlan,
    meaning_folders,
    search_folders,
)
from iron_jarvis.filesearch.service import FileSearchService, SearchNotes, list_drives
from iron_jarvis.memory.embeddings import MockEmbedder
from iron_jarvis.platform import build_platform


@pytest.fixture(autouse=True)
def _scratch_documents(tmp_path, monkeypatch):
    docs = tmp_path / "Documents"
    docs.mkdir()
    monkeypatch.setattr(userdirs, "documents_dir", lambda: docs)
    return docs


def _platform(tmp_path: Path):
    p = build_platform(str(tmp_path / "root"))
    chat = tmp_path / "chat-files"
    chat.mkdir()
    p.config.chat_files_root = str(chat)
    return p, chat


def _project(p, name: str, root: Path, status: str = "active") -> None:
    with session_scope(p.engine) as db:
        db.add(Project(name=name, root=str(root), status=status))
        db.commit()


def _names(hits) -> set[str]:
    return {Path(h["path"]).name for h in hits}


# -- the rule ---------------------------------------------------------------


def test_the_app_data_folder_is_no_longer_the_default(tmp_path):
    """RED on the old code: it searched project_root (the app's own folder)."""
    p, chat = _platform(tmp_path)
    (tmp_path / "root" / "app-log.txt").write_text("reactor log line", encoding="utf-8")
    (chat / "letter.txt").write_text("reactor letter", encoding="utf-8")
    hits = p.filesearch.search("reactor", mode="content")
    assert _names(hits) == {"letter.txt"}
    roots = p.filesearch.roots
    assert (tmp_path / "root").resolve() not in roots
    assert not any(r == p.config.home.resolve() or r.is_relative_to(p.config.home.resolve()) for r in roots)


def test_defaults_are_chat_files_then_active_projects_then_added(tmp_path):
    p, chat = _platform(tmp_path)
    books = tmp_path / "Books"
    books.mkdir()
    old = tmp_path / "Old"
    old.mkdir()
    _project(p, "Client Books", books)
    _project(p, "Old Year", old, status="archived")
    _project(p, "Moved away", tmp_path / "gone")
    scans = tmp_path / "Scans"
    scans.mkdir()
    p.config.search_roots = [str(scans), str(tmp_path / "missing"), ""]
    got = [(f.kind, f.path, f.label) for f in search_folders(p.config, p.engine)]
    assert got == [
        (KIND_CHAT_FILES, chat.resolve(), "Iron Jarvis chat files"),
        (KIND_PROJECT, books.resolve(), "Client Books"),
        (KIND_ADDED, scans.resolve(), "Scans"),
    ]


def test_the_chat_files_default_is_documents_iron_jarvis(tmp_path, _scratch_documents):
    p = build_platform(str(tmp_path / "root"))
    target = _scratch_documents / "Iron Jarvis"
    target.mkdir()
    assert [f.path for f in search_folders(p.config, p.engine)] == [target.resolve()]


def test_a_folder_inside_the_app_home_is_skipped_even_when_added(tmp_path):
    p, chat = _platform(tmp_path)
    inside = p.config.home / "logs"
    inside.mkdir(parents=True, exist_ok=True)
    p.config.search_roots = [str(inside)]
    assert inside.resolve() not in [f.path for f in search_folders(p.config, p.engine)]
    assert meaning_folders(p.config) == []


def test_a_protected_or_refused_folder_is_skipped(tmp_path, monkeypatch):
    p, chat = _platform(tmp_path)
    keys = tmp_path / "keys"
    keys.mkdir()
    monkeypatch.setattr(fs_policy, "_PROTECTED_ROOTS", {fs_policy._canonical(keys)})
    p.config.search_roots = [str(keys)]
    assert keys.resolve() not in [f.path for f in search_folders(p.config, p.engine)]


def test_a_nested_folder_is_walked_once(tmp_path):
    p, chat = _platform(tmp_path)
    p.config.search_roots = [str(tmp_path)]  # holds the chat files folder too
    assert [f.path for f in search_folders(p.config, p.engine)] == [tmp_path.resolve()]
    # The outer one first: a folder added inside a project's folder.
    books = tmp_path / "Books"
    (books / "2024").mkdir(parents=True)
    _project(p, "Books", books)
    p.config.search_roots = [str(books / "2024")]
    assert [f.path for f in search_folders(p.config, p.engine)] == [chat.resolve(), books.resolve()]


# -- live: a saved setting applies to the next search ------------------------


def test_adding_a_folder_through_the_settings_writer_applies_without_a_restart(tmp_path):
    from iron_jarvis.daemon.app import create_app

    client = TestClient(create_app(str(tmp_path / "root")))
    p = client.app.state.platform
    chat = tmp_path / "chat-files"
    chat.mkdir()
    p.config.chat_files_root = str(chat)
    extra = tmp_path / "Clients"
    extra.mkdir()
    (extra / "smith.txt").write_text("depreciation schedule", encoding="utf-8")
    before = client.get("/filesearch", params={"q": "depreciation"}).json()["results"]
    assert before == []
    r = client.put("/settings/values", json={"values": {"search_roots": [str(extra)]}})
    assert r.status_code == 200, r.text
    after = client.get("/filesearch", params={"q": "depreciation"}).json()["results"]
    assert _names(after) == {"smith.txt"}
    # Removing it applies the same way.
    client.put("/settings/values", json={"values": {"search_roots": []}})
    assert client.get("/filesearch", params={"q": "depreciation"}).json()["results"] == []


def test_a_new_project_is_seen_after_the_short_cache(tmp_path):
    clock = [0.0]
    p, chat = _platform(tmp_path)
    plan = FolderPlan(p.config, p.engine, clock=lambda: clock[0])
    assert plan.search() == [chat.resolve()]
    books = tmp_path / "Books"
    books.mkdir()
    _project(p, "Books", books)
    assert plan.search() == [chat.resolve()]  # cached
    clock[0] += 10.0
    assert plan.search() == [chat.resolve(), books.resolve()]


# -- "By meaning": only the added folders, never a frozen turn ----------------


def test_meaning_looks_only_in_added_folders(tmp_path):
    p, chat = _platform(tmp_path)
    (chat / "client.txt").write_text("local first operating system", encoding="utf-8")
    notes = SearchNotes()
    assert p.filesearch.search("operating system", mode="semantic", notes=notes) == []
    assert notes.no_meaning_folders is True
    assert "only the folders the user added" in notes.note()
    # Nothing was read for it: the chat files folder is not in the index.
    assert p.filesearch._indexed == []
    extra = tmp_path / "Added"
    extra.mkdir()
    (extra / "notes.txt").write_text("local first operating system", encoding="utf-8")
    p.config.search_roots = [str(extra)]
    notes = SearchNotes()
    hits = p.filesearch.search("operating system", mode="semantic", notes=notes)
    assert _names(hits) == {"notes.txt"}


def test_meaning_says_when_it_matched_words_not_meaning(tmp_path):
    root = tmp_path / "a"
    root.mkdir()
    (root / "x.txt").write_text("arc reactor", encoding="utf-8")
    fs = FileSearchService([root], embedder=MockEmbedder())
    notes = SearchNotes()
    fs.search_semantic("arc reactor", notes=notes)
    assert notes.words_only is True
    assert "nomic-embed-text" in notes.note() and "Settings › Connections" in notes.note()


async def test_the_agent_tool_says_why_meaning_found_nothing(tmp_path):
    from iron_jarvis.filesearch.tools import FileSearchTool
    from iron_jarvis.tools.base import ToolContext

    p, chat = _platform(tmp_path)
    tool = FileSearchTool(p.filesearch)
    ctx = ToolContext(
        workspace=tmp_path, session_id="t", agent_run_id="r", config=p.config,
        event_bus=p.event_bus, engine=p.engine,
    )
    res = await tool.execute({"query": "tax", "mode": "semantic"}, ctx)
    assert res.ok and res.data["no_meaning_folders"] is True
    assert "only the folders the user added" in res.output


class _SlowEmbedder:
    model = "slow"

    def __init__(self, gate: threading.Event) -> None:
        self.gate = gate
        self.inner = MockEmbedder()

    def embed(self, text: str):
        self.gate.wait(10)
        return self.inner.embed(text)


def test_a_slow_index_answers_still_reading_and_builds_once(tmp_path, monkeypatch):
    monkeypatch.setattr(svc_mod, "SEMANTIC_WAIT_S", 0.2)
    root = tmp_path / "big"
    root.mkdir()
    (root / "a.txt").write_text("arc reactor", encoding="utf-8")
    gate = threading.Event()
    fs = FileSearchService([root], embedder=_SlowEmbedder(gate))
    starts = []
    real = fs._build_chunk_cache

    def counting():
        starts.append(1)
        real()

    monkeypatch.setattr(fs, "_build_chunk_cache", counting)
    t0 = time.monotonic()
    notes = SearchNotes()
    assert fs.search_semantic("arc reactor", notes=notes) == []
    assert notes.indexing is True and "Still reading" in notes.note()
    assert time.monotonic() - t0 < 5.0
    notes2 = SearchNotes()
    assert fs.search_semantic("arc reactor", notes=notes2) == []  # still building
    assert len(starts) == 1
    gate.set()
    deadline = time.monotonic() + 10
    hits = []
    while time.monotonic() < deadline and not hits:
        hits = fs.search_semantic("arc reactor")
    assert _names(hits) == {"a.txt"}
    assert len(starts) == 1


def test_the_meaning_index_is_rebuilt_when_its_folders_change(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "one.txt").write_text("arc reactor", encoding="utf-8")
    (b / "two.txt").write_text("arc reactor", encoding="utf-8")
    current = [a]
    fs = FileSearchService(lambda: current, embedder=MockEmbedder())
    assert _names(fs.search_semantic("arc reactor")) == {"one.txt"}
    current[:] = [b]
    assert _names(fs.search_semantic("arc reactor")) == {"two.txt"}


def test_a_content_search_never_uses_an_index_of_other_folders(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "one.txt").write_text("needle", encoding="utf-8")
    (b / "two.txt").write_text("needle", encoding="utf-8")
    fs = FileSearchService([a, b], embedder=MockEmbedder(), meaning_roots=[b])
    fs.index()  # meaning roots only
    assert _names(fs.search("needle", mode="content")) == {"one.txt", "two.txt"}


# -- the page's "Also search Documents" -----------------------------------------


def test_list_drives_names_documents(_scratch_documents):
    assert {"path": str(_scratch_documents), "label": "Documents"} in list_drives()
