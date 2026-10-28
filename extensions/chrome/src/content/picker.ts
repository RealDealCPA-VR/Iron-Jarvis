// "Point at it" (v1.400.9): the user clicks the thing they mean, and Jarvis is
// told exactly which element "this" is.
//
// WHAT IT READS is what a snapshot already reads about one element and nothing
// more: its role and accessible name (`roleFor`, `accessibleName` — the same
// functions `browser_read_page` uses), its tag, a field's declared TYPE, the
// visible text outside any field (`textOutsideFields`, which never reads a
// field's value), and a CSS selector built from the page's own structure and
// checked to match exactly that element. A password field is described as a
// password field; its contents are never read.
//
// WHAT IT REFUSES TO BE FOOLED BY. Only a TRUSTED click picks — `event.isTrusted`
// is false for any click a page script dispatches — so a page cannot pick an
// element on the user's behalf by synthesising one. The page's own handlers never
// see the pick's click either (capture phase, `preventDefault` +
// `stopImmediatePropagation` on every pointer and mouse event while picking), so
// pointing at "Delete account" deletes nothing: the click went to Iron Jarvis.
// An event no person made passes UNTOUCHED: that is Jarvis's own click or typing
// (or the page's script), and swallowing it made an action report "clicked" on a
// page that never saw it. Jarvis acting on the page also ends the pick
// (content/index.ts), and a pick does not start over a modal dialog, a popover
// or a full-screen element — the browser's top layer sits above every z-index.
//
// THE SHIELD. While picking, a transparent layer covers the whole window and
// takes every pointer event, so the click lands on Iron Jarvis's own element and
// never on the page's — including an EMBEDDED FRAME (a payment form, a sign-in
// box), whose clicks a listener in this page could not see, let alone stop. The
// element meant is found UNDER the shield with `elementsFromPoint`; a click over
// a frame points at the frame itself. A page script listening on the window
// itself still sees that a click happened (its target is the shield), never on
// what.
//
// THE OVERLAY is three elements styled through the CSSOM (`el.style`), never a
// `<style>` tag: a page whose Content-Security-Policy forbids inline styles would
// silently drop a stylesheet and leave the user with an invisible picker. The box
// and the banner carry `pointer-events: none`.
//
// ONE PICK AT A TIME. A second start replaces the first (which reports
// `cancelled`), Esc cancels, and a minute with no click cancels — a picker left
// running would swallow the user's next real click on the page.

import { PICK_CANCELLED_KIND, PICKED_KIND } from "./channel";
import { INTERACTIVE_SELECTOR, isInteractive } from "./elements";
import { countRoleName } from "./actions";
import { fieldType } from "./scrub";
import { accessibleName, roleFor, textOutsideFields } from "./snapshot";

/** How long a pick waits for a click before it lets the page have its clicks back. */
export const PICK_TIMEOUT_MS = 60_000;

/** The bounds of what a pick reports — the daemon cuts again (schemas.PointedElement). */
export const PICK_NAME_CHARS = 200;
export const PICK_TEXT_CHARS = 300;
export const PICK_CSS_CHARS = 300;

/** The dashboard's accent (`--accent-rgb`), the colour the agent glow uses too. */
const ACCENT = "34, 211, 238";

/** The element the user pointed at, as the panel sends it on (`pointed`). */
export interface PickedElement {
  url: string;
  title: string;
  role: string;
  name: string;
  tag: string;
  input_type: string;
  text: string;
  css: string;
  unique_name: boolean;
}

/** One running pick: its overlay, its listeners and its clock. */
interface PickSession {
  shield: HTMLDivElement;
  box: HTMLDivElement;
  banner: HTMLDivElement;
  timer: ReturnType<typeof setTimeout>;
  stop: () => void;
}

let session: PickSession | null = null;

/** Tell the worker; a closed worker or panel is not this page's problem. */
function tell(message: Record<string, unknown>): void {
  try {
    void chrome.runtime.sendMessage(message).catch(() => {});
  } catch {
    // The add-on was reloaded under this page: there is no one to tell.
  }
}

/**
 * The element a click on `node` means: the nearest INTERACTIVE ancestor (a click
 * on the text inside a button means the button), else the node itself — a user
 * pointing at a table or a paragraph means that, and it gets no role.
 */
export function meantElement(node: Element): Element {
  const control = node.closest(INTERACTIVE_SELECTOR);
  if (control && isInteractive(control)) {
    return control;
  }
  return node;
}

function flat(value: string | null | undefined, limit: number): string {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
}

/** `value` quoted for a selector, with the browser's own escaper when it has one. */
function cssEscape(value: string): string {
  const css = (globalThis as { CSS?: { escape?: (v: string) => string } }).CSS;
  return css?.escape ? css.escape(value) : value.replace(/[^a-zA-Z0-9_-]/g, (c) => `\\${c}`);
}

