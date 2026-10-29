"""HELPER AGENTS in an IJ harness turn (v1.372.0, IJ harness part 2 Phase 1).

A harness turn arms ``delegate``, ``spawn_agent``, ``list_agents``,
``skill_search`` and ``skill_load`` (``mode.CODING_TOOLS``; plan mode only the
two skill tools — a helper can change files). The model starts helpers with
its own dialect's tool (Claude Code's ``Agent``/``Task``, Codex's
``spawn_agent``), translated to ``delegate`` / ``spawn_agent`` at the boundary
like every other dialect tool. This module is what makes a helper started FROM
A HARNESS TURN belong to that turn. The user's decision (2026-10-22, plan
decision 4): at most 3 at a time per session, always on the session's own
account and model — never another provider — each with the session's folder
and approval setting.

WHY A HELPER NEEDS THIS. ``delegate`` / ``spawn_agent`` read everything they
hand a child off the PARENT's ``Session`` row (``orch.get_session``). A harness
turn has no such row — its tool calls run under ``build:<id>`` (or ``chat``
for the benchmark) — so a helper used to start on the CONFIGURED DEFAULT
provider and model, in a scratch folder, with no grant and full trust. The
lane now arms a :class:`HelperParent` (a ``ContextVar`` set inside the ONE
tool call that starts the helper — never across a generator ``yield``) and the
two doors read it (:func:`parent_for`):

* PROVIDER + MODEL: the pair that SERVED this turn (the route), else the
  turn's choice. A custom agent pinned to another provider or model is
  refused in words (:func:`target_refusal`), never run on its own pin; a
  ``remote:`` agent is refused (another computer is another provider).
  v1.394.0 (the long-jobs plan's Phase 5, decision 5): a MODEL TEAM MEMBER
  (a custom agent with roles, ``agents/model_team``) is the one exception —
  it runs on ITS OWN model (:func:`for_member`), held to it, on its own
  account when it names one, else its provider's normal pick (never the
  session's account); under Local only a member whose model leaves the
  network is refused in words. The session's own model and account stay
  held for the main reply.
* THE ACCOUNT: the turn's Iron-Proxy :class:`~iron_jarvis.iron_proxy.pin.
  AccountPin` rides the context anyway (``asyncio`` copies it into every task
  the call makes); it is ALSO set explicitly around the creation of the
  helper's task (:func:`pinned`), so a helper never leases another account
  even if a hop on the way dropped the context. The router holds a pinned
  turn on its provider (``held``), so nothing fails over sideways.
* THE FOLDER: the turn's folder (``workspace_root``) — the helper reads and
  writes where the session works.
* LOCAL ONLY: ``options["local_only"]`` when the turn is local only (the
  scope would carry it too — said twice on purpose).
* TRUST: a low (or tainted) turn's helper starts low.
* APPROVAL: the session's posture, ``yolo`` → ``approve_for_me`` (the
  v1.232.0 rule, applied by ``create_session``); the conversation grants as
  ``allow_tools`` — none under ``always_ask`` (``inherited_grants``' rule),
  and under Local only never a tool that reaches outside this PC (the parent
  cards those every time).
* THE TOOLS (review): the SESSION's tools, never more
  (:func:`helper_definition`) — the builder gets the turn's set, a read-only
  type its read-only part, a custom agent the part of its own roster the
  turn holds; the turn's folder rules ride along, and the runtime arms
  nothing on top for the task. A built-in roster carried ``consult`` (a
  remote / cloud-pinned teammate), ``webhook_add`` / ``schedule_create`` at
  the allow tier, ``mcp:*`` and web tools a Local only turn never arms.
* ITS ASKS REACH THE USER: the helper is stamped :data:`HELPER_ORIGIN`
  (``job:build-helper`` — the ``job`` prefix is in BOTH ``runtime.
  ATTENDED_ORIGINS`` and ``ASKING_ORIGINS``), so an ask-tier call WAITS for
  the user under the helper's own session id, with no clock. The Build
  screen shows it on the helper's row (this module streams it, below) and
  answers through the same ``POST /chat/approvals/{id}`` every card uses; the
  bell, the desktop ask watcher and the helper's session page list it too.
  Stop on the session declines nothing by hand: the cancel reaches the
  parked ask and its ``finally`` pops it.

THE CAP (:data:`MAX_HELPERS`, per session): a 4th helper WAITS for a slot
(:func:`helper_slot`) and its row says so ("waiting to start"); it is never
refused. Why wait: the model asked for that work in one breath — a refusal
would cost a round and invite a retry — and helpers cannot start helpers of
their own (no helper type carries ``delegate``), so a waiter can only be
waiting on siblings that are already running: no wait cycle exists.

LIFETIME: a helper is ONE tool call. ``delegate`` and ``spawn_agent`` both
AWAIT the helper's run and return its report (Claude Code's
``run_in_background`` and Codex's ``spawn_agent`` therefore return when the
helper is done; several helpers started in one message run at the same time —
``chat_turn._run_tool_round``'s ``together``). So a helper never outlives the
turn that started it: Stop on the turn cancels every helper call in flight
(:func:`until_stopped`, raced in the lane), the cancel reaches the helper's
run, its ``finally`` ends its programs (``procs.end_session``), and the
orchestrator settles it CANCELLED. Archive / delete / shutdown also run
``procs.end_session("build:<id>")`` / ``end_all``, whose end hook
(:func:`_end_hook`) stops any helper still registered for the session.

THE ROW: :class:`HelperCall` follows the helper's own stream
(``StreamHub.subscribe(child)`` — step frames, phase) and its pending asks
(``approvals.pending_for(child)``) and puts ``helper`` frames on the turn's
side channel (``TurnInteractions.side`` — yielded while the call runs, kept
in the turn's log so a rejoin replays them). The build route saves the
settled helpers on the reply line (``routes/build._ReplyWatch``).

Nothing here blocks the loop; every reader never raises.
"""

from __future__ import annotations

import asyncio
import contextlib
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Callable, Iterator

