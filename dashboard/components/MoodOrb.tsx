"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { get } from "@/lib/api";
import { useEvents } from "@/lib/useEvents";
import type { IJEvent } from "@/lib/types";

/**
 * MoodOrb — a small, dependency-free arc-reactor orb that reflects how the
 * system "feels" right now, driven entirely by the live `/events` WebSocket:
 *
 *   • idle      — calm, dim cyan: nothing running, nothing waiting.
 *   • thinking  — pulsing/rotating cyan: a session or agent is in flight.
 *   • alert     — rose glow: a review or proposal is waiting for the human.
 *
 * SSR-safe (renders the idle state on the server; `useEvents` only subscribes in
 * an effect, so the first client render matches) and adds no new dependencies —
 * pure inline SVG + the existing Tailwind keyframes (animate-spin-slow,
 * animate-pulse-glow). Decorative: announced via aria-label / title only.
 *
 * v1.313.0: at rest it is a DOT AND A RING. Eight spokes around a circle is a
 * settings cog — users read the idle orb as a Settings button and clicked a
 * thing that does nothing. The spokes now appear only while thinking (where
 * they spin, so they read as motion, not a gear). Its box is the bar's one
 * control height (TitleBar's BAR_CONTROL_H).
 *
 * v1.333.0: it no longer spins "thinking" forever after a daemon restart cut a
 * run off. A restart publishes no session.completed for the runs it ended:
 * the boot reconcile publishes ONE `sessions.interrupted` {session_ids} (now
 * read here: those ids are done), and since that frame is sent before the
 * window's socket is back (an unknown `?since=` id replays nothing), the orb
 * also asks once, on each socket RECONNECT, how the runs it still thinks are
 * in flight really stand (the slim `GET /sessions/{id}?transcript=0`, one per
 * run, usually none or one). Nothing is read while connected.
 *
 * v1.352.0 (G26): in the sidebar footer the orb is a BUTTON that opens
 * "Running now" (components/RunningNow.tsx, the popover AppSidebar draws on
 * the Help menu's surface): pass `onPress` (+ `expanded`, `controls`). It
 * keeps its look exactly; its words move to the button's name. Without
 * `onPress` (the system map's reactor, which sits in an aria-hidden,
 * pointer-events-none layer) it stays the decorative `role="img"` it was.
 */

type Mood = "idle" | "thinking" | "alert";

/** Best-effort key for grouping a session's lifecycle events. */
function sid(e: IJEvent): string {
  return String(e.session_id ?? (e.payload?.session_id as string | undefined) ?? e.id);
}

const COLOR: Record<Mood, string> = {
  idle: "#3f4651", // dim slate — present but resting
  thinking: "#22d3ee", // accent cyan
  alert: "#fb7185", // rose-400
};

const LABEL: Record<Mood, string> = {
  idle: "Idle — nothing running",
  thinking: "Thinking — a session is in flight",
  alert: "Attention needed — something is waiting for you",
};

/** The /events types the orb reads (v1.311.0, wave 3): reviews and
 *  proposals for ALERT, session/agent lifecycle for THINKING. The orb is in
 *  the layout on every page; any other frame cost it a render for nothing.
 *  Add a type here in the same change that makes `mood` read it. */
export const MOOD_EVENT_TYPES: readonly string[] = [
  "review.*",
  "autonomy.proposed",
  "autonomy.executed",
  "session.created",
  "session.completed",
  "agent.started",
  "agent.completed",
  "sessions.interrupted",
];

/** Session statuses that end a run (models.SessionStatus). */
const ENDED = new Set(["completed", "failed", "cancelled"]);
const NONE: ReadonlySet<string> = new Set();

/**
 * The sessions/agents that started and have not completed yet, in the
 * window's order — the orb's THINKING rule, exported so the Running now list
 * re-reads when this set changes (v1.352.0). `settled` holds START frame ids a
 * reconnect check found already ended (see the reconnect effect below).
 */
export function inFlightIds(events: readonly IJEvent[], settled: ReadonlySet<string> = NONE): string[] {
  const done = new Set<string>();
  for (const e of events) {
    if (e.type === "session.completed" || e.type === "agent.completed") done.add(sid(e));
    if (e.type === "sessions.interrupted") {
      const ids = (e.payload as { session_ids?: unknown } | null)?.session_ids;
      if (Array.isArray(ids)) for (const id of ids) done.add(String(id));
    }
  }
  const live: string[] = [];
  for (const e of events) {
    if (
      (e.type === "session.created" || e.type === "agent.started") &&
      !done.has(sid(e)) &&
      !settled.has(String(e.id)) &&
      !live.includes(sid(e))
    )
      live.push(sid(e));
  }
  return live;
}

