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
// Tokens are buffered in refs and flushed at most every FLUSH_MS, so a fast
// model updates the live text ~8×/s, not once per token.
//
// v1.309.0 (UX/speed wave 1) — three corrections:
// * NARRATION IS NOT THE RESULT. A step's text that is followed by a tool call
//   (`tool_call` started, or the step's `round`) was the model talking about
//   what it is about to do ("I'll split this into …"); it moves to
//   `coordinatorNarration` and the live result starts over. Only the text of
//   a step with no tool call — the final answer — stays the deliverable. The
//   same rule holds for each teammate's mirrored frames. Client-side on
//   purpose: the daemon's stream feeds other pages that expect tokens to
//   accumulate, so a daemon-wide reset at step end would wipe their text.
// * THE LIVE TEXT IS A STORE, not state of this hook. The screen that owns
//   the hook (rail, cards, activity) never re-renders per flush; only the
//   component that SHOWS the text subscribes (`useMissionLive`) — the chat
//   stream's rule since v1.250.0.
// * A POLL THAT CHANGED NOTHING IS NOT AN UPDATE. The view is replaced only
//   when the daemon's payload differs, so a 2 s poll of a quiet mission does
//   not hand every card a new object.

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
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
  /** v1.309.0: the coordinator's last text that turned out to be narration
   *  (a tool call followed it) — shown as a phase line, never as the result. */
  coordinatorNarration?: string;
  /** session id → that member's streamed text. */
  members: Record<string, LiveMember>;
  /** The member whose text arrived most recently (the one "writing now"). */
  latest: string | null;
}

const EMPTY_LIVE: MissionLive = {
  coordinator: "",
  coordinatorPhase: "",
  coordinatorNarration: "",
  members: {},
  latest: null,
};

/** The live text, published at most once per flush to its subscribers. */
export interface MissionLiveStore {
  get: () => MissionLive;
  subscribe: (fn: () => void) => () => void;
}

interface WritableLiveStore extends MissionLiveStore {
  publish: (next: MissionLive) => void;
}

function createLiveStore(): WritableLiveStore {
  let current = EMPTY_LIVE;
  const listeners = new Set<() => void>();
  return {
    get: () => current,
    subscribe: (fn) => {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
    publish: (next) => {
      if (next === current) return;
      current = next;
      for (const fn of [...listeners]) fn();
    },
  };
}

/** Read the live text — call it ONLY in the component that shows it. */
export function useMissionLive(store: MissionLiveStore): MissionLive {
  return useSyncExternalStore(store.subscribe, store.get, store.get);
}

/** One line of narration for a phase slot: its first line, clipped. */
function narrationLine(text: string): string {
  const line = text.trim().split("\n")[0].trim();
  return line.length > 140 ? `${line.slice(0, 139)}…` : line;
}

export interface UseMission {
  view: MissionView | null;
  /** True until the first answer (or error) for this id. */
  loading: boolean;
  /** The last load failed (offline, 5xx). The previous view stays shown. */
  error: string | null;
  /** The daemon answered `found: false` — the mission is gone. */
  missing: boolean;
  /** The live text. Subscribe with `useMissionLive` where it is SHOWN. */
  liveStore: MissionLiveStore;
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
  const storeRef = useRef<WritableLiveStore | null>(null);
  if (storeRef.current === null) storeRef.current = createLiveStore();
  const store = storeRef.current;
  const liveRef = useRef<MissionLive>(EMPTY_LIVE);
  const dirtyRef = useRef(false);
  // The last payload the view was built from: a poll answering the same JSON
  // keeps the SAME view object, so memoised cards stay put.
  const lastRawRef = useRef("");
  const idRef = useRef(id);
  idRef.current = id;

  const load = useCallback(async () => {
    const want = idRef.current;
    if (!want) return;
    try {
      const raw = await get<unknown>(`/sessions/${encodeURIComponent(want)}/mission`);
      if (idRef.current !== want) return;
      let key = "";
      try {
        key = JSON.stringify(raw);
      } catch {
        key = "";
      }
      if (key && key === lastRawRef.current) {
        setError(null);
        return;
      }
      lastRawRef.current = key;
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
    lastRawRef.current = "";
    liveRef.current = EMPTY_LIVE;
    dirtyRef.current = false;
    store.publish(EMPTY_LIVE);
    if (id) void load();
  }, [id, load, store]);

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
    // A tool call (or the end of a step that ran tools) means the text before
    // it was narration, not the answer: keep it as a phase line, start over.
    const narrated = () => {
      const cur = liveRef.current;
      if (!cur.coordinator.trim()) return;
      touch({ ...cur, coordinator: "", coordinatorNarration: narrationLine(cur.coordinator) });
    };
    es.addEventListener("tool_call", (ev) => {
      if (parse(ev as MessageEvent).status === "started") narrated();
    });
    es.addEventListener("round", narrated);
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
      // The same narration rule as the coordinator's: a teammate's text
      // followed by its own tool call was not its draft.
      else if ((d.event === "tool_call" && inner.status === "started") || d.event === "round") {
        if (!cur.text) return;
        next = { ...cur, text: "", phase: cur.phase || narrationLine(cur.text) };
      }
      else if (d.event === "phase") next = { ...cur, phase: typeof inner.detail === "string" ? inner.detail : "" };
      else if (d.event === "done") {
        next = { ...cur, done: true };
        void load(); // a teammate finished — its card changes now, not in 2 s
      } else return;
      // The centre FOLLOWS one writer until it finishes: two teammates
      // streaming at once would otherwise flip the draft on every word. A
      // held writer whose text was narration (now empty) lets go too.
      const members = { ...liveRef.current.members, [sid]: next };
      const held = liveRef.current.latest;
      const latest =
        d.event === "token" &&
        (!held || held === sid || members[held]?.done || !members[held]?.text.trim())
          ? sid
          : held;
      touch({ ...liveRef.current, members, latest });
    });
    es.onerror = () => {
      /* EventSource reconnects on its own; the poll carries the view */
    };
    const flush = window.setInterval(() => {
      if (dirtyRef.current) {
        dirtyRef.current = false;
        store.publish(liveRef.current);
      }
    }, FLUSH_MS);
    return () => {
      es.close();
      window.clearInterval(flush);
      if (dirtyRef.current) {
        dirtyRef.current = false;
        store.publish(liveRef.current);
      }
    };
  }, [id, running, load, store]);

  // `load` is stable, so `reload` is too — a memoised card handed it as a
  // prop must not re-render because the screen did.
  const reload = useCallback(() => void load(), [load]);
  return { view, loading, error, missing, liveStore: store, running, reload };
}
