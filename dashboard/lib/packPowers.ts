/**
 * v1.355.0 (G70): what a CONNECTED app can actually do, in one quiet line on
 * its Directory card — read off the daemon's row (`connectors/service.
 * pack_powers`), never the catalog's hand-written scopes:
 *
 *   "Can read · Can change (12 tools) · Asks before changing anything"
 *
 * - `can_change` counts the loaded tools that can change things (the tool's
 *   own `write_like`: an MCP `readOnlyHint` makes it read-only);
 * - `asks_first` is false only when app tools run without asking (the
 *   "Run app tools without asking" switch or a pack's own auto-approve);
 * - `held_back` tools arrived since the app was connected and ask until
 *   trusted, whatever the switch says.
 *
 * There is no separate permission model: these are the things the app
 * already enforces. Plain data (no React) so the card and its tests read the
 * same words. `null` when the row carries no counts (an older daemon, or an
 * app with no tools loaded — its status line speaks then).
 */

export interface PackPowers {
  tools_loaded?: number | null;
  can_change?: number | null;
  asks_first?: boolean | null;
  held_back?: number | null;
}

function toolCount(n: number): string {
  return `${n} tool${n === 1 ? "" : "s"}`;
}

export function packPowersLine(row: PackPowers | null | undefined): string | null {
  const total = row?.tools_loaded;
  const change = row?.can_change;
  if (typeof total !== "number" || typeof change !== "number" || total <= 0) return null;
  const n = Math.max(0, Math.min(change, total));
  const parts: string[] = [];
  if (n < total) parts.push("Can read");
  if (n === 0) {
    parts.push("Changes nothing");
    return parts.join(" · ");
  }
  parts.push(`Can change (${toolCount(n)})`);
  if (row?.asks_first === false) {
    const held = typeof row?.held_back === "number" ? row.held_back : 0;
    parts.push(
      held > 0
        ? `Changes things without asking, except ${toolCount(held)} added since, which ask first`
        : "Changes things without asking",
    );
  } else {
    parts.push("Asks before changing anything");
  }
  return parts.join(" · ");
}
