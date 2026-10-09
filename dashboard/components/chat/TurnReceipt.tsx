"use client";

/**
 * The TURN RECEIPT — a quiet one-line strip under an assistant reply that makes
 * the reply ACCOUNTABLE: who actually answered, whether that is what was asked
 * for, which tools ran, which armed tools the engine refused, and which files
 * this turn created.
 *
 * WHY IT EXISTS: a mock provider once answered a real chat with a fabricated
 * "Done. Wrote RESULT.md" and NOTHING in the UI disclosed it — the "answered
 * by X" chip suppressed itself on the default route. This strip renders SERVER
 * truth (the daemon's `route` object + the tool ledger), never client
 * inference, and the dishonesty cases are deliberately NOT hidden behind the
 * expand:
 *   - mock answered        → strongest wording, amber, always visible
 *   - failover / mismatch  → "answered by X — …", amber, always visible
 *   - denied tools         → "N blocked" count in the collapsed line
 *   - envelope adapted     → quiet zinc line, always visible, NEVER amber
 *     (v1.202.0 — the user's own measured local model being fitted is
 *     user-configured-hardware honesty, the prompted-tools/auto-tier quiet
 *     class, not a substitution warning)
 * A turn with literally nothing to say (no route, no tools, no denials, no
 * files) renders NOTHING — zero-noise on trivial turns is a feature, not an
 * omission.
 *
 * WIRE CONTRACT (routes/chat.py "route" object, v1.165.0): `requested` is ""
 * — not undefined — on chat's default path, so the mismatch check must treat
 * empty as "didn't ask". The reason vocabulary is "explicit" | "default" |
 * "failover" | "prompted-tools" | "auto-tier" | "local-oracle" | "mock".
 * "prompted-tools" means the CHOSEN adapter kept the request via the fenced
 * scaffold — same provider served, so it stays quiet; a capability REROUTE is
 * labelled "failover" by the router itself and therefore warns here.
 * Since v1.228.0 a failover also carries `from` (the provider that failed)
 * and `why` (the router's derived word: "http 500" | "timeout" |
 * "unreachable" | "interrupted" | "transient error" | "error"), so the chip
 * can say "answered by Claude Code — fleet-rtx6000ada returned HTTP 500"
 * instead of a bare "failover" that never names the user's own endpoint.
 * Since v1.314.0 every provider a user READS goes through providerDisplay
 * ("claude-cli" → "Claude Code"); a custom endpoint id has no friendly name
 * and passes through unchanged. The raw ids stay a record: a title on the
 * collapsed line and the warning chip, and a quiet "(claude-cli)" in the
 * expanded detail.
 */

import { useId, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Ban,
  ChevronDown,
  ChevronRight,
  FileText,
  Gauge,
  Loader2,
  Route as RouteIcon,
  ShieldAlert,
  ShieldCheck,
  Timer,
  Undo2,
  Wrench,
} from "lucide-react";
import { LIST_PRICE_TITLE, type TurnUsage } from "@/lib/types";
import { providerDisplay } from "@/lib/onboarding";

/** The capability envelope's adaptation disclosure (v1.202.0): the daemon
 *  bent this turn to fit a measured-weak model — e.g. narrowed the auto-tool
 *  menu ("tool_cap:4"). QUIET by design: this is user-configured-hardware
 *  honesty (the user's own local model, measured on their own machine), not a
 *  substitution or a failure — it belongs with the prompted-tools/auto-tier
 *  quiet class in the tone rules above, never amber. */
export interface TurnAdapted {
  model?: string;
  changes?: string[];
}

/** One "context.blocked" notice folded into the receipt (v1.298.0): the
 *  daemon kept `count` passages flagged as injection out of the context,
 *  from `source` (a tool or channel). */
export interface TurnBlocked {
  source?: string;
  count?: number;
}

/** The blocked rows worth saying: a positive count, with its source (or
 *  "context" when the daemon named none). Crosses a JSON boundary, so the
 *  shape is checked at runtime — a row with no count says nothing. */
