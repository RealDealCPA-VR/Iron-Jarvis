"use client";

import { useEffect, useRef } from "react";
import { useDocumentVisible } from "./useDocumentVisible";

/**
 * Run `fn` every `ms` — ONLY while the document is visible (v1.250.0, S-09).
 *
 * The counterpart of `usePolledApi` for panels that poll through their own
 * loader instead of `useApi`: nine of them (the Build files panel, the
 * session blackboard / worklist / files / team tree, the workflow draft card,
 * Creative, Workflows and Channels) kept 2-8 s timers running while the
 * window was minimised, so a dashboard nobody was looking at still woke the
 * daemon dozens of times a minute.
 *
 * Same contract as `usePolledApi`: the interval is TORN DOWN while hidden
 * (never merely ignored), and the hidden->visible edge fires `fn` ONCE so the
 * panel is current the moment the user looks at it — never on mount, because
 * the caller's own first load already covers that.
 *
 * `enabled` keeps the call site's existing guard (a closed panel, a run that
 * has finished) in one place; `false` is the same as an unmounted timer.
 */
export function useVisibleInterval(
  fn: () => void,
  ms: number,
  enabled = true,
): void {
  const visible = useDocumentVisible();
  // The latest callback, so a re-created closure never restarts the interval.
  const fnRef = useRef(fn);
  fnRef.current = fn;

  useEffect(() => {
    if (!enabled || !visible || ms <= 0) return;
    const id = window.setInterval(() => fnRef.current(), ms);
    return () => window.clearInterval(id);
  }, [enabled, visible, ms]);

  // One catch-up run on the hidden->visible edge (not on mount).
  const wasHiddenRef = useRef(false);
  useEffect(() => {
    if (!enabled) return;
    if (!visible) {
      wasHiddenRef.current = true;
      return;
    }
    if (wasHiddenRef.current) {
      wasHiddenRef.current = false;
      fnRef.current();
    }
  }, [enabled, visible]);
}
