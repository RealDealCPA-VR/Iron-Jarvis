"use client";

import { useMemo } from "react";
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
];

export function MoodOrb() {
  const { events, connected } = useEvents(100, { types: MOOD_EVENT_TYPES });

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
    const done = new Set<string>();
    for (const e of events) {
      if (e.type === "session.completed" || e.type === "agent.completed")
        done.add(sid(e));
    }
    for (const e of events) {
      if (
        (e.type === "session.created" || e.type === "agent.started") &&
        !done.has(sid(e))
      )
        return "thinking";
    }

    return "idle";
  }, [events]);

  const color = COLOR[mood];
  const thinking = mood === "thinking";
  const alert = mood === "alert";
  const dim = mood === "idle";

  return (
    <span
      className="relative grid h-9 w-9 shrink-0 place-items-center sm:h-8 sm:w-8"
      title={`${LABEL[mood]}${connected ? "" : " · stream offline"}`}
      aria-label={LABEL[mood]}
      role="img"
    >
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
    </span>
  );
}