#: The two tools that start a helper.
HELPER_TOOLS: frozenset[str] = frozenset({"delegate", "spawn_agent"})
#: The agent tools a harness turn arms in ACT mode.
AGENT_TOOLS: tuple[str, ...] = ("delegate", "spawn_agent", "list_agents")
#: The skill tools a harness turn arms in BOTH modes.
SKILL_TOOLS: tuple[str, ...] = ("skill_search", "skill_load")
#: At most this many helpers RUN at once per session (plan decision 4).
MAX_HELPERS = 3
#: The origin a harness turn's helper is created with: ATTENDED + ASKING.
HELPER_ORIGIN = "job:build-helper"
#: The side frame's event name.
HELPER_EVENT = "helper"
#: v1.372.0 review: the run option naming the Build session (its thread id)
#: a helper belongs to — read by the pending listing for the bell / desktop.
BUILD_SESSION_OPTION = "build_session"
#: v1.401.0: the run option of a READ-ONLY reviewer (the finish check's team
#: review): the runtime arms nothing on top of its definition's tools.
REVIEW_ONLY_OPTION = "review_only"
#: v1.401.0: the ONLY tools a read-only reviewer may hold (those of them the
#: turn holds): reading and searching the folder. Never a write, a command, a
#: process tool, the web, a page or a helper door.
REVIEW_TOOLS: tuple[str, ...] = ("read_file", "glob", "grep", "list_files")
#: A Build session's tool session id prefix (``harness/sessions.session_id_for``).
_BUILD_PREFIX = "build:"


def build_thread_of(session_id: Any) -> str:
    """The Build session's thread id in ``build:<id>``, else ""."""
    sid = str(session_id or "")
    return sid[len(_BUILD_PREFIX):] if sid.startswith(_BUILD_PREFIX) else ""
#: How often a running helper's pending asks are looked at (in memory).
ASK_POLL_S = 0.4
#: Caps on what a frame / a saved helper carries.
TASK_CHARS = 240
RESULT_CHARS = 1200
OUTPUT_CHARS = 400
MAX_SAVED_STEPS = 20
MAX_SAVED_HELPERS = 12

#: The sentence a helper call answers when the user stopped the turn.
STOPPED_RESULT = "Stopped by the user before the helper finished."
#: The states a helper row can be in.
STATES = ("starting", "queued", "running", "waiting", "done", "failed", "stopped")
FINAL_STATES = frozenset({"done", "failed", "stopped"})


