"""v1.297.0, wave 3 — an agent keeps a folder. Offline, deterministic.

``<home>/agents/<slug>/``: AGENTS.md (the instructions, mirrored from the
row's ``system_prompt``), NOTES.md (the notebook the ``notebook`` tool writes
and every run reads), ``revisions/`` (the previous AGENTS.md on every change).

What is guarded, each with its silent failure mode:
  - ``register`` creates the folder and AGENTS.md == the prompt;
  - a prompt change keeps the OLD text as a revision (an edit is never a loss)
    and an unchanged re-register writes none;
  - the revision cap (50) prunes the oldest, newest first in the listing;
  - a restore puts the old text back and itself leaves a revision;
  - notes: write / append / the byte cap drops the OLDEST lines / the
    injection block trims head+tail with the marker / empty is "";
  - confinement: a hostile name is sanitised, and the second lock (the
    containment check) refuses a slug that would leave the root;
  - remove MOVES the folder to ``<home>/trash`` — nothing is deleted;
  - ``load()`` backfills a missing AGENTS.md and never clobbers one;
  - the runtime injects the notebook (and arms the tool) for a custom run and
    NOT for a builtin, captured on the system prompt the model receives;
  - the ``notebook`` tool appends a dated line for the calling custom agent
    and refuses a builtin / unknown session;
  - the routes over the REAL app: files view, PUT instructions mirrors into
    ``GET /agents`` and leaves a revision, restore, notes, open, the 404s.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.agents import files as files_mod
from iron_jarvis.agents.dynamic import DynamicAgentRegistry
from iron_jarvis.agents.files import (
    MAX_REVISIONS,
    NOTEBOOK_INJECT_CHARS,
    NOTES_CAP_BYTES,
    TRIM_MARKER,
    AgentFiles,
    agent_slug,
    trim_head_tail,
)
from iron_jarvis.agents.notebook_tool import NO_NOTEBOOK, NotebookTool, stamp_line
from iron_jarvis.core.config import default_permissions
from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import Session
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import agents as agents_routes
from iron_jarvis.platform import build_platform
from iron_jarvis.tools.base import ToolContext
from iron_jarvis.tools.permissions import PermissionEngine

NAME = "scout"
PROMPT = "You are Scout, a focused research helper. Be concise."
PROMPT2 = "You are Scout. Be thorough, then concise."
TOOLS = ["read_file", "write_file", "list_files"]


# --------------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def platform(tmp_path):
    p = build_platform(str(tmp_path))
    p.permissions = PermissionEngine(
        {
            **p.config.permissions,
            "create_agent": "allow",
            "list_agents": "allow",
            "spawn_agent": "allow",
            "delegate": "allow",
        }
    )
    return p


@pytest.fixture
def registry(platform) -> DynamicAgentRegistry:
    return platform.agents_registry


@pytest.fixture
def files(registry) -> AgentFiles:
    assert registry.files is not None, "build_platform attaches the folder store"
    return registry.files


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(str(tmp_path))) as c:
        yield c


def _ctx(platform, tmp_path, session_id="parent-session", agent_run_id="parent1"):
    return ToolContext(
        workspace=tmp_path,
        session_id=session_id,
        agent_run_id=agent_run_id,
        config=platform.config,
        event_bus=platform.event_bus,
        engine=platform.engine,
    )


def _session_row(engine, agent_name: str) -> str:
    row = Session(task="seeded", agent_name=agent_name)
    with session_scope(engine) as db:
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id


def _spy_complete(platform, seen: dict):
    """Capture every system prompt and the tool names the model was shown."""
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools, **kw):
            seen.setdefault("systems", []).append(system)
            seen.setdefault("tools", []).append(
                [str(t.get("name") or "") for t in (tools or []) if isinstance(t, dict)]
            )
            return await real_complete(system=system, messages=messages, tools=tools, **kw)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get
    return seen


# --------------------------------------------------------------------------- #
# 1. the folder and AGENTS.md
# --------------------------------------------------------------------------- #


def test_register_creates_the_folder_with_agents_md_equal_to_the_prompt(platform, registry, files):
    registry.register(NAME, PROMPT, TOOLS)
    folder = Path(platform.config.home) / "agents" / NAME
    assert folder.is_dir()
    assert (folder / "AGENTS.md").read_text("utf-8") == PROMPT
    assert files.read_instructions(NAME) == PROMPT
    assert files.list_revisions(NAME) == []
    # The row is still the mirror the runtime reads.
    assert registry.get(NAME).system_prompt == PROMPT


def test_a_prompt_change_keeps_the_old_text_as_a_revision(registry, files):
    registry.register(NAME, PROMPT, TOOLS)
    registry.register(NAME, PROMPT2, TOOLS, prompt_reason="tightened the brief")
    assert files.read_instructions(NAME) == PROMPT2
    revs = files.list_revisions(NAME)
    assert len(revs) == 1
    rev = revs[0]
    assert rev["reason"] == "tightened the brief"
    assert rev["bytes"] == len(PROMPT.encode("utf-8"))
    assert rev["id"].endswith("-tightened-the-brief")
    # The header is three lines, then the previous text verbatim.
    raw = (files.folder(NAME) / "revisions" / f"{rev['id']}.md").read_text("utf-8")
    assert raw.startswith("# revision\nreason: tightened the brief\nat: ")
    assert raw.endswith("\n\n" + PROMPT)
    assert files.read_revision(NAME, rev["id"])["text"] == PROMPT
    # Control: an unchanged re-register writes NO revision and keeps the file.
    registry.register(NAME, PROMPT2, TOOLS, description="renamed only")
    assert len(files.list_revisions(NAME)) == 1
    assert files.read_instructions(NAME) == PROMPT2


def test_the_revision_cap_prunes_the_oldest_and_lists_newest_first(files):
    for i in range(MAX_REVISIONS + 5):
        files.write_instructions(NAME, f"text v{i}", reason=f"step {i}")
    revs = files.list_revisions(NAME)
    assert len(revs) == MAX_REVISIONS
    kept = [files.read_revision(NAME, r["id"])["text"] for r in revs]
    # Newest first: the last revision holds the text just before the final write.
    assert kept[0] == f"text v{MAX_REVISIONS + 3}"
    assert kept[-1] == "text v4"  # v0..v3 pruned
    assert "text v0" not in kept
    assert files.read_instructions(NAME) == f"text v{MAX_REVISIONS + 4}"
    # Same-second writes stay in write order (the header's `at` orders them).
    ats = [r["at"] for r in revs]
    assert ats == sorted(ats, reverse=True)


def test_pruning_unlinks_only_revision_files(files):
    """REVIEW v1.297.0: the prune is the one deliberate delete in the folder,
    so it must reach ONLY `revisions/*.md`. A stray file the user dropped in
    that folder (and anything that is not a revision) survives an overflow."""
    files.write_instructions(NAME, "seed")
    rdir = files.folder(NAME) / "revisions"
    rdir.mkdir(parents=True, exist_ok=True)
    stray = rdir / "my-notes.txt"
    stray.write_text("do not touch", encoding="utf-8")
    nested = rdir / "keep"
    nested.mkdir()
    (nested / "old.md").write_text("not a revision", encoding="utf-8")
    for i in range(MAX_REVISIONS + 5):
        files.write_instructions(NAME, f"text v{i}", reason=f"step {i}")
    assert len(files.list_revisions(NAME)) == MAX_REVISIONS
    assert stray.read_text(encoding="utf-8") == "do not touch"
    assert (nested / "old.md").read_text(encoding="utf-8") == "not a revision"
    # The listing never reports the stray as a revision either.
    assert all(r["id"] != "my-notes" for r in files.list_revisions(NAME))


def test_restore_puts_the_old_text_back_and_itself_leaves_a_revision(files):
    files.write_instructions(NAME, PROMPT, reason="hire")
    files.write_instructions(NAME, PROMPT2, reason="edit")
    first = files.list_revisions(NAME)[0]
    assert files.read_revision(NAME, first["id"])["text"] == PROMPT
    info = files.restore_revision(NAME, first["id"])
    assert info.text == PROMPT and info.changed
    assert files.read_instructions(NAME) == PROMPT
    revs = files.list_revisions(NAME)
    assert len(revs) == 2
    assert revs[0]["reason"].startswith("restore ")
    assert files.read_revision(NAME, revs[0]["id"])["text"] == PROMPT2
    with pytest.raises(KeyError):
        files.restore_revision(NAME, "no-such-revision")


# --------------------------------------------------------------------------- #
# 2. NOTES.md
# --------------------------------------------------------------------------- #


def test_notes_write_append_cap_and_the_injection_marker(files):
    assert files.read_notes(NAME) == ""
    assert files.notebook_block(NAME) == ""
    path = files.append_notes(NAME, "- [2026-10-01] first")
    assert path == str(files.folder(NAME) / "NOTES.md")
    files.append_notes(NAME, "- [2026-10-01] second")
    assert files.read_notes(NAME) == "- [2026-10-01] first\n- [2026-10-01] second\n"
    block = files.notebook_block(NAME)
    assert block.startswith("# Your notebook\n")
    assert "- [2026-10-01] second" in block and TRIM_MARKER not in block
    # The cap: oldest lines go, the newest stays, nothing raises.
    lines = [f"line {i:05d} " + "x" * 60 for i in range(400)]
    files.write_notes(NAME, "\n".join(lines) + "\n")
    kept = files.read_notes(NAME)
    assert len(kept.encode("utf-8")) <= NOTES_CAP_BYTES
    assert "line 00399" in kept and "line 00000" not in kept
    files.append_notes(NAME, "line 00400 newest")
    kept = files.read_notes(NAME)
    assert len(kept.encode("utf-8")) <= NOTES_CAP_BYTES
    assert kept.endswith("line 00400 newest\n")
    # The injection: head + tail around the marker, at most the limit.
    block = files.notebook_block(NAME)
    body = block.split("\n\n", 1)[1]
    assert TRIM_MARKER in body
    assert len(body) <= NOTEBOOK_INJECT_CHARS
    assert body.endswith("line 00400 newest")
    assert body.startswith(kept[:100])
    # The pure trimmer: unchanged when it fits.
    assert trim_head_tail("short", 10) == "short"
    assert len(trim_head_tail("a" * 100, 40)) <= 40


# --------------------------------------------------------------------------- #
# 3. confinement
# --------------------------------------------------------------------------- #


def test_a_hostile_name_is_sanitised_and_the_second_lock_refuses_an_escape(tmp_path, monkeypatch):
    files = AgentFiles(tmp_path)
    root = (tmp_path / "agents").resolve()
    for hostile in ("../x", "a/b", "..\\..\\etc", "nul", "C:\\Windows", "a b"):
        slug = agent_slug(hostile)
        assert "/" not in slug and "\\" not in slug and ".." not in slug
        folder = files.folder(hostile).resolve()
        assert folder.parent == root, (hostile, folder)
    assert agent_slug("a/b") != agent_slug("a_b")  # the digest keeps them apart
    assert agent_slug(agent_slug("a/b")) == agent_slug("a/b")  # idempotent
    with pytest.raises(ValueError):
        files.folder("")
    # A revision id cannot reach outside revisions/.
    files.write_instructions(NAME, PROMPT)
    files.write_instructions(NAME, PROMPT2)
    assert files.read_revision(NAME, "../AGENTS") is None
    assert files.read_revision(NAME, "../../NOTES") is None
    # THE SECOND LOCK: even a sanitizer that leaked a separator is refused by
    # the containment check (the mutation that deletes it goes red here).
    monkeypatch.setattr(files_mod, "agent_slug", lambda name: "../escaped")
    with pytest.raises(ValueError):
        files.folder("anything")
    assert not (tmp_path / "escaped").exists()


# --------------------------------------------------------------------------- #
# 4. remove = move to the trash
# --------------------------------------------------------------------------- #


def test_remove_moves_the_folder_to_the_trash_and_deletes_nothing(platform, registry, files):
    registry.register(NAME, PROMPT, TOOLS)
    registry.register(NAME, PROMPT2, TOOLS)
    files.append_notes(NAME, "- [2026-10-01] keep me")
    folder = files.folder(NAME)
    assert registry.remove(NAME) is True
    assert not folder.exists()
    trash = Path(platform.config.home) / "trash"
    moved = [p for p in trash.iterdir() if p.name.startswith(f"agents-{NAME}-")]
    assert len(moved) == 1
    assert (moved[0] / "AGENTS.md").read_text("utf-8") == PROMPT2
    assert (moved[0] / "NOTES.md").read_text("utf-8") == "- [2026-10-01] keep me\n"
    assert len(list((moved[0] / "revisions").iterdir())) == 1
    # A second agent with no folder removes cleanly too.
    assert files.remove("never-registered") is None


# --------------------------------------------------------------------------- #
# 5. backfill on load; a bare registry keeps no folders
# --------------------------------------------------------------------------- #


def test_load_backfills_a_missing_agents_md_and_never_clobbers_one(platform, tmp_path):
    bare = DynamicAgentRegistry(platform.engine)  # files=None: the CLI / test shape
    bare.register("oldie", "hired before the folder existed", TOOLS)
    bare.register("edited", "the row's text", TOOLS)
    assert bare.files is None
    assert not (Path(platform.config.home) / "agents" / "oldie").exists()
    store = AgentFiles(platform.config.home)
    store.write_instructions("edited", "the user's own edit on disk")
    fresh = DynamicAgentRegistry(platform.engine, files=store).load()
    assert fresh.get("oldie") is not None
    assert store.read_instructions("oldie") == "hired before the folder existed"
    assert store.list_revisions("oldie") == []  # a backfill is not an edit
    assert store.read_instructions("edited") == "the user's own edit on disk"
    assert store.list_revisions("edited") == []
    # And the bare registry's remove does not touch the disk.
    assert bare.remove("edited") is True
    assert store.read_instructions("edited") == "the user's own edit on disk"


# --------------------------------------------------------------------------- #
# 6. the runtime: the notebook reaches the custom run's prompt, not a builtin's
# --------------------------------------------------------------------------- #


async def test_runtime_injects_the_notebook_and_arms_the_tool_for_a_custom_run_only(
    platform, registry, files, tmp_path
):
    registry.register(NAME, PROMPT, TOOLS)
    files.append_notes(NAME, "- [2026-10-01] NOTEBOOK-MARK-1297 the K-1s are in /tax")
    seen = _spy_complete(platform, {})
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": "summarize"},
        _ctx(platform, tmp_path), platform.permissions,
    )
    assert result.ok, result.error
    system = seen["systems"][0]
    assert "# Your notebook" in system and "NOTEBOOK-MARK-1297" in system
    assert "notebook" in seen["tools"][0]
    assert "read_file" in seen["tools"][0]  # the roster is untouched
    # A builtin gets neither the block nor the tool.
    seen["systems"].clear()
    seen["tools"].clear()
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": "builder", "task": "summarize"},
        _ctx(platform, tmp_path, agent_run_id="parent2"), platform.permissions,
    )
    assert result.ok, result.error
    assert "# Your notebook" not in seen["systems"][0]
    assert "NOTEBOOK-MARK-1297" not in seen["systems"][0]
    assert "notebook" not in seen["tools"][0]
    # An EMPTY notebook adds no block (and the tool is still there to fill it).
    files.write_notes(NAME, "")
    seen["systems"].clear()
    seen["tools"].clear()
    result = await platform.registry.invoke(
        "spawn_agent", {"agent": NAME, "task": "summarize"},
        _ctx(platform, tmp_path, agent_run_id="parent3"), platform.permissions,
    )
    assert result.ok, result.error
    assert "# Your notebook" not in seen["systems"][0]
    assert "notebook" in seen["tools"][0]


# --------------------------------------------------------------------------- #
# 7. the notebook tool
# --------------------------------------------------------------------------- #


def test_notebook_tool_is_registered_and_allowed_by_default(platform):
    assert default_permissions()["notebook"] == "allow"
    assert platform.registry.get("notebook") is not None
    assert platform.config.permissions["notebook"] == "allow"


async def test_notebook_tool_appends_for_the_calling_custom_agent_and_refuses_a_builtin(
    platform, registry, files, tmp_path
):
    registry.register(NAME, PROMPT, TOOLS)
    tool = NotebookTool(platform)
    custom_sid = _session_row(platform.engine, f"custom:{NAME}")
    builtin_sid = _session_row(platform.engine, "builder")
    ghost_sid = _session_row(platform.engine, "custom:never-hired")

    res = await tool.execute(
        {"action": "append", "text": "the K-1s live in /tax"},
        _ctx(platform, tmp_path, session_id=custom_sid),
    )
    assert res.ok, res.error
    path = str(files.folder(NAME) / "NOTES.md")
    assert res.output == f"noted in {path}" and res.data["abs_path"] == path
    today = datetime.now().strftime("%Y-%m-%d")
    notes = files.read_notes(NAME)
    assert notes.startswith(f"- [{today}] the K-1s live in /tax\n") or (
        notes.startswith("- [20") and "] the K-1s live in /tax\n" in notes
    )
    assert stamp_line("x", datetime(2026, 10, 1)) == "- [2026-10-01] x"
    assert stamp_line("two\nlines", datetime(2026, 10, 1)) == "- [2026-10-01] two\n  lines"

    read = await tool.execute({"action": "read"}, _ctx(platform, tmp_path, session_id=custom_sid))
    assert read.ok and "the K-1s live in /tax" in read.output

    rep = await tool.execute(
        {"action": "replace", "text": "fresh page\n"},
        _ctx(platform, tmp_path, session_id=custom_sid),
    )
    assert rep.ok and rep.output == f"notebook replaced at {path}"
    assert files.read_notes(NAME) == "fresh page\n"

    # A builtin session, an unknown custom agent, a chat/unknown session id,
    # and no context at all: refused, nothing written.
    for sid in (builtin_sid, ghost_sid, "chat", "no-such-session"):
        res = await tool.execute(
            {"action": "append", "text": "sneak"}, _ctx(platform, tmp_path, session_id=sid)
        )
        assert not res.ok and res.error == NO_NOTEBOOK, sid
    res = await tool.execute({"action": "append", "text": "sneak"}, None)
    assert not res.ok and res.error == NO_NOTEBOOK
    assert files.read_notes(NAME) == "fresh page\n"
    assert not files.exists("never-hired")
    # Bad arguments are plain words, not tracebacks.
    bad = await tool.execute({"action": "burn"}, _ctx(platform, tmp_path, session_id=custom_sid))
    assert not bad.ok and "action must be one of" in bad.error
    empty = await tool.execute({"action": "append"}, _ctx(platform, tmp_path, session_id=custom_sid))
    assert not empty.ok and "text is required" in empty.error


# --------------------------------------------------------------------------- #
# 8. the routes, over the real app
# --------------------------------------------------------------------------- #


def _hire(client, name=NAME, prompt=PROMPT):
    r = client.post("/agents", json={"name": name, "system_prompt": prompt, "tools": TOOLS})
    assert r.status_code == 200, r.text
    return r.json()


def _row(client, name):
    return next(r for r in client.get("/agents").json()["dynamic"] if r["name"] == name)


def test_files_view_and_the_404s(client):
    _hire(client)
    r = client.get(f"/agents/{NAME}/files")
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["name"] == NAME
    assert view["instructions"] == PROMPT
    assert view["notes"] == ""
    assert view["revisions"] == []
    folder = Path(view["folder"])
    assert folder.is_absolute() and folder.name == NAME and (folder / "AGENTS.md").is_file()
    assert client.get("/agents/builder/files").status_code == 404
    assert client.get("/agents/builder/files").json()["detail"] == "only your own agents have a folder"
    assert client.get("/agents/nobody/files").status_code == 404
    assert client.get("/agents/nobody/files").json()["detail"] == "unknown agent"
    assert client.put("/agents/builder/files/notes", json={"text": "x"}).status_code == 404
    assert client.get(f"/agents/{NAME}/files/revisions/nope").status_code == 404
    assert client.post(f"/agents/{NAME}/files/revisions/nope/restore").status_code == 404


def test_put_instructions_mirrors_into_the_row_and_leaves_a_revision_then_restore(client):
    _hire(client)
    r = client.put(
        f"/agents/{NAME}/files/instructions",
        json={"text": PROMPT2, "reason": "after the first week"},
    )
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["instructions"] == PROMPT2
    assert len(view["revisions"]) == 1
    rev = view["revisions"][0]
    assert rev["reason"] == "after the first week"
    # The row is the mirror the runtime reads: GET /agents shows the new text.
    assert _row(client, NAME)["system_prompt"] == PROMPT2
    # The old text is readable by id.
    r = client.get(f"/agents/{NAME}/files/revisions/{rev['id']}")
    assert r.status_code == 200
    assert r.json()["text"] == PROMPT and r.json()["id"] == rev["id"]
    assert r.json()["reason"] == "after the first week" and r.json()["at"]
    # Restore: the old text is back in the row AND the file, and the text it
    # replaced is kept as a revision of its own.
    r = client.post(f"/agents/{NAME}/files/revisions/{rev['id']}/restore")
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["instructions"] == PROMPT
    assert _row(client, NAME)["system_prompt"] == PROMPT
    assert len(view["revisions"]) == 2
    assert view["revisions"][0]["reason"] == f"restore {rev['id']}"
    newest = client.get(f"/agents/{NAME}/files/revisions/{view['revisions'][0]['id']}").json()
    assert newest["text"] == PROMPT2
    # The job card survived both writes (register kept the omitted kwargs).
    assert _row(client, NAME)["tools"] == TOOLS
    # The PATCH door writes the same file (one mirror, two doors).
    r = client.patch(f"/agents/{NAME}", json={"system_prompt": "patched"})
    assert r.status_code == 200, r.text
    assert client.get(f"/agents/{NAME}/files").json()["instructions"] == "patched"
    assert len(client.get(f"/agents/{NAME}/files").json()["revisions"]) == 3


def test_put_notes_and_open_folder(client, monkeypatch):
    _hire(client)
    r = client.put(f"/agents/{NAME}/files/notes", json={"text": "- [2026-10-01] remembered\n"})
    assert r.status_code == 200, r.text
    assert r.json()["notes"] == "- [2026-10-01] remembered\n"
    folder = Path(r.json()["folder"])
    assert (folder / "NOTES.md").read_text("utf-8") == "- [2026-10-01] remembered\n"
    opened: list[str] = []
    monkeypatch.setattr(agents_routes, "_open_folder", lambda path: opened.append(path))
    r = client.post(f"/agents/{NAME}/files/open")
    assert r.status_code == 200, r.text
    assert r.json() == {"opened": str(folder)}
    assert opened == [str(folder)]
    # Deleting the agent moves its folder to the trash — the view is gone.
    assert client.delete(f"/agents/{NAME}").status_code == 200
    assert client.get(f"/agents/{NAME}/files").status_code == 404
    assert not folder.exists()
    trash = folder.parent.parent / "trash"
    assert any(p.name.startswith(f"agents-{NAME}-") for p in trash.iterdir())


# --------------------------------------------------------------------------- #
# 9. the skills routes carry provenance (the curator, same wave)
# --------------------------------------------------------------------------- #


def test_skills_rows_and_detail_carry_created_by_and_pinned(client):
    from iron_jarvis.skills import save_skill

    r = client.post("/skills", json={"name": "user-made", "instructions": "do the user thing"})
    assert r.status_code == 200, r.text
    home = client.app.state.platform.config.home
    save_skill(home / "skills", "agent-made", "an agent wrote it", "do the agent thing",
               created_by="agent", pinned=True)
    assert client.post("/skills/rescan").status_code == 200
    rows = {s["name"]: s for s in client.get("/skills").json()["skills"]}
    assert rows["user-made"]["created_by"] == "user" and rows["user-made"]["pinned"] is False
    assert rows["agent-made"]["created_by"] == "agent" and rows["agent-made"]["pinned"] is True
    assert {"name", "description", "source"} <= set(rows["agent-made"])  # every old key kept
    one = client.get("/skills/agent-made").json()
    assert one["created_by"] == "agent" and one["pinned"] is True
    assert one["instructions"] == "do the agent thing" and one["source"]
    plain = client.get("/skills/user-made").json()
    assert plain["created_by"] == "user" and plain["pinned"] is False