export function blockedRows(
  blocked: TurnBlocked[] | null | undefined,
): { source: string; count: number }[] {
  if (!Array.isArray(blocked)) return [];
  const out: { source: string; count: number }[] = [];
  for (const b of blocked) {
    if (!b || typeof b !== "object") continue;
    const n =
      typeof b.count === "number" && Number.isFinite(b.count) && b.count > 0
        ? Math.floor(b.count)
        : 0;
    if (n === 0) continue;
    const source = typeof b.source === "string" ? b.source.trim() : "";
    out.push({ source: source || "context", count: n });
  }
  return out;
}

/**
 * ONE renderer per wire token (the draftFromFence lesson: two renderers of
 * the same source drift, and a mismatch between the receipt and the progress
 * line would read as two different stories about the same turn). This is THE
 * translation from the daemon's `adapted.changes` vocabulary to words — the
 * receipt uses it below, and chat's step narration imports the SAME function.
 * An UNKNOWN token renders verbatim: a new adaptation kind the daemon learns
 * should read oddly here, not silently vanish (the PHASE_LABEL rule).
 */
export function wordChange(token: string): string {
  const t = token.trim();
  const cap = /^tool_cap:(\d+)$/.exec(t);
  // v1.232.0 (audit U8): "3 tools max" read as a limit the user set; the cap
  // is the envelope fitting the user's own measured local model.
  if (cap) return `capped at ${cap[1]} tools for this local model`;
  if (t === "decomposed") return "running step-by-step";
  if (t === "step_retry") return "retried a failed step"; // v1.203.0 (C3)
  return t;
}

/**
 * Human wording for the quiet adapted line, or null when there is nothing to
 * disclose. Token wording is delegated to {@link wordChange} — never inline a
 * second copy of that mapping here.
 */
export function adaptedLabel(
  adapted: TurnAdapted | null | undefined,
): string | null {
  if (!adapted) return null;
  const changes = (adapted.changes ?? []).filter(
    (c) => typeof c === "string" && c.trim().length > 0,
  );
  if (changes.length === 0) return null;
  const worded = changes.map(wordChange);
  const who =
    typeof adapted.model === "string" && adapted.model.trim()
      ? ` to ${adapted.model.trim()}`
      : "";
  return `adapted${who}: ${worded.join(", ")}`;
}

/** Server truth about who served the turn (daemon's routing decision). */
export interface TurnRoute {
  /** What the user/client asked for, when they asked at all. */
  requested?: string;
  /** The provider that actually produced the answer. */
  provider: string;
  model?: string;
  /** Router's own word for why: "default" | "explicit" | "failover" | … */
  reason?: string;
  /** v1.228.0: on a failover, the provider that FAILED ("" otherwise). */
  from?: string;
  /** v1.228.0: on a failover, the router's derived reason for that failure. */
  why?: string;
  /** v1.263.0: the reasoning level the router actually applied on the wire
   *  ("low" | "medium" | "high"); "" or absent when none was asked for or the
   *  serving model offers none — the receipt never names a level that did
   *  not reach the model. */
  reasoning?: string;
}

/** One tool step with its duration (v1.323.0) — `useChatStream`'s TurnStep.
 *  `ok` null = never said; `ms` null = a start or an end was not seen. */
export interface ReceiptStep {
  name: string;
  ok: boolean | null;
  ms: number | null;
}

/** Client-clock timing of the turn (v1.323.0) — `useChatStream`'s TurnTiming. */
export interface ReceiptTiming {
  startedAt: number;
  firstTokenAt: number | null;
  endedAt: number;
}

/** A duration in words: "0.3 s", "12 s", "2 min 5 s". Null for a value that
 *  is not a finite, non-negative number. */
export function secondsText(ms: number | null | undefined): string | null {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return null;
  const s = ms / 1000;
  if (s < 10) return `${s.toFixed(1)} s`;
  if (s < 60) return `${Math.round(s)} s`;
  const whole = Math.round(s);
  const min = Math.floor(whole / 60);
  const rest = whole % 60;
  return rest ? `${min} min ${rest} s` : `${min} min`;
}

/**
 * The expanded receipt's speed line (v1.323.0): "First word after 1.2 s ·
 * 42 tokens/s". The rate is OUTPUT TOKENS over the time from the first word
 * to the end — only when the token count is known and a first word came;
 * never a guessed words/s. Null when there is nothing honest to say.
 */
