"use client";

// The mission screen's own two places (v1.307.0 rail; v1.329.0 calm text
// tabs): New task and Your team. Both belong to the Agents page itself.
//
// v1.329.0 (calm chat wave 8, J4): this used to be a bordered card of seven
// rows (New task, Chat, Projects, Agents, Tools, Files, Settings) beside the
// app's sidebar, which already offers every app-wide one: Chat (New chat and
// the chat list), Projects and Settings are sidebar rows, and Tools and Files
// are on Everything (and in Ctrl K). Five of the seven repeated the sidebar,
// so they went. What stays is what nothing else offers: New task (the
// objective composer) and Your team (`?view=team`, where agents are made,
// edited and given faces). They read as the chat top bar's text tabs: the
// open one in ink, the other muted, no box and no chip.

export type RailTarget = "new" | "team";

interface Row {
  key: string;
  label: string;
  action: RailTarget;
}

export const MISSION_RAIL: Row[] = [
  { key: "new", label: "New task", action: "new" },
  { key: "agents", label: "Your team", action: "team" },
];

export function MissionRail({
  active,
  onAction,
}: {
  /** The tab to mark ("new" while composing, "agents" on Your team; none
   *  while watching an objective or reading an old room). */
  active: string | null;
  onAction: (target: RailTarget) => void;
}) {
  return (
    <nav aria-label="Agents" data-testid="mission-rail" className="flex items-center gap-2">
      {MISSION_RAIL.map((row) => {
        const on = active === row.key;
        return (
          <button
            key={row.key}
            type="button"
            data-testid={`mission-rail-${row.key}`}
            aria-current={on ? "page" : undefined}
            onClick={() => onAction(row.action)}
            className={`rounded-md px-1.5 py-1 text-[13px] transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-accent/50 ${
              on ? "font-medium text-zinc-100" : "text-zinc-500 hover:text-zinc-200"
            }`}
          >
            {row.label}
          </button>
        );
      })}
    </nav>
  );
}
