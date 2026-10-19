"use client";

/**
 * The small facts a model-menu row can carry (v1.300.0) — ONE renderer shared
 * by the composer's model menu (app/chat/page.tsx) and the title bar's
 * ModelSwitcher, so the two pickers cannot describe the same model two ways.
 *
 * The Claude subscription's live picker gives each row a human `label`
 * ("Opus 5.5"), a `context_window`, and whether it draws from pay-as-you-go
 * `usage_credits`. All optional on the wire: a row without them gets no
 * chips, and (v1.359.0) its name in words, never the raw id.
 */

import type { ModelOption } from "@/lib/types";
import { LONG_DOCS_CHIP, LONG_DOCS_TITLE } from "@/lib/chatControlWords";
import { modelRowName } from "@/lib/onboarding";

/** A window this size or larger gets the "long docs" chip (v1.356.0, G75:
 *  it read "1M", a number only an engineer could read). */
export const ONE_MILLION = 1_000_000;

type RowFacts = Pick<ModelOption, "model" | "label" | "context_window" | "usage_credits">;

/** What a menu row calls the model: the picker's label when it sent one,
 *  else words (v1.359.0, lane 9B; lib/onboarding.modelRowName): the live
 *  menu printed "subscription" under Claude Code and Codex, and "fleet" /
 *  "spark/fleet" for the user's own server. Those now read "Your Claude
 *  plan", "Your ChatGPT plan" and "Your model server", and an unlabelled
 *  Claude id reads the way the composer's chip already says it ("Opus 4.8").
 *  The raw id is the row's title (`modelTitle`). */
export function modelText(m: Pick<ModelOption, "provider" | "model" | "label">): string {
  return modelRowName(m);
}

/** The raw id behind a row's words, for its title: the record of what runs.
 *  Undefined when the words ARE the id (nothing to add on hover). */
export function modelTitle(m: Pick<ModelOption, "provider" | "model" | "label">): string | undefined {
  return modelText(m) !== m.model ? m.model : undefined;
}

/** The chips after a row's name: "long docs" for a million-token window, and a
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
          title={LONG_DOCS_TITLE}
          className="shrink-0 rounded border border-white/10 px-1 font-sans text-[10px] leading-tight text-zinc-400"
        >
          {LONG_DOCS_CHIP}
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
