// The DOM and accessibility walk: one page, in, as a bounded semantic snapshot.
//
// This is the only code in Iron Jarvis that reads a page the user is signed in to,
// so three properties matter more than completeness.
//
// 1. NO FIELD VALUE IS EVER COLLECTED. Not by this file and not by anything it
//    calls. `scrub.ts` holds the rule and the one narrow exception (a submit
//    button's label), and this file descends no further than a FIELD — which means
//    a `contenteditable` region as well as a form control (`scrub.isEditableField`),
//    because the registry reports one as a `textbox` and the promise is about what
//    the model can see, not about what tag the author reached for. The same rule
//    governs every route a container's TEXT can be read by: `textOutsideFields` is
//    the one reader of `textContent` in this file, and it descends into no field, so
//    a wrapping `<label>` cannot carry a select's current option into a name.
//
// 2. TRUNCATION IS ALWAYS REPORTED. Every limit in plan section 9.2 is enforced
//    here, and every limit that bites sets `truncated`, names itself in
//    `truncation`, and reports the PRE-TRIM total in `counts` so the daemon's line
//    can say roughly how much was dropped rather than "the page has more". The
//    reason is stated in the plan and worth repeating: a silently short page reads
//    as a complete one, and the model then tells the user that content does not
//    exist. `counts` is the ONLY channel for that figure — the add-on trims first,
//    so the arriving length is already at the cap and the daemon cannot otherwise
//    know what was lost.
//
// 3. THE FRAME MUST FIT. `MAX_FRAME_BYTES` is enforced by the daemon BEFORE
//    `json.loads`, and a refused frame is dropped unread — its request id is
//    unknowable, so the command it answered burns its whole timeout and reports
//    ACTION_TIMEOUT ("your browser did not answer in time") when the browser
//    answered immediately, just too largely. `extension_backend.py`'s own header
//    says the fix belongs on this side. So `fitToFrame` measures the encoded frame
//    here and shrinks it, reporting every shrink as truncation.
//
// What this walk does NOT see, stated plainly because a model that thinks it read
// everything will answer confidently about a page it half read:
//   * cross-origin `<iframe>` content. A content script is injected per frame and
//     this snapshot is the top document only, so an embedded PDF viewer, a payment
//     form or a chat widget reads as absent rather than as unreadable.
//   * a CLOSED shadow root. An OPEN one is walked — `el.shadowRoot` is readable
//     from an isolated world, and a page built out of web components is otherwise
//     an empty snapshot marked complete — but a closed root reads as `null`, which
//     is indistinguishable from a component that has none, so it cannot even be
//     counted. This sentence is the whole disclosure available.
//   * anything below `MAX_AX_DEPTH` levels or after `MAX_AX_NODES` nodes — both of
//     which ARE reported, which is the difference between a bounded read and a lie.
//
// The accessible NAME here is an approximation of the AccName algorithm, not an
// implementation of it: `aria-labelledby`, then `aria-label`, then a control's own
// label element, then its placeholder, then a button's label attribute, then the
// element's own text, then `title`. Chrome's real computed name is only available
// through the debugger protocol, which this add-on deliberately does not use.

import {
  FULL_TEXT_CHARS,
  MAX_AX_DEPTH,
  MAX_AX_NODES,
  MAX_ELEMENTS,
  MAX_FRAME_BYTES,
  MAX_HEADINGS,
  MAX_LINKS,
  MAX_NAME_CHARS,
  MAX_TEXT_CHARS,
  MODE_FULL,
  MODE_INTERACTIVE,
  MODE_SUMMARY,
  SUMMARY_TEXT_CHARS,
  type ElementRow,
  type ElementsResult,
  type FormRow,
  type HeadingRow,
  type LinkRow,
  type SnapshotResult,
  type TruncationRow,
} from "../protocol";
import { BridgeError } from "../bridge/errors";
import { explicitRole, isInteractive, landmarkRole, registry } from "./elements";
import {
  buttonLabel,
  fieldFacts,
  fieldType,
  isEditableField,
  isEditableHost,
  isField,
  stopsTheWalk,
} from "./scrub";

/** What one mode includes. Mirrors `snapshot.MODE_SPECS` on the Python side. */
export interface ContentModeSpec {
  name: string;
  textChars: number;
  elements: boolean;
  headings: boolean;
  forms: boolean;
  links: boolean;
  landmarks: boolean;
}

