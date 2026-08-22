"""Request models for the daemon API (moved out of daemon/app.py).

Pure pydantic request/whitelist declarations shared by app.py and the
routes/ domain modules.
"""

from __future__ import annotations

import re

from typing import Any

from pydantic import BaseModel, field_validator

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


class UploadBody(BaseModel):
    filename: str
    content_b64: str


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
    action: str  # db_integrity | db_vacuum | prune_events | backup_now | recheck
    older_than_days: int = 30


#: Whitelist of config keys the Settings UI may read/write (safe, restart-light).
_SETTINGS_KEYS = [
    "default_provider",
    "default_model",
    # Persona slug or free text used whenever a chat turn carries no explicit
    # persona (desktop chat, stream, phone) — same contract as ChatBody.persona.
    "default_persona",
    # Never substitute an explicitly-picked provider (see config.strict_model_pin).
    "strict_model_pin",
    # Auto model routing — the classifier + optional tier overrides. "auto" as
    # the default_provider is the ON switch.
    "routing_model",
    "routing_tiers_json",
    "max_agent_steps",
    "git_native",
    "self_dev_enabled",
    "self_dev_root",
    "sandbox_runtime",
    "ollama_base_url",
    "ollama_model",
    # Custom OpenAI-compatible endpoint (Ollama Cloud / LM Studio / vLLM /
    # private gateways) — pairs with the optional custom_api_key vault entry.
    "custom_base_url",
    "custom_model",
    # Known context windows (tokens) keyed "provider::model"/"model"/"provider"
    # — scales attachment budgets for local endpoints that don't advertise
    # theirs (a 128k fleet model gets whole documents inline; 8k gets RAG).
    "model_context_windows",
    "context_compaction",
    # Step-aware routing (v1.135.0): role -> "provider:model" overrides for
    # plan/synthesize/extract/judge/vision one-shots inside multi-step runs.
    "model_roles",
    # LOCAL-FIRST ROUTING (v1.148.0). These four shipped in Config but were
    # absent HERE, so the only way to turn local-first on was to hand-edit
    # config.toml — which is not a feature, it is a feature nobody can reach.
    # `routing_local_ladder` is a LIST and is deliberately included: unlike
    # fleet_nodes/mcp_servers (managed by their own routes), this one has no
    # other editor, so the settings round-trip is its only home.
    "prefer_local_when_capable",
    "local_quality_bar",
    "local_quality_min_samples",
    "routing_local_ladder",
    # Short-horizon decomposition for local models (v1.132.0) — same story.
    "decompose_local_tasks",
    # OpenCode store override for the Usage merge (dir or .db path).
    "opencode_data_dir",
    # Voice speech-to-text — an optional DEDICATED whisper endpoint + model, so a
    # self-hosted STT server works independently of the (possibly non-transcribing)
    # chat endpoint. Its key lives in the vault as voice_transcribe_key.
    "voice_transcribe_base_url",
    "voice_transcribe_model",
    "voice_vosk_model_path",  # bundled offline (Vosk) model dir override
    "event_retention_days",
    # Motivation Layer (the pulse) — all OFF / conservative by default. Toggling
    # autonomy_* at runtime re-arms the background loop LIVE (put_settings →
    # _live_rearm); no restart needed.
    "autonomy_enabled",
    "autonomy_level",
    "autonomy_dry_run",
    "autonomy_kill_switch",
    "autonomy_tick_seconds",
    "autonomy_max_actions_per_day",
    "autonomy_max_tokens_per_day",
    # Sentinels (always-on watchers) — OFF by default. Toggling sentinels_* at
    # runtime re-arms the background polling loop LIVE (mirrors autonomy_*).
    "sentinels_enabled",
    "sentinels_tick_seconds",
    # CX-05 calendar trigger (inbound everything) — OFF by default. Toggling
    # calendar_* at runtime re-arms the background polling loop LIVE (mirrors
    # autonomy_*/sentinels_*). The ICS URL itself is a vault secret, not a setting.
    "calendar_trigger_enabled",
    "calendar_tick_seconds",
    "calendar_lead_minutes",
    # Local fleet — SCALARS only. `fleet_nodes` is deliberately absent (same rule
    # as mcp_servers/custom_integrations): a list is managed by /fleet/nodes, and
    # a settings-page round-trip of the whole blob is how nodes get lost.
    "fleet_sampling_enabled",
    "fleet_sampling_seconds",
    "fleet_savings_baseline",
    "fleet_code_route_enabled",
    "fleet_code_target",
    "fleet_code_task_classes",
    # OpenCode CLI provider — CSV of "provider/model" it may serve.
    # "" = auto-detect the models that genuinely run on your own hardware.
    "opencode_local_models",
    # Skill learning (v1.135.0) — the suggest-only skill loop's two switches.
    # Also settable from the Skills page via PATCH /skills/learning/settings.
    "skill_learning_enabled",
    "skill_learning_auto_approve",
    # v1.143.0: the periodic memory-curation review (additions are written,
    # every change/removal is queued for approval — see Config).
    "memory_steward_enabled",
]


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

    @field_validator("max_steps", mode="before")
    @classmethod
    def _v_max_steps(cls, v: Any) -> int | None:
        return _clean_max_steps(v)


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
    name: str
    cron: str | None = None
    run_at: str | None = None
    interval_seconds: int | None = None
    kind: str = "workflow"
    payload: dict = {}


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


class AgentCreate(BaseModel):
    name: str
    system_prompt: str
    tools: list[str] = []
    description: str = ""
    provider: str = ""
    model: str = ""


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


class RemoteAgentRun(BaseModel):
    task: str


class AgentPatch(BaseModel):
    """Edit a dynamic agent in place (only the provided fields change)."""

    system_prompt: str | None = None
    tools: list[str] | None = None
    description: str | None = None
