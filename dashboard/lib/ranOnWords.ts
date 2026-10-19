/**
 * WHAT A RUN RAN ON, in the chat's words (v1.352.0, product-readiness G23).
 *
 * The mission receipt and every teammate card printed the raw stamp — "Answered
 * by custom · fleet" — where the chat's receipt names the model and the model
 * menu names the endpoint the user set up ("DGX Spark"). This joins the two
 * existing readers instead of keeping a third:
 *
 *   the MODEL  — lib/answeredModel (the catalog's own label, else the name
 *                the id spells, else the id exactly as it is);
 *   the PLACE  — the endpoint's name from the same GET /models catalog the
 *                model menu reads (`name`, set for a custom endpoint or a
 *                fleet box), else lib/onboarding.providerDisplay ("Claude
 *                Code", "your own endpoint").
 *
 * So a local fleet run reads "fleet on DGX Spark" and a subscription run
 * "Opus 5.5 on Claude Code". Nothing is invented: an id nobody named stays as
 * it is, and the raw `provider · model` pair rides along as the line's title
 * (the record of what the daemon stamped).
 *
 * A config-seeded endpoint is its own provider ("custom", "ollama") while the
 * catalog also lists it as the fleet node `fleet-<id>` carrying the user's
 * label (routes/connections `_SLOT_TWINS`; the Connections page addresses the
 * same pair), so a "custom" run finds its label on the `fleet-custom` rows.
 *
 * Pure: no React, no fetch. The caller hands in the catalog rows it already
 * holds (lib/useModels).
 */

import { answeredModelName } from "@/lib/answeredModel";
import { providerDisplay } from "@/lib/onboarding";

/** The catalog fields this reads (a GET /models row). */
export interface RanOnRow {
  provider: string;
  model: string;
  label?: string;
  name?: string | null;
}

/** The rows of a catalog that may not have arrived (or a caller that hands
 *  in something else): always a list. */
function rowsOf(catalog: readonly RanOnRow[] | null | undefined): readonly RanOnRow[] {
  return Array.isArray(catalog) ? catalog : [];
}

/** The name the user gave the endpoint a provider id points at, else the
 *  provider's plain word, else "". */
export function endpointLabel(
  provider: string | null | undefined,
  catalog: readonly RanOnRow[] | null | undefined,
): string {
  const p = (provider || "").trim();
  if (!p) return "";
  const rows = rowsOf(catalog);
  const named = (id: string): string => {
    for (const row of rows) {
      if (row.provider === id && typeof row.name === "string" && row.name.trim()) return row.name.trim();
    }
    return "";
  };
  return named(p) || (p.startsWith("fleet-") ? "" : named(`fleet-${p}`)) || providerDisplay(p);
}

export interface RanOn {
  /** "fleet on DGX Spark" — the words after the receipt's verb. "" = nothing known. */
  text: string;
  /** The raw stamp ("custom · fleet"), for the line's title. */
  title: string;
}

/** The words for a run stamped `provider` / `model`. */
export function ranOnWords(
  provider: string | null | undefined,
  model: string | null | undefined,
  catalog: readonly RanOnRow[] | null | undefined,
): RanOn {
  const p = (provider || "").trim();
  const m = (model || "").trim();
  const rows = rowsOf(catalog);
  const where = endpointLabel(p, rows);
  const name = m ? (answeredModelName({ provider: p, model: m }, rows) ?? m) : "";
  const text = name && where && name !== where ? `${name} on ${where}` : name || where;
  const title = [p, m].filter((x, i, all) => x && all.indexOf(x) === i).join(" · ");
  return { text, title };
}

/** How long a finished run took, in milliseconds, from its own two
 *  timestamps. The caller says it in the chat receipt's clock words
 *  (AgentCards.memberTook hands this to TurnReceipt's `secondsText`, giving
 *  "2 min 5 s"), so this file stays free of the chat component. Null when
 *  either end is missing or unreadable, or the ends are out of order: no
 *  guessed duration. Both stamps come from the daemon in the same form, so
 *  their difference is right whatever zone they are read in. */
export function tookMs(startedAt: string | null | undefined, finishedAt: string | null | undefined): number | null {
  if (!startedAt || !finishedAt) return null;
  const a = Date.parse(startedAt);
  const b = Date.parse(finishedAt);
  if (!Number.isFinite(a) || !Number.isFinite(b) || b < a) return null;
  return b - a;
}
