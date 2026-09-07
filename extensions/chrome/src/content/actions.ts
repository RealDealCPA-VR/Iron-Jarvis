// The acting half of the page reader: the only Iron Jarvis code that CHANGES a page.
//
// Everything in this file runs inside a tab the user is signed in to — their bank,
// their email, their client portal. A wrong resolution here is not a failed test, it
// is a click in somebody's account. So every function below is written to refuse
// rather than guess, and every refusal names the next call.
//
// FOUR RULES SHAPE THIS FILE.
//
// 1. STALENESS IS DECIDED HERE (plan section 9.3), because this is where the live
//    node is. `ElementRegistry.resolve` owns the order — STALE_SNAPSHOT, then
//    STALE_ELEMENT, then ELEMENT_NOT_FOUND — and this file does not re-derive it. A
//    second answer to "is this element still there" would diverge from the first
//    within a second on any real page.
//
// 2. NEVER READ A FIELD'S CONTENTS, not even to write them back. Section 9.4's rule
//    survives Ship 3 intact and gets harder here, because the obvious way to append
//    text to a field is to read it and re-write it. So this file types through
//    `document.execCommand("insertText")`, which inserts at the caret and reads
//    nothing, and when that is refused by the host it REFUSES THE CALL rather than
//    falling back to a read-modify-write. `typed_into` reports the element id, role
//    and accessible name — never what the field now holds. The typed text is
//    redacted in the ledger (`browser_type.redact_args`); echoing it in a result
//    would defeat that entirely.
//
// 3. AMBIGUITY IS A REFUSAL, NEVER A FIRST MATCH. A `role`+`name` or `css` target
//    that matches two elements is refused naming the count, because the alternative
//    is acting on whichever one the DOM happened to list first — on a page where the
//    two candidates are plausibly "Transfer" and "Transfer all".
//
// 4. THE EVENTS A REAL PAGE LISTENS FOR, or the action did not happen. A React or
//    Angular form does not notice a value assigned straight onto the node: React
//    installs its own `value` setter to track changes, so `node.value = x` updates
//    the DOM and leaves the component's state on the old value — the user is told
//    the field was filled and the form submits empty. Hence the native prototype
//    setter for a clear, `execCommand` for the insert, and a real `input`/`change`
//    pair after both.
//
// WHY `isRendered` AND `isEnabled` ARE DEFINED TWICE, here and in `snapshot.ts`:
// they are private there, `snapshot.ts` belongs to Ship 2, and a snapshot that calls
// an element visible while an action calls it invisible is the exact contradiction
// this file must not introduce. So the two bodies are kept CHARACTER-IDENTICAL and
// `tests/test_browser_actions_page_v1237.py` compares them token for token. Editing
// one and not the other is red, not silent. The right end state is one exported
// pair; that is a Ship 5 tidy-up, recorded rather than done inside another lane's
// file.

import {
  MAX_NAME_CHARS,
  SCROLL_BOTTOM,
  SCROLL_DOWN,
  SCROLL_TOP,
  SCROLL_UP,
  TARGET_CSS,
  TARGET_ELEMENT_ID,
  TARGET_ROLE,
  type ClickResult,
  type PressKeyResult,
  type ScrollResult,
  type TargetRef,
  type TypeTextResult,
} from "../protocol";
import { BridgeError } from "../bridge/errors";
import { INTERACTIVE_SELECTOR, isInteractive, registry } from "./elements";
import { isEditableField, isEditableHost } from "./scrub";
import { accessibleName, roleFor } from "./snapshot";

/**
 * Whether `el` is rendered at all.
 *
 * MIRROR of `snapshot.isRendered`; see this file's header for why, and the pin that
 * keeps the two identical.
 */
function isRendered(el: Element): boolean {
  const probe = el as Element & { checkVisibility?: (options?: unknown) => boolean };
  if (typeof probe.checkVisibility === "function") {
    return probe.checkVisibility({ checkVisibilityCSS: true });
  }
  const box = el as Element & { offsetParent?: unknown };
  return box.offsetParent !== null || el.getClientRects().length > 0;
}