/**
 * The three modes, as data.
 *
 * These values MUST match `iron_jarvis.browser.snapshot.MODE_SPECS`. They are not
 * generated, because the generator carries constants and not tables; the text
 * budgets are the generated constants, so the numbers cannot drift even though the
 * table is written twice. The booleans are pinned from Python by
 * `tests/test_browser_content_script_v1236.py`.
 */
export const MODE_SPECS: Readonly<Record<string, ContentModeSpec>> = {
  [MODE_SUMMARY]: {
    name: MODE_SUMMARY,
    textChars: SUMMARY_TEXT_CHARS,
    elements: false,
    headings: true,
    forms: false,
    links: false,
    landmarks: false,
  },
  [MODE_INTERACTIVE]: {
    name: MODE_INTERACTIVE,
    textChars: MAX_TEXT_CHARS,
    elements: true,
    headings: true,
    forms: true,
    links: true,
    landmarks: false,
  },
  [MODE_FULL]: {
    name: MODE_FULL,
    textChars: FULL_TEXT_CHARS,
    elements: true,
    headings: true,
    forms: true,
    links: true,
    landmarks: true,
  },
};

/**
 * The mode record for `value`, falling back to `interactive`.
 *
 * LENIENT on purpose, where the daemon is strict. The daemon refuses an unknown
 * mode with a model-facing message naming the three; by the time a request reaches
 * the page the mode has already been validated, so a page that refused here would
 * answer EXTENSION_ERROR — "your browser reported an error" — and blame a browser
 * that is working perfectly.
 */
export function modeSpec(value: unknown): ContentModeSpec {
  const name = typeof value === "string" ? value.trim() : "";
  return MODE_SPECS[name] ?? (MODE_SPECS[MODE_INTERACTIVE] as ContentModeSpec);
}

/** The caps one capture runs under. */
export interface Limits {
  textChars: number;
  elements: number;
  headings: number;
  links: number;
  nameChars: number;
  axDepth: number;
  axNodes: number;
}

/**
 * `requested` when it is a positive integer below `ceiling`, else `ceiling`.
 *
 * A caller may only NARROW, and junk narrows nothing — the same rule as
 * `snapshot.SnapshotLimits.for_mode`, for the same reason: a request that raised a
 * cap would make the limit advisory, and the frame cap is not advisory.
 */
export function narrow(ceiling: number, requested: unknown): number {
  if (typeof requested !== "number" || !Number.isInteger(requested) || requested <= 0) {
    return ceiling;
  }
  return Math.min(requested, ceiling);
}

/** The caps for `spec`, narrowed by whatever the daemon asked for. */
export function limitsFor(spec: ContentModeSpec, params: Record<string, unknown>): Limits {
  return {
    textChars: narrow(spec.textChars, params["max_chars"]),
    elements: narrow(MAX_ELEMENTS, params["max_elements"]),
    headings: narrow(MAX_HEADINGS, params["max_headings"]),
    links: narrow(MAX_LINKS, params["max_links"]),
    nameChars: narrow(MAX_NAME_CHARS, params["max_name_chars"]),
    axDepth: narrow(MAX_AX_DEPTH, params["max_ax_depth"]),
    axNodes: narrow(MAX_AX_NODES, params["max_ax_nodes"]),
  };
}

/** Tags whose subtree is never text and never interactive. */
const SKIP_TAGS: ReadonlySet<string> = new Set([
  "AUDIO",
  "CANVAS",
  "EMBED",
  "HEAD",
  "IFRAME",
  "LINK",
  "MAP",
  "META",
  "NOSCRIPT",
  "OBJECT",
  "SCRIPT",
  "STYLE",
  "SVG",
  "TEMPLATE",
  "TITLE",
  "TRACK",
  "VIDEO",
]);

/** Tags that end a line of page text, so the snapshot reads as a page and not a run. */
const BLOCK_TAGS: ReadonlySet<string> = new Set([
  "ADDRESS",
  "ARTICLE",
  "ASIDE",
  "BLOCKQUOTE",
  "DD",
  "DETAILS",
  "DIALOG",
  "DIV",
  "DL",
  "DT",
  "FIELDSET",
  "FIGCAPTION",
  "FIGURE",
  "FOOTER",
  "FORM",
  "H1",
  "H2",
  "H3",
  "H4",
  "H5",
  "H6",
  "HEADER",
  "HR",
  "LI",
  "MAIN",
  "NAV",
  "OL",
  "P",
  "PRE",
  "SECTION",
  "SUMMARY",
  "TABLE",
  "TD",
  "TH",
  "TR",
  "UL",
]);

