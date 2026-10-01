"""Assignments (v1.296.0): give an agent a job and the job waits for it.

* :mod:`.models` — the ``assignment`` row (imported by ``core.db`` at boot);
* :mod:`.store` — the queue: create / claim (CAS) / start / finish (3-strike
  breaker) / hold / requeue_lost / cancel / unblock / retry / inbox;
* :mod:`.dispatcher` — the background loop that starts a queued job when its
  agent is free (paused/exhausted/busy/no slot → held with a sentence);
* :mod:`.health` — an agent's last run / last outcome / what waits;
* :mod:`.tools` — ``assign_work``, the tool an agent queues with.
"""

from .dispatcher import AssignmentDispatcher
from .health import agent_health
from .models import AssignmentRecord
from .store import AssignmentStore, as_dict
from .tools import AssignWorkTool

__all__ = [
    "AssignWorkTool",
    "AssignmentDispatcher",
    "AssignmentRecord",
    "AssignmentStore",
    "agent_health",
    "as_dict",
]
