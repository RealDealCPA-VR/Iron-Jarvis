// The element registry: `e1`..`eN` mapped to live nodes, and `page_version`.
//
// This module is where STALENESS IS DECIDED (plan section 9.3), because this is the
// only place a live node exists. The daemon's `SnapshotCache` knows which
// `snapshot_id` it handed out last and nothing else; it deliberately does not know
// whether a node is still attached, still visible or still enabled. Two answers to
// "is this element still there" would diverge within a second on any real page, and
// only the page can answer.
//
// THE IDS ARE PER-SNAPSHOT HANDLES, NEVER DURABLE IDENTIFIERS. Every capture calls
// `begin()`, which clears the map and restarts the counter at `e1`, so `e17` from
// the previous read is not the same node as `e17` from this one. That is why a
// refusal names the snapshot as well as the element: "no element e17 in snapshot
// snap_ab12cd34" is actionable, "no element e17" invites a retry with the same id.
//
// WHY `page_version` IS NOT BUMPED BY PATCHING `history.pushState`, which plan
// section 9.3 names as one of its triggers: a content script runs in an ISOLATED
// WORLD. It shares the DOM with the page but not the JavaScript globals, so patching
// `History.prototype.pushState` here intercepts calls made HERE and never the page's
// own. The patch would look correct in review, pass any test that called the patched
// function, and bump nothing on a real single-page app. So the URL is compared
// instead — at every capture, on `popstate`, on `hashchange`, and at the head of
// every mutation batch — which catches `pushState` and `replaceState` by their
// observable effect rather than by a hook that cannot fire.
//
// Bumping is deliberately EAGER: any mutation batch that adds or removes an
// interactive node moves the version, exactly as section 9.3 specifies, and a moved
// version makes every id from the previous snapshot answer STALE_ELEMENT. On a page
// with a live ticker that means re-reading before acting. The alternative — deciding
// which mutations "matter" — is a heuristic that fails silently in the direction of
// acting on the wrong element, which is the one failure this whole model exists to
// prevent.

import { SNAPSHOT_ID_PREFIX } from "../protocol";
import { BridgeError } from "../bridge/errors";
import { SKIPPED_INPUT_TYPES, fieldType, isEditableHost } from "./scrub";

/**
 * ARIA roles that make an element part of the interactive registry.
 *
 * Roles, not tags, because a modern application's buttons are `<div role="button">`
 * far more often than they are `<button>`, and a registry that missed them would
 * report a page as having nothing to press.
 */
export const INTERACTIVE_ROLES: ReadonlySet<string> = new Set([
  "button",
  "checkbox",
  "combobox",
  "gridcell",
  "link",
  "listbox",
  "menuitem",
  "menuitemcheckbox",
  "menuitemradio",
  "option",
  "radio",
  "searchbox",
  "slider",
  "spinbutton",
  "switch",
  "tab",
  "textbox",
  "treeitem",
]);

/** Landmark roles, included as registry rows in `full` mode only. */
export const LANDMARK_ROLES: ReadonlySet<string> = new Set([
  "banner",
  "complementary",
  "contentinfo",
  "form",
  "main",
  "navigation",
  "region",
  "search",
]);

/** Tag -> implicit landmark role. `SECTION` counts only when it is named (below). */
export const LANDMARK_TAGS: Readonly<Record<string, string>> = {
  ASIDE: "complementary",
  FOOTER: "contentinfo",
  FORM: "form",
  HEADER: "banner",
  MAIN: "main",
  NAV: "navigation",
  SECTION: "region",
};

/**
 * A CSS selector matching anything the registry might hold.
 *
 * Used only to ask "did this added or removed subtree contain something
 * interactive". It is deliberately WIDER than `isInteractive`: a false bump costs a
 * re-read, and a missed bump costs an action against a node that is no longer there.
 */
export const INTERACTIVE_SELECTOR =
  "a[href],button,input,select,textarea,summary,[contenteditable],[role],[tabindex]";

/** Attributes whose change can add or remove interactivity, so they are watched. */
export const WATCHED_ATTRIBUTES = [
  "disabled",
  "aria-disabled",
  "hidden",
  "aria-hidden",
  "href",
  "role",
  "type",
];

/** The explicit `role` attribute's first token, lowercased, or `""`. */
export function explicitRole(el: Element): string {
  const raw = (el.getAttribute("role") ?? "").trim().toLowerCase();
  if (!raw) {
    return "";
  }
  return raw.split(/\s+/)[0] ?? "";
}

// `isEditableHost` is imported from `scrub.ts` and re-exported here rather than
// defined here, and the direction is deliberate. Editability is what the NO-VALUE
// rule turns on — a `contenteditable` region is a field whose children are its
// contents — so the predicate belongs beside that rule, where a reader changing it
// sees what depends on it. This module asks the same question for a different
// purpose (an editable region is interactive), and two definitions of "editable"
// are exactly how the registry and the value rule drifted apart in the first place.
export { isEditableHost };

