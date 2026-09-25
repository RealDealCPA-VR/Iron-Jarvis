"""v1.292.0: two knowledge items added inside one clock tick list newest
first by INSERTION, not in coin-flip order.

``list_knowledge`` ordered by ``created_at`` alone; on Windows the clock
ticks every 15.6 ms, so two quick adds share a timestamp and SQLite returned
them in either order -- the release gate's ``test_add_list_remove`` failed
two runs in three on this PC. The v1.286.0 blackboard lesson applies: rowid
only ever grows, so it breaks the tie.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import select

from iron_jarvis.core.db import session_scope
from iron_jarvis.core.models import ProjectKnowledge
from iron_jarvis.platform import build_platform
from iron_jarvis.projects.knowledge import list_knowledge


def test_same_tick_items_list_newest_first_by_insertion(tmp_path):
    p = build_platform(str(tmp_path))
    tick = datetime(2026, 9, 25, 12, 0, 0, tzinfo=timezone.utc)
    with session_scope(p.engine) as db:
        for name in ("first", "second", "third"):
            db.add(ProjectKnowledge(project_id="proj", name=name, text=name, size=5, created_at=tick))
            db.commit()  # one row per commit: distinct rowids, one timestamp
        assert len({r.created_at for r in db.exec(select(ProjectKnowledge))}) == 1
    names = [i["name"] for i in list_knowledge(p, "proj")]
    assert names == ["third", "second", "first"], names
