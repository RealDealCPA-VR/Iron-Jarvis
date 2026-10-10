"use client";

// YOUR PROJECTS on the mission front door (v1.308.0): one row per active
// project with its team's faces and what is waiting on you — a click opens
// that project's mission screen (`/agents?project=<id>`), where objectives are
// worked by the project's own team. Renders nothing when there are no
// projects or the daemon predates `/agents/worlds`.
//
// v1.329.0 (calm chat wave 8, J4): a plain section, not a card. A quiet
// sentence-case label, then the rows between hairlines; the composer above is
// the page's one card.

import { ChevronRight, Users } from "lucide-react";
import { usePolledApi } from "@/lib/useApi";
import { countsLine, worldCards, type WorldsResponse } from "@/lib/agentWorlds";
import { TeamFaces } from "@/components/agents/world/WorldsGrid";

export function ProjectTeams({ onOpen }: { onOpen: (projectId: string) => void }) {
  const { data } = usePolledApi<WorldsResponse>("/agents/worlds", 30000);
  const cards = worldCards(data);
  if (cards.length === 0) return null;
  return (
    <section data-testid="mission-projects">
      <header className="flex flex-wrap items-baseline gap-x-2 px-2 pb-1.5">
        <h2 className="text-[13px] font-medium text-zinc-400">Your projects</h2>
        <span className="text-[12px] text-zinc-500">Give a project&apos;s own team an objective.</span>
      </header>
      <ul className="divide-y divide-white/[0.06] border-t hairline">
        {cards.map((w) => (
          <li key={w.project.id}>
            <button
              type="button"
              data-testid={`mission-project-${w.project.id}`}
              onClick={() => onOpen(w.project.id)}
              className="flex w-full items-center gap-3 rounded-lg px-2 py-2.5 text-left transition-colors hover:bg-white/[0.04]"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium text-zinc-100">
                  {w.project.name || "Untitled project"}
                </span>
                <span className="block truncate text-[11px] text-zinc-500">{countsLine(w.counts)}</span>
              </span>
              {w.counts.waiting > 0 && (
                <span className="shrink-0 rounded-full border border-tone-warn/30 bg-tone-warn/10 px-2 py-0.5 text-[11px] text-tone-warn">
                  {w.counts.waiting} waiting on you
                </span>
              )}
              <span className="shrink-0">
                {w.team.length > 0 ? (
                  <TeamFaces team={w.team} size={26} max={5} />
                ) : (
                  <span className="flex items-center gap-1 text-[11px] text-zinc-500">
                    <Users size={12} aria-hidden /> no team yet
                  </span>
                )}
              </span>
              <ChevronRight size={14} className="shrink-0 text-zinc-500" aria-hidden />
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
