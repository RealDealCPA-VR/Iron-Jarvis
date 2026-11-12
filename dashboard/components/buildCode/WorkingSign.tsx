"use client";

/**
 * The "working" sign of a Build › Code session: a small ring just above the
 * message box that says the coding tool is at work (the owner: the one plain
 * line "5 helpers are still working in the background…" was too easy to
 * miss under a wall of "A helper finished" lines).
 *
 * WHAT IT SHOWS (lib/workingSign.ts decides; this file only draws):
 *   - a reply being written: a plain ring with a light going round it;
 *   - helpers at work after the reply ended: one segment per helper, the
 *     count of those still working in the middle, the light hopping from one
 *     working segment to the next; a helper seen to end leaves a thinner,
 *     steady segment;
 *   - the tool writing a reply of its own: the plain ring again;
 *   - only background commands (a dev server): a still ring and the quiet
 *     sentence, with no motion — nothing is being worked on for the user.
 *
 * WHERE. In the conversation's flow, at its end, in a slot that is ALWAYS
 * there (so nothing moves when the sign comes or goes), and `sticky` to the
 * bottom of the transcript: scrolled up, it stays in view just above the
 * message box, with a soft pill behind it so it reads over the text. At the
 * end of the conversation it has no box at all (the calm look).
 *
 * WORDS. The sentence a screen reader hears is the one the view has always
 * said (`backgroundWorkWords`, in the same `role="status"` element the older
 * tests read); the short words beside the ring are for the eye.
 *
 * MOTION. One light with a short tail, moved in steps (globals.css
 * `.wsign-hop`): under two new frames a second, transform and opacity only
 * (measured in Edge: about a third of one percent of a processor core more
 * than standing still, where the same light turning smoothly took about 23
 * points more of the graphics process). Still when the system asks for less
 * motion and while the window is hidden; the ring and its count say
 * "working" without it. The faint glow round the ring never changes.
 */

import { useEffect, useState, type CSSProperties } from "react";
import { backgroundWorkWords, type CodeSession } from "@/lib/buildApp";
import { useDocumentVisible } from "@/lib/useDocumentVisible";
import {
  BEAT_BACKGROUND_MS,
  BEAT_REPLY_MS,
  RING_BOX,
  RING_COUNT_MAX,
  RING_DONE_WIDTH,
  RING_LIVE_WIDTH,
  RING_RADIUS,
  SOLO_STEPS,
  noteHelpers,
  ringSegment,
  ringSlots,
  signWords,
  workingSignOf,
  type HelpersSeen,
  type WorkingSign as Sign,
} from "@/lib/workingSign";

/** The system's "reduce motion" setting (false where it cannot be read). */
function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    const sync = () => setReduced(mq.matches);
    sync();
    mq.addEventListener?.("change", sync);
    return () => mq.removeEventListener?.("change", sync);
  }, []);
  return reduced;
}

const C = RING_BOX / 2;

/** One segment of the ring (or the whole circle when there is only one). */
function Arc({ i, total, width, className }: { i: number; total: number; width: number; className: string }) {
  const seg = ringSegment(i, total, width);
  return (
    <circle
      cx={C}
      cy={C}
      r={RING_RADIUS}
      fill="none"
      strokeWidth={width}
      strokeLinecap="round"
      className={className}
      {...(seg
        ? {
            pathLength: 360,
            strokeDasharray: `${seg.dash.toFixed(2)} 360`,
            transform: `rotate(${seg.rotate.toFixed(2)} ${C} ${C})`,
          }
        : {})}
    />
  );
}

const LAYER = "absolute inset-0 h-full w-full overflow-visible";
const LIVE = "stroke-accent/40";
const DONE = "stroke-tone-success/80";
const TRACK = "stroke-accent/25";
const LIGHT = "stroke-accent-soft";
const GLOW = "drop-shadow-[0_0_2px_rgb(var(--accent-rgb)/0.7)]";
/** The light's tail: the same segment, one step behind, at half strength. */
const TAIL = "opacity-50";
/** A faint glow round a ring that is working. It never changes, so it costs
 *  no frames. */
const HALO =
  "rounded-full shadow-[0_0_16px_-2px_rgb(var(--accent-rgb)/0.35),inset_0_0_8px_-2px_rgb(var(--accent-rgb)/0.25)]";
const COUNT = "absolute inset-0 grid place-items-center text-[10px] font-semibold leading-none tabular-nums text-zinc-100";

