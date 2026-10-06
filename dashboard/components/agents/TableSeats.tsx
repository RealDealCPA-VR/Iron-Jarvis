"use client";

/**
 * TableSeats — the panel as SEATS AT A REAL TABLE (v1.304.0).
 *
 * The user asked for the agents' faces to be "more prominent", and chose the
 * shape: every participant sits at an elliptical table as a large portrait
 * (72px) with a live STATUS RING, and whoever is speaking LIFTS FORWARD with a
 * glow and a "speaking…" caption. A seat is a door: clicking it opens the
 * agent in full (a hero portrait) — the caller decides what that opens.
 *
 * WHAT THE RING SAYS, AND WHERE IT COMES FROM. Nothing on a seat is invented.
 * Two sources, both already on screen elsewhere:
 *   * the ROSTER row (the page's GET /agents/roster): paused, an offline
 *     remote, the daemon's liveness word, and the v1.296.0 health record
 *     (running / blocked / the last outcome);
 *   * the ROOM itself: the round in flight (who is speaking now), the
 *     participant's newest transcript line (an honest error, a remote's
 *     inbound "question" — which IS waiting on you — or a pending "working,
 *     will report back").
 * Precedence, top down (`seatStatus`): paused > speaking > error > waiting on
 * you > working > idle. A paused agent never speaks (the round answers the
 * pause sentence), and "waiting on you" outranks a background "working"
 * because it is the one that needs the person looking at the screen.
 *
 * FAST BY CONSTRUCTION. RoundTable re-renders on every keystroke in its
 * composer (its input is local state), and on every live refetch mid-round.
 * The seats must not: `TableSeats` and each `SeatView` are memoized, the seat
 * list is built in a `useMemo` keyed on the transcript/round/roster (never
 * the composer), the click handler is a stable callback, and the speaker is a
 * plain string. Typing a paragraph re-renders zero seats — pinned in
 * `__tests__/agent-faces-v1304.test.tsx`.
 *
 * LAYOUT. Up to six seats ring the table, leaving the near side (the bottom,
 * where the person looking at it sits) open; a seventh and beyond wrap into an
 * outer ring, interleaved. When the CONTAINER (not the viewport — the room
 * sits beside a rail and possibly a tab panel) is narrower than
 * `STRIP_BELOW_PX`, the table gives way to a horizontal strip of 56px seats,
 * because an ellipse of 72px seats in 360px is a pile. Exactly ONE of the two
 * is in the DOM at a time (decided in JS, not by CSS display toggles), so a
 * screen reader — and a test — never meets every agent twice.
 *
 * MOTION. The lift is CSS (transform + transition), no framer: the speaker
 * scales and rises, the ring glows, a halo pulses. Under
 * `prefers-reduced-motion` the seat does not move or pulse at all — the glow
 * and the "speaking…" caption alone carry the state, which is the point of
 * the preference (the same reading AgentFace uses to still its blinks).
 *
 * This file also owns the small PURE roster helpers the seats need
 * (`bareName`, `rosterAvatarSrc`, `livenessOf`). They used to live in
 * RosterStrip, which imports the framer-backed `Reveal`; the room must not
 * drag animation code in to read a word off a row, so they moved here and
 * RosterStrip re-exports them under the same names.
 */

