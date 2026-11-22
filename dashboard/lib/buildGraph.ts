/**
 * THE GRAPH (v1.549.0): a coding-session reply drawn as the models that did
 * it, joined by the work they handed each other — the Timeline tab's first
 * view. Built ONLY from the Timeline's own data (lib/buildTimeline
 * `TimelineReply`: the reply's model calls, its steps, its helpers with their
 * task, report, model and times), so the running reply, a rejoin and a saved
 * reply draw one picture, and nothing is drawn that did not happen:
 *
 *  - The LEAD: the model you talk to (one node for the whole reply).
 *  - WAVES: helpers whose times overlap ran together (one row, side by side);
 *    a helper that started after the last one ended starts a new wave below.
 *  - BACK nodes: the lead again after a wave, shown only when it really took
 *    the reports up (a model call at or after the wave's end), or while the
 *    reply still runs (waiting for them). A reply that ended before the lead
 *    asked again draws no return at all.
 *  - EDGES: a HANDOFF (lead -> helper, its words are the helper's task) and a
 *    REPORT (helper -> the lead's next node, its words are the helper's
 *    report). A report edge exists only where a back node does.
 *
 * Layout is computed here too (`graphLayout`), from the panel's width alone:
 * a node's place depends only on its wave and its order in it, so a node
 * never moves while the reply runs (a new helper is added after the others).
 */

import type { BarState, TimelineCall, TimelineHelper, TimelineReply, TimelineStep } from "@/lib/buildTimeline";

/** What a node's glow says. `waiting`: it waits on others or is about to take
 *  their work; `queued`: it has not started. */
export type NodeState = "working" | "waiting" | "done" | "failed" | "stopped" | "queued";

/** What a node is doing right now: a step (its words come from the run
 *  log's), a model call in flight, or waiting on its helpers. */
export type NodeActivity =
  | { kind: "step"; step: TimelineStep }
  | { kind: "thinking" }
  | { kind: "waiting" }
  | null;

export interface GraphNode {
  /** "lead", `helper:<id>` (the Timeline's own row key), `back:<wave>`. */
  key: string;
  kind: "lead" | "helper" | "back";
  state: NodeState;
  /** ms on the reply's clock. */
  start: number;
  end: number;
  provider: string;
  model: string;
  activity: NodeActivity;
  /** The lead's (or this back node's) model calls. */
  calls: TimelineCall[];
  helper?: TimelineHelper;
  wave?: number;
}

export interface GraphEdge {
  key: string;
  from: string;
  to: string;
  kind: "handoff" | "report";
  /** `active`: the work is with the helper now; `pending`: the report has not
   *  come yet; `done` / `failed` / `stopped`: how it went. */
  state: "active" | "pending" | "done" | "failed" | "stopped";
  /** When the work changed hands (ms on the reply's clock), null if unknown. */
  at: number | null;
  helper: TimelineHelper;
}

export interface GraphWave {
  index: number;
  helpers: GraphNode[];
  back: GraphNode | null;
}

export interface ReplyGraph {
  lead: GraphNode;
  waves: GraphWave[];
  edges: GraphEdge[];
  /** The reply's "now" (its length so far). */
  now: number;
  running: boolean;
}

/** A transfer is lit this long after it happened (ms), live only. */
export const FRESH_MS = 4000;
/** The reply clock stamps a helper's end before its report returns: a call
 *  that starts within this much before the end still counts as after it. */
const CLOCK_SLACK_MS = 5;

const HELPER_PREFIX = "helper:";

export function helperKey(id: string): string {
  return `${HELPER_PREFIX}${id}`;
}

export function helperIdOf(key: string): string | null {
  return key.startsWith(HELPER_PREFIX) ? key.slice(HELPER_PREFIX.length) : null;
}

function settled(state: BarState): NodeState {
  return state === "working" ? "working" : state;
}

/** The newest step still working, or null. */
function workingStep(steps: readonly TimelineStep[], skipHelperSteps: boolean): TimelineStep | null {
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const s = steps[i];
    if (s.state === "working" && !(skipHelperSteps && s.helper)) return s;
  }
  return null;
}

