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

  useEffect(() => {
    closedRef.current = false;

    const connect = () => {
      if (closedRef.current) return;
      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl("/events"));
      } catch {
        scheduleRetry();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => setConnected(true);
      ws.onmessage = (ev) => {
        try {
          const data = JSON.parse(ev.data) as IJEvent;
          setEvents((prev) => [data, ...prev].slice(0, max));
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onclose = () => {
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
