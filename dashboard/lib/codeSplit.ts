/**
 * TWO CODING SESSIONS SIDE BY SIDE (v1.406.0 part 7; the user: "two sessions
 * side by side", like Claude Code desktop's split view).
 *
 * The Code view (components/harness/IjHarnessView) shows ONE session, or two
 * in columns: `left` (the session the address calls `session`) and `right`
 * (the one opened BESIDE it, `beside`). One of them has the FOCUS: the
 * right-hand panes (Plan, Timeline, Changes, Terminal, Browser, Files,
 * Session) are that column's, and the keys (Esc stops, Enter answers a card,
 * Ctrl+L, Ctrl+K) are that column's — never the other's.
 *
 * THE RULES, pure and pinned here:
 *  - at most TWO columns; opening a session that is already one of the two
 *    FOCUSES it; opening another replaces the focused column's session;
 *  - "Open beside" puts a session in the right column (replacing what was
 *    there) and focuses it; beside itself = nothing changes but the focus;
 *  - closing a column returns to one: the other session stays;
 *  - the split rides the address (`/chat?view=code&session=<a>&beside=<b>`)
 *    and this WINDOW's memory (module state + sessionStorage — a new window
 *    starts on one column).
 *
 * KEY OWNERSHIP: a column is an element carrying `data-split-column`; the one
 * with focus also carries `data-focused="true"`. A key belongs to the column
 * its target is in, else (nothing focused — the body) to the focused column.
 * A handler inside a column answers a key only when it owns it
 * (`ownsKeys`); an element in no column (one session, no split) owns every
 * key exactly as before. Import-free: DockAsk (the chat) reads it too.
 */

export type SplitSide = "left" | "right";

export interface CodeSplit {
  /** The session in the first (or only) column. */
  left: string | null;
  /** The session opened beside it, or null (one column). */
  right: string | null;
  /** Which column has the focus (always "left" with one column). */
  focus: SplitSide;
}

/** The address parameter of the session in the second column. */
export const BESIDE_PARAM = "beside";
/** sessionStorage: this window's split (`{left, right, focus}`). */
export const SPLIT_KEY = "ij.code.split";
/** Two columns need at least this window width (the harness's own `xl`, where
 *  a session's conversation and its panes sit side by side); below it a split
 *  shows ONE column with a tab per session. */
export const SPLIT_MIN_WIDTH = 1280;
export const SPLIT_QUERY = `(min-width: ${SPLIT_MIN_WIDTH}px)`;
/** The attribute every column carries; its value is the side. */
export const SPLIT_COLUMN_ATTR = "data-split-column";
/** A row dragged out of the session list carries its id in this type. */
export const SESSION_DRAG_TYPE = "application/x-ij-session";

export const ONE: CodeSplit = { left: null, right: null, focus: "left" };

function clean(s: CodeSplit): CodeSplit {
  // A column never shows the same session twice, and one column has no focus
  // but its own.
  if (!s.left && s.right) return { left: s.right, right: null, focus: "left" };
  if (s.right && s.right === s.left) return { left: s.left, right: null, focus: "left" };
  if (!s.right) return { left: s.left, right: null, focus: "left" };
  return s;
}

/** The session in the focused column. */
export function focusedSession(s: CodeSplit): string | null {
  return s.focus === "right" && s.right ? s.right : s.left;
}

/** Which column shows `id`, else null. */
export function sideOf(s: CodeSplit, id: string | null | undefined): SplitSide | null {
  if (!id) return null;
  if (s.left === id) return "left";
  if (s.right === id) return "right";
  return null;
}

/** OPEN `id` (the list, a link, a branch): one of the two is focused; any
 *  other session replaces the focused column's. */
export function openSession(s: CodeSplit, id: string): CodeSplit {
  const side = sideOf(s, id);
  if (side) return clean({ ...s, focus: side });
  if (s.right && s.focus === "right") return clean({ ...s, right: id });
  return clean({ ...s, left: id });
}

/** OPEN `id` BESIDE the session in front: the right column, focused. With
 *  nothing open yet it is simply opened. */
export function openBeside(s: CodeSplit, id: string): CodeSplit {
  const side = sideOf(s, id);
  if (side) return clean({ ...s, focus: side });
  if (!s.left) return clean({ left: id, right: null, focus: "left" });
  return clean({ left: s.left, right: id, focus: "right" });
}

