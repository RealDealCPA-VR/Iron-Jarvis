"use client";

import { useSyncExternalStore } from "react";

/**
 * THE COMPOSER'S OWN STATE, kept out of the chat page's render (v1.250.0, S-05).
 *
 * Typing one character used to re-run the whole 7,700-line ChatPage body — and
 * more than once, because a keystroke moves the caret in events `onChange`
 * never sees (keyup, click, select, focus), so each one set state of its own.
 * Measured on a 50-message thread: 4.1 page renders per keystroke.
 *
 * The five values below are read by the textarea, the two pickers and the send
 * arrow, and by nothing else on the page. Holding them in an external store
 * lets exactly those subtrees subscribe (`useComposer`) while the page reads
 * the text imperatively (`get().text`) on send — the same shape that took the
 * streamed reply out of the page's render in S-03.
 *
 * Every mutation makes a NEW state object and notifies only when a value
 * really changed, because `useSyncExternalStore` re-renders on identity: a
 * setter that always allocated would put the per-keystroke render straight
 * back, one layer down.
 */
export interface ComposerState {
  /** What the user has typed (the textarea's value). */
  text: string;
  /** Caret offset in that text — the "/" and "@" pickers key off the token AT
   *  the caret, so mid-sentence pickers need it (v1.105.0). */
  caret: number;
  /** Esc closed the "/" dropdown; any edit reopens it. */
  slashDismissed: boolean;
  /** Esc closed the "@" dropdown. */
  atDismissed: boolean;
  /** Highlighted row in the "/" dropdown (↑↓ + Enter). */
  skillIndex: number;
}

const EMPTY: ComposerState = {
  text: "",
  caret: 0,
  slashDismissed: false,
  atDismissed: false,
  skillIndex: 0,
};

export interface ComposerStore {
  /** The state as it stands. Stable by identity until something changes, so a
   *  subscriber that re-reads without a notification does not re-render. */
  get(): ComposerState;
  subscribe(cb: () => void): () => void;
  /** A keystroke: text and caret land together, and editing reopens the "/"
   *  dropdown — three writes that used to be three separate page renders. */
  type(text: string, caret: number): void;
  /** Text set for the user (prefill, dictation, retry, `?ask=`, a picked
   *  skill). The caret defaults to the end, which is where a person's would
   *  be after text appears. */
  setText(text: string, caret?: number): void;
  setCaret(caret: number): void;
  setSlashDismissed(v: boolean): void;
  setAtDismissed(v: boolean): void;
  /** Functional form included: the ↑↓ handlers move relative to the current
   *  row and must not read a render's stale copy. */
  setSkillIndex(next: number | ((prev: number) => number)): void;
  /** Back to empty — a sent message, or New Chat. */
  reset(): void;
}

export function createComposerStore(): ComposerStore {
  let state: ComposerState = EMPTY;
  const listeners = new Set<() => void>();

  const commit = (next: ComposerState): void => {
    if (
      next.text === state.text &&
      next.caret === state.caret &&
      next.slashDismissed === state.slashDismissed &&
      next.atDismissed === state.atDismissed &&
      next.skillIndex === state.skillIndex
    )
      return; // nothing moved: no new object, no notification, no render
    state = next;
    for (const cb of [...listeners]) cb();
  };

  return {
    get: () => state,
    subscribe: (cb) => {
      listeners.add(cb);
      return () => {
        listeners.delete(cb);
      };
    },
    type: (text, caret) =>
      commit({ ...state, text, caret, slashDismissed: false }),
    setText: (text, caret) =>
      commit({ ...state, text, caret: caret ?? text.length }),
    setCaret: (caret) => commit({ ...state, caret }),
    setSlashDismissed: (v) => commit({ ...state, slashDismissed: v }),
    setAtDismissed: (v) => commit({ ...state, atDismissed: v }),
    setSkillIndex: (next) =>
      commit({
        ...state,
        skillIndex: typeof next === "function" ? next(state.skillIndex) : next,
      }),
    reset: () => commit(EMPTY),
  };
}

/** Subscribe a component to the composer state. Only the textarea, the two
 *  pickers, the send arrow and the voice auto-send watcher call this — the page
 *  itself deliberately does not, which is the whole point. */
export function useComposer(store: ComposerStore): ComposerState {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}
