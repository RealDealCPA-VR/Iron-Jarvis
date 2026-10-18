/**
 * v1.350.0 (polish wave C, lane C1): the facts behind the live plan checklist.
 *
 * When an agent that chat handed work to MAKES A PLAN (the decomposed lane;
 * it happens on local models), the helper-agent row (components/chat/
 * AgentRunRow) shows the plan as a quiet checklist that ticks off while the
 * agent works: an empty circle for a step still waiting, an arrow for the one
 * running, a check (the goal struck through, muted) for a step done, an x for
 * a failed one and a dash for one never attempted. The idea comes from
 * 21st.dev agent-elements' Todo and Plan tools (MIT, ideas only; nothing
 * installed). The checklist itself is components/chat/PlanChecklist.tsx.
 *
 * Nothing here touches React or the network. This file holds:
 *  - the FOLD: the run's plan.* events (agents/decompose.py, read exactly as
 *    components/chat/stepLabel.ts reads them) into one checklist, on top of a
 *    base (what was known before: an earlier fold, or the daemon's own copy
 *    from GET /sessions/{id}/plan), so a plan.created that scrolled out of the
 *    page's event window, or one made before the page was watching, is not
 *    lost;
 *  - the READER for the daemon's copy, and the SAVED form finalize writes on
 *    the hand-off message (goal + state per step, at most 20 steps and 200
 *    characters per goal) with its defensive reader: a saved thread is
 *    untrusted, and anything malformed reads as no plan;
 *  - the words ("2 of 4 done") and the settle rule (a step still waiting or
 *    running when the run ended was never finished).
 *
 * The payloads, from agents/decompose.py:
 *   plan.created        {run_id, steps: [goal, ...]}
 *   plan.step_started   {run_id, index, goal}              (index 0-based)
 *   plan.step_completed {run_id, index, ok, attempted?, attempts?}
 *                       (attempted: false ONLY on the budget-spent branch)
 */

/** A step's state, the daemon's own vocabulary (GET /sessions/{id}/plan). */
export type PlanStepState = "waiting" | "running" | "done" | "failed" | "skipped";

export const PLAN_STATES: readonly PlanStepState[] = Object.freeze([
  "waiting",
  "running",
  "done",
  "failed",
  "skipped",
]);

/** One row of the checklist. `index` is the step's place in the plan
 *  (0-based); a goal not known yet (a step seen only by its completion) is
 *  "". */
export interface PlanStep {
  index: number;
  goal: string;
  state: PlanStepState;
}

/** The checklist: its known steps in plan order, and how many steps the plan
 *  has (null when only some steps were seen and the plan itself was not). */
export interface PlanView {
  steps: PlanStep[];
  total: number | null;
}

/** The form saved on the hand-off message (`agentRun.plan`). */
export interface SavedPlan {
  steps: { goal: string; state: PlanStepState }[];
  /** How many steps the plan had, when known (it may be more than were
   *  saved: at most MAX_PLAN_STEPS are). */
  total: number | null;
}

/** The decomposed lane's own ceiling (agents/decompose.py
 *  MAX_PLAN_STEPS_CEILING): no real plan is longer, so no more are kept. */
export const MAX_PLAN_STEPS = 20;
/** A goal is model-written: one line of at most this many characters. */
export const MAX_GOAL_CHARS = 200;

/** The event shape this file reads (lib/types IJEvent satisfies it). */
export interface PlanEventLike {
  id?: string;
  type: string;
  session_id?: string | null;
  payload?: Record<string, unknown> | null;
}

/* ------------------------------------------------------------------ helpers */

/** A goal as one clean line, cut at MAX_GOAL_CHARS (by code point, so a cut
 *  never splits a surrogate pair). Anything not a string is "". */
export function cleanGoal(v: unknown): string {
  if (typeof v !== "string") return "";
  const flat = v.replace(/\s+/g, " ").trim();
  const chars = Array.from(flat);
  return chars.length > MAX_GOAL_CHARS ? `${chars.slice(0, MAX_GOAL_CHARS - 1).join("")}…` : flat;
}

function isState(v: unknown): v is PlanStepState {
  return typeof v === "string" && (PLAN_STATES as readonly string[]).includes(v);
}

function stepIndex(p: Record<string, unknown>): number | null {
  const i = p.index;
  return typeof i === "number" && Number.isInteger(i) && i >= 0 && i < MAX_PLAN_STEPS ? i : null;
}

/** The goals of a plan.created payload, or null when it carries none. */
function goalsOf(p: Record<string, unknown>): string[] | null {
  const steps = p.steps;
  if (!Array.isArray(steps) || steps.length === 0) return null;
  return steps.slice(0, MAX_PLAN_STEPS).map((g) => cleanGoal(g));
}

