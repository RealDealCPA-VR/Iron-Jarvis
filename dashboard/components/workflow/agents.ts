// Agent-type metadata shared by the workflow node editor.
// Each agent type gets an icon, a disciplined accent and chip styling so the
// graph reads at a glance — cyan builder, violet planner, amber reviewer,
// emerald supervisor, sky researcher.

import type { ComponentType } from "react";
import { Hammer, MapPinned, ScanEye, Search, ShieldCheck } from "lucide-react";

export type AgentType =
  | "builder"
  | "planner"
  | "reviewer"
  | "supervisor"
  | "researcher";

export const AGENT_TYPES: AgentType[] = [
  "builder",
  "planner",
  "reviewer",
  "supervisor",
  "researcher",
];

type IconType = ComponentType<{ size?: number; className?: string }>;

export interface AgentMeta {
  label: string;
  icon: IconType;
  /** Chip / badge classes (border + bg + text). */
  chip: string;
  /** Icon-tile classes. */
  tile: string;
  /** Selected-ring accent (box-shadow color, rgba). */
  glow: string;
  /** Hex used by the React Flow MiniMap. */
  hex: string;
}

export const AGENT_META: Record<AgentType, AgentMeta> = {
  builder: {
    label: "Builder",
    icon: Hammer,
    chip: "border-accent/30 bg-accent/10 text-accent-soft",
    tile: "border-accent/30 bg-accent/10 text-accent-soft",
    glow: "rgba(34,211,238,0.55)",
    hex: "#22d3ee",
  },
  planner: {
    label: "Planner",
    icon: MapPinned,
    chip: "border-violet-500/30 bg-violet-500/10 text-violet-300",
    tile: "border-violet-500/30 bg-violet-500/10 text-violet-300",
    glow: "rgba(167,139,250,0.55)",
    hex: "#a78bfa",
  },
  reviewer: {
    label: "Reviewer",
    icon: ScanEye,
    chip: "border-amber-500/30 bg-amber-500/10 text-amber-300",
    tile: "border-amber-500/30 bg-amber-500/10 text-amber-300",
    glow: "rgba(251,191,36,0.55)",
    hex: "#fbbf24",
  },
  supervisor: {
    label: "Supervisor",
    icon: ShieldCheck,
    chip: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    tile: "border-emerald-500/30 bg-emerald-500/10 text-emerald-300",
    glow: "rgba(52,211,153,0.55)",
    hex: "#34d399",
  },
  researcher: {
    label: "Researcher",
    icon: Search,
    chip: "border-sky-500/30 bg-sky-500/10 text-sky-300",
    tile: "border-sky-500/30 bg-sky-500/10 text-sky-300",
    glow: "rgba(56,189,248,0.55)",
    hex: "#38bdf8",
  },
};

export function agentMeta(agent: string): AgentMeta {
  return AGENT_META[(agent as AgentType)] ?? AGENT_META.builder;
}

/** Display label for an agent: the friendly built-in label, else the raw name
 *  (dynamic/unknown agents are shown as-is rather than coerced to Builder). */
export function agentLabel(agent: string): string {
  return AGENT_META[(agent as AgentType)]?.label ?? agent;
}

/* ---- Node data shapes ---------------------------------------------------- */

export interface StepNodeData {
  name: string;
  /** Built-in AgentType OR a dynamic/agent-authored agent name — kept verbatim
   *  through load/save/run (never coerced to "builder"). */
  agent: string;
  task: string;
  /** Optional tool tag carried through load/save/run (generated workflows use it). */
  tool?: string | null;
  /** 1-based index shown on the card; kept in sync by the canvas. */
  index?: number;
  [key: string]: unknown;
}

export interface TriggerNodeData {
  label?: string;
  [key: string]: unknown;
}

/* ---- Saved workflow definitions (GET/POST /workflows) -------------------- */

/** A persisted, agent-authored workflow def as returned by the daemon.
 *  `steps_json` is a JSON string of `[{name, agent, task}]`. */
export interface WorkflowDef {
  id?: string;
  name: string;
  description?: string;
  steps_json: string;
  created_at?: string;
  updated_at?: string;
}
