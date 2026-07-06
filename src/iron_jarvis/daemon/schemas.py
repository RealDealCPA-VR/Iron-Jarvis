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


class McpServerBody(BaseModel):
    """An external MCP server to register (prebuilt from the catalog, or custom)."""

    name: str
    command: str
    args: list[str] = []
    env: dict[str, str] = {}
    cwd: str | None = None


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
