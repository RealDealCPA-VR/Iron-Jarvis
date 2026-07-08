"""Typed failover event + real background-completion notifications.

`provider.failover` is now a typed EventType member (not a raw literal in the
router), and finished sessions / autonomous actions / cross-provider failovers
raise an outbound alert by default.
"""

from __future__ import annotations

import inspect

from iron_jarvis.comm import notifier
from iron_jarvis.core.events import EventType
from iron_jarvis.providers import router


def test_provider_failover_is_a_typed_event() -> None:
    assert EventType.PROVIDER_FAILOVER == "provider.failover"


def test_background_completion_kinds_alert_by_default() -> None:
    defaults = notifier.DEFAULT_ALERT_EVENTS
    assert EventType.SESSION_COMPLETED in defaults
    assert EventType.AUTONOMY_EXECUTED in defaults
    assert EventType.PROVIDER_FAILOVER in defaults


def test_router_uses_the_typed_failover_member() -> None:
    # The router imports the enum and publishes the typed member, not a literal.
    assert router.EventType.PROVIDER_FAILOVER == "provider.failover"
    src = inspect.getsource(router)
    assert "EventType.PROVIDER_FAILOVER" in src
    assert '"provider.failover"' not in src
