"use client";

import { useEffect, useState } from "react";
import dynamic from "next/dynamic";

/**
 * The app-wide overlays, loaded on demand (v1.250.0, S-08) — the drawer and
 * the palette (the first-run wizard is no longer mounted: redesign S9).
 *
 * The drawer, the command palette and the first-run wizard are all mounted by
 * the root layout and all render nothing until something opens them — yet their
 * code was in the chunk every one of the 43 routes downloads before first
 * paint. They are the biggest thing in the layout that the user has not asked
 * for yet.
 *
 * `ssr: false` because each is a client overlay keyed off browser state
 * (localStorage, a window event, a media query) and none contributes to the
 * prerendered HTML.
 *
 * WHY A PREFETCH AND NOT JUST next/dynamic: Ctrl+K must stay instant. A bare
 * dynamic import would start downloading the palette AT THE KEYPRESS, which is
 * the one moment the user is waiting. `requestIdleCallback` pulls all three in
 * once the page is quiet, so the press hits a module that is already in memory.
 * A press that still beats the idle callback awaits the very same import —
 * correct either way, just not as fast.
 */
const NavDrawer = dynamic(
  () => import("@/components/Sidebar").then((m) => ({ default: m.NavDrawer })),
  { ssr: false },
);
const CommandPalette = dynamic(
  () =>
    import("@/components/CommandPalette").then((m) => ({
      default: m.CommandPalette,
    })),
  { ssr: false },
);

/** Pull the overlay chunks in while the page is idle. */
function prefetchOverlays(): void {
  void import("@/components/Sidebar");
  void import("@/components/CommandPalette");
}

export function Overlays() {
  // Mount the overlays only after hydration. They are all `ssr: false`, so
  // rendering them on the first client pass buys nothing and would put three
  // dynamic boundaries in the way of first paint.
  const [ready, setReady] = useState(false);
  useEffect(() => {
    setReady(true);
    const w = window as unknown as {
      requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number;
    };
    if (typeof w.requestIdleCallback === "function") {
      w.requestIdleCallback(prefetchOverlays, { timeout: 2000 });
      return;
    }
    // Safari has no requestIdleCallback: a short timer is the same intent.
    const t = window.setTimeout(prefetchOverlays, 1200);
    return () => window.clearTimeout(t);
  }, []);

  if (!ready) return null;
  return (
    <>
      <NavDrawer />
      <CommandPalette />
      {/* Calm UI redesign S9 (AUDIT Q7): no blocking first-run screen. The
          wizard is no longer mounted; a detected model is offered in the
          composer with one tap, and the setup checklist lives in
          Everything › Status. */}
    </>
  );
}
