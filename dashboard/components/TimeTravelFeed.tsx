"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  Wrench,
  Coins,
  Scale,
  CircleDot,
  Sparkles,
  Undo2,
  ExternalLink,
  RotateCw,
  ChevronRight,
} from "lucide-react";
import { get, post, ApiError } from "@/lib/api";
import { useEvents } from "@/lib/useEvents";
import type { AuditEntry, AuditTimeline, UndoResult } from "@/lib/types";
import {
  Badge,
  Empty,
  ErrorNote,
  SkeletonRows,
  LoaderInline,
  type Tone,
} from "@/components/ui";
import { timeAgo, clockTime } from "@/lib/format";
import { providerDisplay } from "@/lib/onboarding";
import { originLabel } from "@/components/sessions/OriginChip";

/** Aggregates over the currently-loaded window, surfaced to the host page. */
export interface FeedStats {
  total: number | null;
  loaded: number;
  undoable: number;
  inputTokens: number;
  outputTokens: number;
  /** METERED dollars only — what an API key is billed. */
  costUsd: number;
  /** v1.300.0: a flat subscription's LIST-PRICE VALUE in this view (never
   *  cost — the work was included in the plan). */
  listPriceUsd: number;
}

/** An entry's dollars, split (v1.300.0 review). The ledger flags a step a
 *  flat subscription covered (`list_price_equivalent: true`) and carries its
 *  value in `list_price_equivalent_usd` with `cost_usd` 0 — so a flagged
 *  entry is NEVER cost, even if a server sent its value in `cost_usd`. An
 *  unflagged (metered or older) entry is cost as before. */
export function entryMoney(e: AuditEntry): { cost: number; listPrice: number } {
  const x = e as AuditEntry & {
    list_price_equivalent?: unknown;
    list_price_equivalent_usd?: unknown;
  };
  const num = (v: unknown) => (typeof v === "number" && Number.isFinite(v) && v > 0 ? v : 0);
  if (x.list_price_equivalent === true) {
    return { cost: 0, listPrice: num(x.list_price_equivalent_usd) || num(x.cost_usd) };
  }
  return { cost: num(x.cost_usd), listPrice: 0 };
}

function dollars(v: number): string {
  return `$${v < 0.01 ? v.toFixed(4) : v.toFixed(3)}`;
}

/** The coarse timeline lanes (kinds) the read-model emits, plus "all". */
const KINDS: { key: string; label: string }[] = [
  { key: "all", label: "Everything" },
  { key: "tool", label: "Actions" },
  { key: "token", label: "Tokens" },
  { key: "decision", label: "Decisions" },
  // v1.314.0: "Progress", not the ledger's word "lifecycle" (the filter's
  // key — what /audit is asked for — is unchanged).
  { key: "lifecycle", label: "Progress" },
];

// v1.314.0 (UX wave 2): a row's first line in words — "lifecycle" and
// "event" were the read-model's kind names. Only the label changed.
const KIND_META: Record<string, { icon: typeof Wrench; tone: Tone; label: string }> = {
  tool: { icon: Wrench, tone: "cyan", label: "Action" },
  token: { icon: Coins, tone: "violet", label: "Model use" },
  decision: { icon: Scale, tone: "amber", label: "Decision" },
  lifecycle: { icon: CircleDot, tone: "slate", label: "Progress" },
  action: { icon: Sparkles, tone: "cyan", label: "Update" },
};

const PAGE = 50;
/** Event types that mean the timeline changed and the head should refresh. */
const LIVE_TYPES = new Set([
  "tool.executed",
  "tool.denied",
  "llm.completed",
  "action.reverted",
  "agent.state_changed",
  "agent.completed",
  "session.completed",
  "provider.routed",
  "provider.failover",
  "autonomy.proposed",
  "autonomy.executed",
]);

function kindMeta(kind: string) {
  return KIND_META[kind] ?? KIND_META.action;
}

