"use client";

import { useEffect, useState } from "react";
import { CalendarClock, Plus, Play, Clock, Repeat, Timer } from "lucide-react";
import { post, del, ApiError } from "@/lib/api";
import { usePolledApi, useApi } from "@/lib/useApi";
import type { Schedule } from "@/lib/types";
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

// Matches the backend's scheduling/models.py KINDS ("callback" was removed).
const KINDS = ["workflow", "event"];

/** Friendly repeat presets that each map to a 5-field cron expression. */
const REPEAT_PRESETS: { label: string; cron: string }[] = [
  { label: "Every minute", cron: "* * * * *" },
  { label: "Every 15 minutes", cron: "*/15 * * * *" },
  { label: "Hourly", cron: "0 * * * *" },
  { label: "Daily at midnight", cron: "0 0 * * *" },
  { label: "Daily at 9am", cron: "0 9 * * *" },
  { label: "Weekdays at 9am", cron: "0 9 * * 1-5" },
  { label: "Weekly Mon 9am", cron: "0 9 * * 1" },
  { label: "Monthly 1st", cron: "0 0 1 * *" },
];

// Sentinel <select> values for the two non-preset modes.
const ADVANCED = "__advanced__";
const ONCE = "__once__";

const CRON_TO_LABEL = new Map(REPEAT_PRESETS.map((p) => [p.cron, p.label]));

/** A human-readable description of a stored schedule's trigger. */
function triggerLabel(s: Schedule): string {
  const tt = (s.trigger_type ?? "").toLowerCase();
  if (tt === "date" || (!s.cron && s.run_at)) {
    return s.run_at ? `Once · ${new Date(s.run_at).toLocaleString()}` : "Once";
  }
  if (tt === "interval" || (!s.cron && s.interval_seconds)) {
    return s.interval_seconds ? `Every ${s.interval_seconds}s` : "Interval";
  }
  if (s.cron) return CRON_TO_LABEL.get(s.cron) ?? "Custom cron";
  return "—";
}

