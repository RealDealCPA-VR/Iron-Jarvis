// The pointer (v1.454.0): when Jarvis clicks or types on a page, the user SEES it.
//
// "I'd also like to see when it has control of the browser and specifically when
// it is clicking with something that displays its screen movement and item
// clicked."
//
// THREE THINGS, all drawn by Iron Jarvis over the page and never part of it:
//   - a pointer that MOVES to the element before a click or typing starts, with a
//     short word under it ("Clicking", "Typing");
//   - a brief highlight around the element, and a ripple where the click lands;
//   - a small "Jarvis is controlling this tab" badge, shown with the tab's glow
//     (glow.ts) for as long as the glow is there.
//
// HOW IT KNOWS WHERE. Before a click or typing, the worker asks the page's own
// reader for the target's box (`LOCATE_OP`, content/actions.ts `locate`): the same
// resolution the action will use, with no scrolling and no refusal. When the box
// is on screen the pointer is drawn moving to it and the action waits
// `POINTER_MOVE_MS`; then the action runs exactly as before. After it, the page
// reader reports where the element it acted on was (`LAST_ACTED_OP`: after the
// action's own scroll into view), and the ripple and highlight are drawn there.
// A target that cannot be found, a page closed to add-ons, a reader that does not
// answer: no pointer, and the action runs unchanged — the action's own answer
// says what happened.
//
// WHAT IT MUST NEVER DO, and why each is true:
//   - change what the click does: every element is `pointer-events: none`, so no
//     event and no `elementFromPoint` ever lands on one; the action itself
//     dispatches its events on the element, not at a point; the only difference
//     is the wait before it.
//   - read the page: the painters run INSIDE the page, take the box as an
//     argument, and touch only the nodes they made (found by their own ids). The
//     words are CSS generated content, so a page read (read_page's text walk)
//     never sees them as the page's text.
//   - move page_version: the nodes carry no role, no tabindex and none of the
//     watched attributes (content/elements.ts WATCHED_ATTRIBUTES), so the
//     element registry's observer does not count them as an interactive change
//     and the model's element ids stay good.
//   - hide the target under reduced motion: with `prefers-reduced-motion` the
//     pointer is placed without moving, the ripple is a still ring, and the
//     highlight still shows.
//
// Injected and cleared like the glow: `chrome.scripting.executeScript` with a
// self-contained function, idempotent, removed by name (AgentGlow.clear calls
// `clearPointer` and `clearBadge`).

import { METHOD_CLICK, METHOD_TYPE_TEXT } from "../protocol";
import { LAST_ACTED_OP, LOCATE_OP } from "../content/channel";

/** The pointer's id inside the page; its other nodes are `${POINTER_ID}-…`. */
export const POINTER_ID = "ij-agent-pointer";

/** The badge's id inside the page; its style element is `${BADGE_ID}-style`. */
export const BADGE_ID = "ij-agent-badge";

/** How long the pointer takes to reach the element, and the action waits for it. */
export const POINTER_MOVE_MS = 420;

/** How long the highlight and the ripple stay before they fade out. */
export const POINTER_MARK_MS = 1100;

/** The acting commands the pointer is drawn for. */
export const POINTER_METHODS: ReadonlySet<string> = new Set([METHOD_CLICK, METHOD_TYPE_TEXT]);

/** One element's box in the page's viewport, in CSS pixels. */
export interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

/** The word under the pointer for each phase. */
export type PointerPhase = "aim-click" | "aim-type" | "click" | "type";

/** A reader's answer as a box, or null when it found nothing usable. */
export function boxFrom(result: Record<string, unknown> | null | undefined): Box | null {
  if (!result || result["found"] !== true) {
    return null;
  }
  const nums = ["x", "y", "width", "height"].map((k) => result[k]);
  if (!nums.every((n) => typeof n === "number" && Number.isFinite(n))) {
    return null;
  }
  const [x, y, width, height] = nums as number[];
  if ((width as number) <= 0 || (height as number) <= 0) {
    return null;
  }
  return { x: x as number, y: y as number, width: width as number, height: height as number };
}

