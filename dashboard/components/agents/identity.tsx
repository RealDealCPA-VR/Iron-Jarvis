"use client";

// Shared visual identity for the Agents page: every agent gets a deterministic
// hue derived from its participant key ("<source>:<name>"), so the same agent
// is recognizable at a glance across the thread rail, panel chips, and
// messages. Also home to the small shared types for agent threads.

import { Bot, Globe, Sparkles } from "lucide-react";

/* ----------------------------------------------------------------- types --- */

export type AgentSource = "builtin" | "dynamic" | "remote";

/** One panel member as stored on a thread ({key} = "<source>:<name>"). */
export interface Participant {
  key: string;
  source: AgentSource;
  name: string;
  role: string;
  provider?: string;
  model?: string;
}

/** GET /agents/threads list row (no messages — GET one for the transcript). */
export interface ThreadRow {
  id: string;
  title: string;
  participants: Participant[];
  message_count: number;
  updated_at: string;
}

/** One transcript entry. `who` is "user" or the participant key. An `error`
 *  is an honest per-agent failure — always rendered, never hidden. */
export interface ThreadEntry {
  who: string;
  role?: string;
  source?: string;
  content: string;
  at: string;
  error?: string;
}

/** GET /agents/threads/{id}. */
export interface ThreadDetail extends ThreadRow {
  messages: ThreadEntry[];
}

/** A registered remote agent (GET /agents/remote — the token never returns). */
export interface RemoteAgentInfo {
  name: string;
  base_url: string;
  kind: string;
  model?: string | null;
  enabled?: boolean;
  timeout_s?: number | null;
  has_credential?: boolean;
}

/** Role presets offered at panel setup — free text is equally valid. */
export const ROLE_PRESETS = [
  "lead",
  "researcher",
  "critic",
  "builder",
  "reviewer",
  "scribe",
] as const;

export function participantKey(source: AgentSource, name: string): string {
  return `${source}:${name}`;
}

/* ------------------------------------------------------------------- hue --- */

/** Deterministic hue (0–359) for an agent key — same color on every surface. */
export function hueFor(key: string): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return h % 360;
}

/** The agent's display color for name labels (readable on dark ink). */
export function nameColor(key: string): string {
  return `hsl(${hueFor(key)} 70% 75%)`;
}

/* ---------------------------------------------------------------- avatar --- */

const AVATAR_SIZES = {
  xs: "h-4 w-4 text-[8px]",
  sm: "h-5 w-5 text-[9px]",
  md: "h-7 w-7 text-[11px]",
  lg: "h-10 w-10 text-[15px]",
} as const;

export function AgentAvatar({
  agentKey,
  name,
  size = "md",
  ring = false,
  title,
  className = "",
}: {
  agentKey: string;
  name: string;
  size?: keyof typeof AVATAR_SIZES;
  /** Adds a dark ring so overlapping avatar stacks read as separate dots. */
  ring?: boolean;
  title?: string;
  className?: string;
}) {
  const h = hueFor(agentKey);
  return (
    <span
      title={title}
      aria-hidden={title ? undefined : true}
      className={`grid shrink-0 select-none place-items-center rounded-full border font-semibold uppercase leading-none ${AVATAR_SIZES[size]} ${
        ring ? "ring-2 ring-ink-900" : ""
      } ${className}`}
      style={{
        background: `hsl(${h} 70% 55% / 0.25)`,
        borderColor: `hsl(${h} 70% 65%)`,
        color: `hsl(${h} 75% 80%)`,
      }}
    >
      {(name || "?").charAt(0)}
    </span>
  );
}

/** A compact overlapping stack of avatar dots (rail rows, progress line). */
export function AvatarStack({
  participants,
  max = 5,
  size = "sm",
}: {
  participants: Participant[];
  max?: number;
  size?: keyof typeof AVATAR_SIZES;
}) {
  const shown = participants.slice(0, max);
  const extra = participants.length - shown.length;
  return (
    <span className="flex items-center -space-x-1.5">
      {shown.map((p) => (
        <AgentAvatar
          key={p.key}
          agentKey={p.key}
          name={p.name}
          size={size}
          ring
          title={p.role ? `${p.name} — ${p.role}` : p.name}
        />
      ))}
      {extra > 0 && (
        <span
          className={`grid shrink-0 place-items-center rounded-full border border-white/10 bg-ink-800 text-zinc-400 ring-2 ring-ink-900 ${AVATAR_SIZES[size]}`}
        >
          +{extra}
        </span>
      )}
    </span>
  );
}

/* --------------------------------------------------------------- markers --- */

/** Source marker: builtin = Bot, dynamic (yours) = Sparkles, remote = Globe. */
export function SourceIcon({
  source,
  size = 11,
  className,
}: {
  source: string | undefined;
  size?: number;
  className?: string;
}) {
  if (source === "dynamic")
    return <Sparkles size={size} className={className ?? "text-violet-300"} />;
  if (source === "remote")
    return <Globe size={size} className={className ?? "text-emerald-300"} />;
  return <Bot size={size} className={className ?? "text-accent-soft/80"} />;
}

export const SOURCE_LABEL: Record<AgentSource, string> = {
  builtin: "Built-in",
  dynamic: "Yours",
  remote: "Remote",
};

/** Tiny uppercase role pill — the agent's job on this panel. */
export function RolePill({ role, className = "" }: { role?: string; className?: string }) {
  if (!role) return null;
  return (
    <span
      className={`inline-flex shrink-0 items-center rounded-full border border-white/10 bg-white/[0.04] px-1.5 py-px text-[9px] font-medium uppercase tracking-[0.12em] text-zinc-400 ${className}`}
    >
      {role}
    </span>
  );
}
