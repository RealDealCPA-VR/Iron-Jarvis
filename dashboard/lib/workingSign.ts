/**
 * The "working" sign of a Build › Code session (the logic; the drawing is
 * `components/buildCode/WorkingSign.tsx`).
 *
 * One small ring above the message box says the coding tool is at work:
 *   - while a reply the user started is being written;
 *   - while helpers work on after that reply ended (one segment per helper,
 *     the count in the middle; a helper that ends leaves a thin, steady
 *     segment behind);
 *   - while the tool writes a reply of its own;
 *   - (still, with no motion) while only background commands run.
 *
 * WHAT "FINISHED" MEANS HERE. Iron Jarvis says how many helpers are working
 * at each read of the session (`background_helpers`, every 4 s while the
 * window is seen). This file counts the drops between two reads. That is a
 * count of helpers SEEN to end while this session was on screen: a helper
 * that ended before the session was opened is not in it, and one that ended
 * while another started inside the same 4 s is not either. It is never more
 * than really ended. It starts again when no helper is left.
 *
 * No imports but a type: every function here is pure.
 */
import type { CodeSession } from "@/lib/buildApp";

type BackgroundFacts = Pick<CodeSession, "background" | "backgroundHelpers" | "selfReply">;

/** What the sign shows (null = nothing to show). */
export type WorkingSign =
  | { kind: "reply" }
  | { kind: "helpers"; working: number; finished: number }
  | { kind: "own" }
  | { kind: "commands"; count: number };

/** The helpers of one stretch of background work, as this window saw them. */
export interface HelpersSeen {
  working: number;
  finished: number;
}

function whole(n: number): number {
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

/**
 * One more read of the session: how many helpers work now. A drop since the
 * read before is that many helpers seen to end. No helper left ends the
 * stretch (null), so the next one counts from nothing.
 */
export function noteHelpers(prev: HelpersSeen | null, workingNow: number): HelpersSeen | null {
  const working = whole(workingNow);
  if (working === 0) return null;
  if (!prev) return { working, finished: 0 };
  return { working, finished: prev.finished + Math.max(0, prev.working - working) };
}

/**
 * What to show. The order is `backgroundWorkWords`' own (lib/buildApp):
 * helpers, then the tool's own reply, then commands. While a reply the user
 * started runs, the sign is the plain "working" one, and it steps aside for a
 * card that waits on the user (nothing is being worked on then).
 */
export function workingSignOf(
  s: BackgroundFacts | null,
  running: boolean,
  asking: boolean,
  finished: number,
): WorkingSign | null {
  if (!s) return null;
  if (running) return asking ? null : { kind: "reply" };
  const helpers = whole(s.backgroundHelpers);
  if (helpers > 0) return { kind: "helpers", working: helpers, finished: whole(finished) };
  if (s.selfReply) return { kind: "own" };
  const commands = whole(s.background);
  if (commands > 0) return { kind: "commands", count: commands };
  return null;
}

/** The words beside the ring. `secondary` is the quieter half ("" = none). */
export function signWords(sign: WorkingSign, toolName: string): { primary: string; secondary: string } {
  if (sign.kind === "reply") return { primary: `${toolName} is working…`, secondary: "" };
  if (sign.kind === "own") return { primary: `${toolName} is writing a reply of its own`, secondary: "" };
  if (sign.kind === "commands") {
    return {
      primary: sign.count === 1 ? "1 background command running" : `${sign.count} background commands running`,
      secondary: "",
    };
  }
  const one = sign.working === 1;
  return {
    primary: one ? "1 helper working" : `${sign.working} helpers working`,
    secondary: sign.finished > 0
      ? `${sign.finished} finished`
      : one
        ? "its report will appear here"
        : "their report will appear here",
  };
}

// ── the ring ────────────────────────────────────────────────────────────────

/** The ring's box and the radius its segments are drawn at (whole pixels). */
export const RING_BOX = 26;
export const RING_RADIUS = 11;
/** A working segment's line, and a finished one's (thinner: not colour alone). */
export const RING_LIVE_WIDTH = 3;
export const RING_DONE_WIDTH = 2;
/** Past this many helpers the ring keeps this many segments and shares them
 *  out by how many ended (the number in the middle stays exact). */
export const RING_SLOTS_MAX = 12;
/** The number in the middle never needs a third digit. */
export const RING_COUNT_MAX = 99;

/** How many segments, and how many of them are finished ones. */
export function ringSlots(working: number, finished: number): { total: number; done: number; live: number } {
  const w = whole(working);
  const f = whole(finished);
  const all = w + f;
  if (all <= RING_SLOTS_MAX) return { total: all, done: f, live: w };
  let done = Math.round((f / all) * RING_SLOTS_MAX);
  if (f > 0) done = Math.max(1, done);
  if (w > 0) done = Math.min(RING_SLOTS_MAX - 1, done);
  return { total: RING_SLOTS_MAX, done, live: RING_SLOTS_MAX - done };
}

/**
 * Segment `i` of `total`, clockwise from 12 o'clock, in degrees: how long its
 * dash is and where it starts (SVG's own zero is 3 o'clock, hence the -90).
 * The line's round ends are taken off the dash so two segments never touch.
 * null = the whole circle (one segment has no gap to draw).
 */
export function ringSegment(i: number, total: number, strokeWidth: number): { dash: number; rotate: number } | null {
  if (total <= 1) return null;
  const slot = 360 / total;
  const cap = (strokeWidth / 2 / RING_RADIUS) * (180 / Math.PI);
  const gap = total <= 6 ? 14 : 10;
  const dash = Math.max(0.5, slot - gap - 2 * cap);
  return { dash, rotate: i * slot + gap / 2 + cap - 90 };
}

/** How long the light rests on one segment, by what the sign says: a reply
 *  being written steps a little quicker than helpers left to themselves. */
export const BEAT_REPLY_MS = 600;
export const BEAT_BACKGROUND_MS = 900;
/** With no segments (a reply being written) the light goes round in this
 *  many steps. */
export const SOLO_STEPS = 8;