function Ring({ sign, moving }: { sign: Sign; moving: boolean }) {
  const view = `0 0 ${RING_BOX} ${RING_BOX}`;
  const box = { width: RING_BOX, height: RING_BOX };

  if (sign.kind === "helpers") {
    const { total, done, live } = ringSlots(sign.working, sign.finished);
    const hop = moving && live > 1;
    const vars = {
      "--wsign-m": live,
      "--wsign-sweep": `${((live * 360) / total).toFixed(2)}deg`,
      "--wsign-beat": `${BEAT_BACKGROUND_MS}ms`,
    } as CSSProperties;
    return (
      <span
        data-testid="code-sign-ring"
        data-total={total}
        data-done={done}
        className={`relative shrink-0 ${HALO}`}
        style={box}
        aria-hidden="true"
      >
        <svg viewBox={view} className={LAYER}>
          {Array.from({ length: total }, (_, i) =>
            i < done ? (
              <Arc key={i} i={i} total={total} width={RING_DONE_WIDTH} className={DONE} />
            ) : (
              <Arc key={i} i={i} total={total} width={RING_LIVE_WIDTH} className={LIVE} />
            ),
          )}
        </svg>
        {/* The light: a bright copy of the first working segment. It hops
            round the working ones; with one left it dims and brightens. Its
            tail is the same light one step behind (the same steps at the
            same moments, so it adds no frame of its own). */}
        {hop ? (
          <svg
            viewBox={view}
            data-testid="code-sign-tail"
            style={{ ...vars, animationDelay: `-${(live - 1) * BEAT_BACKGROUND_MS}ms` }}
            className={`${LAYER} ${TAIL} wsign-hop`}
          >
            <Arc i={done} total={total} width={RING_LIVE_WIDTH} className={LIGHT} />
          </svg>
        ) : null}
        <svg
          viewBox={view}
          data-testid="code-sign-light"
          style={vars}
          className={`${LAYER} ${GLOW} ${hop ? "wsign-hop" : moving ? "wsign-tick" : ""}`}
        >
          <Arc i={done} total={total} width={RING_LIVE_WIDTH} className={LIGHT} />
        </svg>
        <span className={COUNT}>{Math.min(RING_COUNT_MAX, sign.working)}</span>
      </span>
    );
  }

  if (sign.kind === "commands") {
    return (
      <span data-testid="code-sign-ring" className="relative shrink-0" style={box} aria-hidden="true">
        <svg viewBox={view} className={LAYER}>
          <Arc i={0} total={1} width={RING_DONE_WIDTH} className="stroke-white/20" />
        </svg>
        <span className={`${COUNT} text-zinc-400`}>{Math.min(RING_COUNT_MAX, sign.count)}</span>
      </span>
    );
  }

  // A reply being written (the user's, or the tool's own): no segments, the
  // light goes round the whole ring.
  const beat = sign.kind === "reply" ? BEAT_REPLY_MS : BEAT_BACKGROUND_MS;
  const vars = {
    "--wsign-m": SOLO_STEPS,
    "--wsign-sweep": "360deg",
    "--wsign-beat": `${beat}ms`,
  } as CSSProperties;
  const arc = (
    <circle
      cx={C}
      cy={C}
      r={RING_RADIUS}
      fill="none"
      strokeWidth={RING_LIVE_WIDTH}
      strokeLinecap="round"
      pathLength={360}
      strokeDasharray="30 360"
      transform={`rotate(-90 ${C} ${C})`}
      className={LIGHT}
    />
  );
  return (
    <span data-testid="code-sign-ring" className={`relative shrink-0 ${HALO}`} style={box} aria-hidden="true">
      <svg viewBox={view} className={LAYER}>
        <Arc i={0} total={1} width={RING_LIVE_WIDTH} className={TRACK} />
      </svg>
      {moving ? (
        <svg
          viewBox={view}
          data-testid="code-sign-tail"
          style={{ ...vars, animationDelay: `-${(SOLO_STEPS - 1) * beat}ms` }}
          className={`${LAYER} ${TAIL} wsign-hop`}
        >
          {arc}
        </svg>
      ) : null}
      <svg
        viewBox={view}
        data-testid="code-sign-light"
        style={vars}
        className={`${LAYER} ${GLOW} ${moving ? "wsign-hop" : ""}`}
      >
        {arc}
      </svg>
      <span className="absolute left-1/2 top-1/2 h-1 w-1 -translate-x-1/2 -translate-y-1/2 rounded-full bg-accent-soft" />
    </span>
  );
}

