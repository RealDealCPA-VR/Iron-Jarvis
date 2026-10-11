"""The hourly history-search re-check reads KEYS, not whole rows (v1.333.0, D11).

The backfill loop re-walks every chat thread, round table and session from
the start once an hour and skips the ones already indexed. Each page used to
load full rows (``messages_json`` and all) only to throw the indexed ones away.
A page is now listed as ``(id, created_at)`` and full rows are read only for
ids that still need indexing, so a re-check over an indexed history reads none.
"""

from __future__ import annotations

from iron_jarvis.core.models import ChatThreadRecord
from iron_jarvis.search.index import SearchIndex
from tests.test_search_index import _run_backfill, _seed_history, engine, index  # noqa: F401


def test_a_recheck_over_an_indexed_history_loads_no_full_rows(engine, index, monkeypatch):  # noqa: F811
    _seed_history(engine, threads=5, sessions=3)
    loaded: list[str] = []
    real = SearchIndex._load_rows

    def counted(self, model, ids):
        loaded.extend(ids)
        return real(self, model, ids)

    monkeypatch.setattr(SearchIndex, "_load_rows", counted)
    assert _run_backfill(index, batch=2) == 5 * 2 + 3
    assert len(loaded) == 5 + 3  # the first sweep reads each row once
    loaded.clear()
    assert _run_backfill(index, batch=2) == 0
    assert loaded == []  # the hourly re-check: keys only


def test_a_page_is_listed_without_its_message_bodies(engine, index):  # noqa: F811
    _seed_history(engine, threads=3, sessions=0)
    keys = index._keyset(ChatThreadRecord, None, "", 10)
    assert [k.id for k in keys] == ["chat_0", "chat_1", "chat_2"]
    assert not hasattr(keys[0], "messages_json")
