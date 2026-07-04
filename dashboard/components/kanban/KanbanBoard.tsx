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
  assignLanes,
  dropAction,
  laneFor,
  type LaneId,
} from "@/lib/kanban";
import { KanbanColumn } from "./KanbanColumn";
import { CardInner, KanbanActionsContext, type KanbanCardActions } from "./SessionCard";

export function KanbanBoard({
  sessions,
  reviews,
  reload,
}: {
  sessions: SessionView[];
  reviews: Record<string, Review>;
  reload: () => void;
}) {
  const [activeId, setActiveId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [toast, setToast] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const lanes = useMemo(() => assignLanes(sessions, reviews), [sessions, reviews]);
  const byId = useMemo(() => {
    const m = new Map<string, SessionView>();
    for (const s of sessions) m.set(s.id, s);
    return m;
  }, [sessions]);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor),
  );

  const activeSession = activeId ? byId.get(activeId) ?? null : null;
  const draggingFrom: LaneId | null = activeSession
    ? laneFor(activeSession, !!reviews[activeSession.id])
    : null;

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
          clear affordances sit here, right-aligned above Completed/Failed. */}
      {(lanes.completed.length > 0 || lanes.failed.length > 0) && (
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
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-4">
          {LANES.map((lane) => (
            <KanbanColumn
              key={lane.id}
              lane={lane}
              sessions={lanes[lane.id]}
              draggingFrom={draggingFrom}
              busyId={busyId}
              onApprove={(id) => act("approve", id)}
              onReject={(id) => act("reject", id)}
            />
          ))}
        </div>

        <DragOverlay dropAnimation={{ duration: 200, easing: "cubic-bezier(0.22,1,0.36,1)" }}>
          {activeSession && draggingFrom ? (
            <div className="w-[270px]">
              <CardInner session={activeSession} lane={draggingFrom} overlay />
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>
    </div>
    </KanbanActionsContext.Provider>
  );
}
