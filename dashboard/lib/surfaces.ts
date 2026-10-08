import { LayoutGrid, Store } from "lucide-react";
import { NAV_ENTRIES, NON_RAIL_ENTRIES, type NavEntry } from "@/lib/nav";

/**
 * THE SURFACE MANIFEST (calm UI redesign S6, AUDIT §4.2-4.3).
 *
 * Every place in the app, once, with where it lives in the calm shell:
 *
 *   - the SIDEBAR's four items (Build, Projects, Everything, Settings) — the
 *     brief's cap; "New chat" is an action and the chat list is not counted;
 *   - the EVERYTHING page, which lists every other surface in four columns
 *     (Work, Automations, Knowledge, System) plus the places that become
 *     Settings sections — so any surface is two clicks from home (T2);
 *   - the COMMAND PALETTE, which offers every surface by name and alias.
 *
 * The rows themselves (label, icon, aliases, blurb) still come from the ONE
 * nav catalogue (lib/nav.ts) — this file only says where each one belongs, so
 * a page renamed there is renamed everywhere. A page missing from GROUP_OF
 * fails the T2 test: an unfindable page may as well not ship.
 */

export type SurfaceGroup = "primary" | "work" | "automations" | "knowledge" | "system" | "setup";

export interface Surface extends NavEntry {
  group: SurfaceGroup;
}

/** The sidebar's four items, in order (T1: at most four). */
export const SIDEBAR_HREFS = ["/terminals", "/projects", "/everything", "/settings"] as const;

/** Everything's columns, in order. `setup` holds the places that are
 *  Settings sections in the calm shell (Connections, Apps & tools, …). */
export const EVERYTHING_GROUPS: { key: Exclude<SurfaceGroup, "primary">; label: string; hint: string }[] = [
  { key: "work", label: "Work", hint: "Things you make and hand off" },
  { key: "automations", label: "Automations", hint: "Work that runs on its own" },
  { key: "knowledge", label: "Knowledge", hint: "What Jarvis knows and keeps" },
  { key: "system", label: "System", hint: "What ran, what it cost, how it is" },
  { key: "setup", label: "Setup", hint: "Accounts, apps and access" },
];

/** Where each page lives. Every route the dashboard serves is here. */
const GROUP_OF: Readonly<Record<string, SurfaceGroup>> = {
  "/": "primary",
  "/chat": "primary",
  "/terminals": "primary",
  "/projects": "primary",
  "/everything": "primary",
  "/settings": "primary",
  "/agents": "work",
  "/creative": "work",
  "/templates": "work",
  "/documents": "work",
  "/filesearch": "work",
  "/workflows": "automations",
  "/schedules": "automations",
  "/reflex": "automations",
  "/sentinels": "automations",
  "/autonomy": "automations",
  "/webhooks": "automations",
  "/memory": "knowledge",
  "/skills": "knowledge",
  "/artifacts": "knowledge",
  "/you": "knowledge",
  "/train": "knowledge",
  "/sessions": "system",
  "/kanban": "system",
  "/activity": "system",
  "/usage": "system",
  "/fleet": "system",
  "/self-dev": "system",
  "/updates": "system",
  "/help": "system",
  "/connections": "setup",
  "/marketplace": "setup",
  "/secrets": "setup",
  "/channels": "setup",
  "/computeruse": "setup",
  "/tools": "setup",
};

/** Pages that were never on the rail and so are not in lib/nav.ts. */
const EXTRA_ENTRIES: NavEntry[] = [
  {
    href: "/everything",
    label: "Everything",
    icon: LayoutGrid,
    aliases: ["all pages", "all tools", "modules", "directory of pages", "more", "apps", "everything else"],
    blurb: "Every part of Iron Jarvis on one page, grouped, each with one line about what it is for.",
  },
  {
    // v1.315.0 left the Directory off the palette; the manifest closes that.
    href: "/marketplace",
    label: "Directory",
    icon: Store,
    aliases: ["marketplace", "connectors", "connect an app", "notion", "github", "app store", "integrations"],
    blurb: "Apps and extensions you can connect — Notion, GitHub, Slack and more.",
  },
];

export const SURFACES: Surface[] = [...NAV_ENTRIES, ...NON_RAIL_ENTRIES, ...EXTRA_ENTRIES]
  .filter((e, i, all) => all.findIndex((x) => x.href === e.href) === i)
  .filter((e) => GROUP_OF[e.href] !== undefined)
  .map((e) => ({ ...e, group: GROUP_OF[e.href] }));

/** Hrefs the manifest knows about but has not grouped (the T2 test asserts
 *  this is empty against every catalogue entry). */
export function ungrouped(): string[] {
  return [...NAV_ENTRIES, ...NON_RAIL_ENTRIES, ...EXTRA_ENTRIES]
    .map((e) => e.href)
    .filter((h) => GROUP_OF[h] === undefined);
}

export function surfacesIn(group: SurfaceGroup): Surface[] {
  return SURFACES.filter((s) => s.group === group);
}

export function surfaceFor(href: string): Surface | undefined {
  return SURFACES.find((s) => s.href === href);
}

/** The sidebar's status dot links to /everything#status; a link to the page
 *  you are on changes only the hash, so it also announces the tab. */
export const EVERYTHING_TAB_EVENT = "ij:everything-tab";

// ── Pins (AUDIT Q11) ─────────────────────────────────────────────────────────
// Up to three Everything entries the user pins under the sidebar's four items.
// None by default (the four-item cap is measured on a fresh profile). Kept in
// this browser only: a pin is a per-person convenience, not shared state.

export const PIN_KEY = "ij_sidebar_pins";
export const MAX_PINS = 3;
export const PINS_EVENT = "ij:pins-changed";

export function readPins(): string[] {
  try {
    const raw = JSON.parse(window.localStorage.getItem(PIN_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    return raw.filter((h): h is string => typeof h === "string" && !!surfaceFor(h)).slice(0, MAX_PINS);
  } catch {
    return [];
  }
}

export function writePins(pins: string[]): void {
  const clean = pins.filter((h, i) => pins.indexOf(h) === i && !!surfaceFor(h)).slice(0, MAX_PINS);
  try {
    window.localStorage.setItem(PIN_KEY, JSON.stringify(clean));
  } catch {
    /* private window: pins last for the page */
  }
  try {
    window.dispatchEvent(new Event(PINS_EVENT));
  } catch {
    /* no window */
  }
}

/** Pin or unpin; refuses a fourth pin (returns false). */
export function togglePin(href: string): boolean {
  const pins = readPins();
  if (pins.includes(href)) {
    writePins(pins.filter((h) => h !== href));
    return true;
  }
  if (pins.length >= MAX_PINS) return false;
  writePins([...pins, href]);
  return true;
}
