// v1.458.0: "Record a task" — what the USER does in this page, step by step.
//
// The owner: "record a browser based task by the user and that becomes a skill
// for a browser agent to use in the future." The sidebar's Record button asks
// the worker to start; the worker asks this page reader (and every page the tab
// loads next). Each step goes back to the worker as its own message, the worker
// keeps the list, and on Stop the sidebar shows it for review before anything
// is saved.
//
// WHAT A STEP SAYS is what a snapshot says about one element and nothing more:
// its ROLE and ACCESSIBLE NAME (the targeting the browser tools re-find on the
// live page), its tag, a field's declared type, the visible text outside any
// field, and a CSS selector checked to match only it (`describePicked`, the
// "Point at it" reader). Every word is masked (`mask.ts`) before it leaves the
// tab.
//
// WHAT IT NEVER READS: what the user typed or chose. The add-on's rule
// (`scrub.ts`, pinned by tests/test_browser_content_script_v1236.py) is that no
// field's value is collected, ever, and a recording keeps it: a "type" step
// says WHICH field was typed into, never the text — the sidebar turns it into a
// named value the skill asks for each time, unless the user types a fixed text
// there themselves. A password, payment or file field is marked `sensitive`
// and is never given a value at all.
//
// ONLY THE PERSON'S OWN ACTIONS. An event no person made (`isTrusted` false: a
// page script, or Iron Jarvis's own click) records nothing, and while Iron
// Jarvis itself acts on the page (content/index.ts wraps every acting op in
// `whileActing`) even the trusted events its typing produces are ignored. A
// click that only POINTS ("Point at it") is the picker's, not a step.

import { RECORDING_IDLE_STOP_MS } from "../protocol";
import { RECORD_IDLE_KIND, RECORDED_STEP_KIND } from "./channel";
import { maskText } from "./mask";
import { describePicked, isPicking, meantElement } from "./picker";
import { fieldType, isEditableField, isSensitiveField } from "./scrub";

/** The keys a recording keeps (protocol.RECORDING_KEYS). */
export const RECORDED_KEYS: ReadonlySet<string> = new Set(["Enter", "Escape"]);

/** Input types that are a button or a box to tick, not text to type. */
const NOT_TEXT_TYPES: ReadonlySet<string> = new Set([
  "button",
  "submit",
  "reset",
  "image",
  "checkbox",
  "radio",
  "range",
  "color",
  "file",
  "hidden",
]);

/** What a step says about an element: page words, bounded and masked. */
export interface RecordedTarget {
  role: string;
  name: string;
  text: string;
  tag: string;
  input_type: string;
  css: string;
  unique_name: boolean;
}

/** One step the user took. `at` is the page's clock, for ordering only. */
export interface RecordedStep {
  kind: "click" | "type" | "select" | "key" | "file";
  target?: RecordedTarget;
  key?: string;
  sensitive?: boolean;
  at: number;
}

/** The element's description, as a step carries it. Never a field's value. */
export function targetOf(el: Element): RecordedTarget {
  const d = describePicked(el);
  return {
    role: d.role,
    name: maskText(d.name),
    text: maskText(d.text),
    tag: d.tag,
    input_type: d.input_type,
    css: d.css,
    unique_name: d.unique_name,
  };
}

/** Whether `el` is somewhere a person TYPES (not a button, box or list). */
export function isTextEntry(el: Element): boolean {
  if (!isEditableField(el)) {
    return false;
  }
  if (el.tagName === "SELECT") {
    return false;
  }
  if (el.tagName === "INPUT") {
    return !NOT_TEXT_TYPES.has(fieldType(el));
  }
  return true;
}

/** The field an event's target belongs to (a node inside an editable region
 *  means the region), or null. */
export function fieldOf(node: EventTarget | null): Element | null {
  if (!(node instanceof Element)) {
    return null;
  }
  if (isEditableField(node)) {
    return node;
  }
  const host = node.closest("[contenteditable]");
  return host && isEditableField(host) ? host : null;
}

/** A click on `node`, or null when the click is not a step of its own: a click
 *  into a text box (the typing that follows is the step), on a list (the
 *  choice is) or on a file field (the chosen file is). */
export function stepForClick(node: Element, at = Date.now()): RecordedStep | null {
  const el = meantElement(node);
  if (isTextEntry(el) || el.tagName === "SELECT") {
    return null;
  }
  if (el.tagName === "INPUT" && fieldType(el) === "file") {
    return null;
  }
  return { kind: "click", target: targetOf(el), at };
}

/** The user finished typing into `el`. Says WHICH field, never what. */
export function stepForTyping(el: Element, at = Date.now()): RecordedStep | null {
  if (!isTextEntry(el)) {
    return null;
  }
  return { kind: "type", target: targetOf(el), sensitive: isSensitiveField(el) || fieldType(el) === "password", at };
}

/** A `change` the user made on a list or a file field. */
export function stepForChange(el: Element, at = Date.now()): RecordedStep | null {
  if (el.tagName === "SELECT") {
    return { kind: "select", target: targetOf(el), at };
  }
  if (el.tagName === "INPUT" && fieldType(el) === "file") {
    return { kind: "file", target: targetOf(el), sensitive: true, at };
  }
  return null;
}

/** A key the recording keeps, pressed in `el` (a field) or on the page. */
export function stepForKey(key: string, el: Element | null, at = Date.now()): RecordedStep | null {
  if (!RECORDED_KEYS.has(key)) {
    return null;
  }
  const step: RecordedStep = { kind: "key", key, at };
  if (el && isEditableField(el)) {
    step.target = targetOf(el);
  }
  return step;
}

// --- the live recording -------------------------------------------------------

