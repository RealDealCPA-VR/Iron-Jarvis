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
