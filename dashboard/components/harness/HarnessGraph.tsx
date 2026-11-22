"use client";

/**
 * THE GRAPH (v1.549.0): the Timeline tab's first view — the models that did
 * a coding-session reply, as nodes, joined by the work they handed each
 * other (lib/buildGraph builds it from the Timeline's own data; nothing is
 * drawn that did not happen).
 *
 *  - A node says its model, its role, how much it thinks (the lead) and
 *    where it runs, what it is doing and for how long. Its state is told by
 *    a quiet glow AND in words (never by hue alone).
 *  - Pointer or keyboard focus on a node shows its numbers; on an edge, the
 *    words that went across it (the task handed over, the report sent back).
 *  - A press opens the node's steps on the time axis (the Timeline as it
 *    was); the graph keeps updating underneath.
 *  - Calm: nodes never move while the reply runs; a handoff lights its edge
 *    for a few seconds (stopped under reduced motion), nothing else moves.
 */

import { memo, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  edgeFresh,
  graphLayout,
  graphNodes,
  shortDuration,
  type GraphEdge,
  type GraphNode,
  type NodeState,
  type ReplyGraph,
} from "@/lib/buildGraph";
import { addUsage, type TimelineReply } from "@/lib/buildTimeline";
import { helperName } from "@/lib/buildHelpers";
import { secondsText } from "@/lib/durationWords";
import { costWords, stepLabel, tokenWords } from "@/lib/timelineWords";
import type { CatalogRow } from "@/lib/answeredModel";

/** About how tall a node's card is (px): it opens above a node with less
 *  than this below it. */
const CARD_ROOM = 200;

export const HELPERS_APART_SHORT = "not in the reply's cost";
export const OPEN_HINT = "Press to see its steps.";
export const NO_REPORT_YET = "No report yet.";
export const NO_TASK_WORDS = "The words it was given were not kept.";

/** The words for each state (a glow is never the only cue). */
export const NODE_STATE_WORDS: Readonly<Record<NodeState, string>> = {
  working: "Working",
  waiting: "Waiting",
  done: "Done",
  failed: "Failed",
  stopped: "Stopped",
  queued: "Not started",
};

const GLOW: Readonly<Record<NodeState, string>> = {
  working:
    "border-tone-success/50 shadow-[0_0_0_1px_rgb(var(--tone-success)/0.22),0_0_18px_rgb(var(--tone-success)/0.16)]",
  waiting:
    "border-tone-warn/50 shadow-[0_0_0_1px_rgb(var(--tone-warn)/0.2),0_0_18px_rgb(var(--tone-warn)/0.14)]",
  done: "border-zinc-300/25 shadow-[0_0_14px_rgb(var(--zinc-100)/0.06)]",
  failed:
    "border-tone-danger/55 shadow-[0_0_0_1px_rgb(var(--tone-danger)/0.22),0_0_18px_rgb(var(--tone-danger)/0.16)]",
  stopped: "border-zinc-600/50",
  queued: "border-dashed border-zinc-600/60 opacity-60",
};

const DOT: Readonly<Record<NodeState, string>> = {
  working: "bg-tone-success",
  waiting: "bg-tone-warn",
  done: "bg-zinc-200",
  failed: "bg-tone-danger",
  stopped: "bg-zinc-500",
  queued: "border border-zinc-500 bg-transparent",
};

const STATE_TEXT: Readonly<Record<NodeState, string>> = {
  working: "text-tone-success",
  waiting: "text-tone-warn",
  done: "text-zinc-400",
  failed: "text-tone-danger",
  stopped: "text-zinc-500",
  queued: "text-zinc-500",
};

function edgeTone(edge: GraphEdge, fresh: boolean): string {
  if (edge.state === "failed") return "text-tone-danger/70";
  if (fresh || edge.state === "active") return "text-tone-success/80";
  if (edge.state === "pending") return "text-zinc-600";
  if (edge.state === "stopped") return "text-zinc-600";
  return "text-zinc-500";
}

/** Where a model runs, in the receipt's words: "Local" (this PC or your own
 *  network) or "Cloud"; null when the model list does not say. */
