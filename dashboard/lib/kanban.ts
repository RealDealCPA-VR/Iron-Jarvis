import type { SessionView, Review } from "./types";
import type { Tone } from "@/components/ui";
import { statusWords } from "@/lib/statusWords";

/** v1.420.0 (fix plan C5.2): "stopped" is its own lane (a run the user or
 *  Emergency stop ended is not a failure). */
export type LaneId = "active" | "review" | "completed" | "failed" | "stopped";

export interface LaneDef {
  id: LaneId;
  title: string;
  tone: Tone;
  hint: string;
  /** v1.352.0 (G17): what an empty lane says, in a sentence that reads with
   *  the lane's word ("No done sessions" did not). Optional: a lane without
   *  one (the board's own Queued lane) keeps the column's default line. */
  empty?: string;
}

/** A lane named and toned by the ONE status table (v1.352.0, G17): the board
 *  says "Working" / "Done" / "Failed" like the Sessions page, the mission
 *  cards and the inbox ("Active" / "Completed" here, "Working" / "Done" there,
 *  was the drift the audit measured). */
function lane(id: LaneId, status: string, hint: string, empty: string): LaneDef {
  const w = statusWords(status);
  return { id, title: w.word, tone: w.tone, hint, empty };
}

export const LANES: LaneDef[] = [
  lane("active", "active", "Running now", "Nothing is running"),
  // A review to approve, or a run paused on an ask: either way the user is
  // the next step — "Waiting for you", the paused card's own chip words.
  lane("review", "review", "Awaiting approval", "Nothing is waiting for you"),
  // v1.315.0: "Finished", not git's "Merged & done" — most finished runs
  // never merged anything (a summary, a memo, a reply).
  lane("completed", "completed", "Finished", "Nothing has finished yet"),
  lane("failed", "failed", "Errored or rejected", "Nothing failed"),
  // v1.420.0 (fix plan C5.2): a stopped run had sat in the red Failed lane
  // ("Errored, stopped or rejected") beside real failures since v1.352.0; it
  // has its own lane now, named and toned by the ONE status table — slate
  // "Stopped", the same word its card and the Sessions filter say.
  lane("stopped", "cancelled", "Stopped by you or by Emergency stop", "Nothing was stopped"),
];

/** A review takes precedence over the raw status — that is the review lane
 *  ("Waiting for you"; "In Review" before v1.352.0).
 *  So does a PAUSED run (v1.227.0): a session whose `waiting_on` names a
 *  pending ask is waiting for the user, and "Running now" under Active was
 *  the lie the approvals audit measured (30 of a 40-minute job spent waiting,
 *  indistinguishable from work). It sits in the lane whose hint already reads
 *  "Awaiting approval"; the card says which tool. */
export function laneFor(session: SessionView, hasReview: boolean): LaneId {
  if (hasReview) return "review";
  if (session.waiting_on?.approval_id) return "review";
  const s = session.status.toLowerCase();
  if (s === "completed") return "completed";
  // The backend emits exactly: active | completed | failed | cancelled.
  // A cancelled session is NOT in Active — otherwise it shows "Running now"
  // forever — and (v1.420.0) not in Failed either: it has its own grey lane.
  if (s === "failed") return "failed";
  if (s === "cancelled") return "stopped";
  return "active";
}

export function assignLanes(
  sessions: SessionView[],
  reviews: Record<string, Review>,
): Record<LaneId, SessionView[]> {
  const out: Record<LaneId, SessionView[]> = {
    active: [],
    review: [],
    completed: [],
    failed: [],
    stopped: [],
  };
  for (const s of sessions) out[laneFor(s, !!reviews[s.id])].push(s);
  return out;
}

/** Which review action (if any) a drag from `from` onto `to` should trigger. */
export function dropAction(from: LaneId, to: LaneId): "approve" | "reject" | null {
  if (from !== "review") return null;
  if (to === "completed") return "approve";
  if (to === "failed") return "reject";
  return null;
}
