"use client";

import { useMemo, useState } from "react";
import { BarChart3, Coins, Hash, Activity, Cpu, CalendarDays } from "lucide-react";
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

/* -------------------------------------------------------------------------- */
/*  Local types (GET /usage?days=N)                                            */
/* -------------------------------------------------------------------------- */

interface UsageTotals {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
  runs: number;
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
}

interface UsageResponse {
  totals: UsageTotals;
  by_day: UsageByDay[];
  by_model: UsageByModel[];
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

/** Column pitch in px: 11px cell + 3px gap. */
const HEAT_PITCH = 14;

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

export default function UsagePage() {
  const [days, setDays] = useState<number>(30);
  const { data, error, loading } = usePolledApi<UsageResponse>(
    `/usage?days=${days}`,
    15000,
  );
  // Full-year series for the contribution heatmap (DB-backed, so it covers
  // every restart — not just this run of the daemon).
  const { data: yearData, loading: yearLoading } = useApi<UsageResponse>(
    "/usage?days=365",
  );

  const offline = error && error.status === 0;
  const totals = data?.totals;
  const byDay = useMemo(() => data?.by_day ?? [], [data]);
  const byModel = useMemo(
    () =>
      [...(data?.by_model ?? [])].sort(
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

  const totalTokens =
    (totals?.input_tokens ?? 0) + (totals?.output_tokens ?? 0);
  const hasData =
    !!totals && (totals.runs > 0 || byDay.length > 0 || byModel.length > 0);

  const maxDayCost = useMemo(
    () => Math.max(0, ...byDay.map((d) => d.cost_usd)),
    [byDay],
  );

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Usage"
          subtitle="Token spend and run volume across your providers."
          actions={
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
            sub={`Last ${days} days`}
            icon={<Coins size={16} />}
            accent
          />
          <Stat
            label="Total tokens"
            value={count(totalTokens)}
            sub={`${count(totals?.input_tokens)} in · ${count(
              totals?.output_tokens,
            )} out`}
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
            <div>
              <div className="overflow-x-auto pb-1">
                <div className="inline-block">
                  {/* Month labels along the top */}
                  <div
                    className="relative mb-1.5 h-4 text-[10px] text-zinc-500"
                    style={{ width: heat.weeks.length * HEAT_PITCH }}
                  >
                    {heat.months.map((m) => (
                      <span
                        key={`${m.week}-${m.label}`}
                        className="absolute top-0"
                        style={{ left: m.week * HEAT_PITCH }}
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
                              className={`h-[11px] w-[11px] rounded-sm ${
                                HEAT_LEVELS[cell.level] ?? HEAT_LEVELS[0]
                              }`}
                            />
                          ) : (
                            <div
                              key={`pad-${wi}-${di}`}
                              className="h-[11px] w-[11px]"
                            />
                          ),
                        )}
                      </div>
                    ))}
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

      {/* Cost over time */}
      <Reveal>
        <Card title="Cost over time" icon={<BarChart3 size={15} />}>
          {loading && !data ? (
            <SkeletonRows rows={4} />
          ) : !hasData || byDay.length === 0 ? (
            <Empty icon={<BarChart3 size={26} />}>
              No usage recorded in this window yet. Run an agent session to start
              tracking spend.
            </Empty>
          ) : (
            <div className="flex h-44 items-end gap-1 overflow-x-auto pb-1">
              {byDay.map((d) => {
                const h =
                  maxDayCost > 0
                    ? Math.max(2, Math.round((d.cost_usd / maxDayCost) * 100))
                    : 2;
                return (
                  <div
                    key={d.day}
                    className="group flex min-w-[10px] flex-1 flex-col items-center justify-end gap-1.5"
                    title={`${dayLabel(d.day)} · ${usd(d.cost_usd)} · ${count(
                      d.input_tokens + d.output_tokens,
                    )} tokens`}
                  >
                    <div className="relative flex w-full items-end justify-center">
                      <div
                        className="w-full rounded-t-sm bg-accent/40 transition-all duration-300 group-hover:bg-accent/70"
                        style={{ height: `${h}%`, minHeight: 2 }}
                      />
                      <span className="pointer-events-none absolute -top-5 whitespace-nowrap rounded bg-black/80 px-1.5 py-0.5 text-[10px] text-zinc-200 opacity-0 transition-opacity group-hover:opacity-100">
                        {usd(d.cost_usd)}
                      </span>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {hasData && byDay.length > 0 && (
            <div className="mt-2 flex justify-between text-[11px] text-zinc-600">
              <span>{dayLabel(byDay[0].day)}</span>
              <span>{dayLabel(byDay[byDay.length - 1].day)}</span>
            </div>
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
                    title={`${m.provider} · ${m.model || "—"} — ${count(
                      m.input_tokens,
                    )} in · ${count(m.output_tokens)} out · ${count(
                      m.runs,
                    )} run${m.runs === 1 ? "" : "s"}`}
                  >
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="truncate font-mono text-xs text-zinc-300">
                        {m.provider} · {m.model || "—"}
                      </span>
                      <span className="shrink-0 text-right text-xs tabular-nums text-zinc-400">
                        {count(tokens)} tok
                        {m.cost_usd > 0 && (
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
