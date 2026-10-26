"use client";

/**
 * THE TIMELINE (v1.390.0, the long-jobs plan's Phase 1): a coding session's
 * reply on ONE time axis, in the tab beside Plan.
 *
 *  - At the top: the reply's time, cost (its receipt's — "list price" for a
 *    subscription), tokens, model calls, steps and failed steps; the helpers'
 *    own cost on a line of its own (the receipt never counts it).
 *  - Rows: the model you talk to (one bar; each model call a piece of it),
 *    every step under it in the run log's words ("Read app.py"), each helper
 *    with its model and its own steps under it. Rows fold.
 *  - Bars end in their time (and cost, where they have one); a press or
 *    keyboard focus opens a card: status, start, length, tokens, cost, and a
 *    step's input and output (the saved detail; "Not kept for this step"
 *    past the first 60).
 *  - Which reply: the running one live, else the newest, with a picker.
 *
 * CALM AND CHEAP (the v1.250.0 rule): the running reply is folded from its
 * frames into a store (lib/buildTimeline `createLiveTimeline`) with no React
 * state; this pane reads it at most once a second, and only while it is on
 * screen (it is mounted only on its tab, and `useVisibleInterval` stops while
 * the window is hidden). Rows are WINDOWED (only those in view are drawn), so
 * a reply with 2,000 steps stays usable. Tone tokens only; the axis scrolls
 * inside the tab, never the page.
 */

import { memo, useCallback, useEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Bot, ChevronDown, ChevronRight, Sparkles, X } from "lucide-react";
import { STEP_ICON, StepDetailPanel } from "@/components/chat/WorkLine";
import { secondsText } from "@/components/chat/TurnReceipt";
import { answeredModelName, type CatalogRow } from "@/lib/answeredModel";
import {
  LIVE_CHOICE,
  axisTicks,
  callSegments,
  noTimelineReason,
  replyChoices,
  replyTimeline,
  tickLabel,
  timelineRows,
  timelineTotals,
  type BarState,
  type LiveTimeline,
  type Segment,
  type StepCheck,
  type TimelineCall,
  type TimelineHelper,
  type TimelineReply,
  type TimelineRow,
  type TimelineStep,
} from "@/lib/buildTimeline";
import { helperName } from "@/lib/buildHelpers";
import { carriedOnTurns, timelineResumeNote } from "@/lib/buildResume";
import type { BuildSessionView } from "@/lib/buildSessions";
import { providerDisplay } from "@/lib/onboarding";
import { kindWords, rowSubject, stepPhrase, stepPhraseText } from "@/lib/stepWords";
import { LIST_PRICE_TITLE, type TurnUsage } from "@/lib/types";
import { useVisibleInterval } from "@/lib/useVisibleInterval";
import { toolKind } from "@/lib/workTarget";

/** What the session lends the tab for its running reply. */
export interface LiveTimelineHandle {
  store: LiveTimeline;
  running: boolean;
  /** When the running reply started (ms since the epoch), or null. */
  startedAt: number | null;
  /** The running (or last) reply's turn id. */
  turnId: string | null;
}

/** How often the running reply is read again (ms). */
export const LIVE_READ_MS = 1000;
/** One row's height (px) — the windowing counts in rows. */
export const ROW_H = 28;
/** Rows drawn above and below the ones in view. */
const OVERSCAN = 12;
/** Rows drawn when the box cannot say its height (before layout). */
const FALLBACK_ROWS = 40;

export const NO_TIMELINE = "No timeline for this reply.";
export const NO_TIMELINE_WHY = "It was made before Iron Jarvis kept the time of each step.";
/** v1.390.0 review: a reply written since the Timeline that ended before the
 *  model was first asked (an error or a Stop) — never "made before". */
export const NO_TIMELINE_EARLY = "It ended before any work started.";
/** v1.390.0 review: a reply a crash cut off (its line, rebuilt at boot,
 *  keeps no times) — never "made before" either. */
export const NO_TIMELINE_CUT = "Iron Jarvis stopped while answering, so this reply's times were not kept.";
const NO_TIMELINE_REASON = { early: NO_TIMELINE_EARLY, cut: NO_TIMELINE_CUT, before: NO_TIMELINE_WHY } as const;
/** v1.390.0 review: a step Iron Jarvis ran ITSELF to check the reply (the
 *  finish check's test run, a project check) — never drawn as the model's. */
