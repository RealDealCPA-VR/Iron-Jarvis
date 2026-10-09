"use client";

/**
 * The model's REASONING, folded away (v1.323.0). A model that thinks before it
 * answers streams that thinking as `thinking` frames; this is where it goes —
 * one quiet grey row (live: above the reply; settled: inside the reply's
 * "Worked for …" fold since v1.326.0), closed by default, that opens onto the raw
 * text. It is never part of the reply and never rendered as markdown: it is
 * the model talking to itself, shown as it was written.
 *
 *   live    → "Thinking…"
 *   done    → "Thought for N s", or "Thoughts" when the time is unknown
 *   no text → nothing at all
 *
 * A plain button with `aria-expanded` rather than `<details>`: the open state
 * is React's, so a re-render while live never snaps it shut.
 */

import { useId, useState } from "react";
import { Brain, ChevronDown, ChevronRight } from "lucide-react";

export interface ThinkingDisclosureProps {
  /** The reasoning text so far (plain text). Empty renders nothing. */
  text: string;
  /** The model is still thinking/answering this turn. */
  live?: boolean;
  /** How long it thought, in seconds; null/absent when unknown. */
  seconds?: number | null;
}

/** The summary words — exported so the page and the tests share one wording. */
export function thinkingSummary(live: boolean, seconds: number | null | undefined): string {
  if (live) return "Thinking…";
  if (typeof seconds === "number" && Number.isFinite(seconds) && seconds >= 0) {
    return `Thought for ${Math.max(1, Math.round(seconds))} s`;
  }
  return "Thoughts";
}

export function ThinkingDisclosure({ text, live = false, seconds }: ThinkingDisclosureProps) {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  if (!text || !text.trim()) return null;
  // Calm chat W1-5 (v1.326.0): a grey one-line row like every other piece of
  // process detail (components/chat/WorkLine) — a 14px icon, the words, a
  // chevron — and the text opens under a hairline, never inside a box. While
  // live the brain pulses softly; reduced motion keeps it still.
  return (
    <div data-testid="thinking-disclosure" className="min-w-0 text-[13px] leading-5 text-zinc-500">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex max-w-full items-center gap-2 transition-colors hover:text-zinc-300 focus-visible:text-zinc-300 focus-visible:outline-none"
      >
        <Brain
          size={14}
          className={`shrink-0 ${live ? "animate-pulse motion-reduce:animate-none" : ""}`}
          aria-hidden="true"
        />
        <span
          data-testid="thinking-summary"
          className={live ? "animate-pulse text-zinc-300 motion-reduce:animate-none" : "text-zinc-400"}
        >
          {thinkingSummary(live, seconds)}
        </span>
        {open ? (
          <ChevronDown size={14} className="shrink-0" aria-hidden="true" />
        ) : (
          <ChevronRight size={14} className="shrink-0" aria-hidden="true" />
        )}
      </button>
      {open && (
        <div
          id={panelId}
          data-testid="thinking-text"
          className="ml-[7px] mt-1 max-h-64 overflow-y-auto whitespace-pre-wrap break-words border-l border-white/[0.08] py-0.5 pl-3 text-[13px] leading-relaxed text-zinc-500"
        >
          {text}
        </div>
      )}
    </div>
  );
}
