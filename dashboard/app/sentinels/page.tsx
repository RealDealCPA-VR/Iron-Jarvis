"use client";

import { useEffect, useId, useRef, useState } from "react";
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
// v1.316.0 (agent-names-raw-lowercase): THE agent-name rule — built-ins
// read as words ("maintainer" → "Maintainer"), a custom agent keeps the name
// the user typed. The workflow editor's map knew only its own five agents.
import { agentLabel } from "@/lib/agentWorlds";
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
  // Built-in comes from the roster (GET /agents' builtin list); before it
  // answers, the fallback names ARE built-ins.
  const builtinAgents = new Set(agentsData ? agentsData.builtin ?? [] : FALLBACK_AGENTS);
  const nameOf = (a: string) => agentLabel(a, { builtin: builtinAgents.has(a) });

  // Status banner actions (enable + poll now).
  const [enabling, setEnabling] = useState(false);
  const [polling, setPolling] = useState(false);
  const [statusOk, setStatusOk] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);

  // Add form
  const [open, setOpen] = useState(false);
  // v1.316.0 (form-labels-not-associated): ties each label to its field.
  const fid = useId();
  // v1.316.0 (carry-empty-add-scrolls-to-form): "Watch a folder" opens THIS
  // form, which sits ABOVE the list — on a phone a scrolled-down user saw
  // nothing happen. Every press bumps the counter; the effect scrolls the form
  // into view and focuses its first field (Schedules' pattern), also when the
  // header already opened it. The header Add still toggles.
  const formStartRef = useRef<HTMLInputElement | null>(null);
  const [focusForm, setFocusForm] = useState(0);
  useEffect(() => {
    if (!focusForm) return;
    const el = formStartRef.current;
    if (!el) return;
    el.scrollIntoView?.({ behavior: "smooth", block: "center" });
    el.focus();
  }, [focusForm]);
  const openFromEmpty = () => {
    setOpen(true);
    setFocusForm((n) => n + 1);
  };
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
      // v1.314.0: plain words. The loop's first check runs ~30 s after it is
      // armed, then every sentinels_tick_seconds (300 s by default, config.py)
      // — so "every few minutes", never "every 30 seconds".
      setStatusOk(
        "Sentinels are on. The first check runs in about half a minute, then every few " +
          "minutes. “Check now” works right away too.",
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
          "Nothing was checked — sentinels are off. Turn them on first, then check again.",
        );
      } else if (r.proposals.length === 0) {
        setStatusOk("Checked — no changes noticed, nothing to suggest.");
      } else {
        setStatusOk(
          `Checked — ${r.proposals.length} suggestion${r.proposals.length === 1 ? "" : "s"} waiting for you. Review them in Autonomy → Proposals.`,
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
          subtitle="Watchers that keep an eye on a folder and SUGGEST a next step when files change. Each suggestion waits in Autonomy → Proposals for your approval."
          actions={
            <div className="flex items-center gap-2">
              {/* v1.314.0: "Check now" (was "Poll now"). While sentinels are
                  off it stays pressable — the reply says why nothing ran —
                  and its tooltip says so before the press. */}
              <button
                type="button"
                onClick={pollNow}
                disabled={polling}
                title={featureEnabled ? "Check every watched folder now" : "Turn sentinels on first"}
                className="inline-flex items-center gap-1.5 rounded-xl border border-white/10 px-3 py-2 text-sm text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-40"
              >
                {polling ? <LoaderInline label="Checking…" /> : <><Radar size={14} /> Check now</>}
              </button>
              {/* While sentinels are off, "Enable sentinels" is THE primary on
                  the page; Add stays here, quieter, one press as before. */}
              <button
                type="button"
                onClick={() => setOpen((v) => !v)}
                className={featureEnabled ? "btn-accent" : "btn-ghost"}
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
                    Sentinels are <span className="text-emerald-300">on</span> — your watched
                    folders are checked every few minutes.
                  </span>
                ) : (
                  <span className="text-zinc-300">
                    Sentinels are <span className="text-amber-300">off</span> — nothing is being
                    watched. Your watchers are kept and start again when you turn sentinels on.
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
                  for your review — anything they suggest still goes through Autonomy&apos;s
                  settings, budget and approvals.
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
              Same switch as in Settings. It takes effect right away — no restart needed.
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
                  <label htmlFor={`${fid}-name`} className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name
                  </label>
                  <input
                    id={`${fid}-name`}
                    ref={formStartRef}
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="downloads-watch"
                    className="field"
                  />
                </div>
                <div>
                  <label htmlFor={`${fid}-kind`} className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Kind
                  </label>
                  <select
                    id={`${fid}-kind`}
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
                  <label htmlFor={`${fid}-path`} className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    <FolderSearch size={12} /> Path to watch
                  </label>
                  <input
                    id={`${fid}-path`}
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
                  <label htmlFor={`${fid}-glob`} className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Glob (optional)
                  </label>
                  <input
                    id={`${fid}-glob`}
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
                <label htmlFor={`${fid}-task`} className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                  Suggested task (optional)
                </label>
                <input
                  id={`${fid}-task`}
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
                  <label htmlFor={`${fid}-agent`} className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Agent type
                  </label>
                  <select
                    id={`${fid}-agent`}
                    aria-label="Agent type"
                    value={agentType}
                    onChange={(e) => setAgentType(e.target.value)}
                    className="field"
                  >
                    {/* v1.316.0: built-ins in words, custom names as typed
                        (lib/agentWorlds.agentLabel); the value stays the agent
                        id the daemon stores. */}
                    {agentTypes.map((a) => (
                      <option key={a} value={a}>
                        {nameOf(a)}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label htmlFor={`${fid}-risk`} className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Risk
                  </label>
                  <select
                    id={`${fid}-risk`}
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
                    Carried onto the suggestion — a noticed change is never marked high risk.
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
            // v1.314.0: teach on a fresh install (the explanation lives in the
            // (i) popover). A fired sentinel mints SUGGEST-ONLY proposals; the
            // wording stays "suggests … for you to approve" — never an absolute
            // "never acts", because execution still flows through the dial.
            <Empty
              icon={<Radar size={24} />}
              title="Keep an eye on a folder"
              examples={[
                "When a new scan lands in your intake folder, suggest sorting it",
                "When a statement appears in Downloads, suggest filing it",
              ]}
              action={{ label: "Watch a folder", onClick: openFromEmpty }}
            >
              A sentinel watches a folder. When files change, it suggests a next step for you to
              approve.
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
                        {/* v1.316.0: the agent's name; its id stays in title. */}
                        <span className="text-[11px] text-zinc-600" title={s.agent_type}>
                          → {nameOf(s.agent_type)}
                        </span>
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
