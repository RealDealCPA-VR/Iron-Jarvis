"use client";

/**
 * Suggested follow-ups under the newest reply (v1.323.0, the `chat_followups`
 * setting — off by default). Up to three small pills; a press hands the text
 * to `onPick`, and the page puts it in the composer. SUGGEST, NEVER SEND: the
 * user reads, edits or ignores it before anything leaves.
 */

import { CornerDownRight } from "lucide-react";

export interface FollowupChipsProps {
  suggestions: string[];
  onPick: (s: string) => void;
  disabled?: boolean;
}

/** At most this many pills — more is a menu, not a nudge. */
export const MAX_FOLLOWUPS = 3;

export function FollowupChips({ suggestions, onPick, disabled = false }: FollowupChipsProps) {
  const shown = (Array.isArray(suggestions) ? suggestions : [])
    .filter((s): s is string => typeof s === "string" && s.trim().length > 0)
    .slice(0, MAX_FOLLOWUPS);
  if (!shown.length) return null;
  return (
    <div
      data-testid="followup-chips"
      aria-label="Suggested follow-ups"
      className="ml-11 mt-1.5 flex flex-wrap gap-1.5"
    >
      {shown.map((s, i) => (
        <button
          key={`${i}-${s}`}
          type="button"
          data-testid="followup-chip"
          disabled={disabled}
          title="Put this in the message box"
          onClick={() => onPick(s.trim())}
          className="inline-flex max-w-full items-center gap-1 rounded-full border border-white/[0.08] bg-white/[0.02] px-2.5 py-0.5 text-xs text-zinc-400 transition-colors hover:border-white/[0.14] hover:text-zinc-200 disabled:opacity-40"
        >
          <CornerDownRight size={10} className="shrink-0 text-zinc-600" aria-hidden="true" />
          <span className="truncate">{s.trim()}</span>
        </button>
      ))}
    </div>
  );
}