let active = false;
/** Iron Jarvis is acting on the page right now (content/index.ts). */
let acting = 0;
/** Fields the user typed into and has not left yet. */
const dirty = new Set<Element>();
let stopListening: (() => void) | null = null;
/** A recording left with nothing happening stops by itself. */
let idleTimer: ReturnType<typeof setTimeout> | null = null;

function armIdle(): void {
  if (idleTimer !== null) {
    clearTimeout(idleTimer);
  }
  idleTimer = setTimeout(() => {
    idleTimer = null;
    if (!active) {
      return;
    }
    stopRecording();
    try {
      void chrome.runtime.sendMessage({ kind: RECORD_IDLE_KIND }).catch(() => {});
    } catch {
      // Nobody to tell.
    }
  }, RECORDING_IDLE_STOP_MS);
}

/** Hand a step to the worker; a closed worker is not this page's problem. */
function send(step: RecordedStep | null): void {
  if (!step) {
    return;
  }
  armIdle();
  try {
    void chrome.runtime.sendMessage({ kind: RECORDED_STEP_KIND, step }).catch(() => {});
  } catch {
    // The add-on was reloaded under this page: nobody to tell.
  }
}

/** Whether this event is one the recording may read: a person's, while
 *  recording, not while Iron Jarvis acts, not the picker's. */
export function counts(event: Event): boolean {
  return active && acting === 0 && event.isTrusted && !isPicking();
}

/** Typing in `el` is over: one "type" step for it. */
function flush(el: Element): void {
  if (dirty.delete(el)) {
    send(stepForTyping(el));
  }
}

function flushAll(except: Element | null = null): void {
  for (const el of Array.from(dirty)) {
    if (el !== except) {
      flush(el);
    }
  }
}

/** Run an action Iron Jarvis takes in the page without recording what it does. */
export function whileActing<T>(fn: () => T): T {
  acting += 1;
  try {
    return fn();
  } finally {
    acting -= 1;
  }
}

/**
 * How soon after Enter in a field a click with no pointer (`detail` 0) is the
 * click the BROWSER made: Enter in a form field "presses" the form's submit
 * button for the user (implicit submission), and that click arrives trusted.
 * Recording it too would replay the submit twice.
 */
export const ENTER_CLICK_MS = 1000;

/**
 * Whether a click is the one the browser made for an Enter just recorded in a
 * field (`enterAt`, 0 = none): no pointer behind it (`detail` 0) and soon after.
 */
export function clickFromEnter(detail: number, now: number, enterAt: number): boolean {
  return detail === 0 && enterAt > 0 && now - enterAt >= 0 && now - enterAt <= ENTER_CLICK_MS;
}

/** Whether a recording is running in this page. */
export function isRecording(): boolean {
  return active;
}

/** Start recording in this page. Idempotent: the worker sends it after every load. */
export function startRecording(): { started: boolean } {
  if (active) {
    return { started: true };
  }
  active = true;
  armIdle();
  /** When Enter was last recorded in a field (implicit submission follows). */
  let enterAt = 0;
  const onClick = (event: MouseEvent): void => {
    if (!counts(event) || !(event.target instanceof Element)) {
      return;
    }
    if (clickFromEnter(event.detail, Date.now(), enterAt)) {
      // The browser pressed the form's button for the Enter already recorded.
      enterAt = 0;
      return;
    }
    flushAll(fieldOf(event.target));
    send(stepForClick(event.target));
  };
  const onInput = (event: Event): void => {
    if (!counts(event)) {
      return;
    }
    const field = fieldOf(event.target);
    if (field && isTextEntry(field)) {
      dirty.add(field);
    }
  };
  const onChange = (event: Event): void => {
    if (!counts(event) || !(event.target instanceof Element)) {
      return;
    }
    const field = fieldOf(event.target);
    if (field && isTextEntry(field)) {
      flush(field);
      return;
    }
    send(stepForChange(event.target));
  };
  const onFocusOut = (event: FocusEvent): void => {
    if (!counts(event)) {
      return;
    }
    const field = fieldOf(event.target);
    if (field) {
      flush(field);
    }
  };
  const onKey = (event: KeyboardEvent): void => {
    if (!counts(event) || !RECORDED_KEYS.has(event.key)) {
      return;
    }
    const field = fieldOf(event.target);
    if (event.key === "Enter" && !field) {
      // Enter on a focused button or link "clicks" it: the click that follows
      // is the step (with the element it pressed), not a bare key press.
      return;
    }
    // The typing comes first: Enter in a search box means "type, then press Enter".
    if (field) {
      flush(field);
    }
    if (event.key === "Enter") {
      enterAt = Date.now();
    }
    send(stepForKey(event.key, field));
  };
  const onLeave = (): void => {
    if (active) {
      flushAll();
    }
  };
  addEventListener("click", onClick, true);
  addEventListener("input", onInput, true);
  addEventListener("change", onChange, true);
  addEventListener("focusout", onFocusOut, true);
  addEventListener("keydown", onKey, true);
  addEventListener("pagehide", onLeave, true);
  stopListening = () => {
    removeEventListener("click", onClick, true);
    removeEventListener("input", onInput, true);
    removeEventListener("change", onChange, true);
    removeEventListener("focusout", onFocusOut, true);
    removeEventListener("keydown", onKey, true);
    removeEventListener("pagehide", onLeave, true);
  };
  return { started: true };
}

/** Stop recording; typing still pending is sent first. */
export function stopRecording(): { stopped: boolean } {
  const had = active;
  if (active) {
    flushAll();
  }
  active = false;
  dirty.clear();
  if (idleTimer !== null) {
    clearTimeout(idleTimer);
    idleTimer = null;
  }
  stopListening?.();
  stopListening = null;
  return { stopped: had };
}
