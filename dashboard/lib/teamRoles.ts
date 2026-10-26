/**
 * v1.394.0 (the long-jobs plan's Phase 5): the ROLES a model team member can
 * have — the TWIN of `src/iron_jarvis/agents/model_team.py` ROLES, pinned
 * equal by `tests/test_model_team_v1394.py` (ids, labels and order). A custom
 * agent with one or more roles is a team member; the planner hands it the
 * kind of work its roles name. No other import, so any page may read it.
 */

export interface TeamRole {
  id: string;
  label: string;
}

export const TEAM_ROLES: readonly TeamRole[] = [
  { id: "read", label: "Reading and summarizing" },
  { id: "code", label: "Writing code" },
  { id: "review", label: "Reviewing a change" },
  { id: "test", label: "Running and fixing tests" },
];

export const TEAM_ROLE_IDS: readonly string[] = TEAM_ROLES.map((r) => r.id);

/** The known roles in `raw`, once each, in TEAM_ROLES order (junk dropped). */
export function cleanRoles(raw: unknown): string[] {
  const have = new Set(
    (Array.isArray(raw) ? raw : []).filter((r): r is string => typeof r === "string").map((r) => r.trim().toLowerCase()),
  );
  return TEAM_ROLE_IDS.filter((id) => have.has(id));
}

/** "Reading and summarizing, Writing code" — the roles in words. */
export function roleWords(roles: readonly string[]): string {
  const labels = TEAM_ROLES.filter((r) => roles.includes(r.id)).map((r) => r.label);
  return labels.join(", ");
}
