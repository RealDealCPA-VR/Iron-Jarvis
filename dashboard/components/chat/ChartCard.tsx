"use client";

/**
 * A ```chart fence drawn as a chart (v1.325.0). Its own SVG — no chart
 * library — because the shapes are three (grouped bar, line, pie) and a
 * dependency would be heavier than the drawing.
 *
 * Rules this card keeps:
 * - COLOURS COME FROM THE THEME. Every fill and stroke is a CSS token
 *   (`--accent-rgb`, `--tone-*`, `--zinc-*`), applied through `style` (a
 *   `var()` in an SVG presentation attribute is not reliable), so a light
 *   theme or a user-made palette recolours the chart with the rest of the app.
 * - THE NUMBERS ARE ALWAYS ONE PRESS AWAY AS NUMBERS: "Show as table" draws
 *   the same values as a table, "Copy data" puts them on the clipboard as CSV
 *   (`chartToCsv`), and the SVG's aria-label summarises the chart for a screen
 *   reader.
 * - IT FITS A PHONE. The SVG's viewBox follows the card's measured width
 *   (ResizeObserver; 560 when it cannot be measured) and draws at width 100%,
 *   so text stays at its real size instead of shrinking with the picture, and
 *   x labels thin out when they would collide.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { BarChart3, Check, Copy, Table2 } from "lucide-react";
import { chartToCsv, niceTicks, type ChartSpec } from "@/lib/chartSpec";

/** Series colours, in order: the theme accent first, then the tone tokens. */
const SERIES_TOKENS = [
  "--accent-rgb",
  "--tone-violet",
  "--tone-warn",
  "--tone-success",
  "--tone-danger",
  "--tone-info",
] as const;

/** The i-th series (or pie slice) colour as a theme-token `rgb()` string. A pie
 *  with more slices than tokens cycles the tokens at a lower opacity. */
export function seriesColor(i: number, alpha = 1): string {
  const token = SERIES_TOKENS[i % SERIES_TOKENS.length];
  const tier = Math.floor(i / SERIES_TOKENS.length);
  const a = Math.max(0.25, alpha * (tier === 0 ? 1 : Math.pow(0.68, tier)));
  return a >= 1 ? `rgb(var(${token}))` : `rgb(var(${token}) / ${Number(a.toFixed(2))})`;
}

const NUM = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
const COMPACT = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const CURRENCY_UNIT = /^[$€£¥]$/;

function withUnit(s: string, unit?: string): string {
  if (!unit) return s;
  if (CURRENCY_UNIT.test(unit)) return s.startsWith("-") ? `-${unit}${s.slice(1)}` : `${unit}${s}`;
  if (unit === "%") return `${s}%`;
  return `${s} ${unit}`;
}

/** A value as the user reads it: "1,234.5 USD", "$1,234.5", "12%". */
export function formatValue(n: number, unit?: string): string {
  return withUnit(NUM.format(n), unit);
}

/** An axis tick: compact past 10,000 ("12K"); only a short unit rides a tick. */
function formatTick(n: number, unit?: string): string {
  const s = Math.abs(n) >= 10_000 ? COMPACT.format(n) : NUM.format(n);
  return unit && (CURRENCY_UNIT.test(unit) || unit === "%") ? withUnit(s, unit) : s;
}

const TYPE_WORD: Record<ChartSpec["type"], string> = { bar: "Bar", line: "Line", pie: "Pie" };

