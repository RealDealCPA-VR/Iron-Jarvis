"use client";

// A premium n8n-style step card. Shows the step name, an agent-type chip, a
// truncated task, and left/right handles so it can be wired into the chain.

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { agentMeta, type StepNodeData } from "./agents";

const handleClass =
  "!h-3 !w-3 !rounded-full !border-2 !border-ink-950 !bg-accent " +
  "!shadow-[0_0_10px_2px_rgba(34,211,238,0.6)]";

function StepNodeImpl({ data, selected }: NodeProps) {
  const d = data as StepNodeData;
  const meta = agentMeta(d.agent);
  const Icon = meta.icon;

  return (
    <div
      className={`group relative w-[230px] rounded-2xl border bg-ink-850/90 backdrop-blur-sm transition-all duration-200 ${
        selected
          ? "border-accent/70 shadow-glow"
          : "border-white/[0.08] shadow-card hover:-translate-y-0.5 hover:border-accent/40 hover:shadow-card-hover"
      }`}
    >
      <Handle type="target" position={Position.Left} className={handleClass} />

      {/* Header — icon tile + name + index */}
      <div className="flex items-center gap-2.5 border-b hairline px-3.5 py-2.5">
        <span
          className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg border ${meta.tile}`}
        >
          <Icon size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[13px] font-semibold leading-tight text-zinc-100">
            {d.name || "Untitled step"}
          </div>
          <span
            className={`mt-1 inline-flex items-center rounded-full border px-1.5 py-px text-[10px] font-medium uppercase tracking-wide ${meta.chip}`}
          >
            {meta.label}
          </span>
        </div>
        {d.index != null && (
          <span className="grid h-5 w-5 shrink-0 place-items-center rounded-full bg-white/[0.04] text-[10px] font-semibold text-zinc-500">
            {d.index}
          </span>
        )}
      </div>

      {/* Body — truncated task */}
      <div className="px-3.5 py-2.5">
        <p className="line-clamp-2 text-[11.5px] leading-relaxed text-zinc-400">
          {d.task?.trim() ? d.task : (
            <span className="italic text-zinc-600">No task yet — click to edit…</span>
          )}
        </p>
      </div>

      <Handle type="source" position={Position.Right} className={handleClass} />
    </div>
  );
}

export const StepNode = memo(StepNodeImpl);
