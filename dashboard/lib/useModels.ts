"use client";

import { useApi, type ApiState } from "./useApi";
import type { ModelOption } from "./types";

/**
 * THE MODEL CATALOG, loaded once per window (v1.250.0, S-02 dashboard half).
 *
 * Seven surfaces asked `/models` for themselves — the title bar's ModelSwitcher
 * (mounted in the layout, so it is always one of them), the chat page, Agents,
 * Templates, Projects, Build, and the new-session form — and each one paid a
 * fresh request on mount. The switcher has usually already loaded the answer
 * before any page mounts, so every other surface was re-fetching a payload the
 * window was holding, and showed a placeholder while it did.
 *
 * This is deliberately a THIN wrapper over `useApi`, not a provider or a second
 * cache: `useApi` seeds from the module-level payload cache (S-04) and
 * revalidates behind it with a conditional GET, so the sharing already works
 * for anyone on the same path. What the hook adds is ONE path string and one
 * shape, so a caller cannot spell it differently, plus the two things every
 * caller repeated by hand: default to an empty list, and keep `available`
 * meaningful (a model the daemon never rated is NOT offline).
 *
 * Freshness is unchanged: the switcher still calls `reload()` when its panel
 * opens, which refills the cache every other reader is seeded from.
 */
export interface ModelCatalog extends ApiState<{ models: ModelOption[] }> {
  /** Every model the daemon offers, connected or not. Never null: a surface
   *  that renders a picker wants a list, and "no catalog yet" is an empty one.
   *  Unavailable entries are KEPT — v1.148.0 removed the filter that hid them,
   *  because "why isn't my model in the list?" answered with silence is worse
   *  than a labelled row the user cannot pick. */
  models: ModelOption[];
  /** Only the ones a turn could actually reach right now. `available !== false`
   *  rather than `=== true`: an older daemon omits the field entirely, and
   *  treating unknown as offline emptied the pickers on those installs. */
  usable: ModelOption[];
}

export function useModels(): ModelCatalog {
  const state = useApi<{ models: ModelOption[] }>("/models");
  const models = state.data?.models ?? [];
  return {
    ...state,
    models,
    usable: models.filter((m) => m.available !== false),
  };
}
