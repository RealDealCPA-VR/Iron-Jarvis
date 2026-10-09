import { useSyncExternalStore } from "react";

/**
 * Where Chat's own thread list goes (calm UI redesign S7, AUDIT Q5).
 *
 * The persistent sidebar holds the app's conversation list. On the chat
 * surface that list IS the chat page's thread rail — the same component, the
 * same state, the same rename / pin / move / archive / delete — rendered into
 * the sidebar through a portal (layout only; behaviour unchanged). A sidebar
 * publishes its slot element here only while it is on screen: the persistent
 * rail from md up, and (v1.329.0, calm chat W4 F2) the phone nav drawer while
 * it is open. The chat page reads it; with no slot (a pop-out, a collapsed
 * sidebar, a test) it keeps its rail in the page, from md up only.
 *
 * A slot can carry a `close` callback: the phone drawer's. The chat page calls
 * `closeChatSlot()` after a row opens a chat, so the drawer gets out of the
 * way (opening a chat changes only `?thread=`, which never closes it).
 */

let slot: HTMLElement | null = null;
let closer: (() => void) | null = null;
const subs = new Set<() => void>();

function emit(): void {
  for (const f of subs) f();
}

/** Publish `node` as THE slot (null = no slot). Kept for callers that own
 *  the only slot (tests); a sidebar uses claim/release. */
export function setChatSlot(node: HTMLElement | null, close?: () => void): void {
  closer = node ? close ?? null : null;
  if (slot === node) return;
  slot = node;
  emit();
}

/** Make `node` the slot, with an optional `close` (the drawer's). */
export function claimChatSlot(node: HTMLElement, close?: () => void): void {
  setChatSlot(node, close);
}

/** Give the slot up, but only if `node` still holds it: the hidden rail on a
 *  phone must never clear the drawer's slot. */
export function releaseChatSlot(node: HTMLElement | null): void {
  if (node && slot === node) setChatSlot(null);
}

/** Close whatever holds the slot, when it can be closed (the phone drawer).
 *  The persistent rail has no close; nothing happens then. */
export function closeChatSlot(): void {
  const f = closer;
  if (f) f();
}

function subscribe(f: () => void): () => void {
  subs.add(f);
  return () => {
    subs.delete(f);
  };
}

export function useChatSlot(): HTMLElement | null {
  return useSyncExternalStore(
    subscribe,
    () => slot,
    () => null,
  );
}

/** The sidebar's "New chat" on the chat surface starts a fresh conversation
 *  in place (the page owns that state); elsewhere it navigates. */
export const NEW_CHAT_EVENT = "ij:new-chat";