export const CHECK_WORDS: Readonly<Record<StepCheck, string>> = {
  finish: "Finish check",
  project: "Project check",
};
export const CHECK_NOTE = "Iron Jarvis ran this itself, to check the reply's work. The model did not ask for it.";
export const NO_REPLIES = "No replies yet. While the session works, each step shows here on one time axis.";
export const NOT_KEPT = "Not kept for this step.";
export const HELPERS_APART = "not in the cost above";
/** The reply that just ended, until the session holds its saved line. */
export const JUST_ENDED_CHOICE = "Just now";

/* ------------------------------------------------------------ words */

function plural(n: number, noun: string): string {
  return `${n.toLocaleString("en-US")} ${noun}${n === 1 ? "" : "s"}`;
}

/** "~$0.31", "<$0.01", "~$0.31 list price" (a subscription's money is a
 *  list-price equivalent, never a charge); null when there is nothing honest
 *  to say (no price known, or a free model). `short` says "list". */
export function costWords(u: TurnUsage | undefined, short = false): string | null {
  const c = u?.cost_usd;
  if (typeof c !== "number" || !Number.isFinite(c) || c < 0) return null;
  const list = u?.list_price_equivalent === true;
  if (!list && c <= 0) return null;
  const amount = c > 0 && c < 0.005 ? "<$0.01" : `~$${c.toFixed(2)}`;
  return list ? `${amount} ${short ? "list" : "list price"}` : amount;
}

/** "120,400 in · 8,200 out" (+ " · 96,000 read from cache"). */
export function tokenWords(u: TurnUsage | undefined): string | null {
  if (!u || (u.input_tokens === undefined && u.output_tokens === undefined)) return null;
  // Nothing counted (the demo model, a model that reports none): nothing said.
  if (!u.input_tokens && !u.output_tokens) return null;
  const parts = [
    `${(u.input_tokens ?? 0).toLocaleString("en-US")} in`,
    `${(u.output_tokens ?? 0).toLocaleString("en-US")} out`,
  ];
  if (u.cache_read_input_tokens) parts.push(`${u.cache_read_input_tokens.toLocaleString("en-US")} read from cache`);
  return parts.join(" · ");
}

function durationWords(ms: number | null | undefined): string {
  return secondsText(ms ?? null) ?? "0 s";
}

function startWords(ms: number | null | undefined): string {
  if (!ms) return "At the start";
  return `${durationWords(ms)} in`;
}

const STATE_WORDS: Record<BarState, string> = {
  working: "Working",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
};

function modelName(provider: string, model: string, models: readonly CatalogRow[]): string {
  if (!provider && !model) return "The model";
  // The demo model is named as the composer names it ("Demo model
  // (scripted)"), never by its scripted id.
  if (provider === "mock") return providerDisplay(provider);
  return answeredModelName({ provider, model }, models) || providerDisplay(provider) || model;
}

/** A step in the run log's words: "Read app.py", "Reading app.py"; a check
 *  Iron Jarvis ran itself says so first ("Finish check · Ran python"). */
export function stepLabel(step: Pick<TimelineStep, "name" | "target" | "state"> & { check?: StepCheck | null }): string {
  const own = ownStepLabel(step);
  return step.check ? `${CHECK_WORDS[step.check]} · ${own}` : own;
}

function ownStepLabel(step: Pick<TimelineStep, "name" | "target" | "state">): string {
  if (step.state === "working") {
    const kind = toolKind(step.name);
    const subject = rowSubject(kind, step.name, step.target);
    const lead = kindWords(kind, step.name, { subject: !!subject, running: true });
    return subject ? `${lead} ${subject.text}` : lead;
  }
  return stepPhraseText(stepPhrase(step.name, step.target));
}

function callLabel(c: TimelineCall, n: number, models: readonly CatalogRow[]): string {
  const who = modelName(c.provider, c.model, models);
  if (c.kind === "final") return `${who}: its final answer`;
  if (c.kind === "rewrite") return `${who}: the answer again, in your language`;
  return `${who}: model call ${n}`;
}

/* ------------------------------------------------------------ colours */