def _short(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _now_ms() -> int:
    return int(time.time() * 1000)


# --------------------------------------------------------------------------- #
# the parent a helper inherits from
# --------------------------------------------------------------------------- #


@dataclass
class HelperParent:
    """What a helper started from ONE harness tool call inherits."""

    #: The turn's tool session: ``build:<id>`` (a Build session) or ``chat``.
    session_id: str
    #: The turn's folder ("" = none: the helper gets its own scratch folder).
    folder: str
    #: The provider + model the helper runs on (the turn's served pair).
    provider: str
    model: str
    #: The session's approval posture (chat vocabulary; normalised at create).
    approval_mode: str = ""
    #: The conversation's grants ("Allow for this conversation").
    allow_tools: tuple[str, ...] = ()
    local_only: bool = False
    low_trust: bool = False
    trust_reason: str = ""
    project_id: "str | None" = None
    #: The turn's Iron-Proxy account pin (``AccountPin`` or None).
    pin: Any = None
    #: The call this parent was armed for (set by :func:`helper_call`).
    call: "HelperCall | None" = None
    #: v1.372.0 review: the real tools THIS turn may run (the lane's armed +
    #: ask-armed set). A helper works with these and NO others
    #: (:func:`helper_definition`); ``()`` leaves the definition unchanged.
    tools: tuple[str, ...] = ()
    #: v1.381.0: the turn's ``harness/command_shell.TurnRule`` (a project's or
    #: a plugin's command with shell lines) — armed EXPLICITLY around the
    #: helper's task (:func:`pinned`), like the pin, so a helper's commands ask
    #: exactly as the turn's do whatever a hop did with the context.
    command_rule: Any = None
    #: v1.372.0 review: NARROWING permission overrides the helper carries —
    #: ``(key, "deny" | "ask")``: the turn's folder rules and low-trust
    #: narrowing, and under Local only an "ask" for every tool that reaches
    #: outside this PC (the parent cards those every time).
    rules: tuple[tuple[str, str], ...] = ()
    #: v1.394.0: True for a MODEL TEAM MEMBER on its own model
    #: (:func:`for_member`): ``provider``/``model`` are the member's and
    #: ``pin`` is the member's own account pin — or None, which is then SET
    #: (its provider's normal pick), never left to inherit the session's.
    own_model: bool = False
    #: v1.395.0: the MEMBER's name when ``own_model`` (its hold's refusal
    #: names it — never "This Build session" for a member's own model).
    member: str = ""
    #: v1.395.0 review: one id per REPLY (made with the turn's base,
    #: ``chat_turn.helper_base``). The model team's record keeps it on every
    #: piece, and a reviewer's verdict judges only the code / test pieces of
    #: the same reply (``agents/team_record.credit_review``) — a coding
    #: session lasts for days, and a review of this reply's change must not
    #: judge every unreviewed piece the session ever handed out.
    turn: str = ""
    #: v1.401.0 (the team's review at the finish, ``harness/finish``): the
    #: helper only READS — it gets the turn's :data:`REVIEW_TOOLS` and nothing
    #: else, whatever its own definition or base type says
    #: (:func:`helper_definition`), and its run arms nothing on top (the
    #: runtime reads the run option this sets, :meth:`options`). A guarantee,
    #: not a request: a write or a command it calls is refused "not armed".
    review_only: bool = False
    #: v1.403.0 (the coding-session team, ``harness/team``): the turn's
    #: :class:`~iron_jarvis.harness.team.TeamTurn` when the session has a team
    #: (None: solo). The helper doors hand pieces ONLY to its members (the
    #: workers for code / test / read, the reviewer for a review); a worker
    #: gets the whole coding set and the worker rules, the reviewer only reads.
    team: Any = None

    def grants(self) -> tuple[list[str], str]:
        """``(allow_tools, approval_mode)`` for the helper's row:
        ``always_ask`` forwards the posture and NO grant (the v1.288.0 rule —
        a parent that asks once per call must not hand its child a list that
        runs silently); every other posture forwards the grants."""
        mode = str(self.approval_mode or "").strip()
        if mode == "always_ask":
            return [], mode
        return [str(t) for t in self.allow_tools if str(t).strip()], mode

    def trust(self) -> tuple["str | None", str]:
        if not self.low_trust:
            return None, ""
        return "low", (" ".join(str(self.trust_reason or "").split())
                       or "inherited from a low-trust Build turn")

    def options(self) -> "dict[str, Any] | None":
        out: dict[str, Any] = {}
        if self.local_only:
            out["local_only"] = True
        # v1.372.0 review: WHERE the helper's asks belong — the Build session
        # that started it — so the bell and the desktop open THAT session
        # (``/chat?view=code&session=<id>``), where its row and its card are.
        thread = build_thread_of(self.session_id)
        if thread:
            out[BUILD_SESSION_OPTION] = thread
        if self.review_only:
            # v1.401.0: the runtime arms nothing on top of the reviewer's
            # read-only tools (not even an agent's own notebook).
            out[REVIEW_ONLY_OPTION] = True
        return out or None


HELPER_PARENT: ContextVar["HelperParent | None"] = ContextVar("ij_helper_parent", default=None)


def parent_for(ctx: Any) -> "HelperParent | None":
    """The armed parent for a tool call, or None. Only when it was armed for
    THIS call's session (a nested helper's own tool call runs under its own
    session id and never inherits the turn's scope). Never raises."""
    try:
        parent = HELPER_PARENT.get()
    except Exception:  # noqa: BLE001
        return None
    if parent is None:
        return None
    if str(getattr(ctx, "session_id", "") or "") != parent.session_id:
        return None
    return parent


def _same_provider(manager: Any, a: str, b: str) -> bool:
    """Do two provider names reach the same adapter (a keyless ``anthropic``
    served by ``claude-cli``)? The router's own rule
    (``ModelRouter._same_provider``). Never raises."""
    if manager is None or not a or not b:
        return False
    try:
        pa = getattr(manager.get(a), "provider", None)
        pb = getattr(manager.get(b), "provider", None)
    except Exception:  # noqa: BLE001
        return False
    return bool(pa) and pa == pb


def target_refusal(parent: "HelperParent | None", *, entry: Any = None, raw: str = "",
                   record: Any = None, manager: Any = None, platform: Any = None) -> str:
    """The sentence a harness turn's helper is refused with, or "".

    A REMOTE agent is another computer (and, as far as this session can
    know, another provider): refused. A CUSTOM agent pinned to a provider or
    model other than the session's own: refused in words — the user's rule is
    "always on the session's own account and model", so the pin is neither
    obeyed nor silently overridden.

    v1.394.0 (decision 5): a MODEL TEAM MEMBER with a model of its own is
    the exception — allowed (it runs on its own model, :func:`for_member`),
    except under Local only when that model leaves the network: refused in
    words (``model_team.local_only_refusal``)."""
    if parent is None:
        return ""
    name = str(getattr(entry, "name", "") or raw or "").strip()
    kind = str(getattr(entry, "kind", "") or "")
    if kind == "remote" or name.lower().startswith("remote:"):
        return (
            f"'{name}' runs on another computer. A Build session's helpers work on this PC, "
            "on the session's own account and model — start a helper here instead "
            "(researcher, builder or reviewer)."
        )
    if record is not None:
        from ..agents import model_team as _mt

        if _mt.runs_elsewhere(record):
            if parent.local_only:
                return _mt.local_only_refusal(platform, record, name)
            # v1.394.0 review: the account it would run on stays in the
            # session's account GROUP (decision 4) — refused in words when the
            # group has no account of the member's vendor.
            return _mt.member_account(platform, record, parent.pin, coding=True)[1]
        prov = str(getattr(record, "provider", "") or "").strip()
        mod = str(getattr(record, "model", "") or "").strip()
        # v1.372.0 review: an ALIAS of the session's provider is the session's
        # provider (``manager`` resolves it, as the router does).
        other = prov and prov != parent.provider and not _same_provider(manager, prov, parent.provider)
        if other or (mod and parent.model and mod != parent.model):
            pinned = "/".join(p for p in (prov, mod) if p)
            return (
                f"'{name}' is set to use {pinned}. A Build session's helpers always use the "
                f"session's own model ({parent.provider}"
                + (f"/{parent.model}" if parent.model else "")
                + ") — pick a helper with no model of its own (researcher, builder, reviewer)."
            )
    return ""


#: A keyword default that is not None (None is a real pin: "no account").
_UNSET: Any = object()


def for_member(parent: "HelperParent | None", platform: Any, record: Any, *,
               pin: Any = _UNSET) -> "HelperParent | None":
    """The parent a MODEL TEAM MEMBER's helper runs from (v1.394.0, decision
    5): the same session, folder, posture, grants, trust and Local only — but
    ITS OWN provider + model (held by :func:`pinned`) and the account
    ``model_team.member_account`` names: its own ``account``, else the
    SESSION's account when it is of the member's vendor, else an account of
    the session's group, else the vendor's first usable one — always PINNED
    (v1.394.0 review: an unpinned pick landed a member on an account nobody
    picked, and rotated). Never the session's pin for another vendor (a
    Codex helper must not lease under the session's Claude pin). Anyone who
    is not a member with a model of its own gets ``parent`` back unchanged.

    v1.395.0: ``pin`` — the account the TEAM ROUTER chose for a role's piece
    (``model_team.RolePick.pin``, the lowest usage below its threshold among
    the accounts the same rules allow) — replaces ``member_account``'s."""
    if parent is None or record is None:
        return parent
    from ..agents import model_team as _mt

    name = str(getattr(record, "name", "") or "")
    # v1.403.0 (the coding-session team): the session's REVIEWER only reads —
    # when the lead hands it a piece as much as at the finish check — and a
    # team member is named on its parent even when it runs on the session's
    # own model (its worker rules key on the name).
    team = getattr(parent, "team", None)
    review_only = parent.review_only or bool(
        team is not None and getattr(team, "team", None) is not None and team.team.is_reviewer(name)
    )
    if not _mt.runs_elsewhere(record):
        if team is not None:
            return replace(parent, member=name, review_only=review_only)
        return parent
    return replace(
        parent,
        provider=str(getattr(record, "provider", "") or "").strip(),
        model=_mt.pinned_model(platform, record),
        pin=pin if pin is not _UNSET else _mt.member_account(platform, record, parent.pin, coding=True)[0],
        own_model=True,
        member=name,
        review_only=review_only,
    )


#: Never handed to a helper even when its turn holds them: the helper doors
#: (a helper starts no helpers of its own — which is also why the slot wait
#: has no cycle) and the turn's own conversation tools (the user's question
#: card and the plan door belong to the session's turn).
NOT_FOR_HELPERS: frozenset[str] = frozenset(
    {*HELPER_TOOLS, "list_agents", "exit_plan_mode", "ask_user"}
)
#: Built-in helpers that only READ (Claude Code's ``Explore`` / ``Plan`` and
#: Codex's ``explorer`` map to the researcher): the turn's read-only tools.
READ_ONLY_TYPES: frozenset[str] = frozenset({"researcher", "reviewer", "guide"})


def helper_definition(parent: "HelperParent | None", definition: Any, *, custom: bool = False) -> Any:
    """The definition a harness turn's helper RUNS with (v1.372.0 review).

    A helper works with THE SESSION'S tools, never more — Claude Code's rule
    for its subagents (a general-purpose one gets the parent's tools, Explore
    the read-only ones). A built-in definition's own roster is written for
    office and team work (``consult`` a remote or a cloud-pinned teammate,
    ``webhook_add`` / ``schedule_create`` at the allow tier, ``mcp:*``,
    ``web_search`` that a Local only turn never arms) and lacked the coding
    tools (``glob``, ``multi_edit``, ``apply_patch``, ``process_*``); a
    helper started from a Build turn used to run on it, so the session's
    folder rules, Local only and "never another provider" all had a door
    around them. Now:

    * the BUILDER (``general-purpose``) gets the turn's whole tool set;
    * a READ-ONLY type (researcher / reviewer / guide) gets the turn's tools
      that change nothing (``mode.PLAN_MODE_TOOLS``);
    * a CUSTOM agent, or any other built-in, keeps ITS OWN tools that the
      turn also holds — never one the turn does not;
    * the helper door tools and the turn's own question / plan tools never;
    * the turn's narrowing rules are merged in (deny wins; ``ask`` never
      lands on a key the definition denies).

    The runtime arms nothing more for the task (``runtime`` skips
    ``arm_for_task`` for :data:`HELPER_ORIGIN`). ``parent`` None, or a parent
    with no tool set, returns ``definition`` unchanged."""
    if parent is None or definition is None:
        return definition
    if not parent.tools and not parent.review_only:
        # (A read-only reviewer is ALWAYS cut — to nothing, if the turn holds
        # no reading tool: never left with its own definition's.)
        return definition
    from .mode import PLAN_MODE_TOOLS
    # v1.394.0: a MODEL TEAM MEMBER that stores no tool list of its own works
    # like the built-in it is based on (the builder gets the turn's whole set,
    # a read-only base the turn's read-only part) — its base type's roster is
    # written for office work and lacks the coding tools. Callers pass
    # ``custom=False`` for such a member (``model_team.inherits_tools``).

    turn = [t for t in dict.fromkeys(parent.tools) if t not in NOT_FOR_HELPERS]
    kind = str(getattr(getattr(definition, "type", None), "value", "") or "")
    own = set(getattr(definition, "tools", None) or [])
    if parent.review_only:
        # v1.401.0: the finish check's team review READS — whatever the
        # member's own tools or base type (a builder-based member would get
        # the turn's whole set below).
        reads = set(REVIEW_TOOLS)
        tools = [t for t in turn if t in reads]
    elif custom:
        tools = [t for t in turn if t in own]
    elif kind == "builder":
        tools = list(turn)
    elif kind in READ_ONLY_TYPES:
        reads = set(PLAN_MODE_TOOLS)
        tools = [t for t in turn if t in reads]
    else:
        tools = [t for t in turn if t in own]
    overrides = dict(getattr(definition, "permission_overrides", None) or {})
    for key, mode in parent.rules:
        if mode == "deny":
            overrides[key] = "deny"
        elif mode == "ask" and overrides.get(key) != "deny":
            overrides[key] = "ask"
    # Review (v1.375.0): a helper of a session on its own git branch is told
    # so, like the session itself (``branches.prompt_line`` — "" otherwise,
    # so every other helper's prompt is exactly as before).
    from .branches import prompt_line as branch_line

    prompt = str(getattr(definition, "system_prompt", "") or "") + branch_line()
    # v1.403.0 (the coding-session team): a WORKER is told its part — one
    # piece, only the files it names, its tests, a report. Only a worker of a
    # team turn (every other helper's prompt is exactly as before).
    team = getattr(parent, "team", None)
    if (
        team is not None and not parent.review_only and parent.member
        and getattr(team, "team", None) is not None and team.team.is_worker(parent.member)
    ):
        from .team import worker_rules

        prompt += worker_rules()
    return replace(definition, tools=tools, permission_overrides=overrides, system_prompt=prompt)


@contextlib.contextmanager
def pinned(parent: "HelperParent | None") -> Iterator[None]:
    """The context a helper's task is CREATED in (the task copies it):

    * the turn's account pin, armed EXPLICITLY — the helper leases that
      account and no other, whatever a hop on the way did with the context;
    * the turn's model hold (``providers.hold``), armed EXPLICITLY — the
      router keeps the helper on the session's provider even with no account
      pin (no fallback, no failover, no capability reroute);
    * NO app-question scope (``mcp.interact.MCP_SCOPE``): the turn's own
      scope belongs to the ``delegate`` call that is running, so a pack the
      helper calls would otherwise route its question onto that call's
      (hidden) card. A helper is an agent run, and an agent run's pack gets
      the instant decline every agent run gets;
    * v1.381.0: the turn's command rule (``harness/command_shell``), armed
      EXPLICITLY — a project's or a plugin's shell lines ask in the helper's
      run exactly as in the turn's."""
    if parent is None:
        yield
        return
    from ..iron_proxy.pin import reset_pin, set_pin
    from ..mcp.interact import MCP_SCOPE
    from ..providers.hold import reset_hold, set_hold
    from .command_shell import reset_turn_rule, set_turn_rule

    scope_token = MCP_SCOPE.set(None)
    # v1.394.0: a member on its own model takes ITS pin — None included (its
    # provider's normal pick): leaving the session's pin in the copied
    # context would lease a Codex helper under the session's Claude account.
    token = set_pin(parent.pin) if (parent.pin is not None or parent.own_model) else None
    # v1.395.0: a member's own model is HIS hold — its refusal names it.
    who = f"member:{parent.member}" if (parent.own_model and parent.member) else ""
    hold_token = set_hold(parent.provider, parent.model, who)
    rule_token = set_turn_rule(parent.command_rule)
    try:
        yield
    finally:
        reset_turn_rule(rule_token)
        reset_hold(hold_token)
        if token is not None:
            reset_pin(token)
        try:
            MCP_SCOPE.reset(scope_token)
        except (ValueError, LookupError):
            MCP_SCOPE.set(None)


# --------------------------------------------------------------------------- #
# the cap
# --------------------------------------------------------------------------- #


@dataclass
class _Slot:
    sem: asyncio.Semaphore
    users: int = 0


#: (loop id, tool session) -> its slot. Per LOOP: a semaphore is only ever
#: awaited on the loop that made it.
_SLOTS: dict[tuple[int, str], _Slot] = {}


@contextlib.asynccontextmanager
async def helper_slot(parent: "HelperParent | None") -> AsyncIterator[None]:
    """Hold one of the session's :data:`MAX_HELPERS` helper slots for the
    helper's run. A helper that must wait says so on its row ("queued").
    No parent: no cap (every other caller of the two doors, unchanged)."""
    if parent is None:
        yield
        return
    key = (id(asyncio.get_running_loop()), parent.session_id)
    slot = _SLOTS.get(key)
    if slot is None:
        slot = _Slot(asyncio.Semaphore(MAX_HELPERS))
        _SLOTS[key] = slot
    slot.users += 1
    try:
        if slot.sem.locked() and parent.call is not None:
            parent.call.queued()
        async with slot.sem:
            yield
    finally:
        slot.users -= 1
        if slot.users <= 0:
            _SLOTS.pop(key, None)


# --------------------------------------------------------------------------- #
# the live registry (Stop / archive / shutdown)
# --------------------------------------------------------------------------- #

#: tool session -> {child session id: HelperCall}
_LIVE: dict[str, dict[str, "HelperCall"]] = {}
_LIVE_LOCK = threading.Lock()


def live_helpers(session_id: str) -> list[str]:
    """The child session ids of the helpers running for ``session_id``."""
    with _LIVE_LOCK:
        return list(_LIVE.get(str(session_id or ""), {}))


def child_parent(session_id: str, child: str) -> "HelperParent | None":
    """The :class:`HelperParent` the live helper ``child`` of tool session
    ``session_id`` started from, or None (v1.404.1). In memory; never raises."""
    with _LIVE_LOCK:
        call = _LIVE.get(str(session_id or ""), {}).get(str(child or ""))
    return getattr(call, "parent", None)


def asking(session_id: str) -> bool:
    """Is a helper of ``session_id`` parked on an ask of its own? (The
    session list's "waiting" dot reads it.) In memory; never raises."""
    with _LIVE_LOCK:
        calls = list(_LIVE.get(str(session_id or ""), {}).values())
    return any(bool(c.ask_id) for c in calls)


def stop_helpers(session_id: "str | None") -> list[str]:
    """Stop every helper running for ``session_id`` (None = every session):
    cancel each helper's run on its own loop (``team.stop_child``), which the
    starting door reads as the user's Stop. Returns the child ids it reached.
    Thread-safe; never raises."""
    from ..agents import team as _team

    with _LIVE_LOCK:
        if session_id is None:
            children = [c for calls in _LIVE.values() for c in calls]
        else:
            children = list(_LIVE.get(str(session_id), {}))
    reached = []
    for child in children:
        try:
            if _team.stop_child(child):
                reached.append(child)
        except Exception:  # noqa: BLE001
            pass
    return reached


def _end_hook(session_id: "str | None") -> list[int]:
    """``procs.end_session`` / ``end_all`` hook: a session that is archived,
    deleted or shut down leaves no helper running. Returns no pids (the
    helper's own run ends its programs)."""
    stop_helpers(session_id)
    return []


def _register_end_hook() -> None:
    try:
        from ..core import procs

        procs.add_end_hook(_end_hook)
    except Exception:  # noqa: BLE001
        pass


_register_end_hook()


# --------------------------------------------------------------------------- #
# one helper call: its row's frames
# --------------------------------------------------------------------------- #


Emit = Callable[[str, dict], None]


def helper_plan(engine: Any, session_id: str) -> "dict[str, Any] | None":
    """A helper run's plan as the checklist's saved form (``{steps: [{goal,
    state}], total}``) — the daemon's own copy (``agents/transcript``) — or
    None when its run made none (most never do) or it cannot be read. While
    the run is live the first unfinished step is ``running``; once it is over
    every step is settled. BLOCKING (two indexed reads); never raises."""
    from ..agents.transcript import latest_plan, plan_checklist

    try:
        stored = latest_plan(engine, session_id)
    except Exception:  # noqa: BLE001 — unreadable: no plan, never a guess
        return None
    if not stored:
        return None
    steps = plan_checklist(stored)
    return {"steps": steps[:50], "total": len(steps)} if steps else None


@dataclass
class HelperCall:
    """One helper-starting tool call of a harness turn, and the row it draws."""

    call_id: str
    agent: str
    task: str
    emit: "Emit | None" = None
    session_id: str = ""
    child: str = ""
    state: str = "starting"
    steps: int = 0
    since: int = field(default_factory=_now_ms)
    ask_id: str = ""
    _platform: Any = None
    _queue: Any = None
    _pump: "asyncio.Task | None" = None
    _closed: bool = False
    #: v1.374.0 (part 2, Phase 3): the helper's PLAN when its run planned its
    #: work (the decomposed lane) — the daemon's own checklist
    #: (``transcript.plan_checklist``), re-read when a phase frame says the
    #: run moved on, and sent on the row's frame when it changed.
    _plan: Any = None
    _plan_due: bool = False
    #: v1.404.1: the :class:`HelperParent` this call started from — its
    #: posture, Local only, trust and rules — so a grant made on ANOTHER card
    #: of the session wakes this helper's card only where this helper itself
    #: would have been handed that grant (``session_grants``).
    parent: Any = None
    #: v1.390.0 (the Timeline): the provider + model the helper's run is
    #: served by (its run's own ``meta`` frame; the row of its session at the
    #: end) — "" until known. Held to the session's pair today (part 2,
    #: decision 4); the Timeline names it per row so a team of models
    #: (the long-jobs plan's Phase 5) shows itself.
    provider: str = ""
    model: str = ""
    #: v1.395.0 (the long-jobs plan's Phase 6): the ROLE the planner asked
    #: for ("read", "code", …) when it asked for one instead of a member, and
    #: the TEAM ROUTER's one sentence of why this member took it ("local: good
    #: record for reading", "waited: …") — on the row and its Timeline row.
    role: str = ""
    reason: str = ""

    # -- frames ------------------------------------------------------------- #

    def frame(self, **extra: Any) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.call_id,
            "agent": self.agent,
            "task": self.task,
            "state": self.state,
            "steps": self.steps,
            "since": self.since,
        }
        if self.child:
            out["session"] = self.child
        if self.provider:
            out["provider"] = self.provider
        if self.model:
            out["model"] = self.model
        if self.role:
            out["role"] = self.role
        if self.reason:
            out["reason"] = self.reason
        out.update(extra)
        return out

    def say(self, **extra: Any) -> None:
        if self.emit is None:
            return
        try:
            self.emit(HELPER_EVENT, self.frame(**extra))
        except Exception:  # noqa: BLE001 — a row never breaks a helper
            pass

    def queued(self) -> None:
        self.state = "queued"
        self.say(note=f"{MAX_HELPERS} helpers are already working")

    # -- the helper's run ---------------------------------------------------- #

    def attach(self, platform: Any, child_session_id: str, agent: str = "", task: str = "") -> None:
        """The helper's session exists: follow its stream and its asks."""
        if self._closed or not child_session_id:
            return
        self.child = str(child_session_id)
        if agent:
            self.agent = _short(agent, 80)
        if task:
            self.task = _short(task, TASK_CHARS)
        self.state = "running"
        self._platform = platform
        with _LIVE_LOCK:
            _LIVE.setdefault(self.session_id, {})[self.child] = self
        hub = getattr(platform, "streams", None)
        if hub is not None:
            try:
                self._queue = hub.subscribe(self.child)
            except Exception:  # noqa: BLE001
                self._queue = None
        self.say()
        try:
            self._pump = asyncio.ensure_future(self._follow())
        except RuntimeError:  # no loop: nothing to follow
            self._pump = None

    async def _follow(self) -> None:
        while True:
            frame = None
            if self._queue is not None:
                try:
                    frame = await asyncio.wait_for(self._queue.get(), timeout=ASK_POLL_S)
                except asyncio.TimeoutError:
                    frame = None
            else:
                await asyncio.sleep(ASK_POLL_S)
            if frame is not None:
                self._take(frame)
            if self._plan_due:
                self._plan_due = False
                await self._refresh_plan()
            self._check_ask()

    def _take(self, frame: Any) -> None:
        if not isinstance(frame, dict):
            return
        event = frame.get("event")
        data = frame.get("data") if isinstance(frame.get("data"), dict) else {}
        if event == "tool_call":
            status = str(data.get("status") or "")
            step: dict[str, Any] = {
                "id": str(data.get("id") or ""),
                "name": str(data.get("name") or ""),
                "status": "finished" if status == "finished" else "started",
            }
            if status == "finished":
                self.steps += 1
                step["ok"] = bool(data.get("ok"))
                if data.get("output"):
                    step["output"] = _short(data.get("output"), OUTPUT_CHARS)
                if isinstance(data.get("detail"), dict):
                    step["detail"] = data["detail"]
            elif isinstance(data.get("args"), dict):
                # Already redacted by the runtime (RunSink.tool_started).
                step["args"] = data["args"]
            self.say(step=step)
        elif event == "meta":
            # v1.390.0: the model the helper's run is served by.
            provider = _short(data.get("provider"), 120)
            model = _short(data.get("model"), 200)
            if (provider, model) != (self.provider, self.model) and (provider or model):
                self.provider, self.model = provider or self.provider, model or self.model
                self.say()
        elif event == "phase":
            title = str(data.get("phase") or "")
            if title:
                self.say(phase=title, detail=_short(data.get("detail"), 160))
                # v1.374.0: a phase is where a planned run moves (planned,
                # step k of N, checking, writing up): its checklist is read
                # again, off the loop, by the follower.
                self._plan_due = True

    async def _refresh_plan(self) -> None:
        """Read the helper run's plan (off the loop) and send it on the row
        when it changed (v1.374.0). Never raises."""
        platform, child = self._platform, self.child
        if platform is None or not child:
            return
        try:
            plan = await asyncio.to_thread(helper_plan, platform.engine, child)
        except Exception:  # noqa: BLE001 — a row never breaks a helper
            return
        if plan and plan != self._plan:
            self._plan = plan
            self.say(plan=plan)

    def _check_ask(self) -> None:
        platform = self._platform
        approvals = getattr(platform, "approvals", None)
        if approvals is None or not self.child:
            return
        try:
            rows = approvals.pending_for(self.child)
        except Exception:  # noqa: BLE001
            rows = []
        row = rows[0] if rows else None
        if row is not None and row.get("approval_id") != self.ask_id:
            self.ask_id = str(row.get("approval_id") or "")
            self.state = "waiting"
            ask: dict[str, Any] = {
                "id": self.ask_id,
                "tool": str(row.get("tool") or ""),
                "args": row.get("args") if isinstance(row.get("args"), dict) else {},
            }
            if isinstance(row.get("count"), int) and row["count"] > 1:
                ask["count"] = row["count"]
                ask["examples"] = list(row.get("examples") or [])[:3]
            if row.get("cost_note"):
                ask["cost_note"] = str(row["cost_note"])
            # v1.381.0: an ask answered ONCE, every time (a project's or a
            # plugin's command's shell, a commit / push on the session's
            # branch): the helper's card offers "Allow once" and Deny only,
            # and says why — a wider answer is read as once by the runtime.
            if row.get("once_only") is True:
                ask["once_only"] = True
                if row.get("once_reason"):
                    ask["once_reason"] = str(row["once_reason"])
            self.say(ask=ask)
        elif row is None and self.ask_id:
            self.ask_id = ""
            self.state = "running"
            self.say(ask=None)

    def close(self) -> None:
        """The call is over: stop following, deliver what is still queued."""
        if self._closed:
            return
        self._closed = True
        if self._pump is not None and not self._pump.done():
            self._pump.cancel()
        q = self._queue
        if q is not None:
            while True:
                try:
                    self._take(q.get_nowait())
                except Exception:  # noqa: BLE001 — empty
                    break
            hub = getattr(self._platform, "streams", None)
            with contextlib.suppress(Exception):
                if hub is not None:
                    hub.unsubscribe(self.child, q)
        if self.child:
            with _LIVE_LOCK:
                calls = _LIVE.get(self.session_id)
                if calls is not None:
                    calls.pop(self.child, None)
                    if not calls:
                        _LIVE.pop(self.session_id, None)

    def child_of(self, result: Any) -> str:
        """The helper's own session id: the one it attached, else the one its
        call's result names."""
        if self.child:
            return self.child
        data = getattr(result, "data", None) if result is not None else None
        data = data if isinstance(data, dict) else {}
        return str(data.get("child_session_id") or "")

    async def read_usage(self, result: Any) -> "dict[str, Any] | None":
        """v1.390.0 (the Timeline): what the helper's run USED, read off its
        session's row once it ended (the run settles its tokens and cost
        there before its call returns): ``{input_tokens, output_tokens,
        cost_usd?, list_price_equivalent?}`` — the receipt's shape, its cost by
        ``eval.pricing.recorded_cost`` (the ONE rule every spend reader uses);
        the row's provider/model are taken too. Off the loop; None when there
        is no row (the helper never started) or it cannot be read. Never
        raises."""
        platform = self._platform
        child = self.child_of(result)
        engine = getattr(platform, "engine", None)
        if engine is None or not child:
            return None
        try:
            row = await asyncio.to_thread(_session_usage, engine, child)
        except Exception:  # noqa: BLE001 — a row never breaks a helper
            return None
        if row is None:
            return None
        provider, model, usage = row
        if provider:
            self.provider = provider
        if model:
            self.model = model
        return usage

    def finish(self, result: Any, *, stopped: bool = False, crash: str = "",
               usage: "dict[str, Any] | None" = None) -> None:
        """The helper's last frame: how it ended and what it reported (and,
        v1.390.0, what it used — ``usage`` from :meth:`read_usage`)."""
        data = getattr(result, "data", None) if result is not None else None
        data = data if isinstance(data, dict) else {}
        if not self.child and data.get("child_session_id"):
            self.child = str(data["child_session_id"])
        if stopped:
            state, text = "stopped", STOPPED_RESULT
        elif result is None:
            state, text = "failed", crash or "The helper could not start."
        elif getattr(result, "ok", False):
            state, text = "done", report_text(getattr(result, "output", ""))
        else:
            state = "stopped" if str(data.get("state") or "") == "cancelled" else "failed"
            text = str(getattr(result, "error", "") or "The helper failed.")
        self.state = state
        self.ask_id = ""
        extra: dict[str, Any] = {"usage": usage} if isinstance(usage, dict) else {}
        self.say(result=_short(text, RESULT_CHARS), ended=_now_ms(), ask=None, **extra)


