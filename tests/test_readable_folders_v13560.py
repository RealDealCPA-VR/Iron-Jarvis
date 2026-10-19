"""v1.356.0, wave 6 lane 6C (G81): the folders Jarvis may read are a setting.

Narrowing what chat and agents may read used to need the
``IRONJARVIS_FS_ALLOWLIST`` environment variable, and its refusal NAMED that
variable ("path is outside IRONJARVIS_FS_ALLOWLIST"). Now:

* ``readable_folders`` is ONE schema row (Settings › Permissions & ledger,
  "Folders Jarvis may read"), a ``Config`` field persisted ONLY through the one
  settings writer, every folder checked at SAVE time through the file policy's
  own door (``fs_policy.read_root_problem`` = ``root_problem`` for reading);
* ``core/fs_policy`` reads it LIVE (the config ``load_config`` made) beside
  the variable — empty = no limit (exactly the old behaviour); listed = reads
  only there plus Jarvis's own working folders; both set = both must allow;
* a refusal is one sentence pointing back to the setting, never the variable;
* protected folders stay protected inside a listed folder.

Everything is driven through the REAL app factory (real middleware, the real
``PUT /settings/values`` → ``ConfigWriter``), the real gate the file tools call
(``fs_read_ok``) and a real file tool (``read_document``).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from iron_jarvis.core import fs_policy
from iron_jarvis.core.config import load_config
from iron_jarvis.daemon.app import create_app
from iron_jarvis.documents.tools import ReadDocumentTool
from iron_jarvis.settings import schema
from iron_jarvis.tools.base import ToolContext

REFUSAL = "That folder isn't one Jarvis may read. Change the folders in Settings › Permissions."
ENV = "IRONJARVIS_FS_ALLOWLIST"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """No installation limit unless a test sets one, and the process-wide
    binding is cleared after each test so a listed folder never limits a
    later test that builds no platform."""
    monkeypatch.delenv(ENV, raising=False)
    yield
    fs_policy.use_config(None)


def _app(tmp_path: Path) -> tuple[TestClient, object]:
    app = create_app(str(tmp_path / "root"))
    return TestClient(app), app.state.d


def _folders(tmp_path: Path) -> tuple[Path, Path, Path]:
    a = tmp_path / "Clients"
    b = tmp_path / "Tax 2025"
    c = tmp_path / "Private"
    for p in (a, b, c):
        p.mkdir()
        (p / "notes.txt").write_text(f"inside {p.name}", encoding="utf-8")
    return a, b, c


def _ctx(ws: Path) -> ToolContext:
    ws.mkdir(parents=True, exist_ok=True)
    return ToolContext(workspace=ws, session_id="t", agent_run_id="t", config=None, event_bus=None, engine=None)


# --------------------------------------------------------------------------- #
# the row
# --------------------------------------------------------------------------- #


def test_the_setting_is_one_schema_row_in_permissions_in_plain_words(tmp_path):
    d = schema.get("readable_folders")
    assert d.group == "permissions" and d.type == "list" and d.store == "config"
    # A model never changes what it may read without the person, every time.
    assert d.tier == "ask-floor"
    assert d.label == "Folders Jarvis may read"
    words = (d.label + " " + d.help).lower()
    for engine_word in ("ironjarvis_fs_allowlist", "allowlist", "env", "variable", "fs_policy"):
        assert engine_word not in words, engine_word
    # The Settings page draws it from the served schema, and PUT accepts it.
    client, _ = _app(tmp_path)
    served = {s["key"]: s for s in client.get("/settings/schema").json()["settings"]}
    assert served["readable_folders"]["group"] == "permissions"
    assert "readable_folders" in schema.daemon_keys()


def test_the_extension_auto_approve_help_names_no_protocol_word():
    """G74's one schema line (lane 6C owns the schema): plain words."""
    help_text = schema.get("mcp_auto_approve").help
    assert "MCP" not in help_text and "stdio" not in help_text and " env" not in help_text
    assert "without asking" in help_text


# --------------------------------------------------------------------------- #
# the acceptance: limit to two folders, a read outside is refused
# --------------------------------------------------------------------------- #


