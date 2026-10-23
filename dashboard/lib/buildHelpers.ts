/**
 * v1.372.0 (IJ harness part 2, Phase 1): the HELPER AGENTS a Build session
 * starts, as the screen reads them.
 *
 * A Build turn may start helper agents (Claude Code's `Agent` / `Task`,
 * Codex's `spawn_agent`, translated to `delegate` / `spawn_agent` by the
 * daemon). Each helper is ONE row in the conversation. While the turn runs,
 * the daemon streams `helper` frames on the turn's side channel
 * (src/iron_jarvis/harness/helpers.py, `HelperCall.frame`): who, what it was
 * asked, its state, its step count, a step that started or finished, its
 * phase, an ask waiting for the user, and at the end its report. When the
 * turn ends the reply line keeps each helper (routes/build `_ReplyWatch.
 * helper_lines`, line key `helpers`).
 *
 * Every shape here crosses a JSON boundary (a live frame, a saved line), so
 * each field is checked; junk reads as absent, never as a crash.
 */

import type { ToolCard, TurnStep } from "@/lib/useChatStream";
import { agentPhrase, agentRunName, type AgentRunStamp } from "@/lib/agentRun";
import { readSavedPlan, settlePlan, toSavedPlan, type PlanView, type SavedPlan } from "@/lib/planChecklist";

export type HelperState = "starting" | "queued" | "running" | "waiting" | "done" | "failed" | "stopped";

const STATES: ReadonlySet<string> = new Set([
  "starting",
  "queued",
  "running",
  "waiting",
  "done",
  "failed",
  "stopped",
]);

/** A helper's own ask, as the approval card reads it (redacted arguments). */
export interface HelperAsk {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  count?: number;
  examples?: Record<string, unknown>[];
  costNote?: string;
}

/** One helper while the turn runs. */
export interface HelperView {
  /** The tool call that started it (the row's key). */
  id: string;
  /** Its own session (the "Open run" link), once it has one. */
  session: string;
  /** Who: a roster name ("researcher", "custom:scout"). */
  agent: string;
  /** What it was asked (cut by the daemon). */
  task: string;
  state: HelperState;
  /** Finished steps so far (the daemon's count). */
  steps: number;
  /** When the call started / ended (ms epoch, the daemon's clock). */
  since: number | null;
  ended: number | null;
  /** Its report, or why it did not finish. */
  result: string;
  /** Why it waits to start ("3 helpers are already working"). */
  note: string;
  /** Its current phase ("planning" …) and detail. */
  phase: string;
  phaseDetail: string;
  /** An ask of its own waiting for the user. */
  ask: HelperAsk | null;
  /** Its tool calls, live (oldest first). */
  tools: ToolCard[];
  /** v1.374.0: its run's plan when the run planned its work (the decomposed
   *  lane) — the daemon's own checklist, sent on the row's frame each time
   *  the run moves on (harness/helpers `helper_plan`); absent for most. */
  plan?: PlanView | null;
}

/** One helper as the reply keeps it. */
export interface SavedHelper {
  id: string;
  session: string;
  agent: string;
  task: string;
  state: "done" | "failed" | "stopped";
  steps: number;
  startedAt: number | null;
  endedAt: number | null;
  result: string;
  work: TurnStep[];
  /** v1.374.0: the helper's own plan when its run planned its work (the
   *  decomposed lane) — the checklist's saved form, settled; absent else. */
  plan?: SavedPlan;
}

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const str = (v: unknown, max = 4000): string => (typeof v === "string" ? v.slice(0, max) : "");
const int = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? Math.max(0, Math.round(v)) : null;

/** At most this many live steps per helper are kept on screen. */
export const MAX_LIVE_STEPS = 40;

function decodeAsk(raw: unknown): HelperAsk | null {
  const r = rec(raw);
  const id = str(r?.id, 120);
  const tool = str(r?.tool, 200);
  if (!r || !id || !tool) return null;
  const ask: HelperAsk = { id, tool, args: rec(r.args) ?? {} };
  const count = int(r.count);
  if (count && count > 1) {
    ask.count = count;
    ask.examples = (Array.isArray(r.examples) ? r.examples : [])
      .map(rec)
      .filter((e): e is Record<string, unknown> => e !== null)
      .slice(0, 3);
  }
  const cost = str(r.cost_note, 300);
  if (cost) ask.costNote = cost;
  return ask;
}

function blank(id: string): HelperView {
  return {
    id,
    session: "",
    agent: "",
    task: "",
    state: "starting",
    steps: 0,
    since: null,
    ended: null,
    result: "",
    note: "",
    phase: "",
    phaseDetail: "",
    ask: null,
    tools: [],
  };
}