export function speedLine(
  timing: ReceiptTiming | null | undefined,
  outputTokens?: number | null,
): string | null {
  if (!timing) return null;
  const { startedAt, firstTokenAt, endedAt } = timing;
  if (typeof startedAt !== "number" || !Number.isFinite(startedAt)) return null;
  const bits: string[] = [];
  if (typeof firstTokenAt === "number" && Number.isFinite(firstTokenAt)) {
    const first = secondsText(firstTokenAt - startedAt);
    if (first) bits.push(`First word after ${first}`);
    const span = (endedAt - firstTokenAt) / 1000;
    const out = fin(outputTokens);
    if (out != null && out > 0 && Number.isFinite(span) && span > 0) {
      const rate = out / span;
      bits.push(`${rate >= 10 ? Math.round(rate) : rate.toFixed(1)} tokens/s`);
    }
  }
  return bits.length ? bits.join(" · ") : null;
}

/** The steps worth a chip: a non-blank name; ok/ms kept only when real. */
function stepRows(steps: ReceiptStep[] | null | undefined): ReceiptStep[] {
  if (!Array.isArray(steps)) return [];
  const out: ReceiptStep[] = [];
  for (const st of steps) {
    if (!st || typeof st !== "object") continue;
    if (typeof st.name !== "string" || !st.name.trim()) continue;
    out.push({
      name: st.name,
      ok: typeof st.ok === "boolean" ? st.ok : null,
      ms: fin(st.ms),
    });
  }
  return out;
}

export interface TurnReceiptProps {
  /** May be absent on messages persisted before the route object existed. */
  route?: TurnRoute | null;
  /** The envelope bent this turn (v1.202.0) — absent/null renders nothing. */
  adapted?: TurnAdapted | null;
  /** Tools that actually executed this turn. */
  toolsUsed?: string[];
  /** Armed tools the engine refused to run. */
  deniedTools?: string[];
  /** v1.282.0: the preference sentences this turn kept — said on the line,
   *  in the user's own words, so a capture is never a silent write. */
  remembered?: string[];
  /** ABSOLUTE paths of files this turn created or edited. */
  documents?: string[];
  /** v1.298.0: the turn's trust posture from the done frame — "low" when the
   *  turn ran under low trust (it could not change memory, settings, agents
   *  or skills). OPTIONAL on the wire; absent renders nothing. */
  trust?: string | null;
  /** v1.298.0: the daemon's reason for low trust, said on the quiet line. */
  trustReason?: string | null;
  /** v1.298.0: the daemon's one-line note ("low trust: 4 tools kept away"),
   *  shown beside the reason when present. */
  trustNote?: string | null;
  /** v1.298.0: suspicious passages the daemon kept OUT of the context this
   *  turn, per source. Absent/empty renders nothing. */
  blocked?: TurnBlocked[] | null;
  /** Token accounting for the turn. v1.300.0: with cache reads it adds
   *  "cached N%" to the collapsed line, and with `cost_usd` (the LIST-PRICE
   *  EQUIVALENT of a subscription turn) "~$x.xx list". Absent → nothing. */
  usage?: TurnUsage | null;
  /** 0..1 context pressure, when known. */
  contextPct?: number | null;
  /** Wired by the coordinator to the DocPreview rail. Receives the FULL path. */
  onOpenDocument?: (path: string) => void;
  /**
   * v1.168.0 — "Undo this write" under the receipt's file chip. The caller
   * joins the undo journal to each document's absolute path and returns the
   * match, or null/undefined when there is none — an UNMATCHED file shows no
   * undo at all (never a guess). A matched not-undoable row renders disabled
   * with the honest reason as its title. Requires `onUndo` too.
   */
  undoFor?: (path: string) => ReceiptUndoState | null | undefined;
  /** v1.323.0: the turn's tool steps in order, with durations — the expanded
   *  tool chips then say "read_file · 0.3 s" and mark a failed one. Absent →
   *  today's names-only chips. Never a reason to render on its own. */
  steps?: ReceiptStep[] | null;
  /** v1.323.0: client-clock timing — the expanded view's speed line. Never
   *  a reason to render on its own (a trivial turn stays silent). */
  timing?: ReceiptTiming | null;
  /** v1.323.0: output tokens of the turn, for the speed line's tokens/s
   *  (falls back to `usage.output_tokens`). */
  outputTokens?: number | null;
  /**
   * Performs the undo (the caller owns the explicit confirm + POST + refresh).
   * A rejection is shown inline under the file row — a failed undo must never
   * look like it happened.
   */
  onUndo?: (actionId: string, path: string) => void | Promise<void>;
}

