/**
 * THE TIMELINE (v1.390.0, the long-jobs plan's Phase 1): one coding-session
 * reply on one time axis — the model you talk to (one bar, each of its model
 * calls a piece of it), every step it took under it, each helper with its own
 * model and its own steps — with time, tokens and cost.
 *
 * The daemon keeps WHEN (src/iron_jarvis/harness/timeline.py): every step's
 * `start` (ms from the reply's start), `calls` (each billed model call:
 * start, length, provider, model, usage in the receipt's shape), and each
 * helper's model, own usage and step times. The SAME numbers ride the live
 * frames (`model_call`, and `start` / `ms` on `round`, `tool_call` and
 * `helper`), so the running reply (`createLiveTimeline`), a rejoin and the
 * saved line (`replyTimeline`) draw one picture.
 *
 * Plain data: every shape crosses a JSON boundary, so each field is checked
 * and junk reads as absent. The words a person reads are the run log's
 * (lib/stepWords): steps, the model's name, helpers.
 */

import { cleanStepDetail, type StepDetail } from "@/lib/stepDetail";
import { turnUsageFrom, type TurnUsage } from "@/lib/types";
import { stepTarget } from "@/lib/workTarget";
import type { BuildMessage } from "@/lib/buildSessions";

/** The daemon's caps (routes/build MAX_STEPS / MAX_COUNTED_STEPS, harness/
 *  timeline MAX_CALLS, harness/helpers MAX_SAVED_STEPS): the live view keeps
 *  what the saved line will, so a reload draws the same picture. */
export const DETAIL_STEPS = 60;
export const MAX_STEPS = 2000;
export const MAX_CALLS = 2000;
export const HELPER_STEPS = 20;
export const MAX_HELPERS = 12;
/** At most this many pieces on the model's bar; more calls are drawn in
 *  groups (each still opens, as the calls it holds). */
export const MAX_SEGMENTS = 120;

export type BarState = "working" | "done" | "failed" | "stopped";

export interface TimelineCall {
  index: number;
  start: number;
  ms: number;
  provider: string;
  model: string;
  usage?: TurnUsage;
  /** "final" (asked for its final answer), "rewrite" (rewrote the answer in
   *  your language); absent for a call of the work itself. */
  kind?: "final" | "rewrite";
  state: BarState;
}

export interface TimelineStep {
  key: string;
  index: number;
  name: string;
  ok: boolean | null;
  start: number | null;
  ms: number | null;
  target: string | null;
  detail: StepDetail | null;
  /** This call STARTED a helper: the helper's own row stands in for it. */
  helper: boolean;
  /** v1.390.0 review: a step Iron Jarvis ran ITSELF, not the model — the
   *  finish check's test run ("finish") or a project check ("project")
   *  (the daemon's `check`, harness/timeline check_kind); null for the
   *  model's own step. */
  check: StepCheck | null;
  state: BarState;
}

export type StepCheck = "finish" | "project";

const checkOf = (v: unknown): StepCheck | null => (v === "finish" || v === "project" ? v : null);

export interface TimelineHelper {
  id: string;
  agent: string;
  task: string;
  result: string;
  state: BarState;
  start: number | null;
  end: number | null;
  provider: string;
  model: string;
  usage?: TurnUsage;
  /** Its steps (the first HELPER_STEPS), and how many it took in all. */
  steps: TimelineStep[];
  stepCount: number;
  session: string;
}

export interface TimelineReply {
  /** The reply's turn id ("" when unknown). */
  key: string;
  running: boolean;
  state: BarState;
  durationMs: number;
  calls: TimelineCall[];
  steps: TimelineStep[];
  helpers: TimelineHelper[];
  /** The reply's receipt (a saved reply) or its calls added up (live). */
  usage?: TurnUsage;
  route?: { provider: string; model: string; label?: string };
}

/* ------------------------------------------------------------ decoding */

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const str = (v: unknown, max = 400): string => (typeof v === "string" ? v.slice(0, max) : "");
const ms = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.round(v) : null;

