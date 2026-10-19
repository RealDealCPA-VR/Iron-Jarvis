"use client";

/**
 * v1.349.0 (polish wave B, lane B2): the HELPER-AGENT ROW.
 *
 * When chat hands work to an agent (an escalation, an @agent with hands,
 * "Have X do this"), the transcript said it twice: a saved line "Handing this
 * to the researcher: …" and, while the run lasted, a separate live bubble
 * with its last few steps. Now it is ONE quiet row in WorkLine's calm grey
 * (no card, no border, no shadow), borrowing the idea of agent-elements'
 * subagent row (21st.dev, MIT; ideas only, nothing installed):
 *
 *   live     "The researcher is working · Step 2 of 4: draft the summary · 1:24"
 *            with a chevron; open, it shows why the work was handed over and
 *            the step rows (the run's progress labels plus the live tool rows
 *            from WorkLine). The agent's own words stay below the row, where
 *            they always were.
 *   settled  "The researcher worked for 2 min 5 s · 6 steps" and an "Open run"
 *            link to the session; open, it shows why it was handed over. The
 *            reply and the run card under it are unchanged.
 *
 * The clock ticks itself (a leaf, TurnClock's pattern), so the chat page never
 * re-renders once a second. The live words stay out of the page's state: this
 * row reads only the run's tool cards and phase (which the page already
 * holds), never its streamed text (S-02/S-03).
 *
 * WAVE C (v1.350.0, lane C1): a run that MAKES A PLAN (the decomposed lane)
 * shows it as a quiet checklist (components/chat/PlanChecklist) in the
 * expanded body, between the hand-off reason and the step rows, ticking off
 * as plan.step_started / plan.step_completed arrive. Closed, the live row
 * keeps "Step 2 of 4: …" and adds "1 of 4 done". The step rows below then
 * leave out the plan's own progress lines ("Planned 4 steps", "Step 1 of 4
 * done"): the checklist says them. The settled row opens to the plan as it
 * ended (saved on the stamp by finalize). The body still takes `children`
 * after the checklist.
 */

