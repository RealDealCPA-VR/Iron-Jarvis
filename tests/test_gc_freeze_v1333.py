"""The boot heap is frozen once, and a long full collection is named (v1.333.0, D7).

A scratch boot left ~229k objects that every full (generation 2) collection
walked again: ~103 ms with the daemon's one event loop stopped. The lifespan
now collects once after the boot line and ``gc.freeze()``s what is left, and a
``gc.callbacks`` hook logs one WARNING for any full collection over 100 ms.
"""

from __future__ import annotations

import gc
import logging

from fastapi.testclient import TestClient

from iron_jarvis.daemon import app as app_mod


def test_the_lifespan_freezes_the_boot_heap_and_installs_one_pause_watch(tmp_path, monkeypatch):
    gc.unfreeze()
    monkeypatch.setattr(app_mod, "_GC_FROZEN", False)
    assert gc.get_freeze_count() == 0
    with TestClient(app_mod.create_app(str(tmp_path))):
        # The whole boot heap (a few hundred objects reach the permanent
        # generation without any freeze, so this is far above that noise).
        assert gc.get_freeze_count() > 20_000
        assert sum(isinstance(cb, app_mod._GcPauseWatch) for cb in gc.callbacks) == 1
    # Shutdown takes the watch out again (review, v1.333.0): left in place, a
    # long collection after the log's stream closed printed "--- Logging
    # error --- ... I/O operation on closed file" from the callback.
    assert not any(isinstance(cb, app_mod._GcPauseWatch) for cb in gc.callbacks)
    # A second app in the same process gets its own watch, and hooks never stack.
    monkeypatch.setattr(app_mod, "_GC_FROZEN", False)
    with TestClient(app_mod.create_app(str(tmp_path / "again"))):
        assert sum(isinstance(cb, app_mod._GcPauseWatch) for cb in gc.callbacks) == 1
    assert not any(isinstance(cb, app_mod._GcPauseWatch) for cb in gc.callbacks)


def test_the_pause_watch_is_silent_while_the_interpreter_shuts_down(caplog, monkeypatch):
    caplog.set_level(logging.WARNING)
    clock = _Clock()
    watch = app_mod._GcPauseWatch(threshold_s=0.1, clock=clock)
    monkeypatch.setattr(app_mod.sys, "is_finalizing", lambda: True)
    watch("start", {"generation": 2})
    clock.now += 5.0
    watch("stop", {"generation": 2, "collected": 1, "uncollectable": 0})
    assert [r for r in caplog.records if "garbage collection" in r.getMessage()] == []


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_a_long_full_collection_is_one_warning_and_short_or_young_ones_are_silent(caplog):
    caplog.set_level(logging.WARNING)
    clock = _Clock()
    watch = app_mod._GcPauseWatch(threshold_s=0.1, clock=clock)

    def collection(generation: int, seconds: float, collected: int = 7) -> None:
        watch("start", {"generation": generation})
        clock.now += seconds
        watch("stop", {"generation": generation, "collected": collected, "uncollectable": 0})

    collection(2, 0.05)  # a quick full pass
    collection(0, 0.5)  # a young generation, however slow
    collection(1, 0.5)
    assert [r for r in caplog.records if "garbage collection" in r.getMessage()] == []

    collection(2, 0.25, collected=1234)
    lines = [r for r in caplog.records if "garbage collection" in r.getMessage()]
    assert len(lines) == 1
    assert lines[0].levelno == logging.WARNING
    assert lines[0].getMessage() == (
        "garbage collection paused the daemon for 250 ms (full collection, 1234 objects freed)"
    )
