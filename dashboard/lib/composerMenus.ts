/**
 * v1.329.0 (calm chat W5 G1): when the composer's "@" and "/" menus are open.
 *
 * Both menus open from what is typed (the token at the caret), so they have no
 * open flag of their own: the pickers work it out from the composer store each
 * render. The "Jump to latest" pill needs the same answer, so it hides while
 * either menu is open (on a phone it sat on the "@" menu's Chats rows). One
 * definition here keeps the pill and the pickers agreeing.
 *
 * The "/" menu wins when both tokens could open: the "@" picker yields to it.
 */

import { slashTokenAt, tokenAt } from "@/lib/slash";

/** The slice of the composer store the two token menus read. */
export interface TokenMenuState {
  text: string;
  caret: number;
  slashDismissed: boolean;
  atDismissed: boolean;
}

/** The "/" menu is open: a "/" token at the caret, not waved away, no turn running. */
export function slashMenuOpen(s: TokenMenuState, busy: boolean): boolean {
  return !(busy || s.slashDismissed) && slashTokenAt(s.text, s.caret) !== null;
}

/** The "@" menu is open: an "@" token at the caret, not waved away, no turn
 *  running, and the "/" menu is not open (the "@" menu yields to it). */
export function atMenuOpen(s: TokenMenuState, busy: boolean): boolean {
  if (busy || s.atDismissed) return false;
  if (tokenAt(s.text, s.caret, "@") === null) return false;
  return !slashMenuOpen(s, busy);
}

/** Either typed menu is open. */
export function tokenMenuOpen(s: TokenMenuState, busy: boolean): boolean {
  return slashMenuOpen(s, busy) || atMenuOpen(s, busy);
}

/** The composer's other menus, each open or not (the page's own state). */
export interface ComposerMenuFlags {
  /** The "+" menu. */
  plus: boolean;
  /** The tools chip's menu. */
  tools: boolean;
  /** The model menu. */
  model: boolean;
  /** The project chip's menu. */
  project: boolean;
  /** The permission chip's menu. */
  permission: boolean;
  /** An app prompt's little form, which opens above the box like a menu. */
  promptForm: boolean;
}

/** Any of the page's own composer menus is open. */
export function anyComposerMenuOpen(f: ComposerMenuFlags): boolean {
  return f.plus || f.tools || f.model || f.project || f.permission || f.promptForm;
}
