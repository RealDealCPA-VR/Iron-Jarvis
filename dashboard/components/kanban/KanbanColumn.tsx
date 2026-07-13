"use client";

import { useDroppable } from "@dnd-kit/core";
import { AnimatePresence, motion } from "framer-motion";
import type { SessionView } from "@/lib/types";
import { dropAction, type LaneDef, type LaneId } from "@/lib/kanban";
import type { Tone } from "@/components/ui";
import { SessionCard } from "./SessionCard";

const HEAD: Record<Tone, { dot: string; text: string; bar: string }> = {
  cyan: { dot: "bg-accent shadow-[0_0_8px_2px_rgb(var(--accent-rgb)/0.55)]", text: "text-accent-soft", bar: "from-accent/60" },
  amber: { dot: "bg-amber-400 shadow-[0_0_8px_2px_rgba(251,191,36,0.5)]", text: "text-amber-300", bar: "from-amber-400/60" },
  green: { dot: "bg-emerald-400 shadow-[0_0_8px_2px_rgba(52,211,153,0.5)]", text: "text-emerald-300", bar: "from-emerald-400/60" },
  red: { dot: "bg-rose-400 shadow-[0_0_8px_2px_rgba(251,113,133,0.5)]", text: "text-rose-300", bar: "from-rose-400/60" },
  slate: { dot: "bg-zinc-500", text: "text-zinc-300", bar: "from-zinc-500/60" },
  violet: { dot: "bg-violet-400", text: "text-violet-300", bar: "from-violet-400/60" },
};

const RING: Record<Tone, string> = {
  cyan: "ring-accent/40 bg-accent/[0.05]",
  amber: "ring-amber-400/40 bg-amber-400/[0.05]",
  green: "ring-emerald-400/40 bg-emerald-400/[0.05]",
  red: "ring-rose-400/40 bg-rose-400/[0.05]",
  slate: "ring-zinc-400/30 bg-white/[0.03]",
  violet: "ring-violet-400/40 bg-violet-400/[0.05]",
};

export function KanbanColumn({
  lane,
  sessions,
  draggingFrom,
  busyId,
  onApprove,
  onReject,
}: {
  lane: LaneDef;
  sessions: SessionView[];
  draggingFrom: LaneId | null;
  busyId: string | null;
  onApprove: (id: string) => void;
  onReject: (id: string) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: lane.id });
  const head = HEAD[lane.tone];

  // Will this drop do something? (review -> completed = approve, -> failed = reject)
  const action = draggingFrom ? dropAction(draggingFrom, lane.id) : null;
  const armed = isOver && action !== null;

  return (
    <div className="flex min-w-0 flex-1 flex-col">
      {/* Column header */}
      <div className="mb-3 flex items-center justify-between px-1">
        <div className="flex items-center gap-2.5">
          <span className={`h-2.5 w-2.5 rounded-full ${head.dot}`} />
          <h2 className={`text-sm font-semibold ${head.text}`}>{lane.title}</h2>
          <span className="rounded-full bg-white/[0.06] px-2 py-0.5 text-[11px] font-medium tabular-nums text-zinc-400">
            {sessions.length}
          </span>
        </div>
        <span className="text-[11px] text-zinc-600">{lane.hint}</span>
      </div>
      <div className={`mb-3 h-0.5 rounded-full bg-gradient-to-r ${head.bar} to-transparent`} />

      {/* Droppable body */}
      <div
        ref={setNodeRef}
        className={`flex flex-1 flex-col gap-2.5 rounded-2xl border p-2.5 ring-1 ring-inset transition-colors duration-150 ${
          armed
            ? `border-transparent ${RING[lane.tone]}`
            : isOver
              ? "border-white/10 bg-white/[0.02] ring-white/10"
              : "border-white/[0.04] bg-ink-900/40 ring-transparent"
        }`}
      >
        {action && armed && (
          <div className={`rounded-lg border border-dashed border-white/20 py-2 text-center text-[12px] font-semibold capitalize ${head.text}`}>
            Drop to {action}
          </div>
        )}

        <AnimatePresence mode="popLayout" initial={false}>
          {sessions.map((s) => (
            <motion.div
              key={s.id}
              layout
              initial={{ opacity: 0, y: 8, scale: 0.98 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, scale: 0.96 }}
              transition={{ duration: 0.25, ease: [0.22, 1, 0.36, 1] }}
            >
              <SessionCard
                session={s}
                lane={lane.id}
                busy={busyId === s.id}
                onApprove={() => onApprove(s.id)}
                onReject={() => onReject(s.id)}
              />
            </motion.div>
          ))}
        </AnimatePresence>

        {sessions.length === 0 && (
          <div className="flex flex-1 items-center justify-center rounded-xl border border-dashed border-white/[0.07] py-8 text-center text-xs text-zinc-600">
            {lane.id === "review" ? "No sessions awaiting review" : `No ${lane.title.toLowerCase()} sessions`}
          </div>
        )}
      </div>
    </div>
  );
}
