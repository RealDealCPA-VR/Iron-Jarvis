"""v1.538.0 (fourth review, group 29: OT7, OT8, OT9): the other tools' chats
keep being listed and remembered when a file, a folder or a network share
misbehaves.

* OT7: one Claude Chat Cowork file vanishing mid-listing skips only that
  file; a Cowork folder that cannot be read keeps the rows already recorded.
* OT8: a Claude Code chat whose first record is too big for the head window
  (a pasted picture) takes its folder, its title and what started it from the
  end of the file.
* OT9: a chat in a network folder is taken as present with no disk call: a
  network path by its text, a mapped drive by Windows' own answer, asked in a
  child process (bounded, kept per drive letter).

Every test points ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` / Claude desktop's
folder at its own folders and writes the files itself.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from iron_jarvis.core import procs
from iron_jarvis.core.db import session_scope
from iron_jarvis.daemon import chat_turn
from iron_jarvis.daemon.app import create_app
from iron_jarvis.history import index as HI
from iron_jarvis.mcpserver import apps
from iron_jarvis.workmap import outside
from iron_jarvis.workmap.index import work_map
from iron_jarvis.workmap.models import KIND_OUTSIDE, WorkEntry

C1 = "11111111-1111-4111-8111-111111111111"
C2 = "22222222-2222-4222-8222-222222222222"
X1 = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
X2 = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
X3 = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"


@pytest.fixture()
def scene(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_turn, "GROUNDING_SOFT_S", 60.0)
    claude, codex = tmp_path / "claude-home", tmp_path / "codex-home"
    claude.mkdir()
    codex.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("CODEX_HOME", str(codex))
    other = tmp_path / "other-apps"
    monkeypatch.setattr(apps, "TEST_ROOT", str(other))
    monkeypatch.setattr(outside, "READS_DISK", True)
    temp = tmp_path / "pc-temp"
    temp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(temp))
    monkeypatch.setattr(outside, "SCRATCH_MARKERS", ())
    outside.forget_cache()
    app = create_app(str(tmp_path / "home"))
    folder = tmp_path / "Reports"
    folder.mkdir()

    class Scene:
        pass

    s = Scene()
    s.app, s.client, s.platform = app, TestClient(app), app.state.platform
    s.engine = s.platform.engine
    s.folder, s.tmp, s.claude, s.codex = folder, tmp_path, claude, codex
    s.cowork = outside.cowork_root()
    s.wm = work_map(s.engine, s.platform.config.home)
    yield s
    outside.forget_cache()


def _rows(scene) -> dict[str, WorkEntry]:
    with session_scope(scene.engine) as db:
        return {e.title: e for e in db.exec(select(WorkEntry).where(WorkEntry.kind == KIND_OUTSIDE))}


def _titles(view, tool) -> list[str]:
    return [c["title"] for t in view["tools"] if t["tool"] == tool for f in t["folders"] for c in f["chats"]]


# --------------------------------------------------------------------------- #
# OT7: Claude Chat's Cowork sessions
# --------------------------------------------------------------------------- #
def _cowork(scene, sid: str, title: str, folder: str, last: int = 1_789_000_000_000) -> Path:
    d = scene.cowork / "acct" / "org"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"local_{sid}.json"
    path.write_text(json.dumps({
        "sessionId": sid, "title": title, "userSelectedFolders": [folder],
        "createdAt": last - 60_000, "lastActivityAt": last, "isArchived": False,
    }), encoding="utf-8")
    return path


def test_a_cowork_file_vanishing_mid_listing_skips_only_that_file(scene, monkeypatch):
    _cowork(scene, "c1", "Organise the photos", str(scene.folder))
    _cowork(scene, "c2", "Plan the website", str(scene.folder), last=1_789_000_100_000)
    scene.wm.refresh(force=True)
    assert set(_rows(scene)) == {"Organise the photos", "Plan the website"}
    real_glob = Path.glob
    vanished = scene.cowork / "acct" / "org" / "local_gone.json"

    def glob(self, pattern, *a, **kw):  # the race: listed, then deleted before its stat
        found = list(real_glob(self, pattern, *a, **kw))
        if Path(self) == scene.cowork:
            found.append(vanished)
        return iter(found)

    monkeypatch.setattr(Path, "glob", glob)
    outside.forget_cache()
    assert sorted(c["title"] for c in outside.cowork_sessions()) == ["Organise the photos", "Plan the website"]
    view = outside.listing_view()
    assert sorted(_titles(view, "claude-chat")) == ["Organise the photos", "Plan the website"]
    scene.wm.refresh(force=True)
    assert set(_rows(scene)) == {"Organise the photos", "Plan the website"}


def test_a_cowork_folder_that_cannot_be_read_keeps_the_rows(scene, monkeypatch):
    _cowork(scene, "c1", "Organise the photos", str(scene.folder))
    scene.wm.refresh(force=True)
    assert set(_rows(scene)) == {"Organise the photos"}
    real_scandir = os.scandir
    root = os.path.normcase(str(scene.cowork))

    def scandir(path="."):
        if os.path.normcase(os.fspath(path)) == root:
            raise PermissionError(13, "Access is denied", str(path))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", scandir)
    outside.forget_cache()
    # The sidebar's list has nothing to show from that folder and says the
    # listing is not whole ...
    view = outside.listing_view()
    assert _titles(view, "claude-chat") == [] and view["whole"] is False
    # ... but the work record keeps what it holds: the folder said nothing.
    scene.wm.refresh(force=True)
    assert set(_rows(scene)) == {"Organise the photos"}
    # A folder that is simply not there means no Cowork sessions.
    monkeypatch.setattr(os, "scandir", real_scandir)
    for p in sorted(scene.cowork.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    scene.cowork.rmdir()
    scene.wm.refresh(force=True)
    assert _rows(scene) == {}


# --------------------------------------------------------------------------- #
# OT8: a Claude Code chat whose first record is bigger than the head window
# --------------------------------------------------------------------------- #
def _big_first_claude(scene, sid: str, *, entry: str = "claude-desktop", words: str = "Tidy the report folder",
                      ai_title: str = "") -> Path:
    folder = str(scene.folder)
    home = scene.claude / "projects" / "some-slug"
    home.mkdir(parents=True, exist_ok=True)
    picture = "A" * (HI.HEAD_BYTES + 40 * 1024)  # a pasted picture, bigger than the head window
    lines = [
        {"type": "user", "sessionId": sid, "cwd": folder, "entrypoint": entry,
         "timestamp": "2026-11-10T10:00:00.000Z",
         "message": {"role": "user", "content": [
             {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": picture}}]}},
        {"type": "assistant", "sessionId": sid, "cwd": folder, "entrypoint": entry,
         "timestamp": "2026-11-10T10:01:00.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "I see a chart."}]}},
        {"type": "user", "sessionId": sid, "cwd": folder, "entrypoint": entry,
         "timestamp": "2026-11-10T10:02:00.000Z", "message": {"role": "user", "content": words}},
        {"type": "assistant", "sessionId": sid, "cwd": folder, "entrypoint": entry,
         "timestamp": "2026-11-10T10:05:00.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
    ]
    if ai_title:
        lines.append({"type": "ai-title", "sessionId": sid, "aiTitle": ai_title})
    path = home / f"{sid}.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    assert path.stat().st_size > HI.HEAD_BYTES
    return path


def test_a_chat_whose_first_record_is_too_big_takes_its_folder_and_title_from_the_end(scene):
    path = _big_first_claude(scene, C1)
    head, tail = HI._head_tail(path, path.stat().st_size)
    assert head == []  # the shape: nothing readable in the head window
    from iron_jarvis.history import claude_code

    fields = claude_code.summarize(head, tail, path)
    assert fields["project"] == str(scene.folder)
    assert fields["title"] == "Tidy the report folder"
    view = outside.listing_view()
    claude = next(t for t in view["tools"] if t["tool"] == "claude-code")
    assert [(f["folder"], [c["title"] for c in f["chats"]]) for f in claude["folders"]] == [
        (str(scene.folder), ["Tidy the report folder"])]
    scene.wm.refresh(force=True)
    row = _rows(scene)["Tidy the report folder"]
    assert row.tool == "Claude Code" and row.folder == str(scene.folder)


def test_its_own_recorded_title_wins_over_a_later_prompt(scene):
    _big_first_claude(scene, C1, ai_title="Read the sales chart")
    assert _titles(outside.listing_view(), "claude-code") == ["Read the sales chart"]


def test_a_program_started_chat_with_a_big_first_record_stays_left_out(scene):
    _big_first_claude(scene, C2, entry="sdk-cli")
    view = outside.listing_view()
    assert _titles(view, "claude-code") == [] and view["hidden"] == 1


# --------------------------------------------------------------------------- #
# OT9: a network folder is never asked of the disk
# --------------------------------------------------------------------------- #
def _codex(scene, sid: str, words: str, folder: str, day: int = 11) -> Path:
    home = scene.codex / "sessions" / "2026" / "11" / f"{day:02d}"
    home.mkdir(parents=True, exist_ok=True)
    stamp = f"2026-11-{day:02d}T09:00:00.000Z"
    lines = [
        {"timestamp": stamp, "type": "session_meta",
         "payload": {"id": sid, "cwd": folder, "originator": "codex_cli_rs", "source": "cli", "timestamp": stamp}},
        {"timestamp": stamp, "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": words}]}},
    ]
    path = home / f"rollout-2026-11-{day:02d}T09-00-00-{sid}.jsonl"
    path.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return path


def _no_disk_for(monkeypatch, *prefixes: str) -> list[str]:
    """``os.path.isdir`` fails the test when asked about a folder under one of
    ``prefixes``; it answers for every other folder and records each ask."""
    real = os.path.isdir
    asked: list[str] = []

    def isdir(path):
        text = os.fspath(path)
        asked.append(text)
        if any(text.lower().startswith(p.lower()) for p in prefixes):
            pytest.fail(f"the disk was asked about a network folder: {text}")
        return real(path)

    monkeypatch.setattr(os.path, "isdir", isdir)
    return asked


@pytest.mark.parametrize("share", [r"\\nas\shared\website", "//nas/shared/website", r"\\?\UNC\nas\shared\website"])
def test_a_chat_in_a_network_folder_is_listed_with_no_disk_call(scene, monkeypatch, share):
    _codex(scene, X1, "Update the website copy", share)
    _codex(scene, X2, "Fix the import script", str(scene.folder), day=12)
    asked = _no_disk_for(monkeypatch, "\\\\", "//")
    monkeypatch.setattr(procs, "run", lambda *a, **kw: pytest.fail("a network path is judged by its text"))
    view = outside.listing_view()
    assert sorted(_titles(view, "codex")) == ["Fix the import script", "Update the website copy"]
    assert asked == [str(scene.folder)]  # the local folder is still looked at


class _Child:
    """Stands in for the drive-type child (``core/procs.run``): answers each
    letter with its Windows drive type, records every start."""

    def __init__(self, kinds: dict[str, int] | None = None, *, timed_out: bool = False):
        self.kinds, self.timed_out, self.calls = kinds or {}, timed_out, []

    def __call__(self, argv, *, timeout, **kw):
        self.calls.append((list(argv), timeout))
        if self.timed_out:
            return procs.Result(-1, b"", b"", timed_out=True, args=argv, timeout=timeout)
        out = "".join(f"{k}={v}\r\n" for k, v in self.kinds.items() if f"'{k}'" in argv[-1])
        return procs.Result(0, out.encode(), b"", args=argv, timeout=timeout)


@pytest.fixture()
def windows(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(outside, "ON_WINDOWS", True)
    monkeypatch.setattr(outside, "drive_clock", lambda: clock[0])
    outside.forget_cache()
    return clock


def test_a_mapped_drive_is_asked_of_a_child_once_per_letter(scene, monkeypatch, windows):
    _codex(scene, X1, "Update the website copy", r"Z:\shared\website")
    _codex(scene, X2, "Sort the photos", r"Z:\shared\photos", day=12)
    child = _Child({"Z": 4, "Y": 4})
    monkeypatch.setattr(procs, "run", child)
    _no_disk_for(monkeypatch, "Z:", "Y:")
    view = outside.listing_view()
    assert sorted(_titles(view, "codex")) == ["Sort the photos", "Update the website copy"]
    assert len(child.calls) == 1  # one child for the letter, not one per folder
    argv, timeout = child.calls[0]
    assert timeout == outside.DRIVE_ASK_S and "'Z'" in argv[-1]
    outside.listing_view()
    assert len(child.calls) == 1  # kept per letter: the next listing asks nothing
    _codex(scene, X3, "Plan the newsletter", r"Y:\letters", day=13)
    assert "Plan the newsletter" in _titles(outside.listing_view(), "codex")
    assert len(child.calls) == 2 and "'Y'" in child.calls[1][0][-1]
    windows[0] += outside.DRIVE_KEEP_S + 1  # kept for a while, then asked again
    outside.listing_view()
    assert len(child.calls) == 4


def test_a_child_that_runs_out_of_time_counts_as_present(scene, monkeypatch, windows):
    _codex(scene, X1, "Update the website copy", r"Z:\shared\website")
    child = _Child(timed_out=True)
    monkeypatch.setattr(procs, "run", child)
    _no_disk_for(monkeypatch, "Z:")
    assert _titles(outside.listing_view(), "codex") == ["Update the website copy"]
    outside.listing_view()
    assert len(child.calls) == 1  # and that answer is kept too: no second wait


def test_a_local_drive_is_still_looked_at_and_a_gone_folder_left_out(scene, monkeypatch, windows):
    _codex(scene, X1, "Update the website copy", r"Q:\no-such-folder\website")
    child = _Child({"Q": 3})  # DRIVE_FIXED
    monkeypatch.setattr(procs, "run", child)
    asked = _no_disk_for(monkeypatch)
    view = outside.listing_view()
    assert _titles(view, "codex") == [] and view["hidden"] == 1
    assert asked == [r"Q:\no-such-folder\website"] and len(child.calls) == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows drive types")
def test_the_real_child_answers_for_a_real_drive(monkeypatch):
    # The pin is the child's ANSWER, not its speed: a cold PowerShell on a
    # busy runner outlasted the 8 s bound on v1.542.0's release run (an
    # out-of-time child answers "network" for every letter), and this test
    # passed only on its retry. The bound itself is pinned by the timeout
    # tests above.
    monkeypatch.setattr(outside, "DRIVE_ASK_S", 60.0)
    letter = os.environ.get("SystemDrive", "C:")[0].upper()
    kinds = outside._ask_drives([letter])
    assert kinds.get(letter) == 3  # the system drive is a fixed disk
