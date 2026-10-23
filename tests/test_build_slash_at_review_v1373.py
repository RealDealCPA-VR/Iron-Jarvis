"""v1.373.0 (IJ harness part 2, Phase 2) — the review's pins for "/" and "@".

Each test here failed on the Phase 2 commit as it was handed over (or, for
the three rules that were already right, went green only because nothing
pinned them — a mutation that broke the rule left the suite green):

* ONE MESSAGE READS AT MOST ``chat_turn._MAX_ATTACHMENTS`` FILES, uploads
  alone included. Phase 2 refused a message past the limit only when it
  carried "@" files; five uploads and no "@" file still went through, the
  lane read four and dropped the fifth without a word, and the saved question
  said all five were sent;
* a message whose files are all blank strings is NOT a message ("Write a
  message first."), never an empty turn sent to the model;
* an "@" file in a PROTECTED folder inside the session's folder is refused
  (``fs_read_ok``) — the rule was in the code, unpinned;
* the folder check and the folder skill's read run OFF the event loop (the
  repo rule: nothing blocking runs on the loop) — unpinned;
* the picked folder skill is armed for the turn's driver task only: the
  caller's context gets its old value back when ``start_build_turn`` returns
  (``reset_picked``) — unpinned.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from iron_jarvis.core import fs_policy
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.routes import build as build_routes
from iron_jarvis.harness import picked_skill
from iron_jarvis.harness import sessions as S
from tests.test_build_sessions_v1367 import (
    Script,
    messages_of,
    new_session,
    open_turn,
    until,
    wire,
)


@pytest.fixture()
def app(tmp_path):
    return create_app(str(tmp_path / "home"))


@pytest.fixture()
def work(tmp_path) -> Path:
    folder = tmp_path / "work"
    folder.mkdir()
    (folder / "app.py").write_text("x = 1\n", encoding="utf-8")
    return folder


def _uploads(tmp_path: Path, n: int) -> list[str]:
    up = tmp_path / "home" / ".ironjarvis" / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    out = []
    for i in range(n):
        f = up / f"u{i}.txt"
        f.write_text(f"upload {i}\n", encoding="utf-8")
        out.append(str(f))
    return out


def _spy_lane(monkeypatch) -> list:
    seen: list = []
    real = build_routes.stream_chat_turn

    async def spy(platform, personas, body, **kw):
        seen.append(body)
        return await real(platform, personas, body, **kw)

    monkeypatch.setattr(build_routes, "stream_chat_turn", spy)
    return seen


async def test_uploads_alone_past_what_one_turn_reads_are_refused_not_cut(app, work, tmp_path, monkeypatch):
    from iron_jarvis.daemon.chat_turn import _MAX_ATTACHMENTS

    sid = new_session(app, work)["id"]
    seen = _spy_lane(monkeypatch)
    script = wire(app, Script(["never"]))
    ups = _uploads(tmp_path, _MAX_ATTACHMENTS + 1)
    status, raw = await open_turn(app, sid, {"text": "read these", "turn_id": "rv-up5", "attachments": ups})
    assert status == 400, raw
    assert f"at most {_MAX_ATTACHMENTS} files" in raw and f"has {_MAX_ATTACHMENTS + 1}" in raw
    # Nothing started: no lane, no model, no question claiming five files.
    assert seen == [] and script.calls == [] and messages_of(app, sid) == []
    # Exactly the limit is a message, as before.
    wire(app, Script(["ok"]))
    status, raw = await open_turn(app, sid, {"text": "read", "turn_id": "rv-up4", "attachments": ups[:_MAX_ATTACHMENTS]})
    assert status == 200, raw
    assert len(seen[0].attachments) == _MAX_ATTACHMENTS


async def test_blank_file_entries_are_not_a_message(app, work, monkeypatch):
    sid = new_session(app, work)["id"]
    seen = _spy_lane(monkeypatch)
    script = wire(app, Script(["never"]))
    for i, body in enumerate(({"files": ["  ", ""]}, {"attachments": [" "]}, {"files": [" "], "attachments": [""]})):
        status, raw = await open_turn(app, sid, {"text": "", "turn_id": f"rv-blank-{i}", **body})
        assert status == 400, raw
        assert "Write a message first" in raw
    assert seen == [] and script.calls == [] and messages_of(app, sid) == []


async def test_a_protected_file_inside_the_folder_is_refused(app, work, monkeypatch):
    vault = work / "vault"
    vault.mkdir()
    (vault / "key.txt").write_text("KEY-MATERIAL-1144\n", encoding="utf-8")
    fs_policy.register_protected_root(vault)
    try:
        sid = new_session(app, work)["id"]
        seen = _spy_lane(monkeypatch)
        script = wire(app, Script(["never"]))
        status, raw = await open_turn(app, sid, {
            "text": "read it", "turn_id": "rv-prot", "files": [str(vault / "key.txt")],
        })
        assert status == 400, raw
        assert "key.txt cannot be read" in raw
        assert "KEY-MATERIAL-1144" not in raw
        assert seen == [] and script.calls == [] and messages_of(app, sid) == []
    finally:
        fs_policy._PROTECTED_ROOTS.discard(fs_policy._canonical(vault))


async def test_the_folder_check_and_the_skill_read_run_off_the_loop(app, work, monkeypatch):
    sd = work / ".ironjarvis" / "harness" / "skills" / "release-notes"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text("---\ndescription: write release notes\n---\nList the changes.\n", encoding="utf-8")
    loop_thread = threading.get_ident()
    where: dict[str, int] = {}
    real_files, real_read = S.folder_files, picked_skill.read_playbook

    def files_spy(*a, **kw):
        where["folder_files"] = threading.get_ident()
        return real_files(*a, **kw)

    def read_spy(*a, **kw):
        where["read_playbook"] = threading.get_ident()
        return real_read(*a, **kw)

    monkeypatch.setattr(S, "folder_files", files_spy)
    monkeypatch.setattr(picked_skill, "read_playbook", read_spy)
    sid = new_session(app, work)["id"]
    wire(app, Script(["ok"]))
    status, raw = await open_turn(app, sid, {
        "text": "notes", "turn_id": "rv-loop", "files": [str(work / "app.py")], "folder_skill": "release-notes",
    })
    assert status == 200, raw
    assert set(where) == {"folder_files", "read_playbook"}
    assert where["folder_files"] != loop_thread, "the folder check ran on the event loop"
    assert where["read_playbook"] != loop_thread, "the folder skill was read on the event loop"


async def test_the_picked_folder_skill_is_reset_for_the_caller(app, work):
    """Called in THIS task (no request task between): when it returns, the
    caller's context holds no picked skill — only the turn's driver task,
    created while it was armed, keeps it."""
    sd = work / ".ironjarvis" / "harness" / "skills" / "release-notes"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text("---\ndescription: write release notes\n---\nPICKED-RESET-MARK\n", encoding="utf-8")
    sid = new_session(app, work)["id"]
    script = wire(app, Script(["Notes."]))
    assert picked_skill.PICKED.get() == ("", "")
    await build_routes.start_build_turn(
        app.state.d, sid, "notes please", "rv-reset", folder_skill="release-notes",
    )
    assert picked_skill.PICKED.get() == ("", ""), "the picked skill leaked into the caller's context"
    # ... while the turn itself still got its playbook.
    await until(lambda: any(m.get("role") == "assistant" for m in messages_of(app, sid)))
    assert "PICKED-RESET-MARK" in script.calls[0]["system"]


async def test_a_listed_folder_skill_with_nothing_to_follow_is_not_called_missing(app, work):
    """A SKILL.md that is only a header (its `description:` is the trigger
    line) passes the layer's validator, so "/" lists it — and picking it said
    "no such skill in this folder", which was false. It is refused for what
    it is, before any model call."""
    sd = work / ".ironjarvis" / "harness" / "skills" / "header-only"
    sd.mkdir(parents=True)
    (sd / "SKILL.md").write_text("---\ndescription: tidy the imports\n---\n", encoding="utf-8")
    sid = new_session(app, work)["id"]
    script = wire(app, Script(["never"]))
    status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "rv-hdr", "folder_skill": "header-only"})
    assert status == 400, raw
    assert "header-only" in raw and "nothing to follow" in raw
    assert "no such skill" not in raw
    assert script.calls == [] and messages_of(app, sid) == []
    # The really missing one keeps its 404.
    status, raw = await open_turn(app, sid, {"text": "go", "turn_id": "rv-none", "folder_skill": "nope"})
    assert status == 404 and "no such skill in this folder: nope" in raw
