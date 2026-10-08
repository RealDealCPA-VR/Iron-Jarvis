"""Calm UI redesign S1: ONE settings schema.

Every setting is declared once in ``iron_jarvis.settings.schema``; the daemon
whitelist, the Settings page and (S3) the chat config tools read it. These
pins keep it whole: nothing the daemon or the page knows is missing from it,
every config key is a real Config field, every enum option is a value Config
accepts, and ``GET /settings/schema`` serves it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core.config import Config
from iron_jarvis.daemon.app import create_app
from iron_jarvis.daemon.schemas import _SETTINGS_KEYS
from iron_jarvis.settings import schema

ROOT = Path(__file__).resolve().parents[1]


def test_every_whitelisted_key_is_in_the_schema():
    missing = sorted(set(_SETTINGS_KEYS) - set(schema.daemon_keys()))
    assert missing == [], missing


def test_every_settings_page_field_is_in_the_schema():
    src = (ROOT / "dashboard" / "app" / "settings" / "page.tsx").read_text(encoding="utf-8")
    keys = set(re.findall(r'^\s+key: "([a-z_]+)",', src, re.M))
    assert len(keys) > 25, "anti-vacuity: the page's FIELDS were found"
    assert sorted(keys - set(schema.BY_KEY)) == []


def test_config_store_keys_are_real_config_fields():
    fields = set(Config.model_fields)
    assert [k for k in schema.daemon_keys() if k not in fields] == []


@pytest.mark.parametrize("d", [d for d in schema.SETTINGS if d.store == "config" and d.type == "enum"], ids=lambda d: d.key)
def test_every_enum_option_is_a_value_config_accepts(d, tmp_path):
    cfg = Config(home=tmp_path, project_root=tmp_path)
    for value, _label in d.options:
        trial = cfg.model_copy(deep=True)
        setattr(trial, d.field_name, value)  # raises if Config refuses it
        assert getattr(trial, d.field_name) == value


def test_tiers_floor_values_and_groups_are_sane():
    for d in schema.SETTINGS:
        assert d.label and d.help, d.key
        for v in d.floor_when:
            assert d.tier_for(v) == "ask-floor"
    # The safety switches raise to ask-floor when turned OFF / widened.
    assert schema.get("autonomy_kill_switch").tier_for(False) == "ask-floor"
    assert schema.get("autonomy_kill_switch").tier_for(True) == "allow"
    assert schema.get("comm_trust").tier_for("full") == "ask-floor"
    assert schema.get("device.approval_mode").tier_for("yolo") == "ask-floor"


def test_get_settings_schema_serves_groups_settings_and_secrets(tmp_path):
    body = TestClient(create_app(str(tmp_path))).get("/settings/schema").json()
    assert [g["id"] for g in body["groups"]] == list(schema.GROUP_IDS)
    keys = {s["key"] for s in body["settings"]}
    assert {"default_model", "comm_trust", "device.theme", "profile.about"} <= keys
    assert all(s["secret"] is False for s in body["settings"])
    assert body["secrets"] and all(s["secret"] is True and s["tier"] == "ask-floor" for s in body["secrets"])
    enum = next(s for s in body["settings"] if s["key"] == "local_primary_policy")
    assert enum["options"][0] == {"value": "refuse", "label": "Stop and tell me (default)"}
