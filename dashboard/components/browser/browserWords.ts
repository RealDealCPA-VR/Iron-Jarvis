/**
 * The words for the browser the user is actually setting up (v1.261.0).
 *
 * Chrome and Edge load the same add-on with the same identity, but the steps a
 * person follows are written in one browser's vocabulary: its own page for
 * loading add-ons (`chrome://extensions` / `edge://extensions`), its own way of
 * pinning a toolbar icon (Chrome: a pin; Edge: an eye icon, "Show in toolbar").
 * Until this file the guided window wrote every step for Chrome and put Edge in
 * parentheses — an Edge user on an Edge-only PC read Chrome instructions.
 *
 * ONE decision, made here: which browser to write for. The daemon says what is
 * INSTALLED on this machine (`installed_browsers`, from the doctor's own finder)
 * and, once paired, WHICH browser paired (`browser_name`, sent by the add-on on
 * hello). The paired browser wins — it is the one the user is actually using; an
 * Edge-only machine gets Edge; anything else gets Chrome, which is also what the
 * add-on's own README leads with. The user can override the pick in the guided
 * window, and that override is remembered on this device.
 */

import type { BrowserStatus } from "@/lib/types";

export type BrowserKey = "chrome" | "edge";

export interface BrowserWords {
  key: BrowserKey;
  /** "Google Chrome" — the name the add-on reports and the doctor prints. */
  name: string;
  /** "Chrome" — the word a sentence uses. */
  short: string;
  /** "Chrome's" */
  possessive: string;
  /** The browser's OWN page for loading add-ons, quoted as an address. */
  extensionsPage: string;
  /** How a newly loaded add-on's icon is put on the toolbar, from the puzzle-piece menu. */
  pinAction: string;
}

export const BROWSERS: Record<BrowserKey, BrowserWords> = {
  chrome: {
    key: "chrome",
    name: "Google Chrome",
    short: "Chrome",
    possessive: "Chrome's",
    extensionsPage: "chrome://extensions",
    pinAction: "press the pin beside it",
  },
  edge: {
    key: "edge",
    name: "Microsoft Edge",
    short: "Edge",
    possessive: "Edge's",
    extensionsPage: "edge://extensions",
    pinAction: "press the eye icon beside it (Show in toolbar)",
  },
};

/** The key for a browser NAME as the add-on or the doctor spells it, or null. */
export function browserKeyFromName(name: string | null | undefined): BrowserKey | null {
  const n = (name ?? "").toLowerCase();
  if (!n) return null;
  if (n.includes("edge")) return "edge";
  if (n.includes("chrome")) return "chrome";
  return null;
}

/**
 * Which browser to write the steps for, from the status alone.
 *
 *   1. A paired browser that said its name — that is the one in use.
 *   2. An Edge-only machine — Edge.
 *   3. Otherwise Chrome (both installed, Chrome only, or nothing known).
 */
export function pickBrowser(status: BrowserStatus | null | undefined): BrowserKey {
  const paired = browserKeyFromName(status?.browser_name);
  if (paired && (status?.connected || status?.paired)) return paired;
  const installed = (status?.installed_browsers ?? []).map(browserKeyFromName).filter(Boolean);
  if (installed.includes("edge") && !installed.includes("chrome")) return "edge";
  return "chrome";
}

/**
 * v1.449.0 (readiness Wave C, item 31): is the add-on the browser runs an
 * OLDER copy than this app? The add-on reports its own version on
 * `browser.hello` (the manifest's, bumped with the app's by
 * `scripts/bump_version.py`) and `GET /browser/status` forwards it as
 * `extension_version`; until now nothing compared the two numbers. Only an
 * OLDER add-on is said (a newer one — a source checkout ahead of the app —
 * is nobody's problem); an unreadable number on either side says nothing.
 */
export function compareVersions(a: string, b: string): number | null {
  const pa = parseVersion(a);
  const pb = parseVersion(b);
  if (!pa || !pb) return null;
  for (let i = 0; i < 3; i += 1) {
    if (pa[i] !== pb[i]) return pa[i] < pb[i] ? -1 : 1;
  }
  return 0;
}

/** The three numbers of a release version, or null for anything else: a
 *  dev build ("1.448.1-dev"), a bare word, an empty string. A number with a
 *  suffix is NOT read by its prefix — a source checkout's bundle is nobody's
 *  stale copy, and a line about it would send the user to press Reload on
 *  a copy that is not from a release. */
function parseVersion(v: string | null | undefined): [number, number, number] | null {
  const m = /^\s*v?(\d+)\.(\d+)\.(\d+)\s*$/.exec(v ?? "");
  if (!m) return null;
  return [Number(m[1]), Number(m[2]), Number(m[3])];
}

/** The one sentence for a browser running an older add-on than this app, or
 *  null when the two agree, the add-on is newer, or either number is missing. */
export function staleAddonLine(
  addonVersion: string | null | undefined,
  appVersion: string | null | undefined,
  browser: BrowserWords,
): string | null {
  const addon = (addonVersion ?? "").trim();
  const app = (appVersion ?? "").trim();
  if (!addon || !app) return null;
  if (compareVersions(addon, app) !== -1) return null;
  return (
    `${browser.short} is running an older copy of the add-on (${addon}; this app is ${app}) — ` +
    `open ${browser.extensionsPage} and press Reload on Iron Jarvis.`
  );
}

/** Where the guided window remembers an explicit choice on this device. */
export const BROWSER_CHOICE_KEY = "ij.browser.setup.browser";

export function readBrowserChoice(): BrowserKey | null {
  try {
    const v = window.localStorage.getItem(BROWSER_CHOICE_KEY);
    return v === "chrome" || v === "edge" ? v : null;
  } catch {
    return null;
  }
}

export function writeBrowserChoice(key: BrowserKey): void {
  try {
    window.localStorage.setItem(BROWSER_CHOICE_KEY, key);
  } catch {
    /* private mode, blocked storage: the choice simply lasts this session */
  }
}
