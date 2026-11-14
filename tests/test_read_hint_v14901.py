"""A read the user's folder setting refused names the folders Jarvis MAY read
(v1.490.1).

Seen live on both models with reading limited to a home folder: asked to
tidy "the Downloads folder", the model tried the usual place for it, was told
only "That folder isn't one Jarvis may read", and told the user to change
their permissions. The Downloads folder it wanted was readable all along,
inside the home folder the setting names.
"""
from __future__ import annotations

import pytest

from iron_jarvis.core import fs_policy
from iron_jarvis.core.config import Config


@pytest.fixture()
def limited(tmp_path):
    a = tmp_path / "Home"
    a.mkdir()
    (tmp_path / "Other").mkdir()
    cfg = Config(project_root=tmp_path, home=tmp_path / "h", readable_folders=[str(a)])
    fs_policy.use_config(cfg)
    yield cfg, a, tmp_path / "Other"
    fs_policy.use_config(None)


def test_a_refused_read_names_what_may_be_read(limited):
    _cfg, a, other = limited
    ok, why = fs_policy.fs_read_ok(other / "notes.txt")
    assert ok is False
    assert why.startswith(fs_policy.READ_REFUSAL)
    assert f"Jarvis may read: {a}." in why
    assert "use its full path there" in why
    # Inside a listed folder nothing is refused and nothing is added.
    assert fs_policy.fs_read_ok(a / "notes.txt") == (True, "")


def test_no_setting_means_no_hint_and_no_refusal(tmp_path):
    plain = Config(project_root=tmp_path, home=tmp_path / "h")
    fs_policy.use_config(plain)
    try:
        assert fs_policy.readable_hint() == ""
        assert fs_policy.fs_read_ok(tmp_path / "x.txt") == (True, "")
    finally:
        fs_policy.use_config(None)


def test_the_hint_is_bounded_and_counts_the_rest(tmp_path):
    folders = [str(tmp_path / f"F{i}") for i in range(9)]
    # The policy holds its Config WEAKLY: keep each one alive here.
    many = Config(project_root=tmp_path, home=tmp_path / "h", readable_folders=folders)
    fs_policy.use_config(many)
    try:
        hint = fs_policy.readable_hint()
        assert hint.count(str(tmp_path)) == fs_policy.HINT_FOLDERS
        assert "and 3 more" in hint
        long_one = Config(project_root=tmp_path, home=tmp_path / "h", readable_folders=[str(tmp_path / ("x" * 400))])
        fs_policy.use_config(long_one)
        assert 0 < len(fs_policy.readable_hint()) < 320
    finally:
        fs_policy.use_config(None)


def test_the_installations_own_limit_names_no_folder(limited, monkeypatch, tmp_path):
    """When the INSTALLATION's limit refuses, Settings cannot change it and
    its folders are not the user's list: the short sentence stays as it was."""
    _cfg, a, other = limited
    monkeypatch.setenv("IRONJARVIS_FS_ALLOWLIST", str(tmp_path / "Elsewhere"))
    ok, why = fs_policy.fs_read_ok(a / "notes.txt")
    assert (ok, why) == (False, fs_policy.READ_REFUSAL_INSTALL)


def test_a_protected_folder_keeps_its_own_words(limited, tmp_path):
    secret = tmp_path / "h" / "secrets"
    secret.mkdir(parents=True)
    fs_policy.register_protected_root(secret)
    ok, why = fs_policy.fs_read_ok(secret / "key")
    assert ok is False and "Jarvis may read" not in why