export default function SchedulesPage() {
  const { data, error, loading, reload } = usePolledApi<{ schedules: Schedule[] }>(
    "/schedules",
    8000,
  );
  const offline = error && error.status === 0;
  const schedules = data?.schedules ?? [];
  // Saved workflows a "workflow" schedule can reference by name.
  const workflows = useApi<{ workflows: { name: string }[] }>("/workflows");
  const workflowNames = workflows.data?.workflows?.map((w) => w.name) ?? [];

  const [name, setName] = useState("");
  const [workflowName, setWorkflowName] = useState("");
  // "repeat" holds a preset cron, or the ADVANCED / ONCE sentinels.
  const [repeat, setRepeat] = useState<string>("0 9 * * *");
  const [advancedCron, setAdvancedCron] = useState("");
  const [runAt, setRunAt] = useState(""); // datetime-local value
  const [kind, setKind] = useState("workflow");
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  // Deep-link from the workflow editor's "Schedule…" button: ?workflow=<name>
  // prefills the create form for that workflow. Read window.location to avoid a
  // useSearchParams Suspense boundary under static export.
  useEffect(() => {
    try {
      const wf = new URLSearchParams(window.location.search).get("workflow");
      if (wf) {
        setKind("workflow");
        setWorkflowName(wf);
      }
    } catch {
      /* ignore */
    }
  }, []);

  const isOnce = repeat === ONCE;
  const isAdvanced = repeat === ADVANCED;

  // Whether the schedule-defining field for the current mode is filled in.
  const triggerReady = isOnce ? !!runAt : isAdvanced ? !!advancedCron.trim() : !!repeat;
  // A "workflow" schedule MUST reference a saved workflow, else it would fire and
  // run nothing (the backend now rejects an empty workflow at execution time).
  const payloadReady = kind !== "workflow" || !!workflowName;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !triggerReady || !payloadReady) return;
    setBusy(true);
    setFormError(null);
    setOk(null);

    // Exactly one of cron / run_at must be sent. A workflow schedule carries the
    // saved workflow's name so the daemon can run its steps (not an empty no-op).
    const payload = kind === "workflow" ? { workflow: workflowName } : {};
    const body: Record<string, unknown> = { name: name.trim(), kind, payload };
    if (isOnce) {
      const d = new Date(runAt);
      if (Number.isNaN(d.getTime())) {
        setFormError("Pick a valid date and time.");
        setBusy(false);
        return;
      }
      body.run_at = d.toISOString();
    } else {
      body.cron = (isAdvanced ? advancedCron.trim() : repeat);
    }

    try {
      await post("/schedules", body);
      setOk(`Schedule "${name.trim()}" added.`);
      setName("");
      setRepeat("0 9 * * *");
      setAdvancedCron("");
      setRunAt("");
      setKind("workflow");
      setWorkflowName("");
      reload();
    } catch (err) {
      // The daemon's 400 detail is already specific (bad cron, duplicate name,
      // unknown kind, missing workflow) — show it verbatim, don't mislabel it.
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function runNow(schedName: string) {
    setActing(`run:${schedName}`);
    setOk(null);
    setFormError(null);
    try {
      await post(`/schedules/${encodeURIComponent(schedName)}/run`);
      setOk(`Ran "${schedName}".`);
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActing(null);
    }
  }

  async function remove(schedName: string) {
    setActing(`del:${schedName}`);
    setFormError(null);
    try {
      await del(`/schedules/${encodeURIComponent(schedName)}`);
      reload();
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setActing(null);
    }
  }

  return (
    <PageShell>
      <Reveal>
        <PageHeader
          title="Schedules"
          subtitle="Recurring or one-time tasks. Pick a friendly repeat preset or a specific date & time — no cron syntax required."
        />
      </Reveal>
      {offline && (
        <Reveal>
          <OfflineHint />
        </Reveal>
      )}

      <Reveal>
        <div className="grid gap-6 lg:grid-cols-3">
          <div className="lg:col-span-1">
            <Card title="Add schedule" icon={<Plus size={15} />}>
              <form onSubmit={submit} className="space-y-3.5">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="nightly-report"
                    className="field"
                  />
                </div>

                <div>
                  <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    <Repeat size={12} /> Repeat
                  </label>
                  <select
                    aria-label="Repeat"
                    value={repeat}
                    onChange={(e) => setRepeat(e.target.value)}
                    className="field"
                  >
                    {REPEAT_PRESETS.map((p) => (
                      <option key={p.cron} value={p.cron}>
                        {p.label}
                      </option>
                    ))}
                    <option value={ONCE}>Once at a specific time…</option>
                    <option value={ADVANCED}>Advanced cron…</option>
                  </select>
                  {!isOnce && !isAdvanced && (
                    <div className="mt-1 font-mono text-[11px] text-zinc-600">{repeat}</div>
                  )}
                </div>

                {isOnce && (
                  <div>
                    <label className="mb-1.5 flex items-center gap-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      <Timer size={12} /> Run at
                    </label>
                    <input
                      type="datetime-local"
                      value={runAt}
                      onChange={(e) => setRunAt(e.target.value)}
                      className="field"
                    />
                    <div className="mt-1 text-[11px] text-zinc-600">
                      Fires once, then completes.
                    </div>
                  </div>
                )}

                {isAdvanced && (
                  <div>
                    <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      Cron expression
                    </label>
                    <input
                      value={advancedCron}
                      onChange={(e) => setAdvancedCron(e.target.value)}
                      placeholder="0 9 * * *"
                      className="field font-mono"
                    />
                    <div className="mt-1 text-[11px] text-zinc-600">min hour day month weekday</div>
                  </div>
                )}

                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Kind
                  </label>
                  <select aria-label="Task kind" value={kind} onChange={(e) => setKind(e.target.value)} className="field">
                    {KINDS.map((k) => (
                      <option key={k} value={k}>
                        {k}
                      </option>
                    ))}
                  </select>
                </div>

                {kind === "workflow" && (
                  <div>
                    <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      Workflow to run
                    </label>
                    {workflowNames.length === 0 ? (
                      <div className="text-[11px] text-amber-300/80">
                        No saved workflows yet — create one on the Workflows page first.
                      </div>
                    ) : (
                      <select
                        aria-label="Workflow to run"
                        value={workflowName}
                        onChange={(e) => setWorkflowName(e.target.value)}
                        className="field"
                      >
                        <option value="">Select a workflow…</option>
                        {workflowNames.map((w) => (
                          <option key={w} value={w}>
                            {w}
                          </option>
                        ))}
                      </select>
                    )}
                  </div>
                )}
                <button
                  type="submit"
                  disabled={busy || !name.trim() || !triggerReady || !payloadReady}
                  className="btn-accent w-full"
                >
                  {busy ? <LoaderInline label="Adding…" /> : <><Plus size={14} /> Add schedule</>}
                </button>
                {ok && <SuccessNote>{ok}</SuccessNote>}
                {formError && <ErrorNote>{formError}</ErrorNote>}
              </form>
            </Card>
          </div>

          <div className="lg:col-span-2">
            <Card
              title={`Schedules${schedules.length ? ` · ${schedules.length}` : ""}`}
              icon={<CalendarClock size={15} />}
            >
              {loading && !data ? (
                <SkeletonRows rows={5} />
              ) : schedules.length === 0 ? (
                <Empty icon={<CalendarClock size={24} />}>No schedules yet.</Empty>
              ) : (
                <div className="-mx-1 overflow-x-auto">
                  <table className="w-full text-left text-sm">
                    <thead>
                      <tr className="border-b hairline text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        <th className="px-2 py-2.5 font-medium">Name</th>
                        <th className="px-2 py-2.5 font-medium">Repeat</th>
                        <th className="px-2 py-2.5 font-medium">Kind</th>
                        <th className="px-2 py-2.5 font-medium">Next run</th>
                        <th className="px-2 py-2.5 font-medium" />
                      </tr>
                    </thead>
                    <tbody>
                      {schedules.map((s) => (
                        <tr
                          key={s.name}
                          className="border-b border-white/[0.04] align-middle last:border-0 hover:bg-white/[0.02]"
                        >
                          <td className="px-2 py-2.5">
                            <span className="flex items-center gap-2">
                              <Dot on={!!s.enabled} />
                              <span className="text-zinc-100">{s.name}</span>
                            </span>
                          </td>
                          <td className="px-2 py-2.5">
                            <div className="text-zinc-200">{triggerLabel(s)}</div>
                            {s.cron && (
                              <div className="font-mono text-[11px] text-accent-soft/70">
                                {s.cron}
                              </div>
                            )}
                          </td>
                          <td className="px-2 py-2.5">
                            <Badge value={s.kind} tone="violet" />
                          </td>
                          <td className="px-2 py-2.5 text-zinc-500">
                            <span className="inline-flex items-center gap-1.5">
                              <Clock size={12} className="text-zinc-600" />
                              {s.next_run ? new Date(s.next_run).toLocaleString() : "—"}
                            </span>
                          </td>
                          <td className="px-2 py-2.5 text-right">
                            <div className="flex items-center justify-end gap-1.5">
                              <button
                                onClick={() => runNow(s.name)}
                                disabled={acting === `run:${s.name}`}
                                title="Run now"
                                className="rounded-lg border border-white/10 p-1.5 text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-40"
                              >
                                {acting === `run:${s.name}` ? (
                                  <LoaderInline />
                                ) : (
                                  <Play size={14} />
                                )}
                              </button>
                              <ConfirmButton
                                onConfirm={() => remove(s.name)}
                                label="Delete"
                                title={`Delete schedule "${s.name}"`}
                              />
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        </div>
      </Reveal>
    </PageShell>
  );
}
