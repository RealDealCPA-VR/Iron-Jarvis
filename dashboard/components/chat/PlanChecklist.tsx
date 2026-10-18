"use client";

/**
 * v1.350.0 (polish wave C, lane C1): the LIVE PLAN CHECKLIST.
 *
 * When an agent that chat handed work to makes a plan (the decomposed lane),
 * its steps show as a quiet checklist inside the helper-agent row
 * (components/chat/AgentRunRow), ticking off while the agent works:
 *
 *   ○ read the ledger            waiting (an empty circle)
 *   → draft the summary          running (the one step it is on)
 *   ✓ ~~check the totals~~       done (struck through, muted)
 *   × send it                    failed (tone-danger)
 *   – file it                    not attempted (a dash)
 *
 * Calm grey rows in WorkLine's type (13 px, zinc), no box, no border, no
 * shadow, nothing that moves: the running step is marked by its arrow and a
 * brighter ink, never an animation. The idea comes from 21st.dev
 * agent-elements' Todo and Plan tools (MIT, ideas only; nothing installed).
 * The facts (the fold over plan.* events, the saved form, the words) are in
 * lib/planChecklist.ts; this file draws them and holds the one hook that
 * reads the daemon's copy of a plan the page did not see being made.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, Check, Circle, Minus, X, type LucideIcon } from "lucide-react";
import { get } from "@/lib/api";
import {
  foldPlanEvents,
  phaseSaysPlan,
  planCountWords,
  planEventsFor,
  readPlanResponse,
  samePlan,
  STATE_WORDS,
  stepGoalText,
  type PlanEventLike,
  type PlanStep,
  type PlanStepState,
  type PlanView,
} from "@/lib/planChecklist";

/* --------------------------------------------------------------- the rows */

const LOOK: Readonly<Record<PlanStepState, { icon: LucideIcon; iconTone: string; textTone: string }>> = {
  waiting: { icon: Circle, iconTone: "text-zinc-600", textTone: "text-zinc-400" },
  running: { icon: ArrowRight, iconTone: "text-accent-soft", textTone: "text-zinc-200" },
  done: { icon: Check, iconTone: "text-zinc-500", textTone: "text-zinc-500 line-through decoration-zinc-600" },
  failed: { icon: X, iconTone: "text-tone-danger", textTone: "text-zinc-400" },
  skipped: { icon: Minus, iconTone: "text-zinc-600", textTone: "text-zinc-500" },
};

function PlanRow({ step }: { step: PlanStep }) {
  const look = LOOK[step.state];
  const Icon = look.icon;
  const goal = stepGoalText(step);
  return (
    <li
      data-testid="plan-step"
      data-state={step.state}
      aria-current={step.state === "running" ? "step" : undefined}
      className="flex min-w-0 items-center gap-2 text-[13px] leading-5 text-zinc-500"
    >
      <Icon size={14} aria-hidden="true" className={`shrink-0 ${look.iconTone}`} />
      <span className="sr-only">{STATE_WORDS[step.state]}: </span>
      <span data-testid="plan-step-goal" title={goal} className={`min-w-0 truncate ${look.textTone}`}>
        {goal}
      </span>
    </li>
  );
}

/** The checklist. A plain ordered list a screen reader can walk (its name
 *  says how far along it is); every row says its state in words before its
 *  goal, so nothing rests on the icon or the strike-through alone. */