/** The journal row matched to one of this turn's documents. */
export interface ReceiptUndoState {
  actionId: string;
  undoable: boolean;
  /** Honest reason a matched row cannot be undone ("already undone"…). */
  reason?: string;
  kind?: string;
}

/**
 * Last path segment — handles BOTH separators, because the daemon reports
 * Windows paths with backslashes while workspace-relative ones use slashes.
 * Falls back to the raw string rather than fabricating a name.
 */
export function docBasename(path: string): string {
  const parts = path.split(/[/\\]/).filter((p) => p.length > 0);
  return parts.length > 0 ? parts[parts.length - 1] : path;
}

/**
 * The honesty check: the amber wording the collapsed line must carry, or null
 * when the turn was served as asked. Mock outranks everything — "no real model
 * ran" is the strongest claim and the exact fabrication case this component
 * exists for; it must win even when the router ALSO reports a failover.
 * A failover names who was asked for when that is known (`requested` is ""
 * on the default route — the router had nobody specific to fail over FROM
 * that the user would recognise). "default"/"explicit"/"prompted-tools"/
 * "auto-tier"/"local-oracle" with the requested provider serving is the
 * quiet path.
 */
/**
 * Plain words for the router's `why` token (v1.228.0). The router speaks in
 * short derived tokens so the event ledger and the notifier stay greppable;
 * the receipt is where a person reads it. Unknown tokens pass through
 * verbatim — never silently dropped, since the token IS the disclosure.
 */
export function wordWhy(why: string | null | undefined): string {
  const w = (why ?? "").trim();
  if (!w) return "";
  const http = /^http\s+(\d{3})$/i.exec(w);
  if (http) return `returned HTTP ${http[1]}`;
  switch (w) {
    case "unreachable":
      return "was unreachable";
    case "timeout":
      return "didn't respond in time";
    case "interrupted":
      return "dropped the connection";
    case "transient error":
      return "hit a transient error";
    case "error":
      return "returned an error";
    default:
      return w;
  }
}

export function routeWarning(route: TurnRoute | null | undefined): string | null {
  if (!route) return null;
  if (route.provider === "mock") return "mock answer — no real model ran";
  // v1.314.0: plain provider names in the words; the ids stay in titles.
  const who = providerDisplay(route.provider);
  if (route.reason === "failover") {
    // v1.228.0: name the provider that FAILED and why, when the router said.
    // This is what makes the DEFAULT route accountable — `requested` is ""
    // there by contract, so `from` is the only way to name the user's own
    // endpoint that was skipped.
    const from = (route.from ?? "").trim();
    if (from && from !== route.provider) {
      const why = wordWhy(route.why);
      return why
        ? `answered by ${who} — ${providerDisplay(from)} ${why}`
        : `answered by ${who} — failover from ${providerDisplay(from)}`;
    }
    return route.requested && route.requested !== route.provider
      ? `answered by ${who} — failover from ${providerDisplay(route.requested)}`
      : `answered by ${who} — failover`;
  }
  if (route.requested && route.requested !== route.provider) {
    return `answered by ${who} — asked for ${providerDisplay(route.requested)}`;
  }
  return null;
}

/** v1.314.0: the raw route ids behind the plain words — a record, kept in a
 *  title: "claude-cli · claude-fable-5 (failover from codex-cli)". */
function rawRoute(rt: TurnRoute): string {
  const bits = [rt.provider, rt.model].filter(Boolean).join(" · ");
  const from = (rt.from ?? "").trim();
  if (from && from !== rt.provider) return `${bits} (failover from ${from})`;
  if (rt.requested && rt.requested !== rt.provider) return `${bits} (asked for ${rt.requested})`;
  return bits;
}

/**
 * The quiet usage words for the collapsed line (v1.300.0), or nulls when the
 * turn carried nothing worth saying. Cache share = cache reads ÷ the turn's
 * TOTAL prompt, rounded, never over 100, said only when something WAS read
 * from cache. Two conventions reach here: the claude-cli adapter reports
 * `input_tokens` as the total (cache included); the raw Anthropic API
 * reports only the UNcached part. So `input_tokens` is the total when it
 * already covers cache read + creation, else the parts are summed.
 * COST, only from `cost_usd` (never inferred): a SUBSCRIPTION turn
 * (`list_price_equivalent: true`) says "~$x list" — "list" and the
 * subscription title belong to it alone; a metered turn says "~$x"; a turn
 * that cost nothing and is not a subscription turn (mock, local: 0.0) says
 * nothing at all. A cost that would round to $0.00 says "<$0.01".
 */
