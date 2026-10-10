"use client";

// THE MISSION SCREEN (v1.307.0; v1.308.0 the ONLY way team work is given) —
// the Agents page's front door, and each project's own screen.
//
// "One AI interface with a visible workforce behind it, rather than six
// separate AI chats." The user gives Jarvis ONE objective; Jarvis (a
// supervisor run, `POST /missions`) splits it and hands the parts to the team.
//
//   New task · Your team                       (quiet text tabs, v1.329.0)
//   ┌──────────── objective + live result ────────────┐ │ Team
//   │ Report | Markdown | Preview                      │ │ card
//   │                                                  │ │ card
//   └──────────────────────────────────────────────────┘ │
//   Live activity ─────────────────────────────────────────────────
//
// v1.329.0 (calm chat wave 8, J4): the front door is the chat's calm shape.
// The objective composer is the page's ONE card, centred; "Your projects",
// "Recent objectives" and "Team" are plain sections with quiet sentence-case
// labels and hairlines. The old bordered left rail repeated the sidebar's
// navigation and is now two text tabs (MissionRail.tsx).
//
// IN A PROJECT (`projectId`, v1.308.0 — what the project "world" with its
// round table used to be): the objective runs in the project's context and
// folder and only the project's TEAM takes the work (the daemon enforces it);
// the right column shows that team with Edit team; under the composer sit the
// project's Board / Waiting on you / Completed.
//
// Everything is read from the daemon (`/sessions/{id}/mission` + the
// coordinator's stream, `/projects/{id}/world`) — the page invents no progress
// and no lines of its own.
//
// v1.309.0: this screen does NOT read the live text. `useMission` keeps it in
// a store and only MissionOutput subscribes, so a token flush redraws the
// centre and leaves the rail, the cards and the activity log alone.
//
// v1.309.0 review (agents-route-eager-bundle): MissionOutput — and with it
// the markdown renderer and the document viewer — loads ON DEMAND. The front
// door (New task) never shows a result, and a module something else still
// imports statically moves zero bytes, so it must be deferred HERE, its only
// importer (the room transcript, the other markdown user, is deferred in
// app/agents/page.tsx). It is prefetched once the front door is idle, so
// opening a recent objective lands on a module already in memory.

import dynamic from "next/dynamic";
import { useEffect, useMemo } from "react";
import { ArrowLeft } from "lucide-react";
import { composerChipClass } from "@/lib/composerChips";
import AgentFace from "@/components/agents/AgentFace";
import { useApi, usePolledApi } from "@/lib/useApi";
import { useMission } from "@/lib/useMission";
import { MISSION_TERMINAL, statusWord } from "@/lib/mission";
import type { WorldDetail } from "@/lib/agentWorlds";
import { AgentCards, ProgressBar } from "./AgentCards";
import { LiveActivity } from "./LiveActivity";
import { MissionComposer } from "./MissionComposer";
import { MissionRail, type RailTarget } from "./MissionRail";
import { ProjectTeamPanel } from "./ProjectTeamPanel";
import { ProjectTeams } from "./ProjectTeams";
import { ProjectWork } from "./ProjectWork";

const loadMissionOutput = () => import("./MissionOutput");

const MissionOutput = dynamic(() => loadMissionOutput().then((m) => ({ default: m.MissionOutput })), {
  ssr: false,
  loading: () => (
    <section
      data-testid="mission-output-loading"
      aria-busy="true"
      className="card-surface min-h-[28rem] animate-pulse p-0"
    />
  ),
});

interface RosterRow {
  name: string;
  kind?: string;
  description?: string;
  delegable?: boolean;
  healthy?: boolean;
}

/** v1.315.0: the platform's own housekeepers — real teammates Jarvis can
 *  call on, but not what a busy professional came here to hand work to.
 *  Sorted LAST (never hidden, never behind an extra press). */
const BEHIND_THE_SCENES = new Set(["memory", "maintainer"]);

