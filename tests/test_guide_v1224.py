"""v1.224.0 — the Iron Jarvis Guide is a built-in AGENT.

The user asked for a baked-in helper agent in the Agents module: base
knowledge of the project, and tools to search the app for what they need.
(v1.223.0 had shipped it as a chat persona — rejected; the persona is gone.)

What is pinned, each with its silent failure mode:

- the corpus loads every allowlisted doc in dev and from ``_MEIPASS/ijdocs``
  when frozen, reporting a MISSING file rather than improvising;
- retrieval answers real questions with the right section;
- ``AgentType.GUIDE`` exists, is listed by ``GET /agents``, sits in the
  roster as a delegable builtin, and holds exactly read-only tools;
- a Guide SESSION starts with the base-knowledge block in its system prompt
  and the four Guide tools advertised — and a builder session gets neither;
- ``guide_search`` / ``guide_read`` / ``app_search`` / ``app_status`` return
  real, labelled results (an app_search hit carries the dashboard path);
- at the ROUND TABLE (no tools) a Talk with the Guide is grounded in the same
  retrieval + an app_search, and a Talk with the builder is not;
- the persona seam is gone from both chat lanes;
- the inspection routes and the doctor check tell the truth.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis import __version__
from iron_jarvis.core.models import AgentType
from iron_jarvis.daemon.app import create_app
from iron_jarvis.guide import BUNDLED_DOCS, GuideIndex, doc_path, split_markdown
from iron_jarvis.guide.tools import GUIDE_TOOL_NAMES

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = "# Iron Jarvis reference (base knowledge)"
REF = "# Iron Jarvis reference"


# --------------------------------------------------------------- the corpus


def test_every_bundled_doc_exists_in_the_repo():
    for _slug, rel, _title in BUNDLED_DOCS:
        assert doc_path(rel) == REPO_ROOT / rel
        assert (REPO_ROOT / rel).is_file(), f"Guide doc missing from the repo: {rel}"


def test_frozen_build_reads_flat_ijdocs(tmp_path, monkeypatch):
    ijdocs = tmp_path / "ijdocs"
    ijdocs.mkdir()
    (ijdocs / "HANDBOOK.md").write_text("# The Handbook\n\n## Updates\n\nRestart to update.\n")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    idx = GuideIndex()
    assert doc_path("docs/HANDBOOK.md") == ijdocs / "HANDBOOK.md"
    assert [d["slug"] for d in idx.loaded] == ["handbook"]
    assert {m["file"] for m in idx.missing} == {
        Path(rel).name for _s, rel, _t in BUNDLED_DOCS if Path(rel).name != "HANDBOOK.md"
    }
    block = idx.ground("how do updates work")
    assert "Restart to update" in block and "missing from this install" in block
    assert idx.status()["frozen"] is True


def test_split_markdown_respects_headings_and_fences():
    text = (
        "# Doc\n\nintro\n\n## One\n\nbody one\n\n```\n# not a heading\ncode\n```\n\n"
        "### Deep\n\ndeep body\n\n## Two\n\n" + ("para\n\n" * 400)
    )
    secs = split_markdown("t", "T", text, max_chars=600)
    labels = [s.heading for s in secs]
    assert labels[0] == "Doc" and "Doc › One" in labels and "Doc › One › Deep" in labels
    assert "# not a heading" in next(s for s in secs if s.heading == "Doc › One").text
    parts = [s for s in secs if s.heading.startswith("Doc › Two (")]
    assert len(parts) > 1 and all(len(p.text) <= 600 for p in parts)


@pytest.fixture(scope="module")
def dev_index():
    return GuideIndex()


def test_search_finds_the_handbook_for_updates(dev_index):
    top = [s for _, s in dev_index.search("how do updates install")[:3]]
    assert any(s.doc == "handbook" and "Restart to update" in s.text for s in top)


def test_search_finds_the_vocabulary_for_memory_base(dev_index):
    assert any(s.doc == "vocabulary" for _, s in dev_index.search("what is a memory base")[:3])


def test_ground_has_header_labels_and_budget(dev_index):
    block = dev_index.ground("how do updates install", char_budget=2500)
    assert block.startswith(REF) and "do not invent" in block and "## [The Handbook ›" in block
    assert len(block) <= 2500 + 20


# --------------------------------------------------------- the agent itself


def test_guide_is_a_builtin_agent_with_read_only_tools():
    from iron_jarvis.agents.types import _DEFINITIONS, get_agent_definition

    assert AgentType.GUIDE in _DEFINITIONS
    d = get_agent_definition(AgentType.GUIDE)
    assert "As the Guide" in d.system_prompt
    for name in GUIDE_TOOL_NAMES:
        assert name in d.tools
    # Read-only by construction: no writers, no shell, no spawning, no delegate.
    for forbidden in ("write_file", "edit_file", "shell", "run_code", "delegate",
                      "spawn_agent", "write_document", "workflow_run", "schedule_create"):
        assert forbidden not in d.tools, forbidden


def test_guide_is_listed_and_in_the_roster(tmp_path):
    from iron_jarvis.agents.roster import build_roster

    app = create_app(str(tmp_path))
    with TestClient(app) as client:
        assert "guide" in client.get("/agents").json()["builtin"]
        entry = next(e for e in build_roster(app.state.platform) if e.name == "guide")
        assert entry.kind == "builtin" and entry.delegable and entry.healthy
        assert "expert" in entry.description
        roster = client.get("/agents/roster").json()["roster"]
        assert any(r["name"] == "guide" for r in roster)


def _spy(platform, seen: dict):
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools):
            seen.setdefault("calls", []).append(
                {"system": system, "tools": [t.get("name") for t in (tools or [])]}
            )
            return await real_complete(system=system, messages=messages, tools=tools)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get


def test_guide_session_has_base_knowledge_and_its_tools_and_builder_does_not(tmp_path):
    app = create_app(str(tmp_path))
    seen: dict = {}
    _spy(app.state.platform, seen)
    with TestClient(app) as client:
        r = client.post("/sessions", json={"task": "how do updates install?", "agent_type": "guide", "wait": True})
        assert r.status_code == 200 and r.json()["agent_type"] == "guide"
        guide_calls = [c for c in seen["calls"] if BASE in c["system"]]
        assert guide_calls, "the Guide session never received its base knowledge"
        sys_prompt = guide_calls[0]["system"]
        assert "Restart to update" in sys_prompt  # the Handbook's overview rides along
        assert __version__ in sys_prompt  # and this install's live version
        assert "As the Guide" in sys_prompt
        assert set(GUIDE_TOOL_NAMES) <= set(guide_calls[0]["tools"])
        assert "write_file" not in guide_calls[0]["tools"]
        seen["calls"].clear()
        r = client.post("/sessions", json={"task": "hello", "agent_type": "builder", "wait": True})
        assert r.status_code == 200
        assert seen["calls"] and not any(BASE in c["system"] for c in seen["calls"])
        assert not any(set(GUIDE_TOOL_NAMES) & set(c["tools"]) for c in seen["calls"])


# ---------------------------------------------------------------- the tools


@pytest.fixture()
def populated(tmp_path):
    app = create_app(str(tmp_path))
    client = TestClient(app)
    client.__enter__()
    root = tmp_path / "acme"
    root.mkdir()
    pid = client.post("/projects", json={"name": "Acme Tax 2026", "root": str(root), "brief": "client returns"}).json()["id"]
    client.post("/workflows", json={"name": "month-end-close", "description": "close the books",
                                    "steps": [{"name": "s", "agent": "builder", "task": "t"}]})
    client.post("/schedules", json={"name": "nightly-summary", "cron": "0 9 * * *", "kind": "task",
                                    "payload": {"task": "summarize the day", "project_id": pid}})
    client.post("/reflex/rules", json={"name": "on-1099", "source": "comm", "match": "1099",
                                       "action": "session", "project_id": pid})
    yield app, client, pid
    client.__exit__(None, None, None)


async def _run(app, name, args):
    tool = app.state.platform.registry.get(name)
    assert tool is not None, f"{name} is not registered"
    return await tool.execute(args, None)


@pytest.mark.asyncio
async def test_guide_search_and_read(populated):
    app, _client, _pid = populated
    res = await _run(app, "guide_search", {"query": "how do updates install", "k": 4})
    assert res.ok and res.data["hits"]
    label = res.data["hits"][0]["label"]
    assert label.startswith("The Handbook")
    read = await _run(app, "guide_read", {"label": label})
    assert read.ok and "Restart to update" in read.output
    whole = await _run(app, "guide_read", {"doc": "vocabulary"})
    assert whole.ok and "memory base" in whole.output
    bad = await _run(app, "guide_read", {"label": "No Such › Thing"})
    assert not bad.ok and "no reference section" in bad.error
    live = await _run(app, "guide_search", {"query": "DELETE projects route"})
    assert any(h["live"] and "API routes: /projects" in h["label"] for h in live.data["hits"])


@pytest.mark.asyncio
async def test_app_search_finds_the_users_own_things_with_open_paths(populated):
    app, _client, pid = populated
    res = await _run(app, "app_search", {"query": "acme tax"})
    assert res.ok
    hit = next(h for h in res.data["hits"] if h["kind"] == "project")
    assert hit["id"] == pid and hit["open"] == f"/projects/{pid}"
    wf = await _run(app, "app_search", {"query": "month end close", "kinds": ["workflow"]})
    assert [h["name"] for h in wf.data["hits"]] == ["month-end-close"]
    assert wf.data["hits"][0]["open"] == "/workflows"
    sched = await _run(app, "app_search", {"query": "nightly summary"})
    assert any(h["kind"] == "schedule" and h["name"] == "nightly-summary" for h in sched.data["hits"])
    rule = await _run(app, "app_search", {"query": "1099", "kinds": ["reflex"]})
    assert rule.data["hits"] and rule.data["hits"][0]["open"] == "/reflex"
    nothing = await _run(app, "app_search", {"query": "zzqx-not-a-thing"})
    assert nothing.ok and nothing.data["hits"] == [] and "nothing in this install matches" in nothing.output
    assert (await _run(app, "app_search", {"query": "x", "kinds": ["nope"]})).ok is False


@pytest.mark.asyncio
async def test_app_search_names_an_unreadable_store_instead_of_calling_it_empty(populated, monkeypatch):
    app, _client, _pid = populated

    def boom():
        raise RuntimeError("scheduler down")

    monkeypatch.setattr(app.state.platform.scheduler, "list", boom)
    res = await _run(app, "app_search", {"query": "nightly summary"})
    assert res.ok and "schedule" in res.data["unreadable"]
    assert "could not read: schedule" in res.output


@pytest.mark.asyncio
async def test_app_status_reports_this_install(populated):
    app, _client, _pid = populated
    res = await _run(app, "app_status", {})
    assert res.ok and res.data["version"] == __version__
    assert res.data["counts"]["project"] == 1 and res.data["counts"]["workflow"] == 1
    assert "providers available now" in res.output


def test_guide_tools_default_to_allow(tmp_path):
    app = create_app(str(tmp_path))
    perms = app.state.platform.permissions
    for name in GUIDE_TOOL_NAMES:
        assert perms.mode_for(name).value == "allow", name


# ----------------------------------------------------------- the round table


def test_talk_with_the_guide_is_grounded_and_talk_with_builder_is_not(tmp_path):
    app = create_app(str(tmp_path))
    seen: dict = {}
    _spy(app.state.platform, seen)
    with TestClient(app) as client:
        client.post("/workflows", json={"name": "month-end-close", "steps": [{"name": "s", "agent": "builder", "task": "t"}]})
        t = client.post("/agents/threads", json={
            "title": "Talk with guide",
            "participants": [{"source": "builtin", "name": "guide", "role": ""}],
        }).json()
        r = client.post(f"/agents/threads/{t['id']}/say", json={"message": "where is my month-end-close workflow?"})
        assert r.status_code == 200, r.text
        sys_prompt = seen["calls"][-1]["system"]
        assert "As the Guide" in sys_prompt
        assert REF in sys_prompt  # reference sections for the question
        assert "Matching things in this install" in sys_prompt
        assert "month-end-close" in sys_prompt  # the app_search hit rode along
        assert sys_prompt.index("NO TOOLS") < sys_prompt.index(REF)  # after the no-tools rule
        seen["calls"].clear()
        b = client.post("/agents/threads", json={
            "title": "Talk with builder",
            "participants": [{"source": "builtin", "name": "builder", "role": ""}],
        }).json()
        assert client.post(f"/agents/threads/{b['id']}/say", json={"message": "hi"}).status_code == 200
        assert REF not in seen["calls"][-1]["system"]


# ------------------------------------------------- the persona seam is gone


def test_no_guide_persona_and_no_chat_seam_injection(tmp_path):
    from iron_jarvis.personas.builtins import BUILTIN_PERSONAS

    assert "guide" not in BUILTIN_PERSONAS
    app = create_app(str(tmp_path))
    seen: dict = {}
    _spy(app.state.platform, seen)
    with TestClient(app) as client:
        r = client.post("/chat", json={"messages": [{"role": "user", "content": "how do updates install?"}],
                                       "persona": "guide"})
        assert r.status_code == 200
        assert not any(REF in c["system"] for c in seen["calls"])


# ------------------------------------------------------- routes + doctor


def test_guide_status_and_search_routes(tmp_path):
    with TestClient(create_app(str(tmp_path))) as client:
        st = client.get("/guide/status").json()
        assert st["missing"] == [] and st["doc_sections"] > 50
        assert "Version and install" in st["live_headings"]
        r = client.get("/guide/search", params={"q": "write_document tool"}).json()
        assert any(h["live"] and "Tools agents can call" in h["label"] for h in r["hits"])


def test_doctor_reports_missing_guide_docs(tmp_path, monkeypatch):
    import importlib

    doctor = importlib.import_module("iron_jarvis.onboarding.doctor")
    assert doctor.check_guide_docs in doctor.CHECKS
    assert doctor.check_guide_docs()["ok"] is True
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    bad = doctor.check_guide_docs()
    assert bad["ok"] is False and "HANDBOOK.md" in bad["detail"]