/**
 * Helpers in waves: sorted by start (a helper with no start keeps its place
 * after the timed ones), and one wave holds helpers whose times overlap. A
 * running helper (no end yet) reaches to `now`.
 */
export function helperWaves(helpers: readonly TimelineHelper[], now: number): TimelineHelper[][] {
  const timed = helpers
    .map((h, i) => ({ h, i }))
    .filter((x) => x.h.start !== null)
    .sort((a, b) => (a.h.start as number) - (b.h.start as number) || a.i - b.i)
    .map((x) => x.h);
  const untimed = helpers.filter((h) => h.start === null);
  const waves: TimelineHelper[][] = [];
  let waveEnd = -1;
  for (const h of timed) {
    const start = h.start as number;
    const end = h.end ?? (h.state === "working" ? Math.max(now, start) : start);
    const cur = waves[waves.length - 1];
    if (cur && start < waveEnd) {
      cur.push(h);
      waveEnd = Math.max(waveEnd, end);
    } else {
      waves.push([h]);
      waveEnd = end;
    }
  }
  for (const h of untimed) waves.push([h]);
  return waves;
}

/** The reply as a graph (see the file's head). */
export function replyGraph(reply: TimelineReply): ReplyGraph {
  const now = reply.durationMs;
  const running = reply.running;
  const calls = reply.calls;
  const callInFlight = calls.some((c) => c.state === "working");
  const helperWorking = reply.helpers.some((h) => h.state === "working");
  const ownStep = workingStep(reply.steps, true);

  // The lead, for the whole reply.
  let leadState: NodeState;
  let leadActivity: NodeActivity = null;
  if (!running) leadState = settled(reply.state);
  else if (callInFlight) {
    leadState = "working";
    leadActivity = { kind: "thinking" };
  } else if (ownStep) {
    leadState = "working";
    leadActivity = { kind: "step", step: ownStep };
  } else if (helperWorking) {
    leadState = "waiting";
    leadActivity = { kind: "waiting" };
  } else leadState = "working";
  const route = reply.route;
  const lastCall = calls[calls.length - 1];
  const lead: GraphNode = {
    key: "lead",
    kind: "lead",
    state: leadState,
    start: 0,
    end: now,
    provider: route?.provider || lastCall?.provider || "",
    model: route?.model || lastCall?.model || "",
    activity: leadActivity,
    calls: calls.slice(),
  };

  const grouped = helperWaves(reply.helpers, now);
  const waves: GraphWave[] = [];
  const edges: GraphEdge[] = [];
  let anchor = "lead";
  grouped.forEach((group, w) => {
    const helpers: GraphNode[] = group.map((h) => {
      const step = h.state === "working" ? workingStep(h.steps, false) : null;
      return {
        key: helperKey(h.id),
        kind: "helper",
        state: settled(h.state),
        start: h.start ?? 0,
        end: h.end ?? (h.state === "working" ? now : (h.start ?? 0)),
        provider: h.provider,
        model: h.model,
        activity: step ? { kind: "step", step } : null,
        calls: [],
        helper: h,
        wave: w,
      };
    });
    for (const n of helpers) {
      const h = n.helper as TimelineHelper;
      edges.push({
        key: `handoff:${h.id}`,
        from: anchor,
        to: n.key,
        kind: "handoff",
        state: h.state === "working" ? "active" : h.state,
        at: h.start,
        helper: h,
      });
    }

    // The lead again after this wave: only when it really took the reports
    // up, or while the reply still runs.
    const allEnded = group.every((h) => h.state !== "working");
    const ends = group.map((h) => h.end).filter((e): e is number => e !== null);
    const waveEnd = allEnded && ends.length ? Math.max(...ends) : null;
    const nextStart = grouped[w + 1]?.[0]?.start ?? null;
    const isLast = w === grouped.length - 1;
    let back: GraphNode | null = null;
    if (waveEnd === null) {
      if (running && group.every((h) => h.start !== null)) {
        back = backNode(w, "queued", now, now, lead, [], { kind: "waiting" });
      }
    } else {
      const mine = calls.filter(
        (c) => c.start >= waveEnd - CLOCK_SLACK_MS && (nextStart === null || c.start < nextStart),
      );
      if (mine.length || (running && isLast)) {
        let state: NodeState;
        let activity: NodeActivity = null;
        if (mine.some((c) => c.state === "working")) {
          state = "working";
          activity = { kind: "thinking" };
        } else if (!isLast) state = "done";
        else if (!running) state = settled(reply.state);
        else if (!mine.length) {
          // The reports are in; the lead has not been asked again yet.
          state = "waiting";
        } else if (ownStep) {
          state = "working";
          activity = { kind: "step", step: ownStep };
        } else state = "working";
        const end = nextStart ?? now;
        back = backNode(w, state, waveEnd, Math.max(waveEnd, end), lead, mine, activity);
      }
    }
    if (back) {
      for (const n of helpers) {
        const h = n.helper as TimelineHelper;
        edges.push({
          key: `report:${h.id}`,
          from: n.key,
          to: back.key,
          kind: "report",
          state: h.state === "working" ? "pending" : h.state,
          at: h.state === "working" ? null : h.end,
          helper: h,
        });
      }
      anchor = back.key;
    }
    waves.push({ index: w, helpers, back });
  });

  return { lead, waves, edges, now, running };
}

