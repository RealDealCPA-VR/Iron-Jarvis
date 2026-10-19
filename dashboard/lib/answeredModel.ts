/**
 * Calm chat wave 5 G2 (v1.329.0): what a reply's "answered by …" calls the
 * model, so the reply and the composer's model chip use ONE name.
 *
 * The chip said "Opus 4.8" (lib/friendlyModelName) while the reply right above
 * it said "answered by claude-opus-4-8". Now both follow the same rule: the
 * catalog's own label when the row has one ("Opus 5.5", the words the model
 * menu uses), else the name the id spells (friendlyModelName; an id it cannot
 * read stays exactly as it is, nothing invented). The raw id is not lost: the
 * receipt keeps it in the line's title and in the expanded detail.
 *
 * No model on the route: undefined, and the receipt names the provider.
 *
 * v1.359.0 (lane 9B): a route whose model is the provider's own default
 * ("subscription" under Claude Code and Codex) is answered by THAT
 * provider: "answered by Claude Code", never "answered by subscription".
 */

import { friendlyModelName } from "@/lib/friendlyModelName";
import { isProviderDefaultModel, providerDisplay } from "@/lib/onboarding";

export interface AnsweredRoute {
  provider: string;
  model?: string;
}

export interface CatalogRow {
  provider: string;
  model: string;
  label?: string;
}

export function answeredModelName(
  route: AnsweredRoute | null | undefined,
  models: readonly CatalogRow[],
): string | undefined {
  const model = (route?.model ?? "").trim();
  if (!route || !model) return undefined;
  const row = models.find((x) => x.provider === route.provider && x.model === model);
  const label = typeof row?.label === "string" ? row.label.trim() : "";
  if (label) return label;
  if (isProviderDefaultModel(model)) {
    const who = providerDisplay(route.provider);
    if (who && who !== route.provider) return who;
  }
  return friendlyModelName(model);
}
