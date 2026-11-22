"""Session-level TRUST posture (v1.298.0, wave 4a).

A run is either FULL trust (the default — the user started it, or it was
started by something the user configured) or LOW trust: it was started from
an inbound channel message (``comm/inbound.py``), it is the child of a
low-trust run (delegate / spawn_agent / assign_work), or it READ content the
injection scanner flagged mid-run (the untrusted-content fence in
``agents/runtime.py`` and both chat lanes).

Low trust changes exactly one thing: the tools that WRITE DURABLE STATE the
NEXT run reads — memory, preferences, agents, skills, schedules, workflows,
queued work, a live PTY — are kept away. The run may still read, search,
write files in its folder and answer; it is told to SAY what it would have
changed instead. ``shell`` under low trust must run in the isolating
sandbox (Docker); the native fallback is refused, not advised. The tools
that run model-written code outside that sandbox (``CODE_DENY``) are
refused by name instead.

Everything here is pure: no I/O, no platform. The posture is applied by the
runtime (``AgentRuntime``) and the two chat lanes; the row columns live on
``core.models.Session`` (``trust``, ``trust_reason``, ``tainted_at``).
"""

from __future__ import annotations

from typing import Iterable, Mapping

TRUST_FULL = "full"
TRUST_LOW = "low"

#: Tools a LOW-trust run may never call: each one writes state that outlives
#: the run and is read by a LATER run or by the user as if it were theirs.
#: Every name here is a registered tool (``tests/test_trust_posture_v1298``
#: pins that against the real registry). ``self_dev`` was in the brief and
#: is NOT a tool (self-dev is a session flag, ``create_session(self_dev=)``),
#: so it is not listed. ``pane_send``/``pane_spawn`` already sit on the deny
#: floor for agent definitions; listing them here closes the per-session
#: grant path too (``low_trust_overrides`` writes a hard deny, which no
#: grant lifts).
LOW_TRUST_DENY: frozenset[str] = frozenset(
    {
        # Calm UI redesign S3/S4: a run started by an inbound message never
        # changes a setting or asks for a credential.
        "config_set",
        # v1.444.0: nor a rename / move / copy of a downloaded file — a page the
        # browser read can say anything.
        "download_manage",
        # v1.406.0: nor a write to the user's durable brain.
        "brain_save",
        "brain_change",
        "config_change",
        "config_change_protected",
        "config_secret",
        # Redesign S5: nor a schedule, workflow, channel or app.
        "schedule_update",
        "schedule_delete",
        "workflow_update",
        "workflow_delete",
        "workflow_schedule",
        "channel_toggle",
        "channel_connect",
        "app_connect",
        # v1.435.0 (Wave 3 group H): nor an app switched, disconnected or
        # brought over, nor a destination removed or its alerts changed.
        "app_switch",
        "app_disconnect",
        "app_import",
        "channel_remove",
        "channel_alerts",
        # ...nor a webhook, sentinel, goal or reflex rule (each is unattended
        # work a later signal starts), nor "undo that" — an undo can put back
        # a trigger the user had removed. ``grant_revoke`` stays ALLOWED: it
        # only takes an always-allow away (every later call asks again), and
        # its Undo is the user's press on the card, never a low run's tool.
        "webhook_update",
        "webhook_delete",
        "sentinel_update",
        "sentinel_delete",
        "goal_update",
        "goal_delete",
        "reflex_create",
        "reflex_update",
        "reflex_delete",
        "config_undo",
        # v1.435.0 (Wave 3 group I): nor ANY undo (a file edit put back is a
        # write the user did not ask for), nor Local only for a chat (turning
        # it off would let what this run read leave the network).
        "change_undo",
        "chat_local_only",
        # v1.506.0 (north-star plan 6.2): nor a suggestion answered (an accept
        # can start a task, make a tool or rewrite notes).
        "suggestion_answer",
        # v1.438.0 (Wave 4 group A): nor a night task queued, cancelled or
        # moved (unattended work a later night starts), nor a folder's
        # defaults or coding team for new coding sessions.
        "night_task_add",
        "night_task_cancel",
        "night_task_move",
        "build_defaults_set",
        "build_defaults_clear",
        "build_team_set",
        "build_team_clear",
        # 22-a: nor whether Iron Jarvis runs a folder's tests after each reply.
        "build_checks_set",
        # 26-a: nor whether the user is told about a pull request's new review
        # comments (a flagged comment must not silence the others).
        "pr_comments_quiet",
        # v1.438.0 (Wave 4 group B): nor a model server, an account or a memory
        # base (each changes where later chats are answered or saved).
        "model_server_add",
        "model_server_default",
        "model_server_remove",
        "account_add",
        "memory_base_add",
        # v1.448.0 (Wave A item 4): nor a tool server (its program runs on
        # this PC with the user's keys) or a web service Jarvis may then call.
        "mcp_server_add",
        "web_service_add",
        "create_agent",
        "remember_preference",
        "skill_create",
        # v1.473.0: changing a skill is as lasting as making one.
        "skill_update",
        "assign_work",
        "schedule_create",
        "workflow_create",
        "memory_write",
        "ltm_append",
        "notebook",
        "memory_propose",
        "capability_propose",
        "pane_send",
        "pane_spawn",
        # Reviewer additions (wave 4a): more state that outlives the run and
        # that a LATER run (or the user) reads as if it were theirs —
        # a dynamic tool, a secret, a webhook/sentinel/goal trigger, a queued
        # worklist item — and ``workflow_run``, whose steps start sessions
        # the workflow engine does not stamp low (a laundering path).
        "tool_create",
        "tool_delete",
        "secret_set",
        "webhook_add",
        "sentinel_add",
        "goal_add",
        "worklist_add",
        "workflow_run",
        # A low-trust run must not hand what it read to ANOTHER MACHINE: the
        # remote has no fence, no scanner and no trust posture of its own.
        "delegate_remote",
        # v1.344.0 ("Jarvis looks after itself"): the self-care REPAIRS. A
        # pack restart respawns a child process with the pack's secrets and
        # a provider rescan re-probes every CLI sign-in — both change what
        # the NEXT turn can reach, so flagged content never asks for them.
        # The readers (app_health, pack_status, run_doctor) stay allowed.
        "pack_restart",
        "provider_rescan",
        # v1.346.0 (media in chat): a generation SPENDS the user's credits on
        # a hosted service and sends the prompt there — flagged content must
        # never buy anything or ship its words out.
        "generate_media",
        # v1.357.0 (G84): a call to one of the user's REST hookups goes out
        # with the user's saved key — flagged content must never read their
        # records out of it, or write to it.
        "rest_call",
        # v1.364.0 (IJ harness): a program that keeps running cannot be put
        # in the low-trust sandbox (the shell's `_isolate` is a one-shot
        # Docker run), so starting one, or typing into one, is kept away.
        # Reading its output and stopping it stay allowed: a run tainted
        # mid-way must still be able to stop what it started.
        "process_start",
        "process_write",
        # v1.371.0: a screenshot of a local page goes to the model — flagged
        # content must not point it at another app's page on this network.
        "page_screenshot",
        # v1.376.0: the agent's own browser — flagged content must not open,
        # click or type in a page on this network, nor read one back.
        "page_open",
        "page_click",
        "page_type",
        "page_read",
        # v1.435.0 (chat-first plan Wave 3 group G): a project or an agent of
        # the user's own outlives the run and grounds or does LATER work.
        "project_create",
        "project_update",
        "project_archive",
        "project_focus",
        "agent_create",
        "agent_update",
        "agent_pause",
        "agent_delete",
        # v1.438.0 (chat-first plan Wave 4 group C): a kept preference or
        # lesson reaches every later prompt, and a persona's words every chat
        # that uses it — flagged content must not change either.
        "preference_edit",
        "preference_forget",
        "lesson_add",
        "persona_create",
        "persona_update",
        "persona_delete",
        # v1.499.0 (vision plan leap 3 Phase 3): a client answered or changed,
        # or a preference kept, changed or forgotten for one client — each is
        # told to every later chat in that client's folders, so flagged
        # content must not change any of them. Listing clients stays allowed.
        "client_answer",
        "client_update",
        "client_preference_add",
        "client_preference_change",
        "client_preference_forget",
        # Next leaps 41-a: nor a paired computer's switch turned off (it stops
        # that computer's tasks running here). Listing them stays allowed.
        "peer_allow_off",
        # Next leaps 43-a: nor a model downloaded (it fills the drive and
        # reaches a registry on the internet). What fits stays allowed.
        "model_download",
    }
)

