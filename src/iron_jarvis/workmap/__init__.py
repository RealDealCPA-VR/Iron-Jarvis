"""The work record (v1.491.0): what Iron Jarvis did, where, and what it left.

Vision plan leap 3, Phase 1 (``docs/VISION-PLAN-2026-11-14.md``). A derived,
local index of every piece of work that has a folder (chats, coding sessions
in both Code views, agent runs), built only from what Iron Jarvis already
stores. Every chat is told what was done before in its folder, and where
earlier work that its message names lives.

* ``models``  the ``workentry`` table;
* ``keys``    folder keys and search words (pure text);
* ``collect`` the sources → rows;
* ``index``   :class:`WorkMap`: refresh and reads (:func:`work_map`);
* ``block``   what a chat turn is told (:func:`work_block`).
"""

from .block import work_block
from .index import WorkMap, work_map

__all__ = ["WorkMap", "work_block", "work_map"]