function matchesOnly(selector: string, el: Element): boolean {
  try {
    const found = document.querySelectorAll(selector);
    return found.length === 1 && found[0] === el;
  } catch {
    return false;
  }
}

/**
 * A CSS selector that matches `el` and nothing else, or `""` when none fits.
 *
 * An `id` the page gives only this element first; otherwise a path of
 * `tag:nth-of-type(n)` steps up from the element, anchored at the nearest
 * ancestor with a unique id, stopping as soon as the path is unique. Every
 * candidate is CHECKED with `querySelectorAll`, so a selector that comes back is
 * one the add-on's own `css` target would resolve to this element — and one that
 * would not fit the bound comes back empty rather than cut into something else.
 */
export function uniqueSelector(el: Element): string {
  const id = el.getAttribute("id");
  if (id) {
    const byId = `#${cssEscape(id)}`;
    if (matchesOnly(byId, el)) {
      return byId;
    }
  }
  const steps: string[] = [];
  let node: Element | null = el;
  while (node && node !== document.documentElement) {
    const parent: Element | null = node.parentElement;
    const tag = node.tagName.toLowerCase();
    let step = tag;
    if (parent) {
      const same = Array.from(parent.children).filter((c) => c.tagName === node!.tagName);
      if (same.length > 1) {
        step = `${tag}:nth-of-type(${same.indexOf(node) + 1})`;
      }
    }
    steps.unshift(step);
    const selector = steps.join(" > ");
    if (matchesOnly(selector, el)) {
      return selector.length <= PICK_CSS_CHARS ? selector : "";
    }
    const parentId = parent?.getAttribute("id");
    if (parent && parentId) {
      const anchored = `#${cssEscape(parentId)} > ${selector}`;
      if (matchesOnly(anchored, el)) {
        return anchored.length <= PICK_CSS_CHARS ? anchored : "";
      }
    }
    node = parent;
  }
  const full = steps.length ? `html > ${steps.join(" > ")}` : "";
  return full && full.length <= PICK_CSS_CHARS && matchesOnly(full, el) ? full : "";
}

/**
 * The page's address without its query or fragment: those are where a sign-in
 * link carries its token, and naming which page the element is on needs neither.
 */
export function pageAddress(href: string): string {
  try {
    const url = new URL(href);
    return url.origin + url.pathname;
  } catch {
    return "";
  }
}

/** What the pick reports about `el`. Never a field's value (see the file header). */
export function describePicked(el: Element): PickedElement {
  const role = roleFor(el);
  const [name] = accessibleName(el, PICK_NAME_CHARS);
  const isField = el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT";
  return {
    url: flat(pageAddress(location.href), 300),
    title: flat(document.title, PICK_NAME_CHARS),
    role,
    name: flat(name, PICK_NAME_CHARS),
    tag: el.tagName.toLowerCase(),
    input_type: isField ? fieldType(el) : "",
    // A field's text IS its value; only what is written around and outside
    // fields is read, through the snapshot's own field-skipping reader.
    text: isField ? "" : flat(textOutsideFields(el), PICK_TEXT_CHARS),
    css: uniqueSelector(el),
    unique_name: Boolean(role && name) && countRoleName(role, name) === 1,
  };
}

function place(box: HTMLDivElement, el: Element | null): void {
  if (!el) {
    box.style.display = "none";
    return;
  }
  const r = el.getBoundingClientRect();
  Object.assign(box.style, {
    display: "block",
    left: `${r.left - 3}px`,
    top: `${r.top - 3}px`,
    width: `${r.width + 6}px`,
    height: `${r.height + 6}px`,
  });
}

/** Is `node` one of the overlay's own elements? */
function ours(node: EventTarget | null, s: PickSession): boolean {
  return node === s.shield || node === s.box || node === s.banner;
}

/** The page's own element at a point, looking THROUGH the overlay. */
function pageElementAt(x: number, y: number, s: PickSession): Element | null {
  const stack =
    typeof document.elementsFromPoint === "function"
      ? document.elementsFromPoint(x, y)
      : [document.elementFromPoint(x, y)].filter((n): n is Element => n !== null);
  const under = stack.find((node) => !ours(node, s));
  return under ? meantElement(under) : null;
}

/** End a pick. `reason` "" means the user picked; anything else is reported. */
function finish(reason: string): void {
  const s = session;
  if (!s) {
    return;
  }
  session = null;
  s.stop();
  if (reason) {
    tell({ kind: PICK_CANCELLED_KIND, reason });
  }
}

/**
 * Whether something the page put in the browser's TOP LAYER is open — a modal
 * dialog, a popover, a full-screen element. Those sit above any z-index, and a
 * modal dialog makes the rest of the page inert, shield included: a pointing
 * click would land in it for real. So a pick does not start over one.
 */
