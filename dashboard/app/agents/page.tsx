"use client";

// AGENTS (v1.308.0) — one objective, Jarvis runs the team.
//
// The user, after v1.307.0's mission screen: "the roundtable view I saw
// wasn't very user friendly and I was thinking this new method would be
// preferred from the user perspective" — and chose to replace the round table
// everywhere. So this page is now a small router over four screens (the URL is
// the state; read off `window.location`, kept in step with back/forward):
//
//   mission  `/agents`, `?mission=<id>` — New task and one objective's live
//            result, team cards and activity (components/agents/mission/).
//            `?project=<id>[&mission=<id>]` is a PROJECT's mission screen:
//            objectives in the project's context, worked only by its team.
//   team     `?view=team` (and the old `?world=general`) — "Your team": every
//            agent, portraits / faces / inbox / folder / coach, create and
//            connect (the same panel the agents dialog always showed).
//   room     `?thread=<id>` — an old round-table (or chat @-mention panel)
//            conversation, READ-ONLY. Every room link still resolves.
//   guide    `?talk=…&ask=…` — the old "Ask the Guide" link: the question is
//            handed to CHAT as an @guide mention (prefilled, never sent).
//
// What went away with the round table, and where its jobs went: talking to
// one agent → chat's @-mentions (unchanged); giving one agent work → a New
// task (Jarvis picks), or that agent's inbox on Your team; a project's world →
// the project's mission screen (team, Board / Waiting on you / Completed).

import { useCallback, useEffect, useState } from "react";
import { PageShell } from "@/components/motion";
import { MissionScreen } from "@/components/agents/mission/MissionScreen";
import { TeamScreen } from "@/components/agents/mission/TeamScreen";
import { RoomTranscript } from "@/components/agents/mission/RoomTranscript";
import type { RailTarget } from "@/components/agents/mission/MissionRail";
import {
  agentsPath,
  guideChatPath,
  missionPath,
  parseAgentsRoute,
  type AgentsRoute,
} from "@/lib/mission";

function GuideHandoff({ ask }: { ask: string }) {
  const href = guideChatPath(ask);
  useEffect(() => {
    try {
      window.location.replace(href);
    } catch {
      /* the link below still works */
    }
  }, [href]);
  return (
    <p data-testid="guide-handoff" className="card-surface px-5 py-6 text-[13px] text-zinc-400">
      The Guide answers in chat now —{" "}
      <a href={href} className="text-accent hover:underline">
        open the question in chat
      </a>
      .
    </p>
  );
}

export default function AgentsPage() {
  const [route, setRoute] = useState<AgentsRoute | null>(null);
  useEffect(() => {
    const read = () => setRoute(parseAgentsRoute(window.location.search));
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);
  const go = useCallback((path: string) => {
    try {
      window.history.pushState(null, "", path);
    } catch {
      /* no history API — the screen still changes */
    }
    setRoute(parseAgentsRoute(path.includes("?") ? path.slice(path.indexOf("?")) : ""));
  }, []);
  const onRail = useCallback(
    (target: RailTarget) => go(target === "new" ? missionPath() : agentsPath({ kind: "team" })),
    [go],
  );

  if (route === null) return null;
  return (
    <PageShell className="space-y-0">
      {route.kind === "mission" ? (
        <MissionScreen
          key={route.project || "front"}
          missionId={route.id}
          projectId={route.project}
          onOpen={(id, project) => go(missionPath(id, project))}
          onNew={() => go(missionPath())}
          onTeam={() => go(agentsPath({ kind: "team" }))}
          onProject={(project) => go(missionPath("", project))}
        />
      ) : route.kind === "team" ? (
        <TeamScreen onRail={onRail} />
      ) : route.kind === "room" ? (
        <RoomTranscript thread={route.thread} onRail={onRail} />
      ) : (
        <GuideHandoff ask={route.ask} />
      )}
    </PageShell>
  );
}
