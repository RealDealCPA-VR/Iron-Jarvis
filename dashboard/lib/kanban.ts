import type { SessionView, Review } from "./types";
import type { Tone } from "@/components/ui";

export type LaneId = "active" | "review" | "completed" | "failed";

export interface LaneDef {
  id: LaneId;
  title: string;
  tone: Tone;
  hint: string;
}

export const LANES: LaneDef[] = [
  { id: "active", title: "Active", tone: "cyan", hint: "Running now" },
  { id: "review", title: "In Review", tone: "amber", hint: "Awaiting approval" },
  { id: "completed", title: "Completed", tone: "green", hint: "Merged & done" },
  { id: "failed", title: "Failed", tone: "red", hint: "Errored or rejected" },
];

/** A review takes precedence over the raw status — that is the "In Review" lane. */
export function laneFor(session: SessionView, hasReview: boolean): LaneId {
  if (hasReview) return "review";
  const s = session.status.toLowerCase();
  if (s === "completed" || s === "succeeded" || s === "success") return "completed";
  if (s === "failed" || s === "error" || s === "rejected") return "failed";
  return "active"; // active / running / created / pending / unknown
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