import {
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import Link from "next/link";
import { Check, ChevronDown, Loader2, Zap } from "lucide-react";
import type { ToolCard } from "@/lib/useChatStream";
import { LiveToolRows, WorkRow } from "@/components/chat/WorkLine";
import { formatElapsed, useNow } from "@/components/chat/TurnClock";
import { secondsText } from "@/components/chat/TurnReceipt";
import {
  agentRunName,
  handOffSentence,
  liveRuns,
  runDurationMs,
  runEndWords,
  type AgentRunStamp,
  type HandOffLike,
} from "@/lib/agentRun";
import { PlanChecklist } from "@/components/chat/PlanChecklist";
import { isPlanProgressLine, planCountWords, readSavedPlan, type PlanView } from "@/lib/planChecklist";

/* ------------------------------------------------------------------- words */

const flat = (s: string) => s.replace(/\s+/g, " ").trim();
const clip = (s: string, max = 80) => (s.length > max ? `${s.slice(0, max - 1)}…` : s);
const noEllipsis = (s: string) => s.replace(/(…|\.\.\.)$/, "").trim();
/** "is working" already says it: a current step that only says "Working"
 *  (the plain running phase, "Working the task") adds nothing to it. */
const SAYS_WORKING = /^working\b/i;

/**
 * What the run is on now, in a few words, or null:
 *  1. a decomposed run's own step ("step 2 of 4: draft the summary", the
 *     phase detail the daemon sends) reads "Step 2 of 4: draft the summary";
 *  2. else the phase's label ("Planning the work", "Checking its work");
 *  3. else the newest progress line from the run's events ("Using
 *     read_file", "Step 2 of 4 done");
 * each without a trailing ellipsis, and none that only says "Working".
 */
export function currentStepWords(input: {
  phaseTitle?: string | null;
  phaseDetail?: string | null;
  progress?: readonly string[] | null;
}): string | null {
  const detail = flat(input.phaseDetail ?? "");
  if (/^step\s+\d/i.test(detail)) return clip(detail.charAt(0).toUpperCase() + detail.slice(1));
  const fromPhase = noEllipsis(flat(input.phaseTitle ?? ""));
  if (fromPhase && !SAYS_WORKING.test(fromPhase)) return clip(fromPhase);
  const newest = noEllipsis(flat(input.progress?.[0] ?? ""));
  if (newest && !SAYS_WORKING.test(newest)) return clip(newest);
  return null;
}

/** The live row's words: "The researcher is working · Step 2 of 4: …". */
export function liveRunWords(name: string, current: string | null): string {
  return current ? `${name} is working · ${current}` : `${name} is working`;
}

/** "6 steps" / "1 step", or null for none or unknown. */
export function stepsWords(steps: number | null): string | null {
  if (steps === null || steps <= 0) return null;
  return steps === 1 ? "1 step" : `${steps} steps`;
}

/** The settled row's words: "The researcher worked for 2 min 5 s · 6 steps".
 *  The length reads like the folded "Worked for …" line (TurnReceipt's
 *  secondsText); without one known it says only that the run finished.
 *
 *  v1.352.0 (G16, G19): and how it ENDED, last, when it did not simply
 *  finish: "· stopped" (the user pressed Stop), "· failed", "· hit its step
 *  limit", "· needs you" (an ask went unanswered) or "· part failed" (a step
 *  that changes things failed): "The researcher worked for 2 min 5 s · 6
 *  steps · part failed". Without a known length a stopped run "was stopped"
 *  and a failed one "could not finish". A stamp from before v1.352.0 has no
 *  status and reads exactly as it did. */
export function settledRunWords(name: string, run: AgentRunStamp): string {
  const dur = secondsText(runDurationMs(run));
  const end = runEndWords(run);
  let head: string;
  let tail = end;
  if (dur) head = `${name} worked for ${dur}`;
  else if (run.status === "cancelled") {
    head = `${name} was stopped`;
    tail = null; // said already
  } else if (run.status === "failed") {
    head = run.stepLimit ? `${name} hit its step limit` : `${name} could not finish`;
    tail = null;
  } else head = `${name} finished`;
  const steps = stepsWords(run.steps);
  return [head, steps, tail].filter(Boolean).join(" · ");
}

/* ------------------------------------------------------------ the registry */

// A layout effect on the client (the line must step aside BEFORE the frame
// paints, or the saved line and the live row show together for one frame);
// a plain effect where there is no DOM.
const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** Is a live row on screen for `session`? Subscribed in a layout effect and
 *  re-checked there, so a row that mounts in the same commit is seen before
 *  paint whichever of the two commits first. */
function useRunIsLive(session: string | null | undefined): boolean {
  const [isLive, setIsLive] = useState(() => liveRuns.has(session));
  useIsoLayoutEffect(() => {
    const check = () => setIsLive(liveRuns.has(session));
    check();
    return liveRuns.subscribe(check);
  }, [session]);
  return isLive;
}

/**
 * The saved hand-off line's wrapper: renders it exactly as it was, except
 * while a live row for the same run is on screen, which says the same thing
 * and more. A message that waits on nothing (or on a run this window is not
 * showing: another window's hand-off) always shows its line.
 *
 * While it steps aside it leaves ONE hidden marker (`data-handoff-hidden`),
 * so the transcript's message wrapper can hide itself too
 * (`has-[[data-handoff-hidden]]:hidden` on the chat page's messages.map
 * wrapper): an EMPTY wrapper is still an item in the transcript's gap-4
 * column, and the live row sat 16 px lower than a plain agent turn's, then
 * jumped up when the run settled (v1.349.0 integrate).
 */
export function HandOffLine({
  session,
  children,
}: {
  session?: string | null;
  children: ReactNode;
}) {
  const isLive = useRunIsLive(session ?? null);
  return isLive ? <span data-handoff-hidden="" hidden /> : <>{children}</>;
}

/* -------------------------------------------------------------- the pieces */

function Dot() {
  return <span aria-hidden="true" className="h-[3px] w-[3px] shrink-0 rounded-full bg-zinc-600" />;
}

/** The run's clock, "12s" then "1:24", ticking ITSELF: only this leaf
 *  re-renders each second. Nothing under a second (a clock reading 0 says
 *  nothing). */
function RunClock({ since }: { since: number }) {
  const now = useNow(true);
  const ms = Math.max(0, now - since);
  if (ms < 1000) return null;
  return (
    <>
      <Dot />
      <span data-testid="agent-run-clock" className="shrink-0 tabular-nums">
        {formatElapsed(ms)}
      </span>
    </>
  );
}

const TOGGLE =
  "flex min-w-0 flex-1 items-center gap-2 text-left text-[13px] leading-5 text-zinc-500 transition-colors " +
  "hover:text-zinc-300 focus-visible:text-zinc-300 focus-visible:underline focus-visible:underline-offset-[3px] " +
  "focus-visible:outline-none motion-reduce:transition-none";

function Chevron({ open }: { open: boolean }) {
  return (
    <ChevronDown
      size={14}
      aria-hidden="true"
      className={`shrink-0 transition-transform motion-reduce:transition-none ${open ? "rotate-180" : ""}`}
    />
  );
}

/** Why the work was handed over: the saved line's own sentence. */
function Why({ text }: { text: string }) {
  return (
    <p data-testid="agent-run-why" className="text-[13px] leading-5 text-zinc-500">
      {text}
    </p>
  );
}

/* --------------------------------------------------------------- live row */

export interface AgentRunLiveProps {
  /** The session the page is waiting on. */
  session: string;
  /** The hand-off message this run belongs to (its saved line steps aside
   *  while this row is on screen), or null for an agent turn with none. */
  handOff?: HandOffLike | null;
  /** When the run started (ms epoch, the session's own `created_at`), or
   *  null until the page has read it; the row's mount time stands in. */
  since?: number | null;
  /** The run's progress lines from its events, newest first. */
  progress: readonly string[];
  /** The run's live tool calls (useRunStream). */
  tools: readonly ToolCard[];
  /** The phase's label ("Planning the work…"), or null before the first
   *  phase frame. */
  phaseTitle?: string | null;
  /** The phase's detail ("step 2 of 4: draft the summary"). */
  phaseDetail?: string | null;
  /** v1.350.0: the run's live plan checklist (components/chat/PlanChecklist
   *  useLivePlan), or null for a run that made no plan (most never do). */
  plan?: PlanView | null;
  /** Anything else for the expanded body, after the checklist. */
  children?: ReactNode;
}

/** A phase detail naming the plan's step ("step 2 of 4: …"): the checklist
 *  shows that step, so the running row need not say it again. */
const STEP_DETAIL = /^step\s+\d+/i;

/** The running row. Open by default, so the steps stay where they were. */
export function AgentRunLive({
  session,
  handOff,
  since,
  progress,
  tools,
  phaseTitle,
  phaseDetail,
  plan = null,
  children,
}: AgentRunLiveProps) {
  const [open, setOpen] = useState(true);
  const bodyId = useId();
  const [mountedAt] = useState(() => Date.now());
  const start = typeof since === "number" && Number.isFinite(since) ? since : mountedAt;
  const cards = tools ?? [];
  const lines = progress ?? [];
  // With a checklist on screen, the plan's own progress lines ("Planned 4
  // steps", "Step 1 of 4 done") are said by it: the step rows keep the rest.
  const rowLines = plan ? lines.filter((l) => !isPlanProgressLine(l)) : lines;
  const runningDetail = plan && phaseDetail && STEP_DETAIL.test(phaseDetail.trim()) ? null : phaseDetail || null;

  // While this row is on screen, the saved hand-off line for the same run
  // steps aside (HandOffLine): the row says the same thing and more.
  useIsoLayoutEffect(() => liveRuns.add(session), [session]);

  const name = agentRunName(handOff?.escalatedTo);
  const current = currentStepWords({ phaseTitle, phaseDetail, progress: lines });
  const why = handOff?.escalated
    ? handOffSentence(handOff.escalated, handOff.escalatedTo, handOff.droppedRemote)
    : "";
  // ONE clock at a time (the v1.348.0 rule): open with tool rows listed, the
  // live work line under the header already counts the run ("Working for
  // 1 min 24 s · read 2 files"), from the same start.
  const headClock = !open || cards.length === 0;
  return (
    <div data-testid="agent-run-row" data-state="running" className="grid gap-1.5">
      {/* aria-live off on the WHOLE header: the row sits in the bubble's
          polite live region, and the steps below already announce themselves
          as they arrive; a header that changes with them, and a clock that
          changes every second, must not be read out again. */}
      <div data-testid="agent-run-head" aria-live="off" className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          data-testid="agent-run-toggle"
          aria-expanded={open}
          aria-controls={open ? bodyId : undefined}
          onClick={() => setOpen((v) => !v)}
          className={TOGGLE}
        >
          <Zap size={14} aria-hidden="true" className="shrink-0 text-accent-soft" />
          <span data-testid="agent-run-words" className="min-w-0 truncate">
            <span className="ij-shimmer text-zinc-300">{name} is working</span>
            {current ? ` · ${current}` : ""}
          </span>
          {/* v1.350.0: closed, the row still says how far the plan is ("1 of
              4 done"); open, the checklist below says it step by step. */}
          {!open && plan ? (
            <>
              <Dot />
              <span data-testid="agent-run-plan-count" className="shrink-0 tabular-nums">
                {planCountWords(plan)}
              </span>
            </>
          ) : null}
          {headClock ? <RunClock since={start} /> : null}
          <Chevron open={open} />
        </button>
      </div>
      {open && (
        <div id={bodyId} data-testid="agent-run-body" className="grid gap-1.5">
          {why ? <Why text={why} /> : null}
          {plan ? <PlanChecklist plan={plan} /> : null}
          {children}
          {/* Calm chat W1-5 (v1.326.0): the run's work as grey one-line rows,
              oldest first: the last few steps it finished, its tool calls,
              then the step it is on now (spinning, softly shimmering). */}
          <div data-testid="live-work" className="grid gap-1">
            {rowLines.slice(1, 4).reverse().map((s, i) => (
              <WorkRow key={`${i}-${s}`} icon={Check} title={s.replace(/…$/, "")} />
            ))}
            <LiveToolRows cards={cards} since={start} />
            {/* v1.149.0: the run's OWN phase, straight from the daemon, in
                place of a generic "Thinking…". */}
            <WorkRow
              icon={Loader2}
              running
              title={phaseTitle || (rowLines[0] ?? "Thinking…")}
              detail={runningDetail}
            />
          </div>
        </div>
      )}
    </div>
  );
}

/* ------------------------------------------------------------ settled row */

export interface AgentRunSettledProps {
  /** Who did it, at the start of a sentence ("The researcher"). */
  name: string;
  /** The stamp finalize saved on the hand-off message. */
  run: AgentRunStamp;
  /** The saved line's sentence ("Handing this to …: …."), shown open. */
  why?: string | null;
  /** Anything else for the opened body, after the run's plan. */
  children?: ReactNode;
}

/** The row once the run is over: how long, how many steps, "Open run".
 *  Opened, it says why the work was handed over and (v1.350.0) the plan the
 *  run worked through, as it ended (the stamp's own copy, read from the
 *  thread alone: nothing is fetched). */
export function AgentRunSettled({ name, run, why, children }: AgentRunSettledProps) {
  const [open, setOpen] = useState(false);
  const bodyId = useId();
  const words = settledRunWords(name, run);
  const plan = useMemo(() => readSavedPlan(run.plan), [run.plan]);
  const canOpen = !!why || !!plan || children != null;
  return (
    <div data-testid="agent-run-row" data-state="settled" className="grid gap-1.5">
      <div className="flex min-w-0 items-center gap-3">
        {canOpen ? (
          <button
            type="button"
            data-testid="agent-run-toggle"
            aria-expanded={open}
            aria-controls={open ? bodyId : undefined}
            onClick={() => setOpen((v) => !v)}
            className={TOGGLE}
          >
            <Zap size={14} aria-hidden="true" className="shrink-0 text-accent-soft" />
            <span data-testid="agent-run-words" className="min-w-0 truncate">
              {words}
            </span>
            <Chevron open={open} />
          </button>
        ) : (
          <div className="flex min-w-0 flex-1 items-center gap-2 text-[13px] leading-5 text-zinc-500">
            <Zap size={14} aria-hidden="true" className="shrink-0 text-accent-soft" />
            <span data-testid="agent-run-words" className="min-w-0 truncate">
              {words}
            </span>
          </div>
        )}
        <Link
          href={`/sessions/${encodeURIComponent(run.session)}`}
          data-testid="agent-run-open"
          className="shrink-0 text-[13px] leading-5 text-zinc-500 underline decoration-zinc-600 decoration-dotted underline-offset-[3px] transition-colors hover:text-zinc-300 focus-visible:text-zinc-300 focus-visible:decoration-solid focus-visible:outline-none motion-reduce:transition-none"
        >
          Open run
        </Link>
      </div>
      {open && canOpen && (
        <div id={bodyId} data-testid="agent-run-body" className="grid gap-1.5">
          {why ? <Why text={why} /> : null}
          {plan ? <PlanChecklist plan={plan} /> : null}
          {children}
        </div>
      )}
    </div>
  );
}