/**
 * Whether the control is enabled, by attribute and by ARIA.
 *
 * MIRROR of `snapshot.isEnabled`; see this file's header.
 */
function isEnabled(el: Element): boolean {
  if (el.hasAttribute("disabled")) {
    return false;
  }
  return (el.getAttribute("aria-disabled") ?? "").trim().toLowerCase() !== "true";
}

/** The bounded accessible name of `el`, without the truncation flag. */
function nameOf(el: Element): string {
  const [name] = accessibleName(el, MAX_NAME_CHARS);
  return name;
}

/**
 * How one resolved element is identified in a result and in a refusal.
 *
 * `elementId` is `""` when the call addressed the element by role or by CSS rather
 * than by a snapshot id. That is deliberately not filled in with a lookup: the
 * registry maps id -> node in one direction only, and inventing a reverse lookup
 * here would report an id from a snapshot the caller never named. A model that
 * wants an id calls `browser_read_page`, which is the same advice every staleness
 * refusal gives.
 */
interface Resolved {
  node: Element;
  elementId: string;
}

/** `Resolved` as the wire's `TargetRef`: what was ACTUALLY acted on. */
function targetRef(resolved: Resolved): TargetRef {
  return {
    element_id: resolved.elementId,
    role: roleFor(resolved.node),
    // The accessible name of a FIELD is what is written ABOUT it, never what is
    // written IN it (`snapshot.accessibleName` gates on `isEditableField`), so this
    // is safe to echo after a type. That gate is what makes rule 2 hold here.
    name: nameOf(resolved.node),
  };
}

/**
 * How an element is named inside an ELEMENT_NOT_FOUND remedy.
 *
 * The remedy template is Ship 1's, verbatim: "No element {element_id} in snapshot
 * {snapshot_id}. Call browser_read_page to list what is on the page now." Section
 * 9.3 also wants an element that EXISTS but is invisible or disabled to say WHICH
 * of the two it is. The page->worker channel carries a code and its format
 * arguments only — a rendered sentence would be a second copy of the remedy
 * vocabulary (`bridge/errors.ts` owns it, generated from Python) — so the "which"
 * rides in the identification slot rather than in a message this half cannot send:
 *
 *   No element e17 (button "Delete account", on the page but not enabled right now)
 *   in snapshot snap_ab12cd34. Call browser_read_page to list what is on the page now.
 *
 * Inventing a new error code instead would need a daemon-side remedy, which is
 * another lane's file, and a code the daemon has no remedy for reaches the model as
 * "Iron Jarvis does not have a remedy for that".
 */
function described(resolved: Resolved, note: string): string {
  const ref = targetRef(resolved);
  const head = ref.element_id || "(no id — addressed by role or CSS)";
  const parts = [ref.role, ref.name ? JSON.stringify(ref.name) : "", note].filter(
    (part) => part.length > 0,
  );
  return parts.length > 0 ? `${head} (${parts.join(" ")})` : head;
}

/** ELEMENT_NOT_FOUND naming this element and what is wrong with it. */
function notActionable(resolved: Resolved, note: string): BridgeError {
  return new BridgeError("ELEMENT_NOT_FOUND", {
    element_id: described(resolved, note),
    snapshot_id: registry.snapshotId || "(none taken)",
  });
}

/** ELEMENT_NOT_FOUND carrying a whole sentence in the identification slot. */
function noTarget(detail: string): BridgeError {
  return new BridgeError("ELEMENT_NOT_FOUND", {
    element_id: detail,
    snapshot_id: registry.snapshotId || "(none taken)",
  });
}

/**
 * Every candidate a role or CSS target may resolve to.
 *
 * Scoped to `INTERACTIVE_SELECTOR` plus `isInteractive`, which is the same set the
 * snapshot's registry holds. Searching the whole document instead would let a role
 * target resolve to something `browser_read_page` never showed the model, so the
 * model would be acting on an element it has no description of.
 */