/** Tag -> implicit ARIA role, for the roles a snapshot names. */
const TAG_ROLES: Readonly<Record<string, string>> = {
  A: "link",
  ARTICLE: "article",
  BUTTON: "button",
  H1: "heading",
  H2: "heading",
  H3: "heading",
  H4: "heading",
  H5: "heading",
  H6: "heading",
  IMG: "image",
  LI: "listitem",
  OL: "list",
  OPTION: "option",
  SELECT: "combobox",
  SUMMARY: "button",
  TABLE: "table",
  TEXTAREA: "textbox",
  UL: "list",
};

/** `input[type]` -> the role a snapshot reports. Password is a textbox (D13B). */
const INPUT_ROLES: Readonly<Record<string, string>> = {
  button: "button",
  checkbox: "checkbox",
  color: "textbox",
  date: "textbox",
  "datetime-local": "textbox",
  email: "textbox",
  file: "button",
  image: "button",
  month: "textbox",
  number: "spinbutton",
  password: "textbox",
  radio: "radio",
  range: "slider",
  reset: "button",
  search: "searchbox",
  submit: "button",
  tel: "textbox",
  text: "textbox",
  time: "textbox",
  url: "textbox",
  week: "textbox",
};

/** How many bytes of the frame the snapshot itself may use. */
export const FRAME_BUDGET_BYTES = MAX_FRAME_BYTES - 8192;

/** `el.tagName`, uppercased, so an SVG element compares like an HTML one. */
function tagOf(el: Element): string {
  return el.tagName.toUpperCase();
}

/** Runs of whitespace collapsed to one space. Never trimmed here. */
function flatten(value: string | null | undefined): string {
  return (value ?? "").replace(/\s+/g, " ");
}

/**
 * The words inside `el`, with every FIELD skipped. The only `textContent` reader.
 *
 * `el.textContent` on a container returns the contents of the controls inside it
 * too, and that is a value read wearing a different hat: `<label>Filing status
 * <select><option>Head of household</option></select></label>` yields the user's
 * current selection, on a row that says `value: null` beside it. A wrapping label, an
 * `aria-labelledby` target, a heading and an element row's own `text` all read a
 * container this way, so all four read it through here.
 *
 * A skipped field contributes one space rather than nothing, so the words on either
 * side of it do not run together into a term the model then quotes back.
 *
 * Depth-bounded by `MAX_AX_DEPTH` like the walk itself: this recurses, and a name is
 * not worth a stack overflow on a pathological page.
 */
function textOutsideFields(el: Element): string {
  if (stopsTheWalk(el)) {
    return "";
  }
  const pieces: string[] = [];
  const collect = (node: Element, depth: number): void => {
    for (const child of Array.from(node.childNodes)) {
      if (child.nodeType === Node.TEXT_NODE) {
        pieces.push(child.textContent ?? "");
        continue;
      }
      if (child.nodeType !== Node.ELEMENT_NODE) {
        continue;
      }
      const element = child as Element;
      if (stopsTheWalk(element) || SKIP_TAGS.has(tagOf(element))) {
        pieces.push(" ");
        continue;
      }
      if (depth >= MAX_AX_DEPTH) {
        continue;
      }
      collect(element, depth + 1);
    }
  };
  collect(el, 0);
  return flatten(pieces.join(""));
}

/** `value` clipped to `limit`, and whether the clip happened. */
function clip(value: string, limit: number): [string, boolean] {
  if (limit <= 0 || value.length <= limit) {
    return [value, false];
  }
  return [value.slice(0, limit), true];
}

/**
 * Whether `el` is rendered at all.
 *
 * `checkVisibility` is one call into the engine and answers display, visibility and
 * `content-visibility` together; the fallback is for a host that lacks it. Note what
 * is NOT checked: size, opacity and viewport position. A zero-height wrapper with
 * visible children, a fading element mid-transition and everything below the fold
 * are all part of the page, and skipping them would drop text the user can plainly
 * see by scrolling.
 */
function isRendered(el: Element): boolean {
  const probe = el as Element & { checkVisibility?: (options?: unknown) => boolean };
  if (typeof probe.checkVisibility === "function") {
    // `contentVisibilityAuto` is deliberately NOT passed. Turning it on answers
    // false for an element whose rendering is being skipped because it sits in an
    // off-screen `content-visibility: auto` subtree — which is a viewport test by
    // another name, and `content-visibility: auto` is a routine performance measure
    // on long pages. The whole subtree would vanish from the text and from the
    // registry with nothing marked truncated: a short page that reads as complete.
    return probe.checkVisibility({ checkVisibilityCSS: true });
  }
  const box = el as Element & { offsetParent?: unknown };
  return box.offsetParent !== null || el.getClientRects().length > 0;
}

