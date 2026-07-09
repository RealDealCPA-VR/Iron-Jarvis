"use client";

import { useEffect, useMemo, useState, type ReactNode } from "react";
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
  MessageSquare,
  SquareTerminal,
  FolderKanban,
  Images,
  Cpu,
  HardDrive,
  Zap,
  AlertTriangle,
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
import { MoodOrb } from "@/components/MoodOrb";
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

/** GET /diagnostics/reliability — free disk + recent provider failures (24h). */
type Reliability = {
  disk?: { free?: number; total?: number };
  recent_provider_failures?: number;
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

/** Interactive shortcuts into the four hero surfaces + Creative. Real hrefs. */
const QUICK_ACTIONS: {
  href: string;
  title: string;
  desc: string;
  icon: ReactNode;
}[] = [
  { href: "/chat", title: "New chat", desc: "Talk to Iron Jarvis", icon: <MessageSquare size={18} /> },
  { href: "/sessions", title: "New session", desc: "Run an agent task", icon: <Boxes size={18} /> },
  { href: "/projects", title: "Open a project", desc: "Your context spine", icon: <FolderKanban size={18} /> },
  { href: "/creative", title: "Creative", desc: "Images, video, audio", icon: <Images size={18} /> },
  { href: "/terminals", title: "Build", desc: "Terminals & AI CLIs", icon: <SquareTerminal size={18} /> },
];

/** Event types that describe an agent starting or finishing a run. */
const LIVE_EVENT_TYPES = new Set(["agent.started", "agent.completed"]);

/** Just the reflex-rule fields the Overview needs (see GET /reflex/rules). */
type ReflexRuleLite = {
  id: string;
  name: string;
  source: string;
  action: string;
  enabled: boolean;
};

/** The truthful state of a live-activity row. */
type LiveState = "running" | "completed" | "failed";

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

/** A compact stat tile for the hero status band. */
function HeroStat({
  label,
  value,
  icon,
  tone = "neutral",
  title,
}: {
  label: string;
  value: ReactNode;
  icon: ReactNode;
  tone?: "neutral" | "accent" | "bad";
  title?: string;
}) {
  const tint =
    tone === "bad" ? "text-rose-300" : tone === "accent" ? "text-accent-soft" : "text-zinc-100";
  return (
    <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.12em] text-zinc-500">
        <span className="text-accent-soft/70">{icon}</span>
        {label}
      </div>
      <div className={`mt-1 truncate text-sm font-semibold ${tint}`} title={title}>
        {value}
      </div>
    </div>
  );
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
  // Reliability signal (free disk + recent provider failures) — read-only.
  const reliability = usePolledApi<Reliability>("/diagnostics/reliability", 30000);

  const router = useRouter();
  const templates = useApi<{ templates: Template[] }>("/templates");
  // Ambient Operator: the enabled reflex rules (signal→action bindings) so the
  // Overview shows what Iron Jarvis will do on its own, and recent fires.
  const reflexes = usePolledApi<{ rules: ReflexRuleLite[] }>("/reflex/rules", 15000);
  const { events, connected } = useEvents(40);

  // Respect the Sidebar's Simple/Advanced mode (seeded Simple for stable SSR,
  // hydrated from localStorage). Advanced reveals the deeper telemetry sections.
  const [advanced, setAdvanced] = useState(false);
  useEffect(() => {
    try {
      setAdvanced(localStorage.getItem("ij_nav_advanced") === "1");
    } catch {
      /* localStorage unavailable — stay in Simple mode. */
    }
  }, []);

  const offline = health.error && health.error.status === 0;
  const m = metrics.data;

  // Which first-win / template tile is currently starting a session.
  const [starting, setStarting] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  // One click → a real result: start the agent in the background (wait:false) and
  // jump to its detail page so the user watches it run live.
  async function startTask(
    task: string,
    key: string,
    agentType = "builder",
    provider?: string | null,
    model?: string | null,
  ) {
    if (starting) return;
    setStarting(key);
    setStartError(null);
    try {
      const s = await post<SessionView>("/sessions", {
        task,
        agent_type: agentType,
        wait: false,
        // Carry a template's pinned provider/model into the run (else dropped).
        ...(provider ? { provider } : {}),
        ...(model ? { model } : {}),
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

  // Live agent start/finish rows — TRUTHFUL: one row per run, newest event wins.
  // Because events arrive newest-first, the first event seen for a run is its
  // latest state, so a run that already completed never also shows as "Running".
  const liveEvents = useMemo(() => {
    const seen = new Set<string>();
    const rows: {
      id: string;
      session_id: string | null;
      label: string;
      state: LiveState;
      ts: string;
    }[] = [];
    for (const e of events) {
      if (!LIVE_EVENT_TYPES.has(e.type)) continue;
      const key = String(e.payload?.run_id ?? e.session_id ?? e.id);
      if (seen.has(key)) continue;
      seen.add(key);
      const done = e.type === "agent.completed";
      const failed = done && e.payload?.ok === false;
      rows.push({
        id: e.id,
        session_id: e.session_id,
        label: eventLabel(e),
        state: failed ? "failed" : done ? "completed" : "running",
        ts: e.ts,
      });
      if (rows.length >= 3) break;
    }
    return rows;
  }, [events]);

  // Ambient Operator surfacing: enabled reflexes + their recent fires (from the
  // live event stream — reflex.fired carries {rule, action, ok, detail}).
  const activeReflexes = useMemo(
    () => (reflexes.data?.rules ?? []).filter((r) => r.enabled).length,
    [reflexes.data],
  );
  const reflexFires = useMemo(
    () => events.filter((e) => e.type === "reflex.fired").slice(0, 4),
    [events],
  );

  const templateList = templates.data?.templates ?? [];

  // Hero status band signals.
  const runningCount = useMemo(
    () =>
      (sessions.data?.sessions ?? []).filter((s) => {
        const st = s.status.toLowerCase();
        return st === "running" || st === "active" || st === "pending";
      }).length,
    [sessions.data],
  );
  const failures = reliability.data?.recent_provider_failures ?? 0;
  const freeDisk = reliability.data?.disk?.free;
  const statusLine = offline
    ? "Daemon offline"
    : runningCount > 0
      ? `Working on ${runningCount} task${runningCount === 1 ? "" : "s"}`
      : failures > 0
        ? `${failures} provider hiccup${failures === 1 ? "" : "s"} in the last 24h`
        : "All systems nominal";

  // Compact connections summary.
  const realProviders = (health.data?.providers ?? []).filter(
    (p) => p.provider !== "mock" && p.class !== "mock",
  );
  const readyCount = realProviders.filter((p) => p.available).length;
  const vaultLoggedIn = (vault.data?.providers ?? []).filter((p) => p.logged_in).length;

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

      {/* HERO STATUS BAND — arc-reactor MoodOrb + status line + stat tiles. */}
      <Reveal>
        <div className="card-surface relative overflow-hidden">
          <div className="pointer-events-none absolute -left-16 -top-24 h-64 w-64 rounded-full bg-accent/10 blur-3xl" />
          <div className="pointer-events-none absolute -right-10 top-1/2 h-40 w-40 -translate-y-1/2 rounded-full bg-accent/[0.06] blur-3xl" />
          <div className="relative grid gap-6 p-6 lg:grid-cols-[minmax(0,auto)_1fr] lg:items-center">
            {/* Orb + one-line mood/status */}
            <div className="flex items-center gap-5">
              <span className="relative grid h-20 w-20 shrink-0 place-items-center rounded-full border border-accent/15 bg-accent/[0.04] shadow-[0_0_28px_-6px_rgba(34,211,238,0.35)]">
                <span className="scale-[2.05]">
                  <MoodOrb />
                </span>
              </span>
              <div className="min-w-0">
                <div className="text-[11px] font-medium uppercase tracking-[0.16em] text-zinc-500">
                  Iron Jarvis
                </div>
                <div className="mt-1 truncate text-lg font-semibold text-zinc-100">
                  {statusLine}
                </div>
                <div className="mt-1.5 flex items-center gap-2 text-xs text-zinc-500">
                  <Dot on={connected} />
                  <span>{connected ? "live" : "stream offline"}</span>
                  {health.data && (
                    <>
                      <span>·</span>
                      <span>v{health.data.version}</span>
                    </>
                  )}
                </div>
              </div>
            </div>

            {/* Stat tiles */}
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <HeroStat
                label="Model"
                icon={<Cpu size={12} />}
                tone="accent"
                value={
                  health.data ? (
                    <span className="font-mono">
                      {health.data.default_provider}/{health.data.default_model}
                    </span>
                  ) : health.loading ? (
                    <Skeleton className="h-4 w-24" />
                  ) : (
                    "—"
                  )
                }
                title={
                  health.data
                    ? `${health.data.default_provider} / ${health.data.default_model}`
                    : undefined
                }
              />
              <HeroStat
                label="Running"
                icon={<Zap size={12} />}
                tone={runningCount > 0 ? "accent" : "neutral"}
                value={String(runningCount)}
              />
              <HeroStat
                label="Free disk"
                icon={<HardDrive size={12} />}
                value={
                  freeDisk != null ? (
                    fmtBytes(freeDisk)
                  ) : reliability.loading ? (
                    <Skeleton className="h-4 w-16" />
                  ) : (
                    "—"
                  )
                }
              />
              <HeroStat
                label="Failures 24h"
                icon={<AlertTriangle size={12} />}
                tone={failures > 0 ? "bad" : "neutral"}
                value={String(failures)}
              />
            </div>
          </div>
        </div>
      </Reveal>

      {/* INTERACTIVE quick actions into the hero surfaces. */}
      <Reveal>
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          {QUICK_ACTIONS.map((a) => (
            <Link
              key={a.href}
              href={a.href}
              className="group relative flex items-center gap-3 overflow-hidden rounded-2xl border border-white/[0.06] bg-white/[0.02] p-4 transition-all duration-300 hover:-translate-y-0.5 hover:border-accent/30 hover:bg-accent/[0.04] hover:shadow-card-hover"
            >
              <span className="pointer-events-none absolute -right-6 -top-8 h-20 w-20 rounded-full bg-accent/10 opacity-0 blur-2xl transition-opacity duration-300 group-hover:opacity-100" />
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-accent/20 bg-accent/[0.08] text-accent-soft">
                {a.icon}
              </span>
              <span className="min-w-0">
                <span className="block truncate text-sm font-semibold text-zinc-100">
                  {a.title}
                </span>
                <span className="block truncate text-xs text-zinc-500">{a.desc}</span>
              </span>
              <ArrowRight
                size={14}
                className="ml-auto shrink-0 text-zinc-600 transition-all group-hover:translate-x-0.5 group-hover:text-accent-soft"
              />
            </Link>
          ))}
        </div>
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

      {/* Ambient operator — the reflexes that act on their own + recent fires. */}
      <Reveal>
        <Card
          title="Ambient operator"
          icon={<Zap size={15} />}
          right={
            <Link
              href="/reflex"
              className="text-xs text-accent transition-colors hover:text-accent/80"
            >
              Manage →
            </Link>
          }
        >
          <Link
            href="/reflex"
            className="flex items-center justify-between rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2 transition-colors hover:border-accent/25 hover:bg-accent/[0.05]"
          >
            <span className="text-sm text-zinc-300">
              {activeReflexes > 0
                ? `${activeReflexes} active ${activeReflexes === 1 ? "reflex" : "reflexes"}`
                : "No reflexes yet"}
            </span>
            <span className="text-xs text-zinc-500">
              {activeReflexes > 0
                ? "webhooks & messages that run work on their own"
                : "Set one up →"}
            </span>
          </Link>

          {reflexFires.length > 0 && (
            <ul className="mt-3 space-y-1.5">
              {reflexFires.map((e) => {
                const p = e.payload || {};
                const ok = p.ok !== false;
                return (
                  <li
                    key={e.id}
                    className="flex items-center gap-2.5 rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2 text-xs"
                  >
                    <StatusDot status={ok ? "completed" : "failed"} />
                    <span className="shrink-0 font-medium text-zinc-200">
                      {String(p.rule ?? "reflex")}
                    </span>
                    <span className="truncate text-zinc-500">
                      {ok ? "fired" : "failed"} → {String(p.action ?? p.detail ?? "action")}
                    </span>
                    <span className="ml-auto shrink-0 tabular-nums text-[11px] text-zinc-600">
                      {clockTime(e.ts)}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
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
                const failed = e.state === "failed";
                const running = e.state === "running";
                const label = running ? "Running now" : failed ? "Failed" : "Just finished";
                const tone = failed
                  ? "border-rose-500/25 bg-rose-500/[0.06] hover:bg-rose-500/[0.1]"
                  : "border-accent/20 bg-accent/[0.05] hover:bg-accent/[0.09]";
                const row = (
                  <span className="flex items-center gap-2.5">
                    <StatusDot status={e.state} />
                    <span
                      className={`shrink-0 font-medium ${failed ? "text-rose-200" : "text-zinc-200"}`}
                    >
                      {label}
                    </span>
                    <span className="truncate text-zinc-500">{e.label}</span>
                    <span className="ml-auto shrink-0 tabular-nums text-[11px] text-zinc-600">
                      {clockTime(e.ts)}
                    </span>
                  </span>
                );
                return e.session_id ? (
                  <Link
                    key={e.id}
                    href={`/sessions/${e.session_id}`}
                    className={`block rounded-xl border px-3 py-2 text-xs transition-colors ${tone}`}
                  >
                    {row}
                  </Link>
                ) : (
                  <div
                    key={e.id}
                    className={`rounded-xl border px-3 py-2 text-xs ${tone}`}
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
                    onClick={() =>
                      startTask(t.task, key, t.agent_type || "builder", t.provider, t.model)
                    }
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

      {/* Metric cards (Advanced) */}
      {advanced && (
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
      )}

      <Reveal>
        <div className="grid gap-4 lg:grid-cols-2">
          {/* Connections — compact summary + manage link (trimmed from two cards). */}
          <Card
            title="Connections"
            icon={<Server size={15} />}
            right={
              <Link
                href="/connections"
                className="flex items-center gap-1 text-xs text-accent-soft transition-colors hover:text-accent"
              >
                manage <ArrowRight size={12} />
              </Link>
            }
          >
            {health.loading && !health.data ? (
              <SkeletonRows rows={2} />
            ) : health.data ? (
              <div className="space-y-3">
                <div className="text-xs text-zinc-500">
                  default{" "}
                  <span className="font-mono text-zinc-300">
                    {health.data.default_provider} / {health.data.default_model}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3">
                  <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5">
                    <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-zinc-400">
                      <Server size={12} /> Providers
                    </div>
                    <div className="mt-1 flex items-center gap-1.5 text-sm font-medium text-zinc-200">
                      <Dot on={readyCount > 0} />
                      {readyCount} of {realProviders.length} ready
                    </div>
                  </div>
                  <div className="rounded-xl border border-white/[0.05] bg-white/[0.02] px-3 py-2.5">
                    <div className="flex items-center gap-1.5 text-[11px] uppercase tracking-wide text-zinc-400">
                      <ShieldCheck size={12} /> Browser vault
                    </div>
                    <div className="mt-1 flex items-center gap-1.5 text-sm font-medium text-zinc-200">
                      <Dot on={vaultLoggedIn > 0} />
                      {vaultLoggedIn} logged in
                    </div>
                  </div>
                </div>
                {readyCount === 0 && (
                  <Link
                    href="/connections"
                    className="flex items-center justify-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.08] px-3 py-2 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]"
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

      {/* System health (the /diagnostics self-test, surfaced at a glance) — Advanced */}
      {advanced && (
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
      )}

      {advanced && (
        <Reveal>
          <EventStream />
        </Reveal>
      )}
    </PageShell>
  );
}
