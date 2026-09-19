"""v1.280.0 — the Build pane's assist knows the user, the project and the pane.

`POST /terminals/{id}/ai` was the one model call in the app whose system
prompt carried nothing about the user: two sentences about being a terminal
assistant, the matched skills, and other panes' output. A pane sitting in a
project folder answered knowing neither the project nor the profile nor a
lesson. Now it gets the same three seams every chat turn gets — the profile
block, the project whose root contains the pane's folder, the lessons — plus
one line naming the pane itself.

`projects/locate.py` is the daemon-side twin of the dashboard's
`projectForCwd`: most specific active root, segment boundary, case-folded.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from iron_jarvis.core.models import Project
from iron_jarvis.daemon.app import create_app
from iron_jarvis.projects.locate import project_context_block, project_for_path
from iron_jarvis.terminals.backend import FakeBackend

_MARK = "MARKER-PROFILE-ABOUT-1280"


def _app(tmp_path, monkeypatch):
    monkeypatch.setattr("iron_jarvis.terminals.session.default_backend", lambda: FakeBackend())
    app = create_app(str(tmp_path))
    return app, TestClient(app)


def _spy_complete(platform, seen: dict):
    real_get = platform.providers.get

    def spy_get(p, m=None):
        adapter = real_get(p, m)
        real_complete = adapter.complete

        async def spy(*, system, messages, tools, **kw):
            seen.setdefault("systems", []).append(system)
            return await real_complete(system=system, messages=messages, tools=tools, **kw)

        adapter.complete = spy
        return adapter

    platform.providers.get = spy_get


# --------------------------------------------------------------------------- #
# 1. Which project a folder belongs to
# --------------------------------------------------------------------------- #


def test_project_for_path_picks_the_most_specific_active_root_at_a_segment_boundary(tmp_path):
    app, client = create_app(str(tmp_path)), None
    engine = app.state.platform.engine
    from iron_jarvis.core.db import session_scope

    with session_scope(engine) as db:
        db.add(Project(id="p_work", name="Work", root=r"C:\work"))
        db.add(Project(id="p_deep", name="Deep", root=r"C:\work\client\deep"))
        db.add(Project(id="p_old", name="Old", root=r"C:\work\client", status="archived"))
        db.add(Project(id="p_none", name="No folder", root=""))
        db.commit()

    assert project_for_path(engine, r"C:\work\client\deep\src").name == "Deep"
    assert project_for_path(engine, r"C:\work\client").name == "Work"  # the archived one never claims
    assert project_for_path(engine, "c:/WORK/notes").name == "Work"  # case + separators folded
    assert project_for_path(engine, r"C:\work").name == "Work"
    assert project_for_path(engine, r"C:\workshop\x") is None  # a segment boundary, not a prefix
    assert project_for_path(engine, "") is None
    assert project_for_path(object(), r"C:\work") is None  # a broken store answers None


def test_the_project_block_names_the_project_and_is_bounded():
    assert project_context_block(None) == ""
    block = project_context_block(
        Project(name="Deep", root=r"C:\work\deep", brief="b" * 5000, instructions="i" * 5000)
    )
    assert block.startswith("# Project\n- Name: Deep")
    assert "- Instructions: " + "i" * 2000 + "\n" in block and "i" * 2001 not in block
    assert "- Brief: " + "b" * 1500 + "\n" in block
    assert block.endswith("- Project folder: C:\\work\\deep")
    assert project_context_block(Project(name="Bare")) == "# Project\n- Name: Bare"


# --------------------------------------------------------------------------- #
# 2. The assist prompt carries the pane, the profile, the project and a lesson
# --------------------------------------------------------------------------- #


def test_assist_inside_a_project_folder_knows_the_user_the_project_and_the_pane(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch)
    platform = app.state.platform
    root = tmp_path / "client-work"
    (root / "sub").mkdir(parents=True)
    assert client.put("/profile", json={"values": {"about": _MARK}}).status_code == 200
    platform.learning.note_preference("Prefers PowerShell over cmd")
    platform.learning.reflect("s1", task="x", summary="Worked: nothing", ok=True)
    made = client.post("/projects", json={"name": "Client Work", "root": str(root), "brief": "Quarterly close"})
    assert made.status_code == 200, made.text
    # Instructions are set after creation (ProjectPatch), as the Projects page does.
    assert client.patch(f"/projects/{made.json()['id']}", json={"instructions": "Use the 2025 templates"}).status_code == 200
    seen: dict = {}
    _spy_complete(platform, seen)

    term = client.post("/terminals", json={"cwd": str(root / "sub"), "name": "PI worker"}).json()
    r = client.post(f"/terminals/{term['id']}/ai", json={"prompt": "what happened?"})
    assert r.status_code == 200, r.text
    system = seen["systems"][-1]
    assert _MARK in system, "the profile block is missing"
    assert "# Project\n- Name: Client Work" in system
    assert "Quarterly close" in system and "Use the 2025 templates" in system
    assert "Prefers PowerShell over cmd" in system, "the lesson is missing"
    assert "Worked: nothing" not in system, "a task reflection reached the prompt"
    assert "This pane is named 'PI worker', in the folder" in system
    # The original contract still holds: it is a terminal assistant, suggest-only.
    assert system.startswith("You are a terminal assistant embedded in a dashboard shell pane")


def test_assist_outside_any_project_carries_no_project_block(tmp_path, monkeypatch):
    app, client = _app(tmp_path, monkeypatch)
    platform = app.state.platform
    seen: dict = {}
    _spy_complete(platform, seen)
    term = client.post("/terminals", json={"cwd": str(tmp_path)}).json()
    r = client.post(f"/terminals/{term['id']}/ai", json={"prompt": "what happened?"})
    assert r.status_code == 200, r.text
    system = seen["systems"][-1]
    assert "# Project" not in system
    assert "This pane is in the folder" in system