export function MoodOrb({
  onPress,
  expanded = false,
  controls,
}: {
  /** Makes the orb a button (the sidebar footer: opens Running now). */
  onPress?: () => void;
  /** Whether what it opens is open (aria-expanded). */
  expanded?: boolean;
  /** The id of what it opens (aria-controls). */
  controls?: string;
} = {}) {
  const { events, connected } = useEvents(100, { types: MOOD_EVENT_TYPES });
  // The START frames of runs the daemon said had ended when asked after a
  // reconnect (see above). Keyed by the frame, not the session: a continue
  // re-publishes session.created under the SAME session id, and that new
  // start must spin the orb again.
  const [settled, setSettled] = useState<ReadonlySet<string>>(NONE);

  // THINKING: the sessions/agents that started and have not completed yet.
  const inFlight = useMemo<string[]>(() => inFlightIds(events, settled), [events, settled]);
  const inFlightRef = useRef(inFlight);
  inFlightRef.current = inFlight;
  const eventsRef = useRef(events);
  eventsRef.current = events;

  // On a socket RECONNECT (never the first connect), ask once how the runs
  // still shown as in flight really stand; a run the daemon says ended (or no
  // longer knows) stops the spin.
  const openedRef = useRef(false);
  const droppedRef = useRef(false);
  useEffect(() => {
    if (!connected) {
      if (openedRef.current) droppedRef.current = true;
      return;
    }
    openedRef.current = true;
    if (!droppedRef.current) return;
    droppedRef.current = false;
    const ids = inFlightRef.current;
    if (ids.length === 0) return;
    let alive = true;
    void Promise.all(
      ids.map(async (id) => {
        try {
          const d = await get<{ session?: { status?: string } }>(
            `/sessions/${encodeURIComponent(id)}?transcript=0`,
          );
          const st = d?.session?.status;
          return st && ENDED.has(st) ? id : null;
        } catch (err) {
          return (err as { status?: number } | null)?.status === 404 ? id : null;
        }
      }),
    ).then((ended) => {
      const gone = new Set(ended.filter((x): x is string => x !== null));
      if (!alive || gone.size === 0) return;
      // The start frames those runs had when asked: a later start is new.
      const starts = eventsRef.current
        .filter((e) => (e.type === "session.created" || e.type === "agent.started") && gone.has(sid(e)))
        .map((e) => String(e.id));
      setSettled((prev) => new Set([...prev, ...starts]));
    });
    return () => {
      alive = false;
    };
  }, [connected]);

  const mood = useMemo<Mood>(() => {
    // ALERT: an unresolved review request or a fresh autonomy proposal wins.
    const resolved = new Set<string>();
    for (const e of events) {
      if (e.type.startsWith("review.") && e.type !== "review.requested")
        resolved.add(sid(e));
      if (e.type === "autonomy.executed") resolved.add(sid(e));
    }
    for (const e of events) {
      if (e.type === "review.requested" && !resolved.has(sid(e))) return "alert";
      if (e.type === "autonomy.proposed" && !resolved.has(sid(e))) return "alert";
    }

    // THINKING: a session/agent that started but hasn't completed yet.
    if (inFlight.length > 0) return "thinking";

    return "idle";
  }, [events, inFlight]);

  const color = COLOR[mood];
  const thinking = mood === "thinking";
  const alert = mood === "alert";
  const dim = mood === "idle";

  const words = `${LABEL[mood]}${connected ? "" : " · stream offline"}`;
  const look = (
    <>
      {/* soft halo */}
      <span
        className={`absolute inset-0 rounded-full blur-[6px] transition-opacity duration-500 ${
          dim ? "opacity-40" : "opacity-100"
        } ${alert ? "animate-pulse-glow" : ""}`}
        style={{ backgroundColor: color, opacity: dim ? 0.18 : 0.28 }}
      />
      <svg
        viewBox="0 0 24 24"
        className="relative h-6 w-6"
        fill="none"
        stroke={color}
        style={{ transition: "stroke 500ms ease" }}
      >
        {/* rotating spoke ring — drawn ONLY while thinking (at rest the
            spokes made the orb read as a settings gear) */}
        {thinking && (
          <g
            className="animate-spin-slow"
            style={{ transformOrigin: "12px 12px" }}
            opacity={0.85}
          >
            {Array.from({ length: 8 }).map((_, i) => {
              const a = (i * Math.PI) / 4;
              const x1 = 12 + Math.cos(a) * 4.4;
              const y1 = 12 + Math.sin(a) * 4.4;
              const x2 = 12 + Math.cos(a) * 7.4;
              const y2 = 12 + Math.sin(a) * 7.4;
              return (
                <line
                  key={i}
                  x1={x1}
                  y1={y1}
                  x2={x2}
                  y2={y2}
                  strokeWidth="1.1"
                  strokeLinecap="round"
                />
              );
            })}
          </g>
        )}
        <circle cx="12" cy="12" r="8.4" strokeWidth="1.1" opacity={dim ? 0.45 : 0.5} />
        {/* core — pulses on alert */}
        <circle
          cx="12"
          cy="12"
          r="3.2"
          strokeWidth="1.3"
          fill={color}
          fillOpacity={dim ? 0.15 : 0.3}
          className={alert ? "animate-pulse-glow" : ""}
          style={{ transformOrigin: "12px 12px" }}
        />
        <circle cx="12" cy="12" r="1.1" fill={color} stroke="none" />
      </svg>
    </>
  );

  if (onPress) {
    return (
      <button
        type="button"
        onClick={onPress}
        aria-haspopup="dialog"
        aria-expanded={expanded}
        aria-controls={controls}
        aria-label={`Running now: ${LABEL[mood]}`}
        title={`${words} · see what is running`}
        data-testid="mood-orb"
        data-mood={mood}
        className="relative grid h-9 w-9 shrink-0 place-items-center rounded-lg hover:bg-white/[0.06] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/60 sm:h-8 sm:w-8"
      >
        {look}
      </button>
    );
  }

  return (
    <span
      className="relative grid h-9 w-9 shrink-0 place-items-center sm:h-8 sm:w-8"
      title={words}
      aria-label={LABEL[mood]}
      role="img"
    >
      {look}
    </span>
  );
}