function decodeCall(raw: unknown, index: number): TimelineCall | null {
  const r = rec(raw);
  const start = ms(r?.start);
  const len = ms(r?.ms);
  if (!r || start === null || len === null) return null;
  const st = r.state === "stopped" || r.state === "failed" ? (r.state as BarState) : "done";
  const usage = turnUsageFrom(r.usage);
  return {
    index,
    start,
    ms: len,
    provider: str(r.provider, 120),
    model: str(r.model, 200),
    ...(usage ? { usage } : {}),
    ...(r.kind === "final" || r.kind === "rewrite" ? { kind: r.kind } : {}),
    state: st,
  };
}

function decodeStep(raw: unknown, index: number, prefix: string): TimelineStep | null {
  const r = rec(raw);
  const name = str(r?.name, 200);
  if (!r || !name) return null;
  const ok = typeof r.ok === "boolean" ? r.ok : null;
  return {
    key: `${prefix}${index}`,
    index,
    name,
    ok,
    start: ms(r.start),
    ms: ms(r.ms),
    target: str(r.target, 120) || null,
    detail: cleanStepDetail(r.detail),
    helper: r.helper === true,
    check: checkOf(r.check),
    state: ok === false ? "failed" : "done",
  };
}

function decodeHelper(raw: unknown): TimelineHelper | null {
  const r = rec(raw);
  const id = str(r?.id, 200);
  if (!r || !id) return null;
  const steps: TimelineStep[] = [];
  for (const w of Array.isArray(r.work) ? r.work.slice(0, HELPER_STEPS) : []) {
    const s = decodeStep(w, steps.length, `h:${id}:`);
    if (s) steps.push(s);
  }
  const state: BarState =
    r.state === "done" || r.state === "failed" || r.state === "stopped" ? (r.state as BarState) : "stopped";
  const usage = turnUsageFrom(r.usage);
  return {
    id,
    agent: str(r.agent, 120),
    task: str(r.task, 600),
    result: str(r.result, 2000),
    state,
    // On the reply's clock (the daemon's `start` / `end`; a line written
    // before the Timeline has only wall-clock stamps, and no timeline).
    start: ms(r.start),
    end: ms(r.end),
    provider: str(r.provider, 120),
    model: str(r.model, 200),
    ...(usage ? { usage } : {}),
    steps,
    stepCount: Math.max(steps.length, ms(r.steps) ?? 0),
    session: str(r.session, 140),
  };
}

function routeOf(raw: unknown): TimelineReply["route"] {
  const r = rec(raw);
  const provider = str(r?.provider, 120);
  if (!r || !provider) return undefined;
  const label = str(r.label, 120);
  return { provider, model: str(r.model, 200), ...(label ? { label } : {}) };
}

/**
 * The reply's own length on the clock every bar is on (`timing.ms`, v1.390.0
 * review), or null for a line written before the Timeline. `timing`'s
 * startedAt / endedAt are WALL-clock stamps (the reply's Changes window reads
 * the journal by them) and may differ from it when the wall clock stepped.
 */
function replyLength(m: Record<string, unknown>): number | null {
  return ms(rec(m.timing)?.ms);
}

/** Why a saved reply has no timeline: "early" — written since the Timeline,
 *  it ended before any work started (an error or a Stop before the model
 *  was first asked); "cut" — Iron Jarvis itself stopped while answering (a
 *  crash), and the line rebuilt at boot (harness/sessions
 *  reconcile_interrupted_turns) keeps what was streamed and no times at all;
 *  "before" — a reply made before the Timeline. */
export function noTimelineReason(line: BuildMessage | null | undefined): "early" | "cut" | "before" {
  if (!line) return "before";
  const m = line as unknown as Record<string, unknown>;
  if (replyLength(m) !== null) return "early";
  // Every line the reply's own writer wrote has `timing`; only the boot's
  // rebuild of a reply cut off mid-way has none.
  if (m.interrupted === true && !rec(m.timing)) return "cut";
  return "before";
}

/**
 * A SAVED reply's timeline, or null when the reply kept no times (a reply
 * from before the Timeline, or one that ended before it did anything —
 * `noTimelineReason` says which) — the pane then says so in one line.
 */
