"use client";

/**
 * EVERYTHING › STATUS — THE SYSTEM MAP (v1.334.0).
 *
 * The Directory is the list of doors. This is the picture: every part of Iron
 * Jarvis as a star around the reactor, grouped in the Directory's five
 * constellations, each one saying at a glance whether it is SET UP, USED this
 * week, or IN USE right now. The user asked for something you open "just to
 * look at it", so it carries no lists, no forms and no admin — one picture,
 * the day's heartbeat under it, and a hover card per star.
 *
 * Encoding (the legend under the map says the same in words):
 *   not set up  — a small dashed outline, its label dim
 *   set up      — a ringed star, a faint line to the core
 *   used        — the star and its halo grow with this week's use
 *   in use now  — rings ripple out and light flows along its line
 *   attention   — amber, with the reason in its card
 *
 * Data: ONE read, GET /ui/system-map (daemon/routes/ui.py), polled slowly and
 * only while the page is visible (usePolledApi already pauses when hidden).
 * Every animation is CSS on transform/opacity/dash offset, so the compositor
 * runs it; `prefers-reduced-motion` stops all of it.
 */

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { useRouter } from "next/navigation";
import { Circle, type LucideIcon } from "lucide-react";
import { MoodOrb } from "@/components/MoodOrb";
import { surfaceFor } from "@/lib/surfaces";
import { recordOpen } from "@/lib/appTiles";

export type MapNode = {
  key: string;
  label: string;
  href: string;
  /** How many exist / are configured (0 = not set up). */
  setup: number;
  /** Uses in the last 7 days (runs, fires, messages…). */
  used: number;
  /** Running / active right now. */
  live: number;
  /** A plain reason when something here needs the user. */
  attention?: string | null;
  /** One plain line for the hover card. */
  detail?: string;
  /** The word for one `setup` unit ("server", "schedule"…), for the card. */
  unit?: string;
};

export type MapArea = { key: string; label: string; nodes: MapNode[] };

export type SystemMapData = {
  areas: MapArea[];
  /** Events per hour, oldest first, the last entry being the current hour. */
  heartbeat?: number[];
  vitals?: {
    disk_free?: number;
    disk_total?: number;
    memory_mb?: number;
    runs_7d?: number;
    runs_ok_7d?: number;
    runs_done_7d?: number;
  };
};

/** The order the constellations sit in, clockwise from the top. */
export const AREA_ORDER = ["work", "automations", "system", "knowledge", "setup"] as const;

/** Each constellation's colour — theme variables, so every palette works. */
export const AREA_COLOR: Record<string, string> = {
  work: "var(--accent-rgb)",
  automations: "var(--sm-automations)",
  system: "var(--sm-system)",
  knowledge: "var(--sm-knowledge)",
  setup: "var(--sm-setup)",
};
const WARN = "var(--tone-warn)";

export type NodeState = "off" | "setup" | "used" | "live";

export function nodeState(n: MapNode): NodeState {
  if (n.live > 0) return "live";
  if (n.used > 0) return "used";
  if (n.setup > 0) return "setup";
  return "off";
}

/** Star radius: grows with use, on a log scale so one busy part does not
 *  swallow the map. */
export function nodeRadius(n: MapNode, scale = 1): number {
  const s = nodeState(n);
  if (s === "off") return 5.5 * scale;
  const base = 9;
  const growth = Math.min(8, 2.1 * Math.log2(1 + Math.max(0, n.used)));
  return (base + growth) * scale;
}

/** The short second line under a star's name. */
export function nodeLine(n: MapNode): string {
  if (n.attention) return "needs you";
  if (n.live > 0) return `${n.live} in use now`;
  if (n.used > 0) return `${compact(n.used)} this week`;
  if (n.setup > 0) return n.unit ? `${n.setup} ${plural(n.unit, n.setup)}` : "set up";
  return "not set up";
}

