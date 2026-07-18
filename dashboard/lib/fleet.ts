// Types + formatting helpers for the Fleet page (GET /fleet, /fleet/usage,
// /fleet/probe, /fleet/nodes). Scoped to that page — nothing else imports this.
//
// THE RULE THIS FILE EXISTS TO ENFORCE: a metric we could not read is `null`,
// and null renders as an em dash with the reason attached. Never 0. Every
// numeric accessor here returns `number | null`, never a zero fallback.

import type { Tone } from "@/components/ui";

/* -------------------------------------------------------------------------- */
/*  Wire shapes                                                                */
/* -------------------------------------------------------------------------- */

/** Probe outcome. `not-probeable` = we know it exists but cannot reach it. */
export type FleetStatus = "online" | "offline" | "not-probeable" | "unknown";

/** Server flavour. Kept open-ended — the daemon may learn new kinds. */
export type FleetKind = "ollama" | "vllm" | "litellm" | "openai" | string;

export interface FleetNode {
  id: string;
  label?: string | null;
  base_url: string;
  kind: FleetKind;
  /** Where this node came from: "settings", "manual", "proxy", … */
  source?: string | null;
  /** Set on children discovered behind a proxy. */
  parent_id?: string | null;
  /** Proxy-facing name, e.g. "brain" / "coder" / "frontier". */
  alias?: string | null;
  routable?: boolean | null;
  tool_use?: boolean | null;
}

/**
 * Normalised serving metrics. EVERY field is nullable: an absent field means
 * "not read", which the UI must render as an em dash.
 * KV-cache usage is a FRACTION (0..1) — vLLM's own help text says
 * "1 means 100 percent usage".
 *
 * Both spellings of each field are declared because the daemon's NodeMetrics
 * uses the short names (`requests_running`, `kv_cache_usage`) while the raw
 * Prometheus series they come from use the long ones. Accepting both means a
 * rename on either side degrades to "not reported" instead of to a wrong
 * number — and the accessors below are the only thing that reads them.
 */
export interface FleetMetrics {
  requests_running?: number | null;
  requests_waiting?: number | null;
  num_requests_running?: number | null;
  num_requests_waiting?: number | null;
  kv_cache_usage?: number | null;
  kv_cache_usage_perc?: number | null;
  /** Older vLLM builds name it gpu_cache_usage_perc — accepted as an alias. */
  gpu_cache_usage_perc?: number | null;
  prompt_tokens_total?: number | null;
  generation_tokens_total?: number | null;
  prefix_cache_queries_total?: number | null;
  prefix_cache_hits_total?: number | null;
  prefix_cache_hit_rate?: number | null;
  num_preemptions_total?: number | null;
}

/** Derived per-second rates (counter deltas between two samples). */
export interface FleetRates {
  generation_tps?: number | null;
  prompt_tps?: number | null;
  prefix_cache_hit_rate?: number | null;
  /** Seconds between the two samples the rates were derived from. */
  window_seconds?: number | null;
  window_s?: number | null;
  /** The window spanned a restart, so no counter-derived rate is meaningful. */
  counter_reset?: boolean | null;
}

export interface FleetModel {
  id: string;
  /** Ollama: context_length. vLLM: max_model_len. Either may be absent. */
  context_length?: number | null;
  max_model_len?: number | null;
  /** Resident VRAM. The daemon's ModelEntry calls it vram_bytes. */
  size_vram?: number | null;
  vram_bytes?: number | null;
  size?: number | null;
  size_bytes?: number | null;
  quantization?: string | null;
  parameter_size?: string | null;
  loaded?: boolean | null;
  /** Ollama keep-alive deadline; year-2318 values mean "pinned forever". */
  expires_at?: string | null;
  /** vLLM's underlying checkpoint, e.g. deepseek-ai/DeepSeek-V4-Flash-DSpark. */
  root?: string | null;
  hf_id?: string | null;
}

/** LiteLLM /health rollup, as reported BY THE PROXY (secondhand evidence). */
export interface ProxyHealth {
  healthy?: number | null;
  unhealthy?: number | null;
  errors?: string[] | null;
}

