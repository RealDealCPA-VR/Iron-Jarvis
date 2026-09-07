// Scrubbing, at capture time, inside the page (D13B and plan section 9.4).
//
// THE RULE, and it is stricter than D13B asks for: NO FIELD VALUE IS EVER
// COLLECTED, FOR ANY FIELD. Not a password, not an email address, not a search
// box, not a hidden field, not a textarea's contents, not a select's chosen
// option, AND NOT A `contenteditable` REGION'S CONTENTS. A snapshot describes what
// is on the page to fill in; it never describes what is filled in.
//
// "FIELD" IS DECIDED THE WAY THE REGISTRY DECIDES IT, which is why `isEditableHost`
// lives in this file rather than in `elements.ts`. A tag-based rule (INPUT,
// TEXTAREA, SELECT) and a role-based registry disagree about exactly one thing, and
// it is the thing that matters: `<div contenteditable role="textbox">` is reported
// to the model as a textbox, so a rich-text editor, a chat composer or an in-page
// note field IS a field the model can see — and under a tag rule its contents were
// collected into the row's `text`, into its `name` and into the page `text`, with
// no `sensitive` flag and no `value: null`. `isEditableField` is the one predicate
// every rule below asks, so the promise and the registry cannot drift apart again.
//
// The one editable surface this cannot see is `document.designMode = "on"`, which
// makes a document editable with no attribute anywhere. Nothing in the DOM
// distinguishes such a page from an ordinary one element by element; a page-level
// check would blank the whole snapshot, so it is not made, and this sentence is the
// disclosure.
//
// Why that is the rule rather than "scrub the sensitive ones": a scrub list is a
// list of the leaks somebody thought of. `autocomplete` is author-controlled and
// frequently absent or wrong, a bank's account field is often `type="text"`, and a
// tax portal's SSN box is often `type="tel"`. Collecting no value at all removes
// the whole class, and the cost is one round trip: a model that needs to know what
// a field currently holds asks the user, or reads the page text around it.
//
// Where the leak would otherwise land: the snapshot crosses the socket, becomes a
// tool result, and is written to `ToolInvocation.output` in SQLite — which is on
// disk, in the user's backups, and read back into later prompts. A password
// captured once is a password stored indefinitely. Scrubbing here, in the page,
// means the plaintext never leaves the tab at all.
//
// THE ONE `value` READ IN THIS ADD-ON is `buttonLabel` below, and it reads the
// `value` ATTRIBUTE of `input[type=submit|button|reset]` only. Those three types
// hold a BUTTON'S LABEL, not user data: they are not editable, they are never
// autofilled, and a password manager cannot write to them. Without that read, the
// commonest submit button on the web (`<input type="submit" value="Sign in">`)
// reaches the model with no name at all, and the model cannot tell the user what it
// is about to press. Nothing in the add-on reads the `.value` PROPERTY of any
// element, which is what `tests/test_browser_content_script_v1236.py` pins.

import { PASSWORD_AUTOCOMPLETE, PAYMENT_AUTOCOMPLETE, SENSITIVE_AUTOCOMPLETE } from "../protocol";

/**
 * The autocomplete tokens that make a field sensitive, as a set for lookup.
 *
 * Generated from `computeruse/policy.py` through `protocol.ts`, so the browser and
 * the desktop halves of Iron Jarvis cannot disagree about what counts as a
 * credential. `PASSWORD_AUTOCOMPLETE` and `PAYMENT_AUTOCOMPLETE` are named in this
 * import only to make the two halves of that vocabulary visible to a reader here;
 * `SENSITIVE_AUTOCOMPLETE` is their union and is what the check uses.
 */
const SENSITIVE_TOKENS: ReadonlySet<string> = new Set<string>([
  ...PASSWORD_AUTOCOMPLETE,
  ...PAYMENT_AUTOCOMPLETE,
  ...SENSITIVE_AUTOCOMPLETE,
]);

/** The tags that are form CONTROLS, whose contents are never collected. */
export const FIELD_TAGS: ReadonlySet<string> = new Set(["INPUT", "TEXTAREA", "SELECT"]);

/**
 * The reported `type` of an editable region that is not a form control.
 *
 * A word rather than `"text"`, because the row it lands on says what the model may
 * do with the element, and "this is a rich-text region whose contents were not
 * collected" is a different fact from "this is a text input".
 */
export const EDITABLE_HOST_TYPE = "contenteditable";

/**
 * The `input` types whose `value` attribute is a button label rather than data.
 *
 * Exactly three, and the list may not grow without re-reading this file's header.
 */
export const LABEL_ATTRIBUTE_TYPES: ReadonlySet<string> = new Set(["submit", "button", "reset"]);

/**
 * Input types that never appear in the element registry at all.
 *
 * `hidden` is not interactive, cannot be described to a user, and is the single
 * likeliest place for a server to park a token — so it is skipped rather than
 * listed with a null value.
 */
export const SKIPPED_INPUT_TYPES: ReadonlySet<string> = new Set(["hidden"]);

/** What a snapshot says about a form field. `value` is `null`, always. */
export interface FieldFacts {
  type: string;
  autocomplete: string;
  sensitive: boolean;
  /** Typed `null`, and assigned `null` — D13B's example row, verbatim. */
  value: null;
}

/** Whether `el` is a form control. */
export function isField(el: Element): boolean {
  return FIELD_TAGS.has(el.tagName);
}