/** Fold one `helper` frame into the turn's helpers (call order kept). */
export function applyHelperFrame(prev: readonly HelperView[], data: Record<string, unknown>, now = Date.now()): HelperView[] {
  const id = str(data.id, 200);
  if (!id) return prev as HelperView[];
  const at = prev.findIndex((h) => h.id === id);
  const h: HelperView = at === -1 ? blank(id) : { ...prev[at], tools: prev[at].tools };
  const agent = str(data.agent, 120);
  if (agent) h.agent = agent;
  const task = str(data.task, 600);
  if (task) h.task = task;
  const session = str(data.session, 140);
  if (session) h.session = session;
  if (typeof data.state === "string" && STATES.has(data.state)) h.state = data.state as HelperState;
  const steps = int(data.steps);
  if (steps !== null) h.steps = Math.max(h.steps, steps);
  const since = int(data.since);
  if (since !== null && h.since === null) h.since = since;
  const ended = int(data.ended);
  if (ended !== null) h.ended = ended;
  const result = str(data.result, 2000);
  if (result) h.result = result;
  const note = str(data.note, 200);
  if (note) h.note = note;
  if (typeof data.phase === "string" && data.phase) {
    h.phase = str(data.phase, 60);
    h.phaseDetail = str(data.detail, 200);
  }
  if ("ask" in data) h.ask = decodeAsk(data.ask);
  // v1.374.0: the run's plan, read as untrusted as the rest (junk is none).
  if ("plan" in data) h.plan = readSavedPlan(data.plan);
  const step = rec(data.step);
  const sid = str(step?.id, 200);
  const sname = str(step?.name, 200);
  if (step && sid && sname) {
    const finished = step.status === "finished";
    const patch: ToolCard = { id: sid, name: sname, status: finished ? "done" : "running" };
    if (finished) {
      patch.ok = step.ok === true;
      patch.endedAt = now;
      const out = str(step.output, 400);
      if (out) patch.output = out;
    } else {
      patch.startedAt = now;
      const args = rec(step.args);
      if (args) patch.args = args;
    }
    const detail = rec(step.detail);
    if (detail && typeof detail.input === "string" && typeof detail.output === "string") {
      patch.detail = { input: detail.input, output: detail.output };
    }
    const i = h.tools.findIndex((t) => t.id === sid);
    if (i === -1) {
      h.tools = [...h.tools, patch].slice(-MAX_LIVE_STEPS);
    } else {
      const next = h.tools.slice();
      next[i] = { ...next[i], ...patch, startedAt: next[i].startedAt ?? patch.startedAt };
      h.tools = next;
    }
  }
  if (at === -1) return [...prev, h];
  const out = prev.slice();
  out[at] = h;
  return out;
}

/** The tool calls a turn's helper rows stand for (their own tool card is not
 *  drawn a second time). */
export function helperCallIds(helpers: readonly HelperView[]): Set<string> {
  return new Set(helpers.map((h) => h.id));
}

/** The helpers a reply kept (the line's `helpers`), or [] for junk / none. */
export function decodeSavedHelpers(raw: unknown): SavedHelper[] {
  if (!Array.isArray(raw)) return [];
  const out: SavedHelper[] = [];
  for (const item of raw.slice(0, 12)) {
    const r = rec(item);
    const id = str(r?.id, 200);
    if (!r || !id) continue;
    const state = r.state === "done" || r.state === "failed" || r.state === "stopped" ? r.state : "stopped";
    const work: TurnStep[] = [];
    for (const w of Array.isArray(r.work) ? r.work.slice(0, 20) : []) {
      const wr = rec(w);
      const name = str(wr?.name, 200);
      if (!wr || !name) continue;
      const step: TurnStep = { name, ok: typeof wr.ok === "boolean" ? wr.ok : null, ms: int(wr.ms) };
      const d = rec(wr.detail);
      if (d && typeof d.input === "string" && typeof d.output === "string") {
        step.detail = { input: d.input, output: d.output };
      }
      work.push(step);
    }
    out.push({
      id,
      session: str(r.session, 140),
      agent: str(r.agent, 120),
      task: str(r.task, 600),
      state,
      steps: int(r.steps) ?? work.length,
      startedAt: int(r.startedAt),
      endedAt: int(r.endedAt),
      result: str(r.result, 2000),
      work,
      // v1.374.0: read as untrusted as the rest; a malformed plan is none.
      ...(() => {
        const plan = toSavedPlan(readSavedPlan(r.plan));
        return plan ? { plan } : {};
      })(),
    });
  }
  return out;
}

/** Who did it, at the start of a sentence: "The researcher", "Your scout
 *  agent", "A helper" when nothing names it. */
export function helperName(agent: string): string {
  const a = agent.trim();
  return a ? agentRunName(agentPhrase(a)) : "A helper";
}

/** The settled helper as the chat's settled run row reads it. */
export function helperStamp(h: SavedHelper): AgentRunStamp {
  return {
    session: h.session,
    startedAt: h.startedAt,
    endedAt: h.endedAt,
    steps: h.steps,
    status: h.state === "done" ? "completed" : h.state === "failed" ? "failed" : "cancelled",
    agent: h.agent || undefined,
    // v1.374.0: the run's plan as it ended (the settled row opens to it).
    ...(h.plan ? { plan: h.plan } : {}),
  };
}

/** A live helper that ENDED (its last frame came) as a saved one — so the
 *  row reads the same before and after the reply is re-read. v1.374.0: with
 *  the plan its live row showed, settled (nothing left working). */
export function settledFromLive(h: HelperView): SavedHelper | null {
  if (h.state !== "done" && h.state !== "failed" && h.state !== "stopped") return null;
  const saved = toSavedPlan(settlePlan(h.plan ?? null, h.state === "failed" ? "failed" : "completed"));
  return {
    ...(saved ? { plan: saved } : {}),
    id: h.id,
    session: h.session,
    agent: h.agent,
    task: h.task,
    state: h.state,
    steps: h.steps,
    startedAt: h.since,
    endedAt: h.ended,
    result: h.result,
    work: h.tools
      .filter((t) => t.status === "done")
      .map((t) => ({
        name: t.name,
        ok: t.ok ?? null,
        ms: t.startedAt && t.endedAt ? Math.max(0, t.endedAt - t.startedAt) : null,
        ...(t.detail ? { detail: t.detail } : {}),
      })),
  };
}

/** What the row says the helper was asked: "Asked: find config reads". */
export function askedLine(task: string): string {
  const t = task.replace(/\s+/g, " ").trim();
  return t ? `Asked: ${t}` : "";
}
