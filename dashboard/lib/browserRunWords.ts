/**
 * How a browser task ended, in words (v1.358.0, lane 8A), DEPENDENCY-FREE.
 *
 * THE FAILURE THIS ANSWERS. The bell titled every browser (computer-use) run
 * that did not complete "Computer-use run failed" and printed its raw status
 * and run id as the body ("awaiting_approval · 4 steps · run_ab12…"). A run
 * that only STOPPED TO ASK for the user's OK read as a failure, and a real
 * failure said nothing a person could act on.
 *
 * The harness ends a run with one of four statuses
 * (src/iron_jarvis/computeruse/harness.py, `computeruse.run_finished`):
 *
 *   completed          every checkpoint verified
 *   awaiting_approval  a website action needs the user's OK (NOT a failure)
 *   blocked            the user declined a step, or a page tried to steer it
 *   failed             a step did not work, or a check did not hold
 *
 * Each gets a title, one plain sentence and a TONE the bell draws through its
 * icon: `ask` (waiting for the user), `done`, `warn` (stopped for safety),
 * `danger` (could not finish). The raw status and the run id never reach the
 * visible text (the bell row opens the Browser page, where the run is).
 */

export type BrowserRunTone = "done" | "ask" | "warn" | "danger";

export interface BrowserRunWords {
  title: string;
  body: string;
  tone: BrowserRunTone;
}

/** "3 steps" / "1 step", or "" when the count is unknown. */
export function stepCount(steps: unknown): string {
  if (typeof steps !== "number" || !Number.isFinite(steps) || steps < 0) return "";
  const n = Math.floor(steps);
  return `${n} step${n === 1 ? "" : "s"}`;
}

/** The bell's words for a `computeruse.run_finished` payload's status. */
export function browserRunWords(status: unknown, steps?: unknown): BrowserRunWords {
  const s = typeof status === "string" ? status.trim().toLowerCase() : "";
  const count = stepCount(steps);
  switch (s) {
    case "completed":
      return {
        title: "Your browser task finished",
        body: count ? `It took ${count}.` : "",
        tone: "done",
      };
    case "awaiting_approval":
      return {
        title: "Your browser task is waiting for your OK",
        body: "It stopped before a website action that needs your OK. Approve it on the Browser page, then ask for that step again.",
        tone: "ask",
      };
    case "blocked":
      return {
        title: "Your browser task stopped for safety",
        body: "A website action was declined, or a page tried to give it instructions. The Browser page shows which.",
        tone: "warn",
      };
    case "failed":
      return {
        title: "Your browser task could not finish",
        body: count
          ? `A step did not work after ${count}. The Browser page shows which one.`
          : "A step did not work. The Browser page shows which one.",
        tone: "danger",
      };
    default:
      return {
        title: "Your browser task stopped",
        body: "The Browser page shows how far it got.",
        tone: "warn",
      };
  }
}
