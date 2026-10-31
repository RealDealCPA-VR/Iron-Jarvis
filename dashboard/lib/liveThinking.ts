import { useCallback, useSyncExternalStore } from "react";

/**
 * The live REASONING text of a chat stream (v1.323.0) — the twin of
 * `useLiveText`: same ref, same once-a-frame publish, read only by the
 * component that shows it (the page must never subscribe). Falls back to
 * `stream.thinking`, then "", for a stream that has no store — a test double
 * built before this existed carries neither.
 *
 * Its OWN module on purpose: ~50 chat tests mock `@/lib/useChatStream` with a
 * fixed export list, and a page import of a name those mocks never defined
 * would crash every one of them (the v1.250.0 `useLiveText` lesson).
 * `useChatStream` re-exports it for callers that already import from there.
 */
export function useLiveThinking(stream: {
  thinking?: string;
  thinkingStore?: { get(): string; subscribe(cb: () => void): () => void };
}): string {
  const store = stream.thinkingStore;
  const subscribe = useCallback(
    (cb: () => void) => (store ? store.subscribe(cb) : () => {}),
    [store],
  );
  const live = useSyncExternalStore(
    subscribe,
    () => (store ? store.get() : ""),
    () => "",
  );
  return store ? live : (stream.thinking ?? "");
}

/** v1.417.0 (C2.4): the finish check's round of the running turn
 *  (`stream.finishStore`), subscribed at the component that SHOWS it — the
 *  caller never re-renders for it. Null for a stream with no store (a test
 *  double built before it existed) or a turn with no finish check. Its own
 *  module for the same reason as `useLiveThinking`; the shape is
 *  `LiveFinish` in lib/useChatStream (a type, so importing it costs the
 *  mocks nothing). */
export function useLiveFinish<T>(stream: {
  finishStore?: { get(): T | null; subscribe(cb: () => void): () => void };
}): T | null {
  const store = stream.finishStore;
  const subscribe = useCallback(
    (cb: () => void) => (store ? store.subscribe(cb) : () => {}),
    [store],
  );
  return useSyncExternalStore(
    subscribe,
    () => (store ? store.get() : null),
    () => null,
  );
}
