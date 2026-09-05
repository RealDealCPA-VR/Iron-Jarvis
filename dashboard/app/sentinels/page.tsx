"use client";

import { useState } from "react";
import Link from "next/link";
import {
  Radar,
  Plus,
  X,
  ArrowRight,
  Power,
  Eye,
  FolderSearch,
} from "lucide-react";
import { post, del, put, ApiError } from "@/lib/api";
import { usePolledApi, useApi } from "@/lib/useApi";
import type { AgentsResponse } from "@/lib/types";
import {
  Card,
  Badge,
  Dot,
  OfflineHint,
  Empty,
  SkeletonRows,
  ErrorNote,
  SuccessNote,
  LoaderInline,
  ConfirmButton,
} from "@/components/ui";
import { PageHeader } from "@/components/PageHeader";
import { PageShell, Reveal } from "@/components/motion";

/** One watcher, as returned by the daemon's `_sentinel_view` (GET /sentinels). */
interface Sentinel {
  id: string;
  name: string;
  kind: string;
  /** Kind-specific watch spec. For "file": { path, glob? }. */
  config: Record<string, unknown>;
  task: string;
  agent_type: string;
  risk: string;
  enabled: boolean;
  last_checked_at: string | null;
  /** v1.231.0 (audit AE7): "root unreachable since <t>" while the watched
   *  folder is gone (USB / OneDrive), else the last scan error; null when
   *  the last scan succeeded. */
  last_error?: string | null;
  created_at: string;
}

interface SentinelsResponse {
  enabled: boolean;
  sentinels: Sentinel[];
}

/** POST /sentinels/poll result. */
interface PollResult {
  ran: boolean;
  reason?: string;
  proposals: string[];
}

// Matches the backend's sentinels/models.py KINDS — only "file" is wired in
// this build (email/calendar watchers arrive with the integration layer).
const KINDS = ["file"];

// Fallback agent types when the daemon hasn't reported any agents yet
// (same list the Templates page uses).
const FALLBACK_AGENTS = ["builder", "planner", "researcher", "reviewer", "supervisor"];

// A noticed signal is never auto-high — the backend accepts low | med here.
const RISKS = ["low", "med"];

/** Human summary of a sentinel's watch spec (path + optional glob). */
function watchSummary(s: Sentinel): string {
  const path = typeof s.config.path === "string" ? s.config.path : "";
  const glob = typeof s.config.glob === "string" ? s.config.glob : "";
  if (!path) return "—";
  return glob ? `${path} · ${glob}` : path;
}

