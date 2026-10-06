"use client";

/**
 * Inside a project WORLD (v1.304.0) — "still a round table chat at the
 * forefront, but tabs including board, items pending the user, completed
 * tasks, new tasks for the user to give to the group of this project."
 *
 * LAYOUT (the user's pick: "table first, tabs beside"): a header (project
 * name, a link to the project page, the team's faces + Edit team), then the
 * project's round table filling the main area with the tabs in a right panel.
 * Below `lg` the panel drops under the table — a 24rem column beside a
 * transcript on a small screen would be two unusable columns.
 *
 * THE ROOM IS MADE LAZILY. A world's round table is the project's most recent
 * project-bound thread; a world that has none gets one from
 * POST /projects/{id}/world/room the first time it is opened WITH a team —
 * never for an empty team (a table with nobody at it is not a room), and never
 * twice (a ref guards the in-flight call across polls and re-renders).
 *
 * THE TEAM IS THE PANEL. `PUT /projects/{id}/team` re-seats the room SERVER-
 * side (one writer), so after a save this view only adopts the room id the
 * answer names and reloads the transcript — it never writes the panel itself.
 */

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ArrowLeft, ExternalLink, Users } from "lucide-react";
import { post, put, ApiError } from "@/lib/api";
import { usePolledApi } from "@/lib/useApi";
import { Card, ErrorNote, OfflineHint, SkeletonRows } from "@/components/ui";
import { Modal } from "@/components/Modal";
import { RoundTable } from "@/components/agents/RoundTable";
import type { RosterEntry } from "@/components/agents/RosterStrip";
import type { Participant, ThreadDetail } from "@/components/agents/identity";
import {
  PanelPicker,
  type PickerCatalog,
} from "@/components/agents/PanelPicker";
import { TeamFaces } from "./WorldsGrid";
import { TeamEditor } from "./TeamEditor";
import { WorldTabs, type WorldTab } from "./WorldTabs";
import {
  normaliseCounts,
  roomIdOf,
  teamWireNames,
  type WorldDetail,
  type WorldMember,
} from "@/lib/agentWorlds";

/**
 * Rooms this window has asked the daemon to make, by project — the in-flight
 * call and then its answer. Module state ON PURPOSE: a world can remount
 * (leave and come back) before its poll has caught up with the room it just
 * got, and a component ref dies with the component, so a ref alone would
 * POST a second room. Cleared for a project on failure, so Try again can.
 */
const roomRequests = new Map<string, Promise<string | null>>();

function requestRoom(projectId: string, base: string): Promise<string | null> {
  const pending = roomRequests.get(projectId);
  if (pending) return pending;
  const p = post<unknown>(`${base}/world/room`, {}).then(roomIdOf);
  roomRequests.set(projectId, p);
  p.then(
    (id) => {
      if (!id) roomRequests.delete(projectId);
    },
    () => roomRequests.delete(projectId),
  );
  return p;
}

/** Tests only: forget every room request (module state outlives a render). */
export function __resetRoomRequests(): void {
  roomRequests.clear();
}

function errText(e: unknown): string {
  return e instanceof ApiError ? e.message : String(e);
}

