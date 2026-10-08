"""Request models for the daemon API (moved out of daemon/app.py).

Pure pydantic request/whitelist declarations shared by app.py and the
routes/ domain modules.
"""

from __future__ import annotations

import re

from typing import Any

from pydantic import BaseModel, Field, field_validator

from ..core.models import SESSION_MAX_STEPS_MAX, SESSION_MAX_STEPS_MIN

#: What an ``origin`` tag may look like (v1.166.0): the TX-01 provenance values
#: ("job:agents", "schedule:<name>", "self_dev", …) all fit, and nothing that
#: could smuggle markup/control characters into the audit timeline does.
_ORIGIN_RE = re.compile(r"[A-Za-z0-9:_\-. ]{1,64}")


def _clean_origin(value: str | None) -> str | None:
    r"""Normalize an ``origin`` tag: strip; blank -> None (unattributed);
    anything outside ``[A-Za-z0-9:_\-. ]`` or over 64 chars is a 422, never
    silently truncated/laundered."""
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if not _ORIGIN_RE.fullmatch(value):
        raise ValueError(
            "origin must be 1-64 characters of letters, digits, spaces, "
            "or ':' '_' '-' '.'"
        )
    return value


def _clean_max_steps(value: Any) -> int | None:
    """Validate a per-session step budget (v1.174.0, Contract 4).

    ``None`` means "use ``config.max_agent_steps``" — the absent-param default,
    byte-identical to pre-v1.174.0 behavior. Anything outside
    ``SESSION_MAX_STEPS_MIN..MAX`` is a 422, deliberately NOT clamped: a job
    posted with ``max_steps: 1000`` that quietly runs 200 and then reports
    "reached max steps" is a run measured against a budget nobody set.

    Runs in ``mode="before"`` and does its OWN type narrowing, because pydantic
    would otherwise have already coerced the interesting cases away: ``bool``
    is an ``int`` subclass, so a JSON ``true`` arrives at an "after" validator
    as a 1-step budget that strands every run at its first tool call. A
    fractional number is a 422 too — a request asking for 2.5 steps is a
    request nobody can honestly satisfy.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("max_steps must be a whole number, not a boolean")
    if isinstance(value, int):
        steps = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError("max_steps must be a whole number")
        steps = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text.isdigit():
            raise ValueError("max_steps must be a whole number")
        steps = int(text)
    else:
        raise ValueError("max_steps must be a whole number")
    if steps < SESSION_MAX_STEPS_MIN or steps > SESSION_MAX_STEPS_MAX:
        raise ValueError(
            f"max_steps must be between {SESSION_MAX_STEPS_MIN} and "
            f"{SESSION_MAX_STEPS_MAX} (omit it to use the configured default)"
        )
    return steps


class SessionCreate(BaseModel):
    task: str
    agent_type: str = "builder"
    provider: str | None = None
    model: str | None = None
    wait: bool = True
    # Opt-in self-development: run a Maintainer on a worktree of Iron Jarvis's
    # OWN source (gated by config.self_dev_enabled; review-gated, never auto-merge).
    self_dev: bool = False
    # Context spine: tag into a project ("" = the ACTIVE project, if any).
    project_id: str = ""
    # Per-session bundled tool grant (perm_keys) the user approved up front —
    # "ask" tools in this list run without re-prompting for THIS session only.
    allow_tools: list[str] = []
    # THE POSTURE RIDES THE ESCALATION (v1.232.0, audit A7). The chat's
    # approval dropdown (``approve_for_me`` | ``always_ask`` | ``yolo``):
    # ``approve_for_me`` lets the tools armed at escalation (``allow_tools``)
    # run without a pause and asks for every other ask-tier call;
    # ``always_ask`` asks for every ask-tier call, the armed list
    # notwithstanding; ``yolo`` is NEVER inherited — it lands as
    # ``approve_for_me`` (``runtime.inherited_approval_mode``). "" = default.
    approval_mode: str = ""
    # THE FOLDER THE WORK IS ABOUT (v1.189.0). When set (and valid — see the
    # route's guard), the session runs DIRECTLY in this folder instead of a
    # scratch workspace, exactly like a project-folder task. This is the field
    # a CHAT ESCALATION rides: chat's own tools operate in the grounded folder,
    # and the session the turn escalates into must not lose it — measured
    # (session_a63b0a4f): 27 tax documents, rename_file refusing every path as
    # outside a scratch workspace the user had never heard of, and the agent
    # filing a capability request for a tool it already had.
    workspace_root: str = ""
    # TX-01 provenance the CALLER asserts ("job:agents", …). None/blank =
    # unattributed; validated (see _clean_origin) so the audit timeline stays
    # clean.
    origin: str | None = None
    # Per-session STEP BUDGET (v1.174.0, Contract 4). None/absent = the
    # configured ``max_agent_steps`` — today's behavior for every existing
    # caller. A big job ("rename all 26 files in this folder") can ask for the
    # room it needs without raising the global default for every small task.
    max_steps: int | None = None

    @field_validator("origin")
    @classmethod
    def _validate_origin(cls, v: str | None) -> str | None:
        return _clean_origin(v)

    @field_validator("max_steps", mode="before")
    @classmethod
    def _validate_max_steps(cls, v: Any) -> int | None:
        return _clean_max_steps(v)


class MissionCreate(BaseModel):
    """One objective for the whole team (v1.307.0, the Agents page's mission
    door). Becomes a SUPERVISOR session stamped ``origin="job:mission"`` (an
    ATTENDED origin — its asks wait for the user) with the run option
    ``deliverable: true``, so the coordinator's final message is the work
    product itself."""

    objective: str
    project_id: str = ""
    workspace_root: str = ""
    provider: str | None = None
    model: str | None = None
    allow_tools: list[str] = []
    approval_mode: str = ""
    max_steps: int | None = None

    @field_validator("max_steps", mode="before")
    @classmethod
    def _validate_max_steps(cls, v: Any) -> int | None:
        return _clean_max_steps(v)


class DocEnhanceBody(BaseModel):
    """AI pass over a document draft BEFORE creation: better name + content."""

    filename: str = ""
    content: str = ""
    provider: str = ""
    model: str = ""


class LessonCreateBody(BaseModel):
    text: str
    scope: str = "user"


class LiveDocCreate(BaseModel):
    """A living document: prompt + format + optional refresh schedule."""

    name: str
    prompt: str
    format: str = "md"  # md | html | docx | pdf
    cron: str | None = None  # e.g. "0 7 * * 1" — omit for manual-only
    interval_seconds: int | None = None
    provider: str = ""
    model: str = ""


class SkillApplyBody(BaseModel):
    """Use a skill directly: the skill's playbook + this request, one shot."""

    request: str
    provider: str = ""
    model: str = ""


class ChatMessageBody(BaseModel):
    role: str  # user | assistant
    content: str


class TurnSteerBody(BaseModel):
    """``POST /chat/turns/{turn_id}/steer`` (v1.278.0): one note for a running turn."""

    text: str


