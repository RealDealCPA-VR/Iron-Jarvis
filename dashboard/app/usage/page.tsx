"use client";

import { useMemo, useState } from "react";
import { BarChart3, Coins, Hash, Activity, Cpu } from "lucide-react";
import { usePolledApi } from "@/lib/useApi";
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

const DAY_OPTIONS = [7, 30, 90] as const;

export default function UsagePage() {
  const [days, setDays] = useState<number>(30);
  const { data, error, loading } = usePolledApi<UsageResponse>(
    `/usage?days=${days}`,
    15000,
  );

  const offline = error && error.status === 0;
  const totals = data?.totals;
  const byDay = useMemo(() => data?.by_day ?? [], [data]);
  const byModel = useMemo(
    () => [...(data?.by_model ?? [])].sort((a, b) => b.cost_usd - a.cost_usd),
    [data],
  );

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
            <div className="-mx-1 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                    <th className="px-2 py-2.5 font-medium">Model</th>
                    <th className="px-2 py-2.5 text-right font-medium">Runs</th>
                    <th className="px-2 py-2.5 text-right font-medium">Input</th>
                    <th className="px-2 py-2.5 text-right font-medium">Output</th>
                    <th className="px-2 py-2.5 text-right font-medium">Cost</th>
                  </tr>
                </thead>
                <tbody>
                  {byModel.map((m) => (
                    <tr
                      key={`${m.provider}:${m.model}`}
                      className="border-b border-white/[0.04] transition-colors last:border-0 hover:bg-white/[0.03]"
                    >
                      <td className="px-2 py-2.5">
                        <div className="text-zinc-100">{m.model || "—"}</div>
                        <div className="text-[11px] text-zinc-600">
                          {m.provider}
                        </div>
                      </td>
                      <td className="px-2 py-2.5 text-right text-zinc-400">
                        {count(m.runs)}
                      </td>
                      <td className="px-2 py-2.5 text-right text-zinc-400">
                        {count(m.input_tokens)}
                      </td>
                      <td className="px-2 py-2.5 text-right text-zinc-400">
                        {count(m.output_tokens)}
                      </td>
                      <td className="px-2 py-2.5 text-right font-medium text-zinc-100">
                        {usd(m.cost_usd)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </Reveal>
    </PageShell>
  );
}
