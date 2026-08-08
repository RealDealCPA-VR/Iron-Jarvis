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
import { sseEventFrom, upsertTool, type ToolCard } from "./useChatStream";

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
export function useRunStream(): UseRunStream {
  const [text, setText] = useState("");
  const [tools, setTools] = useState<ToolCard[]>([]);
  const [phase, setPhase] = useState<RunPhase | null>(null);
  const [active, setActive] = useState(false);
  const esRef = useRef<EventSource | null>(null);
  const accRef = useRef("");

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
      accRef.current = "";
      setText("");
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
          setText(accRef.current);
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
        stop();
      });
      // EventSource fires `error` on a transport drop AND surfaces a server-sent
      // `error` event the same way — either way the live run is over.
      es.addEventListener("error", () => {
        stop();
      });
    },
    [closeSource, stop],
  );

  // Close the stream if the component unmounts mid-run.
  useEffect(() => () => closeSource(), [closeSource]);

  return { text, tools, phase, active, start, stop };
}
