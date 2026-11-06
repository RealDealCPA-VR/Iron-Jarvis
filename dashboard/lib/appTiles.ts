/**
 * Local counters of which pages were opened (v1.151.0; trimmed v1.439.0).
 *
 * This file used to hold the Overview's tile grid too — its catalogue, the
 * saved arrangement and the three groups (v1.293.0/v1.294.0). The grid left
 * the screen in v1.334.0 (Everything › Status became the system map) and its
 * module, `components/overview/AppGrid.tsx`, was removed in v1.439.0 together
 * with the helpers only it read. What is left is what live code calls:
 *
 * - `recordOpen` — every nav press (sidebar, Everything, Ctrl K, the system
 *   map) counts the page AND tags the coming page visit as "nav".
 * - `recordDoorOpen` — a door under a chat reply counts under its own key, so
 *   "reached through the work" stays separate from "reached through the nav".
 *
 * Openings are counted LOCALLY (this browser's localStorage): they need no
 * daemon round-trip and never leave the device.
 */

import { markNavPress } from "./visitVia";

const USE_KEY = "ironjarvis.overview.usage";
const DOOR_KEY = "ironjarvis.doors.usage";

/** Pages that are not "apps" — reached from chrome, not from a list of pages. */
const NOT_APPS = new Set<string>(["/", "/help", "/updates"]);

function readJson<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback; // corrupt/blocked storage just means "no count yet"
  }
}

function writeJson(key: string, value: unknown): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* private mode / quota — nothing is remembered, nothing breaks */
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
