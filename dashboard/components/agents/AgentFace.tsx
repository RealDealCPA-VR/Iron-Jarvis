"use client";
import { useFaceStyle } from "@/components/agents/FaceStyles";

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
 *
 * CHOOSABLE SINCE v1.180.0. The seed is the DEFAULT, no longer the ceiling: an
 * optional `face` override (stored per agent by the daemon, served on the
 * roster and the agents list) replaces the seeded value FIELD BY FIELD — a set
 * shape overrides the shape, a set color the color, a set eye style the eyes,
 * and anything unset keeps deriving from the name. Precedence, top down:
 *
 *     portrait (avatarUrl)  >  override field  >  name-derived field
 *
 * A portrait still wins over everything, unchanged: a real picture is a
 * stronger identity than a chosen geometry. An override value this build does
 * not know (an older dashboard against a newer daemon) is IGNORED per field
 * and that field derives — a face that renders is always better than a face
 * that throws, and it is never a lie because the seeded face is the honest
 * default.
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

/**
 * Eye styles — a REAL NAMED SET, never a free-form string (v1.180.0).
 *
 * Each name has geometry drawn below, so the daemon can validate a chosen
 * value against exactly this list. THIS ARRAY AND `FACE_EYES` IN
 * `agents/faces.py` MUST NAME THE SAME VALUES: a value the daemon accepts but
 * this file cannot draw renders as the derived face while the picker claims
 * it is set — the one way this feature can lie.
 */
export const EYE_STYLES = [
  "round",
  "oval",
  "wide",
  "sleepy",
  "square",
  "visor",
] as const;
export type EyeStyle = (typeof EYE_STYLES)[number];

/** The shapes and colors, exported so a picker offers the real sets. */
export const FACE_SHAPES = SHAPES;
export const FACE_COLORS = COLORS;

/** A stored per-agent override. Each field is INDEPENDENT and optional;
 *  absent/null means "derive this one from the name". Matches the daemon's
 *  wire shape (`face` on /agents and /agents/roster) exactly. */
export type FaceOverride = {
  shape?: string | null;
  color?: string | null;
  eyes?: string | null;
};

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

/** Third-order seed so the eyes correlate with neither shape nor color. */
export function faceEyes(name: string): EyeStyle {
  return EYE_STYLES[Math.floor(faceSeed(name) / 53) % EYE_STYLES.length];
}

/** An override value, or null when it is unset OR not a value this build can
 *  draw. ONE gate for all three fields — see the precedence note at the top. */
function pick<T extends string>(
  value: string | null | undefined,
  allowed: readonly T[],
): T | null {
  const v = (value ?? "").trim().toLowerCase();
  return (allowed as readonly string[]).includes(v) ? (v as T) : null;
}

/** The face actually drawn: the override where it is set, the name's seed
 *  everywhere else. Exported so tests (and the picker's preview) resolve
 *  precedence through the SAME function the component draws with — two copies
 *  of this rule would drift silently. */
