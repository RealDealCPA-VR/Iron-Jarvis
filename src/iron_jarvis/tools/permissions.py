"""Permission Engine (§20).

Every tool invocation passes through here. Modes: allow / ask / deny. Scopes
merge with precedence agent > project/global. The engine is **fail-closed**: an
unknown tool defaults to ``ask``, and ``ask`` with no resolver (headless) denies.

**Read-only web retrieval tier.** The tools in :data:`READ_ONLY_WEB_TOOLS`
(``web_search``/``web_fetch``) are classified allow-by-default, the same tier as
the other read-only tools (read_file/grep/ltm_search): an ``ask`` resolution for
them — including the stale ``ask`` persisted into live installs' config.toml by
older defaults — upgrades to ``allow``. Without this, headless runs (researcher
agents, scheduled workflow steps) could NEVER research: ``ask`` with no resolver
fail-closes. An explicit ``deny`` (base policy or agent override) ALWAYS wins —
the upgrade only ever touches ``ask``.

**Deny-floor invariant.** Agent-definition ``permission_overrides`` normally
outrank the base policy, but the host-touching tools in :data:`DENY_FLOOR_TOOLS`
are exempt: an agent definition may keep or *lower* them (to ask/deny) yet can
NEVER *raise* them to ``allow``. This closes the path where an unattended,
headless spawn of a user-authored dynamic agent (``spawn_agent`` auto-approves
in headless mode) reaches the host shell purely because the agent's own
definition set ``shell: allow``. The sanctioned way to grant one of these for a
single task is the interactive per-session grant (``session_allow`` in
:meth:`PermissionEngine.authorize`), never an agent definition. A base ``deny``
remains a hard floor that neither an override nor a session grant can lift.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from ..core.models import PermissionMode

# A resolver answers an interactive "ask": True = allow this call, False = deny.
AskResolver = Callable[[str, dict], bool]

# Low-risk *orchestration* tools that are safe to auto-approve when no human is
# present to answer an "ask" (headless daemon mode). ``delegate`` only spawns a
# subagent in an isolated workspace — it never touches the host — so a Supervisor
# can decompose work without a prompt. Genuinely dangerous tools (e.g. ``shell``)
# are deliberately excluded and stay fail-closed.
SAFE_HEADLESS_TOOLS: frozenset[str] = frozenset({"delegate", "spawn_agent"})

# Host-touching capabilities that an agent-definition ``permission_override`` may
# keep or LOWER (to ask/deny) but must NEVER RAISE to ``allow``. See the module
# docstring: this is the deny-floor that prevents a user-authored dynamic agent
# from silently arming the host shell for an unattended headless spawn. Granting
# one of these for a single task must go through ``session_allow`` instead.
DENY_FLOOR_TOOLS: frozenset[str] = frozenset(
    {"shell", "browser_use", "web_action", "mcp_call"}
)

# READ-ONLY web retrieval — classified allow-by-default, same tier as the other
# read-only tools (read_file/grep/ltm_search). These only READ the public web:
# no host access, no side effects, and their output is already injection-scanned
# + fenced as untrusted data at the tool layer. An ``ask`` tier for them is
# unanswerable headless (no resolver => fail-closed deny), which silently
# blinded researcher agents and scheduled workflow steps — and first-boot wrote
# ``web_search = "ask"`` into live installs' config.toml, so the fix must live
# HERE, not in the config default alone. Resolution: ``ask`` upgrades to
# ``allow``; an explicit ``deny`` always wins; everything else (shell,
# web_action, browser_use, mcp_call, writes) keeps its gate exactly.
READ_ONLY_WEB_TOOLS: frozenset[str] = frozenset({"web_search", "web_fetch"})


def headless_ask_resolver(
    allow: Iterable[str] = SAFE_HEADLESS_TOOLS,
) -> AskResolver:
    """Build an :data:`AskResolver` that auto-approves an allowlist, denies else.

    Used by the daemon (§9) so supervised sessions can ``delegate`` end-to-end
    without an interactive approver, while every other ``ask`` tool — notably
    ``shell`` — remains denied (fail-closed, §20).
    """
    allowed = frozenset(allow)

    def _resolve(tool_name: str, _args: dict) -> bool:
        return tool_name in allowed

    return _resolve


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
        """Resolve the effective mode for *tool_name*.

        Agent-definition ``agent_overrides`` take precedence over the base
        policy, with ONE exception: a tool in :data:`DENY_FLOOR_TOOLS` can never
        be *raised* to ``allow`` by an override. An ``allow`` override on a floor
        tool is dropped and the base policy applies instead (an override to
        ``ask``/``deny`` — keeping or lowering — still takes effect). Unknown
        tools fail closed to ``ask`` — EXCEPT :data:`READ_ONLY_WEB_TOOLS`, whose
        ``ask``/unknown resolution upgrades to ``allow`` (an explicit ``deny``
        on them still wins).
        """
        raw = None
        if agent_overrides and tool_name in agent_overrides:
            candidate = agent_overrides[tool_name]
            # Deny-floor: an agent-definition override must not RAISE a
            # host-touching tool to "allow". Drop such an override and fall
            # through to the base policy; ask/deny overrides still apply.
            if (
                tool_name in DENY_FLOOR_TOOLS
                and str(candidate) == PermissionMode.ALLOW.value
            ):
                candidate = None
            raw = candidate
        if raw is None and tool_name in self._base:
            raw = self._base[tool_name]
        if raw is None:
            mode = PermissionMode.ASK  # fail-closed default for unknown tools
        else:
            try:
                mode = PermissionMode(raw)
            except ValueError:
                mode = PermissionMode.ASK
        # Read-only web retrieval tier: an ``ask`` here is unanswerable headless
        # (fail-closed deny), so it upgrades to ``allow`` — this is what lets
        # researcher agents and scheduled workflows actually search. Only ASK is
        # touched: an explicit deny (base or override) resolved above passes
        # through unchanged, so a user denial always wins.
        if mode is PermissionMode.ASK and tool_name in READ_ONLY_WEB_TOOLS:
            return PermissionMode.ALLOW
        return mode

    def authorize(
        self,
        tool_name: str,
        args: dict,
        agent_overrides: dict[str, str] | None = None,
        session_allow: "Iterable[str] | None" = None,
    ) -> PermissionDecision:
        mode = self.mode_for(tool_name, agent_overrides)
        if mode is PermissionMode.ALLOW:
            return PermissionDecision(True, mode, "allowed by policy")
        if mode is PermissionMode.DENY:
            # A hard deny is NEVER lifted by a session grant — safety floor.
            return PermissionDecision(False, mode, "denied by policy")
        # mode is ASK
        # Per-session grant: the user bundle-approved this tool for THIS task
        # before it ran, so we don't re-ask (and headless doesn't fail-close it).
        # This is ALSO the sanctioned way to grant a DENY_FLOOR_TOOLS capability
        # for one task — the deny-floor blocks agent definitions from raising it,
        # but an explicit interactive session grant on an ``ask`` floor tool still
        # lifts it here (a base ``deny`` above is never lifted).
        if session_allow is not None and tool_name in session_allow:
            return PermissionDecision(True, mode, "granted for this task")
        if self._ask_resolver is None:
            return PermissionDecision(
                False, mode, "requires approval; no resolver in headless mode"
            )
        granted = bool(self._ask_resolver(tool_name, args))
        return PermissionDecision(
            granted, mode, "approved by user" if granted else "rejected by user"
        )