/**
 * Whether `el` belongs in the interactive registry.
 *
 * The `tabindex` rule is the one worth reading twice. A bare `[tabindex]` is a
 * standard interactivity hint and also the commonest decoration on the web — modal
 * wrappers, scroll containers, focus traps. Registering all of them would spend the
 * 250-element budget on boxes with no name, and the model would report a page as
 * full of unlabelled controls. So a tabindexed element joins only when it also
 * carries a role or a name, which is exactly the case where a user could be told
 * what it is.
 */
export function isInteractive(el: Element): boolean {
  // Uppercased rather than compared raw: an HTML element's `tagName` is already
  // uppercase, but an SVG element's is as authored, so `<a>` inside an `<svg>` would
  // silently fail every comparison below.
  const tag = el.tagName.toUpperCase();
  if (tag === "A") {
    return el.hasAttribute("href");
  }
  if (tag === "BUTTON" || tag === "SELECT" || tag === "TEXTAREA" || tag === "SUMMARY") {
    return true;
  }
  if (tag === "INPUT") {
    return !SKIPPED_INPUT_TYPES.has(fieldType(el));
  }
  if (isEditableHost(el)) {
    return true;
  }
  const role = explicitRole(el);
  if (role && INTERACTIVE_ROLES.has(role)) {
    return true;
  }
  const tabindex = (el.getAttribute("tabindex") ?? "").trim();
  if (tabindex && tabindex !== "-1") {
    return role !== "" || el.hasAttribute("aria-label") || el.hasAttribute("title");
  }
  return false;
}

/** The landmark role `el` contributes in `full` mode, or `""`. */
export function landmarkRole(el: Element): string {
  const explicit = explicitRole(el);
  if (explicit && LANDMARK_ROLES.has(explicit)) {
    return explicit;
  }
  const implied = LANDMARK_TAGS[el.tagName.toUpperCase()] ?? "";
  if (!implied) {
    return "";
  }
  if (
    el.tagName.toUpperCase() === "SECTION" &&
    !el.hasAttribute("aria-label") &&
    !el.hasAttribute("aria-labelledby")
  ) {
    // An unnamed `<section>` is not a landmark in the accessibility tree, and
    // listing every one of them would bury the real regions.
    return "";
  }
  return implied;
}

/** A fresh `snap_<8 hex>` id, minted where the snapshot is actually taken. */
export function newSnapshotId(): string {
  const bytes = new Uint8Array(4);
  crypto.getRandomValues(bytes);
  let hex = "";
  for (const byte of bytes) {
    hex += byte.toString(16).padStart(2, "0");
  }
  return `${SNAPSHOT_ID_PREFIX}${hex}`;
}

/** What a finished capture reports about itself. */
export interface CaptureHandle {
  snapshotId: string;
  pageVersion: number;
}

/**
 * One document's registry. There is exactly one, `registry`, at the foot of the file.
 *
 * Instance state survives a second `chrome.scripting.executeScript` into the same
 * tab because `content/index.ts` installs itself once per document and a repeated
 * injection returns early — see its header. If it did not, every read would reset
 * `page_version` to 1 and a genuinely changed page would present as unchanged.
 */
export class ElementRegistry {
  private readonly nodes = new Map<string, Element>();
  private snapshot = "";
  private versionAtCapture = 0;
  private version = 1;
  private seq = 0;
  private seenUrl = location.href;
  private observing = false;

  /** The current version. Bumped by navigation and by interactive mutations. */
  get pageVersion(): number {
    return this.version;
  }

  /** The id of the snapshot this registry currently holds, or `""`. */
  get snapshotId(): string {
    return this.snapshot;
  }

  /** How many nodes the current snapshot registered. */
  get size(): number {
    return this.nodes.size;
  }

  /** Whether the page moved since the current snapshot was taken. */
  get moved(): boolean {
    return this.snapshot !== "" && this.version !== this.versionAtCapture;
  }

  /** Move the version on. Every caller names its reason, for a reader of a trace. */
  bump(_reason: string): number {
    this.version += 1;
    return this.version;
  }

  /**
   * Bump when the URL changed since we last looked, and say whether it had.
   *
   * This is the `pushState`/`replaceState` trigger, caught by its effect rather than
   * by a hook the isolated world cannot install (see the file header).
   */
  noteUrl(): boolean {
    const now = location.href;
    if (now === this.seenUrl) {
      return false;
    }
    this.seenUrl = now;
    this.bump("url changed");
    return true;
  }

  /** Start a fresh snapshot: ids restart at `e1` and the previous map is dropped. */
  begin(): string {
    this.nodes.clear();
    this.seq = 0;
    this.snapshot = newSnapshotId();
    return this.snapshot;
  }

