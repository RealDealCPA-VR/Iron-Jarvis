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
//
// v1.309.0: `/agents?view=team&agent=<roster name>` opens this screen ON that
// agent — where an agent's notifications (coach, pause, allowance, a blocked
// assignment) land, instead of on a blank New task. The roster name becomes
// the panel's own selection shape: the roster's KIND and the BARE name. A
// name the roster does not list selects nobody — a selection is never
// invented. Loaded on demand by app/agents/page.tsx (next/dynamic).

import { useMemo, useState } from "react";
import type { AgentSource } from "@/components/agents/identity";
import { useApi } from "@/lib/useApi";
import { useModels } from "@/lib/useModels";
import type { AgentsResponse } from "@/lib/types";
import { AgentsPanel } from "@/components/agents/AgentsModal";
import type { DynamicAgentFull } from "@/components/agents/SetupCard";
import type { RosterEntry } from "@/components/agents/RosterStrip";
import type { RemoteAgentInfo } from "@/components/agents/identity";
import { MissionRail, type RailTarget } from "./MissionRail";

type Selection = { kind: AgentSource; name: string };

/** The roster entry a link names → AgentsPanel's `selected`, or null. */
export function selectionFor(roster: RosterEntry[], agent: string | undefined): Selection | null {
  const want = (agent ?? "").trim();
  if (!want) return null;
  const bare = (n: string) => n.replace(/^(custom|remote):/, "");
  const hit = roster.find((e) => e.name === want) ?? roster.find((e) => bare(e.name) === bare(want));
  return hit ? { kind: hit.kind, name: bare(hit.name) } : null;
}

export function TeamScreen({
  onRail,
  agent,
}: {
  onRail: (target: RailTarget) => void;
  /** v1.309.0: the roster name a link asked for (`&agent=`), if any. */
  agent?: string;
}) {
  const { data: agentsData, reload: reloadAgents } = useApi<AgentsResponse>("/agents");
  const { data: remoteData, reload: reloadRemotes } = useApi<{
    agents?: RemoteAgentInfo[];
    remotes?: RemoteAgentInfo[];
  }>("/agents/remote");
  const { data: rosterData, reload: reloadRoster } = useApi<{ roster?: RosterEntry[] }>("/agents/roster");
  const { data: modelsData } = useModels();
  const roster = useMemo(
    () => (rosterData?.roster ?? []).filter((e): e is RosterEntry => Boolean(e) && typeof e.name === "string"),
    [rosterData],
  );
  // A click inside the panel wins over the link; the link's pick holds until then.
  const [picked, setPicked] = useState<Selection | null>(null);
  const selected = picked ?? selectionFor(roster, agent);
  return (
    <div data-testid="team-screen" className="space-y-3">
      {/* v1.329.0: the page's two text tabs over the panel (the bordered
          left rail is gone; its app-wide rows were the sidebar's). */}
      <MissionRail active="agents" onAction={onRail} />
      {/* v1.329.0 (calm chat wave 9, K4): no outer card. The panel sits on
          the page like the New task view beside it; a hairline under the
          tabs and between the list and the agent sets the parts apart. From
          md the panel keeps its window height (the list and the agent scroll
          on their own); on a phone it is one column that scrolls with the
          page. */}
      <section
        data-testid="team-panel"
        className="flex flex-col md:h-[calc(100vh-9rem-var(--ij-strip-h,0px))] md:min-h-[32rem]"
      >
        <AgentsPanel
          roster={roster}
          dynamic={(agentsData?.dynamic ?? []) as DynamicAgentFull[]}
          remotes={remoteData?.agents ?? remoteData?.remotes ?? []}
          models={modelsData?.models ?? []}
          selected={selected}
          onSelect={(kind, name) => setPicked({ kind, name })}
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
