// Shapes mirror the Iron Jarvis daemon API (FastAPI).

export interface ProviderHealth {
  provider: string;
  available: boolean;
  class: string;
}

export interface Health {
  status: string;
  version: string;
  default_provider: string;
  default_model: string;
  providers: ProviderHealth[];
}

export interface Metrics {
  sessions_evaluated: number;
  avg_completion: number;
  avg_tool_success_rate: number;
  avg_latency_s: number;
  total_tool_invocations: number;
  event_count: number;
}

export interface VaultProvider {
  provider: string;
  logged_in: boolean;
}

export interface SessionView {
  id: string;
  task: string;
  agent_type: string;
  provider: string;
  model: string;
  status: string;
  workspace_path: string;
  summary: string;
  input_tokens?: number;
  output_tokens?: number;
  created_at: string;
  finished_at: string | null;
}

export interface AgentRun {
  id: string;
  session_id: string;
  parent_id: string | null;
  agent_type: string;
  provider: string;
  model: string;
  state: string;
  steps: number;
  result: string;
  created_at: string;
  finished_at: string | null;
}

export interface ToolInvocation {
  id: string;
  session_id: string;
  agent_run_id: string;
  tool: string;
  args_json: string;
  verdict: string;
  ok: boolean;
  output: string;
  created_at: string;
}

export interface Transcript {
  runs: AgentRun[];
  tools: ToolInvocation[];
}

export interface SessionDetail {
  session: SessionView;
  transcript: Transcript;
}

export interface Evaluation {
  completion: number;
  tool_success_rate: number;
  tool_calls: number;
  step_count: number;
  latency_s: number;
  cost?: number;
  review_acceptance?: number | null;
  [k: string]: unknown;
}

export interface Trace {
  type: string;
  ts: string;
  payload: Record<string, unknown>;
}

export interface Review {
  changed_files: string[];
  diff: string;
  risk: string;
  branch?: string;
  summary?: string;
  session_id?: string;
  [k: string]: unknown;
}

export interface MemoryResult {
  layer: string;
  key: string;
  text: string;
  score: number;
}

export interface Skill {
  name: string;
  description: string;
}

export interface SkillDetail extends Skill {
  instructions: string;
}

export interface WorkflowRun {
  id?: string;
  workflow_name?: string;
  status?: string;
  session_ids_json?: string;
  created_at?: string;
  [k: string]: unknown;
}

export interface Tool {
  name: string;
  description: string;
  input_schema?: unknown;
}

export interface IJEvent {
  id: string;
  type: string;
  session_id: string | null;
  ts: string;
  payload: Record<string, unknown>;
}

/* ---- Secrets ------------------------------------------------------------- */
export interface SecretMeta {
  name: string;
  kind: string;
  description: string;
  has_value: boolean;
  updated_at: string | null;
}

/* ---- Integrations -------------------------------------------------------- */
export interface Integration {
  id: string;
  kind: string;
  display_name: string;
  enabled: boolean;
  configured: boolean;
  required_secrets: string[];
}

export interface IntegrationTestResult {
  ok: boolean;
  detail: string;
}

/* ---- Communication channels --------------------------------------------- */
export interface NotifyResult {
  ok: boolean;
  detail: string;
}

/* ---- Webhooks ------------------------------------------------------------ */
export interface Webhook {
  slug: string;
  direction: string;
  target_url: string | null;
  event_types_json: string | null;
  enabled: boolean;
  created_at?: string | null;
  [k: string]: unknown;
}

/* ---- File search --------------------------------------------------------- */
export interface FileSearchResult {
  path: string;
  line?: number | null;
  text?: string | null;
  root?: string | null;
}

export interface Drive {
  path: string;
  label: string;
}

/* ---- Terminals (live shell sessions) ------------------------------------- */
/** A live terminal session as reported by the daemon (`session.info()`). */
export interface TerminalInfo {
  id: string;
  cwd: string;
  shell: string;
  argv: string[];
  cols: number;
  rows: number;
  alive: boolean;
  exit_code: number | null;
  /** True when running on a pipe-based shell (no full TTY) — fallback path. */
  degraded?: boolean;
  created_at: string;
}

/** A shell available on the host (`GET /terminals/shells`). */
export interface Shell {
  name: string;
  argv: string[];
}

