"""Request models for the daemon API (moved out of daemon/app.py).

Pure pydantic request/whitelist declarations shared by app.py and the
routes/ domain modules.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


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


class DocEnhanceBody(BaseModel):
    """AI pass over a document draft BEFORE creation: better name + content."""

    filename: str = ""
    content: str = ""
    provider: str = ""
    model: str = ""


class LessonCreateBody(BaseModel):
    text: str
    scope: str = "user"


class MemoryWriteBody(BaseModel):
    layer: str = "user"  # whatever layers MemoryLayers accepts
    key: str
    text: str


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
    #: as the persona ("" = the default assistant).
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
    #: "" = fall back to the active project (unchanged behavior).
    project_id: str = ""


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


class FsMkdirBody(BaseModel):
    """Create a folder (e.g. a new subfolder for a generation batch)."""

    path: str


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


class MemoryWrite(BaseModel):
    layer: str = "project"
    key: str
    text: str
    scope_id: str | None = None


class WorkflowRunBody(BaseModel):
    toml: str | None = None
    name: str | None = None
    steps: list[dict] | None = None


class WorkflowSaveBody(BaseModel):
    name: str
    steps: list[dict] = []
    description: str = ""


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


class ToolGenerateBody(BaseModel):
    """Describe the tool you want in plain language; an LLM designs it."""

    description: str
    provider: str = ""
    model: str = ""


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


class ReflexToggleBody(BaseModel):
    enabled: bool


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


class McpSuggestBody(BaseModel):
    description: str
    provider: str = ""
    model: str = ""


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


class RemoteAgentRun(BaseModel):
    task: str


class AgentPatch(BaseModel):
    """Edit a dynamic agent in place (only the provided fields change)."""

    system_prompt: str | None = None
    tools: list[str] | None = None
    description: str | None = None