/** v1.232.0 (audit U12): plain words for the event types the ledger projects
 *  straight into a row's `summary` (`_event_summary` says "provider.routed
 *  <model>" for a decision, and `actor` falls back to the type when nothing
 *  stamped an origin). A row read "decision by provider.routed /
 *  provider.routed" — the type twice and no words. Unknown types keep their
 *  summary verbatim (a new kind must read oddly, never vanish). */
export const EVENT_WORDS: Record<string, string> = {
  "provider.routed": "Picked a model for this turn",
  "provider.failover": "Switched provider after a failure",
  // v1.314.0: never "mock" in words, and true to the router since v1.162.0 —
  // this event now means the chosen model could not answer (it refuses; the
  // only time the demo model still answers is the mock-default trap, where
  // no real model answered either).
  "provider.downgraded": "No real model answered",
  "autonomy.proposed": "Proposed an action on its own",
  "autonomy.executed": "Ran an action on its own",
  "llm.completed": "Model call finished",
  "session.created": "Session started",
  "session.completed": "Session finished",
  "agent.started": "Agent started",
  "agent.state_changed": "Agent changed state",
  "agent.completed": "Agent finished",
  "action.reverted": "Action undone",
  "tool.executed": "Ran a tool",
  "tool.denied": "Refused a tool",
  // v1.314.0 (UX wave 2): the events the Activity page printed raw.
  "delegation.started": "Handed work to a teammate",
  "delegation.completed": "Teammate finished",
  "comm.desktop": "Sent a desktop notification",
};

/** v1.314.0: "agent running→completed" (eval/observability._event_summary
 *  for agent.state_changed) as a sentence. */
const AGENT_STATE_RE = /^agent ([a-z_]+)\s*→\s*([a-z_]+)$/;
/** v1.314.0: a token row ("<provider>/<model> · <in>+<out> tok", the same
 *  _event_summary). Anchored on the " tok" tail so a path with a slash in an
 *  ordinary summary is never mistaken for one. */
const TOKEN_SUMMARY_RE = /^([\w.-]+)\/(\S*) · (\d+\+\d+ tok)$/;

const EVENT_TYPE_RE = /^([a-z_]+\.[a-z_.]+)\b\s*(.*)$/s;

/** The row's second line: the event's human label (plus the summary's own
 *  detail, e.g. the model picked) when the summary is a bare event type. */
export function humanSummary(summary: string | null | undefined): string {
  const s = (summary ?? "").trim();
  if (!s) return "";
  const st = AGENT_STATE_RE.exec(s);
  if (st) return `Agent went from ${st[1]} to ${st[2]}`;
  // A token row names the provider in words; the scripted model is "Demo
  // model", never "mock" (its model id is "mock-1", so it is dropped too).
  // A real row keeps its model id — it is the record of what ran.
  const tok = TOKEN_SUMMARY_RE.exec(s);
  if (tok) {
    if (tok[1] === "mock") return `Demo model · ${tok[3]}`;
    return [providerDisplay(tok[1]), tok[2], tok[3]].filter(Boolean).join(" · ");
  }
  const m = EVENT_TYPE_RE.exec(s);
  if (!m) return s;
  const words = EVENT_WORDS[m[1]];
  if (!words) return s;
  const detail = m[2].trim();
  return detail ? `${words} · ${detail}` : words;
}

/** An `actor` that is just an event type is the ledger's fallback, not a
 *  person or an agent — "by provider.routed" says nothing. */
export function isEventTypeActor(actor: string | null | undefined): boolean {
  return !!actor && /^[a-z_]+\.[a-z_.]+$/.test(actor.trim());
}

/**
 * v1.314.0 (UX wave 2): a run of bookkeeping rows folds into ONE row.
 *
 * The Activity page is the audit trail that makes handing over control feel
 * safe, yet its four Undo buttons sat among ~50 "lifecycle" and 0+0 "tokens"
 * rows. A row is FOLDABLE when it is lifecycle, or a token row that used no
 * tokens and no money — and NEVER when it can be undone, was refused, or was
 * reversed: those stay one press away. Two or more consecutive foldable rows
 * of the SAME session become a group that expands in place. Nothing leaves
 * the timeline: every row is still rendered on expand, and the stats count
 * the same entries.
 */
