"use client";

/**
 * v1.358.0 (wave 8, lane 8C): a helper-agent run's step-by-step log, ONE
 * piece for both places it is shown.
 *
 * The receipt's `activity` (agents/outcome.session_result: the newest 20
 * plain lines, oldest first, the mission screen's words) was drawn only by
 * the run card under the reply ("What it did"). Once the settled hand-off row
 * became the run's one receipt, the card stopped repeating it, and the row's
 * opened body shows the same fold under its action lines. Both read it here,
 * so the two can never say a step two ways.
 *
 * Kept in its own small module ON PURPOSE: the chat page loads the run card
 * on demand (a deferred panel, v1.258.0), while the settled row is drawn at
 * first paint. Importing a value from the card's module would pull the whole
 * card into the page's first load.
 *
 * Calm chat: quiet grey rows, no box, no border; the toggle is a native
 * button (Enter and Space open it) with `aria-expanded`.
 */

import { useId, useState } from "react";
import { AlertTriangle, Check, ChevronDown, ChevronRight, Hand } from "lucide-react";

/** One line of the log, checked. */
export interface RunActivityLine {
  text: string;
  tone: "ok" | "warn" | "ask";
}

/** At most this many lines are shown (the receipt's own cap). */
export const RUN_ACTIVITY_MAX = 20;

/** The receipt's action log (`activity`), checked line by line: a line with
 *  words, its tone one of the three, at most 20. Anything else is left out,
 *  never guessed. Takes the raw value (a saved thread is untrusted). */
export function runActivity(result: { activity?: unknown } | null | undefined): RunActivityLine[] {
  const out: RunActivityLine[] = [];
  const raw = result && Array.isArray(result.activity) ? (result.activity as unknown[]) : [];
  for (const entry of raw) {
    if (out.length >= RUN_ACTIVITY_MAX) break;
    const a = entry as { text?: unknown; tone?: unknown } | null;
    const text = a && typeof a.text === "string" ? a.text.replace(/\s+/g, " ").trim() : "";
    if (!text) continue;
    const tone = a!.tone === "warn" || a!.tone === "ask" ? (a!.tone as "warn" | "ask") : "ok";
    out.push({ text: text.length > 200 ? `${text.slice(0, 199)}…` : text, tone });
  }
  return out;
}

/** The toggle's words: "What it did", or "The last 20 things it did" when
 *  the log was cut to its newest 20. Under the settled row's own action
 *  lines (which already say what it did, counted) the same fold reads "Step
 *  by step" / "The last 20 steps". */
export function activityToggleWords(n: number, under: "card" | "row" = "card"): string {
  if (under === "row") return n >= RUN_ACTIVITY_MAX ? "The last 20 steps" : "Step by step";
  return n >= RUN_ACTIVITY_MAX ? "The last 20 things it did" : "What it did";
}

/** The folded log: one quiet toggle, then the lines when opened. Nothing at
 *  all for an empty log. `testIds` keep the run card's existing ids. */
export function RunActivityFold({
  lines,
  under = "card",
  toggleTestId = "run-result-steps-toggle",
  listTestId = "run-result-activity",
}: {
  lines: readonly RunActivityLine[];
  /** Where it is drawn: the run card ("What it did") or the settled row,
   *  under its action lines ("Step by step"). */
  under?: "card" | "row";
  toggleTestId?: string;
  listTestId?: string;
}) {
  const [open, setOpen] = useState(false);
  const listId = useId();
  if (!lines.length) return null;
  return (
    <div>
      <button
        type="button"
        data-testid={toggleTestId}
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-1.5 text-[12px] text-zinc-500 transition-colors hover:text-zinc-300 focus-visible:text-zinc-300 focus-visible:outline-none motion-reduce:transition-none"
      >
        {open ? <ChevronDown size={13} aria-hidden="true" /> : <ChevronRight size={13} aria-hidden="true" />}
        {activityToggleWords(lines.length, under)}
      </button>
      {open && (
        <ul id={listId} data-testid={listTestId} className="mt-1.5 grid gap-1">
          {lines.map((a, i) => (
            <li
              key={i}
              data-tone={a.tone}
              className={`flex min-w-0 items-start gap-2 text-[12px] leading-5 ${
                a.tone === "warn" ? "text-tone-warn" : "text-zinc-400"
              }`}
            >
              {a.tone === "warn" ? (
                <AlertTriangle size={13} aria-hidden="true" className="mt-[3px] shrink-0" />
              ) : a.tone === "ask" ? (
                <Hand size={13} aria-hidden="true" className="mt-[3px] shrink-0 text-zinc-500" />
              ) : (
                <Check size={13} aria-hidden="true" className="mt-[3px] shrink-0 text-zinc-500" />
              )}
              <span className="min-w-0 break-words">{a.text}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
