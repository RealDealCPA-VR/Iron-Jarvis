"use client";

/**
 * The small facts a model-menu row can carry (v1.300.0) — ONE renderer shared
 * by the composer's model menu (app/chat/page.tsx) and the title bar's
 * ModelSwitcher, so the two pickers cannot describe the same model two ways.
 *
 * The Claude subscription's live picker gives each row a human `label`
 * ("Opus 5.5"), a `context_window`, and whether it draws from pay-as-you-go
 * `usage_credits`. All optional on the wire: a row without them renders
 * exactly as before (the raw id, no chips).
 */

import type { ModelOption } from "@/lib/types";

/** A window this size or larger gets the "1M" chip. */
export const ONE_MILLION = 1_000_000;

type RowFacts = Pick<ModelOption, "model" | "label" | "context_window" | "usage_credits">;

/** What a menu row calls the model: the picker's label when it sent one,
 *  else the raw id (today's look). */
export function modelText(m: Pick<ModelOption, "model" | "label">): string {
  const label = typeof m.label === "string" ? m.label.trim() : "";
  return label || m.model;
}

/** The chips after a row's name: "1M" for a million-token window, and a
 *  muted "uses credits" note when the model draws from usage credits on
 *  this plan. Renders nothing when neither applies. */
export function ModelRowChips({ m }: { m: RowFacts }) {
  const big =
    typeof m.context_window === "number" &&
    Number.isFinite(m.context_window) &&
    m.context_window >= ONE_MILLION;
  const credits = m.usage_credits === true;
  if (!big && !credits) return null;
  return (
    <>
      {big && (
        <span
          data-testid="model-1m"
          title="1M-token context window"
          className="shrink-0 rounded border border-white/10 px-1 font-sans text-[9.5px] leading-tight text-zinc-400"
        >
          1M
        </span>
      )}
      {credits && (
        <span
          data-testid="model-credits"
          title="This model draws from your plan's pay-as-you-go usage credits"
          className="shrink-0 font-sans text-[10px] text-zinc-500"
        >
          uses credits
        </span>
      )}
    </>
  );
}