/** Whether the accessibility tree hides `el` and everything under it. */
function ariaHidden(el: Element): boolean {
  return (el.getAttribute("aria-hidden") ?? "").trim().toLowerCase() === "true";
}

/** Whether the control is enabled, by attribute and by ARIA. */
function isEnabled(el: Element): boolean {
  if (el.hasAttribute("disabled")) {
    return false;
  }
  return (el.getAttribute("aria-disabled") ?? "").trim().toLowerCase() !== "true";
}

/** The role a snapshot reports for `el`: explicit first, then the tag's own. */
export function roleFor(el: Element): string {
  const explicit = explicitRole(el);
  if (explicit) {
    return explicit;
  }
  const tag = tagOf(el);
  if (tag === "INPUT") {
    return INPUT_ROLES[fieldType(el)] ?? "textbox";
  }
  if (tag === "SELECT" && el.hasAttribute("multiple")) {
    return "listbox";
  }
  if (isEditableHost(el)) {
    return "textbox";
  }
  return TAG_ROLES[tag] ?? "";
}

/** The text of the elements `aria-labelledby` points at, in the order it names them. */
function labelledByText(el: Element): string {
  const raw = (el.getAttribute("aria-labelledby") ?? "").trim();
  if (!raw) {
    return "";
  }
  const parts: string[] = [];
  for (const id of raw.split(/\s+/)) {
    const target = id ? document.getElementById(id) : null;
    if (target && !isEditableField(target)) {
      // Not a field: `aria-labelledby` pointing at an input — or at a
      // `contenteditable` region — would make the label that control's CONTENTS,
      // which is exactly the read this add-on never makes. And the target may still
      // CONTAIN a control one level down, so its text comes through the skipping
      // reader rather than through `textContent`.
      parts.push(textOutsideFields(target).trim());
    }
  }
  return parts.filter((part) => part.length > 0).join(" ");
}

/** The text of the `<label>`s a control is associated with. */
function labelText(el: Element): string {
  const labelled = el as Element & { labels?: NodeListOf<HTMLLabelElement> | null };
  const labels = labelled.labels;
  if (labels && labels.length) {
    return Array.from(labels)
      .map((label) => textOutsideFields(label).trim())
      .filter((part) => part.length > 0)
      .join(" ");
  }
  // A WRAPPING label contains the control, so its `textContent` is the label text
  // AND the control's contents — `<label>Notes <textarea>a draft</textarea></label>`
  // names the field "Notes a draft". `textOutsideFields` is what makes it "Notes".
  const wrapping = el.closest("label");
  return wrapping ? textOutsideFields(wrapping).trim() : "";
}

/**
 * The accessible name of `el`, bounded, and whether the bound bit.
 *
 * The order is the AccName order this add-on implements (see the file header). Each
 * candidate is tried whole: a name that fell back to `title` when `aria-label` was
 * present would describe the wrong thing, and a model reading a snapshot cannot
 * tell that it has been handed the tooltip instead of the label.
 */
export function accessibleName(el: Element, nameChars: number): [string, boolean] {
  const candidates: string[] = [labelledByText(el), flatten(el.getAttribute("aria-label")).trim()];
  if (isEditableField(el)) {
    // A FIELD is named by what is written ABOUT it, never by what is written IN it:
    // the contents fall-through below would make a chat composer's half-typed
    // message its own accessible name.
    candidates.push(
      labelText(el),
      flatten(el.getAttribute("placeholder")).trim(),
      buttonLabel(el),
      flatten(el.getAttribute("name")).trim(),
    );
  } else {
    candidates.push(textOutsideFields(el).trim());
    const image = el.querySelector("img[alt]");
    candidates.push(image ? flatten(image.getAttribute("alt")).trim() : "");
  }
  candidates.push(flatten(el.getAttribute("alt")).trim(), flatten(el.getAttribute("title")).trim());
  for (const candidate of candidates) {
    if (candidate) {
      return clip(candidate, nameChars);
    }
  }
  return ["", false];
}

/** What a walk produced, before it is shaped into a result. */
interface Capture {
  snapshotId: string;
  pageVersion: number;
  text: string;
  textTotal: number;
  headings: HeadingRow[];
  headingsTotal: number;
  elements: ElementRow[];
  elementsTotal: number;
  links: LinkRow[];
  linksTotal: number;
  forms: FormRow[];
  shortenedNames: number;
  depthHit: boolean;
  nodeBudgetHit: boolean;
}