/**
 * Whether `el` is editable through `contenteditable`.
 *
 * The ATTRIBUTE is read rather than `isContentEditable`, which is a computed
 * property that answers `false` in a detached tree and does not exist on a
 * non-HTML element. `contenteditable=""` and `contenteditable="plaintext-only"`
 * are both editable; only the explicit `"false"` is not.
 */
export function isEditableHost(el: Element): boolean {
  const raw = el.getAttribute("contenteditable");
  if (raw === null) {
    return false;
  }
  return raw.trim().toLowerCase() !== "false";
}

/**
 * Whether `el` is a FIELD — the predicate the whole value rule is written on.
 *
 * A form control OR an editable region. Every rule in this file, and every rule in
 * `snapshot.ts` that suppresses a field's contents, asks this and nothing narrower:
 * see the file header for what a tag-only answer let through.
 *
 * A descendant of an editable region needs no test of its own, because the walk
 * STOPS at the host (`stopsTheWalk`), so nothing below one is ever visited.
 */
export function isEditableField(el: Element): boolean {
  return isField(el) || isEditableHost(el);
}

/**
 * The field's type, lowercased; the tag for a textarea and a select, and
 * `contenteditable` for an editable region that is not a form control.
 *
 * Read from the ATTRIBUTE rather than from `el.type`, and what that buys is exactly
 * one thing: the snapshot reports the type THE PAGE AUTHOR DECLARED. A field
 * written `type="passwrod"` is reported as `type: "passwrod"` rather than as the
 * `"text"` the property would normalise it to, so a reader of the snapshot sees the
 * typo instead of an ordinary-looking text box.
 *
 * It buys no SENSITIVITY, and saying so is the point: Chrome renders
 * `type="passwrod"` as a plain text box, so it is not a password field and
 * `isSensitiveField` does not call it one. Two known holes in that flag, named here
 * because `sensitive` is what a later ship's typing redaction will key on — a
 * misspelled type is not sensitive, and a "show password" toggle that sets
 * `el.type = "text"` reflects to the attribute and clears the flag unless an
 * `autocomplete` token holds it. The VALUE is `null` either way: sensitivity
 * changes how a row is described, never whether its contents were collected.
 */
export function fieldType(el: Element): string {
  if (el.tagName === "TEXTAREA") {
    return "textarea";
  }
  if (el.tagName === "SELECT") {
    return "select";
  }
  if (el.tagName !== "INPUT" && isEditableHost(el)) {
    return EDITABLE_HOST_TYPE;
  }
  const raw = (el.getAttribute("type") ?? "").trim().toLowerCase();
  return raw || "text";
}

/** The field's `autocomplete` tokens, lowercased. Empty when it declares none. */
export function autocompleteTokens(el: Element): string[] {
  const raw = (el.getAttribute("autocomplete") ?? "").trim().toLowerCase();
  if (!raw || raw === "off" || raw === "on") {
    return [];
  }
  return raw.split(/\s+/).filter((token) => token.length > 0);
}

/**
 * Whether this field holds a credential or a payment detail.
 *
 * Two rules, both from section 9.4: the type is `password`, or one of the
 * `autocomplete` TOKENS is in the vocabulary. Tokens rather than the whole string
 * because the spec allows section and shipping prefixes — `autocomplete="section-a
 * cc-number"` is a card number, and a whole-string comparison would call it
 * ordinary.
 */
export function isSensitiveField(el: Element): boolean {
  if (!isEditableField(el)) {
    return false;
  }
  if (fieldType(el) === "password") {
    return true;
  }
  return autocompleteTokens(el).some((token) => SENSITIVE_TOKENS.has(token));
}

/**
 * The field description that goes into an element row, or `null` for a non-field.
 *
 * `value: null` is written explicitly rather than omitted, because the daemon
 * admits the key only as `null` (`snapshot._element_row`) and an explicit null is
 * the difference between "this field's contents were not collected" and "nobody
 * thought about this field".
 */
export function fieldFacts(el: Element): FieldFacts | null {
  if (!isEditableField(el)) {
    return null;
  }
  return {
    type: fieldType(el),
    autocomplete: autocompleteTokens(el).join(" "),
    sensitive: isSensitiveField(el),
    value: null,
  };
}

/**
 * A button's label from its `value` attribute — the one read described in the
 * header, and only for the three non-data input types.
 */
export function buttonLabel(el: Element): string {
  if (el.tagName !== "INPUT" || !LABEL_ATTRIBUTE_TYPES.has(fieldType(el))) {
    return "";
  }
  return (el.getAttribute("value") ?? "").trim();
}

/**
 * Whether the walk must stop at `el` instead of descending into it.
 *
 * True for every FIELD, and that is the second half of the value rule: a
 * `<textarea>`'s text child IS its value, a `<select>`'s options are its possible
 * values, and a `contenteditable` region's child nodes are what the user has typed
 * into it. Collecting page text through any of them would put a server-rendered
 * draft — or a prefilled account number, or a half-written message — into `text`,
 * past every check above, because at that point it is no longer a field's value but
 * "the page's words".
 *
 * The cost is stated plainly rather than discovered: on a page whose whole body is
 * one editable region — a document editor, a compose window — the page text comes
 * back empty. That is the side of the trade this repository takes, because an
 * unread field costs a round trip and a collected one is permanent: the snapshot is
 * written to `ToolInvocation.output` in SQLite.
 */
export function stopsTheWalk(el: Element): boolean {
  return isEditableField(el);
}
