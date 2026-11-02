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
import { modelRowName, type NamedRow } from "@/lib/onboarding";

/** A window this size or larger gets the "long docs" chip (v1.356.0, G75:
 *  it read "1M", a number only an engineer could read). */
export const ONE_MILLION = 1_000_000;

/** v1.424.0 review (D1): a model that draws usage credits, in the menu's own
 *  billing words ("cloud · pay per use"), and what that means for failover. */
export const CREDITS_CHIP = "pay per use";
export const CREDITS_TITLE =
  "This model draws from your plan's pay-as-you-go usage credits, so each message is billed. " +
  "Iron Jarvis counts it as pay per use: if you pick it and it fails, another model that bills " +
  "per use may answer instead.";

type RowFacts = Pick<ModelOption, "model" | "label" | "context_window" | "usage_credits">;

/** What a menu row calls the model: the picker's label when it sent one,
 *  else words (v1.359.0, lane 9B; lib/onboarding.modelRowName): the live
 *  menu printed "subscription" under Claude Code and Codex, and "fleet" /
 *  "spark/fleet" for the user's own server. Those now read "Your Claude
 *  plan", "Your ChatGPT plan" and "Your model server", and an unlabelled
 *  Claude id reads the way the composer's chip already says it ("Opus 4.8").
 *  The raw id is the row's title (`modelTitle`).
 *
 *  v1.360.0 (lane 10B fix): `catalog` is the /models rows the menu already
 *  holds. With it, the config slot's "fleet" row reads the machine's name
 *  ("DGX Spark", its fleet twin's label) exactly as the reply, the mission
 *  screen and the run page say it; without it, "Your model server". */
export function modelText(
  m: Pick<ModelOption, "provider" | "model" | "label">,
  catalog?: readonly NamedRow[] | null,
): string {
  return modelRowName(m, catalog);
}

/** The raw id behind a row's words, for its title: the record of what runs.
 *  Undefined when the words ARE the id (nothing to add on hover). */
export function modelTitle(
  m: Pick<ModelOption, "provider" | "model" | "label">,
  catalog?: readonly NamedRow[] | null,
): string | undefined {
  return modelText(m, catalog) !== m.model ? m.model : undefined;
}

/** The chips after a row's name: "long docs" for a million-token window, and a
 *  muted "pay per use" note when the model draws from usage credits on
 *  this plan. Renders nothing when neither applies.
 *
 *  v1.424.0 review (the owner's decision D1): a model that draws pay-as-you-go
 *  usage credits IS pay per use — real money each message (it has counted as
 *  billed since v1.300.0), and the failover rule (`providers/billing.py`, the
 *  one definition) treats it so: picked, it may fail over to other models
 *  that bill per use, and it never stands in for a pick that does not. The
 *  row under "cloud · on your plan" used to say only "uses credits", so the
 *  menu and the rule disagreed; it now says it in the menu's own words. */
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
          title={CREDITS_TITLE}
          className="shrink-0 font-sans text-[10px] text-zinc-500"
        >
          {CREDITS_CHIP}
        </span>
      )}
    </>
  );
}