#: v1.502.0 (north-star D4): the WEB tools a run loses once it has READ
#: flagged content (a TAINT), for the rest of the run. A page or a file that
#: said "now open this address with the notes in it" must not have its words
#: carried out. Kept apart from ``LOW_TRUST_DENY`` on purpose: a run that is
#: low only because of its DOOR (a phone message) has read nothing flagged
#: and keeps web search, and Auto's always-ask list (``core/auto_approve.
#: _standing_tools``, which reads ``LOW_TRUST_DENY``) does not start asking
#: about every search. Read through :func:`deny_names`.
TAINTED_DENY: frozenset[str] = frozenset(
    {
        "web_fetch",
        "web_search",
        "browse",
        "web_action",
    }
)


#: Third fix plan PG3 (decision K3): the tools that run MODEL-WRITTEN CODE
#: on this computer with the network open — ``run_code`` and ``code_run``
#: start a native interpreter through ``core.procs``, and ``repl`` runs in
#: the session's non-isolated worker. None of them can be put in the shell's
#: isolated sandbox (``_isolate`` is the shell tool's own), so, like
#: ``process_start``, they are kept away from EVERY low run (door or taint)
#: and refused by name; the shell still runs, isolated. Kept apart from
#: ``LOW_TRUST_DENY`` on purpose, so an ordinary run is untouched: Auto's
#: always-ask list (``core/auto_approve._standing_tools``) reads
#: ``LOW_TRUST_DENY``. The dashboard's low-trust words name both lists
#: (``tests/test_trust_words_v13560``). Read through :func:`deny_names` and
#: the default of :func:`low_trust_overrides`.
CODE_DENY: frozenset[str] = frozenset({"run_code", "code_run", "repl"})