/** Mutable state threaded through the recursion. */
interface WalkState {
  pieces: string[];
  nodesVisited: number;
  depthHit: boolean;
  nodeBudgetHit: boolean;
  headings: HeadingRow[];
  headingsTotal: number;
  elements: ElementRow[];
  elementsTotal: number;
  links: LinkRow[];
  linksTotal: number;
  registered: { id: string; node: Element }[];
  shortenedNames: number;
}

/**
 * Walk the document once, building everything the mode asks for.
 *
 * ONE walk, not one per section: a second pass over a live page would see a
 * different page, and the links would then reference element ids from a registry the
 * elements list no longer matches.
 */
function walk(spec: ContentModeSpec, limits: Limits): Capture {
  // Before the id is minted: a `pushState` since the last read must move
  // `page_version` first, or this snapshot would carry the previous page's version
  // and every id in it would present as current.
  registry.noteUrl();
  const snapshotId = registry.begin();
  const state: WalkState = {
    pieces: [],
    nodesVisited: 0,
    depthHit: false,
    nodeBudgetHit: false,
    headings: [],
    headingsTotal: 0,
    elements: [],
    elementsTotal: 0,
    links: [],
    linksTotal: 0,
    registered: [],
    shortenedNames: 0,
  };
  const root = document.body ?? document.documentElement;
  if (root) {
    visit(root, 0, spec, limits, state);
  }
  const handle = registry.finish();
  const whole = state.pieces
    .join("")
    .replace(/[ \t]*\n[ \t]*/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/ {2,}/g, " ")
    .trim();
  const [text] = clip(whole, limits.textChars);
  return {
    snapshotId,
    pageVersion: handle.pageVersion,
    text,
    textTotal: whole.length,
    headings: state.headings,
    headingsTotal: state.headingsTotal,
    elements: state.elements,
    elementsTotal: state.elementsTotal,
    links: state.links,
    linksTotal: state.linksTotal,
    forms: spec.forms ? formRows(state.registered) : [],
    shortenedNames: state.shortenedNames,
    depthHit: state.depthHit,
    nodeBudgetHit: state.nodeBudgetHit,
  };
}

/** Visit one element: describe it, then its children, within both budgets. */
function visit(
  el: Element,
  depth: number,
  spec: ContentModeSpec,
  limits: Limits,
  state: WalkState,
): void {
  if (state.nodesVisited >= limits.axNodes) {
    state.nodeBudgetHit = true;
    return;
  }
  state.nodesVisited += 1;
  const tag = tagOf(el);
  if (SKIP_TAGS.has(tag)) {
    return;
  }
  if (ariaHidden(el) || !isRendered(el)) {
    return;
  }

  describe(el, tag, spec, limits, state);

  if (tag === "BR") {
    state.pieces.push("\n");
    return;
  }
  if (stopsTheWalk(el)) {
    // A form control's children ARE its value (see `scrub.stopsTheWalk`).
    return;
  }
  if (depth >= limits.axDepth) {
    // Reported ONLY when something was actually left behind. A leaf that happens to
    // sit at the depth limit lost nothing, and claiming the accessibility depth bit
    // there would put a truncation line on a snapshot that is in fact complete —
    // over-reporting is as misleading as under-reporting, in the other direction.
    state.depthHit = state.depthHit || hasContentBelow(el);
    return;
  }
  // An OPEN shadow root FIRST, then the light children. The shadow tree is what
  // the browser renders, so it reads in the right order; the light children are
  // still walked because unslotted ones are the component's own configuration and
  // slotted ones are rendered exactly once (a `<slot>` holds only fallback content,
  // never the assigned nodes, so nothing is collected twice). Without this a page
  // built out of web components — every Lit, Polymer or Lightning app — returns
  // empty text, no elements and `truncated: false`: absent AND unstated.
  const nodes: Node[] = [];
  const shadow = (el as Element & { shadowRoot?: ShadowRoot | null }).shadowRoot ?? null;
  if (shadow) {
    nodes.push(...Array.from(shadow.childNodes));
  }
  nodes.push(...Array.from(el.childNodes));
  for (const child of nodes) {
    if (child.nodeType === Node.TEXT_NODE) {
      const piece = flatten(child.textContent);
      if (piece.trim()) {
        state.pieces.push(piece);
      }
      continue;
    }
    if (child.nodeType === Node.ELEMENT_NODE) {
      visit(child as Element, depth + 1, spec, limits, state);
    }
  }
  if (BLOCK_TAGS.has(tag)) {
    state.pieces.push("\n");
  }
}