class ChatBody(BaseModel):
    """A DIRECT conversational turn — frontier-chat style: full history in,
    one reply out. No agent loop, no workspace; fast."""

    messages: list[ChatMessageBody]
    provider: str = ""
    model: str = ""
    #: A builtin persona name (see /chat/personas) or FREE TEXT used verbatim
    #: as the persona ("" = the configured ``default_persona`` setting, which
    #: itself defaults to the built-in assistant).
    persona: str = ""
    #: Workspace/absolute paths of uploaded files to ground this turn on.
    attachments: list[str] = []
    #: THE CONVERSATION'S FILES (v1.251.0, C-01) — everything this chat has
    #: been given or has made, as the Files rail already lists it, MINUS this
    #: turn's own ``attachments``. The user's report: attach a return, ask for
    #: a summary, then say "now turn that into a memo" — the second turn had
    #: no file at all, because history is sent as ``{role, content}`` text and
    #: only the CURRENT message's attachments ride. The daemon names these
    #: files (and their absolute paths) so "it" / "that return" resolve, and
    #: counts them when arming tools so "edit it" gets the editing verbs.
    #: Their TEXT is not re-extracted — naming them is what was missing.
    thread_files: list[str] = []
    #: A skill to invoke this turn (the "/" picker) — instructions injected.
    skill: str = ""
    #: Tools the user ARMED via the "+" menu (registry names, max 6). When set,
    #: the chat runs a small tool loop (up to 4 rounds) with JUST these tools.
    tools: list[str] = []
    #: Ground THIS turn in a SPECIFIC project (instructions + knowledge + brief)
    #: — an in-project conversation, independent of the globally-active project.
    #: "" = NO project grounding at all: the main chat is project-agnostic and
    #: the globally-active project never leaks in (it has never fallen back).
    project_id: str = ""
    #: The chat's WORKSPACE folder (absolute). When set + allowed, armed file
    #: tools run there so created/edited files land in the folder the user is
    #: browsing (the Build-like workspace). "" = project root / uploads default.
    workspace_dir: str = ""
    #: Seamless arming: let the daemon read the request and fill the free tool
    #: slots (under the same 6-tool cap) from a curated safe set — files,
    #: documents, web retrieval, local image tools. Explicit ``tools`` always
    #: come first; the reply's tools_used stays the honest record of what RAN.
    auto_tools: bool = False
    #: Per-conversation permission POSTURE for the mid-turn ask (v1.188.0):
    #: "always_ask" | "approve_for_me" | "yolo". "" / unknown = approve_for_me
    #: (v1.187.0's behaviour). Honoured by the STREAM lane only — the headless
    #: lane has nobody present to answer a card, and yolo from a caller that
    #: never showed the user a dropdown would be a grant nobody made.
    approval_mode: str = ""
    #: Connectors the user TOGGLED ON for this conversation (the "+" menu).
    #: An MCP connector arms its whole tool group (additive to ``tools``,
    #: separately bounded); a memory connector (an LTM source, e.g. an
    #: MCP-served brain) grounds the turn with that store's top hits.
    connectors: list[str] = []
    #: WHICH Build pane this turn came from (v1.236.0) — the pane's stable
    #: `term_*` id, or "" for a surface that is not a pane (the main chat page,
    #: the phone lane, an MCP caller).
    #:
    #: THIS IS THE MISSING LINK a pane-scoped rule needs. Until now a chat
    #: request carried ``workspace_dir`` — the pane's FOLDER — and nothing
    #: identifying the pane itself, so a per-pane capability had nothing to key
    #: on: two panes open on the same folder are indistinguishable by
    #: ``workspace_dir``, and a folder is not an identity anyway (the user can
    #: repoint a pane). ``_filter_browser_tools`` reads it (plan 11.2 gate 2)
    #: and ``_browser_section`` passes it on.
    #:
    #: OPTIONAL AND DEFAULTED, deliberately: every existing caller — the chat
    #: page, the phone lane, `/agents`, an older packaged dashboard talking to a
    #: newer daemon — keeps today's behaviour byte for byte. Absent, the turn is
    #: treated as a pane-LESS surface where gate 2 does not apply and the global
    #: gate still does. It is never a permission by itself: an unknown or
    #: forged id resolves to no pane, and no gate anywhere WIDENS on it.
    pane_id: str = ""
    #: A CALLER-CHOSEN name for this streaming turn (v1.241.0), so it can be
    #: stopped from somewhere other than the connection running it:
    #: ``POST /chat/turns/{turn_id}/stop``.
    #:
    #: THE SILENT FAILURE THIS PREVENTS: a Stop button that does nothing.
    #: Stopping a turn was CONNECTION-BOUND — drop the HTTP response and
    #: Starlette cancels the generator — which is unavailable to any caller
    #: that has no connection to drop (a browser side panel asks the daemon to
    #: run the turn; docs/BROWSER-SIDEBAR-PLAN.md §4).
    #:
    #: OPTIONAL AND DEFAULTED, deliberately, exactly like ``pane_id``: absent,
    #: NOTHING registers and the turn behaves byte for byte as it did before —
    #: the dashboard's existing stop-by-disconnect is untouched. The daemon
    #: never mints one, because an id the caller did not choose has nobody to
    #: use it. It is not a credential and grants nothing: the stop route takes
    #: the ordinary install bearer like every other ``/chat/*`` route, and an
    #: unknown or finished id is a 404, never a silent success.
    turn_id: str | None = None
    #: v1.263.0: the reasoning level the user picked for this turn — "low" /
    #: "medium" / "high", or "" for the model's own default. Applied only when
    #: the serving model offers one (``providers.reasoning``); the route
    #: object reports what was applied.
    reasoning: str = ""
    #: v1.312.0: the tools the user allowed "for this conversation" on an
    #: approval card — the page keeps them in the thread setup and sends them
    #: on every turn. A GRANT, never an arming: a name here is honoured for
    #: THIS turn only when the turn armed or ask-armed it by some other path
    #: (``chat_turn._conversation_grants``), so it skips the card and nothing
    #: more. Uncapped on purpose — the 6-tool limit is about what the model is
    #: SHOWN, and a grant shows the model nothing. Before this field the only
    #: way to keep that answer was to arm the tool, which did nothing at all
    #: once six tools were already in the composer.
    granted_tools: list[str] = []


class ChatCompactBody(BaseModel):
    """Compact this conversation NOW because the user chose to (v1.153.0).

    The same message list a turn would post. The daemon covers everything but
    the most recent ``KEEP_RECENT`` messages, has a model write a structured
    summary, verifies every checkable claim against the transcript and the
    execution ledger, and caches the result against a hash of exactly what it
    covers — so the next ordinary turn picks it up with no further calls and no
    thread id needed.

    This is the 70% path. Past the auto threshold the same thing happens inside
    the turn without asking, because by then there is no headroom left to ask in.
    """

    messages: list[ChatMessageBody]
    provider: str = ""
    model: str = ""


class ChatRememberBody(BaseModel):
    """Commit a saved chat thread to long-term memory. ``mode`` distill = a
    faithful one-shot LLM distillation of what is worth remembering (falls
    back to a verbatim excerpt when no real model is connected — never a
    fabricated summary); full = the verbatim transcript. ``source`` targets a
    registered LTM store ("" = the default brain)."""

    mode: str = "distill"  # distill | full
    source: str = ""  # LTM source name ("" = default brain)
    provider: str = ""  # distill-mode LLM override ("" = default)
    model: str = ""


class ChatCrystallizeBody(BaseModel):
    """Turn a saved chat thread into a reusable workflow DRAFT (v1.120.0).

    The one-shot model generalizes what actually happened in the conversation
    into 2-6 ordered steps. Nothing is saved — the client renders the draft as
    a card and the user decides (suggest-don't-act)."""

    provider: str = ""  # one-shot LLM override ("" = default)
    model: str = ""


class DocumentOpenBody(BaseModel):
    """Open a document with its OS-associated app (preview panel's button)."""

    path: str