export function WorldView({
  projectId,
  pinnedThreadId,
  roster,
  catalog,
  onBack,
  onRosterChanged,
}: {
  projectId: string;
  /** A specific room of this project to open (a `?thread=` deep link to an
   *  older project room); absent = the project's own room. */
  pinnedThreadId?: string;
  /** The page's GET /agents/roster rows (the table's dispatch choices and the
   *  team picker's catalog). */
  roster: RosterEntry[];
  /** The panel picker's catalog, built once by the page. */
  catalog: PickerCatalog;
  onBack: () => void;
  /** A portrait/face changed from a seat at the table — refetch the roster. */
  onRosterChanged?: () => void;
}) {
  const base = `/projects/${encodeURIComponent(projectId)}`;
  const world = usePolledApi<WorldDetail>(`${base}/world`, 10000);
  const data = world.data;

  // The team as last SAVED here, until the poll catches up with it — so a
  // freshly seated team does not flash the build step back for one tick.
  const [teamOverride, setTeamOverride] = useState<WorldMember[] | null>(null);
  const team: WorldMember[] = teamOverride ?? (Array.isArray(data?.team) ? data!.team : []);

  const [roomId, setRoomId] = useState<string | null>(null);
  const [roomError, setRoomError] = useState<string | null>(null);
  const [roomTry, setRoomTry] = useState(0);
  const creatingRef = useRef(false);
  // The daemon's answer wins once it names a room; the one made here bridges
  // the gap until the poll catches up.
  const threadId = pinnedThreadId || data?.thread_id || roomId || null;

  const [editingTeam, setEditingTeam] = useState(false);
  const [panelEdit, setPanelEdit] = useState<ThreadDetail | null>(null);
  const [nonce, setNonce] = useState(0);
  const [tab, setTab] = useState<WorldTab | null>(null);

  // A different world: nothing carried over.
  useEffect(() => {
    setTeamOverride(null);
    setRoomId(null);
    setRoomError(null);
    setEditingTeam(false);
    creatingRef.current = false;
  }, [projectId]);

  // The poll caught up with the saved team: the daemon's answer is the truth
  // again (so a change made elsewhere later is not hidden behind this one).
  useEffect(() => {
    if (!teamOverride || !data || !Array.isArray(data.team)) return;
    if (JSON.stringify(teamWireNames(data.team)) === JSON.stringify(teamWireNames(teamOverride)))
      setTeamOverride(null);
  }, [data, teamOverride]);

  // LAZY ROOM. Only once the world has answered, has a team, and has no room.
  useEffect(() => {
    if (data?.thread_id) roomRequests.delete(projectId); // the poll caught up
    if (!data || threadId || team.length === 0 || creatingRef.current) return;
    creatingRef.current = true;
    setRoomError(null);
    requestRoom(projectId, base)
      .then((id) => {
        if (id) setRoomId(id);
        else setRoomError("The daemon made no room for this project.");
        world.reload();
      })
      .catch((e) => {
        // The guard STAYS up: every poll hands this effect a new `data`, and
        // a failed create must not be re-posted every 10 s behind the
        // user's back — Try again is the door.
        setRoomError(errText(e));
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, threadId, team.length, roomTry, base, projectId]);

  /** The team was saved. The daemon re-seated the room (the PUT is the one
   *  writer of the panel); adopt the room it names and reload the table. */
  function teamSaved(saved: WorldMember[], savedThreadId: string | null) {
    setTeamOverride(saved);
    setEditingTeam(false);
    if (savedThreadId) setRoomId(savedThreadId);
    setNonce((n) => n + 1);
    world.reload();
  }

  async function savePanel(id: string, participants: Participant[]) {
    await put(`/agents/threads/${encodeURIComponent(id)}/participants`, {
      participants: participants.map(({ source, name, role }) => ({ source, name, role })),
    });
    setPanelEdit(null);
    setNonce((n) => n + 1);
  }

  const name = data?.project?.name || "Project";
  const counts = normaliseCounts(data?.counts);
  const waiting = Array.isArray(data?.waiting) ? data!.waiting : [];
  const completed = Array.isArray(data?.completed) ? data!.completed : [];

  /* -------------------------------------------------- not loaded / gone --- */
  if (!data) {
    const err = world.error;
    return (
      <div data-testid="world-view" className="space-y-4">
        <button type="button" data-testid="world-back" onClick={onBack} className="btn-ghost text-sm">
          <ArrowLeft size={14} /> All worlds
        </button>
        {err && err.status === 404 ? (
          <ErrorNote>
            This project has no world — it may have been archived or removed.
          </ErrorNote>
        ) : err && err.status === 0 ? (
          <OfflineHint />
        ) : err ? (
          <ErrorNote>{err.message || `The world could not be read (HTTP ${err.status}).`}</ErrorNote>
        ) : (
          <Card>
            <SkeletonRows rows={4} />
          </Card>
        )}
      </div>
    );
  }

  /* ---------------------------------------------------------- the main --- */
  const main =
    team.length === 0 ? (
      <TeamEditor
        projectId={projectId}
        projectName={name}
        roster={roster}
        mode="build"
        onSaved={teamSaved}
      />
    ) : threadId ? (
      <RoundTable
        threadId={threadId}
        projectId={projectId}
        projectName={data?.project?.name || undefined}
        onRosterChanged={onRosterChanged}
        reloadNonce={nonce}
        onEditPanel={(detail) => setPanelEdit(detail)}
        onRoundDone={() => world.reload()}
        roster={roster}
        assign={null}
      />
    ) : roomError ? (
      <div className="space-y-2">
        <ErrorNote>{roomError}</ErrorNote>
        <button
          type="button"
          data-testid="world-room-retry"
          className="btn-ghost text-sm"
          onClick={() => {
            creatingRef.current = false;
            setRoomTry((n) => n + 1);
          }}
        >
          Try again
        </button>
      </div>
    ) : (
      <Card>
        <p className="mb-3 text-sm text-zinc-500">Setting the table…</p>
        <SkeletonRows rows={3} />
      </Card>
    );

  return (
    <div data-testid="world-view" data-project={projectId} className="flex flex-col gap-3">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <button
          type="button"
          data-testid="world-back"
          onClick={onBack}
          className="btn-ghost shrink-0 text-sm"
          aria-label="Back to all worlds"
        >
          <ArrowLeft size={14} /> Worlds
        </button>
        <h1 className="min-w-0 truncate text-lg font-semibold tracking-tight text-zinc-100">
          {name}
        </h1>
        <Link
          href={base}
          data-testid="world-project-link"
          className="inline-flex shrink-0 items-center gap-1 text-xs text-accent-soft hover:underline"
        >
          Open project <ExternalLink size={12} aria-hidden />
        </Link>
        <div className="ml-auto flex items-center gap-3">
          {team.length > 0 && <TeamFaces team={team} size={32} max={6} />}
          <button
            type="button"
            data-testid="world-edit-team"
            onClick={() => setEditingTeam(true)}
            className="btn-ghost shrink-0 text-sm"
          >
            <Users size={14} /> Edit team
          </button>
        </div>
      </header>

      <div className="flex flex-col gap-4 lg:h-[calc(100vh-8.5rem)] lg:min-h-[28rem] lg:flex-row">
        <div data-testid="world-main" className="min-w-0 flex-1 lg:min-h-0 lg:overflow-y-auto">
          {main}
        </div>
        <aside
          data-testid="world-side"
          className={`flex min-w-0 shrink-0 flex-col lg:min-h-0 ${
            tab === "board" ? "lg:w-[36rem]" : "lg:w-[24rem]"
          }`}
        >
          <WorldTabs
            projectId={projectId}
            waiting={waiting}
            waitingCount={Math.max(counts.waiting, waiting.length)}
            completed={completed}
            team={team}
            onTabChange={setTab}
          />
        </aside>
      </div>

      {editingTeam && (
        <Modal label="Edit the team" onClose={() => setEditingTeam(false)} testId="world-team-modal">
          <TeamEditor
            projectId={projectId}
            projectName={name}
            roster={roster}
            mode="edit"
            onSaved={teamSaved}
            onCancel={() => setEditingTeam(false)}
          />
        </Modal>
      )}
      {panelEdit && (
        <PanelPicker
          mode="edit"
          catalog={catalog}
          initialParticipants={panelEdit.participants}
          onClose={() => setPanelEdit(null)}
          onSubmit={(_title, participants) => savePanel(panelEdit.id, participants)}
        />
      )}
    </div>
  );
}

export default WorldView;
