"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  CalendarClock,
  Plus,
  Play,
  Clock,
  Repeat,
  Timer,
  Sparkles,
  Workflow,
  Radio,
  ArrowUpRight,
} from "lucide-react";
import { post, del, get, ApiError } from "@/lib/api";
import { CRON_TO_LABEL, REPEAT_PRESETS } from "@/lib/schedules";
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
import { ChooserTiles } from "@/components/ChooserTiles";
import { useFocusRef } from "@/lib/useFocusRef";

/** What a fire DOES (v1.119.0) — task leads because "have an agent do X every
 * morning" is the schedule people actually mean; workflow/event serve
 * canvas-builders and API callers. Matches scheduling/models.py KINDS. */
const KIND_OPTIONS = [
  {
    key: "task",
    label: "Run a task",
    blurb: "An agent does this on schedule and reports back.",
    needs: "just the words — plus, optionally, a project and where to send the result",
    effort: "quickest" as const,
    icon: <Sparkles size={15} />,
  },
  {
    key: "workflow",
    label: "Run a saved workflow",
    blurb: "Fire a multi-step workflow you built on the canvas.",
    needs: "a saved workflow to pick",
    effort: "easy" as const,
    icon: <Workflow size={15} />,
  },
  {
    key: "event",
    label: "Emit an event",
    blurb: "Publish a raw event on the internal bus — for automation builders.",
    needs: "an event type string (optional)",
    effort: "technical" as const,
    icon: <Radio size={15} />,
  },
];

/** Ready-made schedules (the on-ramp): one click fills the whole form with a
 * real, useful recipe — answering "what would I even use this for?". */
const TEMPLATES: { label: string; name: string; task: string; cron: string }[] = [
  {
    label: "Morning briefing",
    name: "morning-briefing",
    task: "Write my morning briefing: summarize what happened yesterday across my projects and what is scheduled today, in five crisp bullet points.",
    cron: "0 8 * * 1-5",
  },
  {
    label: "Friday digest",
    name: "friday-digest",
    task: "Write a Friday digest of this week's work: what got done, what is still open, and what deserves attention next week.",
    cron: "0 16 * * 5",
  },
  {
    label: "Tidy Downloads",
    name: "tidy-downloads",
    task: "Tidy my Downloads folder: group files by type into subfolders, list exactly what you moved, and flag anything you were unsure about.",
    cron: "0 18 * * *",
  },
];

/** Friendly repeat presets that each map to a 5-field cron expression. */
// Sentinel <select> values for the two non-preset modes.
const ADVANCED = "__advanced__";
const ONCE = "__once__";


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

