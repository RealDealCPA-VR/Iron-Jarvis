"use client";

/**
 * THE DAEMON'S OFFLINE->ONLINE EPOCH, ON ITS OWN (v1.311.0, wave 3,
 * daemon-ctx-rerender-storm).
 *
 * `useApi` needs exactly one thing from the daemon: the `epoch` that ticks on
 * each offline->online edge (contract C7, v1.226.0). Reading it through
 * `useDaemon()` subscribed every data-fetching component in the app (~70
 * files) to the WHOLE /health context, so a changed /health — a model switch,
 * a provider coming up — re-rendered all of them for a value none of them
 * reads. `DaemonProvider` publishes the epoch here as well, and `useApi` reads
 * only this.
 *
 * A MODULE OF ITS OWN ON PURPOSE: ~20 suites mock `@/lib/daemon` with a
 * factory that exports `useDaemon` alone, and a mocked module throws on any
 * export its factory did not define. Kept here, a real `useApi` under such a
 * mock reads the default 0 — exactly the `epoch: 0` those mocks report.
 */

import { createContext, useContext } from "react";

/** 0 outside a `DaemonProvider`, matching `useDaemon()`'s fallback. */
export const DaemonEpochContext = createContext<number>(0);

/** The daemon's offline->online epoch alone. A component that needs only
 *  this re-renders on a reconnect edge and on nothing else. */
export function useDaemonEpoch(): number {
  return useContext(DaemonEpochContext);
}
