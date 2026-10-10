// Agent-type metadata shared by the workflow node editor.
// Each agent type gets an icon, a disciplined accent and chip styling so the
// graph reads at a glance: the accent for builder, then the theme's tone
// tokens (violet planner, warn reviewer, success supervisor, info
// researcher).
//
// v1.329.0 (calm chat wave 6): every colour here is a THEME token. The chips
// render on the workflow canvas, the node inspector AND the chat's
// WorkflowDraftCard, and literal hues (violet-300, amber-300, sky-300,
// emerald-300) only read on Daylight through the generated light overrides,
// while `tone-*` is re-inked by every theme itself. The S3 source guard
// (__tests__/chat-cards-whole-pixels-v1329.test.tsx) reads this file too.

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
  /** Selected-ring accent (a box-shadow colour; a theme variable). */
  glow: string;
  /** The React Flow MiniMap's fill. The MiniMap applies it as a STYLE fill
   *  (`style={{ fill }}`), so a theme variable resolves and the minimap
   *  follows the theme like the chips do. */
  hex: string;
}

export const AGENT_META: Record<AgentType, AgentMeta> = {
  builder: {
    label: "Builder",
    icon: Hammer,
    chip: "border-accent/30 bg-accent/10 text-accent-soft",
    tile: "border-accent/30 bg-accent/10 text-accent-soft",
    glow: "rgb(var(--accent-rgb)/0.55)",
    hex: "rgb(var(--accent-rgb))",
  },
  planner: {
    label: "Planner",
    icon: MapPinned,
    chip: "border-tone-violet/30 bg-tone-violet/10 text-tone-violet",
    tile: "border-tone-violet/30 bg-tone-violet/10 text-tone-violet",
    glow: "rgb(var(--tone-violet)/0.55)",
    hex: "rgb(var(--tone-violet))",
  },
  reviewer: {
    label: "Reviewer",
    icon: ScanEye,
    chip: "border-tone-warn/30 bg-tone-warn/10 text-tone-warn",
    tile: "border-tone-warn/30 bg-tone-warn/10 text-tone-warn",
    glow: "rgb(var(--tone-warn)/0.55)",
    hex: "rgb(var(--tone-warn))",
  },
  supervisor: {
    label: "Supervisor",
    icon: ShieldCheck,
    chip: "border-tone-success/30 bg-tone-success/10 text-tone-success",
    tile: "border-tone-success/30 bg-tone-success/10 text-tone-success",
    glow: "rgb(var(--tone-success)/0.55)",
    hex: "rgb(var(--tone-success))",
  },
  researcher: {
    label: "Researcher",
    icon: Search,
    chip: "border-tone-info/30 bg-tone-info/10 text-tone-info",
    tile: "border-tone-info/30 bg-tone-info/10 text-tone-info",
    glow: "rgb(var(--tone-info)/0.55)",
    hex: "rgb(var(--tone-info))",
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

/** What a step IS (v1.121.0) — mirrors the engine's STEP_KINDS. */
export const STEP_KINDS = ["agent", "tool", "ask", "notify"] as const;
export type StepKind = (typeof STEP_KINDS)[number];

export const KIND_META: Record<StepKind, { label: string; blurb: string; chip: string }> = {
  agent: {
    label: "Agent",
    blurb: "An agent works the task with judgment and tools.",
    chip: "border-accent/30 bg-accent/10 text-accent-soft",
  },
  tool: {
    label: "Tool call",
    blurb: "One tool call, the same every run, with no model.",
    chip: "border-tone-info/30 bg-tone-info/10 text-tone-info",
  },
  ask: {
    label: "Ask you",
    blurb: "The run pauses and asks you. It resumes when you answer.",
    chip: "border-tone-warn/30 bg-tone-warn/10 text-tone-warn",
  },
  notify: {
    label: "Notify",
    blurb: "Sends a message to your destinations.",
    chip: "border-tone-success/30 bg-tone-success/10 text-tone-success",
  },
};

export const ON_FAILURE_OPTIONS = [
  { key: "halt", label: "Stop the run (default)" },
  { key: "retry", label: "Retry once, then stop" },
  { key: "skip", label: "Skip it and continue" },
] as const;

/** v1.170.0 — deterministic post-step checks ("Prove it"): files the step must
 *  have produced and/or substrings its summary must contain. Serialized ONLY
 *  when non-empty so old defs stay byte-identical. */
export interface StepExpect {
  files?: string[];
  summary_contains?: string[];
}

export interface StepNodeData {
  name: string;
  /** Built-in AgentType OR a dynamic/agent-authored agent name — kept verbatim
   *  through load/save/run (never coerced to "builder"). */
  agent: string;
  task: string;
  /** Optional tool tag carried through load/save/run (generated workflows use it). */
  tool?: string | null;
  /** v1.121.0 — agent | tool | ask | notify. */
  kind?: string;
  /** v1.121.0 — halt | retry | skip. */
  on_failure?: string;
  /** v1.121.0 — consecutive steps sharing a group run concurrently. */
  group?: string | null;
  /** v1.121.0 — tool-kind arguments (templatable with {{Step Name}}). */
  args?: Record<string, unknown>;
  /** v1.121.0 — ask/notify text (the question / the notification). */
  message?: string;
  /** v1.170.0 — optional deterministic post-step checks (see StepExpect). */
  expect?: StepExpect | null;
  /** 1-based index shown on the card; kept in sync by the canvas. */
  index?: number;
  /** v1.170.0 — set by the canvas when this step's parallel group is split by
   *  other steps in serialized order (split parts run separately, not
   *  together); StepNode renders the warning chip. Never serialized. */
  groupSplit?: boolean;
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
  /** Project pin — present on GET /workflows/{name} (v1.170.0: the canvas
   *  preserves it across runs of an edited def); the list endpoint omits it. */
  project_id?: string | null;
  created_at?: string;
  updated_at?: string;
}
