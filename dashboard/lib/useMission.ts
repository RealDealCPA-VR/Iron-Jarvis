"use client";

// THE MISSION HOOK (v1.307.0): one coordinator session → the view the
// mission screen renders.
//
// Two channels, each doing what it is good at:
// * the VIEW (members, progress, activity, deliverable) is POLLED from
//   `GET /sessions/{id}/mission` — the daemon composes it from the ledger, so
//   the page never re-derives what a tool name means or how far a member got;
// * the LIVE TEXT rides the coordinator's SSE stream (`/sessions/{id}/stream`).
//   Since v1.307.0 the daemon mirrors every delegated member's frames into
//   that ONE stream as `member` events, so the report can be watched as it is
//   written without an EventSource per teammate (browsers cap those at ~6).
//
// Tokens are buffered in refs and flushed to state at most every FLUSH_MS, so
// a fast model re-renders this screen ~8×/s, not once per token.

import { useCallback, useEffect, useRef, useState } from "react";
import { get, sseUrl } from "./api";
import { MISSION_TERMINAL, decodeMission, type MissionView } from "./mission";
import { useVisibleInterval } from "./useVisibleInterval";

export const MISSION_POLL_MS = 2000;
const FLUSH_MS = 120;

export interface LiveMember {
  name: string;
  text: string;
  done: boolean;
  phase: string;
}

export interface MissionLive {
  /** The coordinator's own streamed text (its final answer as it is written). */
  coordinator: string;
  coordinatorPhase: string;
  /** session id → that member's streamed text. */
  members: Record<string, LiveMember>;
  /** The member whose text arrived most recently (the one "writing now"). */
  latest: string | null;
}

const EMPTY_LIVE: MissionLive = { coordinator: "", coordinatorPhase: "", members: {}, latest: null };

export interface UseMission {
  view: MissionView | null;
  /** True until the first answer (or error) for this id. */
  loading: boolean;
  /** The last load failed (offline, 5xx). The previous view stays shown. */
  error: string | null;
  /** The daemon answered `found: false` — the mission is gone. */
  missing: boolean;
  live: MissionLive;
  running: boolean;
  reload: () => void;
}

function displayFromAgent(agent: string): string {
  const [head, ...rest] = agent.split(":");
  const tail = rest.join(":");
  if (tail && ["custom", "remote", "builtin", "dynamic"].includes(head.toLowerCase())) return tail;
  return agent ? agent[0].toUpperCase() + agent.slice(1) : "Agent";
}

export function useMission(id: string | null): UseMission {
  const [view, setView] = useState<MissionView | null>(null);
  const [loading, setLoading] = useState(Boolean(id));
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [live, setLive] = useState<MissionLive>(EMPTY_LIVE);
  const liveRef = useRef<MissionLive>(EMPTY_LIVE);
  const dirtyRef = useRef(false);
  const idRef = useRef(id);
  idRef.current = id;

  const load = useCallback(async () => {
    const want = idRef.current;
    if (!want) return;
    try {
      const raw = await get<unknown>(`/sessions/${encodeURIComponent(want)}/mission`);
      if (idRef.current !== want) return;
      const v = decodeMission(raw);
      setMissing(v === null);
      if (v) setView(v);
      setError(null);
    } catch (e) {
      if (idRef.current !== want) return;
      setError(e instanceof Error ? e.message : "could not load the mission");
    } finally {
      if (idRef.current === want) setLoading(false);
    }
  }, []);

  // A new id: reset everything and load once.
  useEffect(() => {
    setView(null);
    setMissing(false);
    setError(null);
    setLoading(Boolean(id));
    liveRef.current = EMPTY_LIVE;
    setLive(EMPTY_LIVE);
    if (id) void load();
  }, [id, load]);

  const status = view?.session.status ?? "";
  const running = Boolean(id) && !MISSION_TERMINAL.has(status) && !missing;
  useVisibleInterval(() => void load(), MISSION_POLL_MS, running);

  // The live stream — only while the mission runs, and only where the browser
  // has EventSource (jsdom does not; the poll still carries the view there).
  useEffect(() => {
    if (!id || !running || typeof EventSource === "undefined") return;
    const es = new EventSource(sseUrl(`/sessions/${encodeURIComponent(id)}/stream`));
    const parse = (ev: MessageEvent): Record<string, unknown> => {
      try {
        const d = JSON.parse(String(ev.data));
        return d && typeof d === "object" ? (d as Record<string, unknown>) : {};
      } catch {
        return {};
      }
    };
    const touch = (next: MissionLive) => {
      liveRef.current = next;
      dirtyRef.current = true;
    };
    es.addEventListener("token", (ev) => {
      const t = parse(ev as MessageEvent).text;
      if (typeof t === "string" && t)
        touch({ ...liveRef.current, coordinator: liveRef.current.coordinator + t });
    });
    es.addEventListener("reset", () => touch({ ...liveRef.current, coordinator: "" }));
    es.addEventListener("phase", (ev) => {
      const d = parse(ev as MessageEvent);
      const detail = typeof d.detail === "string" ? d.detail : "";
      touch({ ...liveRef.current, coordinatorPhase: detail });
    });
    es.addEventListener("done", () => {
      es.close();
      void load();
    });
    es.addEventListener("member", (ev) => {
      const d = parse(ev as MessageEvent);
      const member = (d.member ?? {}) as Record<string, unknown>;
      const sid = typeof member.session_id === "string" ? member.session_id : "";
      if (!sid) return;
      const inner = (d.data ?? {}) as Record<string, unknown>;
      const cur = liveRef.current.members[sid] ?? {
        name: displayFromAgent(typeof member.agent === "string" ? member.agent : ""),
        text: "",
        done: false,
        phase: "",
      };
      let next = cur;
      if (d.event === "token" && typeof inner.text === "string") next = { ...cur, text: cur.text + inner.text };
      else if (d.event === "reset") next = { ...cur, text: "" };
      else if (d.event === "phase") next = { ...cur, phase: typeof inner.detail === "string" ? inner.detail : "" };
      else if (d.event === "done") {
        next = { ...cur, done: true };
        void load(); // a teammate finished — its card changes now, not in 2 s
      } else return;
      // The centre FOLLOWS one writer until it finishes: two teammates
      // streaming at once would otherwise flip the draft on every word.
      const members = { ...liveRef.current.members, [sid]: next };
      const held = liveRef.current.latest;
      const latest =
        d.event === "token" && (!held || held === sid || members[held]?.done) ? sid : held;
      touch({ ...liveRef.current, members, latest });
    });
    es.onerror = () => {
      /* EventSource reconnects on its own; the poll carries the view */
    };
    const flush = window.setInterval(() => {
      if (dirtyRef.current) {
        dirtyRef.current = false;
        setLive(liveRef.current);
      }
    }, FLUSH_MS);
    return () => {
      es.close();
      window.clearInterval(flush);
      if (dirtyRef.current) {
        dirtyRef.current = false;
        setLive(liveRef.current);
      }
    };
  }, [id, running, load]);

  return { view, loading, error, missing, live, running, reload: () => void load() };
}
