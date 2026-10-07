"use client";

// YOUR PROJECTS on the mission front door (v1.308.0): one row per active
// project with its team's faces and what is waiting on you — a click opens
// that project's mission screen (`/agents?project=<id>`), where objectives are
// worked by the project's own team. Renders nothing when there are no
// projects or the daemon predates `/agents/worlds`.

import { ChevronRight, FolderKanban, Users } from "lucide-react";
import { usePolledApi } from "@/lib/useApi";
import { countsLine, worldCards, type WorldsResponse } from "@/lib/agentWorlds";
import { TeamFaces } from "@/components/agents/world/WorldsGrid";

export function ProjectTeams({ onOpen }: { onOpen: (projectId: string) => void }) {
  const { data } = usePolledApi<WorldsResponse>("/agents/worlds", 30000);
  const cards = worldCards(data);
  if (cards.length === 0) return null;
  return (
    <section data-testid="mission-projects" className="card-surface p-0">
      <header className="flex items-center gap-2 border-b hairline px-4 py-2.5">
        <FolderKanban size={14} className="text-accent-soft/80" aria-hidden />
        <h2 className="text-[13px] font-semibold text-zinc-200">Your projects</h2>
        <span className="text-[11px] text-zinc-500">— give a project&apos;s own team an objective</span>
      </header>
      <ul className="divide-y divide-white/5">
        {cards.map((w) => (
          <li key={w.project.id}>
            <button
              type="button"
              data-testid={`mission-project-${w.project.id}`}
              onClick={() => onOpen(w.project.id)}
              className="flex w-full items-center gap-3 px-4 py-2.5 text-left hover:bg-white/[0.03]"
            >
              <span className="min-w-0 flex-1">
                <span className="block truncate text-[13px] font-medium text-zinc-100">
                  {w.project.name || "Untitled project"}
                </span>
                <span className="block truncate text-[11px] text-zinc-500">{countsLine(w.counts)}</span>
              </span>
              {w.counts.waiting > 0 && (
                <span className="shrink-0 rounded-full border border-amber-400/30 bg-amber-400/10 px-2 py-0.5 text-[11px] text-amber-300">
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
