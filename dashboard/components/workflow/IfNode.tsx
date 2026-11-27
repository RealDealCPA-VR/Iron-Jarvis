"use client";

// An "If" box on the workflow canvas (idea 09, phase 09-a): it checks a value
// from an earlier step and runs one of its two lanes, Then or Otherwise. The
// steps in each lane are child nodes laid out by blocks.layoutBlocks; this
// draws the box, the condition in words, the two lane labels and the handles
// that keep it in the workflow's chain.

import { memo } from "react";
import { Handle, Position, type NodeProps } from "@xyflow/react";
import { Split } from "lucide-react";
import { BLOCK_META, OTHERWISE_WORD, THEN_WORD, describeCondition } from "./blocks";
import type { BlockNodeData } from "./EachNode";

const handleClass =
  "!h-3 !w-3 !rounded-full !border-2 !border-ink-950 !bg-accent " +
  "!shadow-[0_0_10px_2px_rgb(var(--accent-rgb)/0.6)]";

function IfNodeImpl({ data, selected }: NodeProps) {
  const d = data as BlockNodeData;
  const meta = BLOCK_META.if;
  const said = describeCondition(d.condition);
  const lanes: { key: string; word: string }[] = [
    { key: "then", word: THEN_WORD },
    { key: "else", word: OTHERWISE_WORD },
  ];
  return (
    <div
      data-testid="if-node"
      style={{ width: d.w, height: d.h }}
      className={`relative rounded-2xl border border-dashed bg-ink-850/40 transition-colors ${
        selected ? "border-accent/70" : "border-tone-violet/40 hover:border-tone-violet/70"
      }`}
    >
      <Handle type="target" position={Position.Left} className={handleClass} />
      <div className="flex items-start gap-2.5 px-3.5 pt-2.5">
        <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg border ${meta.chip}`}>
          <Split size={16} />
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
          <p className="mt-0.5 truncate text-[11px] text-zinc-400" title={said || undefined}>
            {said ? `If ${said}` : <span className="italic text-zinc-600">What should it check?</span>}
          </p>
        </div>
      </div>
      {lanes.map(({ key, word }) => {
        const top = Number(d.laneTops?.[key] ?? 0);
        const count = Number(d.laneCounts?.[key] ?? 0);
        return (
          <div key={key} aria-hidden className="pointer-events-none absolute inset-x-0" style={{ top: top - 22 }}>
            <div className="flex items-center gap-2 px-4">
              <span
                data-testid={`if-lane-${key}`}
                className="text-[11px] font-medium text-zinc-400"
              >
                {word}
              </span>
              <span className="h-px flex-1 bg-white/[0.06]" />
            </div>
            {count === 0 && (
              <p className="px-4 pt-4 text-[11px] italic text-zinc-600">
                {key === "then" ? "Nothing runs when it holds." : "Nothing runs otherwise."}
              </p>
            )}
          </div>
        );
      })}
      <Handle type="source" position={Position.Right} className={handleClass} />
    </div>
  );
}

export const IfNode = memo(IfNodeImpl);