/** Whether stopping at `el` would lose an element or any words. */
function hasContentBelow(el: Element): boolean {
  if (el.firstElementChild !== null) {
    return true;
  }
  return (el.textContent ?? "").trim().length > 0;
}

/** Record `el` in whichever sections the mode includes. */
function describe(
  el: Element,
  tag: string,
  spec: ContentModeSpec,
  limits: Limits,
  state: WalkState,
): void {
  if (spec.headings) {
    const level = headingLevel(el, tag);
    if (level) {
      state.headingsTotal += 1;
      if (state.headings.length < limits.headings) {
        const [text, cut] = clip(textOutsideFields(el).trim(), limits.nameChars);
        if (cut) {
          state.shortenedNames += 1;
        }
        state.headings.push({ level, text });
      }
    }
  }
  if (!spec.elements) {
    return;
  }
  const interactive = isInteractive(el);
  const landmark = interactive ? "" : spec.landmarks ? landmarkRole(el) : "";
  if (!interactive && !landmark) {
    return;
  }
  state.elementsTotal += 1;
  if (state.elements.length >= limits.elements) {
    // Counted, not registered. The count is what makes the truncation line say how
    // many more there were; registering past the cap would put ids in the map that
    // no snapshot lists, and an action could then target an element the model was
    // never shown.
    return;
  }
  const id = registry.add(el);
  state.registered.push({ id, node: el });
  const [name, cut] = accessibleName(el, limits.nameChars);
  if (cut) {
    state.shortenedNames += 1;
  }
  const row: ElementRow = {
    id,
    role: landmark || roleFor(el),
    name,
    // A landmark's own text is the whole region — a `<main>` would carry the entire
    // page twice — and a field's text child IS its value, whether the field is a
    // `<textarea>` or a `contenteditable` composer. Both yield "".
    text:
      landmark !== "" || isEditableField(el)
        ? ""
        : clip(textOutsideFields(el).trim(), limits.nameChars)[0],
    visible: true,
    enabled: isEnabled(el),
  };
  const facts = fieldFacts(el);
  if (facts) {
    row.type = facts.type;
    if (facts.autocomplete) {
      row.autocomplete = facts.autocomplete;
    }
    row.sensitive = facts.sensitive;
    row.value = facts.value;
  }
  state.elements.push(row);
  if (spec.links && tag === "A" && el.hasAttribute("href")) {
    state.linksTotal += 1;
    if (state.links.length < limits.links) {
      const anchor = el as HTMLAnchorElement;
      state.links.push({
        element_id: id,
        text: name || clip(textOutsideFields(el).trim(), limits.nameChars)[0],
        // The resolved absolute URL, never the raw attribute: a model handed
        // `/refunds` cannot tell the user where it points, and a clipped URL points
        // somewhere else entirely, so `href` is never bounded by `nameChars`.
        href: anchor.href || (el.getAttribute("href") ?? ""),
      });
    }
  }
}

/** The heading level of `el`, or 0. */
function headingLevel(el: Element, tag: string): number {
  const match = /^H([1-6])$/.exec(tag);
  if (match) {
    return Number(match[1]);
  }
  if (explicitRole(el) !== "heading") {
    return 0;
  }
  const declared = Number.parseInt((el.getAttribute("aria-level") ?? "").trim(), 10);
  return Number.isInteger(declared) && declared > 0 ? declared : 2;
}

/**
 * Group the registered controls by the form they belong to.
 *
 * `fields` holds ELEMENT IDS, never values (plan section 9.4), so a form row says
 * what there is to fill in and never what is filled in. Grouping runs after the walk
 * with `closest("form")` rather than during it, because a control may be attached to
 * a form by its `form` attribute from anywhere in the document.
 */
function formRows(registered: { id: string; node: Element }[]): FormRow[] {
  const byForm = new Map<Element, string[]>();
  for (const entry of registered) {
    if (!isEditableField(entry.node)) {
      continue;
    }
    const owner = (entry.node as Element & { form?: HTMLFormElement | null }).form ?? entry.node.closest("form");
    if (!owner) {
      continue;
    }
    const fields = byForm.get(owner);
    if (fields) {
      fields.push(entry.id);
    } else {
      byForm.set(owner, [entry.id]);
    }
  }
  const rows: FormRow[] = [];
  for (const [form, fields] of byForm) {
    const named = form as Element & { action?: string };
    rows.push({
      name:
        flatten(form.getAttribute("name")).trim() ||
        flatten(form.getAttribute("aria-label")).trim() ||
        flatten(form.getAttribute("id")).trim(),
      action: named.action ?? (form.getAttribute("action") ?? ""),
      fields,
    });
  }
  return rows;
}

