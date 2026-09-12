"use client";

// SSE consumer for AGENT-mode live tokens (FX-01). Where useChatStream drives a
// one-shot POST turn, an agent SESSION runs in the background and streams its
// tokens + tool activity over `GET /sessions/{id}/stream`. Because a GET stream
// is a natural fit for the browser's EventSource (auto-reconnect, event routing
// by name), we consume it that way — the bearer token rides in the URL via
// sseUrl() since EventSource can't set headers. Token/tool_call frames are
// normalised through the SAME decoder as the chat path; the stream closes on
// `done` (or any transport error).

import { useCallback, useEffect, useRef, useState } from "react";
import { sseUrl } from "./api";
import { sseEventFrom, upsertTool, type TextStore, type ToolCard } from "./useChatStream";

/** Where a run is in its own lifecycle (v1.149.0), straight from the daemon. */
export interface RunPhase {
  phase: "planning" | "running" | "verifying" | "assembling" | string;
  detail: string;
}

export interface UseRunStream {
  /** Accumulated agent text so far for the active session. */
  text: string;
  /** Live tool cards for the active session, keyed by call id. */
  tools: ToolCard[];
  /** The run's current phase, or null before the first phase frame. The daemon
   *  is authoritative — the client never infers a phase from silence, which is
   *  what made a planning run look like a stuck one. */
  phase: RunPhase | null;
  /** True while an EventSource is open for a session. */
  active: boolean;
  /** Open the live stream for a session id (tears down any prior stream). */
  start: (sessionId: string) => void;
  /** Close the live stream. */
  stop: () => void;
  /** The growing text, for a child that subscribes instead of making the caller
   *  re-render per token. Absent on a mocked stream — `useLiveText` falls back
   *  to `text`, which is why the existing test mocks need no change. */
  textStore?: TextStore;
}

/** Options for {@link useRunStream}. Mirrors `UseChatStreamOptions` so the two
 *  streaming lanes read the same way at their call sites. */
export interface UseRunStreamOptions {
  /** Keep the live text in React state (default true). Pass false when a child
   *  renders it through `textStore`, so the caller does not re-render per token. */
  textInState?: boolean;
}

/** Parse an EventSource message's raw `data` string into a typed frame. */
function frameFrom(event: string, raw: string): ReturnType<typeof sseEventFrom> {
  try {
    return sseEventFrom(event, JSON.parse(raw) as Record<string, unknown>);
  } catch {
    return null;
  }
}

/**
 * Subscribe to an agent session's live token stream. Never throws — a transport
 * error simply closes the stream and flips `active` to false.
 */
export function useRunStream(opts: UseRunStreamOptions = {}): UseRunStream {
  // v1.257.0 (S-02): the chat page passes `textInState: false` and renders the
  // agent text in a child that subscribes to `textStore`. Before this, every
  // token called setText HERE, re-rendering an 8,177-line page and firing its
  // scroll effect once per token. `sessions/[id]` keeps the default.
  const textInState = opts.textInState ?? true;
  const [text, setText] = useState("");
  const [tools, setTools] = useState<ToolCard[]>([]);
  const [phase, setPhase] = useState<RunPhase | null>(null);
  const [active, setActive] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const accRef = useRef("");
  // `accRef` is the truth — a handler reads it synchronously. State and
  // subscriber notifications are published at most once per animation frame.
  const listenersRef = useRef<Set<() => void>>(new Set());
  const frameRef = useRef<number | null>(null);
  const storeRef = useRef<TextStore | null>(null);
  if (storeRef.current === null) {
    storeRef.current = {
      get: () => accRef.current,
      subscribe: (cb: () => void) => {
        listenersRef.current.add(cb);
        return () => listenersRef.current.delete(cb);
      },
    };
  }
  const publish = useCallback(() => {
    if (textInState) setText(accRef.current);
    for (const cb of [...listenersRef.current]) cb();
  }, [textInState]);
  /** Publish NOW, cancelling any queued frame. Used when the run ENDS: a queued
   *  frame would be cancelled by teardown and the final token would never be
   *  shown — the reply would stop one word short of what the agent said. */
  const flushText = useCallback(() => {
    if (frameRef.current !== null) {
      cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    }
    publish();
  }, [publish]);
  const scheduleText = useCallback(() => {
    if (frameRef.current !== null) return; // a publish is already queued
    frameRef.current =
      typeof requestAnimationFrame === "function"
        ? requestAnimationFrame(() => {
            frameRef.current = null;
            publish();
          })
        : (setTimeout(() => {
            frameRef.current = null;
            publish();
          }, 16) as unknown as number);
  }, [publish]);

  const closeSource = useCallback(() => {
    if (esRef.current) {
      try {
        esRef.current.close();
      } catch {
        /* already closed */
      }
      esRef.current = null;
    }
  }, []);

  const stop = useCallback(() => {
    closeSource();
    setActive(false);
  }, [closeSource]);

  const start = useCallback(
    (sessionId: string) => {
      closeSource();
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      accRef.current = "";
      setText("");
      for (const cb of [...listenersRef.current]) cb(); // subscribers see the reset
      setTools([]);
      setPhase(null);
      setActive(true);

      let es: EventSource;
      try {
        es = new EventSource(sseUrl(`/sessions/${sessionId}/stream`));
      } catch {
        setActive(false);
        return;
      }
      esRef.current = es;

      es.addEventListener("token", (e) => {
        const ev = frameFrom("token", (e as MessageEvent).data);
        if (ev?.type === "token") {
          accRef.current += ev.text;
          scheduleText(); // at most one publish per frame, not one per token
        }
      });
      es.addEventListener("tool_call", (e) => {
        const ev = frameFrom("tool_call", (e as MessageEvent).data);
        if (ev?.type === "tool_call") setTools((prev) => upsertTool(prev, ev));
      });
      // v1.149.0 — the run says what it is doing. Decoded here rather than via
      // sseEventFrom: that decoder is SHARED with the chat lane, which has no
      // phases, and widening its union for one consumer would make every chat
      // switch carry a case it can never hit.
      es.addEventListener("phase", (e) => {
        try {
          const d = JSON.parse((e as MessageEvent).data) as Record<string, unknown>;
          const name = typeof d.phase === "string" ? d.phase : "";
          if (name) {
            setPhase({ phase: name, detail: typeof d.detail === "string" ? d.detail : "" });
          }
        } catch {
          /* a malformed frame just leaves the previous phase showing */
        }
      });
      es.addEventListener("done", () => {
        flushText(); // the final token must land BEFORE the stream is torn down
        stop();
      });
      // EventSource fires `error` on a transport drop AND surfaces a server-sent
      // `error` event the same way — either way the live run is over.
      es.addEventListener("error", () => {
        flushText(); // a dropped transport still keeps whatever did arrive
        stop();
      });
    },
    [closeSource, stop, scheduleText, flushText],
  );

  // Close the stream if the component unmounts mid-run.
  useEffect(() => () => closeSource(), [closeSource]);

  return { text, tools, phase, active, start, stop, textStore: storeRef.current ?? undefined };
}