/* ---- Filesystem directory browser (terminals tree panel) ----------------- */
/** A single directory child surfaced by the on-demand tree lister. */
export interface FsEntry {
  name: string;
  path: string; // absolute
  is_dir: boolean;
  is_project: string | null; // project type ("git"|"python"|"node"|…) when a project root
  size: number | null; // byte size for files; null for directories
}

/** One level of a directory listing (`GET /fs/list`). */
export interface FsListing {
  path: string;
  parent: string | null;
  entries: FsEntry[];
}

/* ---- Schedules ----------------------------------------------------------- */
export interface Schedule {
  name: string;
  cron: string;
  kind: string;
  enabled: boolean;
  next_run: string | null;
  last_run: string | null;
  trigger_type?: string;
  run_at?: string | null;
  interval_seconds?: number | null;
  [k: string]: unknown;
}

/* ---- Long-term memory ---------------------------------------------------- */
export interface LtmResult {
  title: string;
  snippet: string;
  ref: string;
  source: string;
}

export interface LtmSource {
  name: string;
  kind: string;
  path?: string;
  database_id?: string;
  token_secret?: string;
  created_at?: string | null;
  [k: string]: unknown;
}

/* ---- Models -------------------------------------------------------------- */
export interface ModelOption {
  provider: string;
  model: string;
}

/* ---- Connections (LLM connect: API key + OAuth) -------------------------- */
export interface Connection {
  provider: string;
  display_name: string;
  method: "api_key" | "oauth" | "browser";
  /** A provider may support BOTH account-login (OAuth) and an API key. */
  supports_oauth?: boolean;
  supports_api_key?: boolean;
  oauth_help?: string;
  key_help?: string;
  /** Manual-code OAuth (Anthropic): the provider shows a code to paste back. */
  oauth_manual_code?: boolean;
  connected: boolean;
  status: string; // "connected" | "disconnected" | "needs_auth"
  account: string;
  scopes: string[];
}

export interface ConnectionTestResult {
  ok: boolean;
  detail: string;
}

export interface OAuthStart {
  authorization_url: string;
  state: string;
}

/** Message the daemon's OAuth callback posts back to the dashboard window. */
export interface OAuthMessage {
  type: "ironjarvis-oauth";
  provider: string;
  ok: boolean;
}

/* ---- Onboarding / first-run / doctor ------------------------------------- */
export interface OnboardingStep {
  key: string; // connect_ai | first_session | work_with_document | teach_style
  title: string;
  detail: string;
  done: boolean;
  action: string;
}

export interface DoctorCheck {
  name: string;
  ok: boolean;
  detail: string;
  fix: string;
  level?: string; // "required" | "recommended"
}

export interface Doctor {
  ok: boolean;
  checks: DoctorCheck[];
}

export interface Onboarding {
  version: string;
  first_run: boolean;
  doctor: Doctor;
  checklist: OnboardingStep[];
  next_step: OnboardingStep | null;
}

/* ---- Documents ----------------------------------------------------------- */
export interface DocumentRead {
  path: string;
  text: string;
}

export interface DocumentWriteResult {
  path: string;
  bytes: number;
}

/* ---- Learning / lessons -------------------------------------------------- */
/** A distilled lesson the agent carries forward. `source` ∈ feedback|reflection|preference. */
export interface Lesson {
  text: string;
  source: string;
  weight: number;
  scope: string;
  created_at: string;
  id?: string;
}

export interface FeedbackResult {
  id: string;
  rating: string;
}

/* ---- Computer use (opt-in browser/desktop control; allowlist + approval) - */
export interface ComputerUseStatus {
  enabled: boolean;
  domain_allowlist: string[];
  action_allowlist: string[];
  isolation: string;
  max_steps: number;
  max_retries: number;
  pending_approvals: number;
}

/** A human-in-the-loop approval gate for a sensitive/destructive action. */
export interface Approval {
  id: string;
  run_id: string;
  action_json: string; // JSON of the proposed Action
  reason: string;
  status: string; // pending | approved | denied
  created_at: string;
}

/* ---- Agents -------------------------------------------------------------- */
export interface DynamicAgent {
  name: string;
  description: string;
  provider?: string;
  model?: string;
}

export interface AgentsResponse {
  builtin: string[];
  dynamic: DynamicAgent[];
}