export function replyTimeline(line: BuildMessage | null | undefined): TimelineReply | null {
  if (!line || line.role !== "assistant") return null;
  const m = line as unknown as Record<string, unknown>;
  const timing = rec(m.timing);
  const started = ms(timing?.startedAt);
  const ended = ms(timing?.endedAt);
  const length = replyLength(m);
  const calls: TimelineCall[] = [];
  for (const c of Array.isArray(m.calls) ? m.calls.slice(0, MAX_CALLS + 1) : []) {
    const d = decodeCall(c, calls.length);
    if (d) calls.push(d);
  }
  const steps: TimelineStep[] = [];
  for (const s of Array.isArray(m.steps) ? m.steps.slice(0, MAX_STEPS) : []) {
    const d = decodeStep(s, steps.length, "s:");
    if (d) steps.push(d);
  }
  const helpers: TimelineHelper[] = [];
  for (const h of Array.isArray(m.helpers) ? m.helpers.slice(0, MAX_HELPERS) : []) {
    const d = decodeHelper(h);
    if (d) helpers.push(d);
  }
  const timed = calls.length > 0 || steps.some((s) => s.start !== null) || helpers.some((h) => h.start !== null);
  if (!timed || (length === null && (started === null || ended === null))) return null;
  const state: BarState = m.interrupted ? "stopped" : m.error ? "failed" : "done";
  const usage = turnUsageFrom(m.usage);
  const route = routeOf(m.route);
  return {
    key: str(m.turnId, 120),
    running: false,
    state,
    durationMs: length ?? Math.max(0, (ended ?? 0) - (started ?? 0)),
    calls,
    steps,
    helpers,
    ...(usage ? { usage } : {}),
    ...(route ? { route } : {}),
  };
}

/* ------------------------------------------------------------ the live fold */

interface LiveState {
  turnId: string;
  calls: TimelineCall[];
  steps: TimelineStep[];
  /** call id -> index in `steps` of a step still working. */
  open: Record<string, number>;
  /** call id -> the target its arguments named (said when it finishes). */
  targets: Record<string, string | null>;
  helpers: TimelineHelper[];
  /** helper id -> step id -> index in its steps. */
  helperOpen: Record<string, Record<string, number>>;
  /** The call in flight: from its `round` frame (and its `meta`). */
  call: { start: number; provider: string; model: string } | null;
  /** The newest provider/model a frame named. */
  provider: string;
  model: string;
  /** How the reply ended (its `done` / `error` frame), "" while it runs. */
  ended: "" | "done" | "failed";
  /** Moves with every fold — a reader redraws only when it changed. */
  version: number;
}

function emptyState(turnId = ""): LiveState {
  return {
    turnId,
    calls: [],
    steps: [],
    open: {},
    targets: {},
    helpers: [],
    helperOpen: {},
    call: null,
    provider: "",
    model: "",
    ended: "",
    version: 0,
  };
}

/** The frames the Timeline reads (every other frame is skipped at once). */
export const TIMELINE_FRAMES: ReadonlySet<string> = new Set([
  "round",
  "meta",
  "model_call",
  "tool_call",
  "helper",
  "done",
  "error",
]);

export interface LiveTimeline {
  /** Fold one frame (a no-op for one the Timeline does not read). */
  fold: (event: string, data: Record<string, unknown>) => void;
  /** Start again (a new reply, or a whole replay). */
  reset: (turnId?: string) => void;
  /** The running reply as of `nowMs` (ms since it started). */
  snapshot: (nowMs: number) => TimelineReply;
  /** Moves with every fold. */
  version: () => number;
  /** The reply it folds (its turn id, "" before the first). */
  turn: () => string;
  /** What the turn cache keeps (lib/turnCache) — opaque, adopted back by
   *  `createLiveTimeline(state)`. */
  state: () => unknown;
}

function isLiveState(v: unknown): v is LiveState {
  const r = rec(v);
  return !!r && Array.isArray(r.calls) && Array.isArray(r.steps) && Array.isArray(r.helpers) && typeof r.version === "number";
}