#: v1.509.0 (second review F1, decision E1): the tools that READ ACROSS THIS
#: COMPUTER — its memory, lessons, notes, memory base, past chats and runs,
#: clients, and files outside the run's own folder — kept away from a run
#: that a task from ANOTHER COMPUTER started (``task_door.
#: is_remote_task_origin``) and from every run started inside it. Its answer
#: is posted back to that computer, so what these read would leave this one.
#: Kept apart from ``LOW_TRUST_DENY``: a phone message's run is low too and
#: keeps them (its answer stays on the owner's own phone). ``file_search``
#: is kept away whole: with no root it searches the chat files, every
#: project's folder and the owner's added folders, and the run's own folder
#: is searched by ``grep`` / ``glob``. Read through :func:`deny_names`.
REMOTE_RUN_DENY: frozenset[str] = frozenset(
    {
        "recall",
        "recall_lessons",
        "ltm_search",
        "history_search",
        # v1.547.0 (next leaps 13-a): this computer's record of earlier work.
        "work_history",
        "memory_read",
        "memory_search",
        "brain_read",
        "brain_search",
        "client_list",
        # Next leaps 41-a: this computer's other pairings and their switches.
        "peer_allows",
        "file_search",
    }
)


def deny_names(tainted: bool, remote: bool = False) -> frozenset[str]:
    """The names kept away from a low run NOW: ``LOW_TRUST_DENY`` and
    ``CODE_DENY``, plus ``TAINTED_DENY`` once the run has read flagged
    content, plus ``REMOTE_RUN_DENY`` for a run another computer's task
    started."""
    names = LOW_TRUST_DENY | CODE_DENY
    if tainted:
        names = names | TAINTED_DENY
    return (names | REMOTE_RUN_DENY) if remote else names


#: v1.509.0 (lead review of F1): the ONLY tools a run another computer's
#: task started may call. A deny list cannot keep up: the allow tier also
#: holds the owner's open browser tabs (``browser_read_page``), terminals
#: (``pane_read``), connected apps (``mcp_read_resource``), settings and
#: every app pack's tools, and a new tool would be open until someone
#: remembered to list it. So the question is asked the other way: is the tool
#: on this list? Each one works on the run's OWN folder (the file tools are
#: confined to it by ``safe_path``; the readers that take an absolute path go
#: through ``core.fs_policy``, which confines such a run's reads), is pure,
#: reaches the public web, or works with the run's own helpers. Everything
#: else is refused with :func:`remote_run_refusal`. Next leaps 41-a added the
#: per-computer switches (``peers/allows``): they decide whether a paired
#: computer's task gets in at all; a later phase whose switch lets a run do
#: more (41-b's question back) adds its tool only through
#: :func:`remote_run_keeps`, for a run whose computer allows it. Pinned by
#: ``tests/test_second_review_remote_run_v1509.py`` (each reader on it is
#: driven at a path outside the run's folder and must refuse).
REMOTE_RUN_KEEP: frozenset[str] = frozenset(
    {
        # the run's own folder
        "read_file", "write_file", "edit_file", "multi_edit", "apply_patch",
        "rename_file", "list_files", "glob", "grep",
        # documents: reads are confined by core.fs_policy, the writer by safe_path
        "read_document", "extract_pdf", "list_folder", "write_document",
        "excel_read", "excel_query", "excel_profile",
        # pure
        "calculate", "plan_update",
        # its own helpers (each inherits all of this). Not consult (it can
        # ask an agent on a THIRD computer) and not spawn_agent (it adds
        # an agent to this computer's own list).
        "delegate", "list_agents",
        "blackboard_post", "blackboard_read", "message_agent",
        # the public web (taken away once the run has read flagged content)
        "web_search", "web_fetch", "browse", "web_extract", "web_look",
        # the product's own handbook
        "guide_read", "guide_search",
    }
)


