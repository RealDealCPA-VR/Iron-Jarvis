"use client";

import { useState } from "react";
import { History, Download, Undo2, Coins, CircleDollarSign, ListTree } from "lucide-react";
import { API_BASE, ijToken } from "@/lib/api";
import { PageShell, Reveal } from "@/components/motion";
import { PageHeader } from "@/components/PageHeader";
import { Card, Stat } from "@/components/ui";
import { TimeTravelFeed, type FeedStats } from "@/components/TimeTravelFeed";

export default function ActivityPage() {
  const [stats, setStats] = useState<FeedStats | null>(null);

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
          subtitle="Every action, tool, token, and decision — replayable, newest first. Undo what the action allows, so handing over control feels safe."
          actions={
            <div className="flex items-center gap-2">
              <a
                href={exportUrl("md")}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-sm font-medium text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100"
              >
                <Download size={14} /> .md
              </a>
              <a
                href={exportUrl("json")}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-lg border border-white/10 px-3 py-1.5 text-sm font-medium text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100"
              >
                <Download size={14} /> .json
              </a>
            </div>
          }
        />
      </Reveal>

      <Reveal>
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          <Stat label="Events" value={eventsLabel} icon={<ListTree size={15} />} />
          <Stat
            label="Undoable now"
            value={(stats?.undoable ?? 0).toLocaleString()}
            icon={<Undo2 size={15} />}
            accent={(stats?.undoable ?? 0) > 0}
          />
          <Stat
            label="Tokens (loaded)"
            value={tokens.toLocaleString()}
            icon={<Coins size={15} />}
          />
          <Stat
            label="Cost (loaded)"
            value={`$${(stats?.costUsd ?? 0).toFixed(stats && stats.costUsd < 1 ? 4 : 2)}`}
            icon={<CircleDollarSign size={15} />}
          />
        </div>
      </Reveal>

      <Reveal>
        <Card title="Timeline" icon={<History size={15} />} pad={false}>
          <div className="p-5">
            <TimeTravelFeed onStats={setStats} />
          </div>
        </Card>
      </Reveal>
    </PageShell>
  );
}
