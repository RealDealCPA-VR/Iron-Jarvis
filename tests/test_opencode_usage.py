"""v1.94.0: the Usage page counts OpenCode's local-model sessions.

OpenCode runs its own agent loop against the user's fleet and records tokens
only in its own SQLite store — millions of local tokens the Usage page never
saw. The merge reads that store LIVE (read-only; session aggregates grow, so
imported copies would double-count) and folds totals/by_model/by_day in.
Schema + values modeled on a real store (session.model is JSON like
``{"id":"fleet","providerID":"spark"}``; times are ms epoch).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from iron_jarvis.daemon.app import create_app
from iron_jarvis.eval.opencode_usage import opencode_db_path, opencode_usage

NOW_MS = 1_785_000_000_000  # a fixed "now" for deterministic windows
DAY_MS = 86_400_000


def _store(path: Path, rows: list[tuple]) -> Path:
    db = sqlite3.connect(str(path))
    db.execute(
        "CREATE TABLE session (id TEXT PRIMARY KEY, model TEXT,"
        " tokens_input INTEGER, tokens_output INTEGER, tokens_reasoning INTEGER,"
        " tokens_cache_read INTEGER, tokens_cache_write INTEGER,"
        " cost REAL, time_updated INTEGER)"
    )
    db.executemany("INSERT INTO session VALUES (?,?,?,?,?,?,?,?,?)", rows)
    db.commit()
    db.close()
    return path


def _fleet(sid: str, tin: int, tout: int, t_ms: int, reasoning: int = 0) -> tuple:
    model = json.dumps({"id": "fleet", "providerID": "spark"})
    return (sid, model, tin, tout, reasoning, 0, 0, 0.0, t_ms)


def test_reads_real_store_shapes_and_windows(tmp_path):
    db = _store(tmp_path / "opencode.db", [
        _fleet("s1", 23_772_586, 101_847, NOW_MS - DAY_MS),        # the big one
        _fleet("s2", 377_419, 3_289, NOW_MS - 2 * DAY_MS, reasoning=1_000),
        _fleet("old", 9_999_999, 9_999, NOW_MS - 45 * DAY_MS),     # outside 30d
    ])
    out = opencode_usage(db, 30, now_ms=NOW_MS)
    assert out["available"] is True
    assert out["totals"]["runs"] == 2  # the 45-day-old session is windowed out
    assert out["totals"]["input_tokens"] == 23_772_586 + 377_419
    # Reasoning tokens are GENERATED tokens — they count as output.
    assert out["totals"]["output_tokens"] == 101_847 + 3_289 + 1_000
    row = out["by_model"][0]
    assert row["provider"] == "opencode/spark" and row["model"] == "fleet"
    assert row["runs"] == 2
    assert len(out["by_day"]) == 2  # two distinct last-activity days


def test_absent_or_broken_store_degrades_honestly(tmp_path):
    out = opencode_usage(tmp_path / "nope" / "opencode.db", 30, now_ms=NOW_MS)
    assert out["available"] is False and "not found" in out["note"]
    bad = tmp_path / "bad.db"
    bad.write_text("not a database", encoding="utf-8")
    out = opencode_usage(bad, 30, now_ms=NOW_MS)
    assert out["available"] is False
    assert out["totals"]["input_tokens"] == 0


def test_db_path_override_accepts_dir_or_file(tmp_path):
    class _Cfg:
        opencode_data_dir = str(tmp_path)
    assert opencode_db_path(_Cfg) == tmp_path / "opencode.db"
    _Cfg.opencode_data_dir = str(tmp_path / "x.db")
    assert opencode_db_path(_Cfg) == tmp_path / "x.db"
    _Cfg.opencode_data_dir = ""
    assert opencode_db_path(_Cfg).name == "opencode.db"


def test_usage_endpoint_merges_opencode_totals(tmp_path):
    _store(tmp_path / "opencode.db", [
        _fleet("s1", 1_000_000, 50_000, NOW_MS - DAY_MS),
    ])
    client = TestClient(create_app(str(tmp_path)))
    client.app.state.platform.config.opencode_data_dir = str(tmp_path)
    out = client.get("/usage").json()
    assert out["opencode"]["available"] is True
    assert out["totals"]["input_tokens"] >= 1_000_000  # local work COUNTS now
    assert any(
        r["provider"] == "opencode/spark" and r["model"] == "fleet"
        for r in out["by_model"]
    )
    assert any(r["input_tokens"] >= 1_000_000 for r in out["by_day"])


def test_usage_endpoint_survives_missing_store(tmp_path):
    client = TestClient(create_app(str(tmp_path)))
    client.app.state.platform.config.opencode_data_dir = str(tmp_path / "ghost")
    out = client.get("/usage").json()
    assert out["opencode"]["available"] is False
    assert "totals" in out and "by_model" in out  # the page still renders