/** One-line "what this schedule does" for the row (task text > workflow name). */
function whatLabel(s: Schedule): string {
  let p: Record<string, unknown> = {};
  try {
    p = JSON.parse(s.payload_json || "{}");
  } catch {
    /* unparseable payload — fall through to bare labels */
  }
  if (s.kind === "task") return String(p.task ?? "");
  if (s.kind === "workflow") return `Workflow: ${p.workflow ?? p.name ?? "?"}`;
  return `Event: ${p.type ?? "schedule.fired"}`;
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
  // Projects a task schedule can run inside; destinations its result can reach.
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [destinations, setDestinations] = useState<string[]>([]);
  useEffect(() => {
    get<{ projects: { id: string; name: string }[] }>("/projects")
      .then((r) => setProjects(r.projects ?? []))
      .catch(() => setProjects([]));
    get<{ channels: { name: string }[] }>("/comm/channels")
      .then((r) =>
        setDestinations(
          (r.channels ?? [])
            .map((c) => c.name)
            // Internal test channels aren't a place a person sends results.
            .filter((n) => n !== "mock" && n !== "console"),
        ),
      )
      .catch(() => setDestinations([]));
  }, []);

  const [name, setName] = useState("");
  const [kind, setKind] = useState("task");
  const [taskText, setTaskText] = useState("");
  const [projectId, setProjectId] = useState("");
  // "all" = every destination (the default), "none" = silent, else one name.
  const [dest, setDest] = useState("all");
  const [workflowName, setWorkflowName] = useState("");
  const [eventType, setEventType] = useState("");
  // "repeat" holds a preset cron, or the ADVANCED / ONCE sentinels.
  const [repeat, setRepeat] = useState<string>("0 9 * * *");
  const [advancedCron, setAdvancedCron] = useState("");
  const [runAt, setRunAt] = useState(""); // datetime-local value
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const [acting, setActing] = useState<string | null>(null);

  // ?focus=add (the global search's deep link) rings the add card.
  const addFocusRef = useFocusRef<HTMLDivElement>("add");

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
  // A task schedule needs its words; a workflow schedule MUST reference a saved
  // workflow, else it would fire and run nothing.
  const payloadReady =
    kind === "task" ? !!taskText.trim() : kind === "workflow" ? !!workflowName : true;

  function applyTemplate(t: (typeof TEMPLATES)[number]) {
    setKind("task");
    setName(t.name);
    setTaskText(t.task);
    setRepeat(t.cron);
    setOk(null);
    setFormError(null);
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!name.trim() || !triggerReady || !payloadReady) return;
    setBusy(true);
    setFormError(null);
    setOk(null);

    // Exactly one of cron / run_at must be sent. The payload carries what the
    // fire should do — and, for tasks, where the result should go.
    let payload: Record<string, unknown> = {};
    if (kind === "task") {
      payload = { task: taskText.trim() };
      if (projectId) payload.project_id = projectId;
      if (dest === "none") payload.notify = false;
      else if (dest !== "all") payload.notify_channels = [dest];
    } else if (kind === "workflow") {
      payload = { workflow: workflowName };
      if (dest === "none") payload.notify = false;
      else if (dest !== "all") payload.notify_channels = [dest];
    } else if (eventType.trim()) {
      payload = { type: eventType.trim() };
    }
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
      body.cron = isAdvanced ? advancedCron.trim() : repeat;
    }

    try {
      await post("/schedules", body);
      setOk(`Schedule "${name.trim()}" added.`);
      setName("");
      setTaskText("");
      setProjectId("");
      setDest("all");
      setRepeat("0 9 * * *");
      setAdvancedCron("");
      setRunAt("");
      setKind("task");
      setWorkflowName("");
      setEventType("");
      reload();
    } catch (err) {
      // The daemon's 400 detail is already specific (bad cron, duplicate name,
      // missing task text, unknown destination) — show it verbatim.
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
      // Run-now returns the OUTCOME (v1.119.0) — report how it went, not
      // just that a trigger was pulled.
      const r = await post<{
        ran: string;
        last_status: string;
        last_detail: string;
      }>(`/schedules/${encodeURIComponent(schedName)}/run`);
      if (r.last_status === "error") {
        setFormError(`"${schedName}" failed — ${r.last_detail || "no detail"}`);
      } else {
        setOk(`Ran "${schedName}" ✓${r.last_detail ? ` — ${r.last_detail}` : ""}`);
      }
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
          subtitle="Hand work to an agent on a schedule — it runs the task, records how it went, and sends the result to your destinations."
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
            <div ref={addFocusRef}>
            <Card title="Add schedule" icon={<Plus size={15} />}>
              <form onSubmit={submit} className="space-y-3.5">
                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    What should happen?
                  </label>
                  <ChooserTiles
                    ariaLabel="Schedule kind"
                    value={kind}
                    onChange={(k) => {
                      setKind(k);
                      setFormError(null);
                    }}
                    options={KIND_OPTIONS}
                  />
                  {kind === "task" && (
                    <div className="mt-2 flex flex-wrap items-center gap-1.5">
                      <span className="text-[11px] text-zinc-600">Try:</span>
                      {TEMPLATES.map((t) => (
                        <button
                          key={t.name}
                          type="button"
                          onClick={() => applyTemplate(t)}
                          className="rounded-full border border-accent/25 bg-accent/[0.06] px-2.5 py-1 text-[11.5px] text-accent-soft transition-colors hover:bg-accent/[0.12]"
                        >
                          {t.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                {kind === "task" && (
                  <>
                    <div>
                      <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        The task
                      </label>
                      <textarea
                        value={taskText}
                        onChange={(e) => setTaskText(e.target.value)}
                        placeholder="Every fire, an agent gets exactly these words. e.g. Summarize yesterday's work and today's plan."
                        rows={3}
                        aria-label="Task text"
                        className="field resize-y text-sm leading-relaxed"
                      />
                    </div>
                    <div>
                      <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                        Run inside a project
                      </label>
                      <select
                        aria-label="Project"
                        value={projectId}
                        onChange={(e) => setProjectId(e.target.value)}
                        className="field"
                      >
                        <option value="">No project</option>
                        {projects.map((p) => (
                          <option key={p.id} value={p.id}>
                            {p.name}
                          </option>
                        ))}
                      </select>
                      <div className="mt-1 text-[11px] text-zinc-600">
                        The agent runs with that project&apos;s context and files.
                      </div>
                    </div>
                  </>
                )}

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

                {kind === "event" && (
                  <div>
                    <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      Event type
                    </label>
                    <input
                      value={eventType}
                      onChange={(e) => setEventType(e.target.value)}
                      placeholder="schedule.fired"
                      aria-label="Event type"
                      className="field font-mono text-sm"
                    />
                  </div>
                )}

                {kind !== "event" && (
                  <div>
                    <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                      Send the result to
                    </label>
                    <select
                      aria-label="Send the result to"
                      value={dest}
                      onChange={(e) => setDest(e.target.value)}
                      className="field"
                    >
                      <option value="all">All destinations</option>
                      {destinations.map((d) => (
                        <option key={d} value={d}>
                          {d === "this-pc" ? "This PC" : d}
                        </option>
                      ))}
                      <option value="none">Don&apos;t notify</option>
                    </select>
                    <div className="mt-1 text-[11px] text-zinc-600">
                      Add more on the Notifications page — Telegram puts results on
                      your phone.
                    </div>
                  </div>
                )}

                <div>
                  <label className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                    Name
                  </label>
                  <input
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="morning-briefing"
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
                        <th className="px-2 py-2.5 font-medium">Last result</th>
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
                              <span className="min-w-0">
                                <span className="block text-zinc-100">{s.name}</span>
                                {whatLabel(s) && (
                                  <span
                                    className="block max-w-[260px] truncate text-[11px] text-zinc-500"
                                    title={whatLabel(s)}
                                  >
                                    {whatLabel(s)}
                                  </span>
                                )}
                              </span>
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
                            {/* The row tells the TRUTH (v1.119.0): how the last
                                fire went + a link to the actual session. */}
                            {s.last_status === "ok" ? (
                              <span className="text-[12px] text-emerald-300">✓ ok</span>
                            ) : s.last_status === "error" ? (
                              <span className="text-[12px] text-rose-300">✗ failed</span>
                            ) : (
                              <span className="text-[12px] text-zinc-600">
                                not run yet
                              </span>
                            )}
                            {s.last_detail ? (
                              <div
                                className="max-w-[220px] truncate text-[11px] text-zinc-500"
                                title={s.last_detail}
                              >
                                {s.last_detail}
                              </div>
                            ) : null}
                            {s.last_session_id ? (
                              <Link
                                href={`/sessions/${s.last_session_id}`}
                                className="mt-0.5 inline-flex items-center gap-1 text-[11px] text-accent-soft transition-colors hover:text-accent"
                              >
                                open session <ArrowUpRight size={11} />
                              </Link>
                            ) : null}
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
