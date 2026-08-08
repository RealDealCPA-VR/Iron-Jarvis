"""Additive columns must land on EXISTING databases too (v1.151.2).

THE INCIDENT. v1.150.0 added ``AgentThreadRecord.chat_thread_id``. Every test
passed. The user's daemon then answered every ``@mention`` with

    no such column: agentthreadrecord.chat_thread_id

because ``AgentThreads.__init__`` creates its table LAZILY
(``__table__.create(checkfirst=True)``) the first time a route constructs the
store. That means:

* a FRESH database gets the column, because the lazy create builds the table
  from the model as it is today — and every test mints a fresh database, so the
  whole suite stayed green through a shipped, total failure of the feature;
* an EXISTING database does not. ``checkfirst=True`` sees the table and adds
  nothing, and ``_reconcile_additive_columns`` never gets a chance to ALTER it,
  because it walks ``SQLModel.metadata.tables`` and nothing had imported
  ``agents.threads`` by the time ``init_db`` ran.

WHY THESE TESTS RUN IN A SUBPROCESS. The registration being tested is a
process-global import side effect, so a test that imports ``agents.threads`` —
or that merely runs after some other test did — populates the metadata itself
and then passes whether or not the fix exists. The first version of this file
did exactly that: removing the fix left all seven tests green. Import state
cannot be un-imported, so the only honest harness is a fresh interpreter.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import textwrap

import pytest

#: Tables created with RAW DDL on purpose, so they are legitimately unmapped:
#: the schema-version store, plus SQLite's own bookkeeping and the shadow
#: tables FTS5 materialises beside its virtual table. The virtual table itself
#: must NEVER be mapped (the reconciler would try to ALTER it every boot).
_UNMAPPED_BY_DESIGN = {"_ironjarvis_meta"}
_SQLITE_INTERNAL = ("sqlite_", "searchdoc_fts")


def _run(script: str) -> dict:
    """Execute *script* in a CLEAN interpreter and return its JSON stdout."""
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, f"subprocess failed:\n{out.stdout}\n{out.stderr}"
    last = [ln for ln in out.stdout.splitlines() if ln.startswith("{")][-1]
    return json.loads(last)


def _columns(path, table: str) -> set[str]:
    con = sqlite3.connect(str(path))
    try:
        return {r[1] for r in con.execute(f'PRAGMA table_info("{table}")')}
    finally:
        con.close()


def _write_old_schema(db) -> None:
    """The pre-v1.150.0 agentthreadrecord — the shape a real install has."""
    con = sqlite3.connect(str(db))
    con.execute(
        "CREATE TABLE agentthreadrecord ("
        " id VARCHAR PRIMARY KEY, title VARCHAR, participants_json VARCHAR,"
        " messages_json VARCHAR, created_at DATETIME, updated_at DATETIME)"
    )
    con.execute(
        "INSERT INTO agentthreadrecord VALUES ('athr_old','panel','[]','[]',"
        "'2026-01-01 00:00:00','2026-01-01 00:00:00')"
    )
    con.commit()
    con.close()


# --------------------------------------------------------------------------- #
# (1) THE REGRESSION, reproduced in a clean interpreter.
# --------------------------------------------------------------------------- #
def test_the_agent_thread_chat_binding_reaches_an_EXISTING_database(tmp_path):
    """Boot — and ONLY boot — must add the column to an old database.

    The subprocess imports nothing but ``open_db``, which is exactly what a
    daemon start does before any route has constructed ``AgentThreads``. Remove
    ``_register_late_models`` and this goes red.
    """
    db = tmp_path / "old.db"
    _write_old_schema(db)
    assert "chat_thread_id" not in _columns(db, "agentthreadrecord")

    result = _run(f"""
        import json, sqlite3
        from iron_jarvis.core.db import open_db
        open_db(r{str(db)!r})
        con = sqlite3.connect(r{str(db)!r})
        cols = [r[1] for r in con.execute('PRAGMA table_info("agentthreadrecord")')]
        rows = con.execute("SELECT id, chat_thread_id FROM agentthreadrecord").fetchall()
        con.close()
        print(json.dumps({{"cols": cols, "rows": rows}}))
    """)
    assert "chat_thread_id" in result["cols"], (
        "the additive column did not reach an existing database — this is the "
        "exact 'no such column' the user hit on every @mention"
    )
    assert result["rows"] == [["athr_old", None]], "an ALTER must not drop existing panels"


def test_the_panel_route_works_on_a_migrated_database(tmp_path):
    """End to end on the real failure: an old DB, a booted app, an @mention."""
    home = tmp_path / ".ironjarvis"
    home.mkdir(parents=True)
    _write_old_schema(home / "ironjarvis.db")

    result = _run(f"""
        import json
        from fastapi.testclient import TestClient
        from iron_jarvis.daemon.app import create_app
        c = TestClient(create_app(r{str(tmp_path)!r}))
        r = c.post("/chat/panel", json={{"message": "@builder hi", "chat_thread_id": "c1"}})
        print(json.dumps({{"status": r.status_code, "body": r.text[:300]}}))
    """)
    assert result["status"] == 200, result["body"]


# --------------------------------------------------------------------------- #
# (2) THE GENERAL GUARD — a FOURTH lazily-created table cannot slip through.
# --------------------------------------------------------------------------- #
def test_no_table_a_daemon_creates_is_invisible_to_the_reconciler(tmp_path):
    """Every table a running daemon ends up with must be in the metadata AT
    BOOT, or the next column added to it silently never reaches an install.

    Two phases in ONE clean interpreter: boot (recording the metadata as the
    reconciler saw it), then construct the lazy stores the way routes do
    (creating any table boot missed). A table present on disk but absent from
    that boot-time metadata is the bug, stated once, for any table.
    """
    result = _run(f"""
        import json, sqlite3
        from sqlmodel import SQLModel
        from iron_jarvis.core.db import open_db

        db = r{str(tmp_path / "fresh.db")!r}
        engine = open_db(db)
        at_boot = sorted(SQLModel.metadata.tables)   # what the reconciler walked

        # Now do what the routes do, lazily creating anything boot did not.
        from iron_jarvis.agents.threads import AgentThreads
        from iron_jarvis.agents.remote import RemoteAgentRegistry
        AgentThreads(engine)
        RemoteAgentRegistry(engine)
        try:
            from iron_jarvis.memory.proposals import MemoryProposalStore
            MemoryProposalStore(engine)
        except Exception:
            pass
        try:
            from iron_jarvis.workflows.store import WorkflowStore
            WorkflowStore(engine)
        except Exception:
            pass

        con = sqlite3.connect(db)
        on_disk = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        con.close()
        print(json.dumps({{"at_boot": at_boot, "on_disk": on_disk}}))
    """)
    on_disk = {
        t
        for t in result["on_disk"]
        if t not in _UNMAPPED_BY_DESIGN and not t.startswith(_SQLITE_INTERNAL)
    }
    invisible = sorted(on_disk - set(result["at_boot"]))
    assert not invisible, (
        f"{invisible} exist in the database but were absent from SQLModel.metadata "
        "at boot, so _reconcile_additive_columns cannot see them — the next column "
        "added to any of them will never reach an existing install. Add the "
        "module to core.db._LATE_MODEL_MODULES."
    )


# --------------------------------------------------------------------------- #
# (3) The registration itself, and its blast radius.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "table", ["agentthreadrecord", "memoryproposalrecord", "workflowpinrecord"]
)
def test_boot_registers_each_late_table(table):
    result = _run("""
        import json
        from sqlmodel import SQLModel
        from iron_jarvis.core.db import _register_late_models
        _register_late_models()
        print(json.dumps({"tables": sorted(SQLModel.metadata.tables)}))
    """)
    assert table in result["tables"]


def test_registration_survives_a_module_that_will_not_import(monkeypatch):
    """A broken table import must not brick boot — the daemon still starts, it
    just loses that table's reconcile until the import is fixed."""
    import importlib

    from iron_jarvis.core import db as db_mod

    def boom(name, package=None):
        raise ImportError("simulated")

    monkeypatch.setattr(importlib, "import_module", boom)
    db_mod._register_late_models()  # must not raise
