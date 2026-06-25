from __future__ import annotations

import tomli_w

from iron_jarvis.core.config import load_config


def test_defaults(tmp_path):
    cfg = load_config(tmp_path)
    assert cfg.default_provider == "mock"
    assert cfg.default_model == "claude-opus-4-8"
    assert cfg.permissions["write_file"] == "allow"
    assert cfg.permissions["shell"] == "ask"
    assert cfg.sandbox["host_access"] == "deny"
    assert cfg.db_path == cfg.home / "ironjarvis.db"


def test_layering_merges_without_wiping_defaults(tmp_path):
    home = tmp_path / ".ironjarvis"
    home.mkdir()
    with (home / "config.toml").open("wb") as fh:
        tomli_w.dump(
            {"default_model": "custom-model", "permissions": {"shell": "allow"}}, fh
        )

    cfg = load_config(tmp_path)
    assert cfg.default_model == "custom-model"        # scalar override
    assert cfg.permissions["shell"] == "allow"        # nested override
    assert cfg.permissions["write_file"] == "allow"   # default preserved
    assert cfg.default_provider == "mock"             # untouched default
