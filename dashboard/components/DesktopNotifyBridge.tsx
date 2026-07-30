"use client";

// The renderer half of the "This PC" notification destination (v1.118.0).
//
// DesktopChannel.send (daemon) publishes a `comm.desktop` event; this bridge
// watches the existing event stream and raises a NATIVE OS toast through the
// Electron preload. It is mounted once in the root layout, so it is alive on
// every page — including while the window is minimized to the tray, which is
// exactly when a notification matters. In a plain browser (no preload bridge)
// it is a silent no-op: the destination's tile copy says "desktop app" and
// this component keeps that promise honest rather than half-shimming it.

import { useEffect, useRef } from "react";

import { useEvents } from "@/lib/useEvents";

interface NotifyBridge {
  notify?: (title: string, body: string) => Promise<boolean>;
}

export function DesktopNotifyBridge() {
  const { events } = useEvents(50);
  // Events arrive newest-first and re-render as a rolling window — remember
  // what was already shown or a reconnect would replay toasts.
  const seenRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    const bridge = (window as unknown as { ironjarvis?: NotifyBridge }).ironjarvis;
    if (!bridge?.notify) return;
    for (const ev of events) {
      if (ev.type !== "comm.desktop" || !ev.id || seenRef.current.has(ev.id)) continue;
      seenRef.current.add(ev.id);
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