/** The plain-words summary a screen reader hears for the chart. */
export function chartSummary(spec: ChartSpec): string {
  const head = `${TYPE_WORD[spec.type]} chart${spec.title ? `: ${spec.title}` : ""}.`;
  if (spec.type === "pie") {
    const vals = spec.series[0].values;
    const total = vals.reduce((a, b) => a + b, 0);
    const parts = spec.labels
      .slice(0, 8)
      .map((l, i) => `${l} ${formatValue(vals[i], spec.unit)} (${Math.round((vals[i] / total) * 100)}%)`);
    const more = spec.labels.length > 8 ? `, and ${spec.labels.length - 8} more` : "";
    return `${head} ${parts.join(", ")}${more}.`;
  }
  const all = spec.series.flatMap((s) => s.values);
  const min = Math.min(...all);
  const max = Math.max(...all);
  const n = spec.labels.length;
  const span = n === 1 ? spec.labels[0] : `${spec.labels[0]} to ${spec.labels[n - 1]}`;
  const names = spec.series.map((s) => s.name).filter(Boolean);
  const who = names.length ? `${names.join(", ")} ` : "";
  return `${head} ${who}across ${n} ${n === 1 ? "point" : "points"} (${span}), from ${formatValue(
    min,
    spec.unit,
  )} to ${formatValue(max, spec.unit)}.`;
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s;
  return `${s.slice(0, Math.max(1, max - 1))}…`;
}

/** The card's width, measured; a fallback when the box cannot be measured. */
function useBoxWidth(enabled: boolean, fallback = 560) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const el = ref.current;
    if (!enabled || !el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0]?.contentRect.width ?? 0);
      if (w > 0) setWidth(Math.max(240, Math.min(w, 960)));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [enabled]);
  return { ref, width };
}

const GRID = "rgb(var(--zinc-500) / 0.18)";
const ZERO = "rgb(var(--zinc-500) / 0.5)";
const TICK_TEXT = "rgb(var(--zinc-400))";
const BAND = "rgb(var(--zinc-500) / 0.08)";

interface Active {
  label: number;
  series?: number;
}

