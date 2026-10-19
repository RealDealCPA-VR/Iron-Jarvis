import type { SessionView, Review } from "./types";
import type { Tone } from "@/components/ui";
import { statusWords } from "@/lib/statusWords";

export type LaneId = "active" | "review" | "completed" | "failed";

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
  // A stopped run sits here too (laneFor below); its card says "Stopped".
  lane("failed", "failed", "Errored, stopped or rejected", "Nothing failed or was stopped"),
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
  // Cancelled belongs with failed ("Errored or rejected"), NOT in Active —
  // otherwise a cancelled session shows "Running now" forever.
  if (s === "failed" || s === "cancelled") return "failed";
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
