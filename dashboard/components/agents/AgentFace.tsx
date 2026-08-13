"use client";

import { type ReactElement, useEffect, useState } from "react";

/**
 * AgentFace — a deterministic identity for every agent (v1.171.0).
 *
 * Concept adapted from NousResearch/Hermes-Bot-Mode (MIT): each agent gets a
 * stable geometric face — shape and color seeded from its name — with eyes
 * that carry MOOD. The difference here: Iron Jarvis has REAL state to drive
 * the moods. A delegated child's face scans while its session runs, shows
 * X-X on failure, and blinks idly between jobs — the face is a status
 * surface, not a decoration.
 *
 * Deterministic: same name → same face, everywhere (roster, TeamTree, kanban,
 * round table). An uploaded/generated portrait (avatarUrl) always wins over
 * the geometric face.
 */

const SHAPES = [
  "circle",
  "squircle",
  "pill",
  "triangle",
  "hexagon",
  "cloud",
  "drop",
] as const;
type Shape = (typeof SHAPES)[number];

/** Ten flat body colors (hex, mid-saturation so both themes hold). */
const COLORS = [
  "#e8e4da", // parchment
  "#8a6f52", // brown
  "#c65949", // red
  "#d98a3d", // orange
  "#3f9e8b", // teal
  "#3fb1c9", // cyan
  "#4f6fd8", // royal
  "#8b64c9", // violet
  "#c65a9e", // magenta
  "#a8b0b8", // silver
] as const;

