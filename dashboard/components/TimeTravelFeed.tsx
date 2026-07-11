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
import { timeAgo, clockTime, shortId } from "@/lib/format";

/** Aggregates over the currently-loaded window, surfaced to the host page. */
export interface FeedStats {
  total: number | null;
  loaded: number;
  undoable: number;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
}

/** The coarse timeline lanes (kinds) the read-model emits, plus "all". */
const KINDS: { key: string; label: string }[] = [
  { key: "all", label: "Everything" },
  { key: "tool", label: "Actions" },
  { key: "token", label: "Tokens" },
  { key: "decision", label: "Decisions" },
  { key: "lifecycle", label: "Lifecycle" },
];

const KIND_META: Record<string, { icon: typeof Wrench; tone: Tone; label: string }> = {
  tool: { icon: Wrench, tone: "cyan", label: "action" },
  token: { icon: Coins, tone: "violet", label: "tokens" },
  decision: { icon: Scale, tone: "amber", label: "decision" },
  lifecycle: { icon: CircleDot, tone: "slate", label: "lifecycle" },
  action: { icon: Sparkles, tone: "cyan", label: "event" },
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
    refreshHead();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [latestLive?.id]);

  // Surface aggregates for the host page's stat strip.
  useEffect(() => {
    if (!onStats) return;
    let inTok = 0;
    let outTok = 0;
    let cost = 0;
    let undoable = 0;
    for (const e of entries) {
      inTok += e.input_tokens || 0;
      outTok += e.output_tokens || 0;
      cost += e.cost_usd || 0;
      if (e.undoable) undoable += 1;
    }
    onStats({
      total,
      loaded: entries.length,
      undoable,
      inputTokens: inTok,
      outputTokens: outTok,
      costUsd: cost,
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
          {entries.map((e) => (
            <TimelineRow
              key={e.id}
              e={e}
              showSession={!sessionId}
              armed={armed === e.id}
              undoing={undoing === e.id}
              rowError={rowError?.id === e.id ? rowError.msg : null}
              onUndo={() => undo(e.id)}
            />
          ))}
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
  const cost = e.cost_usd || 0;
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
          {e.actor && (
            <span className="text-[11px] text-zinc-500">
              by <span className="text-zinc-400">{e.actor}</span>
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
            {e.summary}
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
              ${cost < 0.01 ? cost.toFixed(4) : cost.toFixed(3)}
            </span>
          )}
          {showSession && e.session_id && (
            <Link
              href={`/sessions/${e.session_id}`}
              className="inline-flex items-center gap-1 rounded-full border border-white/10 bg-white/[0.03] px-2 py-0.5 font-mono text-[10px] text-zinc-500 transition-colors hover:border-accent/30 hover:text-accent-soft"
            >
              {shortId(e.session_id)} <ExternalLink size={9} />
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
