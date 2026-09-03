"use client";

import { useEffect, useRef, useState } from "react";
import { wsUrl } from "./api";
import type { IJEvent } from "./types";

export interface EventsState {
  events: IJEvent[];
  connected: boolean;
}

/**
 * Subscribe to the daemon's `/events` WebSocket. Reconnects with backoff and
 * never throws — when the daemon is offline it simply reports `connected:false`.
 */
export function useEvents(max = 100): EventsState {
  const [events, setEvents] = useState<IJEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retryRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const closedRef = useRef(false);
  // v1.226.0 (contract C1): the id of the LAST frame received. A reconnect
  // passes it as `?since=` so the daemon replays what happened in the gap
  // (an approval card or a finished-run toast that fired while the socket
  // was down). Never sent on the first connect — there is nothing to resume.
  const lastIdRef = useRef<string | null>(null);

  useEffect(() => {
    closedRef.current = false;

    const connect = () => {
      if (closedRef.current) return;
      let ws: WebSocket;
      try {
        const since = lastIdRef.current;
        ws = new WebSocket(
          wsUrl(since ? `/events?since=${encodeURIComponent(since)}` : "/events"),
        );
      } catch {
        scheduleRetry();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onmessage = (ev) => {
        // v1.226.0 (F-D-7): a socket this hook no longer owns (a StrictMode
        // remount closed it, but its events are still in flight) must not
        // feed the list or schedule a retry that orphans the live socket.
        if (ws !== wsRef.current) return;
        try {
          const data = JSON.parse(ev.data) as IJEvent;
          if (typeof data.id === "string" && data.id) lastIdRef.current = data.id;
          setEvents((prev) => [data, ...prev].slice(0, max));
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
        if (ws !== wsRef.current) return;
        setConnected(false);
        scheduleRetry();
      };
      ws.onerror = () => {
        try {
          ws.close();
        } catch {
          /* ignore */
        }
      };
    };

    const scheduleRetry = () => {
      if (closedRef.current) return;
      if (retryRef.current) clearTimeout(retryRef.current);
      retryRef.current = setTimeout(connect, 2500);
    };

    connect();

    return () => {
      closedRef.current = true;
      if (retryRef.current) clearTimeout(retryRef.current);
      if (wsRef.current) {
        try {
          wsRef.current.close();
        } catch {
          /* ignore */
        }
      }
    };
  }, [max]);

  return { events, connected };
}