export function topLayerInTheWay(): boolean {
  try {
    if (document.fullscreenElement) {
      return true;
    }
    return document.querySelector("dialog:modal, :popover-open") !== null;
  } catch {
    // A browser that cannot parse the selector has no such layer to fear.
    return false;
  }
}

/** Start picking in this page. Answers at once; the pick itself arrives later. */
export function startPick(): { started: boolean; reason?: string } {
  if (session) {
    finish("replaced");
  }
  if (topLayerInTheWay()) {
    return { started: false, reason: "popup" };
  }
  const root = document.documentElement || document.body;
  // The shield takes every pointer event (see the file header). Transparent;
  // the crosshair is the only sign it is there.
  const shield = document.createElement("div");
  shield.setAttribute("aria-hidden", "true");
  Object.assign(shield.style, {
    position: "fixed",
    inset: "0",
    pointerEvents: "auto",
    // The highest z-index there is, and appended LAST, so a page element at
    // the same height (a chat widget's frame) sits under it (review fix).
    zIndex: "2147483647",
    background: "transparent",
    cursor: "crosshair",
  });
  const box = document.createElement("div");
  box.setAttribute("aria-hidden", "true");
  Object.assign(box.style, {
    position: "fixed",
    display: "none",
    pointerEvents: "none",
    zIndex: "2147483647",
    border: `2px solid rgba(${ACCENT}, 0.95)`,
    background: `rgba(${ACCENT}, 0.14)`,
    borderRadius: "6px",
    boxShadow: `0 0 0 4px rgba(${ACCENT}, 0.25)`,
    transition: "left 60ms, top 60ms, width 60ms, height 60ms",
  });
  const banner = document.createElement("div");
  banner.setAttribute("role", "status");
  banner.textContent = "Click what you mean · Esc to cancel";
  Object.assign(banner.style, {
    position: "fixed",
    top: "12px",
    left: "50%",
    transform: "translateX(-50%)",
    pointerEvents: "none",
    zIndex: "2147483647",
    padding: "7px 14px",
    borderRadius: "999px",
    font: "600 13px/1.3 system-ui, -apple-system, 'Segoe UI', sans-serif",
    color: "#06202a",
    background: `rgb(${ACCENT})`,
    boxShadow: "0 6px 20px rgba(0, 0, 0, 0.25)",
  });
  root.appendChild(shield);
  root.appendChild(box);
  root.appendChild(banner);

  let hovered: Element | null = null;
  const onMove = (event: MouseEvent): void => {
    const s = session;
    if (!s) {
      return;
    }
    hovered = pageElementAt(event.clientX, event.clientY, s);
    place(s.box, hovered);
  };
  // Every pointer and mouse event FROM THE PERSON stops here while picking: the
  // page must not react to the click that only says which element is meant.
  // An event no person made (`isTrusted` false) passes untouched: it is the
  // add-on's own click or typing — Jarvis acting on the page — or the page's
  // own script, and swallowing it made an action report "clicked" while the
  // page never saw it (review fix, v1.400.9).
  const swallow = (event: Event): void => {
    if (!event.isTrusted) {
      return;
    }
    event.preventDefault();
    event.stopImmediatePropagation();
  };
  const onClick = (event: MouseEvent): void => {
    if (!event.isTrusted || !session) {
      // Not the person's click: it picks nothing and is not stopped.
      return;
    }
    swallow(event);
    const target = pageElementAt(event.clientX, event.clientY, session) ?? hovered;
    if (!target) {
      return;
    }
    const element = describePicked(target);
    finish("");
    tell({ kind: PICKED_KIND, element });
  };
  const onKey = (event: KeyboardEvent): void => {
    if (event.key === "Escape" && event.isTrusted) {
      swallow(event);
      finish("cancelled");
    }
  };
  const swallowed = ["pointerdown", "pointerup", "mousedown", "mouseup", "auxclick", "dblclick", "contextmenu"];
  addEventListener("mousemove", onMove, true);
  addEventListener("click", onClick, true);
  addEventListener("keydown", onKey, true);
  for (const type of swallowed) {
    addEventListener(type, swallow, true);
  }
  const timer = setTimeout(() => finish("timed out"), PICK_TIMEOUT_MS);
  session = {
    shield,
    box,
    banner,
    timer,
    stop: () => {
      clearTimeout(timer);
      removeEventListener("mousemove", onMove, true);
      removeEventListener("click", onClick, true);
      removeEventListener("keydown", onKey, true);
      for (const type of swallowed) {
        removeEventListener(type, swallow, true);
      }
      shield.remove();
      box.remove();
      banner.remove();
    },
  };
  return { started: true };
}

/** Stop a pick the panel no longer wants (it was closed, or the user pressed again). */
export function cancelPick(): { cancelled: boolean } {
  const had = session !== null;
  finish("cancelled");
  return { cancelled: had };
}