function plural(unit: string, n: number): string {
  if (n === 1) return unit;
  if (unit.endsWith("y") && !/[aeiou]y$/.test(unit)) return `${unit.slice(0, -1)}ies`;
  if (/(x|ch|sh)$/.test(unit)) return `${unit}es`;
  if (unit.endsWith("s")) return unit;
  return `${unit}s`;
}

function compact(n: number): string {
  if (n >= 10000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(n);
}

export type Placed = {
  node: MapNode;
  area: string;
  x: number;
  y: number;
  r: number;
  /** Unit vector pointing away from the core (label direction). */
  ux: number;
  uy: number;
};

export type Layout = {
  w: number;
  h: number;
  cx: number;
  cy: number;
  rx: number;
  ry: number;
  core: number;
  placed: Placed[];
  arcs: { area: string; label: string; a0: number; a1: number; mid: number; setUp: number; total: number }[];
};

/**
 * Where every star goes. Pure (pinned by tests): the constellations share the
 * ellipse in proportion to their size with a gap between them; where stars
 * sit side by side (the top and bottom of the ring) every other one steps in,
 * so neighbouring names do not overlap.
 */
export function layoutMap(areas: MapArea[], w: number, h: number): Layout {
  const narrow = w < 640;
  const padX = narrow ? 58 : 118;
  const padY = narrow ? 46 : 52;
  const cx = w / 2;
  const cy = h / 2;
  const rx = Math.max(60, w / 2 - padX);
  const ry = Math.max(60, h / 2 - padY);
  const core = Math.max(36, Math.min(rx, ry) * (narrow ? 0.3 : 0.34));
  const ordered = [...areas].sort(
    (a, b) => idx(AREA_ORDER as readonly string[], a.key) - idx(AREA_ORDER as readonly string[], b.key),
  );
  const GAP = 1.4;
  const units = ordered.reduce((sum, a) => sum + a.nodes.length, 0) + GAP * ordered.length;
  const step = (Math.PI * 2) / Math.max(1, units);
  let t = -Math.PI / 2 + (GAP / 2) * step;
  const placed: Placed[] = [];
  const arcs: Layout["arcs"] = [];
  const scale = narrow ? 0.78 : 1;
  for (const area of ordered) {
    const a0 = t;
    area.nodes.forEach((node, j) => {
      const a = t + step * 0.5;
      const sin = Math.sin(a);
      const cos = Math.cos(a);
      // Side by side where the ring runs horizontally: alternate in and out.
      const crowded = Math.abs(sin) > (narrow ? 0.35 : 0.55);
      const k = crowded && j % 2 === 1 ? (narrow ? 0.76 : 0.84) : 1;
      const x = cx + rx * k * cos;
      const y = cy + ry * k * sin;
      // Outward direction is the ellipse normal, not the radius.
      const nx = cos / rx;
      const ny = sin / ry;
      const len = Math.hypot(nx, ny) || 1;
      placed.push({ node, area: area.key, x, y, r: nodeRadius(node, scale), ux: nx / len, uy: ny / len });
      t += step;
    });
    arcs.push({
      area: area.key,
      label: area.label,
      a0,
      a1: t,
      mid: (a0 + t) / 2,
      setUp: area.nodes.filter((n) => nodeState(n) !== "off").length,
      total: area.nodes.length,
    });
    t += GAP * step;
  }
  return { w, h, cx, cy, rx, ry, core, placed, arcs };
}

function idx(list: readonly string[], key: string): number {
  const i = list.indexOf(key);
  return i === -1 ? list.length : i;
}

/** A soft curve from a star to the core (bent a little, like a field line). */
function linkPath(p: Placed, l: Layout): string {
  const dx = l.cx - p.x;
  const dy = l.cy - p.y;
  const d = Math.hypot(dx, dy) || 1;
  // End on the core's rim, start on the star's rim.
  const sx = p.x + (dx / d) * p.r;
  const sy = p.y + (dy / d) * p.r;
  const ex = l.cx - (dx / d) * (l.core * 0.92);
  const ey = l.cy - (dy / d) * (l.core * 0.92);
  const mx = (sx + ex) / 2 - (dy / d) * d * 0.12;
  const my = (sy + ey) / 2 + (dx / d) * d * 0.12;
  return `M${sx.toFixed(1)},${sy.toFixed(1)} Q${mx.toFixed(1)},${my.toFixed(1)} ${ex.toFixed(1)},${ey.toFixed(1)}`;
}

function arcPath(l: Layout, a0: number, a1: number, k: number): string {
  const pts: string[] = [];
  const n = Math.max(6, Math.round((a1 - a0) * 24));
  for (let i = 0; i <= n; i += 1) {
    const a = a0 + ((a1 - a0) * i) / n;
    const x = l.cx + l.rx * k * Math.cos(a);
    const y = l.cy + l.ry * k * Math.sin(a);
    pts.push(`${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`);
  }
  return pts.join(" ");
}

/** A fixed, seeded starfield: the same sky on every render. */
function starfield(w: number, h: number, count: number): { x: number; y: number; r: number; o: number }[] {
  let seed = 7;
  const rnd = () => {
    seed = (seed * 16807) % 2147483647;
    return seed / 2147483647;
  };
  return Array.from({ length: count }, () => ({
    x: rnd() * w,
    y: rnd() * h,
    r: rnd() * 0.9 + 0.25,
    o: rnd() * 0.35 + 0.08,
  }));
}

function iconFor(href: string): LucideIcon {
  return (surfaceFor(href)?.icon as LucideIcon | undefined) ?? Circle;
}

function rgb(color: string, alpha: number): string {
  return `rgb(${color} / ${alpha})`;
}

/** Measures the element's width (ResizeObserver; a fixed width in tests). */
function useWidth<T extends HTMLElement>(fallback: number): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [w, setW] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => {
      const next = Math.round(el.getBoundingClientRect().width);
      if (next > 0) setW((prev) => (Math.abs(prev - next) > 2 ? next : prev));
    };
    read();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w];
}

