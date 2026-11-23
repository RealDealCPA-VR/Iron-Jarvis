"use client";

import { useCallback, useEffect, useState } from "react";
import { History, Download, Undo2, Coins, CircleDollarSign, ListTree, CalendarDays } from "lucide-react";
import { API_BASE, ijToken } from "@/lib/api";
import { PageShell, Reveal } from "@/components/motion";
import { PageHeader } from "@/components/PageHeader";
import { Card, Stat } from "@/components/ui";
import { TimeTravelFeed, type FeedStats } from "@/components/TimeTravelFeed";
import { SafetyChecksCard } from "@/components/SafetyChecks";
import { AgentHistoryCard } from "@/components/AgentHistory";
import { ViewSwitch } from "@/components/ViewSwitch";
import { DayRewind } from "@/components/activity/DayRewind";
import { usdPrecise } from "@/lib/format";
import { DAY_VIEW, TIMELINE_VIEW, todayLocal, viewFromSearch, type ActivityView } from "@/lib/dayChanges";

/** Next leaps 40-a (R2.18): the page's two tabs — the Timeline as it was,
 *  and Your day (`?view=day&date=…`). A tab, not a page of its own (G2). */
const ACTIVITY_VIEWS = [
  { value: TIMELINE_VIEW, label: "Timeline", icon: <History size={13} aria-hidden /> },
  { value: DAY_VIEW, label: "Your day", icon: <CalendarDays size={13} aria-hidden /> },
] as const;

export default function ActivityPage() {
  const [stats, setStats] = useState<FeedStats | null>(null);
  // Next leaps 40-a: which tab and which day. The first frame is the
  // Timeline (no address on the server); the address (`?view=day`, a door
  // from chat) is read once mounted, and a choice is written back so a
  // reload keeps it.
  const [view, setView] = useState<ActivityView>(TIMELINE_VIEW);
  const [day, setDay] = useState<string>(() => todayLocal());
  // Neither tab's body is drawn until the address is read, so opening
  // `?view=day` never starts the Timeline's reads for nothing (G7).
  const [ready, setReady] = useState(false);
  useEffect(() => {
    const read = () => {
      try {
        const asked = viewFromSearch(window.location.search);
        setView(asked.view ?? TIMELINE_VIEW);
        if (asked.date) setDay(asked.date);
      } catch {
        /* no address */
      }
      setReady(true);
    };
    read();
    window.addEventListener("popstate", read);
    return () => window.removeEventListener("popstate", read);
  }, []);
  const writeAddress = useCallback((nextView: ActivityView, nextDay: string) => {
    try {
      const url = new URL(window.location.href);
      if (nextView === DAY_VIEW) {
        url.searchParams.set("view", DAY_VIEW);
        if (nextDay !== todayLocal()) url.searchParams.set("date", nextDay);
        else url.searchParams.delete("date");
      } else {
        url.searchParams.delete("view");
        url.searchParams.delete("date");
      }
      window.history.replaceState(window.history.state, "", url.pathname + url.search + url.hash);
    } catch {
      /* the tab still changes */
    }
  }, []);
  const chooseView = useCallback(
    (next: ActivityView) => {
      setView(next);
      writeAddress(next, day);
    },
    [day, writeAddress],
  );
  const chooseDay = useCallback(
    (next: string) => {
      setDay(next);
      writeAddress(DAY_VIEW, next);
    },
    [writeAddress],
  );
  const onDay = view === DAY_VIEW;

  function exportUrl(format: "md" | "json") {
    const base = `${API_BASE}/audit/export?format=${format}`;
    const t = ijToken();
    return t ? `${base}&token=${encodeURIComponent(t)}` : base;
  }

  const tokens = (stats?.inputTokens ?? 0) + (stats?.outputTokens ?? 0);
  const eventsLabel =
    stats?.total != null ? stats.total.toLocaleString() : (stats?.loaded ?? 0).toLocaleString();

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Activity"
          subtitle={
            onDay
              ? "Everything Iron Jarvis changed on one day, how it was before, and Undo for one or many."
              : "Every action, tool, token, and decision — replayable, newest first. Undo what the action allows, so handing over control feels safe."
          }
          center={
            <ViewSwitch
              views={ACTIVITY_VIEWS}
              value={view}
              onChange={chooseView}
              label="Timeline or your day"
              prefix="activity-view"
            />
          }
          actions={
            onDay ? undefined : (
              <div className="flex items-center gap-2">
                <a
                  href={exportUrl("md")}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-sm font-medium text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100"
                >
                  {/* v1.314.0: say what the button does; the format stays in it. */}
                  <Download size={14} /> Export .md
                </a>
                <a
                  href={exportUrl("json")}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-sm font-medium text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100"
                >
                  <Download size={14} /> Export .json
                </a>
              </div>
            )
          }
        />
      </Reveal>

      {!ready ? null : onDay ? (
        // Next leaps 40-a: Your day. The Timeline is not mounted while this
        // tab is on screen, so nothing of it reads or listens (G7).
        <div id="activity-view-panel-day" role="tabpanel" aria-labelledby="activity-view-tab-day" className="space-y-6">
          <Reveal>
            <Card title="Your day" icon={<CalendarDays size={15} />} pad={false}>
              <div className="p-4 sm:p-5">
                <DayRewind date={day} onDate={chooseDay} />
              </div>
            </Card>
          </Reveal>
        </div>
      ) : (
        <div id="activity-view-panel-timeline" role="tabpanel" aria-labelledby="activity-view-tab-timeline" className="space-y-6">
          <Reveal>
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <Stat label="Events" value={eventsLabel} icon={<ListTree size={15} />} />
              <Stat
                label="Undoable now"
                value={(stats?.undoable ?? 0).toLocaleString()}
                icon={<Undo2 size={15} />}
                accent={(stats?.undoable ?? 0) > 0}
              />
              {/* v1.232.0 (audit U12): "(loaded)" was the engineer's word for
                  "the rows on screen" — these sum THIS VIEW, never the account
                  (that is the Usage page). */}
              <Stat
                label="Tokens in this view"
                value={tokens.toLocaleString()}
                icon={<Coins size={15} />}
              />
              {/* v1.300.0: METERED money only. A flat subscription's list-price
                  value is said apart, never summed as cost. */}
              <Stat
                label="Cost in this view"
                // v1.316.0: exactly zero reads "$0.00" like Usage (it read
                // "$0.0000"); a small non-zero cost keeps four decimals.
                value={usdPrecise(stats?.costUsd)}
                sub={
                  (stats?.listPriceUsd ?? 0) > 0
                    ? `+ ~$${(stats?.listPriceUsd ?? 0).toFixed((stats?.listPriceUsd ?? 0) < 1 ? 4 : 2)} list-price value (subscription, not billed)`
                    : undefined
                }
                icon={<CircleDollarSign size={15} />}
              />
            </div>
          </Reveal>

          {/* v1.290.0: what the agents did that looks bad — above the timeline,
              reachable as /activity#safety (the bell links here). */}
          <Reveal>
            <SafetyChecksCard />
          </Reveal>

          <Reveal>
            <Card title="Timeline" icon={<History size={15} />} pad={false}>
              <div className="p-5">
                <TimeTravelFeed onStats={setStats} />
              </div>
            </Card>
          </Reveal>

          {/* v1.290.0: Claude Code / Codex sessions on this PC, read-only. */}
          <Reveal>
            <AgentHistoryCard />
          </Reveal>
        </div>
      )}
    </PageShell>
  );
}