def _session_usage(engine: Any, session_id: str) -> "tuple[str, str, dict[str, Any]] | None":
    """``(provider, model, usage)`` of a helper's session row (see
    :meth:`HelperCall.read_usage`). BLOCKING."""
    from ..core.db import session_scope
    from ..core.models import Session
    from ..eval.pricing import is_list_price_equivalent, is_priced, recorded_cost

    with session_scope(engine) as db:
        row = db.get(Session, session_id)
        if row is None:
            return None
        provider = str(row.provider or "")
        model = str(row.model or "")
        inp = int(row.input_tokens or 0)
        out = int(row.output_tokens or 0)
        stored = float(row.cost_usd or 0.0)
    usage: dict[str, Any] = {"input_tokens": inp, "output_tokens": out}
    cost = recorded_cost(provider, model, inp, out, stored)
    if stored > 0 or is_priced(provider, model):
        usage["cost_usd"] = round(float(cost), 9)
    if is_list_price_equivalent(provider, model):
        usage["list_price_equivalent"] = True
    return provider, model, usage


def report_text(output: Any) -> str:
    """A helper's report without its address line (``[<agent>
    agent_run_id=… session=…]`` — the model's handle, not the user's
    reading)."""
    text = str(output or "")
    first, _, rest = text.partition("\n")
    if first.startswith("[") and "agent_run_id=" in first and first.rstrip().endswith("]"):
        return rest.strip()
    return text.strip()