/** Stable 32-bit hash (FNV-1a) — the ONE seeding function. */
export function faceSeed(name: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < name.length; i++) {
    h ^= name.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

export function faceShape(name: string): Shape {
  return SHAPES[faceSeed(name) % SHAPES.length];
}

export function faceColor(name: string): string {
  // Second-order seed so shape and color don't correlate.
  return COLORS[Math.floor(faceSeed(name) / 7) % COLORS.length];
}

/** Dark eyes on light bodies, parchment on dark (perceptual luminance). */
export function eyeColor(body: string): string {
  const r = parseInt(body.slice(1, 3), 16);
  const g = parseInt(body.slice(3, 5), 16);
  const b = parseInt(body.slice(5, 7), 16);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b < 110 ? "#e8e4da" : "#1d2126";
}

/** Canonical identity seed: a participant KEY ("builtin:builder") and a bare
 * roster name ("builder") must wear the SAME face on every surface — strip
 * the source prefix before seeding (v1.171.0 coordinator, resolving the
 * cross-surface split P1's reviewer flagged). */
export function faceIdentity(key: string): string {
  const colon = key.indexOf(":");
  return colon >= 0 ? key.slice(colon + 1) : key;
}

/** True when the OS asks for reduced motion — stills blinks and scans. */
function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);
  useEffect(() => {
    // jsdom (vitest) has no matchMedia — animation stays on, harmlessly.
    if (typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia("(prefers-reduced-motion: reduce)");
    setReduced(mq.matches);
    const on = (e: MediaQueryListEvent) => setReduced(e.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, []);
  return reduced;
}

export type FaceMood = "idle" | "work" | "error" | "done";

/** Per-shape eye geometry (viewBox 0 0 48 48). */
const EYE_Y: Record<Shape, number> = {
  circle: 22,
  squircle: 22,
  pill: 24,
  triangle: 28,
  hexagon: 23,
  cloud: 26,
  drop: 27,
};

function bodyPath(shape: Shape): ReactElement {
  switch (shape) {
    case "circle":
      return <circle cx="24" cy="24" r="20" />;
    case "squircle":
      return <rect x="5" y="5" width="38" height="38" rx="13" />;
    case "pill":
      return <rect x="4" y="10" width="40" height="28" rx="14" />;
    case "triangle":
      return <path d="M24 5 L44 41 Q45 43 42 43 L6 43 Q3 43 4 41 Z" />;
    case "hexagon":
      return <path d="M24 4 L41 14 V34 L24 44 L7 34 V14 Z" />;
    case "cloud":
      return (
        <path d="M14 38 a9 9 0 0 1 -2 -17.8 A11 11 0 0 1 33.5 16 A8.5 8.5 0 0 1 34 38 Z" />
      );
    case "drop":
      return <path d="M24 4 C34 18 40 24 40 31 a16 16 0 0 1 -32 0 C8 24 14 18 24 4 Z" />;
  }
}

export default function AgentFace({
  name,
  mood = "idle",
  size = 28,
  avatarUrl,
  className = "",
  title,
}: {
  /** The agent's stable identity key (roster name / agent_type / slug). */
  name: string;
  mood?: FaceMood;
  size?: number;
  /** A real portrait always wins over the geometric face. */
  avatarUrl?: string | null;
  className?: string;
  title?: string;
}) {
  const reduced = useReducedMotion();
  const [imgBroken, setImgBroken] = useState(false);
  const seed = faceIdentity(name);
  const decorative = title === "";
  if (avatarUrl && !imgBroken) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={avatarUrl}
        alt={decorative ? "" : (title ?? seed)}
        aria-hidden={decorative || undefined}
        title={decorative ? undefined : (title ?? seed)}
        width={size}
        height={size}
        // A broken portrait falls back to the drawn face, never a broken-image
        // glyph — the identity survives a missing file.
        onError={() => setImgBroken(true)}
        className={`shrink-0 rounded-full object-cover ${className}`}
      />
    );
  }
  const body = faceColor(seed);
  const shape = faceShape(seed);
  const eyes = eyeColor(body);
  const ey = EYE_Y[shape];
  const animate = !reduced;
  // Deterministic per-face phase so a roster of faces doesn't blink in sync.
  const phase = (faceSeed(seed) % 40) / 10; // 0..3.9s
  return (
    <svg
      viewBox="0 0 48 48"
      width={size}
      height={size}
      // Decorative mode (title=""): the face sits beside its own visible name
      // — an EMPTY aria-label on role="img" is an invalid ARIA state, so the
      // face hides from the a11y tree entirely instead (P1 review).
      role={decorative ? undefined : "img"}
      aria-label={decorative ? undefined : (title ?? seed)}
      aria-hidden={decorative || undefined}
      className={`shrink-0 ${className}`}
      data-testid="agent-face"
      data-face-shape={shape}
      data-face-mood={mood}
    >
      {!decorative && <title>{title ?? seed}</title>}
      <g fill={body}>{bodyPath(shape)}</g>
      {mood === "error" ? (
        <g
          stroke={eyes}
          strokeWidth="2.4"
          strokeLinecap="round"
          data-testid="face-eyes-error"
        >
          <path d={`M15 ${ey - 3} l6 6 M21 ${ey - 3} l-6 6`} />
          <path d={`M27 ${ey - 3} l6 6 M33 ${ey - 3} l-6 6`} />
        </g>
      ) : (
        <g fill={eyes}>
          <ellipse cx="18" cy={ey} rx="3" ry="3">
            {animate && mood === "work" && (
              <animate
                attributeName="cx"
                values="16.5;19.5;16.5"
                dur="1.1s"
                repeatCount="indefinite"
              />
            )}
            {animate && mood === "idle" && (
              <animate
                attributeName="ry"
                values="3;3;0.4;3"
                keyTimes="0;0.93;0.965;1"
                dur="4.8s"
                begin={`${phase}s`}
                repeatCount="indefinite"
              />
            )}
          </ellipse>
          <ellipse cx="30" cy={ey} rx="3" ry="3">
            {animate && mood === "work" && (
              <animate
                attributeName="cx"
                values="28.5;31.5;28.5"
                dur="1.1s"
                repeatCount="indefinite"
              />
            )}
            {animate && mood === "idle" && (
              <animate
                attributeName="ry"
                values="3;3;0.4;3"
                keyTimes="0;0.93;0.965;1"
                dur="4.8s"
                begin={`${phase}s`}
                repeatCount="indefinite"
              />
            )}
          </ellipse>
          {mood === "done" && (
            <g
              stroke={eyes}
              strokeWidth="2"
              strokeLinecap="round"
              fill="none"
              data-testid="face-smile"
            >
              <path d={`M18 ${ey + 8} q6 4 12 0`} />
            </g>
          )}
        </g>
      )}
    </svg>
  );
}

/** Map a session/run status onto a face mood — ONE mapping for every surface. */
export function moodForStatus(status?: string | null): FaceMood {
  switch ((status || "").toLowerCase()) {
    case "active":
    case "running":
    case "resuming":
    case "cancelling":
      return "work";
    case "failed":
    case "error":
      return "error";
    case "completed":
      return "done";
    default:
      return "idle";
  }
}
