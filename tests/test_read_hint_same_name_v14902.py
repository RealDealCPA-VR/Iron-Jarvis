"""A refused read also says where a folder of the SAME NAME is readable
(v1.490.2).

Seen live on a local model with reading limited to a home folder: it guessed
the usual place for Downloads, was refused and shown the folders it may read,
listed another folder under the right home, and still told the user that
Downloads could not be read. The Downloads folder was there. The refusal now
names it.
"""
from __future__ import annotations

import os

import pytest

from iron_jarvis.core import fs_policy
from iron_jarvis.core.config import Config
from iron_jarvis.settings import records_self


@pytest.fixture()
def home(tmp_path):
    a = tmp_path / "Sam"
    (a / "Downloads").mkdir(parents=True)
    (a / "Desktop" / "Quotes").mkdir(parents=True)
    (a / "Quotes").mkdir()
    (a / "Downloads" / "bill.pdf").write_bytes(b"x")
    cfg = Config(project_root=tmp_path, home=tmp_path / "h", readable_folders=[str(a)])
    fs_policy.use_config(cfg)
    yield cfg, a, tmp_path
    fs_policy.use_config(None)


def test_a_refused_guess_is_told_where_that_folder_is(home):
    _cfg, a, tmp = home
    ok, why = fs_policy.fs_read_ok(tmp / "Users" / "Sam" / "Downloads")
    assert ok is False
    assert why.startswith(fs_policy.READ_REFUSAL + " Jarvis may read: ")
    assert why.endswith(f"There is a Downloads at {a / 'Downloads'}.")


def test_the_longest_matching_end_wins(home):
    """``Desktop/Quotes`` is named, not the other ``Quotes`` beside it; a
    file is found by its folder and name."""
    _cfg, a, tmp = home
    guess = tmp / "Users" / "Sam" / "Desktop" / "Quotes"
    assert fs_policy.same_name_inside(guess) == f"There is a Desktop / Quotes at {a / 'Desktop' / 'Quotes'}."
    assert fs_policy.same_name_inside(r"D:\Stuff\Downloads\bill.pdf") == (
        f"There is a Downloads / bill.pdf at {a / 'Downloads' / 'bill.pdf'}.")
    assert fs_policy.same_name_inside("C:/Users/Sam/Nowhere/Quotes") == f"There is a Quotes at {a / 'Quotes'}."


def test_a_file_of_the_same_name_somewhere_else_is_not_named(home):
    """One name alone is only ever a folder: ``Private/notes.txt`` is not the
    ``notes.txt`` that happens to sit in a readable folder."""
    _cfg, a, tmp = home
    (a / "notes.txt").write_text("another file", encoding="utf-8")
    assert fs_policy.same_name_inside(tmp / "Private" / "notes.txt") == ""
    ok, why = fs_policy.fs_read_ok(tmp / "Private" / "notes.txt")
    assert ok is False and "There is a" not in why
    # With its folder's name it is the same place.
    (a / "Private").mkdir()
    (a / "Private" / "notes.txt").write_text("x", encoding="utf-8")
    assert fs_policy.same_name_inside(tmp / "Private" / "notes.txt") == (
        f"There is a Private / notes.txt at {a / 'Private' / 'notes.txt'}.")


def test_nothing_is_said_when_no_such_name_is_there(home):
    _cfg, _a, tmp = home
    assert fs_policy.same_name_inside(tmp / "Users" / "Sam" / "Pictures") == ""
    ok, why = fs_policy.fs_read_ok(tmp / "Users" / "Sam" / "Pictures")
    assert ok is False and "There is a" not in why


