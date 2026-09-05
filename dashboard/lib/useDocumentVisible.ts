"use client";

import { useEffect, useState } from "react";

/**
 * Is this document VISIBLE (tab in front, window not minimised)?
 *
 * v1.230.0 (audit FP2): lifted out of `components/project/ProjectSurfaces.tsx`
 * and `app/projects/[id]/page.tsx`, which each carried a private copy, so
 * that `usePolledApi` and `DaemonProvider` can pause on the same fact. Cost
 * should scale with attention, not wall-clock: a minimised dashboard polled
 * at full rate for 6.5 minutes in the audit.
 *
 * SSR-safe: assumes visible until mounted, then tracks `visibilitychange`.
 */
export function useDocumentVisible(): boolean {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const sync = () => setVisible(!document.hidden);
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => document.removeEventListener("visibilitychange", sync);
  }, []);
  return visible;
}