import { memo, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { API_BASE, ijToken } from "@/lib/api";
import AgentFace, { type FaceOverride, useReducedMotion } from "./AgentFace";
import type { Participant, ThreadEntry } from "./identity";
import type { RosterEntry } from "./RosterStrip";

/* ------------------------------------------------------ roster helpers --- */

/** The shown name: the bare slug — the kind pill carries provenance, so the
 *  wire prefixes ("custom:", "remote:") stay off the screen. */
export function bareName(name: string): string {
  if (name.startsWith("custom:")) return name.slice("custom:".length);
  if (name.startsWith("remote:")) return name.slice("remote:".length);
  return name;
}

/** <img> can't send the Authorization header — the token rides as ?token=,
 *  the same pattern every media surface uses (creative gallery, previews).
 *  `cacheKey` (the row's last_active — SetupCard's `rev` idea at low
 *  resolution) busts the browser cache after a portrait is replaced, so the
 *  roster never keeps rendering a stale image the daemon no longer serves. */
export function rosterAvatarSrc(rel: string, cacheKey?: string | null): string {
  const token = ijToken();
  const v = encodeURIComponent(cacheKey || "0");
  return `${API_BASE}${rel}?v=${v}${token ? `&token=${encodeURIComponent(token)}` : ""}`;
}

export type Liveness = "busy" | "queued";
const LIVE_STATES = new Set<string>(["busy", "queued"]);

/** Is this agent working right now? "busy" | "queued" | null (v1.193.0).
 *
 *  TWO SOURCES, ONE MEANING, both the daemon's own word — never inferred from
 *  anything else the UI happens to know:
 *    1. `activity`, the roster field itself;
 *    2. the liveness prefix the daemon already bakes into `line`'s suffix,
 *       which is what a daemon whose /agents/roster serializer does not forward
 *       `activity` still sends today.
 *  An OFFLINE remote reports nothing: the daemon's own `_suffix()` drops
 *  liveness for an unhealthy entry, the row already shows the more urgent
 *  offline pill, and "busy" about an unreachable box is noise.
 *  null is NOT "free" — it is "no claim" (idle, unknown, and every delegated
 *  child, which this signal structurally cannot see). Nothing renders for it. */
export function livenessOf(e: RosterEntry): Liveness | null {
  if (!e.healthy) return null;
  const direct = String(e.activity ?? "").trim().toLowerCase();
  if (LIVE_STATES.has(direct)) return direct as Liveness;
  const paren = /\(([^()]+)\)\s*$/.exec(e.line ?? "")?.[1] ?? "";
  const head = paren.split(",")[0]?.trim().toLowerCase() ?? "";
  // The comma is required: the daemon only ever prefixes liveness ONTO a stats
  // phrase, so a bare "(queued)" from anywhere else is not this signal.
  if (paren.includes(",") && LIVE_STATES.has(head)) return head as Liveness;
  return null;
}

/** participantKey ("<source>:<name>") for a roster row — the join between a
 *  thread's seat and the roster's row for the same agent. */
export function rosterKey(e: Pick<RosterEntry, "kind" | "name">): string {
  return `${e.kind}:${bareName(e.name)}`;
}

/* ------------------------------------------------------------- status --- */

export type SeatStatus = "idle" | "working" | "waiting" | "paused" | "error";

/** Where the participant is in the round in flight, if any. */
export type SeatPhase = "speaking" | "answered" | "next" | null;

/** The words a ring means — the seat's accessible name and tooltip say them,
 *  because a colour alone reaches nobody using a screen reader. */
export const SEAT_STATUS_LABEL: Record<SeatStatus, string> = {
  idle: "idle",
  working: "working",
  waiting: "waiting on you",
  paused: "paused",
  error: "error",
};

/** What the ROSTER alone says about an agent — the status a seat shows when
 *  the room has nothing newer to add. Also the RosterStrip card's ring, so the
 *  rail and the table can never disagree about the same row. */
export function rosterStatus(e: RosterEntry | null | undefined): {
  status: SeatStatus;
  note: string;
} {
  if (!e) return { status: "idle", note: "" };
  if (e.paused)
    return { status: "paused", note: e.pause_reason ? `paused — ${e.pause_reason}` : "paused" };
  if (e.kind === "remote" && e.healthy === false)
    return { status: "error", note: "offline — the other computer did not answer" };
  const h = e.health ?? null;
  if (h && (h.blocked > 0 || h.last_outcome === "needs_you"))
    return {
      status: "waiting",
      note:
        h.blocked > 0
          ? `${h.blocked} job${h.blocked === 1 ? "" : "s"} blocked — waiting on you`
          : "its last job is waiting on you",
    };
  if (livenessOf(e) === "busy" || (h?.running ?? 0) > 0)
    return { status: "working", note: "running a job right now" };
  if (h?.last_outcome === "failed")
    return {
      status: "error",
      note: h.last_error ? `last job failed — ${h.last_error}` : "its last job failed",
    };
  return { status: "idle", note: "" };
}

/** One seat's status: the roster's reading, then the room's newer facts on
 *  top, by the precedence in the header. `latest` is the participant's
 *  newest transcript line (or undefined). */
export function seatStatus(
  entry: RosterEntry | null | undefined,
  phase: SeatPhase,
  latest: ThreadEntry | undefined,
): { status: SeatStatus; note: string } {
  const base = rosterStatus(entry);
  if (base.status === "paused") return base;
  if (phase === "speaking") return { status: "working", note: "speaking now" };
  if (latest?.error) return { status: "error", note: latest.error };
  if (base.status === "error") return base;
  if (latest?.inbound && latest.kind === "question")
    return { status: "waiting", note: "asked you a question" };
  if (base.status === "waiting") return base;
  if (latest?.pending) return { status: "working", note: "working — will report back" };
  return base;
}

/* -------------------------------------------------------------- seats --- */

/** Everything one seat draws, resolved once by the caller. */
export interface Seat {
  /** participant key "<source>:<name>" — stable id. */
  key: string;
  /** The BARE name — the face seed and the visible label. */
  name: string;
  /** The panel role (or "") — shown under the name when nothing live is. */
  role: string;
  /** Token-signed portrait URL when one is stored, else null (drawn face). */
  avatarUrl: string | null;
  status: SeatStatus;
  /** Why the ring says what it says (tooltip). */
  note: string;
  phase: SeatPhase;
  /** A roster row exists, so the seat can open the agent in full. */
  openable: boolean;
}

/** A remote seat's tooltip in a project room (v1.304.0). */
export const REMOTE_PROJECT_NOTE = "sees only your messages";

/** Build the seats from what RoundTable already holds. Pure, so the caller
 *  can memoize it and a test can pin the mapping without rendering. */
export function buildSeats({
  participants,
  rosterByKey,
  messages,
  speakingKey,
  roundKeys,
  answeredKeys,
  projectRoom = false,
}: {
  participants: Participant[];
  rosterByKey: Map<string, RosterEntry>;
  messages: ThreadEntry[];
  speakingKey: string | null;
  /** Who this round expects to speak (empty = no round in flight). */
  roundKeys: string[];
  answeredKeys: string[];
  /** v1.304.0: a PROJECT room. A remote seat there is shown only the user's
   *  messages (never the other seats' replies, which carry project
   *  material), and its tooltip says so. */
  projectRoom?: boolean;
}): Seat[] {
  // Newest line per participant, one pass from the end.
  const latest = new Map<string, ThreadEntry>();
  for (let i = messages.length - 1; i >= 0; i--) {
    const m = messages[i];
    if (m.who !== "user" && !latest.has(m.who)) latest.set(m.who, m);
  }
  return participants.map((p) => {
    const entry = rosterByKey.get(p.key) ?? null;
    const phase: SeatPhase =
      p.key === speakingKey
        ? "speaking"
        : answeredKeys.includes(p.key)
          ? "answered"
          : roundKeys.includes(p.key)
            ? "next"
            : null;
    const read = seatStatus(entry, phase, latest.get(p.key));
    const status = read.status;
    const note =
      projectRoom && p.source === "remote"
        ? [read.note, REMOTE_PROJECT_NOTE].filter(Boolean).join(" · ")
        : read.note;
    return {
      key: p.key,
      name: p.name,
      role: p.role ?? "",
      // The chosen face (shape/colour/eyes) is NOT copied here: AgentFace
      // reads it from the app-wide FaceStylesProvider, which is the one that
      // sees a Reset at once (a roster row keeps the old override until the
      // next refetch — the v1.180.0 lesson).
      avatarUrl: entry?.avatar ? rosterAvatarSrc(entry.avatar, entry.last_active) : null,
      status,
      note,
      phase,
      openable: entry !== null,
    };
  });
}

/* ---------------------------------------------------------- the ring --- */

/** Ring colour per status. `white`/`zinc` invert under the light Marks
 *  (globals.css), and the accent is the theme's own variable, so one class
 *  set serves every theme. */
const RING: Record<SeatStatus, string> = {
  idle: "ring-white/20",
  working: "ring-accent",
  waiting: "ring-amber-400",
  paused: "ring-zinc-500",
  error: "ring-rose-500",
};

/** A portrait inside its status ring — shared by the seats and the
 *  RosterStrip cards so the two draw the same reading the same way. */
export function StatusRing({
  name,
  size,
  status,
  avatarUrl,
  face,
  glow = false,
  className = "",
}: {
  name: string;
  size: number;
  status: SeatStatus;
  avatarUrl?: string | null;
  face?: FaceOverride | null;
  /** The speaker's glow (the seat decides; the ring only draws it). */
  glow?: boolean;
  className?: string;
}) {
  const thick = size >= 48 ? "ring-[3px]" : "ring-2";
  return (
    <span
      data-testid="status-ring"
      data-status={status}
      className={`relative grid shrink-0 place-items-center rounded-full bg-ink-900 ring-offset-2 ring-offset-ink-850 ${thick} ${RING[status]} ${className}`}
      style={{
        width: size,
        height: size,
        boxShadow: glow
          ? "0 0 0 1px rgb(var(--accent-rgb) / 0.35), 0 0 28px 2px rgb(var(--accent-rgb) / 0.55)"
          : undefined,
      }}
    >
      {/* Decorative: the seat's own label (and accessible name) carries the
          identity, so a second SVG <title> would read the name twice. */}
      <span aria-hidden="true" className="contents">
        <AgentFace
          name={name}
          title=""
          size={size}
          mood={status === "error" ? "error" : status === "working" ? "work" : "idle"}
          avatarUrl={avatarUrl ?? undefined}
          face={face ?? null}
          className={status === "paused" ? "opacity-60 grayscale" : ""}
        />
      </span>
    </span>
  );
}

/* ---------------------------------------------------------- one seat --- */

export const SEAT_PX = 72;
export const STRIP_SEAT_PX = 56;
/** Below this container width the table becomes a strip. */
export const STRIP_BELOW_PX = 520;
/** Seats on the inner ring before the outer one starts. */
export const INNER_RING_MAX = 6;

function caption(seat: Seat): { text: string; tone: string } {
  if (seat.phase === "speaking") return { text: "speaking…", tone: "text-accent-soft" };
  if (seat.phase === "answered") return { text: "answered", tone: "text-emerald-300/90" };
  if (seat.phase === "next") return { text: "up next", tone: "text-zinc-500" };
  if (seat.status === "waiting") return { text: "waiting on you", tone: "text-amber-300" };
  if (seat.status === "paused") return { text: "paused", tone: "text-zinc-500" };
  if (seat.status === "error") return { text: "error", tone: "text-rose-300" };
  if (seat.status === "working") return { text: "working", tone: "text-accent-soft/90" };
  // The role only when it SAYS something: a builtin seated as itself
  // ("reviewer" as reviewer) would print its name twice. Else the status word.
  const role = seat.role.trim();
  if (role && role.toLowerCase() !== seat.name.toLowerCase())
    return { text: role, tone: "text-zinc-500" };
  return { text: SEAT_STATUS_LABEL[seat.status], tone: "text-zinc-600" };
}

const SeatView = memo(function SeatView({
  seat,
  size,
  reduced,
  onOpen,
  anchor,
}: {
  seat: Seat;
  size: number;
  reduced: boolean;
  onOpen?: (key: string) => void;
  /** At the table: hang off a zero-size anchor at the portrait's centre,
   *  the name below the portrait ("below") or above it ("above", the far
   *  side). Absent: a plain box in a row (the strip). */
  anchor?: "below" | "above";
}) {
  const speaking = seat.phase === "speaking";
  const above = anchor === "above";
  const cap = caption(seat);
  const label = `${seat.name}${seat.role ? ` — ${seat.role}` : ""}, ${
    speaking ? "speaking" : SEAT_STATUS_LABEL[seat.status]
  }`;
  // THE LIFT. Full motion: rise + scale with a transition. Reduced: nothing
  // moves — the glow and the caption carry it on their own.
  const lift = speaking
    ? reduced
      ? ""
      : "-translate-y-1.5 scale-110 transition-transform duration-300 ease-out"
    : reduced
      ? ""
      : "transition-transform duration-300 ease-out";
  const body = (
    <>
      <span className="relative">
        {speaking && !reduced && (
          // The halo: a pulsing accent ring BEHIND the portrait.
          <span
            aria-hidden="true"
            data-testid="seat-halo"
            className="pointer-events-none absolute -inset-2 rounded-full motion-safe:animate-pulse-glow"
          />
        )}
        <StatusRing
          name={seat.name}
          size={size}
          status={seat.status}
          avatarUrl={seat.avatarUrl}
          glow={speaking}
        />
      </span>
      <span
        className={`${above ? "mb-1.5" : "mt-2"} block max-w-[7.5rem] truncate text-[12px] font-semibold leading-tight ${
          speaking ? "text-zinc-100" : "text-zinc-300"
        }`}
      >
        {seat.name}
      </span>
      {cap.text && (
        <span
          data-testid="seat-caption"
          className={`block max-w-[7.5rem] truncate text-[10.5px] leading-tight ${cap.tone}`}
        >
          {cap.text}
        </span>
      )}
    </>
  );
  const common = {
    "data-testid": `seat-${seat.key}`,
    "data-seat-status": seat.status,
    "data-speaking": speaking ? "true" : undefined,
    "data-phase": seat.phase ?? undefined,
    "data-motion": reduced ? "reduced" : "full",
    // A tooltip only when there is something to say beyond the visible name
    // (a bare-name title would be a second copy of the label under the cursor).
    title: seat.note ? `${seat.name} — ${seat.note}` : undefined,
    "data-anchor": anchor,
    className: `flex w-[7.5rem] ${above ? "flex-col-reverse" : "flex-col"} items-center rounded-2xl p-1 text-center ${
      anchor ? "absolute" : ""
    } ${lift} ${speaking ? "z-10" : ""}`,
    style: anchor
      ? above
        ? { left: -SEAT_HALF_W, bottom: -SEAT_HALF }
        : { left: -SEAT_HALF_W, top: -SEAT_HALF }
      : undefined,
  } as const;
  if (onOpen && seat.openable) {
    return (
      <button
        type="button"
        aria-label={`${label} — open ${seat.name}`}
        onClick={() => onOpen(seat.key)}
        {...common}
        className={`${common.className} outline-none focus-visible:ring-2 focus-visible:ring-accent/60`}
      >
        {body}
      </button>
    );
  }
  return (
    <div role="group" aria-label={label} {...common}>
      {body}
    </div>
  );
});

/* -------------------------------------------------------- the layout --- */

/** Seat angle on the arc: the near side (bottom) is left open for the person
 *  at the screen. Degrees, screen coordinates (y down, 90 = bottom). Seats are
 *  spread EVENLY by angle over the 280 degrees that face the open side. */
export function seatAngle(i: number, n: number, offset = 0.5): number {
  const ARC_START = 130;
  const ARC_SPAN = 280;
  return ARC_START + ((i + offset) * ARC_SPAN) / Math.max(1, n);
}

/** The width assumed before the container has been measured (jsdom, first
 *  paint). Every number below is derived from ONE width, so the table and the
 *  seats always agree with each other. */
export const DEFAULT_TABLE_WIDTH = 720;
/** The table's share of the room's width (about 68%), narrowed only as far as
 *  the side seats need to stay inside the room. */
export const TABLE_WIDTH_FRAC = 0.68;
/** Half the table's height: a flat ellipse, the table seen at an angle. */
const TABLE_RY = 56;
/** How far beyond the rim a seat's portrait centre sits (x, y): close
 *  enough that the seat is visibly AT the table, far enough that the 72px
 *  portrait only overlaps the rim, never the middle. */
const SEAT_GAP_X = 50;
const SEAT_GAP_Y = 38;
/** The second ring (seven or more seats): every other seat one step further
 *  out, so neighbours stagger instead of colliding. */
const OUTER_STEP = 54;
/** Name + caption under (or above) a portrait. */
const CAPTION_PX = 32;
/** Half the seat box: p-1 (4px) + half the 72px portrait. */
const SEAT_HALF = SEAT_PX / 2 + 4;
/** Half the seat box's WIDTH (7.5rem). */
const SEAT_HALF_W = 60;
const PAD = 6;

export interface SeatPoint {
  /** Portrait centre: px from the table's centre (dx) / the room's top (y). */
  dx: number;
  y: number;
  /** The name sits ABOVE the portrait (seats on the far side), so it never
   *  lies across the table. */
  above: boolean;
  ring: 0 | 1;
}

export interface TableLayout {
  width: number;
  height: number;
  /** The table ellipse: centre (width/2, cy) and radii. */
  cy: number;
  rx: number;
  ry: number;
  seats: SeatPoint[];
}

/** WHERE EVERYTHING GOES. Pure, so a test can prove the seats sit AT the
 *  table (outside its rim, within a band of it) and inside the room, for any
 *  count and width. The height is exactly what the seats need: one ring stays
 *  under 300px, two under 390px, so the conversation starts in the first
 *  viewport. */
export function seatLayout(n: number, width: number | null): TableLayout {
  const W = width && width > 0 ? width : DEFAULT_TABLE_WIDTH;
  const two = n > INNER_RING_MAX;
  const reach = SEAT_GAP_X + (two ? OUTER_STEP : 0) + SEAT_HALF_W + PAD;
  const rx = Math.max(80, Math.min((W * TABLE_WIDTH_FRAC) / 2, W / 2 - reach));
  const ry = TABLE_RY;
  const pts = Array.from({ length: n }, (_, i) => {
    const a = (seatAngle(i, n) * Math.PI) / 180;
    const ring: 0 | 1 = two && i % 2 === 1 ? 1 : 0;
    const extra = ring * OUTER_STEP;
    const dx = (rx + SEAT_GAP_X + extra) * Math.cos(a);
    const dy = (ry + SEAT_GAP_Y + extra) * Math.sin(a);
    return { dx, dy, above: Math.sin(a) < -0.25, ring };
  });
  let top = -ry;
  let bottom = ry;
  for (const p of pts) {
    top = Math.min(top, p.dy - SEAT_HALF - (p.above ? CAPTION_PX : 0));
    bottom = Math.max(bottom, p.dy + SEAT_HALF + (p.above ? 0 : CAPTION_PX));
  }
  const cy = Math.ceil(PAD - top);
  const height = Math.ceil(cy + bottom + PAD);
  return {
    width: W,
    height,
    cy,
    rx,
    ry,
    seats: pts.map((p) => ({ dx: p.dx, y: cy + p.dy, above: p.above, ring: p.ring })),
  };
}

/** Container width, read with a ResizeObserver: the room's column, not the
 *  window, decides whether a table fits. No observer (jsdom, an old engine):
 *  null, and the caller keeps the table at the default width. */
function useContainerWidth(): [RefObject<HTMLDivElement | null>, number | null] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState<number | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver !== "function") return;
    const ro = new ResizeObserver((entries) => {
      const w = entries[0]?.contentRect?.width;
      // Rounded to 8px so sub-pixel jitter does not re-render the seats.
      if (typeof w === "number" && w > 0) setWidth(Math.round(w / 8) * 8);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

export const TableSeats = memo(function TableSeats({
  seats,
  onOpen,
  layout = "auto",
  live = false,
}: {
  seats: Seat[];
  /** A seat was clicked (participant key). Absent = seats are not buttons. */
  onOpen?: (key: string) => void;
  /** "auto" measures the container; "table"/"strip" force one (tests, or a
   *  caller that already knows its width). */
  layout?: "auto" | "table" | "strip";
  /** A round is in flight: the table's core brightens. */
  live?: boolean;
}) {
  const reduced = useReducedMotion();
  const [ref, width] = useContainerWidth();
  const mode =
    layout === "auto" ? (width !== null && width < STRIP_BELOW_PX ? "strip" : "table") : layout;
  const geo = useMemo(() => seatLayout(seats.length, width), [seats.length, width]);
  if (seats.length === 0) return null;

  if (mode === "strip") {
    return (
      <div
        ref={ref}
        data-testid="table-seats"
        data-layout="strip"
        role="list"
        aria-label="Who sits at this table"
        className="flex gap-1 overflow-x-auto border-b hairline px-3 pb-3 pt-4"
      >
        {seats.map((s) => (
          <div role="listitem" key={s.key} className="shrink-0">
            <SeatView seat={s} size={STRIP_SEAT_PX} reduced={reduced} onOpen={onOpen} />
          </div>
        ))}
      </div>
    );
  }

  return (
    <div
      ref={ref}
      data-testid="table-seats"
      data-layout="table"
      data-rings={seats.length > INNER_RING_MAX ? 2 : 1}
      role="list"
      aria-label="Who sits at this table"
      className="relative border-b hairline"
      style={{ height: geo.height }}
    >
      {/* THE TABLE: a SOLID elliptical surface. Ink body, a faint sheen on
          the far half, a darker rim, the arc-reactor accent glowing up from
          the middle, and a core that lights while a round runs. Rim and sheen
          are black/white alpha over the theme's own ink, so the light Marks
          get a pale table with a soft grey edge. Decorative only: everything
          it means is said by the seats. */}
      <div
        aria-hidden="true"
        data-testid="round-table-surface"
        data-live={live ? "true" : undefined}
        className="pointer-events-none absolute rounded-[50%] border border-accent/30 bg-ink-800"
        style={{
          left: `calc(50% - ${geo.rx}px)`,
          top: geo.cy - geo.ry,
          width: geo.rx * 2,
          height: geo.ry * 2,
          backgroundImage: [
            "radial-gradient(ellipse 60% 45% at 50% 30%, rgb(255 255 255 / 0.07), transparent 70%)",
            `radial-gradient(ellipse at center, rgb(var(--accent-rgb) / ${
              live ? 0.24 : 0.14
            }), rgb(var(--accent-rgb) / 0.04) 55%, transparent 78%)`,
          ].join(", "),
          boxShadow: [
            "inset 0 0 0 5px rgb(0 0 0 / 0.16)",
            "inset 0 -12px 26px rgb(0 0 0 / 0.32)",
            "0 16px 34px -14px rgb(0 0 0 / 0.65)",
            live
              ? "0 0 70px -14px rgb(var(--accent-rgb) / 0.6)"
              : "0 0 46px -22px rgb(var(--accent-rgb) / 0.45)",
          ].join(", "),
        }}
      >
        <div className="absolute inset-[24%] rounded-[50%] border border-accent/20" />
        <div
          className={`absolute left-1/2 top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ${
            live ? "bg-accent shadow-glow motion-safe:animate-pulse-glow" : "bg-accent/50"
          }`}
        />
      </div>
      {seats.map((s, i) => {
        const p = geo.seats[i];
        return (
          // A zero-size ANCHOR at the portrait's centre; the seat hangs off
          // it (name below, or above for the far side).
          <div
            role="listitem"
            key={s.key}
            data-seat-anchor={s.key}
            data-ring={p.ring}
            className="absolute h-0 w-0"
            style={{ left: `calc(50% + ${p.dx.toFixed(1)}px)`, top: Math.round(p.y) }}
          >
            <SeatView
              seat={s}
              size={SEAT_PX}
              reduced={reduced}
              onOpen={onOpen}
              anchor={p.above ? "above" : "below"}
            />
          </div>
        );
      })}
    </div>
  );
});

export default TableSeats;
