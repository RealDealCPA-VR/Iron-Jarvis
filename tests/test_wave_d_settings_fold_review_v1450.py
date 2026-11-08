"""AN review of Wave D item 32: the schema states each config row's own
default, so the Settings page can say that a folded expert row was CHANGED
(a closed "More options" must never hide that)."""

from __future__ import annotations

import json

from iron_jarvis.core.config import Config
from iron_jarvis.settings import schema


def _rows() -> dict[str, dict]:
    return {r["key"]: r for r in schema.public_schema()["settings"]}


def test_a_config_row_carries_its_fresh_install_default() -> None:
    rows = _rows()
    assert rows["strict_model_pin"]["default"] is False
    assert rows["model_roles"]["default"] == {}
    assert rows["search_roots"]["default"] == []
    # It is the Config field's own default, whatever the row.
    for key, row in rows.items():
        if "default" not in row:
            continue
        d = {x.key: x for x in schema.SETTINGS}[key]
        field = Config.model_fields[d.field_name.partition(".")[0]]
        want = field.get_default(call_default_factory=True)
        if "." in d.field_name:
            want = want[d.field_name.partition(".")[2]]
        assert row["default"] == want, key
    json.dumps(schema.public_schema())


def test_a_device_or_profile_or_pattern_row_states_none() -> None:
    by_key = {d.key: d for d in schema.SETTINGS}
    for row in schema.public_schema()["settings"]:
        d = by_key[row["key"]]
        if d.store != "config" or d.pattern:
            assert "default" not in row, row["key"]