export interface NodeSnapshot {
  node: FleetNode;
  status: FleetStatus;
  /** How we know: "direct" (we probed it) or "proxy" (someone told us). */
  evidence?: string | null;
  latency_ms?: number | null;
  error?: string | null;
  /** Actionable next step. May be a bare string or {text, commands}. */
  hint?: string | FleetHint | null;
  /** Shell commands the user can copy (also accepted inside `hint`). */
  commands?: string[] | null;
  metrics_supported?: boolean | null;
  /** Why metrics are missing — the text that replaces a zero. */
  metrics_reason?: string | null;
  metrics?: FleetMetrics | null;
  rates?: FleetRates | null;
  models?: FleetModel[] | null;
  /**
   * Topology children. ON THE WIRE these are node IDS (the daemon registers a
   * proxy's upstreams as first-class nodes and returns the whole fleet FLAT);
   * `buildFleetTree` resolves them into the snapshots typed here. Read this
   * field only on a tree the builder produced.
   */
  children?: NodeSnapshot[] | null;
  proxy_health?: ProxyHealth | null;
  /**
   * The proxy's per-child verdict from /health ("healthy"/"unhealthy"/
   * "unknown"), lifted out of the wire's `proxy_health` string by
   * `buildFleetTree`. "unknown" means the proxy declined to say — NOT unhealthy.
   */
  proxy_verdict?: string | null;
  /** Epoch seconds (the sampler's clock), or an ISO string on older builds. */
  sampled_at?: number | string | null;
}

export interface FleetHint {
  text?: string | null;
  commands?: string[] | null;
}

export interface FleetSampling {
  active: boolean;
  /** Poll interval the daemon is sampling at, in seconds. */
  interval?: number | null;
  /** Seconds until sampling idles back down without another page view. */
  lease_expires_in?: number | null;
}

/**
 * Where coding work goes. `effective` is resolved LIVE by the daemon, so a
 * target pointing at a deleted or unreachable node says so instead of merely
 * looking configured — `why` is the plain-English answer we render.
 */
export interface FleetCodeRoute {
  enabled?: boolean | null;
  /** "provider:model", e.g. "fleet-vllm:deepseek-v4-flash-dspark". */
  target?: string | null;
  task_classes?: string[] | null;
  effective?: {
    provider?: string | null;
    model?: string | null;
    /** null = the daemon could not determine availability. */
    available?: boolean | null;
    circuit?: string | null;
    tool_use?: boolean | null;
    why?: string | null;
  } | null;
}

export interface FleetResponse {
  nodes: NodeSnapshot[];
  sampling?: FleetSampling | null;
  code_route?: FleetCodeRoute | null;
  /** Non-empty when the fleet stack itself faulted; the route never 500s. */
  error?: string | null;
}

/** GET /fleet/usage — local vs cloud split, with the basis for the estimate. */
export interface FleetUsage {
  days?: number | null;
  local_tokens?: number | null;
  cloud_tokens?: number | null;
  cloud_cost_usd?: number | null;
  /** What the LOCAL tokens would have cost on the comparison model. */
  est_avoided_usd?: number | null;
  /** The baseline the estimate is priced against — required to show any $. */
  comparison_provider?: string | null;
  comparison_model?: string | null;
  /** One sentence naming the basis, straight from the daemon. */
  basis?: string | null;
  by_node?: {
    node_id: string;
    label?: string | null;
    provider?: string | null;
    models?: string[] | null;
    input_tokens?: number | null;
    output_tokens?: number | null;
    runs?: number | null;
    est_avoided_usd?: number | null;
  }[];
}

/**
 * POST /fleet/probe — what we found at a URL, before the user commits to it.
 * Always 200: an unreachable endpoint comes back with its honest snapshot and
 * the error, and the user may still save the node.
 */
export interface FleetProbe {
  kind?: FleetKind | null;
  /** Only meaningful when `kind` is "unknown" — the last transport failure. */
  reason?: string | null;
  snapshot?: NodeSnapshot | null;
  /** Upstreams a LiteLLM proxy named. Every other kind returns []. */
  children?: FleetNode[] | null;
  error?: string | null;
}

/* -------------------------------------------------------------------------- */
/*  Null-honest accessors                                                      */
/* -------------------------------------------------------------------------- */

/**
 * A finite number, or null. The ONLY numeric coercion this page uses —
 * `?? 0` is banned here on purpose (a zero we invented is a lie).
 */
export function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Running requests, or null when unread. */
export function running(m: FleetMetrics | null | undefined): number | null {
  return num(m?.requests_running) ?? num(m?.num_requests_running);
}

/** Queued requests, or null when unread. */
export function waiting(m: FleetMetrics | null | undefined): number | null {
  return num(m?.requests_waiting) ?? num(m?.num_requests_waiting);
}

