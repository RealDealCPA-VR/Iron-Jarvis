"""File search tests (§18 extension, §22 retrieval). Fully offline."""

from __future__ import annotations

from pathlib import Path

import pytest

from iron_jarvis.core.db import init_db, make_engine
from iron_jarvis.core.events import EventBus
from iron_jarvis.filesearch.service import FileSearchService, list_drives
from iron_jarvis.filesearch.tools import filesearch_tools
from iron_jarvis.memory.embeddings import MockEmbedder
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine
from iron_jarvis.tools.registry import ToolRegistry


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A small root with text files plus ignored node_modules/ and .git/ dirs."""
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "docs").mkdir()

    (root / "src" / "alpha.py").write_text(
        "import os\n\n\ndef hello():\n    return 'world'\n", encoding="utf-8"
    )
    (root / "src" / "beta.py").write_text(
        "TODO: refactor the parser\nvalue = 42\n", encoding="utf-8"
    )
    (root / "docs" / "readme.md").write_text(
        "# Iron Jarvis\nLocal-first AI operating system.\n", encoding="utf-8"
    )
    (root / "notes.txt").write_text("plain text notes about taxes\n", encoding="utf-8")

    # These MUST be skipped.
    (root / "node_modules").mkdir()
    (root / "node_modules" / "junk.py").write_text("SECRET_TODO = 1\n", encoding="utf-8")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\nTODO_GITDATA\n", encoding="utf-8")

    # A binary file + an oversized file — must not crash searches.
    (root / "blob.bin").write_bytes(b"\x00\x01\x02TODO\x00binary")
    (root / "big.log").write_text("TODO " * 300_000, encoding="utf-8")  # > 1 MiB
    return root


@pytest.fixture
def service(tree: Path) -> FileSearchService:
    return FileSearchService([tree])


# -- name search ------------------------------------------------------------


def test_search_name_by_glob(service: FileSearchService):
    hits = service.search_name("*.py")
    names = {Path(h["path"]).name for h in hits}
    assert names == {"alpha.py", "beta.py"}  # node_modules/junk.py excluded
    assert all(Path(h["root"]).exists() for h in hits)


def test_search_name_by_substring(service: FileSearchService):
    hits = service.search_name("alpha")
    assert len(hits) == 1
    assert Path(hits[0]["path"]).name == "alpha.py"


# -- content search ---------------------------------------------------------


def test_search_content_reports_path_and_line(service: FileSearchService):
    hits = service.search_content(r"TODO")
    # Only beta.py's TODO — node_modules/.git/binary/oversized are all skipped.
    assert len(hits) == 1
    hit = hits[0]
    assert Path(hit["path"]).name == "beta.py"
    assert hit["line"] == 1
    assert "TODO" in hit["text"]


def test_search_content_regex(service: FileSearchService):
    hits = service.search_content(r"value\s*=\s*\d+")
    assert len(hits) == 1
    assert Path(hits[0]["path"]).name == "beta.py"


def test_search_content_glob_filter(service: FileSearchService):
    # Restrict to markdown — the def in alpha.py won't match this regex anyway.
    hits = service.search_content(r"system", globs=["*.md"])
    assert len(hits) == 1
    assert Path(hits[0]["path"]).name == "readme.md"


def test_ignored_dirs_are_skipped(service: FileSearchService):
    # node_modules + .git both contain TODO markers that must never surface.
    for hit in service.search_content(r"TODO"):
        assert "node_modules" not in hit["path"]
        assert ".git" not in hit["path"]
    for hit in service.search_name("*"):
        assert "node_modules" not in hit["path"]
        assert ".git" not in hit["path"]


def test_bad_regex_returns_empty(service: FileSearchService):
    assert service.search_content(r"(unclosed") == []


# -- index + size/binary guard ---------------------------------------------


def test_index_counts_text_files_only(service: FileSearchService):
    count = service.index()
    # alpha.py, beta.py, readme.md, notes.txt — binary + oversized + ignored excluded.
    assert count == 4
    indexed_names = {Path(p).name for p in service._indexed}
    assert "blob.bin" not in indexed_names
    assert "big.log" not in indexed_names


def test_binary_and_oversized_do_not_crash(service: FileSearchService):
    # Searching everything must complete without raising on the binary/oversized files.
    assert service.search_content(r"binary") == []  # binary file is skipped
    assert service.search_name("blob.bin")  # name search still finds it by path


# -- semantic search --------------------------------------------------------


def test_search_semantic_ranks_relevant_file(tree: Path):
    svc = FileSearchService([tree], embedder=MockEmbedder())
    hits = svc.search_semantic("local first operating system", k=3)
    assert hits  # embedder provided -> non-empty
    assert Path(hits[0]["path"]).name == "readme.md"
    assert hits[0]["score"] > 0.0


def test_search_semantic_disabled_without_embedder(service: FileSearchService):
    assert service.search_semantic("anything") == []


# -- root containment guard -------------------------------------------------


def test_query_cannot_escape_roots(tmp_path: Path):
    root = tmp_path / "roots" / "only"
    root.mkdir(parents=True)
    (root / "inside.txt").write_text("SECRET inside the root\n", encoding="utf-8")

    # A sibling file OUTSIDE the configured root.
    outside = tmp_path / "outside.txt"
    outside.write_text("SECRET outside the root\n", encoding="utf-8")

    svc = FileSearchService([root])
    for hit in svc.search_content(r"SECRET"):
        assert Path(hit["path"]).resolve().is_relative_to(root.resolve())
    for hit in svc.search_name("*.txt"):
        assert Path(hit["path"]).resolve().is_relative_to(root.resolve())
    # The outside file is never returned.
    assert all("outside" not in h["path"] for h in svc.search_name("*"))


def test_symlink_escape_is_blocked(tmp_path: Path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "ok.txt").write_text("hello\n", encoding="utf-8")

    secret_dir = tmp_path / "secret"
    secret_dir.mkdir()
    (secret_dir / "leak.txt").write_text("SECRET_LEAK\n", encoding="utf-8")

    # Symlink inside the root pointing outside it.
    link = root / "escape"
    try:
        link.symlink_to(secret_dir, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform/session")

    svc = FileSearchService([root])
    # The leaked content must not surface (resolves outside the root).
    assert all("SECRET_LEAK" not in h["text"] for h in svc.search_content(r"SECRET"))


# -- drive enumeration + arbitrary-root search (UI: search any local drive) -


def test_list_drives_returns_at_least_the_current_root():
    drives = list_drives()
    assert drives, "expected at least one local root"
    assert all("path" in d and "label" in d for d in drives)
    # Every advertised root must actually exist on disk.
    assert all(Path(d["path"]).exists() for d in drives)


def test_search_can_target_an_arbitrary_root(tmp_path: Path):
    # A service configured with one root...
    configured = tmp_path / "configured"
    configured.mkdir()
    (configured / "elsewhere.txt").write_text("nothing here\n", encoding="utf-8")
    svc = FileSearchService([configured])

    # ...but the per-call override points at a DIFFERENT root with the seed.
    other = tmp_path / "other"
    other.mkdir()
    (other / "seeded.txt").write_text("unique marker zzz123\n", encoding="utf-8")

    content_hits = svc.search("zzz123", mode="content", roots=[other])
    assert any("seeded.txt" in h["path"] for h in content_hits)

    name_hits = svc.search("seeded.txt", mode="name", roots=[other])
    assert any("seeded.txt" in h["path"] for h in name_hits)

    # Path-escape safety still holds relative to the override root.
    for hit in content_hits:
        assert Path(hit["path"]).resolve().is_relative_to(other.resolve())


def test_walk_cap_is_respected(tmp_path: Path):
    root = tmp_path / "many"
    root.mkdir()
    for i in range(50):
        (root / f"f{i:02d}.txt").write_text("needle\n", encoding="utf-8")
    svc = FileSearchService([root])

    # Cap the walk at 5 files -> at most 5 content hits (one match per file).
    hits = svc.search_content("needle", limit=1000, max_walk=5)
    assert len(hits) == 5

    name_hits = svc.search_name("*.txt", limit=1000, max_walk=5)
    assert len(name_hits) == 5


# -- tool via registry ------------------------------------------------------


@pytest.fixture
def engine(tmp_path: Path):
    e = make_engine(str(tmp_path / "fs.db"))
    init_db(e)
    return e


@pytest.fixture
def ctx(engine, tmp_path: Path) -> ToolContext:
    return ToolContext(
        workspace=tmp_path,
        session_id="s1",
        agent_run_id="r1",
        config=None,
        event_bus=EventBus(),
        engine=engine,
    )


async def test_file_search_tool_via_registry(service: FileSearchService, ctx, engine):
    registry = ToolRegistry()
    for tool in filesearch_tools(service):
        registry.register(tool)
    perms = PermissionEngine({"file_search": "allow"})

    res = await registry.invoke(
        "file_search",
        {"query": "*.py", "mode": "name"},
        ctx,
        perms,
    )
    assert res.ok
    assert res.data["count"] == 2
    assert all(Path(r["path"]).suffix == ".py" for r in res.data["results"])

    res2 = await registry.invoke(
        "file_search",
        {"query": "TODO", "mode": "content"},
        ctx,
        perms,
    )
    assert res2.ok
    assert res2.data["count"] == 1
    assert "beta.py" in res2.output


async def test_file_search_tool_permission_denied(service: FileSearchService, ctx):
    registry = ToolRegistry()
    for tool in filesearch_tools(service):
        registry.register(tool)
    # No resolver + default ASK for the unknown key -> fail-closed deny.
    perms = PermissionEngine({})
    res = await registry.invoke("file_search", {"query": "x"}, ctx, perms)
    assert not res.ok
    assert "permission denied" in (res.error or "")