def call_target(name: str, args: Any) -> tuple[str, str]:
    """``(agent, task)`` a helper call names (the REAL tool's arguments). A
    call that asks for a ROLE instead of a member (v1.395.0) is named
    ``role:<id>`` until the team router picks the member — never "builder",
    which it is not."""
    a = args if isinstance(args, dict) else {}
    agent = str((a.get("agent") if name == "spawn_agent" else a.get("agent_type")) or "").strip()
    role = str(a.get("role") or "").strip()
    if not agent and role:
        agent = f"role:{role}"
    return _short(agent or "builder", 80), _short(a.get("task"), TASK_CHARS)


@contextlib.contextmanager
def helper_call(parent: "HelperParent | None", call_id: str, name: str, args: Any,
                emit: "Emit | None") -> Iterator["HelperCall | None"]:
    """Arm the parent for ONE helper-starting call (the ContextVar is set in
    the coroutine that runs it — never across a generator ``yield``). Yields
    the call's :class:`HelperCall` (None when there is no parent); its row is
    said at once ("starting"), and its follower is stopped on the way out."""
    if parent is None:
        yield None
        return
    agent, task = call_target(name, args)
    call = HelperCall(call_id=str(call_id or ""), agent=agent, task=task, emit=emit,
                      session_id=parent.session_id)
    armed = replace(parent, call=call)
    call.parent = armed
    token = HELPER_PARENT.set(armed)
    call.say()
    try:
        yield call
    finally:
        try:
            HELPER_PARENT.reset(token)
        except (ValueError, LookupError):
            HELPER_PARENT.set(None)
        call.close()