export function usageWords(usage: TurnUsage | null | undefined): {
  cache: string | null;
  cost: string | null;
  list: boolean;
} {
  const read = fin(usage?.cache_read_input_tokens);
  const input = fin(usage?.input_tokens);
  const cost = fin(usage?.cost_usd);
  const made = fin(usage?.cache_creation_input_tokens) ?? 0;
  let cache: string | null = null;
  if (read != null && read > 0) {
    const inp = input ?? 0;
    const total = inp >= read + made ? inp : inp + read + made;
    cache = `cached ${Math.min(100, Math.round((read / total) * 100))}%`;
  }
  const list = usage?.list_price_equivalent === true;
  let costText: string | null = null;
  if (cost != null && (list || cost > 0)) {
    const amount = cost > 0 && cost < 0.005 ? "<$0.01" : `~$${cost.toFixed(2)}`;
    costText = list ? `${amount} list` : amount;
  }
  return { cache, cost: costText, list };
}

function count(n: number, noun: string): string {
  return `${n} ${noun}${n === 1 ? "" : "s"}`;
}

/** A finite, non-negative number or null — the server sometimes persists
 *  gaps; "context NaN%" or "-3 in" is worse than saying nothing. */
function fin(n: number | null | undefined): number | null {
  return typeof n === "number" && Number.isFinite(n) && n >= 0 ? n : null;
}

/** Runtime-string, non-blank — server arrays can carry empty entries and the
 *  props cross a JSON boundary, so the types alone are not a guarantee. */
function names(xs: string[]): string[] {
  return xs.filter((x) => typeof x === "string" && x.trim().length > 0);
}