export function mapHeight(w: number): number {
  if (w < 640) return Math.round(Math.min(940, w * 1.85));
  if (w < 960) return Math.round(w * 0.78);
  return Math.round(Math.min(700, Math.max(520, w * 0.56)));
}

export function SystemMap({ data, loading }: { data: SystemMapData | null; loading?: boolean }) {
  const router = useRouter();
  const [wrapRef, w] = useWidth<HTMLDivElement>(1040);
  const h = mapHeight(w);
  const areas = useMemo(() => data?.areas ?? [], [data]);
  const layout = useMemo(() => layoutMap(areas, w, h), [areas, w, h]);
  const stars = useMemo(() => starfield(w, h, w < 640 ? 70 : 140), [w, h]);
  const [hot, setHot] = useState<string | null>(null);
  const hotPlaced = hot ? layout.placed.find((p) => p.node.key === hot) ?? null : null;
  const narrow = w < 640;

  const totals = useMemo(() => {
    let setup = 0;
    let used = 0;
    let live = 0;
    let all = 0;
    for (const a of areas)
      for (const n of a.nodes) {
        all += 1;
        if (n.setup > 0 || n.used > 0 || n.live > 0) setup += 1;
        if (n.used > 0) used += 1;
        if (n.live > 0) live += 1;
      }
    return { setup, used, live, all };
  }, [areas]);

  function open(p: Placed) {
    recordOpen(p.node.href);
    router.push(p.node.href);
  }

  return (
    <div ref={wrapRef} className="relative w-full" data-testid="system-map">
      <svg
        width={w}
        height={h}
        viewBox={`0 0 ${w} ${h}`}
        role="img"
        aria-labelledby="system-map-title"
        className="sm-map block select-none"
        data-hot={hot ? "1" : undefined}
      >
        <title id="system-map-title">
          {`Iron Jarvis system map: ${totals.setup} of ${totals.all} parts set up, ${totals.used} used this week, ${totals.live} in use now`}
        </title>
        <defs>
          {AREA_ORDER.map((k) => (
            <radialGradient key={k} id={`sm-glow-${k}`}>
              <stop offset="0%" stopColor={rgb(AREA_COLOR[k], 0.55)} />
              <stop offset="45%" stopColor={rgb(AREA_COLOR[k], 0.16)} />
              <stop offset="100%" stopColor={rgb(AREA_COLOR[k], 0)} />
            </radialGradient>
          ))}
          <radialGradient id="sm-glow-warn">
            <stop offset="0%" stopColor={rgb(WARN, 0.6)} />
            <stop offset="100%" stopColor={rgb(WARN, 0)} />
          </radialGradient>
          <radialGradient id="sm-core-bloom">
            <stop offset="0%" stopColor="rgb(var(--accent-rgb) / 0.22)" />
            <stop offset="60%" stopColor="rgb(var(--accent-rgb) / 0.05)" />
            <stop offset="100%" stopColor="rgb(var(--accent-rgb) / 0)" />
          </radialGradient>
        </defs>

        {/* The sky. */}
        <g aria-hidden>
          {stars.map((s, i) => (
            <circle key={i} cx={s.x} cy={s.y} r={s.r} fill="rgb(var(--zinc-300))" opacity={s.o} />
          ))}
        </g>

        {/* Orbits. */}
        <g aria-hidden fill="none">
          <ellipse cx={layout.cx} cy={layout.cy} rx={layout.rx} ry={layout.ry} stroke="rgb(var(--zinc-400) / 0.10)" />
          <ellipse
            cx={layout.cx}
            cy={layout.cy}
            rx={layout.rx * 0.62}
            ry={layout.ry * 0.62}
            stroke="rgb(var(--zinc-400) / 0.07)"
            strokeDasharray="2 7"
          />
          <circle cx={layout.cx} cy={layout.cy} r={layout.core * 2.6} fill="url(#sm-core-bloom)" />
        </g>

        {/* Each constellation's arc and name, inside the ring. */}
        <g aria-hidden>
          {layout.arcs.map((a) => {
            const k = 0.62;
            const lx = layout.cx + layout.rx * (narrow ? 0.5 : 0.47) * Math.cos(a.mid);
            const ly = layout.cy + layout.ry * (narrow ? 0.5 : 0.47) * Math.sin(a.mid);
            return (
              <g key={a.area}>
                <path
                  d={arcPath(layout, a.a0 + 0.02, a.a1 - 0.02, k)}
                  fill="none"
                  stroke={rgb(AREA_COLOR[a.area] ?? AREA_COLOR.work, 0.5)}
                  strokeWidth={2.5}
                  strokeLinecap="round"
                />
                <text
                  x={lx}
                  y={ly}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  className="sm-area-label"
                  fill={rgb(AREA_COLOR[a.area] ?? AREA_COLOR.work, 0.85)}
                  fontSize={narrow ? 9 : 10}
                  letterSpacing="0.18em"
                >
                  {a.label.toUpperCase()}
                </text>
                <text
                  x={lx}
                  y={ly + (narrow ? 12 : 14)}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  className="sm-area-label"
                  fill="rgb(var(--zinc-500))"
                  fontSize={narrow ? 9 : 10}
                >
                  {a.setUp} of {a.total}
                </text>
              </g>
            );
          })}
        </g>

        {/* Field lines from every part that exists to the core. */}
        <g aria-hidden fill="none">
          {layout.placed.map((p) => {
            const st = nodeState(p.node);
            if (st === "off") return null;
            const color = p.node.attention ? WARN : AREA_COLOR[p.area] ?? AREA_COLOR.work;
            const d = linkPath(p, layout);
            const isHot = hot === p.node.key;
            const base = st === "live" ? 0.42 : st === "used" ? 0.22 : 0.1;
            return (
              <g key={p.node.key} className="sm-link" data-dim={hot && !isHot ? "1" : undefined}>
                <path d={d} stroke={rgb(color, isHot ? 0.7 : base)} strokeWidth={isHot ? 1.6 : 1} />
                {st === "live" && (
                  <path
                    d={d}
                    stroke={rgb(color, 0.95)}
                    strokeWidth={1.8}
                    strokeLinecap="round"
                    strokeDasharray="3 14"
                    className="sm-flow"
                  />
                )}
              </g>
            );
          })}
        </g>

        {/* The stars. */}
        <g>
          {layout.placed.map((p) => {
            const n = p.node;
            const st = nodeState(n);
            const color = n.attention ? WARN : AREA_COLOR[p.area] ?? AREA_COLOR.work;
            const Icon = iconFor(n.href);
            const isHot = hot === n.key;
            const lx = p.x + p.ux * (p.r + (narrow ? 7 : 10));
            const ly = p.y + p.uy * (p.r + (narrow ? 7 : 10));
            const anchor = p.ux > 0.3 ? "start" : p.ux < -0.3 ? "end" : "middle";
            // Above/below the ring the name sits over/under the star.
            const vshift = p.uy < -0.6 ? -9 : p.uy > 0.6 ? 9 : 0;
            const iconSize = st === "off" ? 0 : Math.max(9, Math.min(15, p.r * 1.05));
            return (
              <a
                key={n.key}
                href={n.href}
                className="sm-node"
                data-testid={`system-map-node-${n.key}`}
                data-state={st}
                data-attention={n.attention ? "1" : undefined}
                data-dim={hot && !isHot ? "1" : undefined}
                aria-label={`${n.label}: ${n.attention ? `needs you — ${n.attention}. ` : ""}${describe(n)}`}
                onMouseEnter={() => setHot(n.key)}
                onMouseLeave={() => setHot((k) => (k === n.key ? null : k))}
                onFocus={() => setHot(n.key)}
                onBlur={() => setHot((k) => (k === n.key ? null : k))}
                onClick={(e) => {
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
                  e.preventDefault();
                  open(p);
                }}
              >
                {/* hit area */}
                <circle cx={p.x} cy={p.y} r={Math.max(18, p.r + 8)} fill="transparent" />
                {st !== "off" && (
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={p.r * (st === "used" || st === "live" ? 3.1 : 2.2)}
                    fill={`url(#${n.attention ? "sm-glow-warn" : `sm-glow-${AREA_ORDER.includes(p.area as never) ? p.area : "work"}`})`}
                    className="sm-halo"
                    style={{ "--sm-delay": `${(hash(n.key) % 40) / 10}s` } as CSSProperties}
                  />
                )}
                {st === "live" && (
                  <>
                    <circle cx={p.x} cy={p.y} r={p.r} fill="none" stroke={rgb(color, 0.8)} className="sm-ripple" />
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={p.r}
                      fill="none"
                      stroke={rgb(color, 0.8)}
                      className="sm-ripple sm-ripple-2"
                    />
                  </>
                )}
                {st === "off" ? (
                  <circle
                    cx={p.x}
                    cy={p.y}
                    r={p.r}
                    fill="rgb(var(--zinc-500) / 0.06)"
                    stroke="rgb(var(--zinc-500) / 0.55)"
                    strokeDasharray="2 2.5"
                  />
                ) : (
                  <>
                    <circle
                      cx={p.x}
                      cy={p.y}
                      r={p.r}
                      fill={rgb(color, st === "setup" ? 0.1 : 0.2)}
                      stroke={rgb(color, isHot ? 1 : st === "setup" ? 0.55 : 0.9)}
                      strokeWidth={isHot ? 1.8 : 1.2}
                    />
                    <Icon
                      x={p.x - iconSize / 2}
                      y={p.y - iconSize / 2}
                      width={iconSize}
                      height={iconSize}
                      strokeWidth={2}
                      color={rgb(color, st === "setup" ? 0.75 : 1)}
                      aria-hidden
                    />
                  </>
                )}
                {n.attention && (
                  <g aria-hidden>
                    <circle cx={p.x + p.r * 0.75} cy={p.y - p.r * 0.75} r={4.5} fill={rgb(WARN, 1)} />
                    <text
                      x={p.x + p.r * 0.75}
                      y={p.y - p.r * 0.75 + 0.5}
                      textAnchor="middle"
                      dominantBaseline="middle"
                      fontSize={7}
                      fontWeight={700}
                      fill="rgb(var(--ink-950))"
                    >
                      !
                    </text>
                  </g>
                )}
                <text
                  x={lx}
                  y={ly + vshift}
                  textAnchor={anchor}
                  dominantBaseline="middle"
                  fontSize={narrow ? 10 : 11.5}
                  fontWeight={500}
                  fill={st === "off" ? "rgb(var(--zinc-500))" : "rgb(var(--zinc-100))"}
                  className="sm-label"
                >
                  {n.label}
                  {!narrow && (
                    <tspan
                      x={lx}
                      dy={13}
                      fontSize={10}
                      fontWeight={400}
                      fill={
                        n.attention
                          ? rgb(WARN, 1)
                          : st === "live"
                            ? rgb(color, 1)
                            : st === "off"
                              ? "rgb(var(--zinc-600))"
                              : "rgb(var(--zinc-500))"
                      }
                    >
                      {nodeLine(n)}
                    </tspan>
                  )}
                </text>
              </a>
            );
          })}
        </g>
      </svg>

      {/* The reactor at the centre (HTML so the CSS sweeps and the live
          MoodOrb are reused as they are on every other surface). */}
      <div
        aria-hidden
        className="pointer-events-none absolute grid place-items-center"
        style={{
          left: layout.cx - layout.core,
          top: layout.cy - layout.core,
          width: layout.core * 2,
          height: layout.core * 2,
        }}
      >
        <div className="absolute inset-0 rounded-full border border-accent/20" />
        <div className="absolute inset-[12%] rounded-full border border-dashed border-accent/25" />
        <div className="sm-spin absolute inset-0 rounded-full [animation-duration:14s] [background:conic-gradient(from_0deg,transparent_0deg,rgb(var(--accent-rgb)/0.6)_55deg,transparent_150deg)] [mask:radial-gradient(farthest-side,transparent_calc(100%_-_5px),#000_calc(100%_-_5px))] [-webkit-mask:radial-gradient(farthest-side,transparent_calc(100%_-_5px),#000_calc(100%_-_5px))]" />
        <div className="sm-spin absolute inset-[18%] rounded-full [animation-direction:reverse] [animation-duration:23s] [background:conic-gradient(from_180deg,transparent,rgb(var(--accent-soft-rgb)/0.45),transparent_120deg)] [mask:radial-gradient(farthest-side,transparent_calc(100%_-_4px),#000_calc(100%_-_4px))] [-webkit-mask:radial-gradient(farthest-side,transparent_calc(100%_-_4px),#000_calc(100%_-_4px))]" />
        <div className="absolute inset-[30%] rounded-full bg-[radial-gradient(closest-side,rgb(var(--accent-rgb)/0.35),transparent)] blur-md" />
        <div className="relative grid h-[44%] w-[44%] place-items-center rounded-full border border-accent/40 bg-accent/[0.08] shadow-[0_0_70px_-6px_rgb(var(--accent-rgb)/0.75),inset_0_0_24px_rgb(var(--accent-rgb)/0.25)]">
          <span style={{ transform: `scale(${Math.max(1.6, layout.core / 26)})` }}>
            <MoodOrb />
          </span>
        </div>
      </div>

      {/* The hover card. */}
      {hotPlaced && <StarCard p={hotPlaced} w={w} h={h} />}

      {loading && areas.length === 0 && (
        <div className="absolute inset-x-0 bottom-6 text-center text-[12px] text-zinc-500">Reading the map…</div>
      )}

      {/* The same picture in words, for a screen reader. */}
      <ul className="sr-only" data-testid="system-map-list">
        {areas.map((a) =>
          a.nodes.map((n) => (
            <li key={n.key}>
              {a.label} — {n.label}: {n.attention ? `needs you — ${n.attention}. ` : ""}
              {describe(n)}
            </li>
          )),
        )}
      </ul>
    </div>
  );
}

