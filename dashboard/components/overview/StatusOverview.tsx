"use client";

/**
 * EVERYTHING › STATUS (calm UI redesign S8; the system map since v1.334.0).
 *
 * This was the Overview page (app/page.tsx) until the home became the chat.
 * Since v1.334.0 it is ONE picture instead of a second directory: the
 * Directory tab lists every door, so this tab no longer repeats them (the
 * module grid, saved tasks, recent sessions, the connections list and the
 * "Systems & admin" drawer are gone — each one is a door in the Directory).
 * What stays is what the user needs to SEE: the status line, the system map
 * (components/overview/SystemMap.tsx — what is set up, used and in use), the
 * day's heartbeat, four plain vitals, and the truthful notes (a failing
 * background task or tool pack is named, work a restart cut off is offered
 * back, the first-run strip leads while setup is unfinished).
 *
 * Cost: one slow poll of GET /ui/system-map (it carries the status-line
 * counts too, so this page no longer polls /sessions, /metrics, /diagnostics,
 * /diagnostics/reliability, /reflex/rules, /templates or /vault), refreshed
 * early when a run starts or ends.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { Activity, AlertTriangle, ChevronDown, Cpu, FolderKanban, HardDrive, Zap } from "lucide-react";
import { usePolledApi, useApi } from "@/lib/useApi";
import { useDaemon } from "@/lib/daemon";
import { useEvents } from "@/lib/useEvents";
import { OVERVIEW_EVENT_TYPES } from "@/components/overview/eventTypes";
import type { Health, Onboarding } from "@/lib/types";
import { Dot, OfflineHint, Skeleton } from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { OnboardingWelcome } from "@/components/OnboardingWelcome";
import { FirstRunStrip } from "@/components/onboarding/FirstRunStrip";
// v1.313.0 (U1-1): `noModelChosen` is the ONE "replies are a scripted demo"
// rule (lib/onboarding); this page used to keep its own copy.
import { isDemoDefault, noModelChosen } from "@/lib/onboarding";
import { InterruptedJobsNote } from "@/components/InterruptedJobs";
import { PageShell, Reveal } from "@/components/motion";
import { Heartbeat, MapLegend, SystemMap, type SystemMapData } from "@/components/overview/SystemMap";
import { RunningNow } from "@/components/RunningNow";

/** GET /ui/system-map — the map plus the status line's own counts. */
export type SystemMapResponse = SystemMapData & {
  status?: {
    running?: number;
    failing_loops?: { name: string; error: string }[];
    failing_packs?: { name: string; error: string }[];
    provider_failures_24h?: number;
  };
};

/** The active project (the "context spine") — carried on /health. */
type ActiveProject = { id: string; name: string; root?: string };

/** How often the map is re-read while the page is visible. Runs starting or
 *  ending re-read it sooner (see the event effect below). */
export const MAP_POLL_MS = 15000;

