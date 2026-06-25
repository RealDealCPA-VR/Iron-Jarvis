"use client";

import { useRouter } from "next/navigation";
import { useDraggable } from "@dnd-kit/core";
import { GripVertical, Check, X, Cpu, LoaderCircle } from "lucide-react";
import type { SessionView } from "@/lib/types";
import type { LaneId } from "@/lib/kanban";
import { StatusDot } from "@/components/ui";
import { timeAgo } from "@/lib/format";

export interface CardData {
  session: SessionView;
  lane: LaneId;
}

/** Purely presentational card body — shared by the live card and the drag overlay. */
export function CardInner({
  session,
  lane,
  dragging = false,
  overlay = false,
  busy,
  onApprove,
  onReject,
  dragHandle,
}: {
  session: SessionView;
  lane: LaneId;
  dragging?: boolean;
  overlay?: boolean;
  busy?: boolean;
  onApprove?: () => void;
  onReject?: () => void;
  dragHandle?: React.ReactNode;
}) {
  const reviewable = lane === "review";
  return (
    <div
      className={`group/card relative rounded-xl border bg-ink-850/90 p-3.5 transition-all duration-200 ${
        overlay
          ? "border-accent/40 shadow-glow rotate-[1.5deg] scale-[1.02]"
          : "border-white/[0.07] hover:border-white/[0.14] hover:bg-ink-800/90 hover:shadow-card-hover"
      } ${dragging ? "opacity-40" : ""}`}
    >
      <div className="flex items-start gap-2">
        <StatusDot status={reviewable ? "review" : session.status} className="mt-1.5" />
        <p className="flex-1 line-clamp-2 text-[13px] font-medium leading-snug text-zinc-100">
          {session.task || "(untitled task)"}
        </p>
        {dragHandle}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <span className="inline-flex items-center gap-1 rounded-md bg-white/[0.05] px-1.5 py-0.5 text-[10.5px] font-medium text-zinc-300">
          <Cpu size={11} className="text-accent-soft/70" />
          {session.agent_type}
        </span>
        <span className="rounded-md bg-white/[0.05] px-1.5 py-0.5 font-mono text-[10.5px] text-zinc-400">
          {session.provider}
        </span>
        <span className="ml-auto text-[11px] tabular-nums text-zinc-500">
          {timeAgo(session.created_at)}
        </span>
      </div>

      {reviewable && (
        <div className="mt-3 flex items-center gap-2 border-t border-white/[0.06] pt-3">
          <button
            type="button"
            disabled={busy}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onApprove?.();
            }}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2 py-1.5 text-[12px] font-semibold text-emerald-300 transition-colors hover:bg-emerald-500/20 disabled:opacity-40"
          >
            {busy ? <LoaderCircle size={13} className="animate-spin-slow" /> : <Check size={13} />}
            Approve
          </button>
          <button
            type="button"
            disabled={busy}
            onPointerDown={(e) => e.stopPropagation()}
            onClick={(e) => {
              e.stopPropagation();
              onReject?.();
            }}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-rose-500/30 bg-rose-500/10 px-2 py-1.5 text-[12px] font-semibold text-rose-300 transition-colors hover:bg-rose-500/20 disabled:opacity-40"
          >
            <X size={13} />
            Reject
          </button>
        </div>
      )}
    </div>
  );
}

export function SessionCard({
  session,
  lane,
  busy,
  onApprove,
  onReject,
}: {
  session: SessionView;
  lane: LaneId;
  busy?: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const router = useRouter();
  const { setNodeRef, listeners, attributes, isDragging } = useDraggable({
    id: session.id,
    data: { lane },
  });

  return (
    <div
      ref={setNodeRef}
      {...attributes}
      onClick={() => router.push(`/sessions/${session.id}`)}
      className="cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-accent/40 rounded-xl"
    >
      <CardInner
        session={session}
        lane={lane}
        dragging={isDragging}
        busy={busy}
        onApprove={onApprove}
        onReject={onReject}
        dragHandle={
          <button
            type="button"
            aria-label="Drag card"
            {...listeners}
            onClick={(e) => e.stopPropagation()}
            className="-m-1 cursor-grab rounded-md p-1 text-zinc-600 opacity-0 transition-opacity hover:text-zinc-300 active:cursor-grabbing group-hover/card:opacity-100"
          >
            <GripVertical size={15} />
          </button>
        }
      />
    </div>
  );
}
