"""v1.358.0 ship fix (wave-8 re-review note 1): the tool-rank cache is shared
by chat turns ranking tools on worker threads. Its get -> move_to_end ->
popitem steps were unguarded, so an eviction on one thread between another
thread's read and its move raised KeyError (the turn then fell back to name
order).

A brute-force hammer cannot show this (the interpreter lock makes the window
too rare), so the interleaving is FORCED: thread A's cache read pauses right
after it finds its entry; thread B then adds an entry that evicts A's. With
the lock, B's eviction waits for A to finish its read-and-move; without it,
A's move hits a key that is gone."""
from __future__ import annotations

import threading
from collections import OrderedDict
from types import SimpleNamespace

from iron_jarvis.tools import registry as reg


class _PausingCache(OrderedDict):
    """An OrderedDict whose first armed hit pauses until the other thread has
    had its chance to evict (or 0.5 s, when a lock keeps it out)."""

    def __init__(self) -> None:
        super().__init__()
        self.armed = False
        self.got = threading.Event()
        self.evicted = threading.Event()

    def get(self, key, default=None):  # noqa: D401 - dict API
        value = super().get(key, default)
        if self.armed and value is not None:
            self.armed = False
            self.got.set()
            self.evicted.wait(0.5)
        return value


def _tool(i: int) -> tuple[str, SimpleNamespace]:
    return (
        f"mcp__github__tool_{i}",
        SimpleNamespace(remote_name=f"list_pull_requests_{i}", description=f"List open pull requests {i}"),
    )


def test_an_eviction_between_a_read_and_its_move_never_raises(monkeypatch):
    cache = _PausingCache()
    monkeypatch.setattr(reg, "_RANK_CACHE", cache)
    monkeypatch.setattr(reg, "_RANK_CACHE_MAX", 1)
    a_name, a_tool = _tool(0)
    b_name, b_tool = _tool(1)
    reg._tool_rank_words(a_name, a_tool)  # A's entry is cached
    cache.armed = True
    errors: list[BaseException] = []

    def reader() -> None:
        try:
            words = reg._tool_rank_words(a_name, a_tool)
            assert "pull" in words[0]
        except BaseException as exc:  # noqa: BLE001 - the test reports any
            errors.append(exc)

    def evictor() -> None:
        cache.got.wait(5)
        try:
            reg._tool_rank_words(b_name, b_tool)  # inserts B, evicts A (max 1)
        finally:
            cache.evicted.set()

    ta = threading.Thread(target=reader)
    tb = threading.Thread(target=evictor)
    ta.start()
    tb.start()
    ta.join(10)
    tb.join(10)
    assert errors == [], f"the reader raised: {errors[0]!r}"
    assert list(cache) == [(b_name, b_tool.description[: reg._RANK_DESC_CHARS])], list(cache)
