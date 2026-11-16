"""v1.505.1: a platform that is dropped is FREED.

v1.505.0 added two hooks so a removed schedule or saved workflow drops the
standing OKs kept on it. The workflow hook lives in a module table keyed
weakly by the database engine, and its callback referred to the grant store,
which holds that engine: so no engine, and no platform, was ever freed again.
The installed app builds one platform and never noticed. The test suite
builds thousands: every worker's memory climbed, a shard that takes 12
minutes took 29, tests timed out, and the v1.505.0 release run was cancelled
at its time limit before anything was published.

This pins the property itself, whatever holds the reference next time: build
several platforms, drop them, collect, and count the engines still alive.
"""

from __future__ import annotations

import gc
import weakref

from iron_jarvis.platform import build_platform

#: How many platforms are built. Before v1.505.0 one of them stayed alive
#: (the newest, held by a module that remembers the last platform built).
BUILT = 6
ALLOWED_ALIVE = 1


def test_a_dropped_platform_frees_its_database(tmp_path):
    engines = []
    for i in range(BUILT):
        platform = build_platform(str(tmp_path / f"home{i}"))
        engines.append(weakref.ref(platform.engine))
        del platform
    for _ in range(3):
        gc.collect()
    alive = sum(1 for ref in engines if ref() is not None)
    assert alive <= ALLOWED_ALIVE, (
        f"{alive} of {BUILT} dropped platforms kept their database alive: something holds every "
        "platform ever built (a module-level table, a hook, a cache keyed by the engine)"
    )


def test_the_removal_hooks_still_drop_a_removed_schedules_and_workflows_oks(tmp_path):
    """The fix made the hooks hold the grant store weakly; they must still work."""
    from iron_jarvis.core.grants import args_hash
    from iron_jarvis.workflows.store import WorkflowStore

    platform = build_platform(str(tmp_path / "home"))
    grants = platform.grants
    git = {"command": "git status"}
    grants.create("schedule", "rent-list", "shell", args_hash("shell", git), "shell git status")
    grants.create("workflow", "Monthly close", "shell", args_hash("shell", git), "shell git status")

    platform.scheduler.on_removed("rent-list")
    assert grants.list("schedule", "rent-list") == []

    store = WorkflowStore(platform.engine)
    store.save("Monthly close", [{"name": "Draft", "task": "x"}])
    assert store.remove("Monthly close") is True
    assert grants.list("workflow", "Monthly close") == []
