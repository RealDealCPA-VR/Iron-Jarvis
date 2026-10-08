"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import {
  BarChart3,
  Coins,
  Hash,
  Activity,
  Cpu,
  CalendarDays,
  RefreshCw,
} from "lucide-react";
import { useApi, usePolledApi } from "@/lib/useApi";
import {
  Card,
  Stat,
  OfflineHint,
  Empty,
  SkeletonRows,
  ErrorNote,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";
import { LIST_PRICE_TITLE } from "@/lib/types";
import Link from "next/link";
import {
  baselineName,
  compactCount,
  exactCount,
  formatTokens,
  usageProviderName,
} from "@/lib/format";

/* -------------------------------------------------------------------------- */
/*  Local types (GET /usage?days=N)                                            */
/* -------------------------------------------------------------------------- */

interface UsageTotals {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  runs: number;
  /** v1.300.0: the subscription's list-price equivalent, kept OUT of
   *  `cost_usd` (which is metered money only). Absent on older daemons. */
  list_price_equivalent_usd?: number;
}

interface UsageByDay {
  day: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

interface UsageByModel {
  provider: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  runs: number;
  /** v1.300.0: the row's cost is the LIST-PRICE EQUIVALENT of a
   *  subscription's tokens (claude-cli) — not money spent. */
  list_price_equivalent?: boolean;
}

/** v1.316.0: the slice of GET /fleet/usage?days=N this page reads — the
 *  local-models estimate, always with the model it was priced against. */
interface FleetUsageEstimate {
  local_tokens?: number | null;
  est_avoided_usd?: number | null;
  comparison_provider?: string | null;
  comparison_model?: string | null;
  basis?: string | null;
}

interface UsageResponse {
  totals: UsageTotals;
  by_day: UsageByDay[];
  by_model: UsageByModel[];
  /** v1.232.0: the daemon's own count of models that did work. */
  model_count?: number;
}

/** A by-model row that is not a model the user used (v1.232.0, audit U6):
 *  the offline mock provider or a zero-token row (a probe, a refused call, a
 *  misconfigured id). The daemon filters these too; this guard keeps the page
 *  honest against an older daemon that still ships them. */
function isNoiseModel(m: UsageByModel): boolean {
  if ((m.provider ?? "").trim().toLowerCase() === "mock") return true;
  return (m.input_tokens ?? 0) + (m.output_tokens ?? 0) <= 0;
}

/* -------------------------------------------------------------------------- */
/*  Formatting helpers                                                         */
/* -------------------------------------------------------------------------- */

function usd(v: number | null | undefined): string {
  const n = typeof v === "number" && !Number.isNaN(v) ? v : 0;
  return n.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

function count(v: number | null | undefined): string {
  const n = typeof v === "number" && !Number.isNaN(v) ? v : 0;
  return n.toLocaleString();
}

/** "$7,687" — a whole-dollar estimate (cents would claim a precision an
 *  estimate does not have). */
function usdWhole(v: number): string {
  return v.toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });
}

function dayLabel(iso: string): string {
  // Accept "YYYY-MM-DD" or full ISO; show "Jun 27".
  const d = new Date(iso.length <= 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/* -------------------------------------------------------------------------- */
/*  Contribution heatmap (GitHub-style, 365 days ending today)                 */
/* -------------------------------------------------------------------------- */

/** Intensity classes, level 0 (no activity) → 4 (busiest quartile). */
const HEAT_LEVELS: readonly string[] = [
  "bg-white/[0.04]",
  "bg-accent/20",
  "bg-accent/40",
  "bg-accent/65",
  "bg-accent/90",
];

/** Gap between cells, px. The cell itself is sized by CSS (see heatVars). */
const HEAT_GAP = 3;

/** Width of the Mon/Wed/Fri label column plus its gap, px. */
const HEAT_LABEL_COL = 34;

/**
 * Fade the scroller's left edge only while older weeks are hidden there.
 * Written straight to a data attribute so scrolling never re-renders the
 * 365-cell grid.
 */
function markHeatFade(node: HTMLElement): void {
  node.dataset.fade = node.scrollLeft > 2 ? "on" : "off";
}

/** Row labels, Sun→Sat; only every other row is named, like GitHub's. */
const HEAT_DAY_LABELS = ["", "Mon", "", "Wed", "", "Fri", ""] as const;

/**
 * The heatmap's sizing, as CSS variables (v1.313.0).
 *
 * The cell used to be a fixed 11px, so on a laptop the year filled about half
 * the card and left a void on the right. Now the cell follows the card's width
 * through container query units: 11px at the smallest (a phone keeps today's
 * size and scrolls), 22px at the largest, which fills a 1440px window. It is
 * right on the FIRST paint, with no measuring script and no jump after load
 * (so the open-on-latest pin below lands where it should). The width is
 * measured on a
 * `container-type: inline-size` wrapper and these variables are declared on a
 * child of it, so `cqw` reads that wrapper.
 */
function heatVars(weeks: number): CSSProperties {
  const n = Math.max(1, weeks);
  return {
    "--heat-cell": `clamp(11px, calc((100cqw - ${HEAT_LABEL_COL}px + ${HEAT_GAP}px) / ${n} - ${HEAT_GAP}px), 22px)`,
    "--heat-pitch": `calc(var(--heat-cell) + ${HEAT_GAP}px)`,
  } as CSSProperties;
}

interface HeatDay {
  iso: string;
  /** Tooltip text, e.g. "Mar 4 — 12,340 tokens · $0.42". */
  title: string;
  month: number;
  monthLabel: string;
  level: number;
}

/** One week column, Sun→Sat; null = padding outside the 365-day window. */
type HeatWeek = (HeatDay | null)[];

interface HeatmapModel {
  weeks: HeatWeek[];
  months: { week: number; label: string }[];
}

function isoDay(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${dd}`;
}

function buildHeatmap(byDay: UsageByDay[]): HeatmapModel {
  // Index the API rows by calendar day (tokens = input + output).
  const byIso = new Map<string, { tokens: number; cost: number }>();
  for (const row of byDay) {
    const iso = row.day.slice(0, 10);
    const prev = byIso.get(iso) ?? { tokens: 0, cost: 0 };
    byIso.set(iso, {
      tokens: prev.tokens + row.input_tokens + row.output_tokens,
      cost: prev.cost + row.cost_usd,
    });
  }

  // Full 365-day series ending today; missing days are 0.
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const raw: { date: Date; iso: string; tokens: number; cost: number }[] = [];
  for (let i = 364; i >= 0; i--) {
    const date = new Date(today);
    date.setDate(today.getDate() - i);
    const iso = isoDay(date);
    const rec = byIso.get(iso);
    raw.push({ date, iso, tokens: rec?.tokens ?? 0, cost: rec?.cost ?? 0 });
  }

  // Bucket thresholds: quartiles (p25/p50/p75) of the year's NONZERO days.
  // If the distribution is too flat for distinct quartiles, fall back to
  // max/4 steps so a lone busy day still reads as level 4.
  const nonzero = raw
    .map((d) => d.tokens)
    .filter((t) => t > 0)
    .sort((a, b) => a - b);
  const quantile = (p: number): number =>
    nonzero.length > 0
      ? nonzero[Math.min(nonzero.length - 1, Math.floor(p * nonzero.length))]
      : 0;
  let t1 = quantile(0.25);
  let t2 = quantile(0.5);
  let t3 = quantile(0.75);
  if (!(t1 < t2 && t2 < t3)) {
    const max = nonzero.length > 0 ? nonzero[nonzero.length - 1] : 0;
    t1 = max / 4;
    t2 = max / 2;
    t3 = (3 * max) / 4;
  }

  const days: HeatDay[] = raw.map((d) => {
    const level =
      d.tokens === 0 ? 0 : d.tokens <= t1 ? 1 : d.tokens <= t2 ? 2 : d.tokens <= t3 ? 3 : 4;
    const dateLabel = d.date.toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
    });
    const costPart = d.cost > 0 ? ` · ${usd(d.cost)}` : "";
    return {
      iso: d.iso,
      title: `${dateLabel} — ${count(d.tokens)} tokens${costPart}`,
      month: d.date.getMonth(),
      monthLabel: d.date.toLocaleDateString(undefined, { month: "short" }),
      level,
    };
  });

  // Chunk into GitHub-style week columns (rows Sun→Sat): pad the first week
  // to Sunday and the last week out to a full column.
  const cells: (HeatDay | null)[] = [];
  const lead = raw.length > 0 ? raw[0].date.getDay() : 0;
  for (let i = 0; i < lead; i++) cells.push(null);
  cells.push(...days);
  while (cells.length % 7 !== 0) cells.push(null);
  const weeks: HeatWeek[] = [];
  for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));

  // Month labels: mark the first week where a new month begins, skipping
  // labels that would crowd the previous one (partial first month).
  const months: { week: number; label: string }[] = [];
  let prevMonth = -1;
  weeks.forEach((week, wi) => {
    const first = week.find((c): c is HeatDay => c !== null);
    if (!first || first.month === prevMonth) return;
    prevMonth = first.month;
    const last = months.length > 0 ? months[months.length - 1] : null;
    if (!last || wi - last.week >= 3) months.push({ week: wi, label: first.monthLabel });
  });

  return { weeks, months };
}

const DAY_OPTIONS = [7, 30, 90] as const;

/* -------------------------------------------------------------------------- */
/*  Over-time chart (v1.316.0)                                                 */
/* -------------------------------------------------------------------------- */

type ChartMetric = "cost" | "tokens";

interface ChartDay {
  iso: string;
  tokens: number;
  cost: number;
  /** No raw row for this calendar day — drawn as a quiet baseline tick. */
  empty: boolean;
}

/** "YYYY-MM-DD" → that LOCAL calendar day at midnight (null if unreadable). */
function localDay(iso: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(d.getTime()) ? null : d;
}

/**
 * One entry per calendar day across the window, zero-filled FOR DRAWING ONLY
 * (hasData and the empty state still read the raw by_day). It used to draw
 * only the days that had rows, evenly spaced under a "Sep 8 → Oct 7" axis,
 * so a three-week gap looked like three busy days in a row.
 *
 * The span is the selected window ending today (local), WIDENED to include
 * any raw row outside it: the daemon cuts off at now-N days in UTC, so a row
 * can sit one local day past either end, and dropping it would hide real
 * usage the totals above still count.
 */
function buildChartDays(byDay: UsageByDay[], days: number): ChartDay[] {
  const byIso = new Map<string, { tokens: number; cost: number }>();
  for (const row of byDay) {
    const iso = row.day.slice(0, 10);
    const prev = byIso.get(iso) ?? { tokens: 0, cost: 0 };
    byIso.set(iso, {
      tokens: prev.tokens + (row.input_tokens ?? 0) + (row.output_tokens ?? 0),
      cost: prev.cost + (row.cost_usd ?? 0),
    });
  }
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  let first = new Date(today);
  first.setDate(today.getDate() - (Math.max(1, days) - 1));
  let last = new Date(today);
  for (const iso of Array.from(byIso.keys())) {
    const d = localDay(iso);
    if (!d) continue;
    if (d < first) first = d;
    if (d > last) last = d;
  }
  const out: ChartDay[] = [];
  // Step by calendar date (not +86,400,000 ms) so a DST change never skips
  // or repeats a day. The cap stops a corrupt far-off row drawing forever.
  for (const d = new Date(first); d <= last && out.length < 3700; d.setDate(d.getDate() + 1)) {
    const iso = isoDay(d);
    const rec = byIso.get(iso);
    out.push({ iso, tokens: rec?.tokens ?? 0, cost: rec?.cost ?? 0, empty: !rec });
  }
  return out;
}

/** Bar height as a percentage of the window's max — one decimal, so the
 *  busiest day is exactly "100%". */
function barPct(v: number, max: number): number {
  if (!(max > 0) || !(v > 0)) return 0;
  return Math.round((v / max) * 1000) / 10;
}

export default function UsagePage() {
  const [days, setDays] = useState<number>(30);
  // v1.316.0: Cost or Tokens over time. null = not chosen yet, so the chart
  // follows the data (Tokens when nothing in the window was billed).
  const [metricPick, setMetricPick] = useState<ChartMetric | null>(null);
  const { data, error, loading, reload } = usePolledApi<UsageResponse>(
    `/usage?days=${days}`,
    15000,
  );
  // Full-year series for the contribution heatmap (DB-backed, so it covers
  // every restart — not just this run of the daemon).
  const {
    data: yearData,
    loading: yearLoading,
    reload: reloadYear,
  } = useApi<UsageResponse>("/usage?days=365");
  // v1.316.0: the local-models estimate for the SAME window, from the Fleet
  // page's own endpoint. Best-effort: if it fails, the line is simply not
  // drawn — Total cost never depends on it.
  const { data: fleet } = useApi<FleetUsageEstimate>(`/fleet/usage?days=${days}`);

  // Manual refresh: reload BOTH data sources; spin only for user-initiated
  // reloads (the 15s poll also flips `loading`, which shouldn't animate).
  const [refreshing, setRefreshing] = useState(false);
  const anyLoading = loading || yearLoading;
  useEffect(() => {
    if (!anyLoading) setRefreshing(false);
  }, [anyLoading]);
  const refresh = () => {
    setRefreshing(true);
    reload();
    reloadYear();
  };

  const offline = error && error.status === 0;
  const totals = data?.totals;
  const byDay = useMemo(() => data?.by_day ?? [], [data]);
  const byModel = useMemo(
    () =>
      (data?.by_model ?? [])
        .filter((m) => !isNoiseModel(m))
        .sort(
          (a, b) =>
            b.input_tokens + b.output_tokens - (a.input_tokens + a.output_tokens),
        ),
    [data],
  );
  const maxModelTokens = useMemo(
    () => Math.max(0, ...byModel.map((m) => m.input_tokens + m.output_tokens)),
    [byModel],
  );
  const heat = useMemo(() => buildHeatmap(yearData?.by_day ?? []), [yearData]);

  // v1.313.0: the heatmap opens on the LATEST weeks. It used to open on the
  // oldest, so on a phone it showed Oct to Mar, all empty ("you've done
  // nothing"), while the real activity sat off-screen to the right. Pinned
  // ONCE, when the scroller first appears: a refresh or a range change must
  // never pull back someone who scrolled to look at older weeks. A stable
  // callback (empty deps), so React does not call it again on every render.
  const heatPinned = useRef(false);
  const heatScrollerRef = useCallback((node: HTMLDivElement | null) => {
    if (!node || heatPinned.current) return;
    heatPinned.current = true;
    node.scrollLeft = Math.max(0, node.scrollWidth - node.clientWidth);
    markHeatFade(node);
  }, []);

  const totalTokens =
    (totals?.input_tokens ?? 0) + (totals?.output_tokens ?? 0);
  const hasData =
    !!totals && (totals.runs > 0 || byDay.length > 0 || byModel.length > 0);

  const maxDayCost = useMemo(
    () => Math.max(0, ...byDay.map((d) => d.cost_usd)),
    [byDay],
  );

  // v1.316.0: the chart. With nothing billed in the window the cost bars
  // were all the 2px floor — a big empty panel that read as broken tracking —
  // so the default series is then Tokens (Cost stays one press away).
  const chartDays = useMemo(() => buildChartDays(byDay, days), [byDay, days]);
  const noBilled = !(maxDayCost > 0);
  const metric: ChartMetric = metricPick ?? (noBilled ? "tokens" : "cost");
  const chartVal = (d: ChartDay) => (metric === "cost" ? d.cost : d.tokens);
  const chartMax = Math.max(0, ...chartDays.map(chartVal));
  const tickStep = chartDays.length <= 10 ? 1 : chartDays.length <= 45 ? 7 : 14;
  const dense = chartDays.length > 45;

  // v1.316.0: what local models did, priced against a NAMED baseline. Only
  // when the daemon priced it (> 0) AND local tokens exist AND the baseline
  // has a name — never a bare "you saved $X" (fleet.py's rule).
  const estProvider = (fleet?.comparison_provider ?? "").trim();
  const estModel = (fleet?.comparison_model ?? "").trim();
  const estUsd = fleet?.est_avoided_usd;
  const showLocalEstimate =
    typeof estUsd === "number" &&
    Number.isFinite(estUsd) &&
    estUsd > 0 &&
    (fleet?.local_tokens ?? 0) > 0 &&
    !!(estProvider || estModel);

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Usage"
          subtitle="Token spend and run volume across your providers."
          actions={
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={refresh}
                disabled={refreshing}
                title="Reload usage data"
                className="btn-ghost py-1.5 text-xs disabled:opacity-50"
              >
                <RefreshCw
                  size={14}
                  className={refreshing ? "animate-spin" : ""}
                />{" "}
                Refresh
              </button>
              <div className="flex items-center gap-1 rounded-xl border border-white/[0.08] bg-ink-900/80 p-1">
                {DAY_OPTIONS.map((d) => (
                  <button
                    key={d}
                    type="button"
                    onClick={() => setDays(d)}
                    className={`rounded-lg px-3 py-1 text-xs font-medium transition-colors ${
                      days === d
                        ? "bg-accent/15 text-accent-soft"
                        : "text-zinc-400 hover:text-zinc-200"
                    }`}
                  >
                    {d}d
                  </button>
                ))}
              </div>
            </div>
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {error && !offline && (
        <Reveal>
          <ErrorNote>{error.message}</ErrorNote>
        </Reveal>
      )}

      {/* Summary cards */}
      <Reveal>
        <div className="grid gap-4 sm:grid-cols-3">
          <Stat
            label="Total cost"
            value={usd(totals?.cost_usd)}
            sub={
              <>
                {`Last ${days} days`}
                {typeof totals?.list_price_equivalent_usd === "number" &&
                  Number.isFinite(totals.list_price_equivalent_usd) &&
                  totals.list_price_equivalent_usd > 0 && (
                    // v1.300.0: its own muted line — never added to the bill.
                    <span
                      data-testid="usage-total-list-price"
                      title={LIST_PRICE_TITLE}
                      className="block text-zinc-500"
                    >
                      ~{usd(totals.list_price_equivalent_usd)} list-price equivalent (subscription)
                    </span>
                  )}
                {/* v1.316.0: "$0.00 for 1.5 billion tokens" read as a
                    tracking bug. When local models did the work, say so —
                    with the estimate's baseline named and the raw
                    provider:model in the title. Its own line, NEVER added to
                    Total cost (the same rule as the list-price line above). */}
                {showLocalEstimate && (
                  <span
                    data-testid="usage-local-estimate"
                    title={
                      fleet?.basis ||
                      `estimate: what the local tokens would have cost on ${[estProvider, estModel]
                        .filter(Boolean)
                        .join(":")} at list price`
                    }
                    className="block text-zinc-500"
                  >
                    <Link href="/fleet" className="hover:text-zinc-300 hover:underline">
                      Local models, not billed: ~{usdWhole(estUsd as number)} vs{" "}
                      {baselineName(estProvider, estModel)} list price (estimate) →
                    </Link>
                  </span>
                )}
              </>
            }
            icon={<Coins size={16} />}
            accent
          />
          <Stat
            label="Total tokens"
            // v1.316.0: "1.52B", not a ten-digit number read digit by digit;
            // the exact counts stay one hover away.
            value={<span title={exactCount(totalTokens)}>{compactCount(totalTokens)}</span>}
            sub={
              <span
                title={`${exactCount(totals?.input_tokens)} in · ${exactCount(
                  totals?.output_tokens,
                )} out`}
              >
                {compactCount(totals?.input_tokens)} in · {compactCount(totals?.output_tokens)} out
              </span>
            }
            icon={<Hash size={16} />}
          />
          <Stat
            label="Runs"
            value={count(totals?.runs)}
            sub={`Across ${byModel.length} model${
              byModel.length === 1 ? "" : "s"
            }`}
            icon={<Activity size={16} />}
          />
        </div>
      </Reveal>

      {/* Daily activity heatmap — 12 months */}
      <Reveal>
        <Card
          title="Daily activity — last 12 months"
          icon={<CalendarDays size={15} />}
          right={
            <span className="hidden text-[11px] font-normal text-zinc-600 sm:block">
              counted from your local history — survives restarts
            </span>
          }
        >
          {yearLoading && !yearData ? (
            <SkeletonRows rows={4} />
          ) : (
            <div className="[container-type:inline-size]">
              <div className="flex gap-2" style={heatVars(heat.weeks.length)}>
                {/* Day names stay put while the weeks scroll beside them. */}
                <div
                  aria-hidden="true"
                  className="flex shrink-0 flex-col gap-[3px] pt-[22px] text-[10px] leading-none text-zinc-500"
                  style={{ width: HEAT_LABEL_COL - 8 }}
                >
                  {HEAT_DAY_LABELS.map((d, i) => (
                    <span key={i} className="flex h-[var(--heat-cell)] items-center">
                      {d}
                    </span>
                  ))}
                </div>
                {/* The left edge fades once there are older weeks to scroll
                    back to. A MASK, not a painted gradient: it changes only
                    alpha, so it reads the same in every theme. */}
                <div
                  ref={heatScrollerRef}
                  data-testid="usage-heatmap-scroller"
                  onScroll={(e) => markHeatFade(e.currentTarget)}
                  className="min-w-0 flex-1 overflow-x-auto pb-1 data-[fade=on]:[-webkit-mask-image:linear-gradient(to_right,transparent,#000_28px)] data-[fade=on]:[mask-image:linear-gradient(to_right,transparent,#000_28px)]"
                >
                  <div className="inline-block">
                    {/* Month labels along the top */}
                    <div
                      className="relative mb-1.5 h-4 text-[10px] text-zinc-500"
                      style={{ width: `calc(var(--heat-pitch) * ${heat.weeks.length})` }}
                    >
                      {heat.months.map((m) => (
                        <span
                          key={`${m.week}-${m.label}`}
                          className="absolute top-0"
                          style={{ left: `calc(var(--heat-pitch) * ${m.week})` }}
                        >
                          {m.label}
                        </span>
                      ))}
                    </div>
                    {/* Week columns, rows Sun→Sat */}
                    <div className="flex gap-[3px]">
                      {heat.weeks.map((week, wi) => (
                        <div key={wi} className="flex flex-col gap-[3px]">
                          {week.map((cell, di) =>
                            cell ? (
                              <div
                                key={cell.iso}
                                title={cell.title}
                                className={`h-[var(--heat-cell)] w-[var(--heat-cell)] rounded-sm ${
                                  HEAT_LEVELS[cell.level] ?? HEAT_LEVELS[0]
                                }`}
                              />
                            ) : (
                              <div
                                key={`pad-${wi}-${di}`}
                                className="h-[var(--heat-cell)] w-[var(--heat-cell)]"
                              />
                            ),
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              </div>
              {/* Legend */}
              <div className="mt-3 flex items-center justify-end gap-1.5 text-[10px] text-zinc-600">
                <span>Less</span>
                {HEAT_LEVELS.map((cls) => (
                  <span
                    key={cls}
                    className={`h-[11px] w-[11px] rounded-sm ${cls}`}
                  />
                ))}
                <span>More</span>
              </div>
            </div>
          )}
        </Card>
      </Reveal>

      {/* Cost / Tokens over time (v1.316.0: one bar per calendar day, a real
          scale, and Tokens when nothing in the window was billed). */}
      <Reveal>
        <Card
          title={metric === "cost" ? "Cost over time" : "Tokens over time"}
          icon={<BarChart3 size={15} />}
          right={
            <div
              role="group"
              aria-label="Chart shows"
              className="flex items-center gap-1 rounded-xl border border-white/[0.08] bg-ink-900/80 p-1"
            >
              {(["cost", "tokens"] as const).map((k) => (
                <button
                  key={k}
                  type="button"
                  aria-pressed={metric === k}
                  onClick={() => setMetricPick(k)}
                  className={`rounded-lg px-2.5 py-0.5 text-xs font-medium transition-colors ${
                    metric === k
                      ? "bg-accent/15 text-accent-soft"
                      : "text-zinc-400 hover:text-zinc-200"
                  }`}
                >
                  {k === "cost" ? "Cost" : "Tokens"}
                </button>
              ))}
            </div>
          }
        >
          {loading && !data ? (
            <SkeletonRows rows={4} />
          ) : !hasData || byDay.length === 0 ? (
            <Empty icon={<BarChart3 size={26} />}>
              No usage recorded in this window yet. Run an agent session to start
              tracking spend.
            </Empty>
          ) : (
            <>
              {noBilled && (
                // Only what is TRUE: nothing was billed. A $0 run can be a
                // subscription, a local model or a model with no known price,
                // so the caption names all three rather than guessing which.
                <p className="mb-3 text-[12px] leading-relaxed text-zinc-500">
                  No billed spend in this window. Runs on a subscription, a local model or a
                  model without a known price count as $0 here.
                </p>
              )}
              {/* The top of the scale, so a bar's height means something. It
                  sits ABOVE the plot (not over it): laid over the bars it hid
                  the busiest day on a phone. */}
              <div
                data-testid="usage-chart-max"
                className="mb-1 text-[10px] tabular-nums text-zinc-500"
              >
                {metric === "cost" ? usd(chartMax) : `${formatTokens(chartMax)} tokens`}
              </div>
              <div className="relative">
                <div className="pointer-events-none absolute inset-x-0 top-0 border-t border-dashed border-white/[0.08]" />
                <div className={`flex h-44 items-end ${dense ? "gap-px" : "gap-1"}`}>
                  {chartDays.map((d) => {
                    const pctH = barPct(chartVal(d), chartMax);
                    const value = metric === "cost" ? usd(d.cost) : formatTokens(d.tokens);
                    return (
                      <div
                        key={d.iso}
                        className={`group relative flex h-full flex-1 items-end ${
                          dense ? "min-w-0" : "min-w-[3px]"
                        }`}
                        title={`${dayLabel(d.iso)} · ${usd(d.cost)} · ${count(d.tokens)} tokens`}
                      >
                        <div
                          data-testid="usage-day-bar"
                          data-day={d.iso}
                          data-empty={d.empty ? "true" : undefined}
                          className={`w-full rounded-t-sm transition-colors duration-300 ${
                            pctH > 0
                              ? "bg-accent/45 group-hover:bg-accent/75"
                              : "bg-white/[0.07] group-hover:bg-white/[0.14]"
                          }`}
                          style={{ height: `${pctH}%`, minHeight: 2 }}
                        />
                        <span className="pointer-events-none absolute bottom-full left-1/2 mb-1 -translate-x-1/2 whitespace-nowrap rounded bg-black/80 px-1.5 py-0.5 text-[10px] text-zinc-200 opacity-0 transition-opacity group-hover:opacity-100">
                          {value}
                        </span>
                      </div>
                    );
                  })}
                </div>
                <div className="border-t border-white/[0.08]" />
              </div>
              {/* Day ticks, counted back from the newest day so it is always
                  named. */}
              <div
                aria-hidden="true"
                className={`mt-1.5 flex h-4 text-[10px] text-zinc-600 ${dense ? "gap-px" : "gap-1"}`}
              >
                {chartDays.map((d, i) => {
                  const fromEnd = chartDays.length - 1 - i;
                  const show = fromEnd % tickStep === 0;
                  return (
                    <div
                      key={d.iso}
                      className={`relative flex-1 ${dense ? "min-w-0" : "min-w-[3px]"}`}
                    >
                      {show && (
                        <span
                          className={`absolute top-0 whitespace-nowrap ${
                            fromEnd === 0 ? "right-0" : "left-1/2 -translate-x-1/2"
                          }`}
                        >
                          {dayLabel(d.iso)}
                        </span>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </Card>
      </Reveal>

      {/* By model */}
      <Reveal>
        <Card title="By model" icon={<Cpu size={15} />}>
          {loading && !data ? (
            <SkeletonRows rows={4} />
          ) : byModel.length === 0 ? (
            <Empty icon={<Cpu size={24} />}>
              No model usage in this window.
            </Empty>
          ) : (
            <div className="space-y-3.5">
              {byModel.map((m) => {
                const tokens = m.input_tokens + m.output_tokens;
                const pct =
                  maxModelTokens > 0 ? (tokens / maxModelTokens) * 100 : 0;
                return (
                  <div
                    key={`${m.provider}:${m.model}`}
                    className="group"
                    // v1.316.0: the raw "provider · model" stays here — it is
                    // the record of what ran; the label below reads words.
                    title={`${m.provider} · ${m.model || "—"} — ${count(
                      m.input_tokens,
                    )} in · ${count(m.output_tokens)} out · ${count(
                      m.runs,
                    )} run${m.runs === 1 ? "" : "s"}`}
                  >
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="truncate text-xs text-zinc-300">
                        {usageProviderName(m.provider) || m.provider} · {m.model || "—"}
                      </span>
                      <span className="shrink-0 text-right text-xs tabular-nums text-zinc-400">
                        {count(tokens)} tok
                        {m.list_price_equivalent === true ? (
                          // v1.300.0: a subscription row's cost is what the
                          // same tokens would cost at list price — said as
                          // such, never as "$0.00" and never as a bill.
                          <span
                            data-testid="usage-list-price"
                            title={LIST_PRICE_TITLE}
                            className="text-zinc-500"
                          >
                            {" "}
                            · ~{usd(m.cost_usd)} list-price equivalent
                          </span>
                        ) : m.cost_usd > 0 && (
                          <span className="text-zinc-500">
                            {" "}
                            · {usd(m.cost_usd)}
                          </span>
                        )}
                      </span>
                    </div>
                    <div className="mt-1.5 h-2.5 w-full overflow-hidden rounded-full bg-white/[0.04]">
                      <div
                        className="h-full rounded-full bg-gradient-to-r from-accent/40 to-accent/90 transition-all duration-500 group-hover:from-accent/60 group-hover:to-accent"
                        style={{ width: `${Math.max(pct, 1.5)}%` }}
                      />
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </Card>
      </Reveal>
    </PageShell>
  );
}