export function PlanChecklist({ plan, className = "" }: { plan: PlanView; className?: string }) {
  const more = plan.total !== null && plan.total > plan.steps.length ? plan.total - plan.steps.length : 0;
  return (
    <div data-testid="plan-checklist" className={`grid gap-0.5 ${className}`.trim()}>
      <ol aria-label={`The plan: ${planCountWords(plan)}`} className="grid gap-0.5">
        {plan.steps.map((s) => (
          <PlanRow key={s.index} step={s} />
        ))}
      </ol>
      {more > 0 ? (
        <p data-testid="plan-more" className="pl-[22px] text-[12px] leading-5 text-zinc-600">
          {more === 1 ? "1 more step" : `${more} more steps`}
        </p>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------- the daemon's copy */

// One read per session at a time: the live hook and finalize may both want
// the plan of a run the page resumed, and the second asks the first's read.
const inflight = new Map<string, Promise<PlanView | null>>();

/**
 * The daemon's copy of a session's plan (GET /sessions/{id}/plan, v1.350.0
 * lane C2): `{"steps": [{"goal", "state"}]}`. Null for no plan: a 404 (the
 * run made none, or an older daemon without the route), a failed read, or a
 * body that is not a plan. Never throws: a plan is never worth an error.
 */
export function readSessionPlan(session: string): Promise<PlanView | null> {
  const open = inflight.get(session);
  if (open) return open;
  const p = (async () => {
    try {
      return readPlanResponse(await get<unknown>(`/sessions/${encodeURIComponent(session)}/plan`));
    } catch {
      return null;
    } finally {
      inflight.delete(session);
    }
  })();
  inflight.set(session, p);
  return p;
}

/* ------------------------------------------------------------ the live plan */

export interface UseLivePlanOptions {
  /** The session whose plan to follow, or null for none. */
  session: string | null;
  /** The page's event window, newest first (useEvents). */
  events: readonly PlanEventLike[];
  /** The id of the newest event from before this turn (the chat page's
   *  sinceRef), or null to read the whole window. */
  boundary?: string | null;
  /** Read the daemon's copy at once: the page is joining a run already under
   *  way (a reopened chat waiting on it, the session page of a live run), so
   *  plan.created may have come and gone before it was watching. */
  eager?: boolean;
  /** The run's phase detail ("step 2 of 4: …"): when it says there is a plan
   *  the page never saw made, the daemon's copy is read. */
  hint?: string | null;
}

/**
 * The run's live checklist, or null while it has no plan (most runs never
 * do). Folded from the page's own plan.* events, ON TOP OF what was known
 * before, so a plan.created that left the page's bounded event window stays
 * known. The daemon's copy is read only when the events cannot tell: a page
 * that joined mid-run (`eager`), or a step or phase that speaks of a plan the
 * events never showed; at most twice per turn. A 404 (no plan, or an older
 * daemon) leaves what the events said, or nothing.
 *
 * Its state changes only when the plan does (a step starts or ends), never
 * per token, so a page may hold it.
 */
export function useLivePlan({ session, events, boundary = null, eager = false, hint = null }: UseLivePlanOptions): PlanView | null {
  const key = session ? `${session}\n${boundary ?? ""}` : null;
  const planEvents = useMemo(() => planEventsFor(events, session, boundary), [events, session, boundary]);
  const [seed, setSeed] = useState<{ key: string; plan: PlanView | null } | null>(null);
  // What the last fold produced for this turn (and which daemon answer it
  // stood on): the base of the next fold.
  const lastRef = useRef<{ key: string | null; plan: PlanView | null; seed: PlanView | null }>({
    key: null,
    plan: null,
    seed: null,
  });
  const triesRef = useRef<{ key: string | null; eager: boolean; hinted: boolean }>({
    key: null,
    eager: false,
    hinted: false,
  });
  const aliveRef = useRef(true);
  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  const seedPlan = seed && seed.key === key ? seed.plan : null;
  const plan = useMemo(() => {
    if (!key) return null;
    const last = lastRef.current.key === key ? lastRef.current : null;
    // A NEW answer from the daemon re-bases the fold: it is newer than any
    // event that has left the window, and the window is folded over it again.
    const base = seedPlan && seedPlan !== last?.seed ? seedPlan : (last?.plan ?? seedPlan);
    const next = foldPlanEvents(base, planEvents);
    // The same checklist keeps its object: most events are not plan events.
    return last && samePlan(last.plan, next) ? last.plan : next;
  }, [key, planEvents, seedPlan]);

  useEffect(() => {
    lastRef.current = { key, plan, seed: seedPlan };
  }, [key, plan, seedPlan]);

  // Nothing followed (the run ended, or the page left it): what was known
  // belonged to that run. The next run a page follows starts afresh, even
  // under the same key (the session page's follow-up continues the SAME
  // session): no daemon answer and no spent read carry over to it.
  useEffect(() => {
    if (key) return;
    triesRef.current = { key: null, eager: false, hinted: false };
    setSeed((prev) => (prev === null ? prev : null));
  }, [key]);

  const sawCreated = planEvents.some((e) => e.type === "plan.created");
  const hinted = planEvents.length > 0 || phaseSaysPlan(hint);
  useEffect(() => {
    if (!key || !session) return;
    if (triesRef.current.key !== key) triesRef.current = { key, eager: false, hinted: false };
    const tries = triesRef.current;
    if (sawCreated || seedPlan) return; // the plan is known whole
    let go = false;
    if (eager && !tries.eager) {
      tries.eager = true;
      go = true;
    } else if (hinted && !tries.hinted) {
      tries.hinted = true;
      tries.eager = true;
      go = true;
    }
    if (!go) return;
    void readSessionPlan(session).then((answer) => {
      if (!aliveRef.current || triesRef.current.key !== key) return; // another turn now
      setSeed({ key, plan: answer });
    });
  }, [key, session, eager, hinted, sawCreated, seedPlan]);

  return plan;
}