async def test_a_user_limits_reading_to_two_folders_and_a_read_outside_is_refused(tmp_path):
    a, b, c = _folders(tmp_path)
    client, d = _app(tmp_path)
    # Before: no limit — exactly today's behaviour.
    assert fs_policy.fs_read_ok(c / "notes.txt") == (True, "")

    r = client.put("/settings/values", json={"values": {"readable_folders": [str(a), str(b)]}})
    assert r.status_code == 200, r.text
    assert "readable_folders" in r.json()["updated"]
    assert r.json()["action_id"], "the change is on the ledger with an Undo"

    # The gate the file tools call: inside both folders reads, outside refuses
    # with the sentence that points back to the setting.
    assert fs_policy.fs_read_ok(a / "notes.txt") == (True, "")
    assert fs_policy.fs_read_ok(b / "notes.txt") == (True, "")
    ok, why = fs_policy.fs_read_ok(c / "notes.txt")
    assert ok is False and why == REFUSAL
    assert ENV not in why

    # A real file tool says the same thing to the model and the user.
    ws = tmp_path / "ws"
    res = await ReadDocumentTool().execute({"path": str(c / "notes.txt")}, _ctx(ws))
    assert res.ok is False
    assert "isn't one Jarvis may read" in (res.error or "") and "Settings › Permissions" in (res.error or "")
    assert ENV not in (res.error or "")
    assert "inside Private" not in (res.output or "")
    good = await ReadDocumentTool().execute({"path": str(a / "notes.txt")}, _ctx(ws))
    assert good.ok and "inside Clients" in good.output

    # Jarvis's own working folders stay readable (an attached upload), and the
    # protected folders inside its home stay refused.
    home = Path(d.platform.config.home)
    up = home / "uploads"
    up.mkdir(parents=True, exist_ok=True)
    (up / "attached.txt").write_text("an upload", encoding="utf-8")
    assert fs_policy.fs_read_ok(up / "attached.txt") == (True, "")
    assert fs_policy.fs_read_ok(home / "secrets" / "x.key")[0] is False

    # Persisted through the one writer: config.toml holds it, and a fresh
    # load of the same root limits reads the same way.
    toml = (home / "config.toml").read_text(encoding="utf-8")
    assert "readable_folders" in toml
    again = load_config(str(tmp_path / "root"))
    assert [Path(x) for x in again.readable_folders] == [a, b]
    assert fs_policy.fs_read_ok(c / "notes.txt") == (False, REFUSAL)


def test_settings_access_names_the_chosen_folders(tmp_path):
    a, b, _c = _folders(tmp_path)
    client, _ = _app(tmp_path)
    before = client.get("/settings/access").json()["reads"]
    assert before["limited"] is False and before["chosen"] == [] and before["install"] is False
    client.put("/settings/values", json={"values": {"readable_folders": [str(a), str(b)]}})
    reads = client.get("/settings/access").json()["reads"]
    assert reads["limited"] is True
    assert reads["install"] is False
    assert reads["chosen"] == [str(a), str(b)]
    # `folders` is what the gate enforces (the REPL child is handed the same).
    assert reads["folders"] == [str(p) for p in fs_policy.allowlist_roots()]
    assert str(a.resolve()) in reads["folders"]


def test_clearing_the_setting_puts_back_the_old_behaviour_and_undo_restores_the_limit(tmp_path):
    a, b, c = _folders(tmp_path)
    client, _ = _app(tmp_path)
    client.put("/settings/values", json={"values": {"readable_folders": [str(a), str(b)]}})
    cleared = client.put("/settings/values", json={"values": {"readable_folders": []}})
    assert cleared.status_code == 200, cleared.text
    assert fs_policy.fs_read_ok(c / "notes.txt") == (True, "")
    # Undo the clear: the limit comes back — even when one folder has gone
    # missing since (putting a reading limit back is the safe direction).
    (b / "notes.txt").unlink()
    b.rmdir()
    assert client.post(f"/undo/{cleared.json()['action_id']}").status_code == 200
    assert fs_policy.fs_read_ok(c / "notes.txt") == (False, REFUSAL)
    assert fs_policy.fs_read_ok(a / "notes.txt") == (True, "")


# --------------------------------------------------------------------------- #
# validated at save time, through the file policy's own door
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("bad", ["relative/folder", "missing"])
def test_a_folder_that_is_not_a_real_absolute_folder_is_refused_in_a_sentence(tmp_path, bad):
    client, d = _app(tmp_path)
    value = bad if bad.startswith("relative") else str(tmp_path / "no-such-folder")
    r = client.put("/settings/values", json={"values": {"readable_folders": [value]}})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert detail.startswith("Folders Jarvis may read:")
    assert ENV not in detail
    # Nothing was saved: still no limit.
    assert d.platform.config.readable_folders == []
    assert fs_policy.fs_read_ok(tmp_path / "anything.txt") == (True, "")