function candidates(): Element[] {
  return Array.from(document.querySelectorAll(INTERACTIVE_SELECTOR)).filter((el) =>
    isInteractive(el),
  );
}

/**
 * Pick one element out of `matches`, or refuse naming the ambiguity.
 *
 * Visible candidates win over hidden ones, because a page routinely keeps an
 * off-screen copy of a control (a mobile menu, a print header) and the one the user
 * can see is the one they meant. Beyond that, TWO candidates is a refusal and never
 * a first match: the pair this rule exists for is "Transfer" beside "Transfer all".
 */
function exactlyOne(matches: Element[], what: string): Element {
  if (matches.length === 0) {
    throw noTarget(`nothing on this page matches ${what}`);
  }
  const visible = matches.filter((el) => isRendered(el));
  const pool = visible.length > 0 ? visible : matches;
  const first = pool[0];
  if (pool.length > 1 || first === undefined) {
    throw noTarget(
      `${pool.length} elements match ${what}, so Iron Jarvis refused rather than ` +
        "guess which one you meant. Call browser_read_page and send the element_id " +
        "of the one you want",
    );
  }
  return first;
}

/**
 * Resolve one target to one live node (plan section 8.6), or refuse.
 *
 * The three forms in the plan's own order — `element_id` preferred, then
 * `role`+`name`, then `css` — and EXACTLY ONE per call. The daemon's
 * `protocol.normalise_target` already enforces that; this is the backstop at the
 * last point before a real click, and it earns its place because the daemon is not
 * the only thing that can put a frame on this socket.
 *
 * Two forms is refused, never resolved by preference order: a caller that sent both
 * an `element_id` and a `css` is a caller that is unsure which is right, and
 * honouring the first would act on the target it did not mean with a result echoing
 * the one that won.
 */
export function resolveTarget(
  target: Record<string, unknown> | undefined,
  snapshotId: string | null,
): Resolved {
  const row = target ?? {};
  const elementId = String(row[TARGET_ELEMENT_ID] ?? "").trim();
  const role = String(row[TARGET_ROLE] ?? "").trim();
  const name = String(row["name"] ?? "").trim();
  const css = String(row[TARGET_CSS] ?? "").trim();

  const forms: string[] = [];
  if (elementId) {
    forms.push(TARGET_ELEMENT_ID);
  }
  if (role || name) {
    forms.push(TARGET_ROLE);
  }
  if (css) {
    forms.push(TARGET_CSS);
  }
  if (forms.length === 0) {
    throw noTarget(
      'no target was given. Name exactly one of: {"element_id": "e17"} from ' +
        'browser_read_page (preferred), {"role": "button", "name": "Sign in"}, or ' +
        '{"css": "#submit"}',
    );
  }
  if (forms.length > 1) {
    throw noTarget(
      `the target names ${forms.length} forms at once (${forms.join(", ")}); exactly ` +
        "one is allowed. Send the element_id from browser_read_page on its own",
    );
  }

  if (elementId) {
    // The registry owns the staleness order (section 9.3). Re-deriving it here
    // would be the second answer this whole model exists to prevent.
    return { node: registry.resolve(elementId, snapshotId), elementId };
  }
  if (css) {
    let matches: Element[];
    try {
      matches = Array.from(document.querySelectorAll(css));
    } catch {
      // An invalid selector throws a DOMException. Left unmapped it would reach the
      // model as EXTENSION_ERROR telling the user to reload the add-on, which
      // cannot help: the selector is the thing to fix.
      throw noTarget(`the CSS selector ${JSON.stringify(css)} is not valid CSS`);
    }
    return { node: exactlyOne(matches, `the CSS selector ${JSON.stringify(css)}`), elementId: "" };
  }
  if (!role || !name) {
    throw noTarget(
      'a role target needs both role and name, for example {"role": "button", ' +
        '"name": "Sign in"}. Call browser_read_page to see what is on the page now',
    );
  }
  const wantedRole = role.toLowerCase();
  const wantedName = name.toLowerCase();
  const matched = candidates().filter(
    (el) => roleFor(el).toLowerCase() === wantedRole && nameOf(el).toLowerCase() === wantedName,
  );
  return {
    node: exactlyOne(matched, `the ${role} named ${JSON.stringify(name)}`),
    elementId: "",
  };
}