/** Whether the reader said the box is on screen now (the pointer can move to it). */
export function onScreen(result: Record<string, unknown> | null | undefined): boolean {
  return Boolean(result && result["on_screen"] === true);
}

/** The phase for a command, before (`aim`) or after the action. */
export function phaseFor(method: string, after: boolean): PointerPhase | null {
  if (method === METHOD_CLICK) {
    return after ? "click" : "aim-click";
  }
  if (method === METHOD_TYPE_TEXT) {
    return after ? "type" : "aim-type";
  }
  return null;
}

/**
 * Runs INSIDE the page. Self-contained on purpose: `executeScript` serialises the
 * function, so it may reference nothing from this module — the id, the box, the
 * phase and the timings arrive as arguments. The accent is the dashboard's own
 * (`--accent-rgb: 34 211 238`), the glow's colour.
 */
function paintPointer(id: string, box: Box, phase: string, moveMs: number, markMs: number): boolean {
  const reduced =
    typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  const root = document.documentElement || document.body;
  if (!document.getElementById(id + "-style")) {
    const style = document.createElement("style");
    style.id = id + "-style";
    style.textContent =
      "#" + id + "{position:fixed;left:0;top:0;width:22px;height:22px;pointer-events:none;" +
      "z-index:2147483647;transition:transform " + moveMs + "ms cubic-bezier(.3,.7,.2,1)}" +
      "#" + id + "[data-still=\"1\"]{transition:none}" +
      "#" + id + " svg{display:block;width:22px;height:22px;filter:drop-shadow(0 1px 2px rgba(0,0,0,.45))}" +
      "#" + id + "::after{position:absolute;left:18px;top:20px;padding:2px 7px;border-radius:999px;" +
      "font:600 11px/1.4 system-ui,sans-serif;white-space:nowrap;color:#04252c;background:rgba(34,211,238,.95)}" +
      "#" + id + "[data-phase=\"aim-click\"]::after,#" + id + "[data-phase=\"click\"]::after{content:\"Clicking\"}" +
      "#" + id + "[data-phase=\"aim-type\"]::after,#" + id + "[data-phase=\"type\"]::after{content:\"Typing\"}" +
      "#" + id + "-target{position:fixed;pointer-events:none;z-index:2147483646;border-radius:6px;" +
      "box-shadow:0 0 0 2px rgba(34,211,238,.95),0 0 0 6px rgba(34,211,238,.25)}" +
      "#" + id + "-target[data-fade=\"1\"]{animation:" + id + "-fade " + markMs + "ms ease-out forwards}" +
      "#" + id + "-ripple{position:fixed;pointer-events:none;z-index:2147483647;width:36px;height:36px;" +
      "margin:-18px 0 0 -18px;border-radius:999px;border:2px solid rgba(34,211,238,.95)}" +
      "#" + id + "-ripple[data-still=\"0\"]{animation:" + id + "-ripple 600ms ease-out forwards}" +
      "@keyframes " + id + "-ripple{from{transform:scale(.3);opacity:1}to{transform:scale(1.6);opacity:0}}" +
      "@keyframes " + id + "-fade{0%,70%{opacity:1}100%{opacity:0}}";
    root.appendChild(style);
  }
  const vw = window.innerWidth || 0;
  const vh = window.innerHeight || 0;
  const cx = Math.max(0, Math.min(vw, box.x + box.width / 2));
  const cy = Math.max(0, Math.min(vh, box.y + box.height / 2));
  let pointer = document.getElementById(id);
  if (!pointer) {
    pointer = document.createElement("div");
    pointer.id = id;
    pointer.setAttribute("aria-hidden", "true");
    // The arrow: the system pointer's shape, in the accent, outlined in dark.
    // Built node by node, never through innerHTML: a page that enforces
    // Trusted Types refuses an HTML string.
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", "M4 2.5l15 9.2-6.6 1.3 3.9 7.2-2.6 1.4-3.9-7.2-4.8 4.7z");
    path.setAttribute("fill", "rgba(34,211,238,1)");
    path.setAttribute("stroke", "#04252c");
    path.setAttribute("stroke-width", "1.4");
    path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
    pointer.appendChild(svg);
    // A NEW pointer starts a little up and to the left of its target, so the
    // first move is visible; under reduced motion it is simply placed.
    pointer.style.transform =
      "translate(" + Math.max(0, cx - 90) + "px," + Math.max(0, cy - 70) + "px)";
    root.appendChild(pointer);
  }
  pointer.dataset["still"] = reduced ? "1" : "0";
  pointer.dataset["phase"] = phase;
  // A reflow between placing and moving, so the browser animates the move
  // rather than jumping to it (reading our OWN node's size, nothing else).
  void pointer.offsetWidth;
  pointer.style.transform = "translate(" + cx + "px," + cy + "px)";

  let target = document.getElementById(id + "-target");
  if (!target) {
    target = document.createElement("div");
    target.id = id + "-target";
    target.setAttribute("aria-hidden", "true");
    root.appendChild(target);
  }
  target.style.left = box.x - 3 + "px";
  target.style.top = box.y - 3 + "px";
  target.style.width = box.width + 6 + "px";
  target.style.height = box.height + 6 + "px";
  // Restart the fade: a mark for a new action stays its full time. The token
  // lets a timer below take down only the mark it was set for.
  const token = String(Date.now()) + Math.random().toString(36).slice(2, 8);
  target.dataset["mark"] = token;
  target.dataset["fade"] = "0";
  void target.offsetWidth;
  target.dataset["fade"] = reduced ? "0" : "1";

  if (phase === "click") {
    document.getElementById(id + "-ripple")?.remove();
    const ripple = document.createElement("div");
    ripple.id = id + "-ripple";
    ripple.setAttribute("aria-hidden", "true");
    ripple.dataset["still"] = reduced ? "1" : "0";
    ripple.style.left = cx + "px";
    ripple.style.top = cy + "px";
    root.appendChild(ripple);
    setTimeout(() => ripple.remove(), reduced ? markMs : 700);
  }
  if (reduced) {
    // No animation to fade it, so the highlight is taken down by time — but
    // only this mark: a newer action's mark on the same node stays its time.
    const mark = target;
    setTimeout(() => {
      if (mark.dataset["mark"] === token) mark.remove();
    }, moveMs + markMs);
  }
  return reduced;
}

