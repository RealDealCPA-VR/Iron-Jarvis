"use client";

/**
 * What everything cost, by folder, client, automation and agent (next leaps
 * 46-a): the Usage page's **By** switch.
 *
 * One card, one switch (Folder | Client | Automation | Agent), one read of
 * `GET /usage/by` per choice or window (no polling: it reads again only when
 * the page's Refresh is pressed). Each row shows three figures side by side,
 * never added up: **Pay per use** (money, marked measured or estimated),
 * **Your plan** (tokens, never money) and **Free** (your own models). An
 * automation row also says how many times it asked you. The three figures
 * sit in three equal columns at every width, so nothing pans at 390 px.
 */

import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { Layers } from "lucide-react";

import { LoadFailedLine } from "@/components/LoadFailedLine";
import { Card, Empty, SkeletonRows } from "@/components/ui";
import { ViewSwitch } from "@/components/ViewSwitch";
import { useApi } from "@/lib/useApi";
import {
  EMPTY_WORDS,
  GROUP_WORDS,
  PLAN_NOTE,
  RECORD_NOTE,
  TRUNCATED_NOTE,
  UNKNOWN_PRICE,
  USAGE_BY_GROUPS,
  askedWords,
  decodeUsageBy,
  folderName,
  markWords,
  moneyWords,
  planWords,
  tokenWords,
  type UsageByGroup,
  type UsageByRow,
} from "@/lib/usageBy";

/** Rows shown before "Show all". */
export const FIRST_ROWS = 12;

const VIEWS = USAGE_BY_GROUPS.map((g) => ({ value: g, label: GROUP_WORDS[g], icon: null }));

/** The window the daemon answers for `days` (it clamps to 1..365). */
function windowDays(days: number): number {
  const n = Math.trunc(Number(days));
  return Number.isFinite(n) ? Math.min(365, Math.max(1, n)) : 30;
}

/** An answer that is not a view this card can read: the hover detail only. */
const UNREADABLE = { status: 200, message: "The answer was not one this card can read." };

function Figure({ label, children, testId }: { label: string; children: ReactNode; testId: string }) {
  return (
    <div className="min-w-0" data-testid={testId}>
      <dt className="text-[10px] uppercase tracking-wide text-zinc-500">{label}</dt>
      <dd className="mt-0.5 break-words text-xs tabular-nums text-zinc-300">{children}</dd>
    </div>
  );
}

function PayCell({ row }: { row: Pick<UsageByRow, "pay_per_use"> }) {
  const p = row.pay_per_use;
  if (p.usd > 0) {
    return (
      <>
        {moneyWords(p.usd)}{" "}
        <span data-testid="usage-by-mark" className="block text-[10px] text-zinc-500">
          {markWords(p)}
        </span>
        {p.price_unknown_tokens > 0 && (
          <span className="block text-[10px] text-zinc-500">
            {tokenWords(p.price_unknown_tokens)}, {UNKNOWN_PRICE}
          </span>
        )}
      </>
    );
  }
  if (p.price_unknown_tokens > 0) {
    return (
      <>
        {tokenWords(p.price_unknown_tokens)}
        <span className="block text-[10px] text-zinc-500">{UNKNOWN_PRICE}</span>
      </>
    );
  }
  return <span className="text-zinc-600">—</span>;
}

function RowView({ row, group }: { row: UsageByRow; group: UsageByGroup }) {
  const asked = group === "automation" ? askedWords(row.asks) : "";
  const isFolder = group === "folder" && !!row.key;
  const name = isFolder ? folderName(row.label) : row.label;
  const plan = planWords(row.plan);
  return (
    <li data-testid="usage-by-row" data-key={row.key} className="rounded-lg border border-white/[0.06] px-3 py-2.5">
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
        <span className="min-w-0 max-w-full truncate text-[13px] text-zinc-200" title={row.folder || row.label}>
          {name}
        </span>
        {asked && (
          <span data-testid="usage-by-asked" className="shrink-0 text-[11px] text-zinc-500">
            {asked}
          </span>
        )}
      </div>
      {isFolder && name !== row.label && (
        <p className="truncate text-[10px] text-zinc-600" title={row.label}>
          {row.label}
        </p>
      )}
      <dl className="mt-2 grid grid-cols-3 gap-2">
        <Figure label="Pay per use" testId="usage-by-pay">
          <PayCell row={row} />
        </Figure>
        <Figure label="Your plan" testId="usage-by-plan">
          {plan || <span className="text-zinc-600">—</span>}
        </Figure>
        <Figure label="Free" testId="usage-by-free">
          {row.free.tokens > 0 ? tokenWords(row.free.tokens) : <span className="text-zinc-600">—</span>}
        </Figure>
      </dl>
    </li>
  );
}

