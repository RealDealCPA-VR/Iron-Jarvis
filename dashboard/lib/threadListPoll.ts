"use client";

// The chat list's live re-read (v1.327.0, calm chat W2-1).
//
// `GET /chat/threads` rows carry `running` and `waiting` from the daemon's
// live turn registry, but nothing PUSHES a change in them: a chat that stops
// running, or one whose question is answered elsewhere, only shows it on the
// next list. So while ANY row is running or waiting the page re-reads the list
// every THREAD_POLL_MS — and only then. The timer is torn down when no row is
// live, while the window is hidden (one catch-up read when it shows again),
// and on unmount: `useVisibleInterval` owns all three.
//
// Its own module because tests mock `@/lib/useChatStream` with a fixed export
// list; a new hook there would be missing from every one of those mocks.

import { useVisibleInterval } from "./useVisibleInterval";
import type { ThreadSummary } from "./threadStatus";

/** How often the chat list is re-read while a chat is running or waiting. */
export const THREAD_POLL_MS = 4000;

/** True when the daemon says some row is running or waiting on the user. */
export function hasLiveThreads(threads: readonly ThreadSummary[]): boolean {
  return threads.some((t) => t.running === true || t.waiting === true);
}

/** Re-read the list (`refresh`) every `ms` while a row is live and the window
 *  is visible. Returns whether it is polling. */
export function useThreadListPoll(
  threads: readonly ThreadSummary[],
  refresh: () => void,
  ms: number = THREAD_POLL_MS,
): boolean {
  const live = hasLiveThreads(threads);
  useVisibleInterval(refresh, ms, live);
  return live;
}
