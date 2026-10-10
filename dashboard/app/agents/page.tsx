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
//
// v1.309.0: Your team and the old-room transcript load ON DEMAND
// (next/dynamic), and MissionScreen defers its result panel the same way.
// The front door is New task, which needs none of them. What that moves off
// the /agents first load is whatever the app-build-manifest A/B says, not
// what this comment hopes: a module that something else on the route still
// imports statically moves zero bytes (CLAUDE.md). Both are prefetched once
// the page is idle — the Overlays.tsx pattern — so a press on the rail's
// Agents row still lands on a module already in memory.
// `?view=team&agent=<roster name>` opens Your team on that agent.
//
// THE ROUTE FOLLOWS NEXT'S SEARCH PARAMS, not only mount + popstate (v1.309.0
// review). Next keeps this page MOUNTED when only the query changes (its
// layout router keys a segment without the search params) and a Link push
// dispatches no popstate — so a bell or palette link to `?view=team&agent=…`
// or `?mission=<id>`, pressed while the user is already on /agents, changed
// the URL and left the screen as it was. `useSearchParams` re-renders on
// exactly that change; it needs a Suspense boundary under static export.
// Until the URL is read the page shows the neutral skeleton, never the New
// task composer: a deep link would flash the wrong screen and fire its
// fetches for nothing.

import dynamic from "next/dynamic";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { PageShell } from "@/components/motion";
import { MissionScreen } from "@/components/agents/mission/MissionScreen";
import type { RailTarget } from "@/components/agents/mission/MissionRail";
import {
  agentsPath,
  guideChatPath,
  missionPath,
  parseAgentsRoute,
  type AgentsRoute,
} from "@/lib/mission";

/** What a deferred screen shows for the moment its module is loading.
 *  v1.330.0 (calm chat wave 10): no card. The screens it stands in for sit
 *  on the page without a box, so the wait does too: one quiet line a screen
 *  reader hears (role=status) and three faint lines under a hairline. It
 *  keeps the screen's full height, so nothing below jumps when the real
 *  screen arrives. */
function ScreenLoading() {
  return (
    <div
      data-testid="agents-screen-loading"
      role="status"
      aria-busy="true"
      aria-live="polite"
      className="h-[calc(100vh-7rem-var(--ij-strip-h,0px))] min-h-[32rem] px-1 pt-1"
    >
      <p className="flex items-center gap-2 text-[13px] text-zinc-500">
        <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-zinc-500 motion-safe:animate-pulse" />
        Loading…
      </p>
      <div aria-hidden="true" className="mt-4 space-y-3 border-t border-white/[0.06] pt-5 motion-safe:animate-pulse">
        <div className="h-2 w-2/3 max-w-[28rem] rounded-full bg-white/[0.05]" />
        <div className="h-2 w-1/2 max-w-[20rem] rounded-full bg-white/[0.05]" />
        <div className="h-2 w-3/5 max-w-[24rem] rounded-full bg-white/[0.05]" />
      </div>
    </div>
  );
}

const loadTeamScreen = () => import("@/components/agents/mission/TeamScreen");
const loadRoomTranscript = () => import("@/components/agents/mission/RoomTranscript");

const TeamScreen = dynamic(() => loadTeamScreen().then((m) => ({ default: m.TeamScreen })), {
  ssr: false,
  loading: ScreenLoading,
});
const RoomTranscript = dynamic(() => loadRoomTranscript().then((m) => ({ default: m.RoomTranscript })), {
  ssr: false,
  loading: ScreenLoading,
});

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
    <p data-testid="guide-handoff" className="px-2 py-6 text-[13px] text-zinc-400">
      The Guide answers in chat now.{" "}
      <a href={href} className="text-accent hover:underline">
        Open the question in chat
      </a>
      .
    </p>
  );
}

/** Same screen, same props: keep the old object so nothing re-renders. */
function sameRoute(a: AgentsRoute | null, b: AgentsRoute): boolean {
  return a !== null && JSON.stringify(a) === JSON.stringify(b);
}

export default function AgentsPage() {
  return (
    <Suspense fallback={<ScreenLoading />}>
      <AgentsRouter />
    </Suspense>
  );
}

function AgentsRouter() {
  const [route, setRoute] = useState<AgentsRoute | null>(null);
  // Next's own record of the query: it changes on a Link push to this page
  // (no remount, no popstate). `null` outside an app router (unit tests).
  const searchKey = useSearchParams()?.toString() ?? "";
  useEffect(() => {
    const read = () => {
      const next = parseAgentsRoute(window.location.search);
      setRoute((prev) => (sameRoute(prev, next) ? prev : next));
    };
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, [searchKey]);
  // Pull the deferred screens in once the page is quiet (never at import).
  useEffect(() => {
    const prefetch = () => {
      void loadTeamScreen().catch(() => {});
      void loadRoomTranscript().catch(() => {});
    };
    const w = window as unknown as {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
      cancelIdleCallback?: (id: number) => void;
    };
    if (typeof w.requestIdleCallback === "function") {
      const id = w.requestIdleCallback(prefetch, { timeout: 4000 });
      return () => w.cancelIdleCallback?.(id);
    }
    const t = window.setTimeout(prefetch, 2000);
    return () => window.clearTimeout(t);
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

  if (route === null) return <ScreenLoading />;
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
        <TeamScreen key={route.agent || "team"} onRail={onRail} agent={route.agent} />
      ) : route.kind === "room" ? (
        <RoomTranscript thread={route.thread} onRail={onRail} />
      ) : (
        <GuideHandoff ask={route.ask} />
      )}
    </PageShell>
  );
}