export function UsageBy({ days, refreshKey = 0 }: { days: number; refreshKey?: number }) {
  const [group, setGroup] = useState<UsageByGroup>("folder");
  const [all, setAll] = useState(false);
  const { data, error, loading, reload } = useApi<unknown>(`/usage/by?group=${group}&days=${days}`, [refreshKey]);
  const view = useMemo(() => decodeUsageBy(data), [data]);
  // useApi keeps the last path's answer while the next one loads: an answer
  // for another grouping or window is NOT this one, so it is never read as
  // "nothing used" (nor shown as this window's figures) while it is pending.
  const stale = !view || view.group !== group || view.days !== windowDays(days);
  const rows = !stale && view ? view.rows : [];
  const shown = all ? rows : rows.slice(0, FIRST_ROWS);
  const anyPlan = rows.some((r) => r.plan.tokens > 0);

  return (
    <Card
      title="What it cost"
      icon={<Layers size={15} />}
      right={
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-zinc-500">By</span>
          <ViewSwitch
            views={VIEWS}
            value={group}
            onChange={(g) => {
              setGroup(g);
              setAll(false);
            }}
            label="What it cost, by"
            prefix="usage-by"
          />
        </div>
      }
    >
      <div role="tabpanel" id={`usage-by-panel-${group}`} aria-labelledby={`usage-by-tab-${group}`}>
        {error ? (
          <LoadFailedLine thing="What it cost" error={error} onRetry={reload} testId="usage-by-failed" />
        ) : stale ? (
          loading ? (
            <SkeletonRows rows={3} />
          ) : (
            // Answered, but not with this grouping's rows: never "nothing used".
            <LoadFailedLine thing="What it cost" error={UNREADABLE} onRetry={reload} testId="usage-by-failed" />
          )
        ) : view?.error ? (
          <p className="py-2 text-sm text-zinc-500" data-testid="usage-by-failed">
            {view.error}
          </p>
        ) : rows.length === 0 ? (
          <Empty icon={<Layers size={24} />}>{EMPTY_WORDS}</Empty>
        ) : (
          <>
            <p className="mb-3 text-[12px] leading-relaxed text-zinc-500" data-testid="usage-by-note">
              {RECORD_NOTE}
              {anyPlan ? ` ${PLAN_NOTE}` : ""}
              {view?.truncated ? ` ${TRUNCATED_NOTE}` : ""}
            </p>
            <ul className="space-y-2" data-testid="usage-by-rows">
              {shown.map((r) => (
                <RowView key={`${group}:${r.key}:${r.label}`} row={r} group={group} />
              ))}
            </ul>
            {(rows.length > FIRST_ROWS || (view?.rest?.count ?? 0) > 0) && (
              <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[12px] text-zinc-500">
                {rows.length > FIRST_ROWS && (
                  <button
                    type="button"
                    data-testid="usage-by-more"
                    onClick={() => setAll((v) => !v)}
                    className="text-zinc-400 hover:text-zinc-200 hover:underline"
                  >
                    {all ? "Show fewer" : `Show all ${rows.length}`}
                  </button>
                )}
                {(view?.rest?.count ?? 0) > 0 && (
                  <span data-testid="usage-by-rest">
                    and {view?.rest?.count.toLocaleString()} more, each smaller
                  </span>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </Card>
  );
}

export default UsageBy;