export function TurnReceipt({
  route,
  adapted,
  toolsUsed = [],
  deniedTools = [],
  remembered = [],
  documents = [],
  trust,
  trustReason,
  trustNote,
  blocked,
  usage,
  contextPct,
  onOpenDocument,
  undoFor,
  onUndo,
  steps,
  timing,
  outputTokens,
}: TurnReceiptProps) {
  const [open, setOpen] = useState(false);
  const [undoingPath, setUndoingPath] = useState<string | null>(null);
  const [undoErr, setUndoErr] = useState<string | null>(null);
  const panelId = useId();

  /** Run the caller's undo; surface a rejection inline instead of swallowing
   *  it — a failed undo must never look like it happened. */
  async function runUndo(actionId: string, path: string) {
    if (!onUndo || undoingPath) return;
    setUndoingPath(path);
    setUndoErr(null);
    try {
      await onUndo(actionId, path);
    } catch (e) {
      setUndoErr(e instanceof Error ? e.message : String(e));
    } finally {
      setUndoingPath(null);
    }
  }

  const tools = names(toolsUsed);
  const denied = names(deniedTools);
  // Dedupe: a file both created and edited this turn is ONE file — "2 files"
  // for one path would be the kind of small lie this strip exists to end.
  const docs = Array.from(new Set(names(documents)));

  const warning = routeWarning(route);
  // A route object with no provider and nothing to warn about (degenerate
  // persisted shapes) carries no accountability fact — treat it as absent.
  const rt = route && (route.provider || warning) ? route : null;
  // The envelope's adaptation note (v1.202.0) — quiet zinc, NEVER amber:
  // this is the user's own configured hardware being fitted, not a
  // substitution (the mock/failover class) — see the tone rules up top.
  const adaptedText = adaptedLabel(adapted);

  // Zero-noise guard: nothing to account for, render nothing at all. A route
  // carrying a WARNING always renders — the warning is the whole point; an
  // adaptation note alone also renders (a bent turn must never be silent).
  const kept = names(remembered);
  // v1.298.0: the trust posture and the context the daemon kept out. Both
  // OPTIONAL on the wire this wave — absent fields add nothing to the line.
  const lowTrust = trust === "low";
  const blockedOut = blockedRows(blocked);
  // v1.300.0: the turn's cache share and list-price cost — quiet, optional.
  const used = usageWords(usage);
  if (
    !used.cache &&
    !used.cost &&
    !rt &&
    !tools.length &&
    !denied.length &&
    !docs.length &&
    !adaptedText &&
    !kept.length &&
    !lowTrust &&
    !blockedOut.length
  ) {
    return null;
  }

  const mismatch = !!rt?.requested && rt.requested !== rt.provider;
  const inTok = fin(usage?.input_tokens);
  const outTok = fin(usage?.output_tokens);
  const ctx = fin(contextPct);
  // v1.323.0: the expanded view's step durations and speed line. Neither
  // reaches the zero-noise guard above — timing alone is not a receipt.
  const stepChips = stepRows(steps);
  const speed = speedLine(timing, fin(outputTokens) ?? outTok);

  // The collapsed line, assembled as parts joined by "·". The warning chip is
  // its own styled element so it reads as a WARNING, not just another word.
  const parts: ReactNode[] = [];
  if (rt) {
    parts.push(
      warning ? (
        <span
          key="who"
          title={rawRoute(rt)}
          className="inline-flex min-w-0 items-center gap-1 rounded-full border border-amber-500/25 bg-amber-500/[0.06] px-1.5 py-px font-medium text-amber-300"
        >
          <AlertTriangle size={10} className="shrink-0" />
          {warning}
        </span>
      ) : (
        <span key="who" className="text-zinc-400" title={rawRoute(rt)}>
          {providerDisplay(rt.provider)}
        </span>
      ),
    );
  }
  if (adaptedText) {
    // Visible WITHOUT expanding (an invisible adaptation is a silent
    // degrade), but in the QUIET class — plain zinc, no icon, no chip.
    parts.push(
      <span key="adapted" className="text-zinc-500">
        {adaptedText}
      </span>,
    );
  }
  if (rt?.reasoning) {
    // v1.263.0: the level that reached the model, in the quiet class — a
    // choice the user made, honoured; never a warning.
    parts.push(
      <span key="reasoning" data-testid="turn-reasoning" className="text-zinc-500">
        reasoning {rt.reasoning}
      </span>,
    );
  }
  if (tools.length > 0) {
    parts.push(<span key="tools">{count(tools.length, "tool")}</span>);
  }
  if (denied.length > 0) {
    // A silent denial invisible until expand would repeat the original bug —
    // the count is on the line, in warning colour.
    parts.push(
      <span key="denied" className="inline-flex items-center gap-1 text-amber-300">
        <Ban size={10} className="shrink-0" />
        {denied.length} blocked
      </span>,
    );
  }
  if (docs.length > 0) {
    parts.push(<span key="docs">{count(docs.length, "file")}</span>);
  }
  if (kept.length > 0) {
    // v1.282.0: VISIBLE WITHOUT EXPANDING, in the accent — this is the app
    // learning something about the user, and it says so where they stand.
    parts.push(
      <span key="remembered" data-testid="turn-remembered" className="text-accent-soft">
        Remembered: {kept.join("; ")}
      </span>,
    );
  }
  if (lowTrust) {
    // v1.298.0: a turn that ran under low trust says so on the line — amber,
    // because it is a restriction the user did not choose; the reason is the
    // daemon's own words when it gave them.
    const why = (trustReason ?? "").trim();
    // The note often opens with the same two words ("low trust: 4 tools
    // kept away") — said once, not twice.
    const note = (trustNote ?? "").trim().replace(/^low trust:?\s*/i, "");
    const head = why ? `low trust: ${why}` : "low trust";
    parts.push(
      <span
        key="trust"
        data-testid="turn-trust"
        className="inline-flex items-center gap-1 text-amber-300"
      >
        <ShieldAlert size={10} className="shrink-0" />
        {note ? `${head} — ${note}` : head}
      </span>,
    );
  }
  if (used.cache || used.cost) {
    // v1.300.0: one quiet zinc part. A subscription turn's cost is a
    // LIST-PRICE EQUIVALENT (the subscription paid for it), so it carries
    // "list" and says so in full on hover; a metered cost is just money.
    parts.push(
      <span key="usage" data-testid="turn-usage" className="text-zinc-500">
        {used.cache}
        {used.cache && used.cost ? " · " : null}
        {used.cost && (
          <span
            data-testid="turn-usage-cost"
            title={used.list ? LIST_PRICE_TITLE : undefined}
          >
            {used.cost}
          </span>
        )}
      </span>,
    );
  }
  for (const b of blockedOut) {
    // v1.298.0: passages the daemon kept out of the context. Quiet zinc with
    // a check — the system did its job; this is disclosure, not a warning.
    parts.push(
      <span
        key={`blocked-${b.source}`}
        data-testid="turn-context-blocked"
        className="inline-flex items-center gap-1 text-zinc-500"
      >
        <ShieldCheck size={10} className="shrink-0" />
        {b.count} blocked from {b.source}
      </span>,
    );
  }

  return (
    <div className="mt-1 text-[11px] text-zinc-500">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        className="group inline-flex max-w-full flex-wrap items-center gap-x-1.5 gap-y-1 text-left transition-colors hover:text-zinc-300"
      >
        {open ? (
          <ChevronDown size={10} className="shrink-0" />
        ) : (
          <ChevronRight size={10} className="shrink-0" />
        )}
        {parts.map((p, i) => (
          <span key={i} className="inline-flex min-w-0 items-center gap-1.5">
            {i > 0 && <span aria-hidden="true">·</span>}
            {p}
          </span>
        ))}
      </button>

      {open && (
        <div
          id={panelId}
          className="mt-1.5 max-w-[560px] space-y-2 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5"
        >
          {rt && (
            <div className="flex items-start gap-2">
              <RouteIcon size={12} className="mt-0.5 shrink-0 text-zinc-500" />
              <div className="min-w-0 text-[11.5px] leading-relaxed">
                <span className={warning ? "text-amber-300" : "text-zinc-300"}>
                  {providerDisplay(rt.provider)}
                </span>
                {/* v1.314.0: the raw id, quietly — a record for support. */}
                {providerDisplay(rt.provider) !== rt.provider && (
                  <span className="text-zinc-600"> ({rt.provider})</span>
                )}
                {rt.model && (
                  <span className="text-zinc-500"> · {rt.model}</span>
                )}
                {mismatch && (
                  <span className="text-amber-300/90" title={rt.requested}>
                    {" "}
                    — requested {providerDisplay(rt.requested)}
                  </span>
                )}
                {rt.reason === "failover" && rt.from && rt.from !== rt.provider && (
                  <span className="text-amber-300/90" title={rt.from}>
                    {" "}
                    — {providerDisplay(rt.from)} {wordWhy(rt.why) || "failed"}
                  </span>
                )}
                {rt.reason === "auto-tier" ? (
                  // v1.169.0: auto-tier stays QUIET (it is the user's own
                  // configured automation, not a substitution — v1.165.0), but
                  // the judgment behind it must be REACHABLE: the reason links
                  // to the Connections page, where each local model's report
                  // card shows the quality stats the tiering keys off. The
                  // link lives in the expanded panel only — the collapsed line
                  // is inside the toggle button, where a nested anchor would
                  // be illegal DOM.
                  <span className="text-zinc-500">
                    {" "}
                    (
                    <Link
                      href="/connections"
                      title="Auto picked this model from its difficulty tiers and your local models' measured quality — see each model's report card on Connections"
                      className="underline decoration-zinc-700 underline-offset-2 transition-colors hover:text-zinc-300"
                    >
                      auto-tier
                    </Link>
                    )
                  </span>
                ) : (
                  rt.reason && (
                    <span className="text-zinc-500"> ({rt.reason})</span>
                  )
                )}
              </div>
            </div>
          )}

          {stepChips.length > 0 ? (
            <div className="flex items-start gap-2">
              <Wrench size={12} className="mt-0.5 shrink-0 text-zinc-500" />
              <div className="flex min-w-0 flex-wrap gap-x-1.5 gap-y-1">
                {stepChips.map((st, i) => {
                  const dur = secondsText(st.ms);
                  const failed = st.ok === false;
                  const label = [st.name, dur, failed ? "failed" : null]
                    .filter(Boolean)
                    .join(" · ");
                  return (
                    <code
                      key={`${st.name}-${i}`}
                      data-testid="turn-step"
                      data-ok={st.ok === null ? "unknown" : String(st.ok)}
                      title={label}
                      className={
                        failed
                          ? "max-w-full truncate rounded border border-rose-500/20 bg-rose-500/[0.05] px-1.5 py-0.5 font-mono text-[11px] text-rose-300/90"
                          : "max-w-full truncate rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300"
                      }
                    >
                      {st.name}
                      {dur && <span className="text-zinc-500"> · {dur}</span>}
                      {failed && <span> · failed</span>}
                    </code>
                  );
                })}
              </div>
            </div>
          ) : tools.length > 0 && (
            <div className="flex items-start gap-2">
              <Wrench size={12} className="mt-0.5 shrink-0 text-zinc-500" />
              <div className="flex min-w-0 flex-wrap gap-x-1.5 gap-y-1">
                {tools.map((t, i) => (
                  <code
                    key={`${t}-${i}`}
                    title={t}
                    className="max-w-full truncate rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300"
                  >
                    {t}
                  </code>
                ))}
              </div>
            </div>
          )}

          {denied.length > 0 && (
            <div className="flex items-start gap-2">
              <Ban size={12} className="mt-0.5 shrink-0 text-amber-400" />
              <div className="flex min-w-0 flex-wrap gap-x-1.5 gap-y-1">
                {denied.map((t, i) => (
                  <code
                    key={`${t}-${i}`}
                    title={t}
                    className="max-w-full truncate rounded border border-amber-500/25 bg-amber-500/[0.06] px-1.5 py-0.5 font-mono text-[11px] text-amber-300"
                  >
                    blocked: {t}
                  </code>
                ))}
              </div>
            </div>
          )}

          {docs.length > 0 && (
            <div className="flex items-start gap-2">
              <FileText size={12} className="mt-0.5 shrink-0 text-zinc-500" />
              <div className="min-w-0 flex-1">
                <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1">
                  {docs.map((path) => {
                    // Undo renders ONLY for a chip the caller matched to a
                    // journal row by path (v1.168.0) — never a guess.
                    const undoState =
                      undoFor && onUndo ? (undoFor(path) ?? null) : null;
                    return (
                      <span
                        key={path}
                        className="inline-flex min-w-0 items-center gap-0.5"
                      >
                        <button
                          type="button"
                          title={path}
                          onClick={() => onOpenDocument?.(path)}
                          className="max-w-[16rem] truncate rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-[11px] text-zinc-300 transition-colors hover:bg-white/[0.08] hover:text-accent-soft"
                        >
                          {docBasename(path)}
                        </button>
                        {undoState && (
                          <button
                            type="button"
                            onClick={() =>
                              void runUndo(undoState.actionId, path)
                            }
                            disabled={
                              !undoState.undoable || undoingPath !== null
                            }
                            aria-label={`Undo the write to ${docBasename(path)}`}
                            title={
                              undoState.undoable
                                ? `Undo this write — revert ${docBasename(path)}`
                                : `Can't undo: ${undoState.reason ?? "not undoable"}`
                            }
                            className="inline-flex shrink-0 items-center gap-1 rounded px-1 py-0.5 text-[10.5px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-amber-300 disabled:opacity-40"
                          >
                            {undoingPath === path ? (
                              <Loader2 size={10} className="animate-spin" />
                            ) : (
                              <Undo2 size={10} />
                            )}
                            undo
                          </button>
                        )}
                      </span>
                    );
                  })}
                </div>
                {undoErr && (
                  <p className="mt-1 text-[10.5px] text-rose-300/90">
                    {undoErr}
                  </p>
                )}
              </div>
            </div>
          )}

          {(inTok != null || outTok != null || ctx != null) && (
            <div className="flex items-start gap-2">
              <Gauge size={12} className="mt-0.5 shrink-0 text-zinc-500" />
              <div className="min-w-0 text-[11.5px] text-zinc-500">
                {[
                  inTok != null ? `${inTok.toLocaleString()} in` : null,
                  outTok != null ? `${outTok.toLocaleString()} out` : null,
                  ctx != null ? `context ${Math.round(ctx * 100)}%` : null,
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </div>
            </div>
          )}

          {speed && (
            <div className="flex items-start gap-2">
              <Timer size={12} className="mt-0.5 shrink-0 text-zinc-500" />
              <div data-testid="turn-speed" className="min-w-0 text-[11.5px] text-zinc-500">
                {speed}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