const PRIMARY = "shrink-0 text-[13px] font-medium text-zinc-200";
const SECONDARY = "min-w-0 truncate text-[12px] text-zinc-500";

export function WorkingSign({
  session,
  toolName,
  running,
  asking,
  floating = false,
  compact = false,
}: {
  session: Pick<CodeSession, "background" | "backgroundHelpers" | "selfReply">;
  toolName: string;
  /** A reply the user started is being written. */
  running: boolean;
  /** A card waits on the user (the plain "working" sign steps aside). */
  asking: boolean;
  /** The reader scrolled up: the sign floats over the conversation's text
   *  and gets a soft pill behind it. */
  floating?: boolean;
  /** "Jump to latest" is on screen beside it: the sign keeps to its first
   *  few words, and on a narrow window to its ring (the count is in it), so
   *  the two never overlap. */
  compact?: boolean;
}) {
  // The helpers seen to end, counted from one read of the session to the
  // next (lib/workingSign.noteHelpers). Kept here: a session's pane is
  // mounted per session, so another session starts from nothing.
  const [seen, setSeen] = useState<HelpersSeen | null>(null);
  const next = noteHelpers(seen, session.backgroundHelpers);
  if ((next?.working ?? 0) !== (seen?.working ?? 0) || (next?.finished ?? 0) !== (seen?.finished ?? 0)) {
    setSeen(next);
  }
  const visible = useDocumentVisible();
  const reduced = useReducedMotion();

  const sign = workingSignOf(session, running, asking, next?.finished ?? 0);
  const moving = visible && !reduced;
  // The sentence the view has always said (and a screen reader still hears).
  const sentence = backgroundWorkWords(session, toolName, running);
  const words = sign ? signWords(sign, toolName) : null;
  const hideWords = compact ? "max-sm:hidden" : "";

  return (
    <div
      data-testid="code-sign-slot"
      // Always here, always this tall: the sign comes and goes inside it and
      // nothing around it moves. `sticky`: scrolled up, it stays in view.
      // (A sticky box positions what it holds, so the screen-reader sentence
      // below stays inside the transcript: the v1.420.2 rule.)
      //
      // `-bottom-2`, not `bottom-2`: the browser measures a sticky box's
      // distance from INSIDE the transcript's padding (16 px), so this is
      // 8 px above the transcript's edge. With a positive distance the slot
      // could never rest in its own place at the end of the conversation: it
      // was pushed up 14 px, onto the last line (measured in Edge, and held
      // by e2e/build-code-working-sign.spec.ts).
      className="pointer-events-none sticky -bottom-2 z-[5] -mb-2 mt-2 flex h-8 w-full items-center"
    >
      {sign && words ? (
        <div
          data-testid="code-working-sign"
          data-sign={sign.kind}
          data-motion={moving ? "on" : "off"}
          data-floating={floating ? "true" : "false"}
          title={sign.kind === "reply" ? undefined : sentence || undefined}
          className={`pointer-events-auto relative inline-flex h-8 max-w-full items-center gap-2.5 rounded-full pl-[3px] pr-3 transition-[background-color,box-shadow] duration-200 motion-reduce:transition-none ${
            floating ? "bg-ink-850/95 shadow-[0_0_0_0.5px_rgb(var(--white)/0.14)]" : ""
          }`}
        >
          {sign.kind === "reply" ? (
            <p data-testid="code-working" className="flex min-w-0 items-center gap-2.5">
              <Ring sign={sign} moving={moving} />
              <span className={`${PRIMARY} ${hideWords}`}>{words.primary}</span>
            </p>
          ) : sign.kind === "commands" ? (
            <>
              <Ring sign={sign} moving={false} />
              <p
                role="status"
                data-testid="code-background"
                className={`min-w-0 truncate text-[12px] text-zinc-500 ${compact ? "max-sm:sr-only" : ""}`}
              >
                {sentence}
              </p>
            </>
          ) : (
            <>
              <Ring sign={sign} moving={moving} />
              <span aria-hidden="true" className={`flex min-w-0 items-baseline gap-2 ${hideWords}`}>
                <span data-testid="code-sign-primary" className={PRIMARY}>
                  {words.primary}
                </span>
                {words.secondary && !compact ? (
                  <span data-testid="code-sign-secondary" className={SECONDARY}>
                    {words.secondary}
                  </span>
                ) : null}
              </span>
              <p role="status" data-testid="code-background" className="sr-only">
                {sentence}
              </p>
            </>
          )}
        </div>
      ) : null}
    </div>
  );
}
