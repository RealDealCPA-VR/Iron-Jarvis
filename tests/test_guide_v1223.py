"""v1.223.0 — the Iron Jarvis Guide: the built-in expert on the app itself.

A chat persona (``guide``) whose turns are grounded in a retrieved block from
the app's own bundled docs + live catalogs. What is pinned here, each with
its silent failure mode:

- the corpus loads every allowlisted doc from the repo in dev and from
  ``_MEIPASS/ijdocs`` when frozen (a build that drops one must be VISIBLE:
  ``status().missing`` + the doctor check, never a Guide that improvises);
- markdown splits at headings, never inside a code fence, and long sections
  are chunked with a numbered heading;
- retrieval is deterministic and answers real questions with the right
  section (updates → Handbook; memory base → Vocabulary; live version);
- the injected block names its origin, carries the honesty header, respects
  the budget, and reaches BOTH chat lanes only when the persona is the Guide
  (explicit or configured default) — mutation-proven by asserting absence
  for the default persona;
- the inspection routes tell the truth about what the Guide knows.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis import __version__
from iron_jarvis.daemon.app import create_app
from iron_jarvis.guide import (
    BUNDLED_DOCS,
    GUIDE_PERSONA,
    GuideIndex,
    doc_path,
    split_markdown,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
HEADER = "# Iron Jarvis reference"


# --------------------------------------------------------------- the corpus


def test_every_bundled_doc_exists_in_the_repo():
    for _slug, rel, _title in BUNDLED_DOCS:
        assert doc_path(rel) == REPO_ROOT / rel
        assert (REPO_ROOT / rel).is_file(), f"Guide doc missing from the repo: {rel}"


def test_frozen_build_reads_flat_ijdocs(tmp_path, monkeypatch):
    """Packaged: every doc is looked up by BASENAME under _MEIPASS/ijdocs —
    the directory the .spec fills from the same BUNDLED_DOCS list."""
    ijdocs = tmp_path / "ijdocs"
    ijdocs.mkdir()
    (ijdocs / "HANDBOOK.md").write_text("# The Handbook\n\n## Updates\n\nRestart to update.\n")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    idx = GuideIndex()
    assert doc_path("docs/HANDBOOK.md") == ijdocs / "HANDBOOK.md"
    assert [d["slug"] for d in idx.loaded] == ["handbook"]
    # Every other doc is reported MISSING by file name — the honest signal.
    assert {m["file"] for m in idx.missing} == {
        Path(rel).name for _s, rel, _t in BUNDLED_DOCS if Path(rel).name != "HANDBOOK.md"
    }
    block = idx.ground("how do updates work")
    assert "Restart to update" in block
    assert "missing from this install" in block
    assert idx.status()["frozen"] is True


def test_split_markdown_respects_headings_and_fences():
    text = (
        "# Doc\n\nintro\n\n## One\n\nbody one\n\n```\n# not a heading\ncode\n```\n\n"
        "### Deep\n\ndeep body\n\n## Two\n\n" + ("para\n\n" * 400)
    )
    secs = split_markdown("t", "T", text, max_chars=600)
    labels = [s.heading for s in secs]
    assert labels[0] == "Doc"
    assert "Doc › One" in labels
    assert "Doc › One › Deep" in labels
    one = next(s for s in secs if s.heading == "Doc › One")
    assert "# not a heading" in one.text  # the fence kept the section whole
    parts = [s for s in secs if s.heading.startswith("Doc › Two (")]
    assert len(parts) > 1 and all(len(p.text) <= 600 for p in parts)
    assert parts[0].heading.endswith(f"(1/{len(parts)})")


# ------------------------------------------------------------- retrieval


@pytest.fixture(scope="module")
def dev_index():
    return GuideIndex()


def test_search_finds_the_handbook_for_updates(dev_index):
    hits = dev_index.search("how do updates install")
    assert hits, "no hits"
    top = [s for _, s in hits[:3]]
    assert any(s.doc == "handbook" for s in top)
    assert any("Restart to update" in s.text for s in top)


def test_search_finds_the_vocabulary_for_memory_base(dev_index):
    hits = dev_index.search("what is a memory base")
    assert any(s.doc == "vocabulary" for _, s in hits[:3])


def test_ground_has_header_labels_and_budget(dev_index):
    block = dev_index.ground("how do updates install", char_budget=2500)
    assert block.startswith(HEADER)
    assert "do not invent" in block
    assert "## [The Handbook ›" in block
    assert len(block) <= 2500 + 20  # the truncation marker may overhang a little


def test_ground_with_no_question_is_the_overview(dev_index):
    block = dev_index.ground("")
    assert HEADER in block and "The Handbook" in block


# ------------------------------------------------------------ live catalog


def test_live_catalog_knows_version_routes_tools_and_skills(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    with TestClient(app) as client:
        st = client.get("/guide/status").json()
        assert st["persona"] == GUIDE_PERSONA
        assert st["missing"] == []
        assert st["doc_sections"] > 50
        heads = st["live_headings"]
        assert "Version and install" in heads
        assert any(h.startswith("API routes: /projects") for h in heads)
        assert any(h.startswith("Tools agents can call") for h in heads)
        assert "Built-in chat personas" in heads
        # Version is the live one, and a real route + tool are retrievable.
        g = client.get("/guide/ground", params={"q": "which version am I running"}).json()
        assert __version__ in g["block"]
        r = client.get("/guide/search", params={"q": "DELETE projects route"}).json()
        assert any("API routes: /projects" in h["label"] for h in r["hits"])
        assert all(set(h) >= {"doc", "label", "live", "score", "preview"} for h in r["hits"])
        t = client.get("/guide/search", params={"q": "write_document tool"}).json()
        assert any(h["live"] and "Tools agents can call" in h["label"] for h in t["hits"])
    # The index is cached on the platform (one build per daemon).
    from iron_jarvis.guide import index_for

    assert index_for(platform) is index_for(platform)


# ------------------------------------------------- the persona + both seams


def _spy_complete(platform, seen: dict):
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools):
            seen.setdefault("systems", []).append(system)
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get


def test_guide_persona_is_builtin_and_listed(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        personas = client.get("/chat/personas").json()["personas"]
        g = next(p for p in personas if p["name"] == GUIDE_PERSONA)
        assert g["builtin"] is True
        assert "Answer ONLY from that material" in g["prompt"]
        assert "Iron Jarvis reference" in g["prompt"]  # names the block it expects


def test_guide_grounding_reaches_both_chat_lanes_only_for_the_guide(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    seen: dict = {}
    _spy_complete(platform, seen)
    msg = {"messages": [{"role": "user", "content": "how do updates install?"}]}
    with TestClient(app) as client:
        # (a) POST /chat — default persona: NO reference block (mutation control).
        assert client.post("/chat", json=msg).status_code == 200
        assert not any(HEADER in s for s in seen["systems"]), "leaked into the default persona"
        seen["systems"].clear()
        # (b) POST /chat with the Guide.
        assert client.post("/chat", json={**msg, "persona": GUIDE_PERSONA}).status_code == 200
        sys_prompt = next(s for s in seen["systems"] if HEADER in s)
        assert "Restart to update" in sys_prompt
        assert "You are the Iron Jarvis Guide" in sys_prompt
        assert sys_prompt.index("You are the Iron Jarvis Guide") < sys_prompt.index(HEADER)
        seen["systems"].clear()
        # (c) POST /chat/stream — the lock-step lane the Help page lands in.
        captured: dict = {}

        async def fake_stream(*, provider=None, model=None, system, messages, tools,
                              session_id=None, task_class=None):
            captured["system"] = system
            adapter = platform.providers.get(
                provider or platform.router.default_provider, model
            )
            async for frame in adapter.stream(system=system, messages=messages, tools=tools):
                if frame.get("type") == "final":
                    yield {**frame, "provider": adapter.provider, "model": adapter.model}
                else:
                    yield frame

        platform.router.stream = fake_stream
        assert client.post("/chat/stream", json={**msg, "persona": GUIDE_PERSONA}).status_code == 200
        assert HEADER in captured["system"] and "Restart to update" in captured["system"]
        captured.clear()
        assert client.post("/chat/stream", json=msg).status_code == 200
        assert HEADER not in captured["system"], "stream lane leaked into the default persona"


def test_configured_default_persona_guide_grounds_without_an_explicit_pick(tmp_path):
    app = create_app(str(tmp_path))
    platform = app.state.platform
    platform.config.default_persona = GUIDE_PERSONA
    seen: dict = {}
    _spy_complete(platform, seen)
    with TestClient(app) as client:
        r = client.post("/chat", json={"messages": [{"role": "user", "content": "what is a memory base"}]})
        assert r.status_code == 200
        assert any(HEADER in s for s in seen["systems"])


# ---------------------------------------------------------------- doctor


def test_doctor_reports_missing_guide_docs(tmp_path, monkeypatch):
    import importlib

    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")

    assert doctor.check_guide_docs in doctor.CHECKS
    ok = doctor.check_guide_docs()
    assert ok["ok"] is True and ok["level"] == doctor.RECOMMENDED
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    bad = doctor.check_guide_docs()
    assert bad["ok"] is False
    assert "HANDBOOK.md" in bad["detail"] and "VOCABULARY.md" in bad["detail"]
