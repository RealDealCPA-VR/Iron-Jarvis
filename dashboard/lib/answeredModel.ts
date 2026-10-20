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
 *
 * v1.360.0 (lane 10B, the final re-audit): the "fleet" alias in front of the
 * user's own model server is ONE name everywhere — the machine's name when
 * the user gave it one (the route's own `label`, the one the daemon now
 * sends for the custom endpoint too, else the catalog's: lib/onboarding
 * `machineName`), else "your model server" — never "answered by fleet"
 * while the model menu said "Your model server" for the same pick.
 */

import { friendlyModelName } from "@/lib/friendlyModelName";
import {
  isProviderDefaultModel,
  isServerAlias,
  machineName,
  providerDisplay,
  SERVER_PICK_WORDS,
} from "@/lib/onboarding";

export interface AnsweredRoute {
  provider: string;
  model?: string;
  /** v1.360.0: the name the user gave the endpoint that answered (the
   *  daemon's `route.label`; absent on older replies and on runs). */
  label?: string;
}

export interface CatalogRow {
  provider: string;
  model: string;
  label?: string;
  /** v1.360.0: the endpoint's name (a GET /models row's `name`). */
  name?: string | null;
}

/** The server alias's name for a route (v1.360.0, lane 10B): the machine's
 *  name when known, else "your model server". */
export function serverPickName(route: AnsweredRoute, models: readonly CatalogRow[]): string {
  return (
    machineName(route.provider, { label: route.label, model: route.model, catalog: models }) ||
    SERVER_PICK_WORDS
  );
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
  if (isServerAlias(model)) return serverPickName(route, models);
  return friendlyModelName(model);
}