#: How much of the router's reason a row carries.
REASON_CHARS = 300


def note_route(parent: "HelperParent | None", *, role: str = "", reason: str = "") -> None:
    """The TEAM ROUTER decided (v1.395.0): the row says the role it was asked
    for and why this member took it — or why nothing started. Said at once
    (before the helper's session exists), and on every later frame of the
    row. A no-op outside a coding session."""
    if parent is None or parent.call is None:
        return
    call = parent.call
    if role:
        call.role = _short(role, 20)
    if reason:
        call.reason = _short(reason, REASON_CHARS)
    call.say()


def team_route_kwargs(parent: "HelperParent | None", role: str, project_team: Any) -> dict[str, Any]:
    """The team router's ``team`` for one role piece: a coding session's team
    (v1.403.0 — its workers for code / test / read, its reviewer for a review,
    named "this session's team"), else a project mission's team as before.

    v1.403.0 review: a WORKER piece may go to any of the session's workers,
    whatever roles it holds (``team_takes_role``) — the user picked them as
    workers, and a refusal of a test or read piece on a code-only team used
    to UNLOCK the lead. A review still goes only to a member holding the
    review role."""
    team = getattr(parent, "team", None) if parent is not None else None
    if team is None:
        return {"team": project_team}
    from .team import TEAM_WORDS, WORKER_ROLES

    out: dict[str, Any] = {"team": team.route_names(role), "team_words": TEAM_WORDS}
    if role in WORKER_ROLES:
        out["team_takes_role"] = True
    return out


