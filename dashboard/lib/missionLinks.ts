// WHERE A SESSION OPENS (v1.309.0) — one rule for every list OUTSIDE the
// mission screen (Overview, a project's Activity, Continue on an interrupted
// job, the bell).
//
// Since v1.307.0 a mission is a session whose origin is "job:mission", and
// its place is its mission screen (`/agents?mission=<id>`, inside its project
// when it has one) — the raw `/sessions/<id>` page is an Advanced-mode
// engineering view with no way back to the objective. Only the ROOT origin
// matches: a teammate ("job:mission-member", contract 1) is not a mission of
// its own, and every other run keeps its session page.
//
// Pure, and imports only lib/mission.ts (itself import-free) on purpose: ~71
// test files mock lib/api wholesale, and a helper living there would vanish
// under them.

import { missionPath } from "./mission";

/** The origin POST /missions stamps on a mission's root session. */
export const MISSION_ORIGIN = "job:mission";

/** The fields of a session row (or a /continue response) this rule reads. */
export interface SessionLinkRow {
  id: string;
  origin?: string | null;
  project_id?: string | null;
}

/** True for a mission's ROOT session (never a teammate). */
export function isMissionRow(row: Pick<SessionLinkRow, "origin">): boolean {
  return row.origin === MISSION_ORIGIN;
}

/** Where a session row opens: a mission on its mission screen, anything else
 *  on its session page. */
export function sessionHref(row: SessionLinkRow): string {
  if (isMissionRow(row)) return missionPath(row.id, row.project_id ?? "");
  return `/sessions/${encodeURIComponent(row.id)}`;
}