  /** Register one node and return its handle. */
  add(node: Element): string {
    this.seq += 1;
    const id = `e${this.seq}`;
    this.nodes.set(id, node);
    return id;
  }

  /** Close the snapshot, recording the version it was taken at. */
  finish(): CaptureHandle {
    this.versionAtCapture = this.version;
    return { snapshotId: this.snapshot, pageVersion: this.version };
  }

  /**
   * The live node behind `elementId`, or a refusal a model can act on.
   *
   * The ORDER of the checks is plan section 9.3's table, and it is the whole
   * contract: an unknown snapshot is STALE_SNAPSHOT ("read the page"), a known
   * snapshot on a moved page is STALE_ELEMENT ("read the page and use the NEW id"),
   * and only then is a missing id ELEMENT_NOT_FOUND ("here is what to list").
   * Checking the id first would answer ELEMENT_NOT_FOUND for a page that has simply
   * re-rendered, and the model would conclude the control does not exist.
   *
   * Ship 3's `actions.ts` is the caller. The rule lives here, in the ship that owns
   * the registry, because the rule and the state it reads are one thing: a Ship 3
   * that re-derived staleness against a map it does not own would be the second
   * answer this module exists to prevent.
   */
  resolve(elementId: string, snapshotId: string | null = null): Element {
    // The URL first, BEFORE `moved` is read. A single-page app can change route
    // with no DOM mutation at all, and until a mutation batch arrives the observer
    // has nothing to notice; the ids from the previous route would then resolve as
    // fresh and an action would land on the wrong page. Asking here costs one string
    // comparison and makes the URL trigger independent of the observer.
    this.noteUrl();
    const wanted = String(elementId ?? "");
    if (!this.snapshot) {
      throw new BridgeError("STALE_SNAPSHOT");
    }
    if (snapshotId && snapshotId !== this.snapshot) {
      throw new BridgeError("STALE_SNAPSHOT");
    }
    if (this.moved) {
      throw new BridgeError("STALE_ELEMENT");
    }
    const node = this.nodes.get(wanted);
    if (!node) {
      throw new BridgeError("ELEMENT_NOT_FOUND", {
        element_id: wanted || "(none)",
        snapshot_id: this.snapshot,
      });
    }
    if (!node.isConnected) {
      // The id is ours and the version says nothing changed, but the node left the
      // document — a mutation the observer classified as uninteresting, or a batch
      // that has not been delivered yet. STALE_ELEMENT, not ELEMENT_NOT_FOUND: the
      // page moved, and the remedy is a re-read rather than a different id.
      throw new BridgeError("STALE_ELEMENT");
    }
    return node;
  }

  /**
   * Start watching the document. Idempotent, and called once per document.
   *
   * `subtree` with `childList` is what section 9.3 asks for. The attribute filter is
   * the honest addition: `disabled` flipping on a button adds or removes an
   * interactive node just as surely as inserting one does, and a snapshot taken
   * before the flip would tell a model a control is available when it is not.
   */
  observe(): void {
    if (this.observing) {
      return;
    }
    this.observing = true;
    const observer = new MutationObserver((records) => {
      // A navigation the observer sees arrives as a batch of mutations — a route
      // change rewrites the DOM — and it has ALREADY moved the version by one here.
      // Returning is what keeps it one move: falling through would bump a second
      // time for the same event, and the reason recorded for the move would be
      // "interactive mutation" on a page that in fact navigated.
      if (this.noteUrl()) {
        return;
      }
      for (const record of records) {
        if (touchesInteractive(record)) {
          this.bump("interactive mutation");
          return;
        }
      }
    });
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: WATCHED_ATTRIBUTES,
    });
    // `popstate` and `hashchange` are DOM events on the shared window, so they DO
    // reach an isolated world — unlike a patched `pushState` (see the header).
    addEventListener("popstate", () => {
      this.noteUrl();
    });
    addEventListener("hashchange", () => {
      this.noteUrl();
    });
  }
}

/** Whether one mutation record added or removed something interactive. */
export function touchesInteractive(record: MutationRecord): boolean {
  if (record.type === "attributes") {
    // The observer only delivers the attributes in `WATCHED_ATTRIBUTES`, and every
    // one of them can turn a control on or off, so the arrival of the record is the
    // evidence. Re-testing `isInteractive` here would MISS the important direction:
    // an element that just stopped being interactive no longer matches.
    return record.target instanceof Element;
  }
  for (const list of [record.addedNodes, record.removedNodes]) {
    for (const node of Array.from(list)) {
      if (!(node instanceof Element)) {
        continue;
      }
      if (isInteractive(node) || node.querySelector(INTERACTIVE_SELECTOR) !== null) {
        return true;
      }
    }
  }
  return false;
}

/** The one registry for this document. */
export const registry = new ElementRegistry();
