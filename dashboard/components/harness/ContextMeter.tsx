"use client";

/**
 * HOW FULL THE CONVERSATION IS, and Compact (v1.374.0, IJ harness part 2
 * Phase 3).
 *
 * A small quiet ring in the session's box row with the percent beside it —
 * the DAEMON's reading of the newest reply (lib/buildContext: the planner's
 * own numbers for that turn, never a guess here). Past the offer threshold
 * (the daemon's `level`, 70 % by default) the ring takes the warning tone and
 * a **Compact** press appears beside it: the session's own model writes a
 * checked summary of the older turns, which later turns read in their place
 * (the conversation shown here is never cut). Once a summary stands, the
 * meter says the earlier turns are summarized and opens the summary itself
 * (the chat's CompactionCard), until the next reply's reading replaces it.
 *
 * Colours only through the theme's tokens; whole-pixel sizes.
 */

import { Layers, Loader2 } from "lucide-react";
import { meterTitle, type TurnContext } from "@/lib/buildContext";
import { COMPACTION_OFFER_BUTTON } from "@/lib/chatControlWords";

const RING = 14;
const STROKE = 2;

function Ring({ percent, warn }: { percent: number; warn: boolean }) {
  const r = (RING - STROKE) / 2;
  const c = 2 * Math.PI * r;
  const filled = Math.max(0, Math.min(1, percent / 100));
  return (
    <svg width={RING} height={RING} viewBox={`0 0 ${RING} ${RING}`} aria-hidden className="shrink-0 -rotate-90">
      <circle cx={RING / 2} cy={RING / 2} r={r} fill="none" strokeWidth={STROKE} className="stroke-white/[0.12]" />
      <circle
        cx={RING / 2}
        cy={RING / 2}
        r={r}
        fill="none"
        strokeWidth={STROKE}
        strokeDasharray={`${c * filled} ${c}`}
        strokeLinecap="round"
        className={warn ? "stroke-tone-warn" : "stroke-zinc-500"}
      />
    </svg>
  );
}

export function ContextMeter({
  reading,
  offer,
  summarized,
  busy,
  onCompact,
  onViewSummary,
}: {
  /** The newest reply's reading (null: nothing to show yet). */
  reading: TurnContext | null;
  /** The daemon says the conversation is past the offer threshold. */
  offer: boolean;
  /** A summary stands in for the earlier turns (made after the reading). */
  summarized: boolean;
  /** A Compact is under way. */
  busy: boolean;
  onCompact: () => void;
  onViewSummary: () => void;
}) {
  if (busy) {
    return (
      <span
        data-testid="harness-context-meter"
        data-state="compacting"
        role="status"
        className="flex shrink-0 items-center gap-1.5 px-1 text-[12px] text-zinc-500"
      >
        <Loader2 size={12} className="animate-spin" aria-hidden />
        Summarizing earlier turns…
      </span>
    );
  }
  if (summarized) {
    return (
      <button
        type="button"
        data-testid="harness-context-meter"
        data-state="summarized"
        onClick={onViewSummary}
        title="The model reads a summary in place of the earlier turns; the full conversation is kept. Press to read the summary."
        className="flex shrink-0 items-center gap-1.5 rounded-md px-1 text-[12px] text-zinc-500 transition-colors hover:text-zinc-200"
      >
        <Layers size={12} aria-hidden />
        Earlier turns summarized
      </button>
    );
  }
  if (!reading) return null;
  const warn = offer;
  return (
    <span className="flex shrink-0 items-center gap-1">
      <span
        data-testid="harness-context-meter"
        data-state={warn ? "full" : "ok"}
        role="meter"
        aria-label="How full the conversation is for this model"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.min(100, reading.percent)}
        aria-valuetext={`${reading.percent}%`}
        title={meterTitle(reading)}
        className={`flex items-center gap-1 px-1 text-[12px] tabular-nums ${warn ? "text-tone-warn" : "text-zinc-500"}`}
      >
        <Ring percent={reading.percent} warn={warn} />
        <span data-testid="harness-context-percent">{reading.percent}%</span>
      </span>
      {offer ? (
        <button
          type="button"
          data-testid="harness-compact"
          onClick={onCompact}
          title="Summarize the earlier turns so the conversation stays light. The model reads the summary in their place; the full conversation is kept."
          className="rounded-md px-1.5 py-0.5 text-[12px] font-medium text-tone-warn transition-colors hover:bg-tone-warn/10"
        >
          {/* Harness gaps (fix 4): the chat's word for the same press
              (v1.356.0 G75 retired "Compact now"); the "/compact" command
              keeps its name — it is Claude Code's. */}
          {COMPACTION_OFFER_BUTTON}
        </button>
      ) : null}
    </span>
  );
}
