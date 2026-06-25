"""Permission Engine (§20).

Every tool invocation passes through here. Modes: allow / ask / deny. Scopes
merge with precedence agent > project/global. The engine is **fail-closed**: an
unknown tool defaults to ``ask``, and ``ask`` with no resolver (headless) denies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..core.models import PermissionMode

# A resolver answers an interactive "ask": True = allow this call, False = deny.
AskResolver = Callable[[str, dict], bool]


@dataclass
class PermissionDecision:
    allowed: bool
    mode: PermissionMode
    reason: str


class PermissionEngine:
    def __init__(
        self,
        base: dict[str, str],
        ask_resolver: AskResolver | None = None,
    ) -> None:
        self._base = dict(base)
        self._ask_resolver = ask_resolver

    def mode_for(
        self, tool_name: str, agent_overrides: dict[str, str] | None = None
    ) -> PermissionMode:
        raw = None
        if agent_overrides and tool_name in agent_overrides:
            raw = agent_overrides[tool_name]
        elif tool_name in self._base:
            raw = self._base[tool_name]
        if raw is None:
            return PermissionMode.ASK  # fail-closed default for unknown tools
        try:
            return PermissionMode(raw)
        except ValueError:
            return PermissionMode.ASK

    def authorize(
        self,
        tool_name: str,
        args: dict,
        agent_overrides: dict[str, str] | None = None,
    ) -> PermissionDecision:
        mode = self.mode_for(tool_name, agent_overrides)
        if mode is PermissionMode.ALLOW:
            return PermissionDecision(True, mode, "allowed by policy")
        if mode is PermissionMode.DENY:
            return PermissionDecision(False, mode, "denied by policy")
        # mode is ASK
        if self._ask_resolver is None:
            return PermissionDecision(
                False, mode, "requires approval; no resolver in headless mode"
            )
        granted = bool(self._ask_resolver(tool_name, args))
        return PermissionDecision(
            granted, mode, "approved by user" if granted else "rejected by user"
        )
