"use client";

/**
 * The model's REASONING, folded away (v1.323.0). A model that thinks before it
 * answers streams that thinking as `thinking` frames; this is where it goes —
 * one quiet line under the bubble, closed by default, that opens onto the raw
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
  return (
    <div data-testid="thinking-disclosure" className="mb-1.5 text-xs text-zinc-500">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1 transition-colors hover:text-zinc-300"
      >
        {open ? (
          <ChevronDown size={11} className="shrink-0" aria-hidden="true" />
        ) : (
          <ChevronRight size={11} className="shrink-0" aria-hidden="true" />
        )}
        <Brain
          size={11}
          className={`shrink-0 ${live ? "animate-pulse" : ""}`}
          aria-hidden="true"
        />
        <span data-testid="thinking-summary">{thinkingSummary(live, seconds)}</span>
      </button>
      {open && (
        <div
          id={panelId}
          data-testid="thinking-text"
          className="mt-1 max-h-64 overflow-y-auto whitespace-pre-wrap break-words rounded-lg border border-white/[0.06] bg-white/[0.02] px-2.5 py-2 text-xs leading-relaxed text-zinc-500"
        >
          {text}
        </div>
      )}
    </div>
  );
}
