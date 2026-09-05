"use client";

import {
  createContext,
  createElement,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { wsUrl } from "./api";
import type { IJEvent } from "./types";

export interface EventsState {
  events: IJEvent[];
  connected: boolean;
}

/* ---- Reconnect schedule (v1.230.0, audit Wave 4, FP6) -------------------- */

export const RECONNECT_BASE_MS = 2_500;
export const RECONNECT_CAP_MS = 30_000;
export const RECONNECT_JITTER = 0.2;

/**
 * Delay before reconnect attempt number `attempt` (0 = the first retry after
 * a socket closed). The first retry is the flat 2.5 s, exact: a blip should
 * recover fast and identically everywhere. Every later attempt doubles up to
 * the 30 s cap and carries ±20% jitter, so the windows of one install do not
 * all knock on a restarting daemon in lock-step. A successful open resets
 * the counter (see `EventsHub`).
 */
export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  if (attempt <= 0) return RECONNECT_BASE_MS;
  const raw = Math.min(RECONNECT_CAP_MS, RECONNECT_BASE_MS * 2 ** attempt);
  const jitter = 1 + RECONNECT_JITTER * (2 * random() - 1);
  return Math.round(raw * jitter);
}

/* ---- The hub: one socket, many subscribers ------------------------------- */

interface Listener {
  frame?: (event: IJEvent) => void;
  state?: (connected: boolean) => void;
}

/**
 * Owns ONE WebSocket to `/events` and fans every frame out to its listeners.
 * Before v1.230.0 every `useEvents` call site opened its own socket (4–7 per
 * window) and each retried on a flat 2.5 s, so an outage cost ~26 sockets a
 * minute PER HOOK. The socket opens when the first listener subscribes and
 * closes when the last one leaves; `EventsProvider` holds a listener for the
 * life of the window so a route change never drops the connection or the
 * replay cursor. Replay semantics are unchanged: the id of the LAST frame is
 * sent as `?since=` on reconnect (never on the first connect — nothing to
 * resume) so the daemon replays what happened in the gap.
 */
export class EventsHub {
  private listeners = new Set<Listener>();
  private ws: WebSocket | null = null;
  private retry: ReturnType<typeof setTimeout> | null = null;
  private attempts = 0;
  private lastId: string | null = null;
  connected = false;

  /** Subscribe; returns the unsubscribe. The first subscriber opens the socket. */
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    if (this.listeners.size === 1) this.connect();
    listener.state?.(this.connected);
    return () => {
      if (!this.listeners.delete(listener)) return;
      if (this.listeners.size === 0) this.teardown();
    };
  }

  private connect() {
    if (this.listeners.size === 0) return;
    let ws: WebSocket;
    try {
      const since = this.lastId;
      ws = new WebSocket(
        wsUrl(since ? `/events?since=${encodeURIComponent(since)}` : "/events"),
      );
    } catch {
      this.scheduleRetry();
      return;
    }
    this.ws = ws;

    ws.onopen = () => {
      if (ws !== this.ws) return;
      this.attempts = 0;
      this.setConnected(true);
    };
    ws.onmessage = (ev) => {
      // v1.226.0 (F-D-7): a socket the hub no longer owns (closed on the last
      // unsubscribe, but its events are still in flight) must not feed the
      // listeners or schedule a retry that orphans the live socket.
      if (ws !== this.ws) return;
      let data: IJEvent;
      try {
        data = JSON.parse(ev.data) as IJEvent;
      } catch {
        return; // malformed frame
      }
      if (typeof data.id === "string" && data.id) this.lastId = data.id;
      for (const l of this.listeners) l.frame?.(data);
    };
    ws.onclose = () => {
      if (ws !== this.ws) return;
      this.setConnected(false);
      this.scheduleRetry();
    };
    ws.onerror = () => {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    };
  }

  private scheduleRetry() {
    if (this.listeners.size === 0) return;
    if (this.retry) clearTimeout(this.retry);
    const delay = reconnectDelay(this.attempts);
    this.attempts += 1;
    this.retry = setTimeout(() => {
      this.retry = null;
      this.connect();
    }, delay);
  }

  /** Last listener gone: close the socket and forget the cursor — nobody is
   * left to resume for. (Under `EventsProvider` this never runs.) */
  private teardown() {
    if (this.retry) {
      clearTimeout(this.retry);
      this.retry = null;
    }
    const ws = this.ws;
    this.ws = null;
    if (ws) {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
    }
    this.attempts = 0;
    this.lastId = null;
    this.connected = false;
  }

  private setConnected(value: boolean) {
    if (this.connected === value) return;
    this.connected = value;
    for (const l of this.listeners) l.state?.(value);
  }
}

/* ---- Provider + hook ------------------------------------------------------ */

// A hook rendered outside any provider still shares this module-level hub
// (one socket per window either way); only the keep-alive across route
// changes needs the provider.
const defaultHub = new EventsHub();
const EventsContext = createContext<EventsHub | null>(null);

/**
 * Mounted ONCE in `app/layout.tsx`. Owns the window's hub and keeps its
 * socket open for the life of the window, so navigating between pages
 * neither reopens the connection nor loses the `?since=` cursor.
 */
export function EventsProvider({ children, hub }: { children?: ReactNode; hub?: EventsHub }) {
  const ref = useRef<EventsHub | null>(null);
  if (!ref.current) ref.current = hub ?? new EventsHub();
  const owned = ref.current;
  useEffect(() => owned.subscribe({}), [owned]);
  return createElement(EventsContext.Provider, { value: owned }, children);
}

/**
 * Subscribe to the daemon's `/events` feed. Every call site keeps its OWN
 * window (`max` newest frames) and `connected` flag over the window's one
 * shared socket; it never throws — when the daemon is offline it simply
 * reports `connected:false`. A frame whose id the window already holds is
 * not appended twice (a replay after reconnect is idempotent).
 */
export function useEvents(max = 100): EventsState {
  const hub = useContext(EventsContext) ?? defaultHub;
  const [events, setEvents] = useState<IJEvent[]>([]);
  const [connected, setConnected] = useState(false);

  useEffect(
    () =>
      hub.subscribe({
        frame: (data) =>
          setEvents((prev) => {
            if (typeof data.id === "string" && data.id && prev.some((e) => e.id === data.id)) {
              return prev;
            }
            return [data, ...prev].slice(0, max);
          }),
        state: setConnected,
      }),
    [hub, max],
  );

  return { events, connected };
}