/**
 * Refuse an element that is on the page but cannot be acted on, naming WHICH.
 *
 * Both halves matter and they fail differently. An INVISIBLE element is usually a
 * collapsed menu the model has to open first. A DISABLED one is usually a form with
 * a field still empty. "Element not found" for either would send the model looking
 * for a control that is right there.
 */
function requireActionable(resolved: Resolved): Element {
  if (!isRendered(resolved.node)) {
    throw notActionable(resolved, "on the page but not visible right now");
  }
  if (!isEnabled(resolved.node)) {
    throw notActionable(resolved, "on the page but not enabled right now");
  }
  return resolved.node;
}

/** What every acting result reports about the page it left behind. */
interface PageState {
  page_version: number;
  url: string;
  title: string;
  navigated: boolean;
}

/**
 * The page as it stands after an action, and whether it moved.
 *
 * `navigated` is honest about a narrow thing: whether the URL changed WITHIN THIS
 * TASK. A single-page app routing during its own click handler is caught, because
 * that handler has already run by the time `click` returns. A full document
 * navigation is NOT caught and cannot be — the content script is torn down with the
 * document, and this reply is sent before the new one exists. The daemon learns
 * about those from `browser.navigation_completed`, which is why that event exists.
 * Claiming otherwise here would make `navigated: false` read as "the click did
 * nothing" on exactly the clicks that did the most.
 *
 * `registry.noteUrl()` FIRST, so a route change the action caused moves
 * `page_version` before it is reported. Without it the result would carry the
 * pre-action version, the daemon would compare equal, and the next call would act
 * against a registry describing the page that has just been replaced.
 */
function pageState(before: string): PageState {
  registry.noteUrl();
  return {
    page_version: registry.pageVersion,
    url: location.href,
    title: document.title || "",
    navigated: location.href !== before,
  };
}

/** One mouse event, configured the way a real pointer produces it. */
function mouseInit(): MouseEventInit {
  return { bubbles: true, cancelable: true, composed: true, button: 0, buttons: 1, view: window };
}

/**
 * Click one element the way a page expects to be clicked.
 *
 * `HTMLElement.click()` alone dispatches a `click` event and nothing else, and a
 * large minority of real controls — drag handles, custom menus, anything built on a
 * pointer library — listen on `pointerdown`/`mousedown` and never see it. The user
 * would be told the button was pressed while the page did nothing at all. So the
 * full sequence is dispatched, and `click()` is called last because it is what
 * performs the element's DEFAULT ACTION (following a link, submitting a form),
 * which a synthesised `click` event cannot do.
 *
 * `scrollIntoView` first, for the user rather than for the code: the dispatch does
 * not need the element on screen, but a person watching their own browser should
 * see what Iron Jarvis just pressed.
 */
function clickNode(node: Element): void {
  node.scrollIntoView({ block: "center", inline: "nearest" });
  const focusable = node as Element & { focus?: (options?: FocusOptions) => void };
  if (typeof focusable.focus === "function") {
    focusable.focus({ preventScroll: true });
  }
  const down = { ...mouseInit(), isPrimary: true, pointerType: "mouse" };
  const up = { ...down, buttons: 0 };
  node.dispatchEvent(new PointerEvent("pointerdown", down));
  node.dispatchEvent(new MouseEvent("mousedown", mouseInit()));
  node.dispatchEvent(new PointerEvent("pointerup", up));
  node.dispatchEvent(new MouseEvent("mouseup", { ...mouseInit(), buttons: 0 }));
  const clickable = node as Element & { click?: () => void };
  if (typeof clickable.click === "function") {
    clickable.click();
    return;
  }
  // SVG and other non-HTML elements have no `click()`. The event alone still runs
  // every listener; only the default action is missing, and these elements have none.
  node.dispatchEvent(new MouseEvent("click", { ...mouseInit(), buttons: 0 }));
}