/** Runs INSIDE the page: removes what `paintPointer` made, and nothing else. */
function clearPointer(id: string): void {
  for (const suffix of ["", "-target", "-ripple", "-style"]) {
    document.getElementById(id + suffix)?.remove();
  }
}

/**
 * Runs INSIDE the page: the "Jarvis is controlling this tab" badge, top centre.
 * The words are generated content, so a page read never sees them. Idempotent.
 */
function paintBadge(id: string): void {
  if (document.getElementById(id)) {
    return;
  }
  const root = document.documentElement || document.body;
  const style = document.createElement("style");
  style.id = id + "-style";
  style.textContent =
    "#" + id + "{position:fixed;top:10px;left:50%;transform:translateX(-50%);pointer-events:none;" +
    "z-index:2147483647;padding:4px 12px 4px 26px;border-radius:999px;" +
    "font:600 12px/1.4 system-ui,sans-serif;color:#04252c;background:rgba(34,211,238,.95);" +
    "box-shadow:0 4px 14px rgba(0,0,0,.25)}" +
    "#" + id + "::before{content:\"\";position:absolute;left:10px;top:50%;width:8px;height:8px;" +
    "margin-top:-4px;border-radius:999px;background:#04252c}" +
    "#" + id + "::after{content:\"Jarvis is controlling this tab\"}";
  const el = document.createElement("div");
  el.id = id;
  el.setAttribute("aria-hidden", "true");
  root.appendChild(style);
  root.appendChild(el);
}

