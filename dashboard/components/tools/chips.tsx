"use client";

/**
 * The four things a tool card has to answer, in order (v1.216.0).
 *
 * From the review: "Change the card to answer four questions in order: what it
 * does (title) / status (Not added / Added / Needs Node) / risk (read-only vs
 * writes disk vs opens browser vs network) / action."
 *
 * These are the middle two. They are shared so the built-in grid and the
 * extension grid cannot drift into describing the same idea two ways — the
 * review's §2 complaint ("two nearly duplicate products") was exactly that
 * happening at the section level.
 *
 * STATUS IS NEVER COLOUR-ONLY (review, accessibility): every state carries an
 * icon and a word, so "Added" and "Add" are distinguishable without seeing
 * green vs teal.
 */

import type { ReactNode } from "react";
import { AlertTriangle, Check, Download, Eye, Globe, HardDrive, Pencil } from "lucide-react";
import { CAPABILITY_CHIP, CAPABILITY_LABEL, type Capability } from "./meta";

/* ------------------------------------------------------------------ status */

export type ToolStatus = "added" | "available" | "blocked";

export function StatusChip({
  status,
  needs,
  className = "",
}: {
  status: ToolStatus;
  /** The runtime a blocked item is waiting on ("Node", "Python (uv)"). */
  needs?: string;
  className?: string;
}) {
  if (status === "added") {
    return (
      <span
        data-testid="status-added"
        className={`inline-flex shrink-0 items-center gap-1 rounded-md border border-emerald-500/30 bg-emerald-500/[0.1] px-1.5 py-0.5 text-[10.5px] font-medium text-emerald-300 ${className}`}
      >
        <Check size={11} aria-hidden /> Enabled
      </span>
    );
  }
  if (status === "blocked") {
    return (
      <span
        data-testid="status-blocked"
        title={`This extension runs through ${needs}`}
        className={`inline-flex shrink-0 items-center gap-1 rounded-md border border-amber-400/25 bg-amber-400/[0.08] px-1.5 py-0.5 text-[10.5px] font-medium text-amber-200/90 ${className}`}
      >
        <AlertTriangle size={11} aria-hidden /> Needs {needs}
      </span>
    );
  }
  return (
    <span
      data-testid="status-available"
      className={`inline-flex shrink-0 items-center gap-1 rounded-md border border-white/10 px-1.5 py-0.5 text-[10.5px] font-medium text-zinc-500 ${className}`}
    >
      <Download size={11} aria-hidden /> Not added
    </span>
  );
}

/* -------------------------------------------------------------------- risk */

const CAP_ICON: Record<Capability, ReactNode> = {
  read: <Eye size={10} aria-hidden />,
  write: <Pencil size={10} aria-hidden />,
  network: <Globe size={10} aria-hidden />,
  browser: <Globe size={10} aria-hidden />,
  system: <HardDrive size={10} aria-hidden />,
};

/** The two that put something NEW on the machine, or hand a URL to whatever
 *  the OS opens. They read amber; the rest stay quiet. Risk that looks like
 *  every other chip is not a risk chip. */
const LOUD: Capability[] = ["write", "browser"];

export function RiskChips({ caps }: { caps: Capability[] }) {
  if (caps.length === 0) return null;
  return (
    <span className="flex flex-wrap items-center gap-1" data-testid="risk-chips">
      {caps.map((c) => (
        <span
          key={c}
          data-testid={`risk-${c}`}
          title={CAPABILITY_LABEL[c]}
          className={`inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${
            LOUD.includes(c)
              ? "border-amber-400/25 bg-amber-400/[0.07] text-amber-200/90"
              : "border-white/[0.07] bg-white/[0.02] text-zinc-500"
          }`}
        >
          {CAP_ICON[c]}
          {CAPABILITY_CHIP[c]}
        </span>
      ))}
    </span>
  );
}

/* ------------------------------------------------------------------ action */

/**
 * The primary action, as a real button (review §4: "Put + Add / Added /
 * Manage as the primary visual, not a small corner chip").
 *
 * The old card put a 11px chip in the top-right for both the action and the
 * added state, so the one thing a user came to do was the smallest thing on
 * the card.
 */
export function PrimaryAction({
  label,
  onClick,
  disabled = false,
  tone = "accent",
  icon,
  title,
  testId,
}: {
  label: ReactNode;
  onClick: () => void;
  disabled?: boolean;
  tone?: "accent" | "quiet" | "danger";
  icon?: ReactNode;
  title?: string;
  testId?: string;
}) {
  const tones = {
    accent:
      "border-accent/40 bg-accent/[0.12] text-accent-soft hover:bg-accent/[0.2] hover:border-accent/60",
    quiet:
      "border-white/10 bg-white/[0.02] text-zinc-300 hover:border-white/20 hover:bg-white/[0.06]",
    danger:
      "border-rose-500/30 bg-rose-500/[0.08] text-rose-300 hover:bg-rose-500/[0.15]",
  } as const;
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title={title}
      data-testid={testId}
      className={`inline-flex w-full items-center justify-center gap-1.5 rounded-lg border px-3 py-1.5 text-[12.5px] font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${tones[tone]}`}
    >
      {icon}
      {label}
    </button>
  );
}

/* ------------------------------------------------------------------ source */

/** Official vs community, said ONCE per card (review §8: "'Official reference
 *  server' on every card becomes wallpaper"). Community is the one worth
 *  marking, so official stays quiet and community carries the word. */
export function SourceChip({ official }: { official: boolean }) {
  return (
    <span
      data-testid={official ? "source-official" : "source-community"}
      title={
        official
          ? "Published by the Model Context Protocol project"
          : "Published by a third party — review what it can do before enabling"
      }
      className={`inline-flex shrink-0 items-center rounded-md border px-1.5 py-0.5 text-[10px] font-medium ${
        official
          ? "border-white/[0.07] bg-white/[0.02] text-zinc-500"
          : "border-violet-400/25 bg-violet-400/[0.07] text-violet-200/90"
      }`}
    >
      {official ? "official" : "community"}
    </span>
  );
}
