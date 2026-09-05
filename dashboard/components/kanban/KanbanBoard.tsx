"use client";

import { useMemo, useState } from "react";
import {
  DndContext,
  DragOverlay,
  PointerSensor,
  KeyboardSensor,
  useSensor,
  useSensors,
  type DragStartEvent,
  type DragEndEvent,
} from "@dnd-kit/core";
import { post, ApiError } from "@/lib/api";
import { usePolledApi } from "@/lib/useApi";
import { ConfirmButton } from "@/components/ui";
import type { Review, SessionView } from "@/lib/types";
import {
  LANES,
  dropAction,
  laneFor,
  type LaneDef,
  type LaneId,
} from "@/lib/kanban";
import { KanbanColumn } from "./KanbanColumn";
import {
  CardInner,
  KanbanActionsContext,
  KanbanTeamContext,
  type KanbanCardActions,
  type KanbanTeamState,
  type TeamCardInfo,
} from "./SessionCard";

/* ---- Queued lane (v1.166.0, B6) -----------------------------------------
 * `spawn_managed` parks sessions past the `max_concurrent_sessions` cap with
 * status "queued" — a state laneFor predates, so without this the board files
 * a queued session under Active and claims "Running now" about work that has
 * not started. lib/kanban.ts is coordinator-owned shared glue, so the queued
 * extension lives here, board-local. The column only appears when occupied:
 * with an empty queue (the limit-0 default) the board is exactly today's. */

export type BoardLaneId = LaneId | "queued";

export const QUEUED_LANE: LaneDef = {
  // The cast is deliberate: LaneId is frozen in shared glue; the queued id
  // only ever flows into droppable ids and record keys, both plain strings.
  id: "queued" as LaneId,
  title: "Queued",
  tone: "violet",
  hint: "Waiting for a free slot",
};

/** laneFor, plus the queued state. Review precedence is preserved. */
export function boardLaneFor(session: SessionView, hasReview: boolean): BoardLaneId {
  if (!hasReview && session.status.toLowerCase() === "queued") return "queued";
  return laneFor(session, hasReview);
}

export function assignBoardLanes(
  sessions: SessionView[],
  reviews: Record<string, Review>,
): Record<BoardLaneId, SessionView[]> {
  const out: Record<BoardLaneId, SessionView[]> = {
    queued: [],
    active: [],
    review: [],
    completed: [],
    failed: [],
  };
  for (const s of sessions) out[boardLaneFor(s, !!reviews[s.id])].push(s);
  return out;
}

/** Queued column only when occupied — an empty board keeps the familiar 4. */
export function visibleLanes(lanes: Record<BoardLaneId, SessionView[]>): LaneDef[] {
  return lanes.queued.length > 0 ? [QUEUED_LANE, ...LANES] : LANES;
}

/** Narrow a board lane for the shared components (drag semantics only — a drag
 *  out of Queued triggers no action, so Active's behaviour is the right stand-in). */
function asLaneId(lane: BoardLaneId): LaneId {
  return lane === "queued" ? "active" : lane;
}

/* ---- Team nesting (v1.168.0, P5) ----------------------------------------
 * `GET /sessions/teams` maps child session -> parent session (derived from
 * AgentRun.parent_id — the honest record). The board lays each child out
 * DIRECTLY UNDER its parent's card, in the parent's lane, indented; a parent
 * card grows a "Team of N" badge that collapses/expands its members. A child
 * whose parent is not on the board (filtered out, deleted, different project
 * scope) renders exactly as before — flat, in its own lane. */

const LANE_ORDER: BoardLaneId[] = ["queued", "active", "review", "completed", "failed"];

export interface TeamRow {
  session: SessionView;
  /** 0 = root; >0 = nested this many levels under the preceding parent. */
  depth: number;
}

export interface TeamLayout {
  rows: Record<BoardLaneId, TeamRow[]>;
  /** session id -> descendants ON THE BOARD under it (the "Team of N" count). */
  counts: Map<string, number>;
}

/** Visual cap only — a corrupt 50-deep chain must not push cards off-screen. */
const MAX_TEAM_DEPTH = 3;