class ChatShareBody(BaseModel):
    """Render a saved chat thread for sharing. ``mode`` full = the verbatim
    transcript; compact = a faithful one-shot LLM digest. Read-only — the
    daemon returns text; nothing leaves the machine unless the user does it."""

    mode: str = "full"  # full | compact
    format: str = "markdown"  # markdown | html (self-contained page)
    provider: str = ""  # compact-mode LLM override ("" = default)
    model: str = ""


class ProjectCreate(BaseModel):
    """A context-spine project: brief + activity shared across all surfaces."""

    name: str
    brief: str = ""
    root: str = ""


class ProjectPatch(BaseModel):
    name: str | None = None
    brief: str | None = None
    root: str | None = None
    status: str | None = None  # active | archived
    instructions: str | None = None  # per-project custom instructions
    default_provider: str | None = None  # per-project default model halves
    default_model: str | None = None
    #: LTM source names this project reads from (v1.110.0). [] = every base,
    #: which is the default; naming bases NARROWS recall to them.
    memory_sources: list[str] | None = None


class ProjectKnowledgeBody(BaseModel):
    """Add a knowledge item to a project: a pasted note (``text``), or a file
    (``content_b64`` — extracted to text server-side). ``name`` labels it."""

    name: str = ""
    text: str = ""
    content_b64: str = ""
    filename: str = ""


class ContinueBody(BaseModel):
    message: str
    wait: bool = True
    # GRANTS RIDE THE CONTINUE TOO (v1.232.0, audit A6). The chat page sends
    # its armed set on the opener only; a tool granted on a card AFTER the
    # opener ("Allow for this conversation") reached no later turn, and this
    # body could not even carry it. UNIONED with the stored grant — a
    # continue can widen what run 1 was allowed, never narrow it.
    allow_tools: list[str] = []
    # The posture for the follow-up run (v1.232.0, audit A7); "" = inherit
    # the parent's. Same vocabulary and the same yolo rule as SessionCreate.
    approval_mode: str = ""
    # A RESUME, not a follow-up (v1.309.0): ``message`` is the app's own
    # "pick up where you left off" (the bell's / the mission screen's
    # Continue after a restart), so a mission's continuation keeps the
    # PARENT's display objective instead of listing that instruction as
    # what the user asked for. False = the user's own words (contract 3).
    resume: bool = False


class UploadBody(BaseModel):
    filename: str
    content_b64: str


class WorkfolderBody(BaseModel):
    """``POST /documents/workfolder`` (v1.244.0) — a no-project conversation's
    own visible folder. ``files`` are paths ``/documents/upload`` returned;
    ``title`` names a NEW folder; ``prefer`` is the folder the chat is already
    pointed at (kept when the app can work in it); ``into`` is a folder this
    route made earlier for the same conversation (later attachments join it)."""

    files: list[str] = []
    title: str = ""
    prefer: str = ""
    into: str = ""


class BatchFolderBody(BaseModel):
    """``POST /documents/batch/preview`` and ``/documents/batch`` (v1.251.0,
    C-04) — "a folder of documents becomes one summary sheet".

    ``folder`` is the folder the conversation is pointed at (absolute).
    ``instructions`` is what the sheet should cover. ``output`` is the
    deliverable format the batch tool accepts (``xlsx`` | ``docx`` | ``both``).
    ``max_files`` bounds the run; the preview reports the same bound so the
    card can state the cap BEFORE the user spends anything.

    ``workspace_dir`` is where the deliverables land — the conversation's own
    folder (v1.244.0), so the sheet appears beside the documents it summarises
    instead of in a hidden scratch dir. Empty falls back to the folder itself.

    ``session_id`` tags the progress events so the page can tell THIS
    conversation's batch from any other.
    """

    folder: str = ""
    instructions: str = ""
    output: str = "both"
    max_files: int = 25
    workspace_dir: str = ""
    session_id: str = "chat"


class SettingsBody(BaseModel):
    values: dict[str, Any]


class ProfileBody(BaseModel):
    """PUT /profile — a PARTIAL update of the user profile (v1.144.0).

    Same ``{"values": {...}}`` envelope as SettingsBody on purpose: both are
    "write some of the user's preferences", and one shape means the dashboard's
    save helpers, the error handling, and the mental model are shared. Keys the
    store doesn't know are ignored rather than 400ing, so an older client can
    never blank a field a newer one added (see ``profile.store.save``)."""

    values: dict[str, Any]


class ProfileAccessibilityBody(BaseModel):
    """POST /profile/accessibility — turn a mode on (``""`` turns it off)."""

    mode: str = ""


class WritingSampleBody(BaseModel):
    """POST /profile/samples — one piece of the user's own writing (v1.145.0).

    Either ``text`` (pasted) or ``content_b64`` + ``filename`` (a document,
    converted through the same ``document_to_markdown`` path /ltm/ingest-document
    uses — one converter, not two)."""

    label: str = ""
    text: str = ""
    filename: str = ""
    content_b64: str = ""


class TranscribeBody(BaseModel):
    """Server-side dictation fallback (the packaged desktop app has no Web
    Speech engine): a short audio clip, base64-encoded — same wire pattern as
    UploadBody (JSON body, no multipart dependency)."""

    audio_b64: str
    mime: str = "audio/webm"
    language: str = ""  # optional ISO-639-1 hint, e.g. "en"


class RepairBody(BaseModel):
    #: db_integrity | db_vacuum | prune_events | backup_now | clear_media |
    #: purge_trash | recheck  (clear_media/purge_trash: v1.256.0, R-01)
    action: str
    older_than_days: int = 30


class RestoreBody(BaseModel):
    """``POST /maintenance/restore`` (v1.229.0): the archive's file NAME as
    listed by ``GET /maintenance/backups`` — never a path."""

    name: str


#: Whitelist of config keys the Settings UI may read/write. Since the calm UI
#: redesign (S1/S2) it is GENERATED from the one settings schema
#: (``iron_jarvis.settings.schema``) — the hand-kept list it replaces drifted
#: (``comm_trust`` was rendered by the page and missing here until v1.320.2).
from ..settings.schema import daemon_keys as _daemon_keys  # noqa: E402

_SETTINGS_KEYS = _daemon_keys()


class ConnectionKeyBody(BaseModel):
    key: str


class CreativePublishBody(BaseModel):
    """Publish media to Pixio's public CDN → a permanent public url.

    Exactly one source: a gallery ``name`` (artifact), a local ``path``, or a
    remote ``url`` to mirror. ``endpoint``: 'media' (any media, default) or
    'images' (images only)."""

    name: str = ""
    version: int | None = None
    path: str = ""
    url: str = ""
    endpoint: str = "media"


class CreativeTranscodeBody(BaseModel):
    """Re-encode a video to a universally-playable MP4 (H.264 / yuv420p /
    +faststart). Exactly one source: a gallery ``name`` or a local ``path``."""

    name: str = ""
    version: int | None = None
    path: str = ""


class CreativeIntakeBody(BaseModel):
    """Ask for clarifying questions to sharpen a generation brief. The model
    proposes a few targeted questions (duration, style, aspect, …) with quick
    options, given the brief + chosen skill/model."""

    brief: str = ""
    skill: str = ""
    provider: str = ""
    model: str = ""


