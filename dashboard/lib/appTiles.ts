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

import { NAV, type NavEntry } from "./nav";

const USE_KEY = "ironjarvis.overview.usage";
const ORDER_KEY = "ironjarvis.overview.order";
const DOOR_KEY = "ironjarvis.doors.usage";

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