export default function SentinelsPage() {
  const { data, error, loading, reload } = usePolledApi<SentinelsResponse>(
    "/sentinels",
    10000,
  );
  const offline = error && error.status === 0;
  const sentinels = data?.sentinels ?? [];
  const featureEnabled = data?.enabled ?? false;

  // Agent types a fired sentinel can suggest (built-in + dynamic).
  const { data: agentsData } = useApi<AgentsResponse>("/agents");
  const agentTypes = (() => {
    const names = [
      ...(agentsData?.builtin ?? []),
      ...(agentsData?.dynamic ?? []).map((d) => d.name),
    ];
    return names.length ? names : FALLBACK_AGENTS;
  })();

  // Status banner actions (enable + poll now).
  const [enabling, setEnabling] = useState(false);
  const [polling, setPolling] = useState(false);
  const [statusOk, setStatusOk] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  // Add form
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("file");
  const [path, setPath] = useState("");
  const [glob, setGlob] = useState("");
  const [task, setTask] = useState("");
  const [agentType, setAgentType] = useState("builder");
  const [risk, setRisk] = useState("low");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function enableSentinels() {
    setEnabling(true);
    setStatusOk(null);
    setStatusError(null);
    try {
      await put("/settings", { values: { sentinels_enabled: true } });
      setStatusOk(
        "Sentinels enabled — the background watch loop is arming now (first " +
          "sweep in ~30s). “Poll now” works immediately too.",
      );
      reload();
    } catch (err) {
      setStatusError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setEnabling(false);
    }
  }

  async function pollNow() {
    setPolling(true);
    setStatusOk(null);
    setStatusError(null);
    try {
      const r = await post<PollResult>("/sentinels/poll");
      if (!r.ran) {
        setStatusOk(
          "Sweep skipped — sentinels are disabled, so the poll was a no-op. Enable them first.",
        );
      } else if (r.proposals.length === 0) {
        setStatusOk("Sweep complete — no changes noticed, nothing suggested.");
      } else {
        setStatusOk(
          `Sweep complete — ${r.proposals.length} suggestion${r.proposals.length === 1 ? "" : "s"} minted. Review them in Autonomy → Proposals.`,
        );
      }
      reload();
    } catch (err) {
      setStatusError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setPolling(false);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !path.trim()) return;
    setBusy(true);
    setFormError(null);
    setOk(null);
    const body: Record<string, unknown> = {
      name: name.trim(),
      kind,
      path: path.trim(),
      task: task.trim(),
      agent_type: agentType,
      risk,
    };
    if (glob.trim()) body.glob = glob.trim();
    try {
      await post("/sentinels", body);
      setOk(
        `Sentinel "${name.trim()}" added. Its first check records a baseline — pre-existing files never fire.`,
      );
      setName("");
      setPath("");
      setGlob("");
      setTask("");
      setAgentType("builder");
      setRisk("low");
      reload();
    } catch (err) {
      // The daemon's 400 detail is already specific (protected path, bad glob,
      // duplicate name, unknown kind) — show it verbatim.
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function remove(sentinelName: string) {
    setDeleteError(null);
    try {
      await del(`/sentinels/${encodeURIComponent(sentinelName)}`);
      reload();
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : String(err));
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Sentinels"
          subtitle="Always-on watchers that observe (filesystem, for now) and only ever SUGGEST — a fired sentinel mints a proposal into the Autonomy backlog. It never acts on its own."
          actions={
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={pollNow}
                disabled={polling}
                className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-2 text-sm text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-40"
              >
                {polling ? <LoaderInline label="Sweeping…" /> : <><Radar size={14} /> Poll now</>}
              </button>
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className="btn-accent"
              >
                <Plus size={14} /> Add sentinel
              </button>
            </div>
          }
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <Card>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <Dot on={featureEnabled} />
              <div className="text-sm">
                {featureEnabled ? (
                  <span className="text-zinc-200">
                    Sentinels are <span className="text-emerald-300">enabled</span> — the
                    background loop sweeps enabled watchers on a timer.
                  </span>
                ) : (
                  <span className="text-zinc-300">
                    Sentinels are <span className="text-amber-300">disabled</span> — watchers are
                    kept but never checked, and manual polls no-op.
                  </span>
                )}
                <div className="mt-0.5 text-[11px] text-zinc-500">
                  Suggestions land in{" "}
                  <Link
                    href="/autonomy"
                    className="text-accent-soft underline-offset-2 hover:underline"
                  >
                    Autonomy → Proposals
                  </Link>{" "}
                  for your review — execution still flows through the autonomy dial, budget and
                  approval.
                </div>
              </div>
            </div>
            <div className="flex items-center gap-2">
              {!featureEnabled && (
                <button
                  type="button"
                  onClick={enableSentinels}
                  disabled={enabling}
                  className="btn-accent"
                >
                  {enabling ? (
                    <LoaderInline label="Enabling…" />
                  ) : (
                    <><Power size={14} /> Enable sentinels</>
                  )}
                </button>
              )}
              <Link
                href="/autonomy"
                className="inline-flex items-center gap-1.5 rounded-xl border border-accent/30 bg-accent/[0.08] px-3 py-1.5 text-xs font-medium text-accent-soft transition-colors hover:bg-accent/[0.14]"
              >
                Review proposals <ArrowRight size={13} />
              </Link>
            </div>
          </div>
          {!featureEnabled && (
            <div className="mt-2 text-[11px] text-zinc-600">
              Enabling flips <code className="font-mono">sentinels_enabled</code> in Settings.
              The background watch loop arms live — no restart needed.
            </div>
          )}
          {(statusOk || statusError) && (
            <div className="mt-3 space-y-2">
              {statusOk && <SuccessNote>{statusOk}</SuccessNote>}
              {statusError && <ErrorNote>{statusError}</ErrorNote>}
            </div>
          )}
        </Card>
      </Reveal>

      {open && (
        <Reveal>
          <Card title="Add sentinel" icon={<Plus size={15} />}>
            <form onSubmit={submit} className="space-y-3.5">
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="downloads-watch"
                    className="field"
                  />
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Kind
                  </label>
                  <select
                    aria-label="Watcher kind"
                    value={kind}
                    onChange={(e) => setKind(e.target.value)}
                    className="field"
                  >
                    {KINDS.map((k) => (
                      <option key={k} value={k}>
                        {k} (filesystem)
                      </option>
                    ))}
                  </select>
                  <div className="mt-1 text-[11px] text-zinc-600">
                    Filesystem is the only watcher kind in this build.
                  </div>
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    <FolderSearch size={12} /> Path to watch
                  </label>
                  <input
                    value={path}
                    onChange={(e) => setPath(e.target.value)}
                    placeholder="C:\Users\you\Downloads"
                    className="field font-mono"
                  />
                  <div className="mt-1 text-[11px] text-zinc-600">
                    A file, directory or glob. Protected paths and paths outside the allowed
                    roots are rejected.
                  </div>
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Glob (optional)
                  </label>
                  <input
                    value={glob}
                    onChange={(e) => setGlob(e.target.value)}
                    placeholder="*.pdf"
                    className="field font-mono"
                  />
                  <div className="mt-1 text-[11px] text-zinc-600">
                    Pattern relative to the path, e.g. <code className="font-mono">**/*.csv</code>.
                  </div>
                </div>
              </div>

              <div>
                <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Suggested task (optional)
                </label>
                <input
                  value={task}
                  onChange={(e) => setTask(e.target.value)}
                  placeholder="Summarise any new bank statements and flag anything unusual"
                  className="field"
                />
                <div className="mt-1 text-[11px] text-zinc-600">
                  Becomes the proposal's task when this sentinel fires. Leave blank for a
                  generic “review what changed” suggestion.
                </div>
              </div>

              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Agent type
                  </label>
                  <select
                    aria-label="Agent type"
                    value={agentType}
                    onChange={(e) => setAgentType(e.target.value)}
                    className="field"
                  >
                    {agentTypes.map((a) => (
                      <option key={a} value={a}>
                        {a}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Risk
                  </label>
                  <select
                    aria-label="Risk"
                    value={risk}
                    onChange={(e) => setRisk(e.target.value)}
                    className="field"
                  >
                    {RISKS.map((r) => (
                      <option key={r} value={r}>
                        {r}
                      </option>
                    ))}
                  </select>
                  <div className="mt-1 text-[11px] text-zinc-600">
                    Carried onto the minted proposal — a noticed signal is never auto-high.
                  </div>
                </div>
              </div>

              <div className="flex items-center gap-2">
                <button
                  type="submit"
                  disabled={busy || !name.trim() || !path.trim()}
                  className="btn-accent"
                >
                  {busy ? <LoaderInline label="Adding…" /> : <><Plus size={14} /> Add sentinel</>}
                </button>
                <button
                  type="button"
                  onClick={() => setOpen(false)}
                  className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-2 text-sm text-zinc-400 transition-colors hover:border-white/20 hover:text-zinc-200"
                >
                  <X size={14} /> Cancel
                </button>
              </div>
              <div className="text-[11px] text-zinc-600">
                Adding never scans or fires — the first check records a baseline, so
                pre-existing files won't flood the backlog.
              </div>
              {ok && <SuccessNote>{ok}</SuccessNote>}
              {formError && <ErrorNote>{formError}</ErrorNote>}
            </form>
          </Card>
        </Reveal>
      )}

      <Reveal>
        <Card
          title={`Watchers${sentinels.length ? ` · ${sentinels.length}` : ""}`}
          icon={<Eye size={15} />}
        >
          {deleteError && (
            <div className="mb-3">
              <ErrorNote>{deleteError}</ErrorNote>
            </div>
          )}
          {loading && !data ? (
            <SkeletonRows rows={4} />
          ) : sentinels.length === 0 ? (
            <Empty icon={<Radar size={24} />}>
              No sentinels yet — use “Add sentinel” to watch a folder for changes.
            </Empty>
          ) : (
            <div className="-mx-1 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    <th className="px-2 py-2.5 font-medium">Name</th>
                    <th className="px-2 py-2.5 font-medium">Kind</th>
                    <th className="px-2 py-2.5 font-medium">Watches</th>
                    <th className="px-2 py-2.5 font-medium">Suggests</th>
                    <th className="px-2 py-2.5 font-medium">Risk</th>
                    <th className="px-2 py-2.5 font-medium">Last checked</th>
                    <th className="px-2 py-2.5 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {sentinels.map((s) => (
                    <tr
                      key={s.id}
                      className="border-b border-white/[0.04] align-middle last:border-0 hover:bg-white/[0.02]"
                    >
                      <td className="px-2 py-2.5">
                        <span className="flex items-center gap-2">
                          <Dot on={!!s.enabled} />
                          <span className="text-zinc-100">{s.name}</span>
                        </span>
                      </td>
                      <td className="px-2 py-2.5">
                        <Badge value={s.kind} tone="cyan" />
                      </td>
                      <td className="max-w-xs px-2 py-2.5">
                        <span
                          className="block truncate font-mono text-[11px] text-zinc-400"
                          title={watchSummary(s)}
                        >
                          {watchSummary(s)}
                        </span>
                      </td>
                      <td className="max-w-[14rem] px-2 py-2.5">
                        <span
                          className="block truncate text-[12px] text-zinc-400"
                          title={s.task || undefined}
                        >
                          {s.task || <span className="text-zinc-600">review what changed</span>}
                        </span>
                        <span className="text-[11px] text-zinc-600">→ {s.agent_type}</span>
                      </td>
                      <td className="px-2 py-2.5">
                        <Badge value={s.risk} tone={s.risk === "low" ? "green" : "amber"} />
                      </td>
                      <td className="px-2 py-2.5 text-zinc-500">
                        {s.last_checked_at
                          ? new Date(s.last_checked_at).toLocaleString()
                          : "never (baseline pending)"}
                        {s.last_error && (
                          <span
                            data-testid={`sentinel-last-error-${s.id}`}
                            className="block max-w-[16rem] truncate text-[11px] text-amber-300"
                            title={s.last_error}
                          >
                            {s.last_error}
                          </span>
                        )}
                      </td>
                      <td className="px-2 py-2.5 text-right">
                        <ConfirmButton
                          onConfirm={() => remove(s.name)}
                          label="Delete"
                          title={`Delete sentinel "${s.name}"`}
                        />
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
