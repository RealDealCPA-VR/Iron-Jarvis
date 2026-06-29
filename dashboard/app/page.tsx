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
  PlugZap,
  HeartPulse,
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
  MockChip,
  SkeletonRows,
  Skeleton,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { EventStream } from "@/components/EventStream";
import { ProviderDowngradeBanner } from "@/components/ProviderDowngradeBanner";
import { OnboardingWelcome } from "@/components/OnboardingWelcome";
import { PageShell, Reveal } from "@/components/motion";
import { pct, num, timeAgo, shortId } from "@/lib/format";

type Diagnostics = {
  db_integrity?: string;
  db_bytes?: number;
  wal_bytes?: number;
  secrets_key_present?: boolean;
  secrets_key_valid?: boolean;
  running_sessions?: number;
  pending_reviews?: number;
  tracked_worktrees?: number;
};

function fmtBytes(b?: number): string {
  if (!b || b <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let n = b;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(i > 0 && n < 10 ? 1 : 0)} ${u[i]}`;
}

function HealthItem({
  label,
  value,
  status,
}: {
  label: string;
  value: string;
  status: "ok" | "bad" | "warn" | "neutral";
}) {
  const tint =
    status === "ok"
      ? "text-emerald-300"
      : status === "bad"
        ? "text-rose-300"
        : status === "warn"
          ? "text-amber-300"
          : "text-zinc-200";
  return (
    <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2">
      <div className="text-[11px] uppercase tracking-wide text-zinc-500">{label}</div>
      <div className={`mt-0.5 flex items-center gap-1.5 text-sm font-medium ${tint}`}>
        {(status === "ok" || status === "bad") && <Dot on={status === "ok"} />}
        <span className="truncate" title={value}>
          {value}
        </span>
      </div>
    </div>
  );
}

export default function OverviewPage() {
  const health = usePolledApi<Health>("/health", 5000);
  const metrics = usePolledApi<Metrics>("/metrics", 5000);
  const vault = useApi<{ providers: VaultProvider[] }>("/vault");
  const sessions = usePolledApi<{ sessions: SessionView[] }>("/sessions", 5000);
  // /diagnostics runs a full DB integrity scan — poll slowly.
  const diag = usePolledApi<Diagnostics>("/diagnostics", 30000);

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

      {/* Loud warning when a session silently fell back to the mock model. */}
      <Reveal>
        <ProviderDowngradeBanner />
      </Reveal>

      {/* First-run welcome + getting-started checklist */}
      <Reveal>
        <OnboardingWelcome />
      </Reveal>

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
                {!health.data.providers.some(
                  (p) => p.available && p.provider !== "mock" && p.class !== "mock",
                ) && (
                  <Link
                    href="/connections"
                    className="mt-1 flex items-center justify-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.08] px-3 py-2 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]"
                  >
                    <PlugZap size={14} /> Connect a real model <ArrowRight size={12} />
                  </Link>
                )}
              </div>
            ) : (
              <Empty icon={<Server size={22} />} action={{ label: "Connect a model", href: "/connections" }}>
                No provider data.
              </Empty>
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
              <Empty
                icon={<ShieldCheck size={22} />}
                action={{ label: "Connect a model", href: "/connections" }}
              >
                No vault providers configured.
              </Empty>
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
                        {s.provider === "mock" && <MockChip className="ml-auto" />}
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

      {/* System health (the /diagnostics self-test, surfaced at a glance) */}
      <Reveal>
        <Card
          title="System health"
          icon={<HeartPulse size={15} />}
          right={<span className="text-[11px] text-zinc-500">self-test</span>}
        >
          {diag.loading && !diag.data ? (
            <SkeletonRows rows={2} />
          ) : diag.data ? (
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
              <HealthItem
                label="DB integrity"
                value={diag.data.db_integrity === "ok" ? "ok" : diag.data.db_integrity || "—"}
                status={diag.data.db_integrity === "ok" ? "ok" : "bad"}
              />
              <HealthItem
                label="Secrets key"
                value={
                  diag.data.secrets_key_valid === false
                    ? "invalid"
                    : diag.data.secrets_key_present
                      ? "valid"
                      : "missing"
                }
                status={
                  diag.data.secrets_key_valid === false || !diag.data.secrets_key_present
                    ? "bad"
                    : "ok"
                }
              />
              <HealthItem
                label="WAL size"
                value={fmtBytes(diag.data.wal_bytes)}
                status={(diag.data.wal_bytes || 0) > 64 * 1024 * 1024 ? "warn" : "neutral"}
              />
              <HealthItem
                label="Running"
                value={String(diag.data.running_sessions ?? 0)}
                status="neutral"
              />
              <HealthItem
                label="Pending reviews"
                value={String(diag.data.pending_reviews ?? 0)}
                status={(diag.data.pending_reviews || 0) > 0 ? "warn" : "neutral"}
              />
              <HealthItem
                label="Worktrees"
                value={String(diag.data.tracked_worktrees ?? 0)}
                status="neutral"
              />
            </div>
          ) : (
            <Empty icon={<HeartPulse size={22} />}>No diagnostics available.</Empty>
          )}
        </Card>
      </Reveal>

      <Reveal>
        <EventStream />
      </Reveal>
    </PageShell>
  );
}