/**
 * Pure lane→rows layout. Children move into their parent's lane, right after
 * the parent, depth-first, siblings in created_at order; collapsed parents
 * keep their descendants off the board. Corrupt data degrades flat, never
 * silently drops a session: self-links are ignored and a parent cycle
 * (a→b→a — both "children", so neither would ever be emitted) falls through
 * to the second pass, which renders survivors flat in their own lane.
 */
export function layoutTeams(
  lanes: Record<BoardLaneId, SessionView[]>,
  parents: Record<string, string>,
  collapsed: ReadonlySet<string>,
): TeamLayout {
  const present = new Map<string, SessionView>();
  for (const laneId of LANE_ORDER) {
    for (const s of lanes[laneId]) present.set(s.id, s);
  }

  const childrenOf = new Map<string, SessionView[]>();
  const isChild = new Set<string>();
  for (const [cid, pid] of Object.entries(parents)) {
    if (cid === pid) continue; // self-link: corrupt, ignore
    const child = present.get(cid);
    if (!child || !present.has(pid)) continue; // parent off the board → flat
    const arr = childrenOf.get(pid);
    if (arr) arr.push(child);
    else childrenOf.set(pid, [child]);
    isChild.add(cid);
  }
  for (const arr of childrenOf.values()) {
    arr.sort(
      (a, b) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
  }

  const counts = new Map<string, number>();
  function countUnder(id: string, seen: Set<string>): number {
    let n = 0;
    for (const c of childrenOf.get(id) ?? []) {
      if (seen.has(c.id)) continue; // cycle guard
      seen.add(c.id);
      n += 1 + countUnder(c.id, seen);
    }
    return n;
  }
  for (const pid of childrenOf.keys()) {
    counts.set(pid, countUnder(pid, new Set([pid])));
  }

  const rows: Record<BoardLaneId, TeamRow[]> = {
    queued: [],
    active: [],
    review: [],
    completed: [],
    failed: [],
  };
  const emitted = new Set<string>();
  function emit(laneId: BoardLaneId, s: SessionView, depth: number) {
    if (emitted.has(s.id)) return; // cycle guard — a card renders exactly once
    emitted.add(s.id);
    rows[laneId].push({ session: s, depth });
    if (collapsed.has(s.id)) return;
    for (const c of childrenOf.get(s.id) ?? []) {
      emit(laneId, c, Math.min(depth + 1, MAX_TEAM_DEPTH));
    }
  }
  for (const laneId of LANE_ORDER) {
    for (const s of lanes[laneId]) {
      if (!isChild.has(s.id)) emit(laneId, s, 0);
    }
  }
  // Second pass: anything still unplaced (parent cycles) renders flat in its
  // OWN lane — unless a collapsed ancestor legitimately hides it.
  for (const laneId of LANE_ORDER) {
    for (const s of lanes[laneId]) {
      if (emitted.has(s.id)) continue;
      if (hiddenByCollapse(s.id, parents, present, collapsed)) continue;
      emit(laneId, s, 0);
    }
  }
  return { rows, counts };
}

/** Is some ancestor of `id` on the board AND collapsed? (Walk is cycle-safe.) */
function hiddenByCollapse(
  id: string,
  parents: Record<string, string>,
  present: ReadonlyMap<string, SessionView>,
  collapsed: ReadonlySet<string>,
): boolean {
  const seen = new Set<string>([id]);
  let cur = parents[id];
  while (cur && present.has(cur) && !seen.has(cur)) {
    if (collapsed.has(cur)) return true;
    seen.add(cur);
    cur = parents[cur];
  }
  return false;
}

export function KanbanBoard({
  sessions,
  reviews,
  reload,
  projectId,
}: {
  sessions: SessionView[];
  reviews: Record<string, Review>;
  reload: () => void;
  /**
   * When set, the board is scoped to ONE project: only that project's sessions
   * are laned/dragged/cleared. The standalone /kanban page passes nothing and
   * sees every session — unchanged behaviour.
   */
  projectId?: string;
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  // Project scoping is a pure client-side filter over the incoming sessions, so
  // every downstream memo (lanes, byId) derives from the scoped set.
  const scoped = useMemo(
    () =>
      projectId ? sessions.filter((s) => s.project_id === projectId) : sessions,
    [sessions, projectId],
  );

  const lanes = useMemo(() => assignBoardLanes(scoped, reviews), [scoped, reviews]);
  const byId = useMemo(() => {
    const m = new Map<string, SessionView>();
    for (const s of scoped) m.set(s.id, s);
    return m;
  }, [scoped]);

  // Team nesting (v1.168.0): one cheap board-wide map, polled on the same
  // rhythm as the session list's slower cousins. Until it arrives (or if the
  // endpoint errors) `parents` is empty and the board is exactly the flat
  // pre-v1.168.0 board — honest degradation, no layout flicker.
  const { data: teamsData } = usePolledApi<{ parents: Record<string, string> }>(
    "/sessions/teams",
    8000,
  );
  const parents = useMemo(() => teamsData?.parents ?? {}, [teamsData]);
  const [collapsedTeams, setCollapsedTeams] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const layout = useMemo(
    () => layoutTeams(lanes, parents, collapsedTeams),
    [lanes, parents, collapsedTeams],
  );
  const teamState = useMemo<KanbanTeamState>(() => {
    const info = new Map<string, TeamCardInfo>();
    for (const laneId of LANE_ORDER) {
      for (const row of layout.rows[laneId]) {
        info.set(row.session.id, {
          depth: row.depth,
          childCount: layout.counts.get(row.session.id) ?? 0,
          collapsed: collapsedTeams.has(row.session.id),
          // The card's OWN lane — a nested child renders in the parent's
          // column, but its affordances (Approve/Reject, Retry/Dismiss, drag
          // payload) must follow its own status/review, or a nested review
          // child arms "Drop to approve" that no-ops on drop.
          trueLane: asLaneId(boardLaneFor(row.session, !!reviews[row.session.id])),
        });
      }
    }
    return {
      info,
      toggle: (id: string) =>
        setCollapsedTeams((prev) => {
          const next = new Set(prev);
          if (next.has(id)) next.delete(id);
          else next.add(id);
          return next;
        }),
    };
  }, [layout, collapsedTeams, reviews]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor),
  );

  const activeSession = activeId ? byId.get(activeId) ?? null : null;
  const draggingFrom: BoardLaneId | null = activeSession
    ? boardLaneFor(activeSession, !!reviews[activeSession.id])
    : null;
  // Columns render the TEAM layout (children live in their parent's lane), so
  // lane visibility must follow the same rows — otherwise a queued child
  // nested under an active parent would summon an empty Queued column.
  const laneSessions = useMemo(() => {
    const out = {} as Record<BoardLaneId, SessionView[]>;
    for (const laneId of LANE_ORDER) {
      out[laneId] = layout.rows[laneId].map((r) => r.session);
    }
    return out;
  }, [layout]);
  const columns = visibleLanes(laneSessions);

  // Card-level actions (failed-lane retry/dismiss, review-lane add-context) reach
  // the cards via context — KanbanColumn sits between us and them, prop-frozen.
  const cardActions = useMemo<KanbanCardActions>(
    () => ({
      reload,
      notify: (kind, text) => setToast({ kind, text }),
    }),
    [reload],
  );

  async function clearLane(lane: "completed" | "failed") {
    setToast(null);
    // The Failed lane holds both failed AND cancelled sessions (see laneFor).
    const statuses = lane === "completed" ? ["completed"] : ["failed", "cancelled"];
    try {
      const res = await post<{ cleared: number }>("/sessions/clear", { statuses });
      setToast({
        kind: "ok",
        text: `Cleared ${res.cleared} session${res.cleared === 1 ? "" : "s"}.`,
      });
      reload();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      setToast({ kind: "err", text: `Could not clear ${lane}: ${msg}` });
    }
  }

  async function act(kind: "approve" | "reject", id: string) {
    setBusyId(id);
    setToast(null);
    try {
      // Approve returns { merged: <result string> } — surface the REAL outcome
      // (a merge can be non-clean) instead of always claiming "merged".
      const res = await post<{ merged?: string }>(`/reviews/${id}/${kind}`);
      setToast({
        kind: "ok",
        text:
          kind === "approve"
            ? `Approved — ${res?.merged || "merged"}.`
            : "Review rejected — card moved to Failed.",
      });
      reload();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : String(err);
      setToast({ kind: "err", text: `Could not ${kind}: ${msg}` });
    } finally {
      setBusyId(null);
    }
  }

  function onDragStart(e: DragStartEvent) {
    setActiveId(String(e.active.id));
  }

  function onDragEnd(e: DragEndEvent) {
    const id = String(e.active.id);
    setActiveId(null);
    if (!e.over) return;
    const from = (e.active.data.current?.lane as LaneId) ?? null;
    const to = e.over.id as LaneId;
    if (!from) return;
    // A PAUSED run sits in the review lane (laneFor, v1.227.0) but has no
    // review record — a drop onto Completed/Failed would POST
    // /reviews/{id}/approve and 404. Its answer lives on the card and the
    // session page; the drop is purely visual.
    if (byId.get(id)?.waiting_on?.approval_id) return;
    const action = dropAction(from, to);
    if (action) act(action, id);
    // Any other drop is purely visual — server state is the source of truth,
    // so the card simply settles back into its lane on the next render.
  }

  return (
    <KanbanActionsContext.Provider value={cardActions}>
    <KanbanTeamContext.Provider value={teamState}>
    <div className="space-y-3">
      {toast && (
        <div
          className={`rounded-xl border px-3 py-2 text-sm ${
            toast.kind === "ok"
              ? "border-emerald-500/25 bg-emerald-500/[0.07] text-emerald-200"
              : "border-rose-500/25 bg-rose-500/[0.07] text-rose-200"
          }`}
        >
          {toast.text}
        </div>
      )}

      {/* Board toolbar — the lane headers live inside KanbanColumn, so the
          clear affordances sit here, right-aligned above Completed/Failed.
          POST /sessions/clear is status-wide (not project-scoped), so the
          bulk-clear buttons only appear on the unscoped standalone board — an
          embedded per-project board must never over-clear other projects.
          Counts derive from laneSessions — the SAME rows the columns render —
          so the toolbar can never contradict the column right under it (a
          failed child nested under an active parent lives in the Active
          column; "Clear failed (1)" over an empty Failed column was the
          v1.168.0 review finding). The clear itself stays status-wide and the
          toast reports the REAL cleared count. */}
      {!projectId &&
        (laneSessions.completed.length > 0 || laneSessions.failed.length > 0) && (
        <div className="flex flex-wrap items-center justify-end gap-2">
          {laneSessions.completed.length > 0 && (
            <ConfirmButton
              label={`Clear completed (${laneSessions.completed.length})`}
              confirmLabel="Confirm clear?"
              title="Remove every completed session from the board"
              onConfirm={() => clearLane("completed")}
            />
          )}
          {laneSessions.failed.length > 0 && (
            <ConfirmButton
              label={`Clear failed (${laneSessions.failed.length})`}
              confirmLabel="Confirm clear?"
              title="Remove every failed or cancelled session from the board"
              onConfirm={() => clearLane("failed")}
            />
          )}
        </div>
      )}

      <DndContext
        sensors={sensors}
        onDragStart={onDragStart}
        onDragEnd={onDragEnd}
        onDragCancel={() => setActiveId(null)}
      >
        <div
          className={`grid grid-cols-1 gap-4 md:grid-cols-2 ${
            columns.length === 5 ? "xl:grid-cols-5" : "xl:grid-cols-4"
          }`}
        >
          {columns.map((lane) => (
            <KanbanColumn
              key={lane.id}
              lane={lane}
              sessions={laneSessions[lane.id]}
              count={lanes[lane.id].length}
              draggingFrom={draggingFrom ? asLaneId(draggingFrom) : null}
              busyId={busyId}
              onApprove={(id) => act("approve", id)}
              onReject={(id) => act("reject", id)}
            />
          ))}
        </div>

        <DragOverlay dropAnimation={{ duration: 200, easing: "cubic-bezier(0.22,1,0.36,1)" }}>
          {activeSession && draggingFrom ? (
            <div className="w-[270px]">
              <CardInner session={activeSession} lane={asLaneId(draggingFrom)} overlay />
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>
    </div>
    </KanbanTeamContext.Provider>
    </KanbanActionsContext.Provider>
  );
}