export function wherePlace(provider: string, model: string, models: readonly CatalogRow[]): "Local" | "Cloud" | null {
  if (!provider || provider === "mock") return null;
  const rows = models as readonly (CatalogRow & { on_this_network?: unknown; kind?: unknown })[];
  const row = rows.find((m) => m.provider === provider && m.model === model) ?? rows.find((m) => m.provider === provider);
  if (!row) return null;
  if (typeof row.on_this_network === "boolean") return row.on_this_network ? "Local" : "Cloud";
  if (row.kind === "local") return "Local";
  if (row.kind === "cli" || row.kind === "api") return "Cloud";
  return null;
}

/** "High" for "high"; null for no level (or one it cannot name). */
export function effortWord(level: string | null | undefined): string | null {
  const l = (level ?? "").trim().toLowerCase();
  if (l === "low" || l === "medium" || l === "high" || l === "max") return l[0].toUpperCase() + l.slice(1);
  if (l === "xhigh") return "Extra high";
  return null;
}

export interface GraphNames {
  /** A model's name ("Opus 5.5"), from the catalog. */
  model: (provider: string, model: string) => string;
}

/** What a node is doing, in words. */
export function nodeStatusWords(node: GraphNode): string {
  const a = node.activity;
  if (node.state === "working") {
    if (a?.kind === "step") return stepLabel(a.step);
    if (a?.kind === "thinking") return "Thinking";
    return "Working";
  }
  if (node.state === "waiting") return node.kind === "back" ? "Reports in" : "Waiting for helpers";
  if (node.state === "queued") return node.kind === "back" ? "Waiting for the reports" : NODE_STATE_WORDS.queued;
  return NODE_STATE_WORDS[node.state];
}

function nodeTitle(node: GraphNode, names: GraphNames): string {
  if (node.kind === "helper") {
    const h = node.helper;
    const m = node.provider || node.model ? names.model(node.provider, node.model) : "";
    return m || helperName(h?.agent ?? "");
  }
  return names.model(node.provider, node.model) || "The model";
}

function nodeRole(node: GraphNode, hasHelpers: boolean): string | null {
  if (node.kind === "helper") return helperName(node.helper?.agent ?? "");
  if (node.kind === "back") return null;
  return hasHelpers ? "Lead" : null;
}

function elapsed(node: GraphNode, now: number): number {
  if (node.kind === "lead") return now;
  return Math.max(0, node.end - node.start);
}

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

function plural(n: number, noun: string): string {
  return `${n.toLocaleString("en-US")} ${noun}${n === 1 ? "" : "s"}`;
}

function atWords(ms: number | null): string | null {
  if (ms === null) return null;
  return ms <= 0 ? "At the start" : `${secondsText(ms) ?? "0 s"} in`;
}

/* ------------------------------------------------------------ the hover card */

type Hover = { kind: "node" | "edge"; key: string } | null;

function NodeFacts({
  node,
  reply,
  names,
  models,
  effort,
  hasHelpers,
}: {
  node: GraphNode;
  reply: TimelineReply;
  names: GraphNames;
  models: readonly CatalogRow[];
  effort: string | null;
  hasHelpers: boolean;
}) {
  const where = wherePlace(node.provider, node.model, models);
  const model = node.provider || node.model ? names.model(node.provider, node.model) : null;
  const now = nodeStatusWords(node);
  if (node.kind === "helper" && node.helper) {
    const h = node.helper;
    const cost = costWords(h.usage);
    return (
      <Facts
        rows={[
          ["Status", NODE_STATE_WORDS[node.state]],
          ["Now", node.state === "working" ? now : null],
          ["Model", model ? (where ? `${model} · ${where}` : model) : null],
          ["Why", h.reason || null],
          ["Started", atWords(h.start)],
          ["Took", secondsText(elapsed(node, reply.durationMs)) ?? "0 s"],
          ["Steps", h.stepCount ? h.stepCount.toLocaleString("en-US") : null],
          ["Tokens", tokenWords(h.usage)],
          ["Cost", cost ? `${cost}, ${HELPERS_APART_SHORT}` : null],
        ]}
      />
    );
  }
  const own = node.kind === "lead";
  const steps = own ? reply.steps.filter((s) => !s.helper) : [];
  const failed = steps.filter((s) => s.state === "failed").length;
  const usage = own ? reply.usage : addUsage(node.calls);
  return (
    <Facts
      rows={[
        ["Status", NODE_STATE_WORDS[node.state]],
        ["Now", node.state === "working" || node.state === "waiting" ? now : null],
        ["Model", model ? (where ? `${model} · ${where}` : model) : null],
        ["Thinking", own ? effort : null],
        ["Took", secondsText(elapsed(node, reply.durationMs)) ?? "0 s"],
        ["Model calls", node.calls.length ? node.calls.length.toLocaleString("en-US") : null],
        ["Steps", own && steps.length ? `${steps.length.toLocaleString("en-US")}${failed ? `, ${failed} failed` : ""}` : null],
        ["Helpers", own && hasHelpers ? reply.helpers.length.toLocaleString("en-US") : null],
        ["Tokens", tokenWords(usage)],
        ["Cost", costWords(usage)],
      ]}
    />
  );
}