function fmtBytes(b?: number): string {
  if (!b || b <= 0) return "0 B";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let n = b;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(i > 0 && n < 10 ? 1 : 0)} ${u[i]}`;
}

/** A compact vital. */
function HeroStat({
  label,
  value,
  icon,
  tone = "neutral",
  title,
  hint,
}: {
  label: string;
  value: ReactNode;
  icon: ReactNode;
  tone?: "neutral" | "accent" | "bad";
  title?: string;
  /** v1.313.0: the tile's exact meaning (and the metric's raw name) on hover,
   *  so the label itself can stay plain words. */
  hint?: string;
}) {
  const tint = tone === "bad" ? "text-rose-300" : tone === "accent" ? "text-accent-soft" : "text-zinc-100";
  return (
    <div title={hint} className="min-w-0 px-1">
      <div className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-zinc-500">
        <span className="text-accent-soft/70">{icon}</span>
        {label}
      </div>
      <div className={`mt-1 truncate text-[15px] font-semibold ${tint}`} title={title}>
        {value}
      </div>
    </div>
  );
}

export default function OverviewPage({ embedded = false }: { embedded?: boolean } = {}) {
  // v1.230.0 (FP2/FP3): /health is the app's ONE shared poll (DaemonProvider).
  const daemon = useDaemon();
  const health = { data: daemon.health, loading: daemon.checking };
  const map = usePolledApi<SystemMapResponse>("/ui/system-map", MAP_POLL_MS);
  // v1.310.0 (wave 2, overview-buries-first-action): ONE read of /onboarding
  // for the whole page — the strip's gate and the welcome card share it.
  const onboarding = useApi<Onboarding>("/onboarding");
  // The gate is next_step ALONE: while a getting-started step is still open,
  // the first-run strip leads the page. `!= null` so an older/partial payload
  // with no next_step key reads as "nothing to lead".
  const firstRunOpen = onboarding.data?.next_step != null;
  // LATCHED for this visit (v1.310.0 review): the press that ticks the last
  // open step must not unmount the strip that shows its confirmation.
  const [firstRunLatched, setFirstRunLatched] = useState(false);
  useEffect(() => {
    if (firstRunOpen) setFirstRunLatched(true);
  }, [firstRunOpen]);
  const showFirstRun = firstRunOpen || firstRunLatched;

  // v1.311.0 (wave 3): only the types this page reads. A run starting or
  // ending re-reads the map once (debounced), so a star lights up while the
  // run is still going rather than up to 15 s later.
  const { events, connected } = useEvents(40, { types: OVERVIEW_EVENT_TYPES });
  const reloadMap = map.reload;
  const lastSeen = useRef<string | null>(null);
  useEffect(() => {
    const newest = events[0]?.id ?? null;
    if (newest === null || newest === lastSeen.current) return;
    const first = lastSeen.current === null;
    lastSeen.current = newest;
    if (first) return; // the backlog on mount is already in the first read
    const t = window.setTimeout(reloadMap, 1200);
    return () => window.clearTimeout(t);
  }, [events, reloadMap]);

  // v1.352.0 (G26): the Running vital opens the Running now list in place.
  const [runningOpen, setRunningOpen] = useState(false);
  const runningListId = useId();

  const offline = !daemon.checking && !daemon.online;
  const status = map.data?.status;
  const runningCount = status?.running ?? 0;
  const failures = status?.provider_failures_24h ?? 0;
  const freeDisk = map.data?.vitals?.disk_free;
  // v1.229.0 (audit OBS2): the background loops that report ok:false, each
  // with its reason. The status must never say "all good" over a dead loop,
  // and the loop is NAMED.
  const failingLoops = status?.failing_loops ?? [];
  // v1.229.0 (audit U4): a tool pack that did not start costs every agent its
  // tools; named here with a way to the Retry.
  const failedPacks = status?.failing_packs ?? [];
  const statusLine = offline
    ? "Daemon offline"
    : runningCount > 0
      ? `Working on ${runningCount} task${runningCount === 1 ? "" : "s"}`
      : failingLoops.length > 0
        ? `${failingLoops.length} background task${failingLoops.length === 1 ? "" : "s"} failing`
        : failedPacks.length > 0
          ? `${failedPacks.length} thing${failedPacks.length === 1 ? "" : "s"} need${failedPacks.length === 1 ? "s" : ""} attention`
          : failures > 0
            ? `${failures} provider hiccup${failures === 1 ? "" : "s"} in the last 24h`
            : // v1.313.0: never "all good" while every reply is the scripted
              // demo, and say neither until /health has answered.
              daemon.provided && !health.data
              ? "Checking in…"
              : health.data && isDemoDefault(health.data)
                ? "Ready — choose a model to get real answers"
                : "All good — ready when you are";

  const activeProject = (health.data as (Health & { active_project?: ActiveProject }) | undefined)?.active_project;
  // v1.310.0 (mock-default-trap-cli-ollama, d): a fresh install's default is
  // the offline demo; say what is true instead of "mock/<model>".
  const model = health.data
    ? noModelChosen(health.data.default_provider)
      ? "No model chosen yet"
      : `${health.data.default_provider}/${health.data.default_model}`
    : undefined;
  const modelChosen = !!health.data && !noModelChosen(health.data.default_provider);
  const vitals = map.data?.vitals;
  const runs = vitals?.runs_7d ?? 0;
  const runsOk = vitals?.runs_ok_7d ?? 0;
  const runsDone = vitals?.runs_done_7d ?? 0;
  const mapError = map.error && map.error.status !== 0 && !map.data ? map.error : null;

  return (
    <PageShell>
      {/* Embedded in Everything › Status the page's header is Everything's. */}
      {!embedded && (
        <Reveal>
          <PageHeader
            title="Overview"
            subtitle="What Iron Jarvis has set up, what it has been doing, and what it is doing right now."
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
      )}

      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      {/* v1.310.0 (wave 2): while setup is unfinished the way to a first real
          answer leads, above the map. Once next_step is null it is gone. */}
      {showFirstRun && (
        <Reveal>
          <FirstRunStrip onboarding={onboarding} />
        </Reveal>
      )}

      {/* v1.249.0 (R-02): work a restart cut off, with its Continue. Renders
          nothing when there is nothing to pick up. */}
      <InterruptedJobsNote />

      {failingLoops.length > 0 && (
        <Reveal>
          <div
            role="status"
            data-testid="loop-failing-note"
            className="flex items-center gap-2 rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-4 py-2 text-[12px] text-amber-200"
          >
            <AlertTriangle size={14} className="shrink-0" />
            <span className="min-w-0 truncate">
              Background task{failingLoops.length === 1 ? "" : "s"} failing:{" "}
              {failingLoops.map((l) => `${l.name} — ${l.error}`).join(" · ")}
            </span>
          </div>
        </Reveal>
      )}

      {failedPacks.length > 0 && (
        <Reveal>
          <div
            role="status"
            data-testid="pack-failing-note"
            className="flex items-center gap-2 rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-4 py-2 text-[12px] text-amber-200"
          >
            <AlertTriangle size={14} className="shrink-0" />
            <span className="min-w-0 truncate">
              Tool pack{failedPacks.length === 1 ? "" : "s"} didn’t start:{" "}
              {failedPacks.map((p) => `${p.name} — ${p.error}`).join(" · ")}
            </span>
            <Link href="/tools" className="ml-auto shrink-0 font-medium text-amber-100 underline-offset-2 hover:underline">
              Retry on Tools
            </Link>
          </div>
        </Reveal>
      )}

      {/* THE PICTURE. */}
      <Reveal>
        <section aria-label="System map" className="card-surface relative overflow-hidden" data-testid="status-map-card">
          <div className="pointer-events-none absolute inset-0" aria-hidden>
            <div className="absolute -left-24 -top-28 h-72 w-72 rounded-full bg-accent/10 blur-3xl" />
            <div className="absolute -bottom-24 -right-20 h-72 w-72 rounded-full bg-accent/[0.06] blur-3xl" />
          </div>
          <div className="relative px-5 pt-6 sm:px-8 sm:pt-7">
            <div className="text-[11px] font-medium uppercase tracking-[0.22em] text-accent-soft/70">Iron Jarvis</div>
            {/* A live status line, not a second <h1> — PageHeader owns it. */}
            <p role="status" className="text-gradient mt-1.5 text-2xl font-semibold leading-tight tracking-tight sm:text-[2rem]">
              {statusLine}
            </p>
            <div className="mt-2.5 flex flex-wrap items-center gap-x-2.5 gap-y-2 text-xs text-zinc-500">
              <span className="inline-flex items-center gap-1.5">
                <Dot on={connected} />
                {connected ? "live" : "stream offline"}
              </span>
              {activeProject && (
                <Link
                  href={`/projects/${encodeURIComponent(activeProject.id)}`}
                  title="Your active context spine — new chats, sessions & workflows carry it"
                  className="inline-flex items-center gap-1.5 rounded-full border border-accent/40 bg-accent/[0.12] px-2.5 py-0.5 font-medium text-accent-soft shadow-[0_0_14px_rgb(var(--accent-rgb)/0.3)] transition-colors hover:bg-accent/[0.18]"
                >
                  <FolderKanban size={11} />
                  {activeProject.name}
                </Link>
              )}
            </div>
          </div>
          <div className="relative px-2 sm:px-4">
            {mapError ? (
              <p role="alert" className="px-4 py-16 text-center text-[13px] text-tone-warn">
                The map could not be read: {mapError.message}
              </p>
            ) : (
              <SystemMap data={map.data ?? null} loading={map.loading} />
            )}
          </div>
          <div className="relative px-5 pb-6 sm:px-8">
            <MapLegend />
          </div>
        </section>
      </Reveal>

      {/* The day's pulse and four plain vitals. */}
      <Reveal>
        <section aria-label="Vitals" className="card-surface relative overflow-hidden px-5 py-5 sm:px-8">
          <div className="grid grid-cols-2 gap-x-4 gap-y-4 sm:grid-cols-5">
            <HeroStat
              label="Model"
              icon={<Cpu size={12} />}
              tone="accent"
              value={model ? <span className={modelChosen ? "font-mono" : undefined}>{model}</span> : "—"}
              title={model}
            />
            <HeroStat
              label="Running"
              icon={<Zap size={12} />}
              tone={runningCount > 0 ? "accent" : "neutral"}
              value={
                <button
                  type="button"
                  data-testid="status-running-toggle"
                  aria-expanded={runningOpen}
                  aria-controls={runningOpen ? runningListId : undefined}
                  aria-label={`${runningCount} running. ${runningOpen ? "Hide" : "Show"} what is running`}
                  title={runningOpen ? "Hide what is running" : "Show what is running"}
                  onClick={() => setRunningOpen((o) => !o)}
                  // ring-INSET: HeroStat's value box is `truncate` (overflow
                  // hidden), which clipped an outer focus ring to nothing.
                  className="inline-flex items-center gap-1 rounded-md underline decoration-white/20 decoration-dotted underline-offset-4 hover:decoration-current focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-accent/60"
                >
                  {String(runningCount)}
                  <ChevronDown
                    size={13}
                    aria-hidden
                    className={`text-zinc-500 transition-transform motion-reduce:transition-none ${runningOpen ? "rotate-180" : ""}`}
                  />
                </button>
              }
            />
            <HeroStat
              label="Runs this week"
              hint="Agent runs started in the last 7 days, and the share of the finished ones that ended without an error"
              icon={<Activity size={12} />}
              value={runs > 0 && runsDone > 0 ? `${runs} · ${Math.round((runsOk / runsDone) * 100)}% ok` : String(runs)}
            />
            <HeroStat
              label="Disk space free"
              hint="Free disk — space left on the drive Iron Jarvis keeps its data on"
              icon={<HardDrive size={12} />}
              value={freeDisk != null ? fmtBytes(freeDisk) : map.loading ? <Skeleton className="h-4 w-16" /> : "—"}
            />
            <HeroStat
              label="Problems today"
              hint="Failures 24h — model calls that failed in the last 24 hours"
              icon={<AlertTriangle size={12} />}
              tone={failures > 0 ? "bad" : "neutral"}
              value={String(failures)}
            />
          </div>
          {runningOpen && (
            <div id={runningListId} className="-mx-3 mt-4" data-testid="status-running-list">
              <RunningNow />
            </div>
          )}
          <div className="mt-5">
            <Heartbeat hours={map.data?.heartbeat ?? []} />
          </div>
        </section>
      </Reveal>

      {/* First-run welcome — here only while the strip is NOT up (one card,
          never two). It still nudges for a failing check after setup. */}
      {!showFirstRun && (
        <Reveal>
          <OnboardingWelcome state={onboarding} />
        </Reveal>
      )}
    </PageShell>
  );
}
