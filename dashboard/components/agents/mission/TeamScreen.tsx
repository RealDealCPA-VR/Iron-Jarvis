"use client";

// YOUR TEAM (v1.308.0) — the rail's "Agents" row. The round table is gone;
// what stays is everything about the agents themselves: who exists (built-in,
// yours, remote), each one's portrait and face, its inbox, folder and coach,
// and the create / connect / edit surfaces. It is the very panel the old
// agents dialog showed (`AgentsPanel`), now a page section, so an agent can
// never look different here than anywhere else.
//
// Work is not given from here — the user gives Jarvis an objective (New task)
// and Jarvis hands the parts out. So the panel's Talk / Give work buttons are
// not offered.

import { useApi } from "@/lib/useApi";
import { useModels } from "@/lib/useModels";
import type { AgentsResponse } from "@/lib/types";
import { AgentsPanel } from "@/components/agents/AgentsModal";
import type { DynamicAgentFull } from "@/components/agents/SetupCard";
import type { RosterEntry } from "@/components/agents/RosterStrip";
import type { RemoteAgentInfo } from "@/components/agents/identity";
import { MissionRail, type RailTarget } from "./MissionRail";

export function TeamScreen({ onRail }: { onRail: (target: RailTarget) => void }) {
  const { data: agentsData, reload: reloadAgents } = useApi<AgentsResponse>("/agents");
  const { data: remoteData, reload: reloadRemotes } = useApi<{
    agents?: RemoteAgentInfo[];
    remotes?: RemoteAgentInfo[];
  }>("/agents/remote");
  const { data: rosterData, reload: reloadRoster } = useApi<{ roster?: RosterEntry[] }>("/agents/roster");
  const { data: modelsData } = useModels();
  const roster = (rosterData?.roster ?? []).filter(
    (e): e is RosterEntry => Boolean(e) && typeof e.name === "string",
  );
  return (
    <div data-testid="team-screen" className="grid grid-cols-1 gap-3 lg:grid-cols-[12.5rem_minmax(0,1fr)] lg:items-start">
      <div className="lg:sticky lg:top-3">
        <MissionRail active="agents" onAction={onRail} />
      </div>
      <section className="card-surface flex h-[calc(100vh-7rem)] min-h-[32rem] flex-col p-0">
        <AgentsPanel
          roster={roster}
          dynamic={(agentsData?.dynamic ?? []) as DynamicAgentFull[]}
          remotes={remoteData?.agents ?? remoteData?.remotes ?? []}
          models={modelsData?.models ?? []}
          selected={null}
          onSelect={() => {}}
          onAgentsChanged={() => {
            reloadAgents();
            reloadRoster();
          }}
          onRemotesChanged={() => {
            reloadRemotes();
            reloadRoster();
          }}
        />
      </section>
    </div>
  );
}