/** `click`: press one element and report what was pressed. */
export function click(params: Record<string, unknown>): ClickResult {
  const before = location.href;
  const resolved = resolveTarget(
    params["target"] as Record<string, unknown> | undefined,
    (params["snapshot_id"] as string | undefined) ?? null,
  );
  const node = requireActionable(resolved);
  // The reference is built BEFORE the click. A click that replaces its own button
  // (a "Sign in" that becomes a spinner) would otherwise be reported as having
  // pressed whatever the node has since become, or as having no name at all.
  const ref = targetRef(resolved);
  clickNode(node);
  const state = pageState(before);
  return {
    tab_id: Number(params["tab_id"] ?? 0),
    clicked: ref,
    url: state.url,
    title: state.title,
    page_version: state.page_version,
    navigated: state.navigated,
  };
}

// --- typing -----------------------------------------------------------------

/**
 * Empty one field without ever reading what was in it.
 *
 * The native prototype setter, not `node.value = ""`. React installs its own
 * `value` accessor on the instance to track changes; assigning through it marks the
 * new value as already-seen, so the component's state keeps the OLD text and the
 * form submits what the user thought had been replaced. Going through the prototype
 * descriptor writes the DOM underneath that tracker, and the `input` event dispatched
 * afterwards is then read as a genuine change.
 */
function clearField(node: Element): boolean {
  if (isEditableHost(node)) {
    // `replaceChildren()` with no arguments removes every child. It is a write; the
    // read-and-rewrite spelling would carry the field's contents into this script,
    // which section 9.4 forbids.
    node.replaceChildren();
    return true;
  }
  const setter = valueSetter(node);
  if (!setter) {
    return false;
  }
  setter.call(node, "");
  return true;
}

/**
 * The NATIVE `value` setter for `node`'s class, or `undefined`.
 *
 * Taken off the prototype rather than the instance on purpose — see `clearField`.
 * Returns `undefined` for anything that is not an input or a textarea, which is how
 * the callers tell "this is not a field" from "this field refused the write".
 */
function valueSetter(node: Element): ((this: Element, next: string) => void) | undefined {
  const proto =
    node instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : node instanceof HTMLInputElement
        ? HTMLInputElement.prototype
        : null;
  if (!proto) {
    return undefined;
  }
  return Object.getOwnPropertyDescriptor(proto, "value")?.set;
}

/**
 * Put the caret at the END of `node`, reading nothing out of it.
 *
 * The one thing an append needs and `focus()` does not provide. See `typeInto`'s
 * docstring for what goes wrong without it and why both spellings are write-only.
 *
 * Every branch is wrapped, because both APIs throw on shapes that reach here
 * legitimately: `setSelectionRange` raises `InvalidStateError` on the input types
 * that have no selection (`number`, `email` in some engines), and `getSelection()`
 * is null in a document without one. A caret that could not be moved is not a
 * reason to refuse the call — the insert still happens, at wherever the caret is —
 * so this function never throws.
 */
function collapseToEnd(node: Element): void {
  if (node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement) {
    try {
      // MAX_SAFE_INTEGER, not a length: the platform clamps both offsets to the end
      // of the content, which is how the caret is moved without this script ever
      // learning how long the text is.
      node.setSelectionRange(Number.MAX_SAFE_INTEGER, Number.MAX_SAFE_INTEGER);
    } catch {
      // An input type that has no selection. It also has no append problem.
    }
    return;
  }
  if (!isEditableHost(node)) {
    return;
  }
  try {
    const selection = getSelection();
    if (selection) {
      selection.selectAllChildren(node);
      selection.collapseToEnd();
    }
  } catch {
    // A detached or shadow-hosted region the selection API refuses. The insert
    // still runs; only the position is the host's choice.
  }
}