export function resolveFace(
  name: string,
  override?: FaceOverride | null,
): { shape: Shape; color: string; eyes: EyeStyle } {
  const seed = faceIdentity(name);
  return {
    shape: pick(override?.shape, SHAPES) ?? faceShape(seed),
    color: pick(override?.color, COLORS) ?? faceColor(seed),
    eyes: pick(override?.eyes, EYE_STYLES) ?? faceEyes(seed),
  };
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

/** Per-STYLE eye size (viewBox 0 0 48 48). `visor` is drawn separately — it is
 *  one bar across both sockets rather than a pair. */
const EYE_DIMS: Record<Exclude<EyeStyle, "visor">, { rx: number; ry: number }> = {
  round: { rx: 3, ry: 3 },
  oval: { rx: 2.2, ry: 3.8 },
  wide: { rx: 4, ry: 4 },
  sleepy: { rx: 3.6, ry: 1.4 },
  square: { rx: 2.8, ry: 2.8 },
};

/** The two eye centres — one x per socket, shared by every style. */
const EYE_X = [18, 30] as const;

/**
 * The eyes, in the chosen style, still carrying MOOD.
 *
 * Mood outranks style everywhere it matters: `error` is drawn by the caller as
 * X-X for EVERY style (a status surface must not become unreadable because the
 * user liked visors), and idle-blink / work-scan animations exist in each style
 * rather than only in the default one — a style that silently lost the scan
 * would make a running agent look idle.
 */
function eyePair({
  style,
  ey,
  body,
  animate,
  mood,
  phase,
}: {
  style: EyeStyle;
  ey: number;
  body: string;
  animate: boolean;
  mood: FaceMood;
  phase: number;
}): ReactElement {
  const blink = animate && mood === "idle";
  const scan = animate && mood === "work";
  if (style === "visor") {
    const h = 5.6;
    return (
      <>
        <rect
          x="12.5"
          y={ey - h / 2}
          width="23"
          height={h}
          rx={h / 2}
          data-testid="face-eye"
        >
          {blink && (
            <>
              <animate
                attributeName="height"
                values={`${h};${h};1;${h}`}
                keyTimes="0;0.93;0.965;1"
                dur="4.8s"
                begin={`${phase}s`}
                repeatCount="indefinite"
              />
              <animate
                attributeName="y"
                values={`${ey - h / 2};${ey - h / 2};${ey - 0.5};${ey - h / 2}`}
                keyTimes="0;0.93;0.965;1"
                dur="4.8s"
                begin={`${phase}s`}
                repeatCount="indefinite"
              />
            </>
          )}
        </rect>
        {/* The scanner: a body-coloured dot sweeping the bar, so "working"
            reads at 20px the way the paired scan does. */}
        {scan && (
          <circle cx="16" cy={ey} r="1.6" fill={body} data-testid="face-scan">
            <animate
              attributeName="cx"
              values="16;32;16"
              dur="1.3s"
              repeatCount="indefinite"
            />
          </circle>
        )}
      </>
    );
  }
  const { rx, ry } = EYE_DIMS[style];
  return (
    <>
      {EYE_X.map((cx) =>
        style === "square" ? (
          <rect
            key={cx}
            x={cx - rx}
            y={ey - ry}
            width={rx * 2}
            height={ry * 2}
            rx="0.9"
            data-testid="face-eye"
          >
            {scan && (
              <animate
                attributeName="x"
                values={`${cx - rx - 1.5};${cx - rx + 1.5};${cx - rx - 1.5}`}
                dur="1.1s"
                repeatCount="indefinite"
              />
            )}
            {blink && (
              <>
                <animate
                  attributeName="height"
                  values={`${ry * 2};${ry * 2};0.8;${ry * 2}`}
                  keyTimes="0;0.93;0.965;1"
                  dur="4.8s"
                  begin={`${phase}s`}
                  repeatCount="indefinite"
                />
                <animate
                  attributeName="y"
                  values={`${ey - ry};${ey - ry};${ey - 0.4};${ey - ry}`}
                  keyTimes="0;0.93;0.965;1"
                  dur="4.8s"
                  begin={`${phase}s`}
                  repeatCount="indefinite"
                />
              </>
            )}
          </rect>
        ) : (
          <ellipse key={cx} cx={cx} cy={ey} rx={rx} ry={ry} data-testid="face-eye">
            {scan && (
              <animate
                attributeName="cx"
                values={`${cx - 1.5};${cx + 1.5};${cx - 1.5}`}
                dur="1.1s"
                repeatCount="indefinite"
              />
            )}
            {blink && (
              <animate
                attributeName="ry"
                values={`${ry};${ry};0.4;${ry}`}
                keyTimes="0;0.93;0.965;1"
                dur="4.8s"
                begin={`${phase}s`}
                repeatCount="indefinite"
              />
            )}
          </ellipse>
        ),
      )}
    </>
  );
}

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
  face,
  className = "",
  title,
}: {
  /** The agent's stable identity key (roster name / agent_type / slug). */
  name: string;
  mood?: FaceMood;
  size?: number;
  /** A real portrait always wins over the geometric face. */
  avatarUrl?: string | null;
  /** The user's stored choice — per field, absent means "derive from the
   *  name" (v1.180.0). Absent entirely = the pre-v1.180.0 behaviour exactly. */
  face?: FaceOverride | null;
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
  // Override where set, seed everywhere else — ONE resolver (v1.180.0).
  // An explicit prop wins (the picker previews a DRAFT, not the saved
  // value); otherwise the stored override arrives from the provider, so a
  // chosen face reaches every surface that draws one — not only the control
  // it was chosen in. No provider, or an older daemon: `stored` is null and
  // this is byte-identical to the derived face that shipped before.
  const stored = useFaceStyle(seed);
  const { shape, color: body, eyes: eyeStyle } = resolveFace(seed, face ?? stored);
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
      // The resolved values, so a surface passing the wrong seed OR dropping
      // the override is catchable from the DOM (v1.180.0).
      data-face-eyes={eyeStyle}
      data-face-color={body}
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
          {eyePair({ style: eyeStyle, ey, body, animate, mood, phase })}
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