/** Runs INSIDE the page: removes what `paintBadge` made, and nothing else. */
function clearBadge(id: string): void {
  document.getElementById(id)?.remove();
  document.getElementById(id + "-style")?.remove();
}

/** The page painters, for AgentGlow and the tests. */
export const pagePainters = { paintPointer, clearPointer, paintBadge, clearBadge };

/** How the worker reaches a page: `runInPage` from tabs.ts, injected for tests. */
export type PageRunner = (
  op: string,
  params: Record<string, unknown>,
  hostPermission: boolean,
) => Promise<Record<string, unknown>>;

/** What `before` saw, handed to `after`. */
export interface Aimed {
  tabId: number;
  box: Box;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function paintIn(tabId: number, box: Box, phase: PointerPhase): Promise<void> {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      func: paintPointer,
      args: [POINTER_ID, box, phase, POINTER_MOVE_MS, POINTER_MARK_MS],
    });
  } catch {
    // A page closed to add-ons, or a tab mid-navigation: no pointer there.
  }
}

/** The pointer, driven by the worker around each click and typing. */
export class ActionPointer {
  constructor(
    private readonly run: PageRunner,
    private readonly wait: (ms: number) => Promise<void> = sleep,
  ) {}

  /**
   * BEFORE the action: draw the pointer moving to the target and wait for it.
   * Never throws and never refuses — a target it cannot see is the action's to
   * report. Returns what it aimed at, or null when it drew nothing.
   */
  async before(method: string, params: Record<string, unknown>, hostPermission: boolean): Promise<Aimed | null> {
    const phase = phaseFor(method, false);
    if (!phase || !hostPermission) {
      return null;
    }
    let found: Record<string, unknown>;
    try {
      found = await this.run(LOCATE_OP, params, hostPermission);
    } catch {
      return null;
    }
    const box = boxFrom(found);
    const tabId = found["tab_id"];
    if (!box || typeof tabId !== "number" || !onScreen(found)) {
      return null;
    }
    await paintIn(tabId, box, phase);
    await this.wait(POINTER_MOVE_MS);
    return { tabId, box };
  }

  /**
   * AFTER the action: the ripple and highlight where the element was when it
   * was acted on (after the action's own scroll). Never throws; a page that
   * navigated away has no reader left to ask, and then nothing is drawn.
   */
  async after(method: string, result: Record<string, unknown>, hostPermission: boolean): Promise<void> {
    const phase = phaseFor(method, true);
    const tabId = result["tab_id"];
    if (!phase || !hostPermission || typeof tabId !== "number") {
      return;
    }
    let acted: Record<string, unknown>;
    try {
      acted = await this.run(LAST_ACTED_OP, { tab_id: tabId }, hostPermission);
    } catch {
      return;
    }
    const box = boxFrom(acted);
    if (box) {
      await paintIn(tabId, box, phase);
    }
  }
}

/** Clear the pointer and the badge in one tab (AgentGlow.clear). */
export async function clearInTab(tabId: number): Promise<void> {
  for (const [func, id] of [
    [clearPointer, POINTER_ID],
    [clearBadge, BADGE_ID],
  ] as const) {
    try {
      await chrome.scripting.executeScript({ target: { tabId }, func, args: [id] });
    } catch {
      // The tab closed or navigated: the nodes went with the page.
    }
  }
}

/** Show the badge in one tab (AgentGlow.show). */
export async function badgeInTab(tabId: number): Promise<void> {
  try {
    await chrome.scripting.executeScript({ target: { tabId }, func: paintBadge, args: [BADGE_ID] });
  } catch {
    // A page closed to add-ons: no badge, and no error either.
  }
}
