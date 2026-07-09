"use client";

// Side panel for editing a selected step node: name, agent type, and task
// (with a voice-dictation mic). Overlays the right edge of the canvas.

import { X, Trash2, SlidersHorizontal } from "lucide-react";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";
import { useApi } from "@/lib/useApi";
import type { AgentsResponse } from "@/lib/types";
import {
  AGENT_TYPES,
  agentMeta,
  agentLabel,
  type StepNodeData,
} from "./agents";

export function NodeInspector({
  data,
  onChange,
  onDelete,
  onClose,
}: {
  data: StepNodeData;
  onChange: (patch: Partial<StepNodeData>) => void;
  onDelete: () => void;
  onClose: () => void;
}) {
  // Live agent roster: built-ins + user/agent-authored dynamic agents. Merge in
  // the step's CURRENT agent so an unknown/dynamic value stays selectable (and
  // isn't silently coerced to Builder) even before /agents resolves.
  const { data: agentsData } = useApi<AgentsResponse>("/agents");
  const builtin = agentsData?.builtin ?? AGENT_TYPES;
  const dynamic = (agentsData?.dynamic ?? []).map((d) => d.name);
  const agentOptions = Array.from(
    new Set([...builtin, ...dynamic, data.agent].filter(Boolean)),
  );

  return (
    <div className="card-surface absolute right-3 top-3 bottom-3 z-20 flex w-[300px] flex-col overflow-hidden">
      <header className="flex items-center justify-between gap-3 border-b hairline px-4 py-3">
        <h3 className="flex items-center gap-2 text-[13px] font-semibold text-zinc-200">
          <SlidersHorizontal size={14} className="text-accent-soft/80" />
          Edit step
        </h3>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close inspector"
          className="rounded-lg border border-white/10 p-1 text-zinc-500 transition-colors hover:border-white/20 hover:text-zinc-200"
        >
          <X size={14} />
        </button>
      </header>

      <div className="flex-1 space-y-4 overflow-y-auto p-4">
        <div>
          <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
            Step name
          </label>
          <input
            value={data.name}
            onChange={(e) => onChange({ name: e.target.value })}
            placeholder="e.g. Gather"
            className="field"
          />
        </div>

        <div>
          <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
            Agent type
          </label>
          <div className="grid grid-cols-2 gap-2">
            {agentOptions.map((a) => {
              const meta = agentMeta(a);
              const Icon = meta.icon;
              const active = data.agent === a;
              return (
                <button
                  key={a}
                  type="button"
                  onClick={() => onChange({ agent: a })}
                  className={`flex items-center gap-2 rounded-xl border px-2.5 py-2 text-xs font-medium transition-all ${
                    active
                      ? `${meta.chip} ring-1 ring-inset ring-white/10`
                      : "border-white/[0.08] bg-white/[0.02] text-zinc-400 hover:border-white/20 hover:text-zinc-200"
                  }`}
                >
                  <Icon size={14} />
                  <span className="truncate">{agentLabel(a)}</span>
                </button>
              );
            })}
          </div>
        </div>

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <label className="text-[11px] uppercase tracking-[0.1em] text-zinc-400">
              Task
            </label>
            <VoiceInput
              size="sm"
              onTranscript={(chunk) =>
                onChange({ task: appendDictation(data.task ?? "", chunk) })
              }
            />
          </div>
          <textarea
            value={data.task}
            onChange={(e) => onChange({ task: e.target.value })}
            rows={6}
            placeholder="What should this agent do?"
            className="field resize-y"
          />
        </div>

        <div>
          <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
            Tool (optional)
          </label>
          <input
            value={data.tool ?? ""}
            onChange={(e) => onChange({ tool: e.target.value || null })}
            placeholder="e.g. web_search"
            className="field"
          />
          <p className="mt-1.5 text-[11px] text-zinc-500">
            Advanced: tag this step with a tool name.
          </p>
        </div>
      </div>

      <footer className="border-t hairline p-3">
        <button
          type="button"
          onClick={onDelete}
          className="flex w-full items-center justify-center gap-2 rounded-xl border border-rose-500/25 bg-rose-500/[0.07] px-3 py-2 text-sm font-medium text-rose-200 transition-colors hover:border-rose-500/50 hover:bg-rose-500/[0.12]"
        >
          <Trash2 size={15} /> Delete step
        </button>
      </footer>
    </div>
  );
}
