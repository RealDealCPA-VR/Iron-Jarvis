"""v1.426.0 (Wave 6 lane B, E5): a folder carries its own defaults for NEW
coding sessions started in it — Chat › Code's model, reasoning level and
approval level, and Build › Code's tool, model, effort and mode.

* ``GET /build/folders/defaults?folder=`` answers 200 for every folder (none
  saved: ``{}``); ``PUT`` saves or clears ONE view's defaults, the other
  view's, the folder's display name and its team kept.
* Never a folder default: Chat › Code's "Don't ask" and a tool's Full access
  (each chosen per session, on purpose) — a 400 in one sentence; an unknown
  tool, mode, level or a model with no provider likewise.
* ``BuildFolderRecord.defaults_json`` is ADDITIVE: an older database opens in
  a fresh interpreter, gains the column, its rows read as no defaults.
* The start composers read it (pinned on the dashboard side); the create
  routes are unchanged.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.harness import folder_defaults as FD
from iron_jarvis.harness import sessions as S

CODE = {"provider": "claude-cli", "model": "claude-opus-5-5", "reasoning": "high", "approval_mode": "accept_edits"}
TOOL = {"harness": "codex", "model": "gpt-6-astra", "effort": "medium", "mode": "ask"}


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(str(tmp_path / "home")))


@pytest.fixture
def work(tmp_path) -> Path:
    folder = tmp_path / "Work"
    folder.mkdir()
    return folder


def _put(client, folder, view, defaults):
    return client.put("/build/folders/defaults", json={"folder": str(folder), "view": view, "defaults": defaults})


def test_each_view_saves_reads_and_clears_on_its_own(client, work):
    assert client.get("/build/folders/defaults", params={"folder": str(work)}).json() == {
        "folder": str(work), "defaults": {}}
    r = _put(client, work, "code", CODE)
    assert r.status_code == 200, r.text
    assert r.json()["defaults"] == {"code": CODE}
    r = _put(client, work, "tool", TOOL)
    assert r.json()["defaults"] == {"code": CODE, "tool": TOOL}
    # The folder's key folds case and a trailing separator (the same folder).
    other_spelling = str(work).upper() + "\\"
    assert client.get("/build/folders/defaults", params={"folder": other_spelling}).json()["defaults"] == {
        "code": CODE, "tool": TOOL}
    # Clearing one view keeps the other.
    assert _put(client, work, "code", None).json()["defaults"] == {"tool": TOOL}
    assert _put(client, work, "tool", None).json()["defaults"] == {}
    assert client.get("/build/folders/defaults", params={"folder": ""}).json() == {"folder": "", "defaults": {}}


def test_only_what_was_chosen_is_kept(client, work):
    r = _put(client, work, "code", {"provider": "ollama", "model": "qwen3:8b", "reasoning": "", "approval_mode": ""})
    assert r.json()["defaults"] == {"code": {"provider": "ollama", "model": "qwen3:8b"}}
    r = _put(client, work, "code", {"approval_mode": "always_ask"})
    assert r.json()["defaults"]["code"] == {"approval_mode": "always_ask"}
    # Nothing chosen at all is a clear.
    assert _put(client, work, "code", {"provider": "", "reasoning": ""}).json()["defaults"] == {}


@pytest.mark.parametrize("view, defaults, words", [
    ("code", {"approval_mode": "yolo"}, "\"Don't ask\" is chosen per session"),
    ("tool", {"harness": "claude-code", "mode": "full"}, "Full access is chosen per session"),
    ("tool", {"harness": "vim"}, "not one Iron Jarvis knows"),
    ("tool", {"model": "x"}, "Say which coding tool"),
    ("tool", {"harness": "codex", "mode": "sideways"}, "That mode is not"),
    ("code", {"model": "gpt"}, "needs the provider"),
    ("code", {"provider": "openai", "reasoning": "max"}, "reasoning level"),
    ("code", {"provider": "has space"}, "That model is not"),
    ("sideways", {"x": 1}, "which coding view"),
])
def test_a_default_the_folder_cannot_keep_is_refused_in_words(client, work, view, defaults, words):
    r = _put(client, work, view, defaults)
    assert r.status_code == 400, r.text
    assert words in r.json()["detail"]
    assert client.get("/build/folders/defaults", params={"folder": str(work)}).json()["defaults"] == {}


def test_the_display_name_and_the_team_are_kept_both_ways(client, work):
    engine = client.app.state.platform.engine
    S.rename_folder(engine, str(work), "Shop")
    _put(client, work, "code", CODE)
    from iron_jarvis.harness import team as T

    T.set_folder_team(engine, str(work), None)  # a team write after the defaults
    assert FD.read(engine, str(work)) == {"code": CODE}
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import BuildFolderRecord

    with session_scope(engine) as db:
        row = db.get(BuildFolderRecord, S.folder_key(str(work)))
        assert row.name == "Shop"


def test_a_stored_value_that_no_longer_passes_reads_as_none(client, work):
    engine = client.app.state.platform.engine
    _put(client, work, "code", CODE)
    from iron_jarvis.core.db import session_scope
    from iron_jarvis.core.models import BuildFolderRecord

    with session_scope(engine) as db:
        row = db.get(BuildFolderRecord, S.folder_key(str(work)))
        row.defaults_json = json.dumps({"code": {"approval_mode": "yolo"}, "tool": TOOL})
        db.add(row)
        db.commit()
    assert FD.read(engine, str(work)) == {"tool": TOOL}
    with session_scope(engine) as db:
        row = db.get(BuildFolderRecord, S.folder_key(str(work)))
        row.defaults_json = "{not json"
        db.add(row)
        db.commit()
    assert FD.read(engine, str(work)) == {}
    assert FD.read(None, str(work)) == {} and FD.read(engine, "") == {}


_REOPEN = r"""
import json, sqlite3, sys
from iron_jarvis.platform import build_platform
from iron_jarvis.harness import folder_defaults as FD
home, folder = sys.argv[1], sys.argv[2]
p = build_platform(home)  # init_db -> the additive reconciler
print(json.dumps({"before": FD.read(p.engine, folder),
                  "after": FD.save(p.engine, folder, "code", {"approval_mode": "always_ask"})}))
"""


def test_an_older_database_opens_in_a_fresh_interpreter_and_gains_the_column(tmp_path, work):
    from iron_jarvis.platform import build_platform

    home = tmp_path / "old-home"
    first = build_platform(str(home))
    S.rename_folder(first.engine, str(work), "Old name")
    first.engine.dispose()
    db_path = next(home.rglob("ironjarvis.db"))
    con = sqlite3.connect(str(db_path))
    con.execute("ALTER TABLE buildfolderrecord DROP COLUMN defaults_json")  # … made old
    assert "defaults_json" not in {r[1] for r in con.execute("PRAGMA table_info(buildfolderrecord)")}
    con.commit()
    con.close()
    r = subprocess.run([sys.executable, "-c", _REOPEN, str(home), str(work)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr[-2000:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got == {"before": {}, "after": {"code": {"approval_mode": "always_ask"}}}
    con = sqlite3.connect(str(db_path))
    cols = {r[1] for r in con.execute("PRAGMA table_info(buildfolderrecord)")}
    name = con.execute("SELECT name FROM buildfolderrecord").fetchone()[0]
    con.close()
    assert "defaults_json" in cols and name == "Old name"