function foldHelper(s: LiveState, data: Record<string, unknown>): void {
  const id = str(data.id, 200);
  if (!id) return;
  let h = s.helpers.find((x) => x.id === id);
  if (!h) {
    if (s.helpers.length >= MAX_HELPERS) return;
    h = {
      id,
      agent: "",
      task: "",
      result: "",
      state: "working",
      start: null,
      end: null,
      provider: "",
      model: "",
      steps: [],
      stepCount: 0,
      session: "",
    };
    s.helpers.push(h);
  }
  const agent = str(data.agent, 120);
  if (agent) h.agent = agent;
  const task = str(data.task, 600);
  if (task) h.task = task;
  const result = str(data.result, 2000);
  if (result) h.result = result;
  const session = str(data.session, 140);
  if (session) h.session = session;
  const provider = str(data.provider, 120);
  if (provider) h.provider = provider;
  const model = str(data.model, 200);
  if (model) h.model = model;
  const usage = turnUsageFrom(data.usage);
  if (usage) h.usage = usage;
  if (data.state === "done" || data.state === "failed" || data.state === "stopped") h.state = data.state;
  const start = ms(data.start);
  if (start !== null && h.start === null) h.start = start;
  const end = ms(data.end);
  if (end !== null) h.end = end;
  const count = ms(data.steps);
  if (count !== null) h.stepCount = Math.max(h.stepCount, count);
  const step = rec(data.step);
  const sid = str(step?.id, 200);
  const sname = str(step?.name, 200);
  if (!step || !sid || !sname) return;
  const open = (s.helperOpen[id] ??= {});
  if (step.status === "finished") {
    const at = open[sid];
    delete open[sid];
    const ok = typeof step.ok === "boolean" ? step.ok : null;
    const patch = {
      ok,
      start: ms(step.start),
      ms: ms(step.ms),
      detail: cleanStepDetail(step.detail),
      state: (ok === false ? "failed" : "done") as BarState,
    };
    if (at !== undefined && h.steps[at]) Object.assign(h.steps[at], patch);
    else if (h.steps.length < HELPER_STEPS)
      h.steps.push({ key: `h:${id}:${h.steps.length}`, index: h.steps.length, name: sname, target: null, helper: false, check: null, ...patch });
  } else if (h.steps.length < HELPER_STEPS) {
    open[sid] = h.steps.length;
    h.steps.push({
      key: `h:${id}:${h.steps.length}`,
      index: h.steps.length,
      name: sname,
      ok: null,
      start: ms(step.start),
      ms: null,
      target: stepTarget(sname, rec(step.args)),
      detail: null,
      helper: false,
      check: null,
      state: "working",
    });
  }
}

