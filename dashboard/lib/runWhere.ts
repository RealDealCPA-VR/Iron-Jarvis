/**
 * Did a run's model stay on the user's network? (v1.420.0, C6.7)
 *
 * The mission screen named the model a run answered on ("Answered by glm on
 * DGX Spark") but never said local or cloud — the one fact the chat's own
 * receipt always says (TurnReceipt `replyWhere`, v1.352.0). The answer is the
 * catalog row's `on_this_network` for that exact provider and model: the
 * daemon fills it from `egress.stays_on_this_network`, the SAME predicate the
 * session receipt's `on_this_network` is computed by, and the page already
 * holds the catalog (lib/useModels), so no extra read is made per poll.
 *
 * Exact pair only: a proxy can send one model to the cloud and keep another
 * local (v1.362.0), so a provider's other rows never answer for a model. A
 * config-seeded endpoint ("custom", "ollama") is also listed as its fleet twin
 * (`fleet-<slot>`), which is read too. Nothing found = null (say nothing).
 * The demo model is never local or cloud.
 *
 * Pure: no React, no fetch.
 */

export interface WhereRow {
  provider: string;
  model: string;
  on_this_network?: boolean;
}

export function onThisNetworkOf(
  provider: string | null | undefined,
  model: string | null | undefined,
  catalog: readonly WhereRow[] | null | undefined,
): boolean | null {
  const p = (provider ?? "").trim();
  const m = (model ?? "").trim();
  if (!p || !m || p === "mock" || !Array.isArray(catalog)) return null;
  const ids = p.startsWith("fleet-") ? [p] : [p, `fleet-${p}`];
  for (const id of ids) {
    for (const row of catalog) {
      if (row?.provider !== id || (row.model ?? "").trim() !== m) continue;
      if (typeof row.on_this_network === "boolean") return row.on_this_network;
    }
  }
  return null;
}