function AxisChart({
  spec,
  width,
  active,
  setActive,
}: {
  spec: ChartSpec;
  width: number;
  active: Active | null;
  setActive: (a: Active | null) => void;
}) {
  const H = 240;
  const n = spec.labels.length;
  const k = spec.series.length;
  const all = spec.series.flatMap((s) => s.values);
  let lo = Math.min(...all);
  let hi = Math.max(...all);
  if (spec.type === "bar") {
    lo = Math.min(0, lo);
    hi = Math.max(0, hi);
  }
  const ticks = niceTicks(lo, hi, 5);
  const t0 = ticks[0];
  const t1 = ticks[ticks.length - 1];
  const tickLabels = ticks.map((t) => formatTick(t, spec.unit));
  const left = Math.min(96, 10 + Math.max(...tickLabels.map((s) => s.length)) * 6.6);
  const right = 10;
  const top = 10;
  const bottom = 26;
  const plotW = Math.max(40, width - left - right);
  const plotH = H - top - bottom;
  const y = (v: number) => top + plotH - ((v - t0) / (t1 - t0 || 1)) * plotH;
  const slot = plotW / n;
  const cx = (i: number) => left + (i + 0.5) * slot;
  const every = Math.max(1, Math.ceil(n / Math.max(1, Math.floor(plotW / 58))));
  const labelChars = Math.max(4, Math.floor((slot * every) / 6.6));
  const baseY = y(Math.min(Math.max(0, t0), t1));

  return (
    <svg
      role="img"
      aria-label={chartSummary(spec)}
      viewBox={`0 0 ${width} ${H}`}
      width="100%"
      preserveAspectRatio="xMidYMid meet"
      className="block h-auto max-w-full select-none"
      data-testid="chart-svg"
      onMouseLeave={() => setActive(null)}
    >
      {ticks.map((t, i) => (
        <g key={`t${i}`}>
          <line
            x1={left}
            x2={left + plotW}
            y1={y(t)}
            y2={y(t)}
            style={{ stroke: t === 0 ? ZERO : GRID }}
            strokeWidth={1}
          />
          <text
            x={left - 6}
            y={y(t)}
            dy="0.32em"
            textAnchor="end"
            fontSize={11}
            style={{ fill: TICK_TEXT }}
          >
            {tickLabels[i]}
          </text>
        </g>
      ))}
      {active && <rect x={left + active.label * slot} y={top} width={slot} height={plotH} style={{ fill: BAND }} />}
      {spec.labels.map((label, i) =>
        i % every === 0 ? (
          <text
            key={`x${i}`}
            x={cx(i)}
            y={H - 8}
            textAnchor="middle"
            fontSize={11}
            style={{ fill: TICK_TEXT }}
          >
            {truncate(label, labelChars)}
          </text>
        ) : null,
      )}
      {spec.type === "bar" &&
        spec.series.map((s, j) => {
          const inner = slot * 0.78;
          const barW = Math.max(1, inner / k);
          return (
            <g key={`s${j}`} data-series={j}>
              {s.values.map((v, i) => {
                const x = left + i * slot + (slot - inner) / 2 + j * barW;
                const yv = y(v);
                return (
                  <rect
                    key={i}
                    data-testid="chart-bar"
                    x={x}
                    y={Math.min(baseY, yv)}
                    width={Math.max(1, barW - (k > 1 ? 1 : 0))}
                    height={Math.max(0, Math.abs(baseY - yv))}
                    rx={Math.min(3, barW / 4)}
                    style={{ fill: seriesColor(j, active && active.label !== i ? 0.55 : 1) }}
                    onMouseEnter={() => setActive({ label: i, series: j })}
                  >
                    <title>{`${spec.labels[i]} · ${s.name ? `${s.name}: ` : ""}${formatValue(v, spec.unit)}`}</title>
                  </rect>
                );
              })}
            </g>
          );
        })}
      {spec.type === "line" &&
        spec.series.map((s, j) => (
          <g key={`s${j}`} data-series={j}>
            <polyline
              points={s.values.map((v, i) => `${cx(i)},${y(v)}`).join(" ")}
              fill="none"
              style={{ stroke: seriesColor(j) }}
              strokeWidth={2}
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {s.values.map((v, i) => (
              <circle
                key={i}
                data-testid="chart-point"
                cx={cx(i)}
                cy={y(v)}
                r={active?.label === i ? 4.5 : 3}
                style={{ fill: seriesColor(j), stroke: "rgb(var(--ink-900))" }}
                strokeWidth={1.5}
                onMouseEnter={() => setActive({ label: i, series: j })}
              >
                <title>{`${spec.labels[i]} · ${s.name ? `${s.name}: ` : ""}${formatValue(v, spec.unit)}`}</title>
              </circle>
            ))}
          </g>
        ))}
      {/* Whole-column hover targets for a line: a point is a 6 px target. */}
      {spec.type === "line" &&
        spec.labels.map((_, i) => (
          <rect
            key={`h${i}`}
            x={left + i * slot}
            y={top}
            width={slot}
            height={plotH}
            fill="transparent"
            onMouseEnter={() => setActive({ label: i })}
          />
        ))}
    </svg>
  );
}

function arcPath(cx: number, cy: number, r: number, a0: number, a1: number): string {
  const x0 = cx + r * Math.sin(a0);
  const y0 = cy - r * Math.cos(a0);
  const x1 = cx + r * Math.sin(a1);
  const y1 = cy - r * Math.cos(a1);
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const f = (n: number) => Number(n.toFixed(3));
  return `M ${f(cx)} ${f(cy)} L ${f(x0)} ${f(y0)} A ${r} ${r} 0 ${large} 1 ${f(x1)} ${f(y1)} Z`;
}

