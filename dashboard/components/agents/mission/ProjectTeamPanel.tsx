"use client";

// A PROJECT'S TEAM in the mission screen's right column (v1.308.0): who works
// on this project's objectives, and the door to change it. The choice is
// REAL — a mission started here hands work only to these agents (the daemon
// snapshots the team onto the run and refuses anyone else). With no team,
// Jarvis picks from everyone, and the panel says so.

import { useState } from "react";
import { Pencil } from "lucide-react";
import AgentFace from "@/components/agents/AgentFace";
import { Modal } from "@/components/Modal";
import { TeamEditor } from "@/components/agents/world/TeamEditor";
import type { RosterEntry } from "@/components/agents/RosterStrip";
import { bareMemberName, memberAvatar, memberSource, type WorldMember } from "@/lib/agentWorlds";
import { useApi } from "@/lib/useApi";

export function ProjectTeamPanel({
  projectId,
  projectName,
  team,
  onSaved,
}: {
  projectId: string;
  projectName: string;
  team: WorldMember[];
  onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const { data } = useApi<{ roster?: RosterEntry[] }>(editing ? "/agents/roster" : null);
  const roster = (data?.roster ?? []).filter(
    (e): e is RosterEntry => Boolean(e) && typeof e.name === "string",
  );
  return (
    <div data-testid="mission-project-team" className="space-y-2.5">
      {team.length === 0 ? (
        <p className="px-1 text-[12px] text-zinc-400">
          No team picked yet — Jarvis chooses from all your agents. Pick a team to keep this project&apos;s work
          with the agents you choose.
        </p>
      ) : (
        <>
          <p className="px-1 text-[12px] text-zinc-400">Jarvis hands this project&apos;s work only to:</p>
          <ul className="space-y-1.5">
            {team.map((m) => {
              const bare = bareMemberName(m.name);
              return (
                <li key={m.name} data-testid={`project-team-${m.name}`} className="flex items-center gap-2.5 px-1">
                  <AgentFace name={bare} size={26} avatarUrl={memberAvatar(m) ?? undefined} title="" />
                  <div className="min-w-0">
                    {/* v1.316.0: CSS title-case for a built-in only — a custom
                        or remote teammate's name reads exactly as typed. */}
                    <div
                      className={`truncate text-[12.5px] font-medium text-zinc-200 ${
                        memberSource(m) === "builtin" ? "capitalize" : ""
                      }`}
                    >
                      {bare}
                    </div>
                    {m.description && <div className="truncate text-[11px] text-zinc-500">{m.description}</div>}
                  </div>
                </li>
              );
            })}
          </ul>
        </>
      )}
      <button
        type="button"
        data-testid="mission-edit-team"
        onClick={() => setEditing(true)}
        className="btn-ghost w-full justify-center py-1.5 text-[12px]"
      >
        <Pencil size={12} /> {team.length ? "Edit team" : "Pick a team"}
      </button>
      {editing && (
        <Modal label="Edit the team" onClose={() => setEditing(false)} testId="mission-team-modal">
          <div className="p-4">
            <TeamEditor
              projectId={projectId}
              projectName={projectName}
              roster={roster}
              mode={team.length ? "edit" : "build"}
              onSaved={() => {
                setEditing(false);
                onSaved();
              }}
              onCancel={() => setEditing(false)}
            />
          </div>
        </Modal>
      )}
    </div>
  );
}
