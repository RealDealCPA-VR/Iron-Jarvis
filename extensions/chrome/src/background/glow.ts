// The glow (v1.273.0): the tab Jarvis is working in is outlined in the app's accent
// while it works, so the user can see at a glance which tab the agent has.
//
// "I want to have a light glow around the tab it is controlling so I can visually
// see the tab that is being operated by the agent."
//
// HOW IT KNOWS. Every command the daemon sends lands here in the worker, and every
// command that touched a tab answers with that tab's id. `glowTarget` reads the id
// off the finished command; the glow is painted into that page; it stays while the
// turn runs and is cleared when the sidebar's turn ends (`done`, `error`, a `state`
// frame with `running: false`), when the socket is lost, or — for a turn driven from
// the Iron Jarvis window, whose end this worker never hears — after GLOW_LINGER_MS
// without a further command.
//
// WHAT IT IS. One fixed, full-viewport element with an inset box-shadow, injected
// with `chrome.scripting.executeScript` and painted by a function that runs INSIDE
// the page. It never captures input (`pointer-events: none`), never reads the page
// (it creates two nodes and touches nothing else), is idempotent (a second paint
// finds the first), and is removed by name. A page closed to add-ons cannot take it;
// the paint fails quietly and the glow simply is not there.
//
// A navigation unloads the page and the element with it; the navigate command's own
// result (which arrives after the new document settled) paints it again.

import {
  METHOD_ACTIVATE_TAB,
  METHOD_CLICK,
  METHOD_CLOSE_TAB,
  METHOD_CREATE_TAB,
  METHOD_GET_ELEMENTS,
  METHOD_NAVIGATE,
  METHOD_PRESS_KEY,
  METHOD_READ_PAGE,
  METHOD_SCREENSHOT,
  METHOD_SCROLL,
  METHOD_TYPE_TEXT,
} from "../protocol";

/** The element's id inside the page; the style element is `${GLOW_ID}-style`. */
export const GLOW_ID = "ij-agent-glow";

/** How long the glow outlives the last command when no sidebar turn is known. */
export const GLOW_LINGER_MS = 20_000;

/** The commands that work IN a tab. `status`, `list_tabs` and `close_tab` do not. */
export const GLOW_METHODS: ReadonlySet<string> = new Set([
  METHOD_READ_PAGE,
  METHOD_GET_ELEMENTS,
  METHOD_SCREENSHOT,
  METHOD_ACTIVATE_TAB,
  METHOD_CREATE_TAB,
  METHOD_NAVIGATE,
  METHOD_CLICK,
  METHOD_TYPE_TEXT,
  METHOD_PRESS_KEY,
  METHOD_SCROLL,
]);

/** Which tab a finished command worked in, or null when it touched no tab. */
export function glowTarget(method: string, result: Record<string, unknown> | null | undefined): number | null {
  if (!GLOW_METHODS.has(method)) {
    return null;
  }
  const tabId = result?.["tab_id"];
  return typeof tabId === "number" && Number.isFinite(tabId) ? tabId : null;
}

/**
 * Runs INSIDE the page. Self-contained on purpose: `executeScript` serialises the
 * function, so it may reference nothing from this module — the id arrives as an
 * argument. The accent is the dashboard's own (`--accent-rgb: 34 211 238`).
 */
function paintGlow(id: string): void {
  if (document.getElementById(id)) {
    return;
  }
  const root = document.documentElement || document.body;
  const style = document.createElement("style");
  style.id = id + "-style";
  style.textContent =
    "@keyframes " + id + "-pulse{0%,100%{opacity:.7}50%{opacity:1}}" +
    "#" + id + "{position:fixed;inset:0;pointer-events:none;z-index:2147483647;" +
    "box-shadow:inset 0 0 0 3px rgba(34,211,238,.9),inset 0 0 56px 12px rgba(34,211,238,.45);" +
    "animation:" + id + "-pulse 2.4s ease-in-out infinite}";
  const el = document.createElement("div");
  el.id = id;
  el.setAttribute("aria-hidden", "true");
  root.appendChild(style);
  root.appendChild(el);
}

/** Runs INSIDE the page: removes what `paintGlow` made, and nothing else. */
function clearGlow(id: string): void {
  document.getElementById(id)?.remove();
  document.getElementById(id + "-style")?.remove();
}

export class AgentGlow {
  private readonly tabs = new Set<number>();
  private running = false;
  private linger: ReturnType<typeof setTimeout> | null = null;

  /** A command finished: glow the tab it worked in; a closed tab is forgotten. */
  async afterCommand(method: string, result: Record<string, unknown> | null | undefined): Promise<void> {
    if (method === METHOD_CLOSE_TAB) {
      const closed = result?.["tab_id"];
      if (typeof closed === "number") {
        this.tabs.delete(closed);
      }
      return;
    }
    const tabId = glowTarget(method, result);
    if (tabId === null) {
      return;
    }
    await this.show(tabId);
  }

  /** The sidebar's turn, as the daemon narrates it. */
  notePanelEvent(event: string, payload: Record<string, unknown>): void {
    if (event === "state") {
      this.running = payload["running"] === true;
      if (!this.running) {
        void this.hideAll();
      }
      return;
    }
    if (event === "done" || event === "error") {
      this.running = false;
      void this.hideAll();
      return;
    }
    if (event === "delta" || event === "tool" || event === "approval") {
      // A turn is running: the glow stays until it ends, however long it thinks.
      this.running = true;
      this.cancelLinger();
    }
  }

  /** The tab is gone; nothing to clear, nothing to remember. */
  forget(tabId: number): void {
    this.tabs.delete(tabId);
  }

  async show(tabId: number): Promise<void> {
    this.tabs.add(tabId);
    try {
      await chrome.scripting.executeScript({ target: { tabId }, func: paintGlow, args: [GLOW_ID] });
    } catch {
      // A page closed to add-ons, or a tab mid-navigation: no glow there, and no
      // error either — the command itself already reported what it could.
    }
    this.armLinger();
  }

  async hideAll(): Promise<void> {
    const ids = [...this.tabs];
    this.tabs.clear();
    this.cancelLinger();
    await Promise.all(ids.map((id) => this.clear(id)));
  }

  private async clear(tabId: number): Promise<void> {
    try {
      await chrome.scripting.executeScript({ target: { tabId }, func: clearGlow, args: [GLOW_ID] });
    } catch {
      // The tab closed or navigated: the element went with the page.
    }
  }

  private armLinger(): void {
    if (this.running) {
      return;
    }
    this.cancelLinger();
    this.linger = setTimeout(() => {
      this.linger = null;
      void this.hideAll();
    }, GLOW_LINGER_MS);
  }

  private cancelLinger(): void {
    if (this.linger !== null) {
      clearTimeout(this.linger);
      this.linger = null;
    }
  }
}
