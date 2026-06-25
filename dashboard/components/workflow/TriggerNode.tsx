"use client";

// The start node of every workflow — fires "on run". Source handle only.

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Zap } from "lucide-react";
import type { TriggerNodeData } from "./agents";

function TriggerNodeImpl({ data, selected }: NodeProps) {
  const d = data as TriggerNodeData;
  return (
    <div
      className={`relative flex items-center gap-3 rounded-2xl border bg-gradient-to-br from-ink-800/90 to-ink-850/90 px-4 py-3 backdrop-blur-sm transition-all duration-200 ${
        selected
          ? "border-accent/70 shadow-glow"
          : "border-accent/30 shadow-card hover:border-accent/50 hover:shadow-glow-sm"
      }`}
    >
      <span className="relative grid h-9 w-9 place-items-center rounded-xl border border-accent/40 bg-accent/15 text-accent-soft">
        <span className="pointer-events-none absolute inset-0 animate-pulse-glow rounded-xl" />
        <Zap size={18} className="relative z-10" />
      </span>
      <div className="pr-1">
        <div className="text-[13px] font-semibold leading-tight text-zinc-100">
          {d.label || "Trigger"}
        </div>
        <div className="text-[10px] uppercase tracking-[0.14em] text-accent-soft/70">
          On run
        </div>
      </div>
      <Handle
        type="source"
        position={Position.Right}
        className="!h-3 !w-3 !rounded-full !border-2 !border-ink-950 !bg-accent !shadow-[0_0_10px_2px_rgba(34,211,238,0.6)]"
      />
    </div>
  );
}

export const TriggerNode = memo(TriggerNodeImpl);
