"""v1.433.0 (Wave 1, A12): names the code reads are real settings.

The code read ``assignments_enabled`` (daemon/app.py), ``curator_enabled`` and
the curator's three numbers (skills/curator.py) and ``max_concurrent_children``
(agents/orchestrator.py) with ``getattr(config, name, default)`` — but
``core.config._load_config`` keeps only ``Config.model_fields``, and none of
them was a field, so a line in config.toml was silently dropped and nothing
(Settings, chat) could change them. They are typed Config fields now, with the
defaults the readers used, and each has a settings row chat can change.
"""

from __future__ import annotations

import asyncio

import pytest
import tomli_w
from fastapi.testclient import TestClient

from iron_jarvis.agents.orchestrator import max_concurrent_children
from iron_jarvis.core.config import Config, _load_config
from iron_jarvis.daemon.app import create_app
from iron_jarvis.settings import schema
from iron_jarvis.settings.writer import SettingError

NAMES = {
    "assignments_enabled": True,
    "curator_enabled": True,
    "curator_min_age_days": 14,
    "curator_stale_after_days": 30,
    "curator_interval_hours": 24,
    "max_concurrent_children": 4,
}


@pytest.fixture(autouse=True)
def _no_global_config(tmp_path, monkeypatch):
    """Load only the file this test writes (never the user's own)."""
    import iron_jarvis.core.config as config_mod

    monkeypatch.setattr(config_mod, "global_config_path", lambda: tmp_path / "no-global" / "config.toml")


def _write(tmp_path, values: dict) -> None:
    home = tmp_path / ".ironjarvis"
    home.mkdir(parents=True, exist_ok=True)
    (home / "config.toml").write_bytes(tomli_w.dumps(values).encode("utf-8"))


def test_the_defaults_are_the_ones_the_readers_used():
    from iron_jarvis.agents.orchestrator import _DEFAULT_MAX_CONCURRENT_CHILDREN
    from iron_jarvis.skills.curator import DEFAULT_SETTINGS

    for name, default in NAMES.items():
        assert Config.model_fields[name].default == default, name
    assert NAMES["max_concurrent_children"] == _DEFAULT_MAX_CONCURRENT_CHILDREN
    assert {k: NAMES[k] for k in DEFAULT_SETTINGS} == DEFAULT_SETTINGS


def test_config_toml_sets_them_and_the_readers_see_it(tmp_path):
    _write(tmp_path, {
        "assignments_enabled": False,
        "curator_enabled": False,
        "curator_min_age_days": 3,
        "curator_stale_after_days": 9,
        "curator_interval_hours": 6,
        "max_concurrent_children": 2,
    })
    cfg = _load_config(tmp_path)
    assert cfg.assignments_enabled is False
    assert max_concurrent_children(cfg) == 2
    from iron_jarvis.skills.curator import SkillCurator

    curator = SkillCurator(cfg.home, None, None, config=cfg)
    assert curator.settings() == {
        "curator_enabled": False,
        "curator_min_age_days": 3,
        "curator_stale_after_days": 9,
        "curator_interval_hours": 6,
    }
    assert curator.enabled() is False


def test_a_zero_hour_tidy_in_config_toml_falls_back_instead_of_sweeping_without_pause(tmp_path):
    """0 hours between sweeps would make the curator loop sweep without a
    pause; a hand-edited 0 is dropped at load like any invalid value."""
    _write(tmp_path, {"curator_interval_hours": 0, "max_concurrent_children": 3})
    cfg = _load_config(tmp_path)
    assert cfg.curator_interval_hours == 24
    assert cfg.max_concurrent_children == 3, "only the bad value is dropped"


@pytest.mark.parametrize(
    ("key", "tier", "advanced", "restart"),
    [
        ("max_concurrent_children", "ask", False, False),
        ("assignments_enabled", "allow", True, True),
        ("curator_enabled", "allow", True, False),
        ("curator_min_age_days", "allow", True, False),
        ("curator_stale_after_days", "allow", True, False),
        ("curator_interval_hours", "allow", True, False),
    ],
)
def test_each_has_a_settings_row(key, tier, advanced, restart):
    d = schema.get(key)
    assert d.store == "config" and d.field_name == key
    assert (d.tier, d.advanced, d.restart) == (tier, advanced, restart)
    assert key in schema.daemon_keys(), "PUT /settings accepts it"


@pytest.mark.parametrize(
    ("key", "bad", "good"),
    [
        ("max_concurrent_children", (0, 17), 8),
        ("curator_min_age_days", (0, 366), 7),
        ("curator_stale_after_days", (0, 3651), 60),
        ("curator_interval_hours", (0, 721), 12),
    ],
)
def test_the_ranges_are_checked_and_a_change_lands_live(tmp_path, key, bad, good):
    app = create_app(str(tmp_path))
    p = app.state.platform
    for value in bad:
        with pytest.raises(SettingError, match="must be between"):
            p.config_writer.apply({key: value}, actor="test")
    p.config_writer.apply({key: good}, actor="test")
    assert getattr(p.config, key) == good
    if key == "max_concurrent_children":
        assert max_concurrent_children(p.config) == good
    else:
        assert p.skill_curator.settings()[key] == good, "the curator reads the live config"


@pytest.mark.asyncio
async def test_chat_changes_the_helper_limit_with_a_card(tmp_path):
    from tests.test_chat_approvals_v1187 import _asgi_post, _drive_stream
    from tests.test_settings_chat_tools_v1321 import _body, _calls_stream, _done

    app = create_app(str(tmp_path))
    p = app.state.platform
    p.router.stream = _calls_stream([("config_change", {"key": "max_concurrent_children", "value": "6"})])
    seen: list[str] = []

    async def approve(data):
        seen.append(data["tool"])
        status, _ = await _asgi_post(app, f"/chat/approvals/{data['id']}", {"decision": "once"})
        assert status == 200

    frames = await _drive_stream(app, _body("let an agent run 6 helpers at once, change that setting"), approve)
    assert seen == ["config_change"]
    assert p.config.max_concurrent_children == 6
    (card,) = _done(frames)["config_cards"]
    assert (card["old"], card["new"]) == (4, 6)


def test_the_curator_loop_is_armed_while_switched_off_so_turning_it_on_needs_no_restart(tmp_path, monkeypatch):
    """The loop reads the switch on every pass (run_forever skips the sweep
    while it is off). It used to be armed only when the switch was on AT BOOT,
    so turning it on from Settings or chat did nothing until a restart."""
    from iron_jarvis.skills.curator import SkillCurator

    armed: list[bool] = []

    async def fake_run_forever(self, stop, tick_cb=None, **kw):
        armed.append(self.enabled())
        await stop.wait()

    monkeypatch.setattr(SkillCurator, "run_forever", fake_run_forever)
    app = create_app(str(tmp_path))
    app.state.platform.config.curator_enabled = False
    with TestClient(app):
        for _ in range(200):
            if armed:
                break
            asyncio.run(asyncio.sleep(0.01))
    assert armed == [False]