/** The team Jarvis can call on — shown on the front door with no mission. */
function AvailableTeam() {
  const { data } = useApi<{ roster?: RosterRow[] }>("/agents/roster");
  const rows = useMemo(() => {
    const ok = (data?.roster ?? []).filter(
      (r): r is RosterRow => Boolean(r) && typeof r.name === "string" && r.delegable !== false && r.healthy !== false,
    );
    // Stable: roster order within each group.
    return [
      ...ok.filter((r) => !BEHIND_THE_SCENES.has(r.name)),
      ...ok.filter((r) => BEHIND_THE_SCENES.has(r.name)),
    ];
  }, [data]);
  return (
    <div data-testid="mission-available-team" className="space-y-2.5">
      {/* v1.315.0 (UX wave 3, "one AI identity"): the panel opens on JARVIS —
          the one assistant the user talks to — the same face + name the
          mission view's coordinator block shows. A header, not a roster row:
          the coordinator takes no delegated work, so it is never offered as
          a teammate (and the internal word "supervisor" never appears). */}
      <div data-testid="mission-available-jarvis" className="flex items-start gap-2.5 px-1">
        <AgentFace name="jarvis" size={28} title="" />
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-zinc-100">Jarvis</div>
          <p className="text-[12px] leading-snug text-zinc-400">
            Picks from these teammates. You don&apos;t have to choose. Just say what you need.
          </p>
        </div>
      </div>
      <ul className="space-y-1.5 border-l hairline pl-3 ml-4">
        {/* v1.315.0: no preview cap — the old `slice(0, 12)` would push the
            housekeepers (sorted last above) off the panel once 11+ custom
            teammates exist, contradicting "never hidden". Every delegable,
            healthy teammate is listed. */}
        {rows.map((r) => {
          const label = r.name.includes(":") ? r.name.split(":").slice(1).join(":") : r.name;
          return (
            <li key={r.name} className="flex items-center gap-2.5">
              <AgentFace name={r.name} size={24} title="" />
              <div className="min-w-0">
                {/* v1.316.0: a "custom:"/"remote:" name reads as typed; only a
                    built-in id is title-cased by CSS. */}
                <div
                  className={`truncate text-[13px] font-medium text-zinc-200 ${
                    r.name.includes(":") ? "" : "capitalize"
                  }`}
                >
                  {label}
                </div>
                {/* v1.315.0: two lines, never cut off mid-word. */}
                {r.description && <div className="line-clamp-2 text-[11px] leading-snug text-zinc-500">{r.description}</div>}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function MissionScreen({
  missionId,
  projectId = "",
  onOpen,
  onNew,
  onTeam,
  onProject,
}: {
  /** The coordinator session on screen; "" = the New task composer. */
  missionId: string;
  /** v1.308.0: a project's mission screen ("" = the front door). */
  projectId?: string;
  onOpen: (id: string, projectId: string) => void;
  onNew: () => void;
  onTeam: () => void;
  /** Open a project's mission screen ("" = the front door). */
  onProject: (projectId: string) => void;
}) {
  const m = useMission(missionId || null);
  const view = m.view;
  const running = view !== null && !MISSION_TERMINAL.has(view.session.status);
  const world = usePolledApi<WorldDetail>(
    projectId ? `/projects/${encodeURIComponent(projectId)}/world` : null,
    10000,
  );
  const projectName = world.data?.project?.name || "";
  const team = Array.isArray(world.data?.team) ? world.data!.team : [];
  const waiting = Array.isArray(world.data?.waiting) ? world.data!.waiting : [];
  const completed = Array.isArray(world.data?.completed) ? world.data!.completed : [];

  const onRail = (target: RailTarget) => (target === "new" ? onNew() : onTeam());

  // The result panel's module, pulled in once the front door is quiet (never
  // at import) — the page's Overlays.tsx pattern.
  useEffect(() => {
    if (missionId) return;
    const prefetch = () => void loadMissionOutput().catch(() => {});
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
  }, [missionId]);

  return (
    <div data-testid="mission-screen" data-project={projectId || undefined} className="space-y-4">
      {/* v1.329.0 (calm chat wave 8, J4): the page's own two places as quiet
          text tabs over the content. The app-wide rows the old bordered rail
          repeated (Chat, Projects, Tools, Files, Settings) are the sidebar's. */}
      <MissionRail active={missionId || projectId ? null : "new"} onAction={onRail} />

      {projectId && (
        <div data-testid="mission-project-header" className="flex flex-wrap items-center gap-3">
          <button
            type="button"
            data-testid="mission-back"
            onClick={() => (missionId ? onProject(projectId) : onProject(""))}
            className={composerChipClass()}
          >
            <ArrowLeft size={12} /> {missionId ? "This project" : "All objectives"}
          </button>
          <h1 className="text-[15px] font-semibold text-zinc-100">
            {projectName || (world.error ? "Project" : "…")}
          </h1>
          <span className="text-[12px] text-zinc-500">project team</span>
        </div>
      )}

      <div
        data-testid="mission-columns"
        className="grid grid-cols-1 gap-x-8 gap-y-6 lg:grid-cols-[minmax(0,1fr)_19rem] lg:items-start"
      >
        {/* The front door (and a project's) centres its one card like a new
            chat; an objective's result uses the whole column. */}
        <div
          className={`min-w-0 space-y-3 ${missionId ? "" : "mx-auto w-full max-w-[760px] lg:pt-[5vh]"}`}
        >
          {projectId && world.error && world.error.status === 404 ? (
            <p data-testid="mission-project-missing" className="px-2 py-6 text-[13px] text-zinc-400">
              This project no longer exists.{" "}
              <button type="button" className="text-accent hover:underline" onClick={() => onProject("")}>
                Back to all objectives
              </button>
            </p>
          ) : !missionId ? (
            <div className="space-y-8">
              <MissionComposer
                onStarted={onOpen}
                onOpen={onOpen}
                fixedProject={projectId}
                projectName={projectName}
              />
              {projectId ? (
                <ProjectWork key={projectId} projectId={projectId} waiting={waiting} completed={completed} />
              ) : (
                <ProjectTeams onOpen={onProject} />
              )}
            </div>
          ) : m.missing ? (
            <p data-testid="mission-missing" className="px-2 py-6 text-[13px] text-zinc-400">
              This objective no longer exists.{" "}
              <button type="button" className="text-accent hover:underline" onClick={onNew}>
                Start a new one
              </button>
            </p>
          ) : (
            <MissionOutput view={view} liveStore={m.liveStore} objective="" onChanged={m.reload} onOpen={onOpen} />
          )}
          {m.error && missionId && (
            <p className="text-[12px] text-tone-warn">
              Can&apos;t reach the daemon right now. Showing the last known state.
            </p>
          )}
        </div>

        {/* v1.329.0: a plain section beside the work, set off by a hairline
            (above it on a phone, to its left from lg), never a card. */}
        <aside
          data-testid="mission-team"
          className="min-w-0 border-t hairline pt-4 lg:sticky lg:top-3 lg:border-l lg:border-t-0 lg:pl-6 lg:pt-1"
        >
          <header className="flex items-center justify-between px-1 pb-2">
            <h2 className="text-[13px] font-medium text-zinc-400">Team</h2>
            {view && view.progress.total > 0 && (
              <span data-testid="mission-team-count" className="text-[11px] text-zinc-400">
                {view.progress.done} of {view.progress.total} done
              </span>
            )}
          </header>
          <div className="space-y-3">
            {!missionId ? (
              projectId ? (
                <ProjectTeamPanel
                  projectId={projectId}
                  projectName={projectName}
                  team={team}
                  onSaved={world.reload}
                />
              ) : (
                <AvailableTeam />
              )
            ) : view ? (
              <>
                <div data-testid="mission-coordinator" className="flex items-center gap-2.5 px-1">
                  <AgentFace name="jarvis" size={28} mood={running ? "work" : "done"} title="" />
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-semibold text-zinc-100">{view.coordinator.name}</div>
                    <div className="text-[11px] text-zinc-400">
                      {running ? "Coordinating the team" : statusWord(view.coordinator.status)}
                    </div>
                  </div>
                </div>
                {view.deliverable.worklist && (
                  <div className="px-1">
                    <div className="mb-1 text-[11px] text-zinc-400">
                      {view.deliverable.worklist.done} of {view.deliverable.worklist.total} items done
                    </div>
                    <ProgressBar
                      status={running ? "working" : "done"}
                      progress={{
                        pct: Math.floor(
                          ((view.deliverable.worklist.done + view.deliverable.worklist.failed) * 100) /
                            Math.max(1, view.deliverable.worklist.total),
                        ),
                        label: "items",
                        basis: "worklist",
                      }}
                    />
                  </div>
                )}
                <AgentCards members={view.members} onChanged={m.reload} />
              </>
            ) : (
              <p className="px-1 text-[12px] text-zinc-500">Loading the team…</p>
            )}
          </div>
        </aside>

        {missionId && !m.missing && (
          <div className="min-w-0 lg:col-span-2">
            <LiveActivity lines={view?.activity ?? []} running={running || (m.loading && !view)} />
          </div>
        )}
      </div>
    </div>
  );
}