function EdgeFacts({ edge }: { edge: GraphEdge }) {
  const h = edge.helper;
  if (edge.kind === "handoff") {
    return (
      <div className="space-y-1">
        <p className="text-[12px] text-zinc-500">{atWords(edge.at) ? `Handed over ${atWords(edge.at)?.toLowerCase()}` : "Handed over"}</p>
        {h.reason ? <p className="text-[12px] text-zinc-400">{`Why: ${h.reason}`}</p> : null}
        <p data-testid="graph-edge-words" className="line-clamp-[10] whitespace-pre-wrap break-words text-[12px] text-zinc-200">
          {h.task || NO_TASK_WORDS}
        </p>
      </div>
    );
  }
  const words =
    edge.state === "pending" ? NO_REPORT_YET : h.result || (edge.state === "failed" ? "It failed before it reported." : NO_TASK_WORDS);
  return (
    <div className="space-y-1">
      <p className="text-[12px] text-zinc-500">
        {edge.state === "pending" ? "Still working" : edge.at !== null ? `Sent back ${atWords(edge.at)?.toLowerCase()}` : "Sent back"}
      </p>
      <p data-testid="graph-edge-words" className="line-clamp-[10] whitespace-pre-wrap break-words text-[12px] text-zinc-200">
        {words}
      </p>
    </div>
  );
}

/* ------------------------------------------------------------ the view */

