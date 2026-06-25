"use client";

import Link from "next/link";
import {
  Activity,
  Gauge,
  Wrench,
  Timer,
  Server,
  ShieldCheck,
  Boxes,
  ArrowRight,
} from "lucide-react";
import { usePolledApi, useApi } from "@/lib/useApi";
import type { Health, Metrics, VaultProvider, SessionView } from "@/lib/types";
import {
  Card,
  Stat,
  Badge,
  StatusDot,
  Dot,
  Spinner,
  OfflineHint,
  Empty,
  SkeletonRows,
  Skeleton,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { EventStream } from "@/components/EventStream";
import { PageShell, Reveal } from "@/components/motion";
import { pct, num, timeAgo, shortId } from "@/lib/format";

export default function OverviewPage() {
  const health = usePolledApi<Health>("/health", 5000);
  const metrics = usePolledApi<Metrics>("/metrics", 5000);
  const vault = useApi<{ providers: VaultProvider[] }>("/vault");
  const sessions = usePolledApi<{ sessions: SessionView[] }>("/sessions", 5000);

  const offline = health.error && health.error.status === 0;
  const m = metrics.data;

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Overview"
          subtitle="Health, metrics, and live activity for the Iron Jarvis daemon."
          actions={
            health.data ? (
              <span className="flex items-center gap-2 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-1.5 text-xs text-zinc-300">
                <Dot on={health.data.status === "ok"} />
                <span className="text-zinc-400">v{health.data.version}</span>
              </span>
            ) : null
          }
        />
      </Reveal>

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {/* Metric cards */}
      <Reveal>
        <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
          <Stat
            label="Sessions evaluated"
            icon={<Activity size={16} />}
            accent
            value={m ? m.sessions_evaluated : metrics.loading ? <Skeleton className="h-8 w-12" /> : "—"}
          />
          <Stat
            label="Avg completion"
            icon={<Gauge size={16} />}
            value={m ? pct(m.avg_completion) : metrics.loading ? <Skeleton className="h-8 w-16" /> : "—"}
          />
          <Stat
            label="Tool success"
            icon={<Wrench size={16} />}
            value={m ? pct(m.avg_tool_success_rate) : metrics.loading ? <Skeleton className="h-8 w-16" /> : "—"}
          />
          <Stat
            label="Avg latency"
            icon={<Timer size={16} />}
            value={m ? `${num(m.avg_latency_s)}s` : metrics.loading ? <Skeleton className="h-8 w-16" /> : "—"}
            sub={m ? `${m.total_tool_invocations} tool calls · ${m.event_count} events` : undefined}
          />
        </div>
      </Reveal>

      <Reveal>
        <div className="grid gap-4 lg:grid-cols-3">
          {/* Providers */}
          <Card title="Providers" icon={<Server size={15} />}>
            {health.loading && !health.data ? (
              <SkeletonRows rows={3} />
            ) : health.data ? (
              <div className="space-y-2">
                <div className="mb-2 text-xs text-zinc-500">
                  default{" "}
                  <span className="font-mono text-zinc-300">
                    {health.data.default_provider} / {health.data.default_model}
                  </span>
                </div>
                {health.data.providers.map((p) => (
                  <div
                    key={p.provider}
                    className="flex items-center justify-between rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2 text-sm"
                  >
                    <span className="flex items-center gap-2">
                      <Dot on={p.available} />
                      <span className="text-zinc-200">{p.provider}</span>
                    </span>
                    <span className="font-mono text-xs text-zinc-500">{p.class}</span>
                  </div>
                ))}
              </div>
            ) : (
              <Empty icon={<Server size={22} />}>No provider data.</Empty>
            )}
          </Card>

          {/* Vault */}
          <Card title="Browser vault" icon={<ShieldCheck size={15} />}>
            {vault.loading && !vault.data ? (
              <SkeletonRows rows={3} />
            ) : vault.data && vault.data.providers.length > 0 ? (
              <div className="space-y-2">
                {vault.data.providers.map((p) => (
                  <div
                    key={p.provider}
                    className="flex items-center justify-between rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2 text-sm"
                  >
                    <span className="text-zinc-200">{p.provider}</span>
                    <Badge value={p.logged_in ? "logged in" : "logged out"} />
                  </div>
                ))}
              </div>
            ) : (
              <Empty icon={<ShieldCheck size={22} />}>No vault providers configured.</Empty>
            )}
          </Card>

          {/* Recent sessions */}
          <Card
            title="Recent sessions"
            icon={<Boxes size={15} />}
            right={
              <Link
                href="/sessions"
                className="flex items-center gap-1 text-xs text-accent-soft transition-colors hover:text-accent"
              >
                view all <ArrowRight size={12} />
              </Link>
            }
          >
            {sessions.loading && !sessions.data ? (
              <SkeletonRows rows={4} />
            ) : sessions.data && sessions.data.sessions.length > 0 ? (
              <ul className="space-y-2">
                {sessions.data.sessions.slice(0, 6).map((s) => (
                  <li key={s.id}>
                    <Link
                      href={`/sessions/${s.id}`}
                      className="block rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2 transition-colors hover:border-white/[0.12] hover:bg-white/[0.05]"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="flex min-w-0 items-center gap-2">
                          <StatusDot status={s.status} />
                          <span className="truncate text-sm text-zinc-200">{s.task}</span>
                        </span>
                        <Badge value={s.status} />
                      </div>
                      <div className="mt-1 flex items-center gap-2 pl-4 text-[11px] text-zinc-500">
                        <span className="font-mono">{shortId(s.id)}</span>
                        <span>·</span>
                        <span>{s.agent_type}</span>
                        <span>·</span>
                        <span>{timeAgo(s.created_at)}</span>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            ) : sessions.loading ? (
              <Spinner />
            ) : (
              <Empty icon={<Boxes size={22} />}>No sessions yet.</Empty>
            )}
          </Card>
        </div>
      </Reveal>

      <Reveal>
        <EventStream />
      </Reveal>
    </PageShell>
  );
}
