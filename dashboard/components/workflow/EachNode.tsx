"use client";

// A "For each" box on the workflow canvas (idea 09, phase 09-a): the steps
// inside it run once for every item. The steps are child nodes laid out by
// blocks.layoutBlocks; this draws the box, its name and what it works
// through, and the left/right handles that keep it in the workflow's chain.

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Repeat } from "lucide-react";
import { BLOCK_META, DEFAULT_MAX_ITEMS, describeOver } from "./blocks";

const handleClass =
  "!h-3 !w-3 !rounded-full !border-2 !border-ink-950 !bg-accent " +
  "!shadow-[0_0_10px_2px_rgb(var(--accent-rgb)/0.6)]";

export interface BlockNodeData {
  name: string;
  kind: string;
  w?: number;
  h?: number;
  index?: number;
  over?: Record<string, unknown>;
  condition?: Record<string, unknown>;
  max_items?: number;
  parallel?: number;
  on_failure?: string;
  laneTops?: Record<string, number>;
  laneHeights?: Record<string, number>;
  laneCounts?: Record<string, number>;
  [key: string]: unknown;
}

function EachNodeImpl({ data, selected }: NodeProps) {
  const d = data as BlockNodeData;
  const meta = BLOCK_META.for_each;
  const over = describeOver(d.over);
  const max = Number(d.max_items ?? DEFAULT_MAX_ITEMS);
  const atOnce = Number(d.parallel ?? 1);
  const empty = Number(d.laneCounts?.body ?? 0) === 0;
  return (
    <div
      data-testid="each-node"
      style={{ width: d.w, height: d.h }}
      className={`relative rounded-2xl border border-dashed bg-ink-850/40 transition-colors ${
        selected ? "border-accent/70" : "border-tone-info/40 hover:border-tone-info/70"
      }`}
    >
      <Handle type="target" position={Position.Left} className={handleClass} />
      <div className="flex items-start gap-2.5 px-3.5 pt-2.5">
        <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg border ${meta.chip}`}>
          <Repeat size={16} />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="truncate text-[13px] font-semibold leading-tight text-zinc-100">
              {d.name || meta.label}
            </span>
            <span className={`shrink-0 rounded-full border px-1.5 py-px text-[11px] font-medium ${meta.chip}`}>
              {meta.label}
            </span>
            {d.index != null && (
              <span className="ml-auto grid h-5 w-5 shrink-0 place-items-center rounded-full bg-white/[0.04] text-[10px] font-semibold text-zinc-500">
                {d.index}
              </span>
            )}
          </div>
          <p className="mt-0.5 truncate text-[11px] text-zinc-400" title={over || undefined}>
            {over || <span className="italic text-zinc-600">What should it work through?</span>}
            {` · at most ${max.toLocaleString()} items`}
            {atOnce > 1 ? ` · ${atOnce} at once` : ""}
          </p>
        </div>
      </div>
      {empty && (
        <p
          className="absolute left-4 text-[11px] italic text-zinc-600"
          style={{ top: Number(d.laneTops?.body ?? 72) + 16 }}
        >
          No steps inside yet. Select the box and press Add step.
        </p>
      )}
      <Handle type="source" position={Position.Right} className={handleClass} />
    </div>
  );
}

export const EachNode = memo(EachNodeImpl);
