/**
 * The Overview's app grid: which tiles, in what order (v1.151.0).
 *
 * Two things decide the order, and the split matters:
 *
 * 1. **Your own arrangement wins.** Once you drag a tile, that layout is the
 *    layout — it never gets silently re-sorted underneath you because usage
 *    shifted. A desktop that rearranges itself is not a desktop.
 * 2. **Until then, most-used first.** Openings are counted LOCALLY (this
 *    browser's localStorage) because that is genuinely your usage of your
 *    machine, it needs no daemon round-trip to render the first paint, and it
 *    never leaves the device. Ties fall back to the nav catalogue's own order,
 *    so a fresh install is the curated order rather than an arbitrary one.
 *
 * The tile catalogue itself is NOT a new list — it is `lib/nav.ts`, which
 * already carries every page's icon, label and one-line blurb and is already
 * the single source of truth for "what pages exist". A second list here would
 * drift the moment a page is added, and the hover detail would go stale.
 */

import { Briefcase, Cog, Cpu, Layers, type LucideIcon } from "lucide-react";
import { NAV, type NavEntry } from "./nav";
import { markNavPress } from "./visitVia";

const USE_KEY = "ironjarvis.overview.usage";
const ORDER_KEY = "ironjarvis.overview.order";
const DOOR_KEY = "ironjarvis.doors.usage";
const GROUP_KEY = "ironjarvis.overview.group";

/** Pages that are not "apps" — reached from chrome, not from the desktop. */
const NOT_APPS = new Set<string>(["/", "/help", "/updates"]);

export interface AppTile extends NavEntry {
  /** Times this page has been opened on this machine. */
  opens: number;
  /** Section it belongs to in the nav catalogue ("Work", "Knowledge", …). */
  section: string;
}

function readJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback; // corrupt/blocked storage just means "no preference yet"
  }
}

function writeJson(key: string, value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode / quota — the grid still works, it just won't remember */
  }
}

/** Record that a module was opened. Called from the nav, once per navigation. */
export function recordOpen(href: string): void {
  // Redesign S0: every nav press also tags the coming page visit as "nav".
  markNavPress();
  if (!href || NOT_APPS.has(href)) return;
  const counts = readJson<Record<string, number>>(USE_KEY, {});
  counts[href] = (counts[href] ?? 0) + 1;
  writeJson(USE_KEY, counts);
}

export function readUsage(): Record<string, number> {
  return readJson<Record<string, number>>(USE_KEY, {});
}

/**
 * Record that a DOOR under a chat reply was opened (v1.199.0).
 *
 * This is the local, never-leaves-the-machine counter for the
 * emergent-surface metric ("touched N subsystems without the nav"). Nav
 * opens already count in `ironjarvis.overview.usage` via `recordOpen`;
 * doors count HERE, under their own key, so the two paths stay
 * distinguishable — one merged tally could never say whether a subsystem
 * was reached through the sidebar or through the work itself.
 */
export function recordDoorOpen(href: string): void {
  if (!href) return;
  const counts = readJson<Record<string, number>>(DOOR_KEY, {});
  counts[href] = (counts[href] ?? 0) + 1;
  writeJson(DOOR_KEY, counts);
}

export function readOrder(): string[] {
  const raw = readJson<string[]>(ORDER_KEY, []);
  return Array.isArray(raw) ? raw.filter((h) => typeof h === "string") : [];
}

export function writeOrder(order: string[]): void {
  writeJson(ORDER_KEY, order);
}

export function clearOrder(): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.removeItem(ORDER_KEY);
  } catch {
    /* nothing to clear */
  }
}

/** Every module that belongs on the desktop, flattened out of the nav. */
export function allTiles(): AppTile[] {
  const out: AppTile[] = [];
  for (const section of NAV) {
    for (const item of section.items) {
      if (NOT_APPS.has(item.href)) continue;
      out.push({ ...item, opens: 0, section: section.label });
    }
  }
  return out;
}

/**
 * The tiles in display order.
 *
 * A saved arrangement is applied FIRST and verbatim; anything it doesn't
 * mention (a module added by a later version, or one you never dragged) keeps
 * its usage/catalogue position after it. That is what stops an upgrade from
 * either hiding a new page or quietly reshuffling a layout you set.
 */
export function orderedTiles(
  usage: Record<string, number> = {},
  order: string[] = [],
): AppTile[] {
  const tiles = allTiles().map((t) => ({ ...t, opens: usage[t.href] ?? 0 }));
  const index = new Map(tiles.map((t) => [t.href, t]));
  const seen = new Set<string>();
  const pinned: AppTile[] = [];
  for (const href of order) {
    const hit = index.get(href);
    if (hit && !seen.has(href)) {
      pinned.push(hit);
      seen.add(href);
    }
  }
  const rest = tiles.filter((t) => !seen.has(t.href));
  // Catalogue order is the tie-break, so an untouched install shows the
  // curated arrangement rather than an alphabetical or random one.
  const catalogue = new Map(allTiles().map((t, i) => [t.href, i]));
  rest.sort((a, b) => {
    if (b.opens !== a.opens) return b.opens - a.opens;
    return (catalogue.get(a.href) ?? 0) - (catalogue.get(b.href) ?? 0);
  });
  return [...pinned, ...rest];
}