function barTone(state: BarState, kind: "step" | "helper" | "reply"): string {
  if (state === "failed") return "bg-tone-danger/70";
  if (state === "stopped") return "bg-tone-warn/60";
  if (kind === "helper") return state === "working" ? "bg-tone-violet/40" : "bg-tone-violet/60";
  if (kind === "reply") return "bg-accent/15";
  return state === "working" ? "bg-accent/40" : "bg-zinc-500/60";
}

function segTone(state: BarState): string {
  if (state === "failed") return "bg-tone-danger/80";
  if (state === "stopped") return "bg-tone-warn/70";
  if (state === "working") return "bg-accent/50";
  return "bg-accent/80";
}

/* ------------------------------------------------------------ the card */

type Picked =
  | { kind: "reply" }
  | { kind: "segment"; segment: Segment }
  | { kind: "step"; step: TimelineStep }
  | { kind: "helper"; helper: TimelineHelper };

function Facts({ rows }: { rows: [string, string | null | undefined][] }) {
  const shown = rows.filter((r): r is [string, string] => !!r[1]);
  return (
    <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[12px] leading-5">
      {shown.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-zinc-500">{k}</dt>
          <dd className="min-w-0 break-words text-zinc-300">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Card({
  picked,
  reply,
  models,
  onClose,
}: {
  picked: Picked;
  reply: TimelineReply;
  models: readonly CatalogRow[];
  onClose: () => void;
}) {
  let title = "";
  let body: ReactNode;
  if (picked.kind === "reply") {
    const route = reply.route;
    title = route ? modelName(route.provider, route.model, models) : "The model";
    const totals = timelineTotals(reply);
    body = (
      <Facts
        rows={[
          ["Status", STATE_WORDS[reply.state]],
          ["Took", durationWords(reply.durationMs)],
          // Reading the folder, the files and the memory before the model is
          // first asked — part of the reply's time, before its first piece.
          ["Getting ready", reply.calls.length ? durationWords(reply.calls[0].start) : null],
          ["Model calls", totals.calls.toLocaleString("en-US")],
          ["Steps", totals.steps.toLocaleString("en-US")],
          ["Checks", totals.checks ? totals.checks.toLocaleString("en-US") : null],
          ["Tokens", tokenWords(reply.usage)],
          ["Cost", costWords(reply.usage)],
        ]}
      />
    );
  } else if (picked.kind === "segment") {
    const seg = picked.segment;
    const first = seg.calls[0];
    if (seg.calls.length === 1) {
      title = callLabel(first, first.index + 1, models);
      body = (
        <Facts
          rows={[
            ["Status", STATE_WORDS[first.state]],
            ["Started", startWords(first.start)],
            ["Took", durationWords(first.ms)],
            ["Tokens", tokenWords(first.usage)],
            ["Cost", costWords(first.usage)],
          ]}
        />
      );
    } else {
      title = `${modelName(first.provider, first.model, models)}: model calls ${first.index + 1} to ${seg.calls[seg.calls.length - 1].index + 1}`;
      const usage = seg.calls.reduce<TurnUsage | undefined>((acc, c) => {
        if (!c.usage) return acc;
        const a = acc ?? {};
        return {
          input_tokens: (a.input_tokens ?? 0) + (c.usage.input_tokens ?? 0),
          output_tokens: (a.output_tokens ?? 0) + (c.usage.output_tokens ?? 0),
          ...(c.usage.cost_usd !== undefined || a.cost_usd !== undefined
            ? { cost_usd: (a.cost_usd ?? 0) + (c.usage.cost_usd ?? 0) }
            : {}),
          ...(a.list_price_equivalent || c.usage.list_price_equivalent ? { list_price_equivalent: true } : {}),
        };
      }, undefined);
      body = (
        <Facts
          rows={[
            ["Calls", `${seg.calls.length}, drawn together at this size`],
            ["Started", startWords(seg.start)],
            ["Took", durationWords(seg.calls.reduce((n, c) => n + c.ms, 0))],
            ["Tokens", tokenWords(usage)],
            ["Cost", costWords(usage)],
          ]}
        />
      );
    }
  } else if (picked.kind === "step") {
    const st = picked.step;
    title = stepLabel(st);
    body = (
      <div className="space-y-2">
        <Facts
          rows={[
            ["Status", STATE_WORDS[st.state]],
            ["Started", st.start !== null ? startWords(st.start) : null],
            ["Took", st.ms !== null ? durationWords(st.ms) : null],
          ]}
        />
        {st.check ? (
          <p data-testid="timeline-check-note" className="text-[12px] text-zinc-500">
            {CHECK_NOTE}
          </p>
        ) : null}
        {st.detail ? (
          <StepDetailPanel id={`timeline-detail-${st.key}`} name={st.name} detail={st.detail} />
        ) : st.state !== "working" ? (
          <p data-testid="timeline-not-kept" className="text-[12px] text-zinc-500">
            {NOT_KEPT}
          </p>
        ) : null}
      </div>
    );
  } else {
    const h = picked.helper;
    title = helperName(h.agent);
    const more = h.stepCount - h.steps.length;
    body = (
      <div className="space-y-2">
        <Facts
          rows={[
            ["Status", STATE_WORDS[h.state]],
            ["Model", h.provider || h.model ? modelName(h.provider, h.model, models) : null],
            ["Started", h.start !== null ? startWords(h.start) : null],
            ["Took", h.start !== null && h.end !== null ? durationWords(h.end - h.start) : null],
            ["Steps", `${h.stepCount.toLocaleString("en-US")}${more > 0 ? ` (the first ${h.steps.length} are drawn)` : ""}`],
            ["Tokens", tokenWords(h.usage)],
            ["Cost", costWords(h.usage) ? `${costWords(h.usage)}, ${HELPERS_APART}` : null],
            ["Asked", h.task || null],
            ["Report", h.result || null],
          ]}
        />
        <p className="text-[12px] text-zinc-500">Its own model calls are counted together.</p>
      </div>
    );
  }
  return (
    <section
      data-testid="timeline-card"
      aria-label="Details"
      aria-live="polite"
      className="max-h-[45%] shrink-0 overflow-y-auto border-t border-white/[0.06] px-1 pt-2"
    >
      <div className="mb-1 flex items-start gap-2">
        <h3 data-testid="timeline-card-title" className="min-w-0 flex-1 break-words text-[13px] font-medium text-zinc-100">
          {title}
        </h3>
        <button
          type="button"
          aria-label="Close the details"
          title="Close the details (Esc)"
          onClick={onClose}
          className="grid h-6 w-6 shrink-0 place-items-center rounded-md text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
        >
          <X size={13} />
        </button>
      </div>
      {body}
    </section>
  );
}

/* ------------------------------------------------------------ one row */

function pct(ms: number, duration: number): number {
  return duration > 0 ? Math.min(100, Math.max(0, (ms / duration) * 100)) : 0;
}

/** The words at a bar's end: "30 s · ~$0.05". */
function barEnd(ms: number, usage?: TurnUsage): string {
  const cost = costWords(usage, true);
  return cost ? `${durationWords(ms)} · ${cost}` : durationWords(ms);
}

const Row = memo(function Row({
  row,
  duration,
  label,
  usage,
  segments,
  open,
  picked,
  onPick,
  onToggle,
}: {
  row: TimelineRow;
  duration: number;
  label: string;
  usage?: TurnUsage;
  segments?: Segment[];
  open: boolean;
  picked: string | null;
  onPick: (key: string) => void;
  onToggle: (key: string) => void;
}) {
  const left = pct(row.start, duration);
  const width = Math.max(pct(row.end - row.start, duration), 0.6);
  // Where the bar's words go: after the bar while there is room, else before
  // it, else (a bar across most of the axis) inside its end.
  const place: "after" | "before" | "inside" = left + width <= 62 ? "after" : left >= 30 ? "before" : "inside";
  const kind = row.kind === "reply" ? "reply" : row.kind === "helper" ? "helper" : "step";
  const Icon =
    row.kind === "reply" ? Sparkles : row.kind === "helper" ? Bot : STEP_ICON[toolKind(row.step.name)];
  const foldable = row.kind === "reply" || row.kind === "helper";
  const words = `${label}, ${STATE_WORDS[row.state].toLowerCase()}, ${startWords(row.start).toLowerCase()}, took ${durationWords(row.end - row.start)}`;
  return (
    <div
      data-testid="timeline-row"
      data-kind={row.kind}
      data-state={row.state}
      className="grid items-center gap-2"
      style={{ height: ROW_H, gridTemplateColumns: "minmax(0, 2fr) minmax(0, 3fr)" }}
    >
      <div className="flex min-w-0 items-center gap-1" style={{ paddingLeft: row.depth * 12 }}>
        {foldable ? (
          <button
            type="button"
            aria-expanded={open}
            aria-label={open ? `Fold ${label}` : `Unfold ${label}`}
            onClick={() => onToggle(row.key)}
            className="grid h-5 w-5 shrink-0 place-items-center rounded text-zinc-500 hover:text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
          >
            {open ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          </button>
        ) : (
          <span aria-hidden className="w-5 shrink-0" />
        )}
        <Icon size={13} aria-hidden className="shrink-0 text-zinc-500" />
        <span
          title={label}
          className={`min-w-0 truncate text-[12px] ${row.state === "failed" ? "text-tone-danger" : row.depth === 0 ? "font-medium text-zinc-100" : "text-zinc-300"}`}
        >
          {label}
        </span>
      </div>
      <div className="relative h-full min-w-0 overflow-hidden">
        <button
          type="button"
          data-testid="timeline-bar"
          data-key={row.key}
          aria-label={words}
          aria-pressed={picked === row.key}
          onClick={() => onPick(row.key)}
          onFocus={() => onPick(row.key)}
          className={`absolute top-1/2 h-3 -translate-y-1/2 rounded-sm ${barTone(row.state, kind)} focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent ${picked === row.key ? "ring-1 ring-accent" : ""}`}
          style={{ left: `${left}%`, width: `${width}%` }}
        />
        {segments?.map((seg) => {
          const sl = pct(seg.start, duration);
          const sw = Math.max(pct(seg.end - seg.start, duration), 0.4);
          return (
            <button
              key={seg.key}
              type="button"
              data-testid="timeline-segment"
              data-key={seg.key}
              aria-label={`Model call${seg.calls.length > 1 ? `s ${seg.calls[0].index + 1} to ${seg.calls[seg.calls.length - 1].index + 1}` : ` ${seg.calls[0].index + 1}`}, ${startWords(seg.start).toLowerCase()}, took ${durationWords(seg.end - seg.start)}`}
              aria-pressed={picked === seg.key}
              onClick={() => onPick(seg.key)}
              onFocus={() => onPick(seg.key)}
              className={`absolute top-1/2 h-3 -translate-y-1/2 rounded-sm ${segTone(seg.state)} focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent ${picked === seg.key ? "ring-1 ring-accent" : ""}`}
              style={{ left: `${sl}%`, width: `${sw}%` }}
            />
          );
        })}
        <span
          aria-hidden
          data-testid="timeline-bar-end"
          data-place={place}
          className={`pointer-events-none absolute top-1/2 -translate-y-1/2 whitespace-nowrap px-1 text-[11px] ${place === "inside" ? "text-zinc-200" : "text-zinc-500"}`}
          style={
            place === "after"
              ? { left: `${left + width}%` }
              : place === "before"
                ? { right: `${100 - left}%` }
                : { right: `${Math.max(0, 100 - left - width)}%` }
          }
        >
          {barEnd(row.end - row.start, usage)}
        </span>
      </div>
    </div>
  );
});

/* ------------------------------------------------------------ the tab */

export const HarnessTimeline = memo(function HarnessTimeline({
  view,
  live,
  models = [],
}: {
  view: BuildSessionView;
  live: LiveTimelineHandle | null;
  models?: readonly CatalogRow[];
}) {
  const running = !!live?.running;
  const [choice, setChoice] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [picked, setPicked] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [scrollTop, setScrollTop] = useState(0);
  const [boxH, setBoxH] = useState(0);
  const boxRef = useRef<HTMLDivElement | null>(null);

  // The live reply is shown while it runs, and after it ends until the
  // session holds its saved line (no flash of the reply before it).
  const liveTurn = live?.turnId ?? null;
  const savedLive = !!liveTurn && view.messages.some((m) => m.role === "assistant" && m.turnId === liveTurn);
  const liveShown = running || (!!live && live.store.version() > 0 && !!liveTurn && !savedLive);
  const choices = useMemo(
    () => replyChoices(view.messages, liveShown ? (running ? LIVE_CHOICE : JUST_ENDED_CHOICE) : null),
    [view.messages, liveShown, running],
  );
  const effective =
    choice && choices.some((c) => c.value === choice) ? choice : liveShown ? "live" : (choices.find((c) => c.value !== "live")?.value ?? null);

  // The running reply, read once a second while this tab is on screen.
  useVisibleInterval(() => setTick((t) => t + 1), LIVE_READ_MS, effective === "live" && running);

  const reply: TimelineReply | null = useMemo(() => {
    if (effective === "live" && live) {
      const now = running && live.startedAt ? Date.now() - live.startedAt : 0;
      const snap = live.store.snapshot(now);
      return running ? snap : { ...snap, running: false };
    }
    if (effective === null) return null;
    const m = view.messages[Number(effective)];
    return m ? replyTimeline(m) : null;
    // `tick` re-reads the live store (it is mutable by design).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effective, live, running, view.messages, tick]);

  const rows = useMemo(() => (reply ? timelineRows(reply, collapsed) : []), [reply, collapsed]);
  const segments = useMemo(() => (reply ? callSegments(reply.calls, reply.durationMs) : []), [reply]);
  const totals = useMemo(() => (reply ? timelineTotals(reply) : null), [reply]);
  const ticks = useMemo(() => axisTicks(reply?.durationMs ?? 0), [reply?.durationMs]);

  // The rows' box exists only while a reply is drawn: measured again when it
  // appears (or goes).
  const drawn = reply !== null;
  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    setBoxH(el.clientHeight);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setBoxH(el.clientHeight));
    ro.observe(el);
    return () => ro.disconnect();
  }, [drawn]);

  const onToggle = useCallback((key: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
  const onPick = useCallback((key: string) => setPicked(key), []);
  const onKey = useCallback((e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "Escape") setPicked(null);
  }, []);

  const pickedItem: Picked | null = useMemo(() => {
    if (!picked || !reply) return null;
    if (picked === "reply") return { kind: "reply" };
    const seg = segments.find((s) => s.key === picked);
    if (seg) return { kind: "segment", segment: seg };
    const row = rows.find((r) => r.key === picked);
    if (row?.kind === "step") return { kind: "step", step: row.step };
    if (row?.kind === "helper") return { kind: "helper", helper: row.helper };
    return null;
  }, [picked, reply, rows, segments]);

  if (!choices.length && !liveShown) {
    return (
      <p data-testid="timeline-empty" className="text-[13px] text-zinc-400">
        {NO_REPLIES}
      </p>
    );
  }

  const route = reply?.route;
  const replyName = route ? modelName(route.provider, route.model, models) : modelName(view.setup.provider, view.setup.model, models);
  const rowLabel = (r: TimelineRow): string => {
    if (r.kind === "reply") return replyName;
    if (r.kind === "helper") {
      const h = r.helper;
      const m = h.provider || h.model ? modelName(h.provider, h.model, models) : "";
      return m ? `${helperName(h.agent)} · ${m}` : helperName(h.agent);
    }
    return stepLabel(r.step);
  };

  const visible = boxH > 0 ? Math.ceil(boxH / ROW_H) : FALLBACK_ROWS;
  const first = Math.max(0, Math.floor(scrollTop / ROW_H) - OVERSCAN);
  const last = Math.min(rows.length, first + visible + 2 * OVERSCAN);
  const cost = costWords(totals?.usage);
  const tokens = tokenWords(totals?.usage);
  const helperCost = costWords(totals?.helperUsage);
  const helperTokens = tokenWords(totals?.helperUsage);

  return (
    <div data-testid="pane-timeline" className="flex h-full min-h-0 flex-col gap-2" onKeyDown={onKey}>
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <label className="flex min-w-0 flex-1 items-center gap-1.5 text-[12px] text-zinc-500">
          <span className="shrink-0">Reply</span>
          <select
            data-testid="timeline-reply"
            aria-label="Which reply"
            value={effective ?? ""}
            onChange={(e) => {
              setChoice(e.target.value);
              setPicked(null);
              setScrollTop(0);
              if (boxRef.current) boxRef.current.scrollTop = 0;
            }}
            className="min-w-0 max-w-full flex-1 truncate rounded-md bg-white/[0.04] px-1.5 py-0.5 text-[12px] text-zinc-200 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50"
          >
            {choices.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
        </label>
        {effective === "live" && running ? (
          <span
            data-testid="timeline-live"
            className="inline-flex shrink-0 items-center gap-1 rounded-full bg-tone-success/10 px-2 py-0.5 text-[11px] font-medium text-tone-success"
          >
            <span aria-hidden className="inline-block h-1.5 w-1.5 rounded-full bg-tone-success" />
            Live
          </span>
        ) : null}
      </div>

      {!reply || !totals ? (
        <div data-testid="timeline-none" className="space-y-1">
          <p className="text-[13px] text-zinc-300">{NO_TIMELINE}</p>
          <p className="text-[12px] text-zinc-500">
            {NO_TIMELINE_REASON[
              effective !== null && effective !== "live" ? noTimelineReason(view.messages[Number(effective)]) : "before"
            ]}
          </p>
        </div>
      ) : (
        <>
          <div data-testid="timeline-totals" className="shrink-0 space-y-0.5">
            <p className="text-[13px] text-zinc-100">
              {[durationWords(totals.durationMs), cost, tokens].filter(Boolean).map((part, i) => (
                <span key={i}>
                  {i ? <span aria-hidden className="text-zinc-600"> · </span> : null}
                  <span title={part === cost && totals.usage?.list_price_equivalent ? LIST_PRICE_TITLE : undefined}>{part}</span>
                </span>
              ))}
            </p>
            <p className="text-[12px] text-zinc-500">
              {[
                plural(totals.calls, "model call"),
                plural(totals.steps, "step"),
                totals.checks ? plural(totals.checks, "check") : null,
                totals.failed ? `${totals.failed.toLocaleString("en-US")} failed` : null,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
            {(() => {
              // v1.391.0: a reply a stop cut off (it kept what ran), and the
              // reply that carried it on, say so.
              const saved = effective !== null && effective !== "live" ? view.messages[Number(effective)] : null;
              const note = timelineResumeNote(saved, carriedOnTurns(view.messages));
              return note ? (
                <p data-testid="timeline-resume-note" className="text-[12px] text-zinc-400">
                  {note}
                </p>
              ) : null;
            })()}
            {totals.helpers ? (
              <p data-testid="timeline-helpers-apart" className="text-[12px] text-zinc-500">
                {`${plural(totals.helpers, "helper")}: ${[plural(totals.helperSteps, "step"), helperCost, helperTokens]
                  .filter(Boolean)
                  .join(" · ")}${helperCost ? `, ${HELPERS_APART}` : ""}`}
              </p>
            ) : null}
          </div>

          <div
            data-testid="timeline-axis"
            aria-hidden
            className="grid shrink-0 gap-2"
            style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(0, 3fr)" }}
          >
            <span />
            <div className="relative h-4 min-w-0 overflow-hidden border-b border-white/[0.06]">
              {ticks.map((t) => {
                const at = pct(t, reply.durationMs);
                return (
                  <span
                    key={t}
                    className="absolute top-0 whitespace-nowrap text-[11px] text-zinc-500"
                    style={at > 80 ? { right: `${100 - at}%` } : { left: `${at}%` }}
                  >
                    {tickLabel(t)}
                  </span>
                );
              })}
            </div>
          </div>

          <div
            ref={boxRef}
            data-testid="timeline-rows"
            onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
            className="min-h-0 flex-1 overflow-y-auto overflow-x-hidden"
          >
            <div style={{ height: first * ROW_H }} />
            {rows.slice(first, last).map((r) => (
              <div key={r.key}>
                <Row
                  row={r}
                  duration={reply.durationMs}
                  label={rowLabel(r)}
                  usage={r.kind === "reply" ? reply.usage : r.kind === "helper" ? r.helper.usage : undefined}
                  segments={r.kind === "reply" ? segments : undefined}
                  open={!collapsed.has(r.key)}
                  picked={picked}
                  onPick={onPick}
                  onToggle={onToggle}
                />
              </div>
            ))}
            <div style={{ height: Math.max(0, rows.length - last) * ROW_H }} />
          </div>

          {pickedItem ? <Card picked={pickedItem} reply={reply} models={models} onClose={() => setPicked(null)} /> : null}
        </>
      )}
    </div>
  );
});