/** KV-cache utilisation as a 0..1 fraction, accepting any of the metric names. */
export function kvCache(m: FleetMetrics | null | undefined): number | null {
  return (
    num(m?.kv_cache_usage) ??
    num(m?.kv_cache_usage_perc) ??
    num(m?.gpu_cache_usage_perc)
  );
}

/** Generation throughput, or null when we have no two samples to derive it. */
export function genTps(r: FleetRates | null | undefined): number | null {
  return num(r?.generation_tps);
}

/* -------------------------------------------------------------------------- */
/*  Formatting                                                                 */
/* -------------------------------------------------------------------------- */

/** The em dash every unread metric renders as. */
export const DASH = "—";

/**
 * Byte formatter copied verbatim from dashboard/app/page.tsx (fmtBytes, ~line
 * 173) so the two pages agree; it is not exported there.
 */
export function fmtBytes(b?: number): string {
  if (!b || b <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let n = b;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(i > 0 && n < 10 ? 1 : 0)} ${u[i]}`;
}

/** Context window: 1048576 → "1M", 32768 → "32k". Null in, null out. */
export function fmtCtx(v: number | null | undefined): string | null {
  const n = num(v);
  if (n === null || n <= 0) return null;
  if (n >= 1_000_000) {
    const m = n / 1_048_576;
    return `${trim(m)}M`;
  }
  if (n >= 1024) return `${trim(n / 1024)}k`;
  return String(n);
}

/** Drop a trailing ".0" so 1.0 → "1" but 1.5 stays "1.5". */
function trim(n: number): string {
  const s = n.toFixed(1);
  return s.endsWith(".0") ? s.slice(0, -2) : s;
}

/** 0.42 → "42%". Null in, null out — never "0%" for an unread cache. */
export function fmtPct(frac: number | null): string | null {
  if (frac === null) return null;
  return `${Math.round(frac * 100)}%`;
}

/** Throughput with one decimal below 100 tok/s, integer above. */
export function fmtTps(v: number | null): string | null {
  if (v === null) return null;
  return v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(1);
}

export function fmtLatency(v: number | null | undefined): string | null {
  const n = num(v);
  if (n === null) return null;
  return n >= 1000 ? `${(n / 1000).toFixed(1)}s` : `${Math.round(n)}ms`;
}

export function fmtCount(v: number | null): string | null {
  return v === null ? null : v.toLocaleString();
}

/** Token counts for the local-vs-cloud strip. Unknown stays an em dash. */
export function fmtTokens(v: number | null | undefined): string {
  const n = num(v);
  return n === null ? DASH : Math.round(n).toLocaleString();
}

/** Dollars. Sub-cent amounts keep 4 decimals so they can't read as "$0.00". */
export function fmtUsd(v: number | null | undefined): string {
  const n = num(v);
  if (n === null) return DASH;
  const digits = n > 0 && n < 0.01 ? 4 : 2;
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** "http://100.87.42.62:8003/v1" → "100.87.42.62:8003". */
export function hostOf(baseUrl: string | null | undefined): string {
  if (!baseUrl) return DASH;
  try {
    return new URL(baseUrl).host;
  } catch {
    return baseUrl.replace(/^https?:\/\//, "").replace(/\/.*$/, "");
  }
}

/** Display name for a node: its label, else its id. */
export function nodeName(n: FleetNode): string {
  return (n.label || "").trim() || n.id;
}

const KIND_LABELS: Record<string, string> = {
  ollama: "Ollama",
  vllm: "vLLM",
  litellm: "LiteLLM",
  openai: "OpenAI-compat",
  "openai-compat": "OpenAI-compat",
  unknown: "unknown",
};

export function kindLabel(kind: FleetKind | null | undefined): string {
  if (!kind) return "unknown";
  return KIND_LABELS[String(kind).toLowerCase()] ?? String(kind);
}

const KIND_TONES: Record<string, Tone> = {
  ollama: "violet",
  vllm: "cyan",
  litellm: "amber",
  openai: "slate",
  "openai-compat": "slate",
};

export function kindTone(kind: FleetKind | null | undefined): Tone {
  return KIND_TONES[String(kind ?? "").toLowerCase()] ?? "slate";
}

/**
 * Status colour. `statusTone` in ui.tsx doesn't know these words (it would map
 * them all to slate), so the Fleet vocabulary gets its own map.
 */
export function fleetTone(status: FleetStatus | null | undefined): Tone {
  switch (status) {
    case "online":
      return "green";
    case "offline":
      return "red";
    case "not-probeable":
      return "amber";
    default:
      return "slate";
  }
}

/** Human label for a status pill. */
export function statusLabel(status: FleetStatus | null | undefined): string {
  return status ?? "unknown";
}

/* -------------------------------------------------------------------------- */
/*  Hints                                                                      */
/* -------------------------------------------------------------------------- */

/**
 * Normalise the hint, which the daemon may send as a plain string or as
 * {text, commands}. Commands supplied at the snapshot level are merged in.
 */
export function normHint(
  hint: string | FleetHint | null | undefined,
  commands?: string[] | null,
): { text: string | null; commands: string[] } {
  const cmds: string[] = [];
  let text: string | null = null;
  if (typeof hint === "string") text = hint.trim() || null;
  else if (hint && typeof hint === "object") {
    text = (hint.text || "").trim() || null;
    if (Array.isArray(hint.commands)) cmds.push(...hint.commands);
  }
  if (Array.isArray(commands)) cmds.push(...commands);
  return { text, commands: cmds.filter((c) => typeof c === "string" && c.trim()) };
}

/* -------------------------------------------------------------------------- */
/*  Ollama keep-alive countdown                                                */
/* -------------------------------------------------------------------------- */

export type Unload =
  | { kind: "pinned" }
  | { kind: "expired" }
  | { kind: "in"; text: string };

/** Anything further out than this is a keep-alive sentinel, not a deadline. */
const PINNED_AFTER_MS = 365 * 24 * 60 * 60 * 1000;

/**
 * Countdown until Ollama unloads a model. Ollama writes year-2318 timestamps
 * for keep-alive-forever, so a far-future date is reported as "pinned" rather
 * than as a nonsense 292-year countdown. Unparseable → null (say nothing).
 */
export function unloadIn(
  expiresAt: string | null | undefined,
  now: number = Date.now(),
): Unload | null {
  if (!expiresAt) return null;
  const t = Date.parse(expiresAt);
  if (Number.isNaN(t)) return null;
  const delta = t - now;
  if (delta > PINNED_AFTER_MS) return { kind: "pinned" };
  if (delta <= 0) return { kind: "expired" };
  return { kind: "in", text: fmtDuration(delta) };
}

/** 74000 → "1m 14s"; 5_400_000 → "1h 30m". */
export function fmtDuration(ms: number): string {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/* -------------------------------------------------------------------------- */
/*  Code route                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * One line describing where coding work goes. Prefers the daemon's own `why`
 * (it is resolved live and already plain English) and never invents a
 * reassuring sentence when the daemon didn't send one.
 */
export function codeRouteText(
  route: FleetCodeRoute | null | undefined,
): string | null {
  if (!route) return null;
  const why = (route.effective?.why || "").trim();
  if (why) return why;
  const target = (route.target || "").trim();
  if (!target) return null;
  return route.enabled ? `coding work goes to ${target}` : `code routing is off`;
}

/* -------------------------------------------------------------------------- */
/*  Wire → view: nesting the flat node list                                    */
/* -------------------------------------------------------------------------- */

/** Copy a model row with the daemon's field names mapped onto the view names. */
function alignModel(m: FleetModel): FleetModel {
  return {
    ...m,
    size_vram: num(m.size_vram) ?? num(m.vram_bytes),
    size: num(m.size) ?? num(m.size_bytes),
    root: m.root || m.hf_id || null,
  };
}

/** Child IDs a snapshot named directly (the wire sends ids, not objects). */
function childIds(snap: NodeSnapshot): string[] {
  const raw = (snap as { children?: unknown }).children;
  if (!Array.isArray(raw)) return [];
  return raw
    .map((c) =>
      typeof c === "string" ? c : String((c as NodeSnapshot)?.node?.id ?? ""),
    )
    .filter((id) => !!id);
}

/**
 * Nest the flat `/fleet` node list into roots + their topology children.
 *
 * The daemon registers a LiteLLM proxy's upstreams as first-class nodes, so
 * they arrive ALONGSIDE their parent. Rendered flat, the user's fleet would
 * read as four unreachable boxes rather than one proxy fronting four aliases —
 * the topology is the story on this page.
 *
 * Also does the two shape fixups the card can't: model field aliases, and the
 * per-child `proxy_health` STRING (a /health verdict) lifted into
 * `proxy_verdict`, leaving the parent's `proxy_health` as counts derived only
 * from children the proxy actually ruled on. A child it declined to rule on is
 * counted as neither healthy nor unhealthy.
 *
 * A child whose parent is absent from this response is promoted to a root
 * rather than dropped: a node we cannot place is still a node we know about.
 */
export function buildFleetTree(nodes: NodeSnapshot[] | null | undefined): NodeSnapshot[] {
  const flat: NodeSnapshot[] = [];
  const byId = new Map<string, NodeSnapshot>();
  // Keyed by node id, not by index: rows without an id (or duplicates) are
  // dropped above, so positional lookup would silently pair the wrong raw row.
  const raws = new Map<string, NodeSnapshot>();
  for (const raw of nodes ?? []) {
    const id = raw?.node?.id;
    if (!id || byId.has(id)) continue;
    raws.set(id, raw);
    // On the wire a CHILD's proxy_health is the /health verdict string while a
    // parent's is a rollup object, so this is read as unknown and split.
    const health: unknown = raw.proxy_health;
    const verdict = typeof health === "string" ? health : null;
    const snap: NodeSnapshot = {
      ...raw,
      models: (raw.models ?? []).map(alignModel),
      // Rebuilt below; the wire value is a list of ids, not snapshots.
      children: [],
      proxy_verdict: verdict ?? raw.proxy_verdict ?? null,
      proxy_health: verdict ? null : ((health as ProxyHealth | null) ?? null),
    };
    flat.push(snap);
    byId.set(id, snap);
  }

  const claimed = new Set<string>();
  flat.forEach((snap) => {
    const wanted = new Set<string>(childIds(raws.get(snap.node.id) ?? snap));
    for (const other of flat) {
      if (other.node.parent_id === snap.node.id) wanted.add(other.node.id);
    }
    const kids: NodeSnapshot[] = [];
    for (const id of wanted) {
      const child = byId.get(id);
      if (!child || child === snap || claimed.has(id)) continue;
      kids.push(child);
      claimed.add(id);
    }
    snap.children = kids;
    const ruled = kids.filter((c) => !!c.proxy_verdict && c.proxy_verdict !== "unknown");
    snap.proxy_health = ruled.length
      ? {
          healthy: ruled.filter((c) => c.proxy_verdict === "healthy").length,
          unhealthy: ruled.filter((c) => c.proxy_verdict === "unhealthy").length,
        }
      : snap.proxy_health;
  });

  return flat.filter((s) => !claimed.has(s.node.id));
}

/** Every snapshot in a tree, flattened (series bookkeeping, counters). */
export function flattenTree(roots: NodeSnapshot[]): NodeSnapshot[] {
  const out: NodeSnapshot[] = [];
  const walk = (list: NodeSnapshot[]) => {
    for (const s of list) {
      out.push(s);
      if (s.children?.length) walk(s.children);
    }
  };
  walk(roots);
  return out;
}

/* -------------------------------------------------------------------------- */
/*  tok/s history (client-side, for the sparkline)                             */
/* -------------------------------------------------------------------------- */

/** How many poll ticks of tok/s the sparkline keeps. At 2s ≈ 2 minutes. */
export const SPARK_POINTS = 60;

/** Series plus the sample stamp it ends at, so a re-poll can't double-count. */
export interface Series {
  points: (number | null)[];
  /** `sampled_at` of the last point appended. */
  at: number | string | null;
}

/**
 * Append one sample per node, keeping the last SPARK_POINTS.
 *
 * Two honesty constraints:
 * - a node we could not read contributes `null`, which the Sparkline draws as
 *   a GAP — a flat zero line would claim the server was idle when we simply
 *   don't know;
 * - a snapshot we have ALREADY charted is skipped. The page polls every 2s but
 *   the daemon samples at 2s only while the lease is active (30s otherwise) and
 *   backs a failing node off for minutes, so appending on every tick would
 *   stretch one reading into a plateau of readings that were never taken.
 */
export function pushRates(
  prev: Record<string, Series>,
  nodes: NodeSnapshot[],
): Record<string, Series> {
  const next: Record<string, Series> = {};
  for (const snap of nodes) {
    const id = snap.node.id;
    const seen = prev[id];
    const at = snap.sampled_at ?? null;
    if (seen && at !== null && seen.at === at) {
      next[id] = seen; // same sample — nothing new was measured
      continue;
    }
    next[id] = {
      points: [...(seen?.points ?? []), genTps(snap.rates)].slice(-SPARK_POINTS),
      at,
    };
  }
  return next; // nodes that vanished drop their history with them
}

/**
 * True once a series holds enough to draw: at least one real reading, and at
 * least two slots (Sparkline needs a second point to have a line at all, so a
 * lone reading would render an empty box under a "tok/s history" heading).
 */
export function hasSignal(series: (number | null)[] | undefined): boolean {
  return !!series && series.length >= 2 && series.some((p) => p !== null);
}