function PieChart({
  spec,
  active,
  setActive,
}: {
  spec: ChartSpec;
  active: Active | null;
  setActive: (a: Active | null) => void;
}) {
  const S = 220;
  const c = S / 2;
  const r = c - 6;
  const vals = spec.series[0].values;
  const total = vals.reduce((a, b) => a + b, 0);
  let angle = 0;
  const slices = vals.map((v, i) => {
    const a0 = angle;
    const a1 = angle + (v / total) * Math.PI * 2;
    angle = a1;
    return { i, v, a0, a1 };
  });
  return (
    <svg
      role="img"
      aria-label={chartSummary(spec)}
      viewBox={`0 0 ${S} ${S}`}
      width="100%"
      className="block h-auto w-full max-w-[220px] shrink-0 select-none"
      data-testid="chart-svg"
      onMouseLeave={() => setActive(null)}
    >
      {slices.map(({ i, v, a0, a1 }) => {
        if (v <= 0) return null;
        const title = <title>{`${spec.labels[i]} · ${formatValue(v, spec.unit)} (${Math.round((v / total) * 100)}%)`}</title>;
        const style = {
          fill: seriesColor(i, active && active.label !== i ? 0.55 : 1),
          stroke: "rgb(var(--ink-900))",
        };
        const onEnter = () => setActive({ label: i });
        // One slice holding everything: an arc from a point to itself draws
        // nothing, so it is a circle.
        if (a1 - a0 >= Math.PI * 2 - 1e-9) {
          return (
            <circle key={i} data-testid="chart-slice" cx={c} cy={c} r={r} style={style} onMouseEnter={onEnter}>
              {title}
            </circle>
          );
        }
        return (
          <path
            key={i}
            data-testid="chart-slice"
            d={arcPath(c, c, r, a0, a1)}
            style={style}
            strokeWidth={1.5}
            onMouseEnter={onEnter}
          >
            {title}
          </path>
        );
      })}
    </svg>
  );
}

function Swatch({ color }: { color: string }) {
  return <span aria-hidden className="inline-block h-2.5 w-2.5 shrink-0 rounded-sm" style={{ backgroundColor: color }} />;
}

