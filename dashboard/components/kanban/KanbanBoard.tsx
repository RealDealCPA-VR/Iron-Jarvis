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
import { CardInner, KanbanActionsContext, type KanbanCardActions } from "./SessionCard";

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

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor),
  );

  const activeSession = activeId ? byId.get(activeId) ?? null : null;
  const draggingFrom: BoardLaneId | null = activeSession
    ? boardLaneFor(activeSession, !!reviews[activeSession.id])
    : null;
  const columns = visibleLanes(lanes);

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
    const action = dropAction(from, to);
    if (action) act(action, id);
    // Any other drop is purely visual — server state is the source of truth,
    // so the card simply settles back into its lane on the next render.
  }

  return (
    <KanbanActionsContext.Provider value={cardActions}>
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
          embedded per-project board must never over-clear other projects. */}
      {!projectId && (lanes.completed.length > 0 || lanes.failed.length > 0) && (
        <div className="flex flex-wrap items-center justify-end gap-2">
          {lanes.completed.length > 0 && (
            <ConfirmButton
              label={`Clear completed (${lanes.completed.length})`}
              confirmLabel="Confirm clear?"
              title="Remove every completed session from the board"
              onConfirm={() => clearLane("completed")}
            />
          )}
          {lanes.failed.length > 0 && (
            <ConfirmButton
              label={`Clear failed (${lanes.failed.length})`}
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
              sessions={lanes[lane.id]}
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
    </KanbanActionsContext.Provider>
  );
}