function sameGoals(view: PlanView, goals: readonly string[], total: number): boolean {
  if (view.total !== total || view.steps.length !== goals.length) return false;
  return view.steps.every((s, i) => s.index === i && s.goal === goals[i]);
}

/** Put `step` in its place (by index), replacing any step already there. */
function upsert(steps: PlanStep[], step: PlanStep): PlanStep[] {
  const out = steps.filter((s) => s.index !== step.index);
  out.push(step);
  out.sort((a, b) => a.index - b.index);
  return out;
}

/* --------------------------------------------------------------------- fold */

/** The plan.* events of ONE turn of `session`, oldest first, from a window
 *  given newest first (useEvents' order). `boundary` is the id of the newest
 *  event from BEFORE the turn (the chat page's sinceRef): the walk stops
 *  there, exactly like the page's progress lines. */
export function planEventsFor(
  events: readonly PlanEventLike[],
  session: string | null | undefined,
  boundary?: string | null,
): PlanEventLike[] {
  if (!session) return [];
  const out: PlanEventLike[] = [];
  for (const e of events) {
    if (boundary && e.id === boundary) break; // the turn starts after this one
    if (e.session_id !== session) continue;
    if (e.type === "plan.created" || e.type === "plan.step_started" || e.type === "plan.step_completed") {
      out.push(e);
    }
  }
  return out.reverse();
}

/**
 * Fold the run's plan events (oldest first) onto `base`, into the checklist,
 * or null while nothing is known.
 *
 * Re-folding the same events onto the result of an earlier fold gives the
 * same checklist: each step ends in the state its NEWEST event says (or the
 * base's, when no event in the window touched it), and a plan.created whose
 * goals match the base keeps the base's states. That is what lets the caller
 * fold its whole window again on every new event, on top of what it already
 * knew, and lose nothing when the oldest events leave the window. It is also
 * why a run that resumes after a daemon restart (decompose re-publishes
 * plan.created and skips the steps it already finished) keeps its ticks.
 */
export function foldPlanEvents(base: PlanView | null, events: readonly PlanEventLike[]): PlanView | null {
  let view: PlanView | null = base ? { steps: [...base.steps], total: base.total } : null;
  for (const e of events) {
    const p = (e.payload && typeof e.payload === "object" ? e.payload : {}) as Record<string, unknown>;
    if (e.type === "plan.created") {
      const goals = goalsOf(p);
      if (!goals) continue; // a malformed plan never blanks a known one
      const total = Array.isArray(p.steps) ? p.steps.length : goals.length;
      if (view && sameGoals(view, goals, total)) continue; // the same plan, re-said
      view = { steps: goals.map((goal, index) => ({ index, goal, state: "waiting" })), total };
      continue;
    }
    if (e.type !== "plan.step_started" && e.type !== "plan.step_completed") continue;
    const index = stepIndex(p);
    if (index === null) continue;
    // An index past the plan this window knows belongs to no step of it.
    if (view && view.total !== null && index >= view.total) continue;
    const known = view?.steps.find((s) => s.index === index);
    let state: PlanStepState;
    let goal = known?.goal ?? "";
    if (e.type === "plan.step_started") {
      state = "running";
      const said = cleanGoal(p.goal);
      if (said && !goal) goal = said;
    } else {
      // `attempted: false` is the budget-spent branch: that step never ran,
      // and "failed" would be a lie about it (stepLabel says the same).
      state = p.attempted === false ? "skipped" : p.ok === false ? "failed" : "done";
    }
    view = { steps: upsert(view?.steps ?? [], { index, goal, state }), total: view?.total ?? null };
  }
  return view && view.steps.length > 0 ? view : null;
}

/** True when two checklists say the same thing (same size, same steps in the
 *  same states), so a caller can keep the object it already has. */
export function samePlan(a: PlanView | null | undefined, b: PlanView | null | undefined): boolean {
  if (a === b) return true;
  if (!a || !b || a.total !== b.total || a.steps.length !== b.steps.length) return false;
  return a.steps.every((s, i) => {
    const t = b.steps[i];
    return s.index === t.index && s.goal === t.goal && s.state === t.state;
  });
}

/* ------------------------------------------------------- the daemon's copy */

/** GET /sessions/{id}/plan's body (`{"steps": [{"goal", "state"}]}`) as a
 *  checklist, or null when it is not one. Read defensively: a step whose
 *  state is not one of the five is dropped, never guessed. */
export function readPlanResponse(raw: unknown): PlanView | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const steps = (raw as Record<string, unknown>).steps;
  if (!Array.isArray(steps) || steps.length === 0) return null;
  const out: PlanStep[] = [];
  steps.slice(0, MAX_PLAN_STEPS).forEach((s, index) => {
    if (!s || typeof s !== "object") return;
    const r = s as Record<string, unknown>;
    if (!isState(r.state)) return;
    out.push({ index, goal: cleanGoal(r.goal), state: r.state });
  });
  return out.length > 0 ? { steps: out, total: steps.length } : null;
}