export function ChartCard({ spec }: { spec: ChartSpec }) {
  const [asTable, setAsTable] = useState(false);
  const [active, setActive] = useState<Active | null>(null);
  const [copy, setCopy] = useState<"idle" | "copied" | "failed">("idle");
  const timer = useRef<number | null>(null);
  const { ref, width } = useBoxWidth(!asTable);
  const csv = useMemo(() => chartToCsv(spec), [spec]);
  const pie = spec.type === "pie";
  const pieTotal = pie ? spec.series[0].values.reduce((a, b) => a + b, 0) : 0;

  useEffect(
    () => () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    },
    [],
  );

  function copyData() {
    const done = (state: "copied" | "failed") => {
      setCopy(state);
      if (timer.current !== null) window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setCopy("idle"), 1800);
    };
    try {
      navigator.clipboard.writeText(csv).then(
        () => done("copied"),
        () => done("failed"),
      );
    } catch {
      done("failed");
    }
  }

  const readout = (() => {
    if (!active) return "";
    const label = spec.labels[active.label];
    if (pie) {
      const v = spec.series[0].values[active.label];
      return `${label}: ${formatValue(v, spec.unit)} (${Math.round((v / pieTotal) * 100)}%)`;
    }
    const shown = active.series === undefined ? spec.series : [spec.series[active.series]];
    return `${label} — ${shown
      .map((s) => `${s.name ? `${s.name}: ` : ""}${formatValue(s.values[active.label], spec.unit)}`)
      .join(" · ")}`;
  })();

  // Calm chat W1-4 (v1.326.0): the chart reads as part of the reply, not a
  // card inside it. No frame and no filled header: a quiet title line with
  // ghost buttons, the chart on the page, and a table ruled with hairlines.
  const btn =
    "inline-flex h-7 items-center gap-1.5 rounded-lg px-2 text-[12px] text-zinc-500 transition-colors hover:bg-white/[0.06] hover:text-zinc-100";

  return (
    <figure
      data-testid="chart-card"
      data-chart-type={spec.type}
      className="my-3 max-w-2xl"
    >
      <div className="-mr-2 flex flex-wrap items-center gap-x-1 gap-y-1">
        {/* The title takes the line on a narrow screen and wraps there (the
            two buttons drop below it together) instead of running under them. */}
        <figcaption className="min-w-0 flex-[1_1_14rem] text-[13px] font-medium text-zinc-200">
          <span>{spec.title || `${TYPE_WORD[spec.type]} chart`}</span>
          {spec.unit && <span className="ml-1.5 font-normal text-zinc-500">({spec.unit})</span>}
        </figcaption>
        <div className="flex items-center">
          <button
            type="button"
            className={btn}
            aria-pressed={asTable}
            onClick={() => {
              setAsTable((v) => !v);
              setActive(null);
            }}
          >
            {asTable ? <BarChart3 size={12} aria-hidden /> : <Table2 size={12} aria-hidden />}
            {asTable ? "Show as chart" : "Show as table"}
          </button>
          <button type="button" className={btn} onClick={copyData} title="Copy the numbers as CSV (paste into a spreadsheet)">
            {copy === "copied" ? (
              <Check size={12} aria-hidden className="text-tone-success" />
            ) : (
              <Copy size={12} aria-hidden />
            )}
            {copy === "copied" ? "Copied" : copy === "failed" ? "Couldn't copy" : "Copy data"}
          </button>
        </div>
      </div>

      {asTable ? (
        <div className="overflow-x-auto pt-1">
          <table data-testid="chart-table" className="w-full border-collapse text-[13px]">
            <thead>
              <tr>
                <th className="border-b border-white/[0.16] py-1.5 pr-3 text-left font-medium text-zinc-400">
                  Label
                </th>
                {spec.series.map((s, j) => (
                  <th
                    key={j}
                    className="border-b border-white/[0.16] py-1.5 pl-3 text-right font-medium text-zinc-400"
                  >
                    {s.name || `Series ${j + 1}`}
                  </th>
                ))}
                {pie && (
                  <th className="border-b border-white/[0.16] py-1.5 pl-3 text-right font-medium text-zinc-400">
                    Share
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {spec.labels.map((label, i) => (
                <tr key={i}>
                  <td className="border-b border-white/[0.08] py-1.5 pr-3 text-zinc-300">{label}</td>
                  {spec.series.map((s, j) => (
                    <td key={j} className="border-b border-white/[0.08] py-1.5 pl-3 text-right tabular-nums text-zinc-300">
                      {formatValue(s.values[i], spec.unit)}
                    </td>
                  ))}
                  {pie && (
                    <td className="border-b border-white/[0.08] py-1.5 pl-3 text-right tabular-nums text-zinc-400">
                      {`${Math.round((spec.series[0].values[i] / pieTotal) * 100)}%`}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="pt-2">
          {pie ? (
            <div ref={ref} className="flex flex-col items-center gap-3 sm:flex-row sm:items-start">
              <PieChart spec={spec} active={active} setActive={setActive} />
              <ul className="flex min-w-0 flex-1 flex-col gap-1 text-[12px]" data-testid="chart-legend">
                {spec.labels.map((label, i) => (
                  <li
                    key={i}
                    className={`flex items-center gap-2 rounded px-1 ${active?.label === i ? "bg-white/[0.05]" : ""}`}
                    onMouseEnter={() => setActive({ label: i })}
                    onMouseLeave={() => setActive(null)}
                  >
                    <Swatch color={seriesColor(i)} />
                    <span className="min-w-0 flex-1 truncate text-zinc-300" title={label}>
                      {label}
                    </span>
                    <span className="tabular-nums text-zinc-400">
                      {`${Math.round((spec.series[0].values[i] / pieTotal) * 100)}%`}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ) : (
            <div ref={ref}>
              <AxisChart spec={spec} width={width} active={active} setActive={setActive} />
              {spec.series.length > 1 && (
                <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[12px]" data-testid="chart-legend">
                  {spec.series.map((s, j) => (
                    <li key={j} className="flex items-center gap-1.5 text-zinc-300">
                      <Swatch color={seriesColor(j)} />
                      {s.name || `Series ${j + 1}`}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
          <p data-testid="chart-readout" className="mt-1 min-h-[1.25rem] text-[12px] tabular-nums text-zinc-300">
            {readout}
          </p>
        </div>
      )}
    </figure>
  );
}

export default ChartCard;