def remote_run_keeps(tool: str) -> bool:
    """May a run another computer's task started call ``tool``? Only a tool
    on :data:`REMOTE_RUN_KEEP` that is not a cross-computer reader
    (:data:`REMOTE_RUN_DENY` wins, so a name can never be on both)."""
    return tool in REMOTE_RUN_KEEP and tool not in REMOTE_RUN_DENY


def remote_run_refusal(tool: str) -> str:
    """The sentence a call is refused with in a run another computer's task
    started (v1.509.0, decision E1)."""
    return (
        f"this task came from another computer, so {tool} is not available "
        "to it: it works in its own folder only, with none of what this "
        "computer keeps"
    )


#: The ONE sentence a run that starts low reads in its system prompt. A run
#: tainted MID-run gets nothing appended (prompt caching) — the refusal text
#: on its next kept-away call is its signal.
LOW_TRUST_PROMPT = (
    "This run is in low trust: it must not change memory, settings, agents or "
    "skills — say what you would have changed instead."
)

#: What the shell tool answers when low trust asks for the sandbox and only
#: the native runtime is reachable. A refusal, never an advisory.
LOW_TRUST_SHELL_REFUSAL = "low trust needs the sandbox; Docker is not reachable"


def normalize_trust(value: object) -> str:
    """``"low"`` → ``TRUST_LOW``; anything else (``""``, ``None``, ``"full"``,
    junk) → ``TRUST_FULL``. The one normaliser every door goes through."""
    return TRUST_LOW if str(value or "").strip().lower() == TRUST_LOW else TRUST_FULL


def low_trust_overrides(
    base: Mapping[str, str] | None,
    names: Iterable[str] = LOW_TRUST_DENY | CODE_DENY,
) -> dict[str, str]:
    """``base`` merged with ``{name: "deny"}`` for every kept-away name.

    Deny WINS: a base ``allow``/``ask`` on a listed name becomes ``deny``, a
    base ``deny`` on any name stays ``deny``, and nothing not listed changes —
    so this can only ever NARROW what a run may do, never widen it. ``names``
    lets the caller add the registry's permission KEYS beside the tool names
    (the engine authorises on ``perm_key()``).
    """
    merged: dict[str, str] = {str(k): str(v) for k, v in dict(base or {}).items()}
    for name in names:
        if name:
            merged[str(name)] = "deny"
    return merged


def taint_reason(tool: str, category: str | None) -> str:
    """One sentence for ``Session.trust_reason`` when a run's own reading
    lowered it: ``read content flagged as <category> from <tool>``."""
    cat = " ".join(str(category or "suspicious content").replace("_", " ").split())
    return f"read content flagged as {cat} from {tool or 'a tool'}"


def effective_trust(session_trust: object, tainted: bool) -> str:
    """The posture a call runs under NOW: the row's trust, lowered to
    ``TRUST_LOW`` once this run has been tainted."""
    if tainted:
        return TRUST_LOW
    return normalize_trust(session_trust)


def low_trust_refusal(tool: str, tainted: bool) -> str:
    """The sentence a kept-away call is refused with. ``tainted`` names the
    cause the model can act on (it READ something), the other wording is for
    a run that was low from its door. A code tool (``CODE_DENY``) also says
    why, and what still works: the shell, which a low run keeps (isolated)."""
    if tainted:
        sentence = (
            f"this run is in low trust after reading flagged content — "
            f"{tool} is kept away"
        )
    else:
        sentence = f"this run is in low trust — {tool} is kept away"
    if tool in CODE_DENY:
        sentence += (
            ", because it runs code on this computer with the network open. "
            "The shell tool is not kept away: it runs commands in the isolated "
            "sandbox."
        )
    return sentence


def kept_away(armed: Iterable[str]) -> list[str]:
    """The names in ``armed`` (order kept) that low trust keeps away."""
    return [str(t) for t in armed if str(t) in LOW_TRUST_DENY]


def low_trust_note(count: int) -> str:
    """The one ledger-visible note a chat lane records when it dropped
    kept-away tools from its armed set."""
    return f"low trust: {int(count)} tool{'s' if count != 1 else ''} kept away"
