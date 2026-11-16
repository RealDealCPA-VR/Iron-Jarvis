/**
 * v1.455.0 (readiness Wave F, item 18): the Browser settings queue card
 * words its run and its target.
 *
 * The card said "run cu_9f3a…" (a raw id) and showed the action's target only
 * as `role=button  "Pay now"` in a code box. Now it says which TASK the step
 * belongs to (the run's own words, from the Recent runs list) and what the
 * step acts on ("the “Pay now” button"); the run id stays in the title for
 * whoever is troubleshooting.
 *
 * No imports: pure strings, read by the Browser page and its tests.
 */

const ROLE_NOUNS: Record<string, string> = {
  button: "button",
  link: "link",
  textbox: "box",
  searchbox: "search box",
  checkbox: "checkbox",
  radio: "option",
  combobox: "list",
  menuitem: "menu item",
  tab: "tab",
  option: "option",
  switch: "switch",
};

const cut = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

/** What the step acts on, in words: "the “Pay now” button", "the text
 *  “Delete account”", "the page element #submit", else "" (nothing named). */
export function targetWords(actionJson: string): string {
  let a: Record<string, unknown>;
  try {
    a = JSON.parse(actionJson) as Record<string, unknown>;
  } catch {
    return "";
  }
  if (!a || typeof a !== "object") return "";
  const sel = (a.selector && typeof a.selector === "object" ? a.selector : {}) as Record<string, unknown>;
  const name = typeof sel.name === "string" ? sel.name.trim() : "";
  const role = typeof sel.role === "string" ? sel.role.trim().toLowerCase() : "";
  const text = typeof sel.text === "string" ? sel.text.trim() : "";
  // A selector given as a bare string is a CSS selector.
  const css =
    typeof sel.css === "string" ? sel.css.trim() : typeof a.selector === "string" ? a.selector.trim() : "";
  const url = typeof a.url === "string" ? a.url.trim() : "";
  if (name) return `the “${cut(name, 60)}” ${ROLE_NOUNS[role] ?? "item"}`;
  if (text) return `the text “${cut(text, 60)}”`;
  if (css) return `the page element ${cut(css, 60)}`;
  if (url) return cut(url.replace(/[?#].*$/, ""), 80);
  return "";
}

/** v1.516.0: what the daemon stores in place of typed text
 *  (`computeruse/approvals.TYPED_MARKER`, followed by a short digest). */
export const TYPED_MARKER = "***REDACTED***";

/** v1.516.0: the words a person reads for that stand-in. */
export const HIDDEN_TYPED_WORDS = "typed text: hidden";

/** An action's typed value as a card or a run's trace shows it: the stand-in
 *  (marker plus digest) reads as {@link HIDDEN_TYPED_WORDS}, never as
 *  `***REDACTED*** (sha256:…)`; any other value is quoted as before; none is "". */
export function typedValueWords(value: unknown): string {
  if (value === undefined || value === null || value === "") return "";
  const text = String(value);
  if (text.startsWith(TYPED_MARKER)) return HIDDEN_TYPED_WORDS;
  return `value="${text}"`;
}

/** Which task the step belongs to: "For “Pay the March invoice”", else
 *  "For a browser task" (the run is not in the recent list). */
export function queueRunWords(task: string | null | undefined): string {
  const t = (task ?? "").trim();
  return t ? `For “${cut(t, 80)}”` : "For a browser task";
}