/** "2 set up · 14 uses this week · 1 in use now" — the card's facts line. */
export function describe(n: MapNode): string {
  const parts: string[] = [];
  if (n.setup > 0) parts.push(n.unit ? `${n.setup} ${plural(n.unit, n.setup)}` : `${n.setup} set up`);
  // Harness gaps review: a star in use (a coding session answering with no
  // terminal open lights Build) is never "not set up yet" beside it.
  else if (n.used === 0 && n.live === 0) parts.push("not set up yet");
  if (n.used > 0) parts.push(`${compact(n.used)} use${n.used === 1 ? "" : "s"} this week`);
  if (n.live > 0) parts.push(`${n.live} in use now`);
  return parts.join(" · ");
}

function hash(s: string): number {
  let h = 0;
  for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) | 0;
  return Math.abs(h);
}

function StarCard({ p, w, h }: { p: Placed; w: number; h: number }) {
  const n = p.node;
  const st = nodeState(n);
  const color = n.attention ? WARN : AREA_COLOR[p.area] ?? AREA_COLOR.work;
  const CARD_W = 232;
  // Toward the core from the star, so the card never leaves the map.
  const left = Math.min(w - CARD_W - 8, Math.max(8, p.ux > 0 ? p.x - CARD_W - p.r - 14 : p.x + p.r + 14));
  const top = Math.min(h - 120, Math.max(8, p.y - 40));
  const Icon = iconFor(n.href);
  return (
    <div
      role="tooltip"
      data-testid="system-map-card"
      className="pointer-events-none absolute z-10 rounded-xl border border-white/10 bg-ink-900/90 p-3 shadow-card-hover backdrop-blur-md"
      style={{ left, top, width: CARD_W }}
    >
      <div className="flex items-center gap-2">
        <span
          className="grid h-7 w-7 shrink-0 place-items-center rounded-lg"
          style={{ background: rgb(color, 0.14), color: rgb(color, 1) }}
        >
          <Icon size={14} aria-hidden />
        </span>
        <div className="min-w-0">
          <div className="truncate text-[13px] font-semibold text-zinc-100">{n.label}</div>
          <div className="text-[11px]" style={{ color: rgb(color, 1) }}>
            {n.attention ? "Needs you" : st === "live" ? "In use now" : st === "used" ? "Used this week" : st === "setup" ? "Set up" : "Not set up yet"}
          </div>
        </div>
      </div>
      {n.attention && <p className="mt-2 text-[12px] leading-snug text-tone-warn">{n.attention}</p>}
      {n.detail && <p className="mt-2 text-[12px] leading-snug text-zinc-400">{n.detail}</p>}
      <p className="mt-2 text-[11px] text-zinc-500">{describe(n)}</p>
      <p className="mt-1.5 text-[11px] font-medium text-accent-soft">Click to open →</p>
    </div>
  );
}

