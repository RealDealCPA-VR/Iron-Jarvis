"use client";

import { useEffect, useRef } from "react";
import { useEvents } from "@/lib/useEvents";
import { DEVICE_EVENT_TYPES, namesDeviceKey, refreshDeviceSettings } from "@/lib/deviceSettings";

/** Built ONCE so useEvents sees the same list every render. */
const DEVICE_EVENTS_OPTS = { types: DEVICE_EVENT_TYPES };

/**
 * v1.433.0 (Wave 1 B, decision D1): mounted ONCE in the layout. Reads this
 * device's settings when the window opens, again when the events socket
 * comes back after a drop (the daemon may have restarted, or been offline
 * when the window opened), and again whenever a `config.changed` event names
 * a `device.*` key — and applies what changed (lib/deviceSettings). Renders
 * nothing, and only this component hears the events: the pages it updates
 * re-render only when one of their own values really changes.
 */
export function DeviceSettingsBridge() {
  const { events, connected } = useEvents(10, DEVICE_EVENTS_OPTS);
  const lastSeenRef = useRef<string | null>(null);
  const socketRef = useRef<"never" | "up" | "down">("never");

  useEffect(() => {
    void refreshDeviceSettings();
  }, []);

  // A RECONNECT (never the first connect: the read above covers that).
  useEffect(() => {
    if (connected) {
      if (socketRef.current === "down") void refreshDeviceSettings();
      socketRef.current = "up";
    } else if (socketRef.current === "up") {
      socketRef.current = "down";
    }
  }, [connected]);

  useEffect(() => {
    if (events.length === 0) return;
    // Newest first: look only at frames since the last one handled.
    let named = false;
    for (const ev of events) {
      if (ev.id && ev.id === lastSeenRef.current) break;
      if (namesDeviceKey(ev.payload)) named = true;
    }
    lastSeenRef.current = events[0].id || lastSeenRef.current || "";
    if (named) void refreshDeviceSettings();
  }, [events]);

  return null;
}
