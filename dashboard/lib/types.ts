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
