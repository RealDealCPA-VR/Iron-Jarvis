"""v1.379.0: a Build session with NO folder.

THE USER: "When in the IJ harness it should not require that I am in a
specific folder. Like when i start here [Claude Code] i get to choose to
attach a folder, but i have use of the harness even without it."

THE RULE, pinned here: ``POST /build/sessions`` without a folder makes a
session marked ``no_folder``. It has NO folder until its FIRST turn starts;
then it gets its OWN folder under the chat files folder
(``config.chat_files_dir`` → ``<date> <first words>``, the v1.244.0 rule — where
the user finds what the agent made), kept on the session and used for every
later turn. A first message that does not start takes that folder back (only
while it is empty). Archiving or deleting the session removes its folder only
while it is EMPTY — a folder holding files is kept, and said so. No git: the
git buttons refuse in words, and a branch is refused at creation. All sessions
with no folder share ONE list group, "No folder". "@" files come from its own
folder. Its library neither keeps nor offers lessons.

Every app here points the chat files folder at a temp folder — nothing is
ever made in the user's Documents.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.daemon.routes.build_git import NO_FOLDER_GIT
from iron_jarvis.harness import sessions as S
from iron_jarvis.providers.adapters.base import ToolCall
from tests.test_build_sessions_v1367 import (
    CLAUDE,
    Script,
    asgi,
    frames_of,
    messages_of,
    new_session,
    open_turn,
    setup_of,
    wire,
)


@pytest.fixture()
def chat_files(tmp_path) -> Path:
    return tmp_path / "chat-files"


@pytest.fixture()
def app(tmp_path, chat_files, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    a = create_app(str(tmp_path / "home"))
    a.state.platform.config.chat_files_root = str(chat_files)
    return a


def _new(app, **extra) -> dict:
    r = TestClient(app).post("/build/sessions", json={"provider": CLAUDE[0], "model": CLAUDE[1], **extra})
    assert r.status_code == 201, r.text
    return r.json()


def _spy_lane(monkeypatch) -> list[dict]:
    seen: list[dict] = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append({"body": body, **kw})
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(build_routes, "stream_chat_turn", spy)
    return seen


def _made(chat_files: Path) -> list[Path]:
    return sorted(p for p in chat_files.iterdir()) if chat_files.is_dir() else []


# --------------------------------------------------------------------------- #
# create and list
# --------------------------------------------------------------------------- #


def test_a_session_starts_with_no_folder_and_makes_nothing_yet(app, chat_files):
    view = _new(app, title="")
    assert view["no_folder"] is True and view["folder"] == "" and view["work_folder"] == ""
    assert view["folder_name"] == S.NO_FOLDER_NAME and view["work_root"] == str(chat_files)
    assert "git" not in view
    assert setup_of(app, view["id"]).get("no_folder") is True
    # An abandoned empty session leaves NOTHING on disk.
    assert _made(chat_files) == []
    again = TestClient(app).get(f"/build/sessions/{view['id']}").json()
    assert again["no_folder"] is True and again["folder"] == ""


def test_a_branch_is_refused_in_words_and_a_folder_session_says_nothing_new(app, tmp_path):
    r = TestClient(app).post("/build/sessions", json={"folder": "", "branch": True})
    assert r.status_code == 400 and r.json()["detail"] == build_routes.NO_FOLDER_NO_BRANCH
    work = tmp_path / "w"
    work.mkdir()
    plain = new_session(TestClient(app), work)
    assert "no_folder" not in plain and "work_folder" not in plain and "work_root" not in plain


def test_sessions_with_no_folder_share_one_list_group(app, tmp_path, chat_files):
    work = tmp_path / "w"
    work.mkdir()
    a = _new(app)["id"]
    new_session(TestClient(app), work)
    b = _new(app)["id"]
    out = TestClient(app).get("/build/sessions").json()
    assert out["work_root"] == str(chat_files)
    groups = out["folders"]
    nofolder = [g for g in groups if g.get("no_folder")]
    assert len(nofolder) == 1 and nofolder[0]["path"] == "" and nofolder[0]["name"] == "No folder"
    assert {s["id"] for s in nofolder[0]["sessions"]} == {a, b}
    assert [g for g in groups if not g.get("no_folder")][0]["path"] == str(work)


# --------------------------------------------------------------------------- #
# the first turn makes its own folder
# --------------------------------------------------------------------------- #


async def test_the_first_turn_makes_its_own_folder_and_later_turns_reuse_it(app, chat_files, monkeypatch):
    sid = _new(app)["id"]
    seen = _spy_lane(monkeypatch)
    wire(app, Script([
        [ToolCall(id="w1", name="Write", arguments={"file_path": "fruits.txt", "content": "apple\n"})],
        "Wrote fruits.txt.",
    ]))
    status, raw = await open_turn(app, sid, {"text": "make a list of fruits", "turn_id": "nf-1"})
    assert status == 200 and frames_of(raw)[-1][0] == "done", raw[-400:]
    made = _made(chat_files)
    assert [p.name for p in made] == [f"{date.today().isoformat()} make a list of fruits"]
    folder = made[0]
    # The turn worked IN it: its file landed there.
    assert (folder / "fruits.txt").read_text(encoding="utf-8") == "apple\n"
    assert seen[0]["body"].workspace_dir == str(folder)
    view = TestClient(app).get(f"/build/sessions/{sid}").json()
    assert view["folder"] == str(folder) and view["work_folder"] == str(folder) and view["no_folder"] is True
    # Still in the "No folder" group (its own folder is not a group).
    groups = TestClient(app).get("/build/sessions").json()["folders"]
    assert [g["no_folder"] for g in groups if g.get("no_folder")] == [True]
    # The next turn uses the SAME folder; nothing new is made.
    await open_turn(app, sid, {"text": "and vegetables", "turn_id": "nf-2"})
    assert _made(chat_files) == [folder] and seen[1]["body"].workspace_dir == str(folder)


async def test_a_first_message_that_never_starts_leaves_no_folder_behind(app, chat_files, monkeypatch):
    sid = _new(app)["id"]
    wire(app, Script(["x"]))
    # An unknown skill: the lane refuses before anything starts.
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/turn", {"text": "hi", "skill": "no-such-skill"})
    assert status == 404
    assert _made(chat_files) == [] and S.folder_of(S.get_row(app.state.platform.engine, sid)) == ""
    assert messages_of(app, sid) == []


async def test_at_files_come_from_its_own_folder(app, chat_files, tmp_path, monkeypatch):
    sid = _new(app)["id"]
    seen = _spy_lane(monkeypatch)
    wire(app, Script(["ok"]))
    await open_turn(app, sid, {"text": "start", "turn_id": "nf-a"})
    folder = _made(chat_files)[0]
    (folder / "notes.md").write_text("NOTES", encoding="utf-8")
    (tmp_path / "elsewhere.md").write_text("NO", encoding="utf-8")
    status, out = await asgi(app, "POST", f"/build/sessions/{sid}/turn",
                             {"text": "read it", "files": [str(tmp_path / "elsewhere.md")]})
    assert status == 400 and "not in this session's folder" in out["detail"]
    status, raw = await open_turn(app, sid, {"text": "read it", "files": ["notes.md"], "turn_id": "nf-b"})
    assert status == 200, raw
    assert seen[-1]["body"].attachments == [str((folder / "notes.md").resolve())]


# --------------------------------------------------------------------------- #
# archive and delete
# --------------------------------------------------------------------------- #


async def test_archive_removes_an_empty_folder_and_keeps_one_with_files(app, chat_files):
    wire(app, Script(["ok"]))
    empty = _new(app)["id"]
    await open_turn(app, empty, {"text": "say hi", "turn_id": "nf-e"})
    kept = _new(app)["id"]
    await open_turn(app, kept, {"text": "make files", "turn_id": "nf-k"})
    by_name = {p.name.split(" ", 1)[1]: p for p in _made(chat_files)}
    (by_name["make files"] / "result.txt").write_text("keep me", encoding="utf-8")
    client = TestClient(app)
    assert client.post(f"/build/sessions/{empty}/archive", json={}).status_code == 200
    assert client.post(f"/build/sessions/{kept}/archive", json={}).status_code == 200
    assert not by_name["say hi"].exists() and S.folder_of(S.get_row(app.state.platform.engine, empty)) == ""
    assert (by_name["make files"] / "result.txt").read_text(encoding="utf-8") == "keep me"
    assert S.folder_of(S.get_row(app.state.platform.engine, kept)) == str(by_name["make files"])


async def test_delete_removes_an_empty_folder_and_says_which_one_it_kept(app, chat_files):
    wire(app, Script(["ok"]))
    a = _new(app)["id"]
    await open_turn(app, a, {"text": "nothing here", "turn_id": "nf-d1"})
    b = _new(app)["id"]
    await open_turn(app, b, {"text": "something here", "turn_id": "nf-d2"})
    by_name = {p.name.split(" ", 1)[1]: p for p in _made(chat_files)}
    (by_name["something here"] / "x.txt").write_text("x", encoding="utf-8")
    client = TestClient(app)
    out_a = client.delete(f"/build/sessions/{a}").json()
    out_b = client.delete(f"/build/sessions/{b}").json()
    assert out_a == {"deleted": a} and not by_name["nothing here"].exists()
    assert out_b == {"deleted": b, "kept_folder": str(by_name["something here"])}
    assert (by_name["something here"] / "x.txt").exists()


async def test_a_branched_session_shares_the_folder_and_it_is_never_removed_under_it(app, chat_files):
    wire(app, Script(["ok"]))
    a = _new(app)["id"]
    await open_turn(app, a, {"text": "shared work", "turn_id": "nf-s"})
    folder = _made(chat_files)[0]
    client = TestClient(app)
    r = client.post(f"/build/sessions/{a}/branch", json={"turn_id": "nf-s", "at": "reply"})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["no_folder"] is True and b["folder"] == str(folder)
    # The origin goes; the folder (empty) stays: the branch still works in it.
    assert client.delete(f"/build/sessions/{a}").status_code == 200
    assert folder.is_dir()
    assert client.delete(f"/build/sessions/{b['id']}").status_code == 200
    assert not folder.exists()


def test_a_folder_outside_the_chat_files_folder_is_never_removed(app, chat_files, tmp_path):
    sid = _new(app)["id"]
    outside = tmp_path / "not-ours"
    outside.mkdir()
    S.set_work_folder(app.state.platform.engine, sid, str(outside))
    assert build_routes.drop_empty_work_folder(app.state.platform, sid) is False
    assert outside.is_dir()


# --------------------------------------------------------------------------- #
# no git, no library
# --------------------------------------------------------------------------- #


def test_the_git_buttons_refuse_in_words_and_the_library_is_empty(app):
    sid = _new(app)["id"]
    client = TestClient(app)
    for method, path in (("GET", "git"), ("GET", "git/diff"), ("POST", "git/commit"),
                         ("POST", "git/push"), ("POST", "git/merge")):
        r = client.request(method, f"/build/sessions/{sid}/{path}", json={} if method == "POST" else None)
        assert r.status_code == 409 and r.json()["detail"] == NO_FOLDER_GIT, (path, r.text)
    lib = client.get(f"/build/sessions/{sid}/library")
    assert lib.status_code == 200
    body = lib.json()
    assert body["lessons"] == [] and body["proposals"] == [] and body["folder_name"] == "No folder"
    # Its "/" lists: no folder skills, no project commands.
    assert client.get(f"/build/sessions/{sid}/skills").json() == {"skills": [], "note": ""}
    assert client.get(f"/build/sessions/{sid}/commands").json() == {"commands": [], "note": ""}


def test_a_session_store_refuses_a_folderless_create_that_did_not_ask_for_one(app):
    with pytest.raises(ValueError):
        S.create(app.state.platform.engine, "", "x", {})