/* -------------------------------------------------------------- the saved form */

/** The checklist as finalize saves it: at most MAX_PLAN_STEPS steps, each a
 *  clean goal of at most MAX_GOAL_CHARS and its state. Null for no plan. */
export function toSavedPlan(view: PlanView | null | undefined): SavedPlan | null {
  if (!view || view.steps.length === 0) return null;
  const steps = view.steps
    .slice(0, MAX_PLAN_STEPS)
    .map((s) => ({ goal: cleanGoal(s.goal), state: s.state }));
  const total = typeof view.total === "number" && Number.isInteger(view.total) && view.total >= steps.length
    ? view.total
    : null;
  return { steps, total };
}

/** A saved plan (`agentRun.plan`) read back, or null when there is none or it
 *  is not one. A step with an unknown state is dropped; a total smaller than
 *  the steps it came with is no total. */
export function readSavedPlan(raw: unknown): PlanView | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) return null;
  const r = raw as Record<string, unknown>;
  if (!Array.isArray(r.steps)) return null;
  const steps: PlanStep[] = [];
  for (const s of r.steps.slice(0, MAX_PLAN_STEPS)) {
    if (!s || typeof s !== "object" || Array.isArray(s)) continue;
    const step = s as Record<string, unknown>;
    if (!isState(step.state)) continue;
    steps.push({ index: steps.length, goal: cleanGoal(step.goal), state: step.state });
  }
  if (steps.length === 0) return null;
  const t = r.total;
  const total = typeof t === "number" && Number.isInteger(t) && t >= steps.length && t <= 1000 ? t : null;
  return { steps, total };
}

/* ------------------------------------------------------------- settling */

/** The checklist once the run is over: a step still waiting was never
 *  attempted, and a step still running when the run ended did not finish
 *  (it failed with the run, or was stopped with it). Nothing is left
 *  spinning in a settled row. */
export function settlePlan(view: PlanView | null | undefined, status: string): PlanView | null {
  if (!view || view.steps.length === 0) return null;
  const failedRun = (status || "").toLowerCase() === "failed";
  return {
    total: view.total,
    steps: view.steps.map((s) =>
      s.state === "waiting"
        ? { ...s, state: "skipped" }
        : s.state === "running"
          ? { ...s, state: failedRun ? "failed" : "skipped" }
          : s,
    ),
  };
}

/* ----------------------------------------------------------------- words */

export function planCounts(view: PlanView): { done: number; failed: number; total: number | null } {
  let done = 0;
  let failed = 0;
  for (const s of view.steps) {
    if (s.state === "done") done++;
    else if (s.state === "failed") failed++;
  }
  return { done, failed, total: view.total };
}

/** "2 of 4 done", "2 of 4 done, 1 failed", or "2 done" when the plan's size
 *  is not known. */
export function planCountWords(view: PlanView): string {
  const { done, failed, total } = planCounts(view);
  const head = total !== null ? `${done} of ${total} done` : `${done} done`;
  return failed > 0 ? `${head}, ${failed} failed` : head;
}

/** The running step, or null. */
export function runningStep(view: PlanView | null | undefined): PlanStep | null {
  return view?.steps.find((s) => s.state === "running") ?? null;
}

/** What a screen reader hears before a step's goal. */
export const STATE_WORDS: Readonly<Record<PlanStepState, string>> = Object.freeze({
  waiting: "Not started",
  running: "Working on",
  done: "Done",
  failed: "Failed",
  skipped: "Not attempted",
});

/** A goal-less step (seen only by its completion) is named by its place. */
export function stepGoalText(step: PlanStep): string {
  return step.goal || `Step ${step.index + 1}`;
}

/* ------------------------------------------------------- what says "a plan" */

/**
 * True for a progress line stepLabel made from a plan.* event ("Planned 4
 * steps", "Step 2 of 4: draft the summary", "Step 1 of 4 done", "Step 3
 * failed", "Step 4 of 4 not attempted (step budget spent)"). The live row
 * shows the checklist in their place, so they are not said twice; pinned
 * against stepLabel's real output in plan-checklist-v1350.
 */
export function isPlanProgressLine(line: string): boolean {
  return /^Planned \d+ steps?$/.test(line) || /^Step \d+(?: of \d+)?(?::| done$| failed$| not attempted\b)/.test(line);
}

/** True for a run phase detail that says the run has a plan ("step 2 of 4:
 *  …", "4 steps planned"): the daemon's own words (agents/decompose.py). */
export function phaseSaysPlan(detail: string | null | undefined): boolean {
  const d = (detail ?? "").trim();
  return /^step\s+\d+\s+of\s+\d+/i.test(d) || /^\d+\s+steps?\s+planned\b/i.test(d);
}