/** Every limit that bit, as wire rows. `limit` is the kind, as Python spells it. */
function truncationRows(capture: Capture, limits: Limits): TruncationRow[] {
  const rows: TruncationRow[] = [];
  if (capture.textTotal > capture.text.length) {
    rows.push({ limit: "text", kept: capture.text.length, total: capture.textTotal });
  }
  if (capture.elementsTotal > capture.elements.length) {
    rows.push({ limit: "elements", kept: capture.elements.length, total: capture.elementsTotal });
  }
  if (capture.headingsTotal > capture.headings.length) {
    rows.push({ limit: "headings", kept: capture.headings.length, total: capture.headingsTotal });
  }
  if (capture.linksTotal > capture.links.length) {
    rows.push({ limit: "links", kept: capture.links.length, total: capture.linksTotal });
  }
  if (capture.shortenedNames) {
    rows.push({ limit: "names", kept: capture.shortenedNames, total: capture.shortenedNames });
  }
  if (capture.depthHit) {
    // `total: 0` is the protocol's "the page could not say", and it is the truth
    // here: the walk stopped, so nobody counted what was below.
    rows.push({ limit: "ax_depth", kept: limits.axDepth, total: 0 });
  }
  if (capture.nodeBudgetHit) {
    rows.push({ limit: "ax_nodes", kept: limits.axNodes, total: 0 });
  }
  return rows;
}

/** The encoded size of one frame payload, in bytes. */
function byteLength(value: unknown): number {
  return new TextEncoder().encode(JSON.stringify(value)).length;
}

/**
 * Shrink `result` until its encoded form fits the frame, reporting every shrink.
 *
 * The ORDER of sacrifice is deliberate. Text goes first: a model can re-read in a
 * narrower mode or ask `browser_get_elements` for what it needs, and the text is the
 * only section with no handles in it. Links go next. Element rows go LAST, because an
 * element id is what an action targets and dropping rows silently removes the user's
 * ability to act on the page at all.
 *
 * If it still does not fit, the refusal names the size — which is the outcome
 * `extension_backend.py` asks for, and the opposite of the fifteen-second
 * ACTION_TIMEOUT that a frame refused unread produces.
 */
export function fitToFrame(result: SnapshotResult, budget = FRAME_BUDGET_BYTES): SnapshotResult {
  if (byteLength(result) <= budget) {
    return result;
  }
  const rows: TruncationRow[] = [...(result.truncation ?? [])];
  const note = (limit: string, kept: number, total: number): void => {
    const existing = rows.find((row) => row.limit === limit);
    if (existing) {
      existing.kept = kept;
      existing.total = Math.max(existing.total, total);
      return;
    }
    rows.push({ limit, kept, total });
  };
  const textTotal = Math.max(result.text.length, Number(result.counts?.["text_chars"] ?? 0));
  let size = byteLength(result);
  for (let attempt = 0; attempt < 24 && size > budget && result.text.length > 0; attempt += 1) {
    const over = size - budget;
    const drop = Math.max(1024, Math.min(result.text.length, Math.ceil(over * 1.2)));
    result = { ...result, text: result.text.slice(0, Math.max(0, result.text.length - drop)) };
    size = byteLength(result);
  }
  if (result.text.length < textTotal) {
    note("text", result.text.length, textTotal);
  }
  if (size > budget && result.links.length) {
    const total = Math.max(result.links.length, Number(result.counts?.["links"] ?? 0));
    result = { ...result, links: [] };
    size = byteLength(result);
    note("links", 0, total);
  }
  const elementsTotal = Math.max(result.elements.length, Number(result.counts?.["elements"] ?? 0));
  while (size > budget && result.elements.length > 1) {
    const keep = Math.max(1, Math.floor(result.elements.length / 2));
    result = { ...result, elements: result.elements.slice(0, keep) };
    size = byteLength(result);
  }
  if (result.elements.length < elementsTotal) {
    note("elements", result.elements.length, elementsTotal);
  }
  if (size > budget) {
    throw new BridgeError("EXTENSION_ERROR", {
      detail:
        `this page's snapshot is ${size} bytes, over the ${MAX_FRAME_BYTES} byte ` +
        "frame limit even after trimming; read it in summary mode, or ask the user " +
        "to narrow the page",
    });
  }
  return { ...result, truncation: rows, truncated: true };
}

