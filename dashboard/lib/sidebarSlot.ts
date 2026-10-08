import { useSyncExternalStore } from "react";

/**
 * Where Chat's own thread list goes (calm UI redesign S7, AUDIT Q5).
 *
 * The persistent sidebar holds the app's conversation list. On the chat
 * surface that list IS the chat page's thread rail — the same component, the
 * same state, the same rename / pin / move / delete — rendered into the
 * sidebar through a portal (layout only; behaviour unchanged). The sidebar
 * publishes its slot element here only while it is on screen (md and up); the
 * chat page reads it and, with no slot (a phone, a test, a pop-out without the
 * rail), keeps its rail where it always was.
 */

let slot: HTMLElement | null = null;
const subs = new Set<() => void>();

export function setChatSlot(node: HTMLElement | null): void {
  if (slot === node) return;
  slot = node;
  for (const f of subs) f();
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
