"""v1.358.0: the real-config guards compare an app-rewritten file by the part a
pack import could touch, and STILL catch a real change there.

The v1.358.0 full suite raised three guard errors on the user's PC: a running
Claude Desktop rewrote the ``preferences`` part of claude_desktop_config.json
while the suite ran (its servers unchanged, no test fingerprint in it). The
guard now compares that file's ``mcpServers`` only, like ``.claude.json``'s."""
from __future__ import annotations

import json
from pathlib import Path

from tests.fixtures.real_config_view import real_view


def _write(p: Path, doc: dict) -> None:
    p.write_text(json.dumps(doc), encoding="utf-8")


def test_a_preferences_rewrite_is_not_a_change_but_a_servers_change_is(tmp_path):
    p = tmp_path / "claude_desktop_config.json"
    base = {"mcpServers": {"hermes": {"command": "cmd", "args": ["/c", "x"]}}, "preferences": {"a": 1}}
    _write(p, base)
    before = real_view(p)

    _write(p, {**base, "preferences": {"a": 2, "b": True}, "coworkUserFilesPath": "x"})
    assert real_view(p) == before, "the app rewriting its own preferences is not a test touching the file"

    _write(p, {**base, "mcpServers": {**base["mcpServers"], "slack": {"command": "npx"}}})
    assert real_view(p) != before, "a server added by a test must still be caught"


def test_every_other_guarded_file_is_compared_byte_for_byte(tmp_path):
    p = tmp_path / "settings.json"
    _write(p, {"mcpServers": {}, "preferences": {"a": 1}})
    before = real_view(p)
    _write(p, {"mcpServers": {}, "preferences": {"a": 2}})
    assert real_view(p) != before


def test_dot_claude_json_keeps_its_project_servers_view(tmp_path):
    p = tmp_path / ".claude.json"
    base = {"mcpServers": {}, "projects": {"C:/a": {"mcpServers": {}, "history": [1]}}, "numStartups": 3}
    _write(p, base)
    before = real_view(p)
    _write(p, {**base, "numStartups": 4, "projects": {"C:/a": {"mcpServers": {}, "history": [1, 2]}}})
    assert real_view(p) == before
    _write(p, {**base, "projects": {"C:/a": {"mcpServers": {"x": {"command": "npx"}}}}})
    assert real_view(p) != before