/** CLOSE a column: back to one column, the other session kept. */
export function closeSide(s: CodeSplit, side: SplitSide): CodeSplit {
  if (!s.right) return s;
  return side === "left" ? { left: s.right, right: null, focus: "left" } : { left: s.left, right: null, focus: "left" };
}

/** A session went away (deleted, archived from its column): its column goes. */
export function dropSession(s: CodeSplit, id: string): CodeSplit {
  const side = sideOf(s, id);
  if (!side) return s;
  if (!s.right) return { left: null, right: null, focus: "left" };
  return closeSide(s, side);
}

export function focusSide(s: CodeSplit, side: SplitSide): CodeSplit {
  if (side === "right" && !s.right) return s;
  return s.focus === side ? s : { ...s, focus: side };
}

export function sameSplit(a: CodeSplit, b: CodeSplit): boolean {
  return a.left === b.left && a.right === b.right && a.focus === b.focus;
}

/** The split an address names (`session` + `beside`), else null. */
export function splitFromSearch(search: string | null | undefined): CodeSplit | null {
  let q: URLSearchParams;
  try {
    q = new URLSearchParams(search ?? "");
  } catch {
    return null;
  }
  const left = (q.get("session") ?? "").trim() || null;
  if (!left) return null;
  const right = (q.get(BESIDE_PARAM) ?? "").trim() || null;
  return clean({ left, right, focus: "left" });
}

/** Put the split in the address bar without a navigation (`session`, and
 *  `beside` only while there are two). */
export function writeSplitToAddress(s: CodeSplit): void {
  try {
    const url = new URL(window.location.href);
    if (s.left) url.searchParams.set("session", s.left);
    else url.searchParams.delete("session");
    if (s.right) url.searchParams.set(BESIDE_PARAM, s.right);
    else url.searchParams.delete(BESIDE_PARAM);
    url.searchParams.delete("focus");
    if (s.left) url.searchParams.delete("night");
    window.history.replaceState(window.history.state, "", url.toString());
  } catch {
    /* no history API — the window's memory still holds it */
  }
}

// ── this window's memory ────────────────────────────────────────────────────

let kept: CodeSplit | null = null;

/** This window's split: module state (a return within the app), else
 *  sessionStorage (a reload), else null. */
export function rememberedSplit(): CodeSplit | null {
  if (kept) return kept;
  try {
    const raw = window.sessionStorage.getItem(SPLIT_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as Partial<CodeSplit>;
    const left = typeof v.left === "string" && v.left ? v.left : null;
    const right = typeof v.right === "string" && v.right ? v.right : null;
    if (!left) return null;
    return clean({ left, right, focus: v.focus === "right" ? "right" : "left" });
  } catch {
    return null;
  }
}

/** Remember a SPLIT for this window. One column is not kept here: the session
 *  this window had open is remembered as it always was (LAST_SESSION_KEY). */
export function keepSplit(s: CodeSplit): void {
  kept = s.right ? s : null;
  try {
    if (s.right) window.sessionStorage.setItem(SPLIT_KEY, JSON.stringify(s));
    else window.sessionStorage.removeItem(SPLIT_KEY);
  } catch {
    /* the module copy still holds it */
  }
}

/** Tests only. */
export function __resetSplitMemory(): void {
  kept = null;
}

// ── key ownership ───────────────────────────────────────────────────────────

/** The column `el` sits in, else null. */
export function columnOf(el: Element | null | undefined): Element | null {
  return el && typeof el.closest === "function" ? el.closest(`[${SPLIT_COLUMN_ATTR}]`) : null;
}

/** The column a key press belongs to: its target's, else the focused one. */
export function keyColumn(target: EventTarget | null | undefined): Element | null {
  if (typeof document === "undefined") return null;
  if (target instanceof Element && target !== document.body && target !== document.documentElement) {
    const own = columnOf(target);
    if (own) return own;
  }
  return document.querySelector(`[${SPLIT_COLUMN_ATTR}][data-focused="true"]`);
}

/** Does the handler living in `el` own this key press? Not in a column (no
 *  split): always. In a hidden column (`inert`): never. Else only when the
 *  press belongs to its own column. */
export function ownsKeys(el: Element | null | undefined, target: EventTarget | null | undefined): boolean {
  const mine = columnOf(el);
  if (!mine) return true;
  if (mine.closest("[inert]")) return false;
  return keyColumn(target) === mine;
}