/** Guard the one state a read cannot be honest about. */
function requireReadyPage(params: Record<string, unknown>): void {
  if (document.readyState === "loading") {
    // Reading now would report whatever has parsed so far as the whole page, and the
    // model would tell the user that content is missing. PAGE_NOT_READY names the
    // remedy: call browser_read_page again.
    throw new BridgeError("PAGE_NOT_READY", { tab_id: String(params["tab_id"] ?? "") });
  }
}

/**
 * `read_page`: one bounded, semantic, versioned, scrubbed snapshot.
 *
 * `security` is sent as `null` on purpose. The Q03 detector is
 * `computeruse/safety.detect_injection`, it lives in Python, and it is the only
 * scanner in the repository; the daemon runs it over the arriving text whenever the
 * payload carries no note of its own. A second detector written in TypeScript would
 * be a second answer to "is this page trying to give the model instructions", and
 * the two would disagree.
 */
export function readPage(params: Record<string, unknown>): SnapshotResult {
  requireReadyPage(params);
  const spec = modeSpec(params["mode"]);
  const limits = limitsFor(spec, params);
  const capture = walk(spec, limits);
  const truncation = truncationRows(capture, limits);
  const result: SnapshotResult = {
    snapshot_id: capture.snapshotId,
    page_version: capture.pageVersion,
    tab_id: Number(params["tab_id"] ?? 0),
    title: document.title || "",
    url: location.href,
    mode: spec.name,
    truncated: truncation.length > 0,
    text: capture.text,
    headings: capture.headings,
    elements: capture.elements,
    forms: capture.forms,
    links: capture.links,
    security: null,
    timestamp: new Date().toISOString(),
    truncation,
    // The PRE-trim totals. Without them a truncation line can only say "the page has
    // more"; with them it names the figure the model needs to decide its next call.
    counts: {
      text_chars: capture.textTotal,
      headings: capture.headingsTotal,
      elements: capture.elementsTotal,
      links: capture.linksTotal,
    },
  };
  return fitToFrame(result);
}

/**
 * `get_elements`: the same fresh snapshot, filtered to what was asked for.
 *
 * Two properties a caller depends on. The ids are the SNAPSHOT's ids, not a
 * renumbering of the matches, so an id from here resolves against the registry
 * exactly as one from `read_page` does. And the registry holds every element the
 * walk found, not only the matches, so a later action on an unmatched element is
 * still possible — filtering narrows the ANSWER, never the page.
 *
 * It takes a fresh snapshot rather than filtering the previous one, because the
 * previous one may be minutes old; a filtered stale list would hand out ids that
 * resolve to STALE_ELEMENT and read as a broken tool.
 */
export function getElements(params: Record<string, unknown>): ElementsResult {
  requireReadyPage(params);
  const spec = MODE_SPECS[MODE_INTERACTIVE] as ContentModeSpec;
  const limits = limitsFor(spec, {});
  const capture = walk(spec, limits);
  const query = String(params["query"] ?? "")
    .trim()
    .toLowerCase();
  const role = String(params["role"] ?? "")
    .trim()
    .toLowerCase();
  const matched = capture.elements.filter((row) => matches(row, query, role));
  const limit = narrow(MAX_ELEMENTS, params["limit"]);
  const kept = matched.slice(0, limit);
  return {
    snapshot_id: capture.snapshotId,
    page_version: capture.pageVersion,
    elements: kept,
    count: kept.length,
    // Truncated is true when the ANSWER is short of the matches, and also when the
    // WALK was short of the page — by the element cap, by the depth cap or by the
    // node budget. A depth- or node-capped walk never VISITED the elements it
    // stopped short of, so they are not in `elementsTotal` either and the row count
    // alone cannot notice them; without these two the daemon caches a partial roster
    // as the tab's complete registry and answers ELEMENT_NOT_FOUND for a button that
    // is on the page. Saying so is the difference between "there is no Export
    // button" and "I did not look at all of it".
    truncated:
      matched.length > kept.length ||
      capture.elementsTotal > capture.elements.length ||
      capture.depthHit ||
      capture.nodeBudgetHit,
  };
}

/** Whether one row satisfies the `query` and `role` filters. */
function matches(row: ElementRow, query: string, role: string): boolean {
  if (role && row.role.toLowerCase() !== role) {
    return false;
  }
  if (!query) {
    return true;
  }
  const haystack = `${row.name} ${row.text} ${row.role} ${row.type ?? ""}`.toLowerCase();
  return haystack.includes(query);
}