def test_a_protected_folder_can_never_be_listed_and_stays_protected_inside_a_listed_one(tmp_path):
    client, d = _app(tmp_path)
    home = Path(d.platform.config.home)
    secrets = home / "secrets"
    secrets.mkdir(parents=True, exist_ok=True)
    r = client.put("/settings/values", json={"values": {"readable_folders": [str(secrets)]}})
    assert r.status_code == 400
    assert "keys or records" in r.json()["detail"]
    # Listing the folder that HOLDS it is allowed; the secrets inside stay refused.
    ok = client.put("/settings/values", json={"values": {"readable_folders": [str(home)]}})
    assert ok.status_code == 200, ok.text
    assert fs_policy.fs_read_ok(secrets / ".secrets.key")[0] is False


def test_with_an_installation_limit_a_folder_outside_it_cannot_be_listed(tmp_path, monkeypatch):
    a, _b, c = _folders(tmp_path)
    monkeypatch.setenv(ENV, str(a))
    client, _ = _app(tmp_path)
    r = client.put("/settings/values", json={"values": {"readable_folders": [str(c)]}})
    assert r.status_code == 400
    assert "this installation does not allow reading that folder" in r.json()["detail"]
    assert ENV not in r.json()["detail"]


# --------------------------------------------------------------------------- #
# the installation's limit and the setting together
# --------------------------------------------------------------------------- #


def test_both_limits_set_a_read_must_satisfy_both_and_the_setting_never_widens(tmp_path, monkeypatch):
    a, b, c = _folders(tmp_path)
    sub = a / "Acme"
    sub.mkdir()
    (sub / "k1.txt").write_text("k1", encoding="utf-8")
    client, _ = _app(tmp_path)
    # The user narrows to one client folder…
    assert client.put("/settings/values", json={"values": {"readable_folders": [str(sub)]}}).status_code == 200
    # …and later the installation limits reading to `a` (set AFTER the setting).
    monkeypatch.setenv(ENV, f"{a},{c}")
    assert fs_policy.fs_read_ok(sub / "k1.txt") == (True, "")
    # Inside the installation's limit but outside the user's: the setting refused.
    assert fs_policy.fs_read_ok(a / "notes.txt") == (False, REFUSAL)
    # Outside the installation's limit: refused WITHOUT sending the user to a
    # setting that cannot change it, and never naming the variable.
    ok, why = fs_policy.fs_read_ok(b / "notes.txt")
    assert ok is False and why == fs_policy.READ_REFUSAL_INSTALL
    assert "Settings" not in why and ENV not in why
    # A saved setting outside the installation's limit never reopens it.
    monkeypatch.setenv(ENV, str(c))
    assert fs_policy.fs_read_ok(sub / "k1.txt")[0] is False
    # …and the REPL child, which enforces the same list, reads nothing then.
    roots = fs_policy.allowlist_roots()
    assert roots and all(not str(sub.resolve()).startswith(str(r)) for r in roots)


def test_only_the_installation_limit_keeps_its_old_meaning_without_the_variable_name(tmp_path, monkeypatch):
    a, _b, c = _folders(tmp_path)
    monkeypatch.setenv(ENV, str(a))
    assert fs_policy.fs_read_ok(a / "notes.txt") == (True, "")
    assert fs_policy.fs_read_ok(c / "notes.txt") == (False, fs_policy.READ_REFUSAL_INSTALL)
    assert fs_policy.allowlist_roots() == [a.resolve()]


def test_a_dead_config_limits_nothing(tmp_path):
    """The binding is weak: a config nobody holds any more stops limiting."""
    import gc

    from iron_jarvis.core.config import Config

    a, _b, c = _folders(tmp_path)
    cfg = Config(project_root=tmp_path, home=tmp_path / "h", readable_folders=[str(a)])
    fs_policy.use_config(cfg)
    assert fs_policy.fs_read_ok(c / "notes.txt")[0] is False
    del cfg
    gc.collect()
    assert fs_policy.fs_read_ok(c / "notes.txt") == (True, "")
