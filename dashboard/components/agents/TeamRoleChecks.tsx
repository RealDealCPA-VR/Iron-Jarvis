"use client";

/**
 * v1.394.0 (the model team): the ROLES of a team member as one checkbox each
 * (lib/teamRoles — the daemon's vocabulary's twin). Drawn on an agent's card
 * on the Agents page and on each row of Settings › Model team; one or more
 * ticked makes the agent a team member, none takes it off the team.
 */

import { TEAM_ROLES } from "@/lib/teamRoles";

export function TeamRoleChecks({
  scope,
  value,
  disabled,
  onChange,
}: {
  /** The agent's name (or a starter row's key): ids and the group's label. */
  scope: string;
  value: readonly string[];
  disabled?: boolean;
  onChange: (next: string[]) => void;
}) {
  return (
    <div role="group" aria-label={`Team roles for ${scope}`} className="flex flex-wrap gap-x-3 gap-y-1">
      {TEAM_ROLES.map((r) => {
        const on = value.includes(r.id);
        return (
          <label key={r.id} className="inline-flex items-center gap-1.5 text-[12px] text-zinc-300">
            <input
              type="checkbox"
              data-testid={`team-role-${scope}-${r.id}`}
              checked={on}
              disabled={disabled}
              onChange={() =>
                onChange(TEAM_ROLES.map((x) => x.id).filter((id) => (id === r.id ? !on : value.includes(id))))
              }
            />
            {r.label}
          </label>
        );
      })}
    </div>
  );
}