/** The running reply, folded from its frames (once each — no React state). */
export function createLiveTimeline(initial?: unknown): LiveTimeline {
  let s: LiveState = isLiveState(initial) ? initial : emptyState();

  function fold(event: string, data: Record<string, unknown>): void {
    if (!TIMELINE_FRAMES.has(event)) return;
    s.version += 1;
    if (event === "done" || event === "error") {
      s.ended = event === "done" ? "done" : "failed";
      return;
    }
    if (event === "meta") {
      const p = str(data.provider, 120);
      const m = str(data.model, 200);
      if (p) s.provider = p;
      if (m) s.model = m;
      if (s.call) {
        if (p) s.call.provider = p;
        if (m) s.call.model = m;
      }
      return;
    }
    if (event === "round") {
      const start = ms(data.start);
      if (start !== null && !s.call) s.call = { start, provider: s.provider, model: s.model };
      return;
    }
    if (event === "model_call") {
      s.call = null;
      if (s.calls.length >= MAX_CALLS) return;
      const c = decodeCall(data, s.calls.length);
      if (!c) return;
      s.calls.push(c);
      if (c.provider) s.provider = c.provider;
      if (c.model) s.model = c.model;
      return;
    }
    if (event === "helper") {
      foldHelper(s, data);
      return;
    }
    // tool_call
    const id = str(data.id, 200);
    const name = str(data.name, 200);
    if (!id || !name) return;
    if (data.status === "finished") {
      const at = s.open[id];
      delete s.open[id];
      const ok = typeof data.ok === "boolean" ? data.ok : null;
      const index = at ?? s.steps.length;
      const step: TimelineStep = {
        key: `s:${index}`,
        index,
        name,
        ok,
        start: ms(data.start),
        ms: ms(data.ms),
        target: s.targets[id] ?? null,
        // The line keeps the first DETAIL_STEPS steps' detail.
        detail: index < DETAIL_STEPS ? cleanStepDetail(data.detail) : null,
        helper: s.helpers.some((h) => h.id === id),
        check: checkOf(data.check) ?? (at !== undefined ? (s.steps[at]?.check ?? null) : null),
        state: ok === false ? "failed" : "done",
      };
      delete s.targets[id];
      if (at !== undefined) s.steps[at] = step;
      else if (s.steps.length < MAX_STEPS) s.steps.push(step);
      return;
    }
    if (s.steps.length >= MAX_STEPS) return;
    const target = stepTarget(name, rec(data.args));
    s.targets[id] = target;
    s.open[id] = s.steps.length;
    s.steps.push({
      key: `s:${s.steps.length}`,
      index: s.steps.length,
      name,
      ok: null,
      start: ms(data.start),
      ms: null,
      target,
      detail: null,
      helper: false,
      check: checkOf(data.check),
      state: "working",
    });
  }

  function snapshot(nowMs: number): TimelineReply {
    const now = Math.max(0, Math.round(nowMs));
    const ends = [
      now,
      ...s.calls.map((c) => c.start + c.ms),
      ...s.steps.map((st) => (st.start ?? 0) + (st.ms ?? 0)),
    ];
    const durationMs = Math.max(...ends);
    const calls = s.calls.slice();
    if (s.call) {
      calls.push({
        index: calls.length,
        start: s.call.start,
        ms: Math.max(0, durationMs - s.call.start),
        provider: s.call.provider,
        model: s.call.model,
        // A reply that ended with a call in flight: that call failed (an
        // error) — the saved line says the same (harness/timeline).
        state: s.ended === "failed" ? "failed" : s.ended ? "stopped" : "working",
      });
    }
    const steps = s.steps.map((st) =>
      st.state === "working" && st.start !== null ? { ...st, ms: Math.max(0, durationMs - st.start) } : { ...st },
    );
    const helpers = s.helpers.map((h) => ({
      ...h,
      end: h.end ?? (h.state === "working" ? durationMs : h.end),
      steps: h.steps.map((st) =>
        st.state === "working" && st.start !== null ? { ...st, ms: Math.max(0, durationMs - st.start) } : { ...st },
      ),
    }));
    const usage = addUsage(s.calls);
    const route = s.provider ? { provider: s.provider, model: s.model } : undefined;
    return {
      key: s.turnId,
      running: !s.ended,
      state: s.ended || "working",
      durationMs,
      calls,
      steps,
      helpers,
      ...(usage ? { usage } : {}),
      ...(route ? { route } : {}),
    };
  }

  return {
    fold,
    reset: (turnId = "") => {
      s = emptyState(turnId);
    },
    snapshot,
    version: () => s.version,
    turn: () => s.turnId,
    state: () => s,
  };
}

/** The calls' usage added up, the receipt's way (a key only when known). */
export function addUsage(calls: readonly TimelineCall[]): TurnUsage | undefined {
  const out: TurnUsage = {};
  let any = false;
  for (const c of calls) {
    const u = c.usage;
    if (!u) continue;
    any = true;
    out.input_tokens = (out.input_tokens ?? 0) + (u.input_tokens ?? 0);
    out.output_tokens = (out.output_tokens ?? 0) + (u.output_tokens ?? 0);
    if (u.cache_read_input_tokens !== undefined)
      out.cache_read_input_tokens = (out.cache_read_input_tokens ?? 0) + u.cache_read_input_tokens;
    if (u.cache_creation_input_tokens !== undefined)
      out.cache_creation_input_tokens = (out.cache_creation_input_tokens ?? 0) + u.cache_creation_input_tokens;
    if (u.cost_usd !== undefined) out.cost_usd = (out.cost_usd ?? 0) + u.cost_usd;
    if (u.list_price_equivalent) out.list_price_equivalent = true;
  }
  return any ? out : undefined;
}

/* ------------------------------------------------------------ the rows */