def team_role_refusal(parent: "HelperParent | None", role: str) -> Any:
    """A role piece a coding session's team cannot route at all — a review
    when the team has no reviewer — as the door's ``ToolResult``, else None
    (an empty team list would let the router ask EVERY member)."""
    team = getattr(parent, "team", None) if parent is not None else None
    if team is None or team.route_names(role):
        return None
    from ..tools.base import ToolResult
    from .team import NO_REVIEWER_REFUSAL

    note_route(parent, role=role, reason="not started: this session's team has no reviewer")
    return ToolResult(ok=False, output="", error=NO_REVIEWER_REFUSAL,
                      data={"role": role, "reason": "not started: this session's team has no reviewer",
                            "state": "not_started", "waited": False})


def note_team_piece(parent: "HelperParent | None", target: str) -> None:
    """A piece really started on ``target`` (a roster name): counted on the
    coding session's team record when it is one of its workers (v1.403.0)."""
    team = getattr(parent, "team", None) if parent is not None else None
    if team is not None:
        try:
            team.note_piece(target)
        except Exception:  # noqa: BLE001 — a record never breaks a delegation
            pass


def child_started(parent: "HelperParent | None", platform: Any, child_session_id: str,
                  agent: str = "", task: str = "") -> None:
    """Called by ``delegate`` / ``spawn_agent`` once the helper's session
    exists: its row starts following it. A no-op outside a harness turn."""
    if parent is None or parent.call is None:
        return
    parent.call.attach(platform, child_session_id, agent, task)