/** One `input` event of the shape a real edit produces. */
function fireInput(node: Element, inputType: string, data: string | null): void {
  node.dispatchEvent(
    new InputEvent("input", { bubbles: true, composed: true, cancelable: false, inputType, data }),
  );
}

/** The `change` a real edit produces when the user leaves the field. */
function fireChange(node: Element): void {
  node.dispatchEvent(new Event("change", { bubbles: true, cancelable: false }));
}

/**
 * Type `text` into `node`, and say whether it was cleared first.
 *
 * `execCommand("insertText")` is the insert, and the choice is load-bearing rather
 * than nostalgic. It inserts AT THE CARET, so it appends without this script ever
 * reading what the field already holds — which is the only way to honour "append"
 * and section 9.4's no-value rule at the same time. It also produces the real
 * `beforeinput`/`input` pair every framework's own change tracker listens for.
 *
 * When the host refuses it (it returns false) the fallback is deliberately
 * ASYMMETRIC:
 *
 *   * `clear: true` — the field was just emptied, so writing the whole text through
 *     the prototype setter is exactly what was asked for.
 *   * `clear: false` — the only way left to append is to read the field and write it
 *     back, and this script does not read fields. Overwriting instead would silently
 *     destroy whatever the user had already typed, in a call whose result says
 *     "typed". So it REFUSES and names the retry: `clear: true`.
 *
 * THE CARET IS PLACED BEFORE THE INSERT, and that line is load-bearing rather than
 * tidy. `focus()` on a control the user has never clicked into leaves the caret at
 * offset 0 with nothing selected, so an append would land at the FRONT: a name field
 * holding "John Smith" plus `browser_type {text: " Jr."}` becomes " Jr.John Smith",
 * the form submits, and the result still says `typed_into` because nothing here can
 * see what happened. Worse, `insertText` REPLACES a selection, so a field the page
 * (or a previous call) left selected is silently overwritten — the exact destruction
 * of the user's own text the `if (!clear)` refusal below exists to prevent, reached
 * by the path that refusal never runs on.
 *
 * `collapseToEnd` is spelled with WRITE-ONLY calls. `setSelectionRange` clamps to the
 * value's length without reporting it, and `Selection.selectAllChildren` +
 * `collapseToEnd` move the caret without reading a character; neither touches
 * `.value`, `.textContent`, `.selectionStart` or `.selectionEnd`, so section 9.4
 * holds. It runs BEFORE the `if (clear)` block so the clear path is unchanged: an
 * emptied field has one position and this is a no-op on it.
 */
function typeInto(node: Element, text: string, clear: boolean): boolean {
  const focusable = node as Element & { focus?: (options?: FocusOptions) => void };
  if (typeof focusable.focus === "function") {
    focusable.focus({ preventScroll: false });
  }
  collapseToEnd(node);
  let cleared = false;
  if (clear) {
    cleared = clearField(node);
    if (!cleared) {
      throw new BridgeError("EXTENSION_ERROR", {
        detail:
          "this field could not be emptied — it is not a text input, a textarea or " +
          "an editable region, so browser_type has nothing to type into",
      });
    }
    fireInput(node, "deleteContentBackward", null);
  }
  if (text) {
    const inserted = document.execCommand("insertText", false, text);
    if (!inserted) {
      if (!clear) {
        throw new BridgeError("EXTENSION_ERROR", {
          detail:
            "this field would only accept text by replacing everything already in " +
            "it, and Iron Jarvis will not overwrite what the user typed. Retry with " +
            "clear: true if replacing the field's contents is what you meant",
        });
      }
      const setter = valueSetter(node);
      if (!setter) {
        throw new BridgeError("EXTENSION_ERROR", {
          detail: "this field accepted neither an insert nor a value, so nothing was typed",
        });
      }
      setter.call(node, text);
      fireInput(node, "insertText", text);
    }
  }
  // `change` last, and unconditionally: a plain HTML form reads `change`, not
  // `input`, and a user who types and then clicks elsewhere always produces one.
  fireChange(node);
  return cleared;
}