export type TimelineRow =
  | { kind: "reply"; key: "reply"; depth: 0; start: number; end: number; state: BarState; count: number }
  | { kind: "step"; key: string; depth: 1 | 2; start: number; end: number; state: BarState; step: TimelineStep; helperId?: string }
  | { kind: "helper"; key: string; depth: 1; start: number; end: number; state: BarState; helper: TimelineHelper; count: number };

/** A bar's place on the axis: [start, end] in ms, inside the reply. */
function span(start: number | null, len: number | null, duration: number): [number, number] {
  const s = Math.min(Math.max(0, start ?? 0), duration);
  const e = Math.min(duration, s + Math.max(0, len ?? 0));
  return [s, e];
}

/**
 * The rows, in order: the model you talk to, then everything under it by
 * when it started — its steps, and each helper with its own steps under it.
 * A step that started a helper is drawn as the helper's row. `collapsed`
 * holds the keys of rows folded shut ("reply", "helper:<id>").
 */
export function timelineRows(reply: TimelineReply, collapsed: ReadonlySet<string>): TimelineRow[] {
  const d = reply.durationMs;
  const children: { start: number; order: number; rows: TimelineRow[] }[] = [];
  let order = 0;
  for (const st of reply.steps) {
    if (st.helper) continue;
    const [s, e] = span(st.start, st.ms, d);
    children.push({
      start: s,
      order: order++,
      rows: [{ kind: "step", key: st.key, depth: 1, start: s, end: e, state: st.state, step: st }],
    });
  }
  for (const h of reply.helpers) {
    const [s, e0] = span(h.start, h.end !== null && h.start !== null ? h.end - h.start : 0, d);
    const key = `helper:${h.id}`;
    const rows: TimelineRow[] = [
      { kind: "helper", key, depth: 1, start: s, end: Math.max(e0, s), state: h.state, helper: h, count: h.steps.length },
    ];
    if (!collapsed.has(key)) {
      for (const st of h.steps) {
        const [a, b] = span(st.start, st.ms, d);
        rows.push({ kind: "step", key: st.key, depth: 2, start: a, end: b, state: st.state, step: st, helperId: h.id });
      }
    }
    children.push({ start: s, order: order++, rows });
  }
  const head: TimelineRow = {
    kind: "reply",
    key: "reply",
    depth: 0,
    start: 0,
    end: d,
    state: reply.state,
    count: children.length,
  };
  if (collapsed.has("reply")) return [head];
  children.sort((a, b) => a.start - b.start || a.order - b.order);
  const out: TimelineRow[] = [head];
  for (const c of children) out.push(...c.rows);
  return out;
}

/** One piece of the model's bar: a call, or (past MAX_SEGMENTS) a group of
 *  calls next to each other in time. */
export interface Segment {
  key: string;
  start: number;
  end: number;
  calls: TimelineCall[];
  state: BarState;
}

export function callSegments(calls: readonly TimelineCall[], duration: number, max = MAX_SEGMENTS): Segment[] {
  const one = (c: TimelineCall): Segment => {
    const [s, e] = span(c.start, c.ms, duration);
    return { key: `call:${c.index}`, start: s, end: e, calls: [c], state: c.state };
  };
  if (calls.length <= max) return calls.map(one);
  const per = Math.ceil(calls.length / max);
  const out: Segment[] = [];
  for (let i = 0; i < calls.length; i += per) {
    const group = calls.slice(i, i + per);
    const parts = group.map(one);
    const state = group.some((c) => c.state === "working")
      ? "working"
      : group.some((c) => c.state === "failed")
        ? "failed"
        : group.some((c) => c.state === "stopped")
          ? "stopped"
          : "done";
    out.push({
      key: `calls:${group[0].index}-${group[group.length - 1].index}`,
      start: parts[0].start,
      end: Math.max(...parts.map((p) => p.end)),
      calls: group,
      state,
    });
  }
  return out;
}