# --------------------------------------------------------------------------- #
# Stop: a helper call raced against the turn's Stop
# --------------------------------------------------------------------------- #


#: v1.401.0: the ``data["state"]`` of a helper call that ran past its
#: ``deadline_s`` (:func:`invoke_helper`) and was cancelled.
TIMED_OUT_STATE = "timed_out"


async def _bounded(aw: Any, deadline_s: "float | None") -> Any:
    """``aw`` within ``deadline_s`` seconds (None: no bound). Past it the
    call is CANCELLED — the cancel reaches the helper's run, as Stop's does —
    and an ordinary failed result says so (``data.state`` :data:`TIMED_OUT_STATE`).
    Only the bound's own expiry earns that answer: a ``TimeoutError`` the call
    raised itself keeps its own words (the v1.228.0 rule)."""
    if not deadline_s:
        return await aw
    try:
        async with asyncio.timeout(float(deadline_s)) as cm:
            return await aw
    except TimeoutError:
        if not cm.expired():
            raise
        from ..tools.base import ToolResult

        mins = max(1, round(float(deadline_s) / 60))
        return ToolResult(
            ok=False, output="",
            error=f"the helper ran past its time limit ({mins} min) and was stopped",
            data={"state": TIMED_OUT_STATE},
        )


async def invoke_helper(parent: "HelperParent | None", call_id: str, name: str, args: Any,
                        invoke: "Callable[[], Any]", *, emit: "Emit | None" = None,
                        handle: Any = None, deadline_s: "float | None" = None) -> tuple[Any, bool]:
    """Run ONE helper-starting call of a harness turn: arm its parent (and
    its row), race it against the turn's Stop, and say how it ended.
    ``invoke`` makes the ``registry.invoke`` awaitable (made INSIDE the
    scope). ``(result, stopped)``; an ordinary failure is the result's own,
    a crash is re-raised after the row says so. MIRROR NOTE (lock-step): both
    chat lanes call this for ``delegate`` / ``spawn_agent`` in a harness turn
    (the POST lane with no row and no Stop handle).

    v1.401.0: ``deadline_s`` bounds the call (:func:`_bounded`) — the finish
    check's team review passes one, so a slow reviewer can never hold the
    reply; every other helper call passes None and is exactly as before."""
    with helper_call(parent, call_id, name, args, emit) as call:
        try:
            result, stopped = await until_stopped(_bounded(invoke(), deadline_s), handle)
        except Exception as exc:  # noqa: BLE001 — said on the row, then re-raised
            if call is not None:
                call.close()
                call.finish(None, crash=f"{type(exc).__name__}: {exc}")
            raise
        if call is not None:
            # Every step frame still queued goes out BEFORE the last frame.
            call.close()
            # v1.390.0 (the Timeline): the last frame says what it used — read
            # only when a row listens (the POST lane draws none); a cancel
            # landing on the read still sends the last frame.
            usage = None
            try:
                if call.emit is not None:
                    usage = await call.read_usage(result)
            finally:
                call.finish(result, stopped=stopped, usage=usage)
        return result, stopped


async def until_stopped(aw: Any, handle: Any) -> tuple[Any, bool]:
    """Await ``aw`` (a helper-starting ``registry.invoke``) unless the turn is
    STOPPED first: then the call is CANCELLED — the cancel reaches the
    helper's run (its own task, awaited inside the tool), whose ``finally``
    ends its programs, and the door settles it CANCELLED — and ``(None,
    True)`` is returned once it has unwound. ``(result, False)`` otherwise.
    No handle (an unnamed turn): awaited as before."""
    if handle is None:
        return await aw, False
    task = asyncio.ensure_future(aw)
    stop = asyncio.ensure_future(handle.wait_stopped())
    try:
        await asyncio.wait({task, stop}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        task.cancel()
        stop.cancel()
        raise
    stop.cancel()
    if task.done():
        return task.result(), False
    task.cancel()
    await asyncio.wait({task})
    if not task.cancelled():
        with contextlib.suppress(BaseException):
            task.exception()
    return None, True


__all__ = [
    "AGENT_TOOLS", "ASK_POLL_S", "FINAL_STATES", "HELPER_EVENT", "HELPER_ORIGIN",
    "REVIEW_ONLY_OPTION", "REVIEW_TOOLS", "TIMED_OUT_STATE",
    "HELPER_PARENT", "HELPER_TOOLS", "HelperCall", "HelperParent", "MAX_HELPERS", "for_member",
    "NOT_FOR_HELPERS", "READ_ONLY_TYPES",
    "REASON_CHARS", "SKILL_TOOLS", "STOPPED_RESULT", "asking", "call_target", "child_started",
    "helper_call", "note_route",
    "helper_definition",
    "helper_slot", "invoke_helper", "live_helpers", "parent_for", "pinned", "report_text",
    "stop_helpers", "target_refusal", "until_stopped",
]
