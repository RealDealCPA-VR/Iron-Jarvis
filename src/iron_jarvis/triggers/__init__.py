"""World-starts-work triggers (CX-05 "inbound everything").

Beyond webhooks/comm, the WORLD can start agent work: a calendar event coming
due fires ``calendar`` reflex rules through the SAME gated action path as a local
user (nothing here bypasses the orchestrator + permission engine).

This package is self-contained and OFF BY DEFAULT — a poller does nothing unless
its feature flag is on *and* its secret source (an ICS URL) is configured.
Importing the package registers the trigger tables on ``SQLModel.metadata``.
"""

from __future__ import annotations

from .calendar import CalendarPoller
from .models import CalendarFiredRecord

__all__ = ["CalendarPoller", "CalendarFiredRecord"]
