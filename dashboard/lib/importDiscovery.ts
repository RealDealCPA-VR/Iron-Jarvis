/**
 * v1.353.0 (G42, "one Connections home"): the Directory's one discovery line
 * about the tools the user's OTHER apps already use, read off the SAME
 * `GET /mcp/import/sources` answer the Tools page's "Import from your other
 * apps" door reads. Nothing here adds anything: the line names what was
 * found; the user presses Import and picks, through that door.
 *
 * Pure and dependency-free (lib/api.ts is mocked wholesale by ~71 test files,
 * so helpers live in their own file).
 */

import type { ImportSource } from "@/lib/mcpImport";

/** What the scan found, counted by tool NAME (one server listed by two apps
 *  is one tool). `fresh` = not added here yet. */
export interface Discovery {
  /** Distinct tools found in every readable app. */
  found: number;
  /** Of those, the ones not added to Iron Jarvis yet. */
  fresh: number;
  /** The apps that hold at least one tool not added yet, in scan order. */
  freshApps: string[];
  /** The apps that hold any tool at all, in scan order. */
  foundApps: string[];
  /** Every app the scan looked in, in scan order. */
  checkedApps: string[];
}

/** An app's own name for a source: Claude Code's three files are one app
 *  ("Claude Code settings", "Claude Code (this project)" → "Claude Code"). */
export function appName(source: Pick<ImportSource, "source" | "label">): string {
  if ((source.source ?? "").startsWith("claude-code")) return "Claude Code";
  const label = (source.label ?? "").replace(/\s*\(.*\)\s*$/, "").replace(/\s+settings$/i, "").trim();
  return label || source.source || "another app";
}

function pushOnce(list: string[], name: string) {
  if (!list.includes(name)) list.push(name);
}

/** Count what `GET /mcp/import/sources` found. Never throws on a partial row. */
export function discover(sources: ImportSource[] | null | undefined): Discovery {
  const found = new Set<string>();
  const fresh = new Set<string>();
  const freshApps: string[] = [];
  const foundApps: string[] = [];
  const checkedApps: string[] = [];
  for (const s of sources ?? []) {
    if (!s) continue;
    const app = appName(s);
    pushOnce(checkedApps, app);
    if (!s.readable) continue;
    for (const row of s.servers ?? []) {
      const name = (row?.name ?? "").trim();
      if (!name) continue;
      found.add(name);
      pushOnce(foundApps, app);
      if (!row.exists) {
        fresh.add(name);
        pushOnce(freshApps, app);
      }
    }
  }
  return { found: found.size, fresh: fresh.size, freshApps, foundApps, checkedApps };
}

/** "A", "A and B", "A, B and C" (`or` for a list of places nothing came from). */
export function listWords(names: string[], last: "and" | "or" = "and"): string {
  if (names.length <= 1) return names[0] ?? "";
  return `${names.slice(0, -1).join(", ")} ${last} ${names[names.length - 1]}`;
}

/** The line, in the user's words. "" when nothing was scanned at all. */
export function discoveryLine(d: Discovery): string {
  if (d.fresh > 0) {
    return `Found ${d.fresh} tool${d.fresh === 1 ? "" : "s"} in ${listWords(d.freshApps)}.`;
  }
  if (d.found > 0) {
    return `Everything found in ${listWords(d.foundApps)} is already added.`;
  }
  if (d.checkedApps.length > 0) {
    return `No tools found in ${listWords(d.checkedApps, "or")}.`;
  }
  return "";
}