export const HarnessGraph = memo(function HarnessGraph({
  graph,
  reply,
  names,
  models,
  effort,
  onOpen,
}: {
  graph: ReplyGraph;
  reply: TimelineReply;
  names: GraphNames;
  models: readonly CatalogRow[];
  /** The lead's thinking level, in words ("High"), or null. */
  effort: string | null;
  /** A press on a node: open its steps. */
  onOpen: (key: string) => void;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<Hover>(null);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const layout = useMemo(() => graphLayout(graph, width || 320), [graph, width]);
  const nodes = useMemo(() => graphNodes(graph), [graph]);
  const hasHelpers = graph.waves.length > 0;
  const edgeByKey = useMemo(() => new Map(graph.edges.map((e) => [e.key, e])), [graph.edges]);
  const nodeByKey = useMemo(() => new Map(nodes.map((n) => [n.key, n])), [nodes]);

  const name = (key: string): string => {
    const n = nodeByKey.get(key);
    return n ? nodeTitle(n, names) : "";
  };

  // The hover card: below its node (or at its edge's middle), inside the
  // canvas's width.
  let card: ReactNode = null;
  if (hover) {
    const cardW = Math.min(layout.width - 16, 300);
    if (hover.kind === "node") {
      const n = nodeByKey.get(hover.key);
      const b = layout.boxes[hover.key];
      if (n && b) {
        const left = Math.max(8, Math.min(b.x, layout.width - cardW - 8));
        // A node low in the graph opens its card ABOVE it, so the card never
        // lands past the end of what is drawn.
        const above = b.y > CARD_ROOM && b.y + b.h + CARD_ROOM > layout.height;
        card = (
          <div
            data-testid="graph-card"
            data-place={above ? "above" : "below"}
            role="tooltip"
            id="graph-card"
            className="pointer-events-none absolute z-20 rounded-xl border border-white/10 bg-zinc-900 p-2.5 shadow-lg shadow-black/40"
            style={
              above
                ? { left, top: b.y - 6, width: cardW, transform: "translateY(-100%)" }
                : { left, top: b.y + b.h + 6, width: cardW }
            }
          >
            <p className="mb-1 truncate text-[12px] font-medium text-zinc-100">{nodeTitle(n, names)}</p>
            <NodeFacts node={n} reply={reply} names={names} models={models} effort={effort} hasHelpers={hasHelpers} />
            <p className="mt-1.5 text-[11px] text-zinc-500">{OPEN_HINT}</p>
          </div>
        );
      }
    } else {
      const e = edgeByKey.get(hover.key);
      const p = layout.edges.find((x) => x.key === hover.key);
      if (e && p) {
        const left = Math.max(8, Math.min(p.mid.x - cardW / 2, layout.width - cardW - 8));
        card = (
          <div
            data-testid="graph-card"
            role="tooltip"
            id="graph-card"
            className="pointer-events-none absolute z-20 rounded-xl border border-white/10 bg-zinc-900 p-2.5 shadow-lg shadow-black/40"
            style={{ left, top: p.mid.y + 8, width: cardW }}
          >
            <p className="mb-1 truncate text-[12px] font-medium text-zinc-100">
              {`${name(e.from)} → ${name(e.to)}`}
            </p>
            <EdgeFacts edge={e} />
          </div>
        );
      }
    }
  }

  return (
    <div
      ref={boxRef}
      data-testid="timeline-graph"
      className="relative min-h-0 flex-1 overflow-y-auto overflow-x-hidden"
      onMouseLeave={() => setHover(null)}
    >
      <div className="relative" style={{ height: layout.height }}>
        <svg
          width={layout.width}
          height={layout.height}
          className="pointer-events-none absolute left-0 top-0 overflow-visible"
        >
          {layout.edges.map((p) => {
            const e = edgeByKey.get(p.key);
            if (!e) return null;
            const fresh = edgeFresh(e, graph);
            const dashed = e.state === "active" || e.state === "pending";
            const from = name(e.from);
            const to = name(e.to);
            const label =
              e.kind === "handoff"
                ? `${from} handed work to ${to}${e.helper.task ? `: ${e.helper.task}` : ""}`
                : `${from} ${e.state === "pending" ? "has not reported to" : "reported to"} ${to}${e.state !== "pending" && e.helper.result ? `: ${e.helper.result}` : ""}`;
            return (
              <g
                key={p.key}
                data-testid="graph-edge"
                data-key={p.key}
                data-kind={e.kind}
                data-state={e.state}
                data-fresh={fresh ? "true" : "false"}
                className={`${edgeTone(e, fresh)} transition-colors duration-500`}
              >
                <path
                  d={p.d}
                  fill="none"
                  stroke="currentColor"
                  strokeWidth={1.5}
                  strokeLinejoin="round"
                  strokeDasharray={dashed && !fresh ? "4 4" : undefined}
                  className={fresh ? "graph-edge-flow" : undefined}
                />
                <path d={p.arrow} fill="none" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" />
                <path
                  data-testid="graph-edge-hit"
                  d={p.d}
                  fill="none"
                  stroke="transparent"
                  strokeWidth={12}
                  tabIndex={0}
                  role="img"
                  aria-label={label.slice(0, 400)}
                  className="cursor-default outline-none focus-visible:stroke-accent/20"
                  style={{ pointerEvents: "stroke" }}
                  onMouseEnter={() => setHover({ kind: "edge", key: p.key })}
                  onFocus={() => setHover({ kind: "edge", key: p.key })}
                  onMouseLeave={() => setHover(null)}
                  onBlur={() => setHover(null)}
                />
              </g>
            );
          })}
        </svg>

        {nodes.map((n) => {
          const b = layout.boxes[n.key];
          if (!b) return null;
          const title = nodeTitle(n, names);
          const status = nodeStatusWords(n);
          const time = shortDuration(elapsed(n, graph.now));
          if (n.kind === "back") {
            return (
              <button
                key={n.key}
                type="button"
                data-testid="graph-node"
                data-key={n.key}
                data-kind={n.kind}
                data-state={n.state}
                aria-label={`${title}, back with the reports: ${status}, ${time}. Open its steps.`}
                aria-describedby={hover?.kind === "node" && hover.key === n.key ? "graph-card" : undefined}
                onClick={() => onOpen(n.key)}
                onMouseEnter={() => setHover({ kind: "node", key: n.key })}
                onFocus={() => setHover({ kind: "node", key: n.key })}
                onMouseLeave={() => setHover(null)}
                onBlur={() => setHover(null)}
                className={`absolute flex items-center gap-2 rounded-full border bg-zinc-900/70 px-3 text-left transition-[box-shadow,border-color,opacity] duration-500 hover:bg-zinc-800/70 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${GLOW[n.state]}`}
                style={{ left: b.x, top: b.y, width: b.w, height: b.h }}
              >
                <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${DOT[n.state]}`} />
                <span className="min-w-0 flex-1 truncate text-[12px]">
                  <span className="text-zinc-200">{title}</span>
                  <span className={STATE_TEXT[n.state]}>{` · ${status}`}</span>
                </span>
                <span className="shrink-0 text-[11px] tabular-nums text-zinc-500">{time}</span>
              </button>
            );
          }
          const role = nodeRole(n, hasHelpers);
          const where = wherePlace(n.provider, n.model, models);
          const sub = [role, n.kind === "lead" ? effort : null, where].filter(Boolean).join(" · ");
          return (
            <button
              key={n.key}
              type="button"
              data-testid="graph-node"
              data-key={n.key}
              data-kind={n.kind}
              data-state={n.state}
              aria-label={`${title}${sub ? `, ${sub}` : ""}: ${status}, ${time}. Open its steps.`}
              aria-describedby={hover?.kind === "node" && hover.key === n.key ? "graph-card" : undefined}
              onClick={() => onOpen(n.key)}
              onMouseEnter={() => setHover({ kind: "node", key: n.key })}
              onFocus={() => setHover({ kind: "node", key: n.key })}
              onMouseLeave={() => setHover(null)}
              onBlur={() => setHover(null)}
              className={`absolute flex flex-col justify-center gap-0.5 rounded-xl border bg-zinc-900/70 px-3 text-left transition-[box-shadow,border-color,opacity] duration-500 hover:bg-zinc-800/70 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/60 ${GLOW[n.state]}`}
              style={{ left: b.x, top: b.y, width: b.w, height: b.h }}
            >
              <span className="flex min-w-0 items-center gap-2">
                <span aria-hidden className={`h-2 w-2 shrink-0 rounded-full ${DOT[n.state]}`} />
                <span data-testid="graph-node-title" className={`min-w-0 truncate font-medium text-zinc-100 ${n.kind === "lead" ? "text-[14px]" : "text-[13px]"}`}>
                  {title}
                </span>
              </span>
              {sub ? <span className="truncate pl-4 text-[11px] text-zinc-500">{sub}</span> : null}
              <span className="flex min-w-0 items-baseline gap-2 pl-4">
                <span data-testid="graph-node-status" className={`min-w-0 flex-1 truncate text-[12px] ${STATE_TEXT[n.state]}`}>
                  {status}
                </span>
                <span className="shrink-0 text-[11px] tabular-nums text-zinc-500">{time}</span>
              </span>
            </button>
          );
        })}

        {card}
      </div>

      {hasHelpers ? (
        <div
          data-testid="graph-legend"
          aria-hidden
          className="flex flex-wrap justify-center gap-x-4 gap-y-1 pb-1 pt-2 text-[11px] text-zinc-500"
        >
          {(["working", "waiting", "done", "failed"] as const).map((s) => (
            <span key={s} className="inline-flex items-center gap-1.5">
              <span className={`h-1.5 w-1.5 rounded-full ${DOT[s]}`} />
              {s === "working" ? "Working" : s === "waiting" ? "Waiting" : s === "done" ? "Done" : "Failed"}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
});