export function isFoldable(e: AuditEntry): boolean {
  if (e.undoable || e.undone) return false;
  if ((e.verdict && e.verdict.toLowerCase() === "deny") || e.ok === false) return false;
  if (e.kind === "lifecycle") return true;
  if (e.kind !== "token") return false;
  const { cost, listPrice } = entryMoney(e);
  return !(e.input_tokens || 0) && !(e.output_tokens || 0) && !cost && !listPrice;
}

export type FeedItem =
  | { type: "row"; e: AuditEntry }
  | { type: "group"; key: string; rows: AuditEntry[] };

/** Entries (newest first) → rows and folded groups, order kept. */
export function foldEntries(entries: AuditEntry[]): FeedItem[] {
  const out: FeedItem[] = [];
  let run: AuditEntry[] = [];
  const flush = () => {
    // Keyed on the OLDEST row (entries are newest first): a live refresh adds
    // a running mission's next step on TOP of the run, so the newest id
    // changes every few seconds and would remount the group (losing focus).
    if (run.length >= 2) out.push({ type: "group", key: run[run.length - 1].id, rows: run });
    else for (const e of run) out.push({ type: "row", e });
    run = [];
  };
  for (const e of entries) {
    if (isFoldable(e) && (run.length === 0 || run[0].session_id === e.session_id)) {
      run.push(e);
      continue;
    }
    flush();
    if (isFoldable(e)) run.push(e);
    else out.push({ type: "row", e });
  }
  flush();
  return out;
}

/** v1.314.0: who did it, in words ("job:mission" -> "Mission"). */
function actorWords(actor: string): string {
  return originLabel(actor) || actor;
}

/**
 * The audit time-travel feed. Renders the canonical `GET /audit` stream as a
 * vertical timeline with per-entry undo where the action allows. Reused by the
 * global Activity page and the per-session Time-travel tab.
 *
 * - `sessionId` set  → scoped to one session (no session links, filter locked).
 * - `sessionId` unset → global timeline (session links shown).
 */
