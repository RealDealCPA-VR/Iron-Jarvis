"use client";

/**
 * The four run-quality numbers, as ONE card (v1.151.0).
 *
 * They were four separate `Stat` tiles, each with its own surface, glow and
 * padding — which gave four unrelated-looking objects the same visual weight as
 * a whole module, and pushed everything else below the fold. They are one
 * thought ("how is it actually performing?"), so they are now one object: a
 * single card with four columns, hairline-separated.
 *
 * Honesty rules kept from the old tiles: a missing metric renders "—" rather
 * than a zero (nothing measured is not the same as measured zero), and the
 * sample counts ride along, because a percentage without its denominator is a
 * number you cannot act on.
 */

import { Activity, Gauge, Wrench, Timer } from "lucide-react";
import { Skeleton } from "@/components/ui";

export interface OverviewMetrics {
  sessions_evaluated: number;
  avg_completion: number | null;
  avg_tool_success_rate: number | null;
  avg_latency_s: number | null;
  total_tool_invocations: number;
  event_count: number;
}

function Figure({
  icon,
  label,
  value,
  sub,
  loading,
}: {
  icon: React.ReactNode;
  label: string;
  value: React.ReactNode;
  sub?: string;
  loading?: boolean;
}) {
  return (
    <div className="flex-1 px-4 py-3.5">
      <div className="flex items-center gap-1.5 text-[10.5px] font-medium uppercase tracking-[0.12em] text-zinc-500">
        <span className="text-zinc-600">{icon}</span>
        {label}
      </div>
      <div className="mt-1.5 text-2xl font-semibold tracking-tight text-zinc-50">
        {loading ? <Skeleton className="h-7 w-16" /> : value}
      </div>
      {sub && <div className="mt-0.5 text-[11px] text-zinc-600">{sub}</div>}
    </div>
  );
}

export function HealthCard({
  metrics,
  loading,
}: {
  metrics: OverviewMetrics | null;
  loading: boolean;
}) {
  const m = metrics;
  // "—" not "0". Nothing measured yet is a different fact from a measured
  // zero, and "0% completion / 0% tool success" on a fresh install reads as
  // "this thing fails at everything" when the truth is "no runs yet".
  //
  // The daemon sends 0.0 rather than null for these averages, so the honest
  // signal is the DENOMINATOR: with no evaluated sessions the averages have
  // nothing behind them regardless of what number arrives. Same for latency,
  // which is averaged over the same runs.
  const measured = (m?.sessions_evaluated ?? 0) > 0;
  const pct = (v: number | null | undefined) =>
    !measured || v === null || v === undefined ? "—" : `${Math.round(v * 100)}%`;
  const secs = (v: number | null | undefined) =>
    !measured || v === null || v === undefined ? "—" : `${v.toFixed(1)}s`;

  return (
    <div className="card-surface overflow-hidden !p-0">
      <div className="flex flex-col divide-y divide-white/[0.05] sm:flex-row sm:divide-x sm:divide-y-0">
        <Figure
          icon={<Activity size={12} />}
          label="Sessions evaluated"
          value={m ? Number(m.sessions_evaluated ?? 0).toLocaleString() : "—"}
          sub={m ? `${Number(m.event_count ?? 0).toLocaleString()} events` : undefined}
          loading={loading && !m}
        />
        <Figure
          icon={<Gauge size={12} />}
          label="Avg completion"
          value={m ? pct(m.avg_completion) : "—"}
          sub={
            m
              ? measured
                ? `over ${m.sessions_evaluated} evaluated run${
                    m.sessions_evaluated === 1 ? "" : "s"
                  }`
                : "no runs evaluated yet"
              : undefined
          }
          loading={loading && !m}
        />
        <Figure
          icon={<Wrench size={12} />}
          label="Tool success"
          value={m ? pct(m.avg_tool_success_rate) : "—"}
          sub={
            m
              ? `${Number(m.total_tool_invocations ?? 0).toLocaleString()} tool call${
                  m.total_tool_invocations === 1 ? "" : "s"
                }`
              : undefined
          }
          loading={loading && !m}
        />
        <Figure
          icon={<Timer size={12} />}
          label="Avg latency"
          value={m ? secs(m.avg_latency_s) : "—"}
          sub="per completed run"
          loading={loading && !m}
        />
      </div>
    </div>
  );
}