// --- keys -------------------------------------------------------------------

/** The key events a real press produces, and whether the page consumed the press. */
function dispatchKey(node: Element, key: string): boolean {
  const init: KeyboardEventInit = {
    key,
    bubbles: true,
    cancelable: true,
    composed: true,
    view: window,
  };
  const consumed = !node.dispatchEvent(new KeyboardEvent("keydown", init));
  node.dispatchEvent(new KeyboardEvent("keyup", { ...init, cancelable: false }));
  return consumed;
}

/**
 * Press one key on `node`, and report honestly whether it SUBMITTED anything.
 *
 * A synthesised key event does not perform a default action — the browser only does
 * that for events it generated itself — so dispatching `Enter` at a login field and
 * reporting `submitted: true` would be a lie on the one call where the user most
 * needs the truth. This function therefore performs the default action itself, and
 * only where a real browser would:
 *
 *   * a single-line `<input>` inside a form submits it, through `requestSubmit()`
 *     and never `submit()`. `submit()` skips constraint validation AND the page's
 *     own `submit` listener, so a form that would have been rejected client-side is
 *     posted anyway, and a page that meant to intercept the submit never hears it;
 *   * a `<textarea>` inserts a newline, which is what Enter does there;
 *   * anything else does nothing beyond the events, and says so.
 *
 * `submitted` is true in exactly two cases, both real: the page CONSUMED the keydown
 * (it called preventDefault, which is how a JavaScript form takes the key for
 * itself), or `requestSubmit` ran. It is not a claim that the submission succeeded —
 * only the next page can say that, which is what `navigated` and the follow-up read
 * are for.
 */
function pressOn(node: Element, key: string): boolean {
  const consumed = dispatchKey(node, key);
  if (key !== "Enter" || consumed) {
    return consumed;
  }
  if (node instanceof HTMLTextAreaElement) {
    document.execCommand("insertText", false, "\n");
    return false;
  }
  if (node instanceof HTMLInputElement) {
    const form = node.form;
    if (form && typeof form.requestSubmit === "function") {
      form.requestSubmit();
      return true;
    }
  }
  return false;
}

/** `type_text`: fill one field and report WHAT was typed into, never what it holds. */
export function typeText(params: Record<string, unknown>): TypeTextResult {
  const before = location.href;
  const resolved = resolveTarget(
    params["target"] as Record<string, unknown> | undefined,
    (params["snapshot_id"] as string | undefined) ?? null,
  );
  const node = requireActionable(resolved);
  if (!isEditableField(node)) {
    // A model that aimed `browser_type` at a button needs to hear that, not a
    // success for text that went nowhere. `isEditableField` is the same predicate
    // the snapshot uses to decide a row is a field, so the answer here and the
    // description the model read cannot disagree.
    throw notActionable(resolved, "not a field, so there is nothing to type into");
  }
  const ref = targetRef(resolved);
  const text = String(params["text"] ?? "");
  const cleared = typeInto(node, text, params["clear"] === true);
  const submitted = params["press_enter"] === true ? pressOn(node, "Enter") : false;
  const state = pageState(before);
  return {
    tab_id: Number(params["tab_id"] ?? 0),
    // The element id, role and accessible name. There is no key for the text and
    // there must never be one: `browser_type` redacts `text` in the ledger, so a
    // result that echoed it would put the password back on disk by another route.
    typed_into: ref,
    cleared,
    submitted,
    url: state.url,
    title: state.title,
    page_version: state.page_version,
    navigated: state.navigated,
  };
}

