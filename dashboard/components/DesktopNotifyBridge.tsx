"use client";

// The renderer half of the "This PC" notification destination (v1.118.0).
//
// DesktopChannel.send (daemon) publishes a `comm.desktop` event; this bridge
// watches the existing event stream and raises a NATIVE OS toast through the
// Electron preload. It is mounted once in the root layout, so it is alive on
// every page — including while the window is MINIMISED. In a plain browser
// (no preload bridge) it is a silent no-op: the destination's tile copy says
// "desktop app" and this component keeps that promise honest rather than
// half-shimming it.
//
// WHAT IT CANNOT DO, and used to claim it could (v1.249.0, R-03): closing the
// window to the TRAY destroys the renderer (`hideToTray` in desktop/main.js),
// so this bridge — and every other page-side toast — is gone until a window
// exists again. The desktop app itself watches while no window is open
// (`installAskWatcher` in desktop/main.js): agent and chat approvals waiting
// on the user AND (v1.360.0, lane 10A) unattended runs that failed or fell
// short, said once each in the bell's words (`planRunOutcomeNotifications`).
// A `comm.desktop` message that arrives with no window raises NO toast and is
// NOT in the bell either — it is only in the event log (corrected v1.351.0,
// G03: this comment used to say it waited in the bell). What the bell DOES
// remember: when a window opens again it replays the unattended runs that
// finished, fell short or failed meanwhile (NotificationBell's "While you were
// away", read from GET /audit).
//
// ONE NOTICE PER OUTCOME AND PER ASK (v1.360.0): while a window is open the
// bell's own ping is the desktop notice for a run's outcome and for an ask, so
// the daemon sends NO `comm.desktop` message for exactly those
// (comm/notifier.bell_pings, an agent ask, a parked workflow's question). What
// still arrives here is everything else a destination is told: a clean
// schedule's "done" line, a workflow's own notify step, goal news, a failover.
//
// ONE WINDOW SPEAKS (v1.361.0 integrate, after lane 11A): the main window
// while it is open; closed to the tray, the ONE pop-out the desktop app names
// (`lib/desktopShell.quietWindow`, the bell's own rule). Until v1.361.0 a
// pop-out was always quiet here, so with the main window in the tray a "This
// PC" line reached no toast at all.

import { useEffect, useRef } from "react";

import { useEvents } from "@/lib/useEvents";
import { quietWindow } from "@/lib/desktopShell";

/** Does this window leave the "This PC" toasts to another? Never throws (a
 *  test's partial `lib/desktopShell` mock reads as the main window). */
function quietHere(): boolean {
  try {
    return quietWindow();
  } catch {
    return false;
  }
}

interface NotifyBridge {
  notify?: (title: string, body: string) => Promise<boolean>;
}

/** The one /events type the bridge reads (v1.311.0, wave 3). It is mounted
 *  on every page; any other frame cost it a render for nothing. */
export const DESKTOP_NOTIFY_EVENT_TYPES: readonly string[] = ["comm.desktop"];

export function DesktopNotifyBridge() {
  const { events } = useEvents(50, { types: DESKTOP_NOTIFY_EVENT_TYPES });
  // Events arrive newest-first and re-render as a rolling window — remember
  // what was already shown or a reconnect would replay toasts.
  const seenRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const bridge = (window as unknown as { ironjarvis?: NotifyBridge }).ironjarvis;
    if (!bridge?.notify) return;
    // v1.283.0: every window holds its own events socket, so with a module
    // popped out the same comm.desktop event would toast once per window: ONE
    // window speaks. v1.361.0: decided when the events land, never cached —
    // which window speaks changes while a pop-out is open.
    const quiet = quietHere();
    for (const ev of events) {
      if (ev.type !== "comm.desktop" || !ev.id || seenRef.current.has(ev.id)) continue;
      // A quiet window still RECORDS the line (the window that speaks showed
      // it), so if it becomes the one that speaks it never says it again.
      seenRef.current.add(ev.id);
      if (quiet) continue;
      const payload = (ev.payload ?? {}) as { title?: string; message?: string };
      void bridge.notify(payload.title || "Iron Jarvis", payload.message || "");
    }
    // Bound the replay-guard set so a week in the tray doesn't grow it forever.
    if (seenRef.current.size > 500) {
      seenRef.current = new Set([...seenRef.current].slice(-200));
    }
  }, [events]);

  return null;
}