class CreativeUploadBody(BaseModel):
    """Add a media file to the Creative gallery (same b64-JSON wire pattern as
    UploadBody — no multipart dependency). ``publish=True`` also pushes it to
    Pixio's CDN and returns the permanent public url."""

    filename: str
    content_b64: str
    publish: bool = False
    #: v1.200.0: scope the saved artifact to a project (Media view). Optional —
    #: the Studio has no project picker yet, so callers may omit it.
    project_id: str | None = None


#: File deliverables the project-task composer may request — each maps to a
#: write_document suffix (markdown structure becomes REAL structure in
#: docx/pdf/pptx/html; list-of-rows becomes real cells in xlsx/csv).
PROJECT_TASK_OUTPUTS = ("chat", "md", "txt", "docx", "xlsx", "pptx", "pdf", "csv", "html")


class ProjectTaskBody(BaseModel):
    """Run a plain-text task INSIDE a project's folder, with a chosen
    deliverable: an in-chat answer (the session summary) or a real file
    (Excel/Word/Markdown/PDF/…) written into the folder."""

    text: str
    output: str = "chat"  # one of PROJECT_TASK_OUTPUTS
    filename: str = ""  # optional file stem; defaults to a slug of the task
    # Bundled tool grant (perm_keys) the user approved for this task after the
    # /task/plan step — these run without per-call prompts.
    allow_tools: list[str] = []
    # v1.174.0: the per-session step budget (Contract 4). The measured failure
    # was posted through THIS surface, so a budget that only POST /sessions
    # could set would never have reached it. None = config.max_agent_steps.
    max_steps: int | None = None
    # v1.296.0: name WHO does it and the task becomes an ASSIGNMENT — a
    # durable job queued for that agent, run by the dispatcher when the
    # agent is free — instead of a session started right now. Empty keeps
    # today's behaviour byte-for-byte. A builtin type or an existing custom
    # agent's name (bare or ``custom:<name>``); anything else is a 422.
    assignee: str = ""

    @field_validator("max_steps", mode="before")
    @classmethod
    def _v_max_steps(cls, v: Any) -> int | None:
        return _clean_max_steps(v)


#: The ONLY keys an assignment's payload may carry (v1.296.0). They are the
#: session-creation knobs the dispatcher forwards verbatim; anything else is a
#: typo or a smuggled setting, and is refused by name rather than ignored.
ASSIGNMENT_PAYLOAD_KEYS = ("allow_tools", "workspace_root", "max_steps", "provider", "model")
ASSIGNMENT_PRIORITY_MIN = -10
ASSIGNMENT_PRIORITY_MAX = 10