function backNode(
  wave: number,
  state: NodeState,
  start: number,
  end: number,
  lead: GraphNode,
  calls: TimelineCall[],
  activity: NodeActivity,
): GraphNode {
  return {
    key: `back:${wave}`,
    kind: "back",
    state,
    start,
    end,
    provider: lead.provider,
    model: lead.model,
    activity,
    calls,
    wave,
  };
}

/** Whether a transfer just happened (live only): its edge lights up. */
export function edgeFresh(edge: GraphEdge, graph: Pick<ReplyGraph, "now" | "running">): boolean {
  if (!graph.running || edge.at === null) return false;
  const age = graph.now - edge.at;
  return age >= 0 && age < FRESH_MS;
}

/** Every node, in drawing order. */
export function graphNodes(graph: ReplyGraph): GraphNode[] {
  const out = [graph.lead];
  for (const w of graph.waves) {
    out.push(...w.helpers);
    if (w.back) out.push(w.back);
  }
  return out;
}

/* ------------------------------------------------------------ layout */

export const LAYOUT = {
  pad: 8,
  leadH: 76,
  helperH: 72,
  backH: 40,
  gapY: 44,
  colGap: 12,
  leadMaxW: 300,
  backMaxW: 240,
} as const;

export interface Box {
  key: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface EdgePath {
  key: string;
  /** The SVG path, from the source to the arrow's tip. */
  d: string;
  /** The arrow at the tip. */
  arrow: string;
  /** Where the edge's hover card is placed. */
  mid: { x: number; y: number };
}

export interface GraphLayout {
  width: number;
  height: number;
  boxes: Record<string, Box>;
  edges: EdgePath[];
}

/** How many helpers sit side by side at this width: two in the panel at its
 *  narrowest (about 300 px beside the conversation), so helpers that ran
 *  together are SEEN together. */
export function graphColumns(width: number): number {
  return width < 250 ? 1 : width < 520 ? 2 : 3;
}

const r = (n: number) => Math.round(n * 10) / 10;

function arrowDown(x: number, y: number): string {
  return `M${r(x - 4)} ${r(y - 5)} L${r(x)} ${r(y)} L${r(x + 4)} ${r(y - 5)}`;
}

function arrowRight(x: number, y: number): string {
  return `M${r(x - 5)} ${r(y - 4)} L${r(x)} ${r(y)} L${r(x - 5)} ${r(y + 4)}`;
}

/**
 * Where each node sits and how each edge runs, for a panel `width` px wide.
 * A wave's helpers fill rows of `graphColumns(width)`; a wave of one row is
 * centred, more rows are left-aligned so the gaps between columns line up
 * (an edge into a later row runs down the gap on the helper's left, and a
 * report out of an earlier row down the gap on its right — never across a
 * node).
 */
export function graphLayout(graph: ReplyGraph, width: number): GraphLayout {
  const L = LAYOUT;
  const W = Math.max(160, width);
  const inner = W - 2 * L.pad;
  const cols = graphColumns(W);
  const cellW = (inner - (cols - 1) * L.colGap) / cols;
  const boxes: Record<string, Box> = {};
  const edges: EdgePath[] = [];
  const centre = (w: number) => L.pad + (inner - w) / 2;

  const leadW = Math.min(inner, L.leadMaxW);
  boxes.lead = { key: "lead", x: centre(leadW), y: L.pad, w: leadW, h: L.leadH };
  let y = L.pad + L.leadH;

  for (const wave of graph.waves) {
    const n = wave.helpers.length;
    const lines = Math.ceil(n / cols);
    const top = y + L.gapY;
    wave.helpers.forEach((node, i) => {
      const line = Math.floor(i / cols);
      const col = i % cols;
      const offset = lines === 1 ? (inner - (n * cellW + (n - 1) * L.colGap)) / 2 : 0;
      boxes[node.key] = {
        key: node.key,
        x: L.pad + offset + col * (cellW + L.colGap),
        y: top + line * (L.helperH + L.colGap),
        w: cellW,
        h: L.helperH,
      };
    });
    y = top + lines * L.helperH + (lines - 1) * L.colGap;
    if (wave.back) {
      const bw = Math.min(inner, L.backMaxW);
      boxes[wave.back.key] = { key: wave.back.key, x: centre(bw), y: y + L.gapY, w: bw, h: L.backH };
      y = y + L.gapY + L.backH;
    }
  }

  for (const e of graph.edges) {
    const a = boxes[e.from];
    const b = boxes[e.to];
    if (!a || !b) continue;
    if (e.kind === "handoff") {
      const sx = a.x + a.w / 2;
      const sy = a.y + a.h;
      const bus = sy + L.gapY / 2;
      const firstRow = b.y - sy <= L.gapY + 1;
      if (firstRow) {
        const tx = b.x + b.w / 2;
        edges.push({
          key: e.key,
          d: `M${r(sx)} ${r(sy)} V${r(bus)} H${r(tx)} V${r(b.y)}`,
          arrow: arrowDown(tx, b.y),
          mid: { x: tx, y: bus },
        });
      } else {
        const gx = b.x - L.colGap / 2;
        const ty = b.y + b.h / 2;
        edges.push({
          key: e.key,
          d: `M${r(sx)} ${r(sy)} V${r(bus)} H${r(gx)} V${r(ty)} H${r(b.x)}`,
          arrow: arrowRight(b.x, ty),
          mid: { x: gx, y: ty },
        });
      }
    } else {
      const tx = b.x + b.w / 2;
      const bus = b.y - L.gapY / 2;
      const lastRow = b.y - (a.y + a.h) <= L.gapY + 1;
      if (lastRow) {
        const sx = a.x + a.w / 2;
        edges.push({
          key: e.key,
          d: `M${r(sx)} ${r(a.y + a.h)} V${r(bus)} H${r(tx)} V${r(b.y)}`,
          arrow: arrowDown(tx, b.y),
          mid: { x: sx, y: bus },
        });
      } else {
        const gx = a.x + a.w + L.colGap / 2;
        const sy = a.y + a.h / 2;
        edges.push({
          key: e.key,
          d: `M${r(a.x + a.w)} ${r(sy)} H${r(gx)} V${r(bus)} H${r(tx)} V${r(b.y)}`,
          arrow: arrowDown(tx, b.y),
          mid: { x: gx, y: bus },
        });
      }
    }
  }

  return { width: W, height: y + L.pad, boxes, edges };
}

/** "12 s", "3 min", "42 min", "1 h 12 min" — a node's time, short. */
export function shortDuration(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${h} h ${rest} min` : `${h} h`;
}