/** Hour-by-hour activity, the last 24 hours — one glowing wave. */
export function Heartbeat({ hours }: { hours: number[] }) {
  const [ref, w] = useWidth<HTMLDivElement>(1000);
  const h = 92;
  const vals = hours.length > 1 ? hours : [0, 0];
  const max = Math.max(1, ...vals);
  const total = vals.reduce((a, b) => a + b, 0);
  const pts = vals.map((v, i) => ({
    x: (i / (vals.length - 1)) * (w - 16) + 8,
    y: h - 14 - (v / max) * (h - 30),
  }));
  const line = smooth(pts);
  const area = `${line} L${pts[pts.length - 1].x.toFixed(1)},${h - 14} L${pts[0].x.toFixed(1)},${h - 14} Z`;
  const last = pts[pts.length - 1];
  const peak = vals.indexOf(Math.max(...vals));
  const peakAgo = vals.length - 1 - peak;
  return (
    <div ref={ref} className="w-full" data-testid="system-heartbeat">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2">
        <div className="text-[10px] font-medium uppercase tracking-[0.18em] text-zinc-500">Last 24 hours</div>
        <div className="text-[12px] text-zinc-400">
          {total === 0
            ? "A quiet day so far"
            : `${compact(total)} thing${total === 1 ? "" : "s"} happened · busiest ${peakAgo === 0 ? "this hour" : `${peakAgo}h ago`}`}
        </div>
      </div>
      <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden className="block">
        <defs>
          <linearGradient id="sm-hb-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="rgb(var(--accent-rgb) / 0.32)" />
            <stop offset="100%" stopColor="rgb(var(--accent-rgb) / 0)" />
          </linearGradient>
        </defs>
        <line x1={8} x2={w - 8} y1={h - 14} y2={h - 14} stroke="rgb(var(--zinc-400) / 0.12)" />
        <path d={area} fill="url(#sm-hb-fill)" />
        <path d={line} fill="none" stroke="rgb(var(--accent-rgb) / 0.95)" strokeWidth={1.8} strokeLinejoin="round" />
        <circle cx={last.x} cy={last.y} r={3} fill="rgb(var(--accent-rgb))" />
        <circle cx={last.x} cy={last.y} r={3} fill="none" stroke="rgb(var(--accent-rgb) / 0.8)" className="sm-ripple" />
        {[0, 6, 12, 18].map((ago) => {
          const i = vals.length - 1 - ago;
          if (i < 0) return null;
          const x = pts[i].x;
          return (
            <text key={ago} x={x} y={h - 2} fontSize={9.5} textAnchor={ago === 0 ? "end" : "middle"} fill="rgb(var(--zinc-500))">
              {ago === 0 ? "now" : `${ago}h ago`}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

/** Catmull-Rom through the points, as cubic Béziers. */
function smooth(pts: { x: number; y: number }[]): string {
  if (pts.length === 0) return "";
  let d = `M${pts[0].x.toFixed(1)},${pts[0].y.toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i += 1) {
    const p0 = pts[i - 1] ?? pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] ?? p2;
    const c1x = p1.x + (p2.x - p0.x) / 6;
    const c1y = p1.y + (p2.y - p0.y) / 6;
    const c2x = p2.x - (p3.x - p1.x) / 6;
    const c2y = p2.y - (p3.y - p1.y) / 6;
    d += ` C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;
  }
  return d;
}

/** The legend, in words. */
export function MapLegend() {
  const item = "inline-flex items-center gap-1.5";
  return (
    <div className="flex flex-wrap items-center justify-center gap-x-5 gap-y-2 text-[11px] text-zinc-500" data-testid="system-map-legend">
      <span className={item}>
        <svg width="12" height="12" aria-hidden>
          <circle cx="6" cy="6" r="4.5" fill="none" stroke="rgb(var(--zinc-500) / 0.7)" strokeDasharray="2 2" />
        </svg>
        Not set up
      </span>
      <span className={item}>
        <svg width="12" height="12" aria-hidden>
          <circle cx="6" cy="6" r="4.5" fill="rgb(var(--accent-rgb) / 0.1)" stroke="rgb(var(--accent-rgb) / 0.55)" />
        </svg>
        Set up
      </span>
      <span className={item}>
        <svg width="16" height="16" aria-hidden>
          <circle cx="8" cy="8" r="7.5" fill="rgb(var(--accent-rgb) / 0.12)" />
          <circle cx="8" cy="8" r="5" fill="rgb(var(--accent-rgb) / 0.2)" stroke="rgb(var(--accent-rgb) / 0.9)" />
        </svg>
        Used this week — bigger is busier
      </span>
      <span className={item}>
        <svg width="16" height="16" aria-hidden className="overflow-visible">
          <circle cx="8" cy="8" r="4.5" fill="rgb(var(--accent-rgb) / 0.25)" stroke="rgb(var(--accent-rgb))" />
          <circle cx="8" cy="8" r="4.5" fill="none" stroke="rgb(var(--accent-rgb) / 0.8)" className="sm-ripple" />
        </svg>
        In use now
      </span>
      <span className={item}>
        <svg width="12" height="12" aria-hidden>
          <circle cx="6" cy="6" r="4.5" fill="rgb(var(--tone-warn) / 0.2)" stroke="rgb(var(--tone-warn))" />
        </svg>
        Needs you
      </span>
    </div>
  );
}