class AssignmentCreate(BaseModel):
    """Give an agent a job and let the job wait for it (v1.296.0).

    ``assignee`` is a builtin agent type or an existing custom agent's name;
    ``task`` is the plain-text ask. ``priority`` orders a queue (-10..10,
    higher first; outside is a 422, never clamped — a job posted at 99 that
    quietly runs at 10 was ordered by a number nobody set).
    ``idempotency_key`` lets a caller re-post the same job safely: the store
    coalesces it onto the queued row. ``payload`` carries only the session
    knobs in ``ASSIGNMENT_PAYLOAD_KEYS``."""

    assignee: str
    task: str
    project_id: str = ""
    reason: str = ""
    priority: int = 0
    idempotency_key: str = ""
    payload: dict[str, Any] = {}

    @field_validator("assignee")
    @classmethod
    def _v_assignee(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("assignee is required — a builtin agent type or a custom agent's name")
        return v

    @field_validator("task")
    @classmethod
    def _v_task(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("task is required — say what the agent should do")
        return v

    @field_validator("priority", mode="before")
    @classmethod
    def _v_priority(cls, v: Any) -> int:
        if v is None:
            return 0
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(
                f"priority must be a whole number between {ASSIGNMENT_PRIORITY_MIN} "
                f"and {ASSIGNMENT_PRIORITY_MAX}"
            )
        if v < ASSIGNMENT_PRIORITY_MIN or v > ASSIGNMENT_PRIORITY_MAX:
            raise ValueError(
                f"priority must be between {ASSIGNMENT_PRIORITY_MIN} and "
                f"{ASSIGNMENT_PRIORITY_MAX} (higher runs first); got {v}"
            )
        return v

    @field_validator("payload", mode="before")
    @classmethod
    def _v_payload(cls, v: Any) -> dict[str, Any]:
        if v is None:
            return {}
        if not isinstance(v, dict):
            raise ValueError(
                "payload must be an object with only "
                + ", ".join(ASSIGNMENT_PAYLOAD_KEYS)
            )
        stray = [k for k in v if k not in ASSIGNMENT_PAYLOAD_KEYS]
        if stray:
            raise ValueError(
                f"payload does not accept {', '.join(repr(k) for k in stray)} — "
                f"only {', '.join(ASSIGNMENT_PAYLOAD_KEYS)}"
            )
        out: dict[str, Any] = {}
        if "allow_tools" in v:
            tools = v["allow_tools"]
            if tools is None:
                tools = []
            if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
                raise ValueError("payload.allow_tools must be a list of tool names")
            out["allow_tools"] = [t.strip() for t in tools if t.strip()]
        if "workspace_root" in v:
            root = v["workspace_root"]
            if root is not None and not isinstance(root, str):
                raise ValueError("payload.workspace_root must be a folder path")
            out["workspace_root"] = (root or "").strip()
        if "max_steps" in v:
            out["max_steps"] = _clean_max_steps(v["max_steps"])
        for key in ("provider", "model"):
            if key in v:
                val = v[key]
                if val is not None and not isinstance(val, str):
                    raise ValueError(f"payload.{key} must be a name")
                out[key] = (val or "").strip()
        return out


class ToolPlanBody(BaseModel):
    """Ask the model which tools a plain-text task will likely need, so the UI
    can request permission for the whole bundle at once."""

    text: str


class StudioStartBody(BaseModel):
    """Start a Creative Studio session: open a managed terminal in ``cwd``
    (it shows up on the Build page like any other) and launch the chosen AI
    CLI in it. ``autopilot`` adds the CLI's run-without-prompts flag."""

    cli: str  # an id from GET /terminals/ai-clis (must be installed)
    cwd: str  # absolute destination folder — generations save here
    skill: str = ""  # preferred skill name ("" = let the agent pick)
    autopilot: bool = True


class StudioSayBody(BaseModel):
    """Type one chat-style message into a studio terminal. The FIRST message
    is wrapped with the working brief (skill, save-here, run-to-completion)."""

    text: str
    first: bool = False
    skill: str = ""
    save_dir: str = ""


class CreativeIngestBody(BaseModel):
    """Copy a LOCAL media file (e.g. a Studio generation on disk) into the
    durable gallery (artifact store)."""

    path: str
    #: v1.200.0: scope the saved artifact to a project (Media view). Optional —
    #: the Studio has no project picker yet, so callers may omit it.
    project_id: str | None = None


class FsMkdirBody(BaseModel):
    """Create a folder (e.g. a new subfolder for a generation batch)."""

    path: str


class GraphNodeDeleteBody(BaseModel):
    """Delete one memory-graph node by its composite id (POST body, not a URL
    segment — wm keys legally contain ':' and '/' and URL-escaping those is a
    bug farm)."""

    id: str


class GraphLinkBody(BaseModel):
    """Connect or disconnect two memory-graph nodes (opaque node ids)."""

    a: str
    b: str


class EndpointModelsBody(BaseModel):
    """Probe an OpenAI-compatible endpoint for its model list (setup-form UX:
    the user shouldn't have to type model ids their server can just report).
    POST (not GET) so an optional key never rides a query string/log line."""

    base_url: str
    api_key: str = ""


class OAuthCompleteBody(BaseModel):
    """Manual-code OAuth completion: the pasted code may embed state (code#state)."""

    code: str
    state: str = ""


class SkillCreate(BaseModel):
    """Author a new user skill from the dashboard."""

    name: str
    description: str = ""
    instructions: str


class ChannelCreate(BaseModel):
    """Add a comm channel. ``config`` carries every field (secret + non-secret);
    the server routes ``secret`` fields to the vault by name."""

    name: str
    type: str
    config: dict[str, Any] = {}


class CommThreadSendBody(BaseModel):
    """Desktop reply fan-out (v1.136.0): one user message into a daemon-owned
    comm thread — runs the same chat turn and ALSO sends the reply out the
    thread's bound destination (the phone)."""

    text: str


class IntegrationCreate(BaseModel):
    """Add a custom REST integration (bearer token stored in the vault)."""

    name: str
    base_url: str
    description: str = ""
    auth_token: str = ""


class TerminalAIBody(BaseModel):
    """Per-terminal AI assist: a question + an optional per-PANE model choice.

    ``skill``: "" = AUTO (search the skill library for the best match to the
    prompt and inject it), "none" = no skill injection, anything else = force
    that exact skill by name. Injection is PROMPT-side, so every provider
    (Claude, OpenAI, Grok, Ollama, custom) can use every discovered skill.
    """

    prompt: str
    provider: str = ""
    model: str = ""
    skill: str = ""
    #: Other terminal ids whose recent output to INCLUDE as context — share
    #: what's happening in one terminal with another (and with whatever model
    #: THIS pane uses). Bounded server-side (max 3 terminals, ~4KB each).
    include_terminals: list[str] = []


class ComputerUseEnable(BaseModel):
    enabled: bool = False
    domain_allowlist: list[str] | None = None
    action_allowlist: list[str] | None = None


class TerminalCreate(BaseModel):
    cwd: str | None = None
    shell: str | None = None
    cols: int = 80
    rows: int = 24
    #: v1.217.0 additive. `name` is the pane's human handle (agents address
    #: panes by it); `agent_cli` is what the caller is about to start in it,
    #: which beats sniffing the scrollback for the same answer. Both optional
    #: — an ordinary hand-opened terminal carries neither.
    name: str | None = None
    agent_cli: str | None = None
    #: v1.238.0 additive — what this pane is allowed to do (plan 4.2, D20):
    #: `files`, `shell`, `browser`, `extensions`, `memory`. Typed `Any` rather
    #: than `bool` on purpose: the server normalises through ONE fail-closed
    #: reader (`terminals.session.normalise_pane_capabilities`), and pydantic
    #: coercing some strings to True while 422-ing others would put a second,
    #: looser opinion in front of it. Absent means no capabilities.
    capabilities: dict[str, Any] | None = None
    #: The launch recipe to prepare before the shell starts (plan 13.3), named
    #: by `cli_id`. When set, the pane is spawned with a capability token in its
    #: environment. Absent = today's behaviour exactly: no recipe, no token.
    recipe: str | None = None
    #: v1.302.0 additive — which subscription account each vendor CLI in this
    #: pane runs as: Iron-Proxy provider (`anthropic` / `openai` / `xai`) ->
    #: `"<profile id>"` or `"default"` (this PC's own login). A provider not
    #: named gets Iron-Proxy's first free account while Iron-Proxy is on, and
    #: nothing at all while it is off (`terminals.pane_accounts`). An account
    #: that cannot be used is a 409 sentence and no pane. Fixed for the pane's
    #: life — not in TerminalUpdate.
    accounts: dict[str, str | None] | None = None


class TerminalLaunch(BaseModel):
    """`POST /terminals/launch` (v1.302.0): a NEW pane on an account, with a
    catalog CLI started in it (typed + Enter — the click is the consent).

    `cli` is a Launch-catalog id (`terminals.ai_clis.AI_CLIS`); `account` is
    the profile id (or `"default"`) for THAT CLI's provider (claude ->
    anthropic, codex -> openai, grok -> xai) — a CLI with no account provider
    takes none. `cwd` defaults to the `near` pane's folder; `name` to
    "<CLI label> · <account title>"."""

    cli: str
    account: str | None = None
    cwd: str | None = None
    name: str | None = None
    near: str | None = None
    cols: int = 100
    rows: int = 30


class TerminalUpdate(BaseModel):
    """A PARTIAL update to a live pane (v1.217.0).

    Partial, not a re-POST of the create body: `None` means "leave it", so a
    rename cannot drop the pane's `agent_cli` and recording a launch cannot
    wipe the name the user typed. The remote-agent registry learned this the
    expensive way — a re-post there silently destroyed a credential — and the
    same shape of bug here would quietly un-name the pane an agent is
    addressing. To CLEAR a field, send an empty string.
    """

    name: str | None = None
    agent_cli: str | None = None
    #: v1.245.0: the CLI that was running here before the daemon restarted —
    #: the pane's Resume offer. Send "" to dismiss it (clicking Resume records
    #: the CLI in `agent_cli` and clears this in the same PATCH).
    resume_cli: str | None = None
    #: v1.238.0. PARTIAL twice over: omitting the field keeps every capability,
    #: and a mapping that names only some of the five keeps the rest. The
    #: Capabilities popover toggles one box at a time, so a whole-mapping
    #: replacement here would clear four capabilities the user never touched.
    capabilities: dict[str, Any] | None = None


class CodeArtifactSave(BaseModel):
    """Hand-saving a script into the Code Lab (v1.95.0)."""

    name: str = "untitled"
    language: str = "python"
    source: str
    description: str = ""
    project_id: str | None = None


class CodeArtifactRun(BaseModel):
    """Optional overrides for a re-run. ``timeout_s`` is clamped to the same
    ceiling run_code enforces (300s) inside execute_script."""

    timeout_s: int | None = None


class MemoryImportPreviewBody(BaseModel):
    """Turn a pasted memory dump OR an uploaded export file into CANDIDATE
    memories (v1.123.0). Nothing is saved — the commit route does that."""

    text: str = ""  # pasted "everything you remember about me" reply
    path: str = ""  # server path of an uploaded export (zip/json/txt)
    provider: str = ""  # chatgpt | claude | gemini | grok | other (label only)
    llm_provider: str = ""  # distillation override ("" = default)
    model: str = ""


class MemoryImportEntry(BaseModel):
    """One reviewed candidate with the structure the categorized export
    prompt preserves (v1.129.0): its category and original date."""

    text: str
    category: str = ""  # Instructions | Identity | Career | Projects | Preferences | ""
    date: str = ""  # YYYY-MM-DD from the source model, "" when unknown


class MemoryImportCommitBody(BaseModel):
    """Commit reviewed candidates into a provenance-tagged memory base.
    ``entries`` carries category+date (v1.129.0); plain ``items`` still
    works for uncategorized imports."""

    items: list[str] = []
    entries: list[MemoryImportEntry] = []
    provider: str = "other"


class DesktopIncidentBody(BaseModel):
    """One desktop-shell incident (v1.130.0): the Electron renderer watchdog
    reports freezes / renderer crashes / GPU-process deaths here so they land
    in the same event log as everything else instead of vanishing."""

    kind: str
    detail: str = ""


class MemoryWrite(BaseModel):
    """Body of the (single) POST /memory. ``layer`` defaults to "user" — the
    layer that endpoint has always actually written to; this model once
    defaulted to "project" but sat behind a duplicate registration and never
    served a request, so "project" was never the live behavior."""

    layer: str = "user"  # whatever layers MemoryLayers accepts
    key: str
    text: str
    scope_id: str | None = None


class WorkflowRunBody(BaseModel):
    toml: str | None = None
    name: str | None = None
    #: v1.170.0 — ``name`` ALONE (steps omitted) runs the SAVED def: the server
    #: resolves stored steps + the project pin via ``WorkflowStore.load_def``
    #: (404 when unknown). ``name`` + ``steps`` keeps its ad-hoc meaning.
    steps: list[dict] | None = None
    #: Explicit project pin for THIS run. None inherits the saved def's pin
    #: (matched by name); "" forces an unpinned run.
    project_id: str | None = None
    #: v1.170.0 — run inputs: each becomes a pre-seeded ``completed`` output
    #: under its name (kind "input"), so ``{{name}}`` templating just works.
    #: None (the default) keeps the legacy call byte-identical.
    inputs: dict[str, str] | None = None


class WorkflowPatchBody(BaseModel):
    """Rename / re-describe a saved workflow (v1.170.0) WITHOUT re-posting its
    steps. ``None`` leaves a field alone (the PATCH convention every other
    editor here follows); ``new_name`` moves the def AND its project-pin row
    — 409 when the target name is already taken."""

    new_name: str | None = None
    description: str | None = None


class WorkflowAnswerBody(BaseModel):
    """Answer a parked (waiting) run's ask-step question (v1.121.0)."""

    answer: str


class WorkflowSaveBody(BaseModel):
    name: str
    steps: list[dict] = []
    description: str = ""
    #: Explicit project pin. None PRESERVES an existing pin (a UI that doesn't
    #: know about pins must not silently unpin on re-save); "" unpins.
    project_id: str | None = None


class WorkflowGenerateBody(BaseModel):
    """Build/refine a workflow from a natural-language description via an agent."""

    description: str
    name: str = ""
    current: list[dict] = []  # existing steps to refine (optional)
    provider: str = ""
    model: str = ""


class TerminalWorkflowBody(BaseModel):
    """Turn a terminal session's transcript into a repeatable workflow."""

    note: str = ""  # optional hint: "what this session was doing"
    provider: str = ""
    model: str = ""


class FeedbackBody(BaseModel):
    rating: str = "up"  # up | down | neutral
    comment: str = ""


class ChatFeedbackBody(BaseModel):
    """v1.320.0: a 👍 / 👎 on a chat reply (chat turns have no session row)."""

    rating: str = "up"  # up | down
    comment: str = ""
    thread_id: str = ""


class DocWriteBody(BaseModel):
    path: str
    content: str
    kind: str | None = None


class SaveCopyBody(BaseModel):
    """Copy a produced document out of the confined workspace to a real folder.

    Chat's tools write inside the uploads scratch dir (or the grounded project
    folder), so a finished file lands somewhere the user did not choose. This
    is the "where do you want it?" answer.
    """

    source: str
    #: Absolute destination FOLDER (the picker and the place buttons both send one).
    dest_dir: str
    #: Optional rename; empty keeps the source filename.
    name: str = ""
    overwrite: bool = False


class RedactScanBody(BaseModel):
    """STEP 1 of PII redaction: list what was found so a human can approve it.

    Detection is deterministic (regex + Luhn), so this returns candidates, not
    a verdict — the point is that the user sees every item BEFORE anything is
    written.
    """

    path: str
    #: Extra literal strings to flag (names, employers) — regex can't see these.
    extra_terms: list[str] = []
    #: Optional category subset (ssn, ein, email, …); empty = all.
    categories: list[str] = []


class RedactApplyBody(BaseModel):
    """STEP 2: redact EXACTLY the confirmed values into a chosen destination.

    ``terms`` is the approved list from the scan. It is deliberately required
    to be non-empty at the route: an empty list here would silently fall back
    to auto-detection and redact things the user never approved.
    """

    path: str
    #: The exact values the user ticked. Nothing else is touched.
    terms: list[str]
    #: black = █ blocks (default), label = [SSN] tags, remove = delete.
    style: str = "black"
    #: Absolute destination. Empty = "<name>.redacted.<ext>" beside the source.
    output_path: str = ""
    #: Refuse to clobber an existing file unless the user said so.
    overwrite: bool = False


class SecretSet(BaseModel):
    name: str
    value: str
    kind: str = "generic"
    description: str = ""


class NotifyBody(BaseModel):
    message: str
    channels: list[str] | None = None


class IntegrationConfigBody(BaseModel):
    config: dict = {}


class IntegrationEnableBody(BaseModel):
    enabled: bool = True


class ScheduleAdd(BaseModel):
    """``POST /schedules``. ``payload`` is a free dict; a ``task`` kind reads
    ``task``, ``agent_type``, ``provider``, ``model``, ``project_id``,
    ``notify*`` and (v1.299.0) the five KNOBS validated by
    ``scheduling/knobs.validate_knobs``: ``skills`` (names in the registry),
    ``workspace_root`` (a usable folder), ``context_from`` (another schedule's
    name), ``script`` (``{command, timeout_s ≤ 120, cwd: workspace|home}``,
    user-made schedules only) and ``skip_memory`` (bool)."""

    name: str
    cron: str | None = None
    run_at: str | None = None
    interval_seconds: int | None = None
    kind: str = "workflow"
    payload: dict = {}


class SchedulePatch(BaseModel):
    """``PATCH /schedules/{name}`` (v1.299.0): every ``ScheduleAdd`` field
    optional (``None`` = leave alone; a trigger field given REPLACES the
    trigger), plus ``payload_set`` (keys merged over the stored payload) and
    ``payload_unset`` (keys removed — wins over ``payload_set`` for the same
    key). The MERGED payload is re-validated with the add-time rules."""

    cron: str | None = None
    run_at: str | None = None
    interval_seconds: int | None = None
    kind: str | None = None
    enabled: bool | None = None
    payload_set: dict = {}
    payload_unset: list[str] = []


class SentinelAdd(BaseModel):
    name: str
    path: str
    glob: str | None = None
    task: str = ""
    kind: str = "file"
    agent_type: str = "builder"
    risk: str = "low"  # low | med


class TemplateCreateBody(BaseModel):
    name: str
    task: str
    agent_type: str = "builder"
    provider: str | None = None
    model: str | None = None
    description: str = ""  # "use this when…" — makes the template self-explanatory


class TemplateUpdateBody(BaseModel):
    """Edit a saved template (v1.128.0). ``None`` leaves a field alone;
    ``clear_model`` drops a pinned provider/model back to the session default
    (None can't express "unset")."""

    name: str | None = None
    task: str | None = None
    agent_type: str | None = None
    provider: str | None = None
    model: str | None = None
    description: str | None = None
    clear_model: bool = False


class ToolGenerateBody(BaseModel):
    """Describe the tool you want in plain language; an LLM designs it."""

    description: str
    provider: str = ""
    model: str = ""


class PersonaSaveBody(BaseModel):
    """Create or update a chat persona. ``name`` (slug id) is taken from the URL;
    editing a built-in name writes an override. ``title`` is the display name."""

    title: str = ""
    description: str = ""
    prompt: str = ""


class PersonaCreateBody(BaseModel):
    """Create a NEW persona; the slug id is derived from ``title`` (or ``name``)."""

    name: str = ""
    title: str = ""
    description: str = ""
    prompt: str = ""


class RoutingEnableBody(BaseModel):
    """Turn ON Auto routing. ``routing_model`` ("provider:model") is the cheap
    classifier; blank = use the suggested cheapest connected model."""

    routing_model: str = ""


class RoutingDisableBody(BaseModel):
    """Turn OFF Auto routing and pin a concrete default model. Blank = revert to
    the suggested/first connected model."""

    provider: str = ""
    model: str = ""


class ConnectorConnectBody(BaseModel):
    """One-tap connect for a marketplace connector. ``values`` carries the
    connector's field inputs (MCP token/env/arg fields), or ``{"key": "..."}``
    for an api-key connector. OAuth connectors need no values."""

    values: dict[str, str] = {}


class ReflexRuleBody(BaseModel):
    """A Reflex rule: bind an inbound signal (webhook slug / comm keyword) to an
    action (run a workflow / remote agent / session)."""

    name: str = ""
    source: str = "webhook"       # webhook | comm
    match: str = ""               # webhook slug, or comm keyword
    action: str = "workflow"      # workflow | remote_agent | session
    target: str = ""              # workflow name / remote agent name
    task_template: str = ""
    enabled: bool = True
    #: Context spine (v1.200.0): ground this rule's work in a project. A
    #: session action spawns carrying it; a workflow action uses it only when
    #: the def has no pin of its own. None/"" = ungrounded.
    project_id: str | None = None


class ReflexToggleBody(BaseModel):
    """Partial update. ``enabled`` flips the rule; ``project_id`` re-grounds it.

    Three intents, kept distinct (the remote-agent-token lesson): omit the
    field (None) = UNCHANGED, ``""`` = CLEAR the grounding, non-empty = set it.
    An unedited form that never mentions ``project_id`` must not clear one.
    """

    enabled: bool | None = None
    project_id: str | None = None


class McpServerBody(BaseModel):
    """An external MCP server to register (prebuilt from the catalog, or custom)."""

    name: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}
    cwd: str | None = None
    #: When true, the headless daemon runs this server's tools without an
    #: interactive prompt, so autonomous agents can use it (chat already
    #: approves-by-arming). Coarse — enabling it trusts every connected MCP
    #: tool — and applied at the next daemon restart. Default off (fail-closed).
    auto_approve: bool = False


class McpServerPatch(BaseModel):
    """Edit a connected MCP pack (v1.103.0). ``None`` means "leave alone", so a
    UI that only flips auto-approve can't blank the rest of the record."""

    auto_approve: bool | None = None


class McpSettingsPatch(BaseModel):
    """The GLOBAL MCP auto-approve switch (v1.127.0) — the Tools page checkbox.
    ``None`` reads the current state without changing anything."""

    auto_approve: bool | None = None


class McpSuggestBody(BaseModel):
    description: str
    provider: str = ""
    model: str = ""


class SkillProposalApproveBody(BaseModel):
    """Approve a learned-skill proposal (v1.135.0). ``body_md`` carries an
    edited SKILL.md that wins over the stored draft; ``None`` approves the
    draft as distilled."""

    body_md: str | None = None


class SkillLearningSettingsPatch(BaseModel):
    """The Skills page's two learning toggles (v1.135.0) — real persisted
    settings, the v1.127.0 MCP-auto-approve pattern: ``None`` means "leave
    alone", so a UI flipping one switch can't blank the other."""

    enabled: bool | None = None
    auto_approve: bool | None = None


class SessionsClearBody(BaseModel):
    """Bulk-clear finished sessions (never touches active ones)."""

    statuses: list[str] = ["completed"]  # completed | failed | cancelled


class LTMAppend(BaseModel):
    title: str
    content: str
    # LTM source name; None/empty -> the default (brain) source. This field was
    # MISSING while the handler read body.source — every append 500'd.
    source: str | None = None


class IngestDocumentBody(BaseModel):
    """A base64 document (PDF/office/HTML/text) to convert to Markdown and store
    durably in long-term memory (the knowledge base), not just chat grounding."""

    filename: str
    content_b64: str
    title: str = ""  # defaults to the filename stem
    source: str | None = None  # LTM source name; None -> the brain source


class LTMSourceBody(BaseModel):
    name: str
    kind: str = "markdown"  # see ltm.sources.SOURCE_KINDS
    path: str = ""  # local folder (markdown) / remote path (ssh) / folder scope (cloud)
    database_id: str = ""
    token_secret: str = ""  # existing vault secret name (notion/ssh), if reusing one
    # SSH (remote) source:
    host: str = ""
    port: int = 22
    username: str = ""
    key_path: str = ""  # local private-key file (alternative to a password)
    password: str = ""  # a NEW SSH password to store in the vault (write-only)
    # Offsite HTTP RAG source:
    endpoint_url: str = ""  # query URL of the external RAG service (http_rag)
    config: dict[str, Any] = {}  # HttpRagConfig overrides (http_rag)
    token: str = ""  # a NEW bearer/API token to store in the vault (write-only, http_rag)


#: The approval postures a custom agent may be HIRED with (v1.295.0). The
#: same two the session door accepts plus "" (= the platform default). ``yolo``
#: is refused at the route with a sentence, never mapped: see
#: ``runtime.inherited_approval_mode`` for why a background run never inherits
#: a chat's auto-approve.
AGENT_APPROVAL_MODES = ("", "approve_for_me", "always_ask")


class AgentCreate(BaseModel):
    """Hire a custom agent (v1.295.0: an employee has a job card, a monthly
    allowance and a day off).

    Every employee field below is validated AT THE ROUTE (``routes/agents.py``,
    ``_employee_fields``) with a plain-words 422 — ``reports_to`` needs the
    registry (the manager must exist) and the rest keep the same wording
    beside it. The one exception is ``max_steps``, which reuses the session
    door's own ``_clean_max_steps`` so the bounds can never drift from
    ``POST /sessions``.
    """

    name: str
    system_prompt: str
    tools: list[str] = []
    description: str = ""
    provider: str = ""
    model: str = ""
    # The builtin whose lifecycle/roster this agent borrows; never "supervisor"
    # (the builtin supervisor would run and discard the custom prompt).
    base_type: str = "builder"
    # "" | "approve_for_me" | "always_ask" — the posture every spawn starts in
    # unless the spawn body states one.
    approval_mode: str = ""
    # Per-run step budget, same bounds as SessionCreate; None = configured default.
    max_steps: int | None = None
    # Monthly allowance (calendar month): 0 = unlimited.
    allowance_tokens: int = 0
    allowance_usd: float = 0.0
    # Who this agent reports to: "" = the user, a builtin type name, or
    # "custom:<name>" of an EXISTING custom agent (never itself).
    reports_to: str = ""
    # The job card: skill names it is hired for, and tools it must never hold.
    skills: list[str] = []
    deny_tools: list[str] = []

    @field_validator("max_steps", mode="before")
    @classmethod
    def _validate_max_steps(cls, v: Any) -> int | None:
        return _clean_max_steps(v)


class CustomToolCreate(BaseModel):
    name: str
    description: str = ""
    parameters: list[dict] = []
    command: list[str] = []
    timeout_seconds: int = 60


class WebhookCreate(BaseModel):
    slug: str
    direction: str = "inbound"  # inbound | outbound
    target_url: str = ""
    event_types: list[str] = []
    secret_name: str = ""


class SpawnBody(BaseModel):
    task: str
    # wait=false returns immediately (run continues in the background) so the
    # UI can jump to the live session view instead of blocking on the run.
    wait: bool = True
    # Parity with SessionCreate (v1.166.0) so the Agents-page job poster can
    # dispatch a dynamic agent exactly like POST /sessions. An explicit
    # ``provider``/``model`` wins over the dynamic record's pinned pair.
    provider: str | None = None
    model: str | None = None
    project_id: str = ""
    allow_tools: list[str] = []
    # Same contract as SessionCreate.approval_mode (v1.232.0) — the chat's
    # posture rides a custom-agent escalation exactly as a builtin one.
    approval_mode: str = ""
    # Same contract as SessionCreate.workspace_root (v1.189.0) — a spawned
    # dynamic agent escalated from a folder-grounded chat works IN that folder.
    workspace_root: str = ""
    origin: str | None = None

    @field_validator("origin")
    @classmethod
    def _validate_origin(cls, v: str | None) -> str | None:
        return _clean_origin(v)


class UpdateBody(BaseModel):
    # Whether to rebuild the dashboard (pnpm install && pnpm build) after pulling.
    build_dashboard: bool = True


class GoalBody(BaseModel):
    text: str
    category: str = "general"
    priority: int = 3
    autonomy_level: str = "suggest"  # suggest | act_low | act_all
    source: str = "user"


class GoalPatch(BaseModel):
    text: str | None = None
    category: str | None = None
    priority: int | None = None
    autonomy_level: str | None = None  # the per-goal dial
    status: str | None = None  # active | paused | done | abandoned
    action_budget: int | None = None
    spend_budget: int | None = None
    actions_taken: int | None = None  # set to 0 to reset the rolling counter
    tokens_spent: int | None = None


class GoalContractCreate(BaseModel):
    """Create one GOAL CONTRACT (v1.208.0, the ``goals/`` package) —
    ``POST /goals`` (routes/goals.py).

    NOT the motivation-era ``GoalBody`` above: that lightweight intent shape
    belongs to the Motivation Layer and now answers at ``POST /autonomy/goals``.
    This model is deliberately a THIN wire shape: every rule with a WHY
    (deny-floor grants, budget must be bounded or explicitly unlimited, a
    ``checks`` verifier must check something) lives in ``GoalStore.create``,
    and the route relays its ``ValueError`` text verbatim as the 400 — one
    rule set, one wording, no drift."""

    name: str = ""  # blank falls back to the contract's first 60 chars
    contract_text: str  # the goal, stated checkably — required
    agent_type: str = "builder"
    project_id: str | None = None  # context spine; None/blank = ungrounded
    schedule: str = ""  # cron for the kind="goal" dispatch; "" = run-now only
    allowed_grants: list[str] = Field(default_factory=list)
    budget: dict[str, Any] | None = None  # store-validated (bound or unlimited)
    verifier: dict[str, Any] | None = None  # store-validated; None = manual


class GoalGrantsPatch(BaseModel):
    """Extend a goal contract's ``allowed_grants`` (v1.209.0 —
    ``PATCH /goals/{id}/grants``, the trust ladder's acceptance door).

    Add-only ON PURPOSE: the offer flow grants; revoking is an edit with
    different stakes and gets its own verb when it exists. The merged list is
    validated by the store's own ``grants_violation`` (deny-floor tools 400
    verbatim) — this model stays a thin wire shape."""

    add: list[str] = Field(default_factory=list)
    #: v1.299.0: EXACT grants — ``[{tool, args_hash, label?}]`` — live only
    #: in the standing-grant store (``core/grants.py``), scope ``goal``;
    #: the per-tool ``add`` list ALSO lands a store row (``args_hash == ""``)
    #: beside the compat ``allowed_grants`` append.
    add_exact: list[dict[str, Any]] = Field(default_factory=list)
    #: Days until the minted rows expire; ``null`` = never (goal scope only).
    expires_days: int | None = 30


class KillBody(BaseModel):
    enabled: bool = True  # engage (True) or release (False) the global kill switch


class RemoteAgentCreate(BaseModel):
    """Register a remote agent the user runs elsewhere (§11/§12)."""

    name: str
    base_url: str
    kind: str = "http-task"  # http-task | openai-chat
    model: str = ""  # model id for openai-chat endpoints
    token: str = ""  # bearer credential — stored in the vault, never returned
    enabled: bool = True
    timeout_s: int = 120


class RemoteAgentPatch(BaseModel):
    """Fix a registered remote agent WITHOUT re-entering everything (§11/§12).

    Every field is optional and ``None`` means "leave it alone" — the point of a
    PATCH here rather than reusing the create body. The bearer token cannot be
    prefilled by any UI (it is stored encrypted and never returned), so a form
    that posted the full record would send an empty token and wipe a working
    credential. Omitting ``token`` keeps the stored one; ``clear_token`` removes
    it deliberately.

    ``name`` is absent on purpose: it is the identity panels and threads refer
    to (``participantKey("remote", name)``), so renaming would orphan those
    references silently. Deleting and re-adding is the honest way to rename.
    """

    base_url: str | None = None
    kind: str | None = None
    model: str | None = None
    token: str | None = None  # a new credential; omit to keep the existing one
    clear_token: bool = False  # explicit removal, so it can never be accidental
    enabled: bool | None = None
    timeout_s: int | None = None
    #: v1.285.0: where the remote is told to message back (see
    #: ``RemoteAgentRecord.inbound_url``). Omit to keep; "" is refused.
    inbound_url: str | None = None


class RemoteAgentRun(BaseModel):
    task: str


class RemoteInboundEnable(BaseModel):
    """Turn a remote's inbound on (v1.285.0). ``url`` overrides the address
    derived from this request — for a daemon reached through a tunnel or a
    reverse proxy the remote sees a different host than the dashboard does."""

    url: str = ""


class AgentPatch(BaseModel):
    """Edit a dynamic agent in place (only the provided fields change).

    ``None`` means "keep what is stored" for EVERY field — the route passes
    only the provided ones to ``DynamicAgentRegistry.register`` (v1.295.0),
    which keeps an omitted kwarg's existing value. Before this, the route
    re-passed the record's own ``provider``/``model``/``base_type``, so those
    three could not be edited at all without delete-and-recreate.
    """

    system_prompt: str | None = None
    tools: list[str] | None = None
    description: str | None = None
    provider: str | None = None
    model: str | None = None
    base_type: str | None = None
    # Employee fields (v1.295.0) — same validation as AgentCreate, at the route.
    approval_mode: str | None = None
    max_steps: int | None = None
    allowance_tokens: int | None = None
    allowance_usd: float | None = None
    reports_to: str | None = None
    skills: list[str] | None = None
    deny_tools: list[str] | None = None
    # ``max_steps: null`` means KEEP on a PATCH, so clearing a stored budget
    # (back to the configured default) needs its own explicit flag — the same
    # reason RemoteAgentPatch has ``clear_token``. Sent together with a
    # ``max_steps`` value it is a 422: the two say opposite things.
    clear_max_steps: bool = False

    @field_validator("max_steps", mode="before")
    @classmethod
    def _validate_max_steps(cls, v: Any) -> int | None:
        return _clean_max_steps(v)


class PauseBody(BaseModel):
    """Give a custom agent a day off (v1.295.0, ``POST /agents/{name}/pause``).

    ``reason`` is what every refused spawn will say back; blank gets the
    route's default wording so a paused agent is never paused "for no reason".
    """

    reason: str = ""