/** `press_key`: send one key, to a named element or to whatever has focus. */
export function pressKey(params: Record<string, unknown>): PressKeyResult {
  const before = location.href;
  const key = String(params["key"] ?? "").trim();
  if (!key) {
    throw new BridgeError("EXTENSION_ERROR", {
      detail: "press_key needs a key name, for example 'Enter' or 'Escape'",
    });
  }
  const target = params["target"] as Record<string, unknown> | undefined;
  let node: Element;
  if (target && Object.keys(target).length > 0) {
    const resolved = resolveTarget(target, (params["snapshot_id"] as string | undefined) ?? null);
    node = requireActionable(resolved);
  } else {
    // No target: the key goes where the user's own keystroke would go.
    // `activeElement` is null in a document that has never been focused, and
    // `document.body` is where an untargeted key lands then.
    node = document.activeElement ?? document.body ?? document.documentElement;
  }
  pressOn(node, key);
  const state = pageState(before);
  return {
    tab_id: Number(params["tab_id"] ?? 0),
    key,
    url: state.url,
    title: state.title,
    page_version: state.page_version,
    navigated: state.navigated,
  };
}

// --- scrolling --------------------------------------------------------------

/**
 * How far one `up`/`down` step moves when the caller names no amount.
 *
 * Nine tenths of the viewport, which is what a page-down does and what a reader
 * expects: a full viewport leaves no overlap, so a line sitting at the fold is
 * skipped entirely and a model reading the next snapshot never sees it.
 */
function defaultStep(): number {
  return Math.max(1, Math.round(window.innerHeight * 0.9));
}

/** How far the document can scroll, floored at zero for a page that fits. */
function maxScroll(): number {
  const doc = document.documentElement;
  return Math.max(0, doc.scrollHeight - window.innerHeight);
}

/** Where the page ended up, in words a model can repeat to a user. */
function scrolledTo(): string {
  const limit = maxScroll();
  const at = Math.round(window.scrollY);
  if (limit <= 0) {
    return "the whole page (it does not scroll)";
  }
  if (at <= 0) {
    return "the top";
  }
  if (at >= limit - 1) {
    return "the bottom";
  }
  return `${at}px of ${limit}px`;
}

/**
 * `scroll`: move the user's view, and say where it ended up.
 *
 * An unknown direction is REFUSED rather than ignored. The daemon's
 * `protocol.scroll_params` refuses it first; this is the backstop, and it earns its
 * place because without it a direction outside the four falls through every branch,
 * scrolls nowhere and answers success — a silent no-op a model reads as "done" and
 * then reasons from, reporting content that is still off screen as absent.
 */
export function scroll(params: Record<string, unknown>): ScrollResult {
  const before = location.href;
  const direction = String(params["direction"] ?? "");
  const rawAmount = Number(params["amount"]);
  const amount =
    Number.isFinite(rawAmount) && rawAmount > 0 ? Math.round(rawAmount) : defaultStep();
  if (direction === SCROLL_UP) {
    window.scrollBy({ top: -amount, behavior: "auto" });
  } else if (direction === SCROLL_DOWN) {
    window.scrollBy({ top: amount, behavior: "auto" });
  } else if (direction === SCROLL_TOP) {
    window.scrollTo({ top: 0, behavior: "auto" });
  } else if (direction === SCROLL_BOTTOM) {
    window.scrollTo({ top: maxScroll(), behavior: "auto" });
  } else {
    throw new BridgeError("EXTENSION_ERROR", {
      detail:
        `scroll direction ${JSON.stringify(direction)} is not one of ` +
        `${SCROLL_UP}, ${SCROLL_DOWN}, ${SCROLL_TOP}, ${SCROLL_BOTTOM}`,
    });
  }
  const state = pageState(before);
  return {
    tab_id: Number(params["tab_id"] ?? 0),
    scrolled_to: scrolledTo(),
    url: state.url,
    title: state.title,
    page_version: state.page_version,
  };
}