/* ---------------------------------------------------------------------------
 * Groups (v1.293.0 as swipeable screens; v1.294.0 as three large icons that
 * open in place): the desktop is three groups of ten, not one wall of thirty.
 *
 * The grouping is by what the tiles are FOR to the person using them, not by
 * which nav section they live in (that split — Work / Automate / Knowledge —
 * is the rail's, and it is what the hover card still names):
 *
 *   Office      — where the work happens: talk, build, create, and what
 *                 Jarvis knows about you and your files.
 *   Operations  — agents and automation working on your behalf.
 *   System      — under the hood: what ran, what it cost, accounts, endpoints,
 *                 settings.
 *
 * Three rules, each pinned by a test:
 *
 * 1. **Every tile has exactly one home, and no group holds more than ten.**
 *    Ten is the ask ("group them in 10 tiles"), and it is also one row of the
 *    widest grid. A page added to the nav without a group here does not
 *    vanish — it lands in a trailing "More" group (the v1.151.1 lesson: an
 *    unreachable page may as well not ship) — and the test fails until it is
 *    placed, on purpose.
 * 2. **The order INSIDE a group is the order it always was.** `orderedTiles`
 *    still decides (your arrangement first, then most-used, then the
 *    catalogue); a group is that list filtered to its own tiles. Nothing here
 *    re-sorts anything.
 * 3. **A rearrangement is still ONE saved list.** Dragging a tile inside a
 *    group rewrites the flat order with that group's tiles in their new
 *    sequence and every other group's tile exactly where it was, so the
 *    storage key, its shape and `orderedTiles` are untouched — an
 *    arrangement saved before v1.293.0 renders the same tiles in the same
 *    relative order, just behind three doors.
 * ------------------------------------------------------------------------- */

/** The most tiles a group is allowed to hold. */
export const GROUP_SIZE = 10;

export interface TileGroupDef {
  /** Stable id — the remembered open group is stored by key, never by index. */
  key: string;
  label: string;
  /** One line under the icon: what this group is for. */
  hint: string;
  icon: LucideIcon;
  hrefs: readonly string[];
}

export const TILE_GROUPS: readonly TileGroupDef[] = [
  {
    key: "office",
    label: "Office",
    hint: "Where the work happens — talk, build, create, and what Jarvis knows about you and your files.",
    icon: Briefcase,
    hrefs: [
      "/chat",
      "/terminals",
      "/projects",
      "/creative",
      "/documents",
      "/filesearch",
      "/memory",
      "/you",
      "/train",
      "/templates",
    ],
  },
  {
    key: "operations",
    label: "Operations",
    hint: "Agents and automation working on your behalf — and the switches that keep them in check.",
    icon: Cog,
    hrefs: [
      "/agents",
      "/workflows",
      "/schedules",
      "/tools",
      "/skills",
      "/autonomy",
      "/sentinels",
      "/reflex",
      "/webhooks",
      "/computeruse",
    ],
  },
  {
    key: "system",
    label: "System",
    hint: "Under the hood — what ran, what it cost, your accounts and endpoints, and the settings.",
    icon: Cpu,
    hrefs: [
      "/sessions",
      "/activity",
      "/artifacts",
      "/usage",
      "/connections",
      "/fleet",
      "/secrets",
      "/channels",
      "/settings",
      "/self-dev",
    ],
  },
];

/** Where a tile no group has claimed lands, so a new page is never hidden. */
export const UNGROUPED: TileGroupDef = {
  key: "more",
  label: "More",
  hint: "Modules added since the groups were drawn.",
  icon: Layers,
  hrefs: [],
};

const GROUP_OF = new Map<string, TileGroupDef>();
for (const g of TILE_GROUPS) for (const href of g.hrefs) GROUP_OF.set(href, g);

/** The group a page belongs to, or the "More" fallback. */
export function groupOf(href: string): TileGroupDef {
  return GROUP_OF.get(href) ?? UNGROUPED;
}

export interface TileGroup {
  group: TileGroupDef;
  tiles: AppTile[];
}

/**
 * The groups, in catalogue order, each holding its tiles IN THE ORDER GIVEN.
 * Pass `orderedTiles(...)` and every group inherits the arrangement/usage
 * order for free. A group with no tiles is left out; "More" appears only
 * when something is unplaced.
 */
export function groupedTiles(tiles: AppTile[]): TileGroup[] {
  const buckets = new Map<string, AppTile[]>();
  for (const g of TILE_GROUPS) buckets.set(g.key, []);
  buckets.set(UNGROUPED.key, []);
  for (const t of tiles) buckets.get(groupOf(t.href).key)!.push(t);
  const out: TileGroup[] = [];
  for (const g of [...TILE_GROUPS, UNGROUPED]) {
    const bucket = buckets.get(g.key)!;
    if (bucket.length > 0) out.push({ group: g, tiles: bucket });
  }
  return out;
}

/**
 * The FULL flat order after moving one tile within its group.
 *
 * `flat` is every tile in display order; `groupIds` is one group's tiles in
 * their current order; `from`/`to` are indexes within the group. The group's
 * tiles are re-sequenced INTO THE SLOTS THEY ALREADY OCCUPY in the flat
 * list, so tiles in other groups never move. Out-of-range indexes return the
 * flat list untouched.
 */
export function reorderWithinGroup(
  flat: string[],
  groupIds: string[],
  from: number,
  to: number,
): string[] {
  if (from < 0 || to < 0 || from >= groupIds.length || to >= groupIds.length) return flat;
  const moved = groupIds.slice();
  const [item] = moved.splice(from, 1);
  moved.splice(to, 0, item);
  const mine = new Set(groupIds);
  let i = 0;
  return flat.map((href) => (mine.has(href) ? moved[i++] : href));
}

/** The group the desktop was left open on (a key), or null = closed. Local only. */
export function readOpenGroup(): string | null {
  const raw = readJson<unknown>(GROUP_KEY, null);
  return typeof raw === "string" ? raw : null;
}

/** Remember the open group; `null` remembers "closed" (the three icons). */
export function writeOpenGroup(key: string | null): void {
  if (key === null) {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.removeItem(GROUP_KEY);
    } catch {
      /* nothing to clear */
    }
    return;
  }
  writeJson(GROUP_KEY, key);
}