export function TimeTravelFeed({
  sessionId,
  onStats,
}: {
  sessionId?: string;
  onStats?: (s: FeedStats) => void;
}) {
  const [kind, setKind] = useState("all");
  const [tool, setTool] = useState("");
  const [toolInput, setToolInput] = useState("");

  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  // Guard against out-of-order responses when filters change mid-flight.
  const reqRef = useRef(0);
  // Coalesces a burst of live events into a single trailing head refetch.
  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const buildQuery = useCallback(
    (before?: string | null) => {
      const q = new URLSearchParams();
      if (sessionId) q.set("session_id", sessionId);
      if (kind !== "all") q.set("kind", kind);
      if (tool.trim()) q.set("tool", tool.trim());
      q.set("limit", String(PAGE));
      if (before) q.set("before", before);
      return `/audit?${q.toString()}`;
    },
    [sessionId, kind, tool],
  );

  // First page (also re-run on any filter change).
  useEffect(() => {
    const seq = ++reqRef.current;
    // A filter change supersedes any pending debounced head-refresh — that timer
    // captured the PREVIOUS buildQuery, so letting it fire would merge stale-filter
    // rows onto the new list. Cancel it; this fresh first-page load replaces them.
    if (refreshTimer.current !== null) {
      clearTimeout(refreshTimer.current);
      refreshTimer.current = null;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    get<AuditTimeline>(buildQuery())
      .then((res) => {
        if (cancelled || seq !== reqRef.current) return;
        setEntries(res.entries || []);
        setCursor(res.next_cursor ?? null);
        setTotal(typeof res.total === "number" ? res.total : null);
      })
      .catch((e: unknown) => {
        if (cancelled || seq !== reqRef.current) return;
        setError(e instanceof ApiError ? e : new ApiError(String(e), 0));
      })
      .finally(() => {
        if (!cancelled && seq === reqRef.current) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [buildQuery]);

  // Load an older page (keyset). Never clears what's already shown.
  async function loadOlder() {
    if (!cursor || loadingMore) return;
    setLoadingMore(true);
    try {
      const res = await get<AuditTimeline>(buildQuery(cursor));
      setEntries((prev) => {
        const seen = new Set(prev.map((e) => e.id));
        return [...prev, ...(res.entries || []).filter((e) => !seen.has(e.id))];
      });
      setCursor(res.next_cursor ?? null);
    } catch {
      /* keep what we have; a transient failure just leaves the button */
    } finally {
      setLoadingMore(false);
    }
  }

  // Live: when relevant activity arrives, pull the newest page and merge any
  // entries we do not already have onto the top — preserving loaded older pages.
  const { events } = useEvents(60);
  const latestLive = useMemo(() => {
    for (const e of events) {
      if (!LIVE_TYPES.has(e.type)) continue;
      if (sessionId && e.session_id !== sessionId) continue;
      return e;
    }
    return null;
  }, [events, sessionId]);

  const refreshHead = useCallback(() => {
    const seq = reqRef.current; // do not bump; a filter change supersedes us
    get<AuditTimeline>(buildQuery())
      .then((res) => {
        if (seq !== reqRef.current) return;
        setEntries((prev) => {
          const seen = new Set(prev.map((e) => e.id));
          const fresh = (res.entries || []).filter((e) => !seen.has(e.id));
          // Also reconcile undone flags on rows we already show.
          const byId = new Map((res.entries || []).map((e) => [e.id, e]));
          const merged = prev.map((e) => byId.get(e.id) ?? e);
          return fresh.length ? [...fresh, ...merged] : merged;
        });
        if (typeof res.total === "number") setTotal(res.total);
      })
      .catch(() => {
        /* transient — the next tick retries */
      });
  }, [buildQuery]);

  useEffect(() => {
    if (!latestLive) return;
    // A busy run emits many tool.executed/llm.completed events per second, and
    // each refreshHead() is a full GET /audit. Debounce: clear+reset the timer
    // on every event so a burst collapses into ONE trailing refetch (~600ms).
    if (refreshTimer.current !== null) clearTimeout(refreshTimer.current);
    refreshTimer.current = setTimeout(() => {
      refreshTimer.current = null;
      refreshHead();
    }, 600);
    return () => {
      if (refreshTimer.current !== null) {
        clearTimeout(refreshTimer.current);
        refreshTimer.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestLive?.id]);

  // Surface aggregates for the host page's stat strip.
  useEffect(() => {
    if (!onStats) return;
    let inTok = 0;
    let outTok = 0;
    let cost = 0;
    let listPrice = 0;
    let undoable = 0;
    for (const e of entries) {
      inTok += e.input_tokens || 0;
      outTok += e.output_tokens || 0;
      const money = entryMoney(e);
      cost += money.cost;
      listPrice += money.listPrice;
      if (e.undoable) undoable += 1;
    }
    onStats({
      total,
      loaded: entries.length,
      undoable,
      inputTokens: inTok,
      outputTokens: outTok,
      costUsd: cost,
      listPriceUsd: listPrice,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entries, total]);

  /* ---- Undo ------------------------------------------------------------- */
  const [undoing, setUndoing] = useState<string | null>(null);
  const [armed, setArmed] = useState<string | null>(null);
  const [rowError, setRowError] = useState<{ id: string; msg: string } | null>(null);

  useEffect(() => {
    if (!armed) return;
    const t = setTimeout(() => setArmed(null), 3500);
    return () => clearTimeout(t);
  }, [armed]);

  async function undo(id: string) {
    if (armed !== id) {
      setArmed(id);
      setRowError(null);
      return;
    }
    setArmed(null);
    setUndoing(id);
    setRowError(null);
    try {
      await post<UndoResult>(`/undo/${id}`);
      // Optimistically mark undone; the live refresh will add the undo entry.
      setEntries((prev) =>
        prev.map((e) => (e.id === id ? { ...e, undoable: false } : e)),
      );
      refreshHead();
    } catch (err) {
      setRowError({
        id,
        msg: err instanceof ApiError ? err.message : String(err),
      });
    } finally {
      setUndoing(null);
    }
  }

  const offline = error && error.status === 0;

  // v1.314.0: fold runs of bookkeeping rows (see foldEntries). An expanded
  // group is remembered by EVERY row id it held when the user opened it, and
  // a group is open when ANY of its rows is in that set. A live refresh puts
  // a mission's next progress row on top of the same run, so keying "open"
  // on the newest row snapped an open group shut on every new row.
  // Membership survives a prepend (the old rows are still in the run).
  const items = useMemo(() => foldEntries(entries), [entries]);
  const [openGroups, setOpenGroups] = useState<Set<string>>(() => new Set());
  const groupOpen = (rows: AuditEntry[]) => rows.some((r) => openGroups.has(r.id));
  const toggleGroup = (rows: AuditEntry[]) =>
    setOpenGroups((prev) => {
      const next = new Set(prev);
      const isOpen = rows.some((r) => next.has(r.id));
      for (const r of rows) {
        if (isOpen) next.delete(r.id);
        else next.add(r.id);
      }
      return next;
    });
  const renderRow = (e: AuditEntry) => (
    <TimelineRow
      key={e.id}
      e={e}
      showSession={!sessionId}
      armed={armed === e.id}
      undoing={undoing === e.id}
      rowError={rowError?.id === e.id ? rowError.msg : null}
      onUndo={() => undo(e.id)}
    />
  );

  return (
    <div className="space-y-4">
      {/* Filter row */}
      <div className="flex flex-wrap items-center gap-2">
        <div className="flex flex-wrap items-center gap-1.5">
          {KINDS.map((k) => {
            const active = kind === k.key;
            return (
              <button
                key={k.key}
                type="button"
                onClick={() => setKind(k.key)}
                className={`rounded-lg border px-2.5 py-1 text-xs font-medium transition-colors ${
                  active
                    ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
                    : "border-white/10 text-zinc-400 hover:border-white/20 hover:text-zinc-200"
                }`}
              >
                {k.label}
              </button>
            );
          })}
        </div>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            setTool(toolInput);
          }}
          className="ml-auto flex items-center gap-1.5"
        >
          <input
            value={toolInput}
            onChange={(e) => setToolInput(e.target.value)}
            placeholder="Filter by tool…"
            className="w-40 rounded-lg border border-white/[0.08] bg-ink-900/80 px-2.5 py-1 text-xs text-zinc-100 outline-none transition-colors placeholder:text-zinc-600 focus:border-accent/60"
          />
          {(tool || toolInput) && (
            <button
              type="button"
              onClick={() => {
                setTool("");
                setToolInput("");
              }}
              className="text-xs text-zinc-500 transition-colors hover:text-zinc-300"
            >
              clear
            </button>
          )}
        </form>
      </div>

      {offline ? (
        <Empty icon={<RotateCw size={22} />}>Daemon offline — cannot load the timeline.</Empty>
      ) : error ? (
        <ErrorNote>Could not load the timeline: {error.message}</ErrorNote>
      ) : loading && entries.length === 0 ? (
        <SkeletonRows rows={6} />
      ) : entries.length === 0 ? (
        <Empty icon={<CircleDot size={22} />}>
          Nothing on the timeline yet. Every action, token, and decision shows up here as
          Iron Jarvis works — so you can replay it, and undo what allows it.
        </Empty>
      ) : (
        <ol className="relative space-y-1.5 pl-1">
          {/* the rail */}
          <span className="pointer-events-none absolute bottom-2 left-[10px] top-2 w-px bg-white/[0.06]" />
          {items.map((it) =>
            it.type === "row" ? (
              renderRow(it.e)
            ) : (
              <StepGroup
                key={`group:${it.key}`}
                rows={it.rows}
                open={groupOpen(it.rows)}
                onToggle={() => toggleGroup(it.rows)}
                renderRow={renderRow}
              />
            ),
          )}
        </ol>
      )}

      {cursor && !loading && (
        <div className="flex justify-center pt-1">
          <button
            type="button"
            onClick={loadOlder}
            disabled={loadingMore}
            className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-xs font-medium text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100 disabled:opacity-50"
          >
            {loadingMore ? <LoaderInline label="Loading…" /> : "Load older"}
          </button>
        </div>
      )}
    </div>
  );
}

/** v1.314.0: a folded run of bookkeeping rows — one button that expands the
 *  rows in place (aria-expanded). Who ran them is said in words. */
function StepGroup({
  rows,
  open,
  onToggle,
  renderRow,
}: {
  rows: AuditEntry[];
  open: boolean;
  onToggle: () => void;
  renderRow: (e: AuditEntry) => React.ReactNode;
}) {
  const actor = rows.find((r) => r.actor && !isEventTypeActor(r.actor))?.actor ?? "";
  const newest = rows[0];
  return (
    <>
      <li className="relative flex gap-3 rounded-xl px-2 py-1.5">
        <span className="relative z-10 mt-0.5 grid h-[21px] w-[21px] shrink-0 place-items-center">
          <span className={`grid h-[21px] w-[21px] place-items-center rounded-full border ${NODE.slate}`}>
            <CircleDot size={11} />
          </span>
        </span>
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={open}
            title={open ? "Hide these steps" : "Show each step"}
            className="inline-flex min-w-0 items-center gap-1.5 rounded-lg border border-white/[0.08] px-2 py-0.5 text-xs text-zinc-400 transition-colors hover:border-white/20 hover:text-zinc-200"
          >
            <ChevronRight
              size={12}
              className={`shrink-0 transition-transform ${open ? "rotate-90" : ""}`}
            />
            <span className="truncate">
              {actor ? <span title={actor}>{actorWords(actor)} · </span> : null}
              {rows.length} steps
            </span>
          </button>
          <span className="text-[11px] text-zinc-600">progress updates</span>
          <span
            className="ml-auto shrink-0 text-[11px] text-zinc-600"
            title={clockTime(newest.ts)}
          >
            {timeAgo(newest.ts)}
          </span>
        </div>
      </li>
      {open && rows.map((e) => renderRow(e))}
    </>
  );
}

/** A single timeline entry with its kind node, chips, and undo affordance. */
function TimelineRow({
  e,
  showSession,
  armed,
  undoing,
  rowError,
  onUndo,
}: {
  e: AuditEntry;
  showSession: boolean;
  armed: boolean;
  undoing: boolean;
  rowError: string | null;
  onUndo: () => void;
}) {
  const meta = kindMeta(e.kind);
  const Icon = meta.icon;
  const deny =
    (e.verdict && e.verdict.toLowerCase() === "deny") || e.ok === false;
  const nodeTone = deny ? "red" : meta.tone;
  const inTok = e.input_tokens || 0;
  const outTok = e.output_tokens || 0;
  const { cost, listPrice } = entryMoney(e);
  // Explicit flag from the ledger — NOT inferred from reversible/undoable, since a
  // reversible action whose capture produced no inverse is not-undoable yet never
  // reversed.
  const reversed = !!e.undone;

  return (
    <li className="relative flex gap-3 rounded-xl px-2 py-2 transition-colors hover:bg-white/[0.02]">
      {/* node */}
      <span className="relative z-10 mt-0.5 grid h-[21px] w-[21px] shrink-0 place-items-center">
        <span
          className={`grid h-[21px] w-[21px] place-items-center rounded-full border ${NODE[nodeTone]}`}
        >
          <Icon size={11} />
        </span>
      </span>

      {/* body */}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-sm text-zinc-200">
            {e.tool ? (
              <span className="font-mono text-[13px] text-zinc-100">{e.tool}</span>
            ) : (
              <span className="text-zinc-300">{meta.label}</span>
            )}
          </span>
          {/* v1.314.0: who, in words ("Mission"); the raw origin is the title. */}
          {e.actor && !isEventTypeActor(e.actor) && (
            <span className="text-[11px] text-zinc-500" title={`Started by: ${e.actor}`}>
              by <span className="text-zinc-400">{actorWords(e.actor)}</span>{" "}
            </span>
          )}
          {deny && <Badge value="denied" tone="red" />}
          {reversed && (
            <span className="inline-flex items-center gap-1 rounded-full border border-zinc-500/25 bg-zinc-500/[0.08] px-2 py-0.5 text-[10px] font-medium text-zinc-400">
              <Undo2 size={10} /> reversed
            </span>
          )}
          <span
            className="ml-auto shrink-0 text-[11px] text-zinc-600"
            title={clockTime(e.ts)}
          >
            {timeAgo(e.ts)}
          </span>
        </div>

        {e.summary && (
          <div className="mt-0.5 truncate text-xs text-zinc-500" title={e.summary}>
            {humanSummary(e.summary)}
          </div>
        )}

        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          {(inTok > 0 || outTok > 0) && (
            <span className="inline-flex items-center gap-1 rounded-full border border-violet-500/20 bg-violet-500/[0.06] px-2 py-0.5 text-[10px] font-medium text-violet-300">
              <Coins size={10} />
              {inTok.toLocaleString()}↓ {outTok.toLocaleString()}↑
            </span>
          )}
          {cost > 0 && (
            <span className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] font-medium text-zinc-400">
              {dollars(cost)}
            </span>
          )}
          {listPrice > 0 && (
            <span
              data-testid="list-price-chip"
              title="List-price value — included in your Claude subscription, not billed"
              className="rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] font-medium text-zinc-500"
            >
              ~{dollars(listPrice)} list
            </span>
          )}
          {showSession && e.session_id && (
            // v1.314.0: words on the link; the session id (a record — the
            // Sessions search finds it) rides the title.
            <Link
              href={`/sessions/${e.session_id}`}
              title={`Open session ${e.session_id}`}
              className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 text-[10px] text-zinc-500 transition-colors hover:border-accent/30 hover:text-accent-soft"
            >
              Open session <ExternalLink size={9} />
            </Link>
          )}

          {e.undoable && (
            <button
              type="button"
              onClick={onUndo}
              disabled={undoing}
              title="Reverse this action — restores the prior state"
              className={`ml-auto inline-flex items-center gap-1 rounded-lg border px-2 py-0.5 text-[11px] font-medium transition-colors disabled:opacity-50 ${
                armed
                  ? "border-amber-500/50 bg-amber-500/15 text-amber-200"
                  : "border-white/10 text-zinc-400 hover:border-accent/40 hover:text-accent-soft"
              }`}
            >
              {undoing ? (
                <LoaderInline label="Undoing…" />
              ) : (
                <>
                  <Undo2 size={12} /> {armed ? "Confirm undo?" : "Undo"}
                </>
              )}
            </button>
          )}
        </div>

        {rowError && (
          <div className="mt-1.5 text-[11px] text-rose-300">{rowError}</div>
        )}
      </div>
    </li>
  );
}

const NODE: Record<Tone, string> = {
  green: "border-emerald-500/30 bg-emerald-500/[0.08] text-emerald-300",
  amber: "border-amber-500/30 bg-amber-500/[0.08] text-amber-300",
  red: "border-rose-500/30 bg-rose-500/[0.08] text-rose-300",
  cyan: "border-accent/30 bg-accent/[0.08] text-accent-soft",
  violet: "border-violet-500/30 bg-violet-500/[0.08] text-violet-300",
  slate: "border-white/10 bg-white/[0.04] text-zinc-400",
};