def test_only_plain_names_are_looked_up(home):
    """Never ``..``, a drive or a stream: the lookup stays inside the folder
    the user named. At most the last three parts are used."""
    _cfg, a, tmp = home
    (tmp / "Outside").mkdir()
    assert fs_policy.same_name_inside("C:/x/../Outside") == ""
    assert fs_policy.same_name_inside("C:/x/..") == ""
    assert fs_policy.same_name_inside("C:/x/Downloads:stream") == ""
    assert fs_policy.same_name_inside("C:") == ""
    assert fs_policy.same_name_inside("") == ""
    deep = a / "one" / "two" / "three" / "four"
    deep.mkdir(parents=True)
    assert fs_policy.same_name_inside("Z:/one/two/three/four") == ""  # four parts are not looked up
    (a / "three" / "four").mkdir(parents=True)
    assert "three / four" in fs_policy.same_name_inside("Z:/one/two/three/four")
    assert fs_policy.SAME_NAME_PARTS == 3


def test_a_drive_in_the_middle_of_a_guess_is_not_followed(home, monkeypatch):
    """``Path.joinpath`` starts again at a part that names a drive, so such a
    part would take the lookup out of the folder the user named."""
    _cfg, a, tmp = home
    (a / "Zeta").mkdir()
    monkeypatch.chdir(a)
    guess = f"q/{tmp.drive or 'C:'}/Zeta"
    assert fs_policy.same_name_inside(guess) == f"There is a Zeta at {a / 'Zeta'}."


def test_the_refused_path_itself_is_never_touched(home, monkeypatch):
    """Only names are taken from it. Every lookup is inside a folder of the
    user's own setting."""
    _cfg, a, _tmp = home
    seen: list[str] = []
    real = os.path.lexists

    def spy(p):
        seen.append(os.fspath(p))
        return real(p)

    monkeypatch.setattr(fs_policy.os.path, "lexists", spy)
    assert fs_policy.same_name_inside(r"\\fileserver\share\Downloads").endswith(f"{a / 'Downloads'}.")
    assert seen and all(s.startswith(str(a)) for s in seen), seen


def test_a_place_jarvis_may_not_read_is_never_named(home, monkeypatch):
    _cfg, a, tmp = home
    vault = a / "vault"
    vault.mkdir()
    fs_policy.register_protected_root(vault)
    assert fs_policy.same_name_inside("C:/Users/Sam/vault") == ""
    # The installation's own limit refuses the folder the setting names.
    monkeypatch.setenv("IRONJARVIS_FS_ALLOWLIST", str(tmp / "Elsewhere"))
    assert fs_policy.same_name_inside("C:/Users/Sam/Downloads") == ""


def test_only_the_first_few_folders_are_searched_and_nothing_without_a_setting(tmp_path):
    folders = []
    for i in range(fs_policy.HINT_FOLDERS + 1):
        f = tmp_path / f"F{i}"
        f.mkdir()
        folders.append(str(f))
    (tmp_path / f"F{fs_policy.HINT_FOLDERS}" / "Late").mkdir()
    (tmp_path / "F1" / "Early").mkdir()
    many = Config(project_root=tmp_path, home=tmp_path / "h", readable_folders=folders)
    fs_policy.use_config(many)
    try:
        assert fs_policy.same_name_inside("C:/q/Early").endswith(f"{tmp_path / 'F1' / 'Early'}.")
        assert fs_policy.same_name_inside("C:/q/Late") == ""
        plain = Config(project_root=tmp_path, home=tmp_path / "h")
        fs_policy.use_config(plain)
        assert fs_policy.same_name_inside("C:/q/Early") == ""
    finally:
        fs_policy.use_config(None)


def test_a_hint_never_fails_the_read_check(home, monkeypatch):
    _cfg, _a, tmp = home

    def boom(_p):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(fs_policy.os.path, "lexists", boom)
    ok, why = fs_policy.fs_read_ok(tmp / "Users" / "Sam" / "Downloads")
    assert ok is False and why.startswith(fs_policy.READ_REFUSAL) and "There is a" not in why


def test_the_preference_reader_says_the_prompt_is_only_a_part():
    """Seen live: asked what it remembers about how the user likes things, a
    model answered from the few preferences in its instructions and never
    read the list."""
    words = records_self.PreferenceListTool.description
    assert "what your instructions show of it is only a part" in words
    assert "call this" in words
