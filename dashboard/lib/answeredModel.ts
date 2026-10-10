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
 */

import { friendlyModelName } from "@/lib/friendlyModelName";

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
  return label || friendlyModelName(model);
}
