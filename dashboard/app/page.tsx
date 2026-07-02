"use client";

import { useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
  Rocket,
  FolderSearch,
  Sparkles,
  ScrollText,
  Mail,
  History,
  LayoutGrid,
  Play,
  BookMarked,
} from "lucide-react";
import { usePolledApi, useApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import { post, ApiError } from "@/lib/api";
import type { Health, Metrics, VaultProvider, SessionView, IJEvent } from "@/lib/types";
import {
  Card,
  Stat,
  Badge,
  StatusDot,
  StatusIcon,
  Dot,
  Spinner,
  OfflineHint,
  Empty,
  MockChip,
  SkeletonRows,
  Skeleton,
  LoaderInline,
  ErrorNote,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { EventStream } from "@/components/EventStream";
import { ProviderDowngradeBanner } from "@/components/ProviderDowngradeBanner";
import { OnboardingWelcome } from "@/components/OnboardingWelcome";
import { PageShell, Reveal } from "@/components/motion";
import { pct, num, timeAgo, clockTime, shortId } from "@/lib/format";

type Diagnostics = {
  db_integrity?: string;
  db_bytes?: number;
  wal_bytes?: number;
  secrets_key_present?: boolean;
  secrets_key_valid?: boolean;
  running_sessions?: number;
  pending_reviews?: number;
  tracked_worktrees?: number;
  background_loops?: Record<string, { ok?: boolean; error?: string }>;
};

/** A saved reusable task from GET /templates (mirrors the Templates page). */
interface Template {
  id: string;
  name: string;
  agent_type: string;
  task: string;
  provider?: string | null;
  model?: string | null;
  created_at: string;
}

/** One-click, broadly-safe starter tasks that take a first-time user straight to
 *  a real result. Clicking POSTs /sessions (wait:false) and opens the live run. */
const FIRST_WIN_TASKS: {
  key: string;
  title: string;
  task: string;
  icon: ReactNode;
}[] = [
  {
    key: "downloads",
    title: "Tidy my Downloads",
    task: "List the largest files in my Downloads folder and suggest what's safe to delete",
    icon: <FolderSearch size={18} />,
  },
  {
    key: "examples",
    title: "What can you do?",
    task: "Give me 5 example tasks you can do for me right now",
    icon: <Sparkles size={18} />,
  },
  {
    key: "recap",
    title: "Recap today",
    task: "Summarize today: what sessions ran and what happened",
    icon: <ScrollText size={18} />,
  },
  {
    key: "email",
    title: "Draft a follow-up",
    task: "Draft a polite follow-up email to a client who hasn't replied",
    icon: <Mail size={18} />,
  },
];

/** Event types that describe an agent starting or finishing a run. */
const LIVE_EVENT_TYPES = new Set(["agent.started", "agent.completed"]);

/** Short, human-ish label for a live activity row. */
function eventLabel(e: IJEvent): string {
  const p = e.payload || {};
  const pick = (k: string) => (p[k] != null ? String(p[k]) : "");
  return (
    pick("summary") ||
    pick("task") ||
    pick("agent_type") ||
    pick("name") ||
    (e.session_id ? e.session_id.slice(0, 8) : "activity")
  );
}

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
      <div className="text-[11px] uppercase tracking-wide text-zinc-400">{label}</div>
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

  const router = useRouter();
  const templates = useApi<{ templates: Template[] }>("/templates");
  const { events, connected } = useEvents(40);

  const offline = health.error && health.error.status === 0;
  const m = metrics.data;

  // Which first-win / template tile is currently starting a session.
  const [starting, setStarting] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  // One click → a real result: start the agent in the background (wait:false) and
  // jump to its detail page so the user watches it run live.
  async function startTask(task: string, key: string, agentType = "builder") {
    if (starting) return;
    setStarting(key);
    setStartError(null);
    try {
      const s = await post<SessionView>("/sessions", {
        task,
        agent_type: agentType,
        wait: false,
      });
      if (s?.id) {
        router.push(`/sessions/${s.id}`);
        return; // keep the spinner up while we navigate away
      }
      setStarting(null);
    } catch (err) {
      setStartError(err instanceof ApiError ? err.message : String(err));
      setStarting(null);
    }
  }

  // Recently finished sessions ("while you were away"), newest first.
  const finished = useMemo(() => {
    const all = sessions.data?.sessions ?? [];
    return [...all]
      .filter((s) => {
        const st = s.status.toLowerCase();
        return st === "completed" || st === "failed";
      })
      .sort(
        (a, b) =>
          new Date(b.finished_at || b.created_at).getTime() -
          new Date(a.finished_at || a.created_at).getTime(),
      )
      .slice(0, 6);
  }, [sessions.data]);

  // Latest agent start/finish lines from the live stream.
  const liveEvents = useMemo(
    () => events.filter((e) => LIVE_EVENT_TYPES.has(e.type)).slice(0, 3),
    [events],
  );

  const templateList = templates.data?.templates ?? [];

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

      {/* First-win: one click → a real result. */}
      <Reveal>
        <Card
          title="Try it now"
          icon={<Rocket size={15} />}
          right={<span className="text-[11px] text-zinc-500">one click → your first result</span>}
        >
          {startError && (
            <div className="mb-3">
              <ErrorNote>{startError}</ErrorNote>
            </div>
          )}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {FIRST_WIN_TASKS.map((t) => {
              const busy = starting === t.key;
              return (
                <button
                  key={t.key}
                  type="button"
                  disabled={!!starting}
                  onClick={() => startTask(t.task, t.key)}
                  className="group relative flex h-full flex-col gap-3 overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 text-left transition-all duration-300 hover:-translate-y-0.5 hover:border-accent/30 hover:bg-accent/[0.04] hover:shadow-card-hover disabled:pointer-events-none disabled:opacity-60"
                >
                  <span className="pointer-events-none absolute -right-6 -top-8 h-24 w-24 rounded-full bg-accent/10 opacity-0 blur-2xl transition-opacity duration-300 group-hover:opacity-100" />
                  <span className="flex h-9 w-9 items-center justify-center rounded-xl border border-accent/20 bg-accent/[0.08] text-accent-soft">
                    {t.icon}
                  </span>
                  <div className="flex-1">
                    <div className="text-sm font-semibold text-zinc-100">{t.title}</div>
                    <p className="mt-1 line-clamp-3 text-xs leading-relaxed text-zinc-500">
                      {t.task}
                    </p>
                  </div>
                  <span className="flex items-center gap-1.5 text-xs font-medium text-accent-soft">
                    {busy ? (
                      <LoaderInline label="Starting…" />
                    ) : (
                      <>
                        Run
                        <ArrowRight
                          size={13}
                          className="transition-transform group-hover:translate-x-0.5"
                        />
                      </>
                    )}
                  </span>
                </button>
              );
            })}
          </div>
        </Card>
      </Reveal>

      {/* While you were away — live activity + recently finished sessions. */}
      <Reveal>
        <Card
          title="While you were away"
          icon={<History size={15} />}
          right={
            <span className="flex items-center gap-2 text-xs text-zinc-500">
              <Dot on={connected} />
              {connected ? "live" : "offline"}
            </span>
          }
        >
          {liveEvents.length > 0 && (
            <div className="mb-3 space-y-1.5">
              {liveEvents.map((e) => {
                const running = e.type === "agent.started";
                const row = (
                  <span className="flex items-center gap-2.5">
                    <StatusDot status={running ? "running" : "completed"} />
                    <span className="shrink-0 font-medium text-zinc-200">
                      {running ? "Running now" : "Just finished"}
                    </span>
                    <span className="truncate text-zinc-500">{eventLabel(e)}</span>
                    <span className="ml-auto shrink-0 tabular-nums text-[11px] text-zinc-600">
                      {clockTime(e.ts)}
                    </span>
                  </span>
                );
                return e.session_id ? (
                  <Link
                    key={e.id}
                    href={`/sessions/${e.session_id}`}
                    className="block rounded-xl border border-accent/20 bg-accent/[0.05] px-3 py-2 text-xs transition-colors hover:bg-accent/[0.09]"
                  >
                    {row}
                  </Link>
                ) : (
                  <div
                    key={e.id}
                    className="rounded-xl border border-accent/20 bg-accent/[0.05] px-3 py-2 text-xs"
                  >
                    {row}
                  </div>
                );
              })}
            </div>
          )}

          {sessions.loading && !sessions.data ? (
            <SkeletonRows rows={4} />
          ) : finished.length > 0 ? (
            <ul className="space-y-2">
              {finished.map((s) => (
                <li key={s.id}>
                  <Link
                    href={`/sessions/${s.id}`}
                    className="block rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2 transition-colors hover:border-white/[0.12] hover:bg-white/[0.05]"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex min-w-0 items-center gap-2">
                        <StatusIcon status={s.status} size={14} />
                        <span className="truncate text-sm text-zinc-200">
                          {s.task || "Untitled session"}
                        </span>
                      </span>
                      <Badge value={s.status} />
                    </div>
                    <div className="mt-1 flex items-center gap-2 pl-6 text-[11px] text-zinc-500">
                      <span>{timeAgo(s.finished_at || s.created_at)}</span>
                      {s.summary && (
                        <>
                          <span>·</span>
                          <span className="truncate">{s.summary}</span>
                        </>
                      )}
                      {s.provider === "mock" && <MockChip className="ml-auto" />}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          ) : liveEvents.length === 0 ? (
            <Empty icon={<History size={22} />}>Nothing yet — try a task above.</Empty>
          ) : null}
        </Card>
      </Reveal>

      {/* Your apps — one-click tiles from saved templates (omitted when none). */}
      {templateList.length > 0 && (
        <Reveal>
          <Card
            title="Your apps"
            icon={<LayoutGrid size={15} />}
            right={
              <Link
                href="/templates"
                className="flex items-center gap-1 text-xs text-accent-soft transition-colors hover:text-accent"
              >
                manage <ArrowRight size={12} />
              </Link>
            }
          >
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {templateList.map((t) => {
                const key = `tpl-${t.id}`;
                const busy = starting === key;
                return (
                  <button
                    key={t.id}
                    type="button"
                    disabled={!!starting}
                    onClick={() => startTask(t.task, key, t.agent_type || "builder")}
                    title={t.task}
                    className="group flex items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 text-left transition-all duration-300 hover:-translate-y-0.5 hover:border-violet-500/30 hover:bg-violet-500/[0.04] disabled:pointer-events-none disabled:opacity-60"
                  >
                    <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-violet-500/20 bg-violet-500/[0.08] text-violet-300">
                      {busy ? <LoaderInline /> : <BookMarked size={16} />}
                    </span>
                    <div className="min-w-0 flex-1">
                      <div className="truncate text-sm font-medium text-zinc-100">{t.name}</div>
                      <p className="truncate text-xs text-zinc-500">{t.task}</p>
                    </div>
                    <Play
                      size={14}
                      className="shrink-0 text-zinc-600 transition-colors group-hover:text-violet-300"
                    />
                  </button>
                );
              })}
            </div>
          </Card>
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
              {(() => {
                const loops = diag.data.background_loops ?? {};
                const bad = Object.entries(loops).filter(([, v]) => v && v.ok === false);
                return (
                  <HealthItem
                    label="Boot loops"
                    value={bad.length ? `${bad.length} failed` : "ok"}
                    status={bad.length ? "bad" : "ok"}
                  />
                );
              })()}
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
