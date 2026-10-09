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