/** Up to `want` round times on the axis (0 first), in ms. */
export function axisTicks(duration: number, want = 4): number[] {
  const steps = [
    100, 200, 500, 1_000, 2_000, 5_000, 10_000, 15_000, 30_000, 60_000, 120_000, 300_000, 600_000, 900_000, 1_800_000,
    3_600_000, 7_200_000, 14_400_000, 28_800_000,
  ];
  const d = Math.max(0, duration);
  const step = steps.find((s) => d / s <= want) ?? steps[steps.length - 1];
  const out: number[] = [];
  for (let t = 0; t <= d && out.length < want + 1; t += step) out.push(t);
  return out;
}

/** An axis label: "0", "0.5 s", "30 s", "2 min", "1 h". */
export function tickLabel(t: number): string {
  if (t <= 0) return "0";
  if (t < 1_000) return `${(t / 1000).toFixed(1)} s`;
  if (t < 60_000) return `${Math.round(t / 1000)} s`;
  if (t < 3_600_000) return `${Math.round(t / 60_000)} min`;
  const h = t / 3_600_000;
  return `${Number.isInteger(h) ? h : h.toFixed(1)} h`;
}

/* ------------------------------------------------------------ the totals */

export interface TimelineTotals {
  durationMs: number;
  usage?: TurnUsage;
  calls: number;
  /** The model's OWN steps (a check Iron Jarvis ran is counted apart). */
  steps: number;
  /** The checks Iron Jarvis ran itself (finish check, project checks). */
  checks: number;
  failed: number;
  helpers: number;
  helperSteps: number;
  helperUsage?: TurnUsage;
}

/** What the top of the pane says. The cost and tokens are the reply's OWN
 *  (its receipt); the helpers' are added up APART — the receipt never counts
 *  them, so neither does the line above them. */
export function timelineTotals(reply: TimelineReply): TimelineTotals {
  const own = reply.steps.filter((s) => !s.check);
  const helperUsage = addUsage(
    reply.helpers.filter((h) => h.usage).map((h, i) => ({ index: i, start: 0, ms: 0, provider: h.provider, model: h.model, usage: h.usage, state: "done" as BarState })),
  );
  return {
    durationMs: reply.durationMs,
    ...(reply.usage ? { usage: reply.usage } : {}),
    calls: reply.calls.filter((c) => c.state !== "working").length,
    steps: own.length,
    checks: reply.steps.length - own.length,
    failed: reply.steps.filter((s) => s.ok === false).length + reply.helpers.reduce((n, h) => n + h.steps.filter((s) => s.ok === false).length, 0),
    helpers: reply.helpers.length,
    helperSteps: reply.helpers.reduce((n, h) => n + h.stepCount, 0),
    ...(helperUsage ? { helperUsage } : {}),
  };
}

/* ------------------------------------------------------------ which reply */

export interface ReplyChoice {
  /** "live" for the running reply, else the reply's index in `messages`. */
  value: string;
  label: string;
}

/** The words of the running reply's choice. */
export const LIVE_CHOICE = "Working now";

/** The replies to choose from, newest first: the running one (`live`: its
 *  words, or null when none runs), then every saved reply ("10:42 · Add a
 *  cart", the question it answered). */
export function replyChoices(messages: readonly BuildMessage[], live: string | null): ReplyChoice[] {
  const out: ReplyChoice[] = live ? [{ value: "live", label: live }] : [];
  let question = "";
  const found: ReplyChoice[] = [];
  for (let i = 0; i < messages.length; i += 1) {
    const m = messages[i];
    if (m.role === "user") {
      if (!m.steer && !m.continuation && typeof m.content === "string" && m.content.trim()) question = m.content;
      continue;
    }
    const words = question.replace(/\s+/g, " ").trim();
    const short = words.length > 48 ? `${words.slice(0, 47)}…` : words;
    const when = clock(typeof m.at === "string" ? m.at : "");
    found.push({ value: String(i), label: [when, short || "A reply"].filter(Boolean).join(" · ") });
  }
  return [...out, ...found.reverse()];
}

function clock(iso: string): string {
  if (!iso) return "";
  let s = iso.trim();
  if (/[T ]\d{2}:\d{2}/.test(s) && !/[zZ]|[+-]\d{2}:?\d{2}$/.test(s)) s = `${s.replace(" ", "T")}Z`;
  const t = Date.parse(s);
  if (Number.isNaN(t)) return "";
  return new Date(t).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
