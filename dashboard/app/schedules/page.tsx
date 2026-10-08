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
  Pencil,
  X,
} from "lucide-react";
import { post, del, get, patch, ApiError } from "@/lib/api";
import { CRON_TO_LABEL, REPEAT_PRESETS } from "@/lib/schedules";
import { usePolledApi, useApi } from "@/lib/useApi";
import type { Schedule, ScheduleScript } from "@/lib/types";
import { Modal } from "@/components/Modal";
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
import AgentFace from "@/components/agents/AgentFace";
import { PageGrid } from "@/components/PageGrid";

/** Fallback only — the real list comes live from GET /agents (builtin +
 * dynamic), the same source NewSessionForm's picker uses. */
const FALLBACK_AGENTS = ["builder", "supervisor", "planner", "researcher", "reviewer"];

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

/** Which agent a task row fires as (v1.171.0). Prefers the server-decoded
 * `agent_type` field; falls back to the payload blob for a daemon older than
 * this dashboard. "" / garbage decays to "builder" — the fire's own default —
 * never to an invented name. */
function scheduleAgent(s: Schedule): string {
  const decoded = (s as { agent_type?: unknown }).agent_type;
  if (typeof decoded === "string" && decoded.trim()) return decoded.trim();
  try {
    const p = JSON.parse(s.payload_json || "{}") as Record<string, unknown>;
    if (typeof p.agent_type === "string" && p.agent_type.trim()) {
      return p.agent_type.trim();
    }
  } catch {
    /* unparseable payload — the default below is the honest answer */
  }
  return "builder";
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

/* ---- v1.299.0 knobs: skills, folder, chain, pre-run script, skip-memory --- */

/** The knob keys the daemon surfaces on GET rows (and accepts in a payload). */
const KNOB_KEYS = ["skills", "workspace_root", "context_from", "script", "skip_memory"] as const;

/** Does THIS daemon know the knobs? GET rows carry them (every one present,
 *  possibly empty) once it does. With no rows there is nothing to read, so the
 *  controls show — an older daemon simply ignores unknown payload keys. */
function knobsSupported(rows: Schedule[]): boolean {
  if (rows.length === 0) return true;
  return rows.some((s) => KNOB_KEYS.some((k) => k in s));
}

/** One schedule's knob values — the row's own fields first, the payload blob
 *  for a daemon that stores them but does not surface them. */
interface ScheduleKnobs {
  task: string;
  skills: string[];
  workspace_root: string;
  context_from: string;
  script: ScheduleScript | null;
  skip_memory: boolean;
}

function knobsOf(s: Schedule): ScheduleKnobs {
  let p: Record<string, unknown> = {};
  try {
    p = JSON.parse(s.payload_json || "{}") as Record<string, unknown>;
  } catch {
    /* unparseable payload — the row's own fields are all we have */
  }
  const pick = <T,>(key: (typeof KNOB_KEYS)[number], ok: (v: unknown) => v is T): T | undefined => {
    if (ok(s[key])) return s[key];
    if (ok(p[key])) return p[key];
    return undefined;
  };
  const isStrList = (v: unknown): v is string[] =>
    Array.isArray(v) && v.every((x) => typeof x === "string");
  const isStr = (v: unknown): v is string => typeof v === "string";
  const isBool = (v: unknown): v is boolean => typeof v === "boolean";
  const isScript = (v: unknown): v is ScheduleScript =>
    !!v && typeof v === "object" && typeof (v as ScheduleScript).command === "string";
  return {
    task: typeof p.task === "string" ? p.task : "",
    skills: pick("skills", isStrList) ?? [],
    workspace_root: pick("workspace_root", isStr) ?? "",
    context_from: pick("context_from", isStr) ?? "",
    script: pick("script", isScript) ?? null,
    skip_memory: pick("skip_memory", isBool) ?? false,
  };
}

/** The pre-run script object the payload carries — only the keys that are set. */
function scriptOf(command: string, timeout: string, cwd: string): ScheduleScript | null {
  const cmd = command.trim();
  if (!cmd) return null;
  const out: ScheduleScript = { command: cmd };
  const t = Number(timeout);
  if (timeout.trim() && Number.isFinite(t) && t > 0) out.timeout_s = Math.round(t);
  if (cwd.trim()) out.cwd = cwd.trim();
  return out;
}

/** Add the knobs to a CREATE payload — only the ones the user set, so a form
 *  with none touched posts exactly the body an older dashboard posted. */
function applyKnobs(
  payload: Record<string, unknown>,
  k: {
    skills: string[];
    workspaceRoot: string;
    contextFrom: string;
    scriptCommand: string;
    scriptTimeout: string;
    scriptCwd: string;
    skipMemory: boolean;
  },
) {
  if (k.skills.length > 0) payload.skills = k.skills;
  if (k.workspaceRoot.trim()) payload.workspace_root = k.workspaceRoot.trim();
  if (k.contextFrom) payload.context_from = k.contextFrom;
  const script = scriptOf(k.scriptCommand, k.scriptTimeout, k.scriptCwd);
  if (script) payload.script = script;
  if (k.skipMemory) payload.skip_memory = true;
}

/** The PATCH body for an edit (v1.299.0): `payload_set` carries every knob
 *  that changed to a value, `payload_unset` every knob cleared; name and
 *  trigger ride top-level. Empty = nothing to send (pinned through the UI). */
function editBody(
  before: Schedule,
  after: {
    name: string;
    cron: string;
    runAt: string;
    intervalSeconds: string;
    task: string;
    skills: string[];
    workspaceRoot: string;
    contextFrom: string;
    scriptCommand: string;
    scriptTimeout: string;
    scriptCwd: string;
    skipMemory: boolean;
  },
): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  // The name is the row's key: PATCH /schedules/{name} has no `name` field
  // (SchedulePatch), so a rename is not something this editor can send.
  const cron = after.cron.trim();
  if (cron && cron !== (before.cron ?? "")) body.cron = cron;
  if (after.runAt) {
    const d = new Date(after.runAt);
    if (!Number.isNaN(d.getTime()) && d.toISOString() !== before.run_at) body.run_at = d.toISOString();
  }
  if (after.intervalSeconds.trim()) {
    const n = Number(after.intervalSeconds);
    if (Number.isFinite(n) && n > 0 && n !== before.interval_seconds) body.interval_seconds = Math.round(n);
  }
  const was = knobsOf(before);
  const set: Record<string, unknown> = {};
  const unset: string[] = [];
  const task = after.task.trim();
  if (before.kind === "task" && task && task !== was.task) set.task = task;
  if (after.skills.length > 0) {
    if (JSON.stringify(after.skills) !== JSON.stringify(was.skills)) set.skills = after.skills;
  } else if (was.skills.length > 0) unset.push("skills");
  const root = after.workspaceRoot.trim();
  if (root) {
    if (root !== was.workspace_root) set.workspace_root = root;
  } else if (was.workspace_root) unset.push("workspace_root");
  if (after.contextFrom) {
    if (after.contextFrom !== was.context_from) set.context_from = after.contextFrom;
  } else if (was.context_from) unset.push("context_from");
  const script = scriptOf(after.scriptCommand, after.scriptTimeout, after.scriptCwd);
  if (script) {
    if (JSON.stringify(script) !== JSON.stringify(was.script)) set.script = script;
  } else if (was.script) unset.push("script");
  if (after.skipMemory) {
    if (!was.skip_memory) set.skip_memory = true;
  } else if (was.skip_memory) unset.push("skip_memory");
  if (Object.keys(set).length > 0) body.payload_set = set;
  if (unset.length > 0) body.payload_unset = unset;
  return body;
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
  // Agents a task schedule can run as (builtin + dynamic — NewSessionForm's source).
  const { data: agentsData } = useApi<{ builtin: string[]; dynamic: { name: string }[] }>(
    "/agents",
  );
  const builtinAgents = agentsData?.builtin ?? FALLBACK_AGENTS;
  const dynamicAgents = (agentsData?.dynamic ?? []).map((a) => a.name);
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
  const [agentName, setAgentName] = useState("builder");
  const [projectId, setProjectId] = useState("");
  // "all" = every destination (the default), "none" = silent, else one name.
  const [dest, setDest] = useState("all");
  const [workflowName, setWorkflowName] = useState("");
  const [eventType, setEventType] = useState("");
  // "repeat" holds a preset cron, or the ADVANCED / ONCE sentinels.
  const [repeat, setRepeat] = useState<string>("0 9 * * *");
  const [advancedCron, setAdvancedCron] = useState("");
  const [runAt, setRunAt] = useState(""); // datetime-local value
  // v1.299.0 knobs (task kind): what the run carries beyond its words.
  const [skills, setSkills] = useState<string[]>([]);
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [contextFrom, setContextFrom] = useState("");
  const [scriptCommand, setScriptCommand] = useState("");
  const [scriptTimeout, setScriptTimeout] = useState("");
  const [scriptCwd, setScriptCwd] = useState("");
  const [skipMemory, setSkipMemory] = useState(false);
  // The row being edited (v1.299.0) — PATCH /schedules/{name}.
  const [editing, setEditing] = useState<Schedule | null>(null);
  const knobs = knobsSupported(schedules);
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
      // Absent = builder, exactly the daemon's default — an explicit
      // "builder" pick stays omitted so default payloads look like today's.
      if (agentName && agentName !== "builder") payload.agent_type = agentName;
      if (projectId) payload.project_id = projectId;
      if (dest === "none") payload.notify = false;
      else if (dest !== "all") payload.notify_channels = [dest];
      // v1.299.0: only the knobs the user set — an untouched form posts
      // exactly the body it always did.
      applyKnobs(payload, {
        skills,
        workspaceRoot,
        contextFrom,
        scriptCommand,
        scriptTimeout,
        scriptCwd,
        skipMemory,
      });
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
      setAgentName("builder");
      setProjectId("");
      setDest("all");
      setRepeat("0 9 * * *");
      setAdvancedCron("");
      setRunAt("");
      setKind("task");
      setWorkflowName("");
      setEventType("");
      setSkills([]);
      setWorkspaceRoot("");
      setContextFrom("");
      setScriptCommand("");
      setScriptTimeout("");
      setScriptCwd("");
      setSkipMemory(false);
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
        <PageGrid cols={3}>
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
                        Who runs it
                      </label>
                      <div className="flex items-center gap-2">
                        <AgentFace name={agentName} size={22} />
                        <select
                          aria-label="Agent"
                          value={agentName}
                          onChange={(e) => setAgentName(e.target.value)}
                          className="field"
                        >
                          {builtinAgents.map((t) => (
                            <option key={t} value={t}>
                              {t}
                            </option>
                          ))}
                          {/* A dynamic agent named exactly like a builtin
                              would render a SECOND option with the same
                              value — indistinguishable to the select, and
                              picking the "custom" one would silently fire
                              the BUILTIN (agent_type==="builder" is omitted
                              from the payload). The builtin option already
                              covers the name; at fire time the daemon
                              resolves it dynamic-first anyway. */}
                          {dynamicAgents
                            .filter((t) => !builtinAgents.includes(t))
                            .map((t) => (
                              <option key={`custom:${t}`} value={t}>
                                {t} (custom)
                              </option>
                            ))}
                        </select>
                      </div>
                      <div className="mt-1 text-[11px] text-zinc-600">
                        Custom agents fire with their own prompt and tools.
                      </div>
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
                    {/* v1.299.0 knobs — folded; most schedules need none. Hidden
                        on a daemon whose rows do not carry them. */}
                    {knobs && (
                      <details data-testid="schedule-knobs" className="rounded-lg border border-white/[0.06] px-3 py-2">
                        <summary className="cursor-pointer text-[11px] uppercase tracking-[0.1em] text-zinc-400">
                          More: skills, folder, chain, pre-run script, memory
                        </summary>
                        <div className="mt-3 space-y-3">
                          <SkillChips value={skills} onChange={setSkills} scope="this schedule" testId="schedule-skills" />
                          <KnobFields
                            others={schedules.map((s) => s.name)}
                            self={name.trim()}
                            workspaceRoot={workspaceRoot}
                            setWorkspaceRoot={setWorkspaceRoot}
                            contextFrom={contextFrom}
                            setContextFrom={setContextFrom}
                            scriptCommand={scriptCommand}
                            setScriptCommand={setScriptCommand}
                            scriptTimeout={scriptTimeout}
                            setScriptTimeout={setScriptTimeout}
                            scriptCwd={scriptCwd}
                            setScriptCwd={setScriptCwd}
                            skipMemory={skipMemory}
                            setSkipMemory={setSkipMemory}
                          />
                        </div>
                      </details>
                    )}
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
                                {s.kind === "task" && (
                                  <span
                                    className="mt-0.5 flex items-center gap-1.5 text-[11px] text-zinc-500"
                                    data-testid="schedule-agent"
                                  >
                                    <AgentFace name={scheduleAgent(s)} size={14} />
                                    {scheduleAgent(s)}
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
                            ) : s.last_status === "missed" ? (
                              /* v1.231.0: the app was closed or asleep at fire time —
                                 recorded, never fired late (last_detail says when). */
                              <span className="text-[12px] text-amber-300">missed</span>
                            ) : s.last_status === "skipped" ? (
                              /* v1.231.0: the previous fire was still running. */
                              <span className="text-[12px] text-amber-300">skipped</span>
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
                              {knobs && (
                                <button
                                  type="button"
                                  onClick={() => setEditing(s)}
                                  title={`Edit schedule "${s.name}"`}
                                  aria-label={`Edit schedule ${s.name}`}
                                  data-testid={`schedule-edit-${s.name}`}
                                  className="rounded-lg border border-white/10 p-1.5 text-zinc-400 transition-colors hover:border-accent/40 hover:text-accent-soft"
                                >
                                  <Pencil size={14} />
                                </button>
                              )}
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
        </PageGrid>
      </Reveal>
      {editing && (
        <ScheduleEditor
          schedule={editing}
          others={schedules.map((s) => s.name)}
          onClose={() => setEditing(null)}
          onSaved={(msg) => {
            setEditing(null);
            setOk(msg);
            setFormError(null);
            reload();
          }}
        />
      )}
    </PageShell>
  );
}

/* ---- v1.299.0: the knob controls, shared by Add and Edit ------------------ */

/** Skills as chips — a local copy of SetupCard's NameChips (not exported
 *  there): type one, Enter adds it, × takes it off. */
function SkillChips({
  value,
  onChange,
  scope,
  testId,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  scope: string;
  testId: string;
}) {
  const [text, setText] = useState("");
  function add() {
    const t = text.trim();
    if (!t) return;
    if (!value.includes(t)) onChange([...value, t]);
    setText("");
  }
  return (
    <div data-testid={testId} className="space-y-1">
      <span className="block text-[10px] uppercase tracking-[0.12em] text-zinc-500">
        Skills it carries
      </span>
      {value.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {value.map((t) => (
            <span
              key={t}
              className="inline-flex items-center gap-1 rounded border border-accent/30 bg-accent/[0.08] px-1 py-px font-mono text-[10px] text-accent-soft"
            >
              {t}
              <button
                type="button"
                onClick={() => onChange(value.filter((v) => v !== t))}
                aria-label={`Remove skill ${t} for ${scope}`}
                className="grid h-3 w-3 place-items-center rounded text-accent-soft/80 hover:text-accent-soft"
              >
                <X size={9} />
              </button>
            </span>
          ))}
        </div>
      )}
      <input
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            add();
          }
        }}
        placeholder="skill name, then Enter"
        aria-label={`Add a skill for ${scope}`}
        className="field text-xs"
      />
    </div>
  );
}

function KnobFields({
  others,
  self,
  workspaceRoot,
  setWorkspaceRoot,
  contextFrom,
  setContextFrom,
  scriptCommand,
  setScriptCommand,
  scriptTimeout,
  setScriptTimeout,
  scriptCwd,
  setScriptCwd,
  skipMemory,
  setSkipMemory,
}: {
  others: string[];
  self: string;
  workspaceRoot: string;
  setWorkspaceRoot: (v: string) => void;
  contextFrom: string;
  setContextFrom: (v: string) => void;
  scriptCommand: string;
  setScriptCommand: (v: string) => void;
  scriptTimeout: string;
  setScriptTimeout: (v: string) => void;
  scriptCwd: string;
  setScriptCwd: (v: string) => void;
  skipMemory: boolean;
  setSkipMemory: (v: boolean) => void;
}) {
  // A schedule cannot chain to itself (the daemon 422s); a stored pick that
  // names a deleted schedule still renders so it can be cleared.
  const choices = Array.from(new Set([...others.filter((n) => n && n !== self), contextFrom].filter(Boolean)));
  return (
    <>
      <div>
        <label className="mb-1 block text-[10px] uppercase tracking-[0.12em] text-zinc-500">
          Folder
        </label>
        <input
          value={workspaceRoot}
          onChange={(e) => setWorkspaceRoot(e.target.value)}
          placeholder="C:\\Users\\you\\Documents\\reports"
          aria-label="Folder"
          className="field font-mono text-xs"
        />
        <div className="mt-1 text-[11px] text-zinc-600">
          The agent runs in that folder; a rules file AGENTS.md or .ironjarvis.md in that
          folder loads.
        </div>
      </div>
      <div>
        <label className="mb-1 block text-[10px] uppercase tracking-[0.12em] text-zinc-500">
          Use the result of
        </label>
        <select
          aria-label="Use the result of"
          value={contextFrom}
          onChange={(e) => setContextFrom(e.target.value)}
          className="field text-xs"
        >
          <option value="">Nothing — start fresh</option>
          {choices.map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <div className="mt-1 text-[11px] text-zinc-600">
          That schedule&apos;s last result is handed to this run.
        </div>
      </div>
      <div>
        <label className="mb-1 block text-[10px] uppercase tracking-[0.12em] text-zinc-500">
          Pre-run script
        </label>
        <input
          value={scriptCommand}
          onChange={(e) => setScriptCommand(e.target.value)}
          placeholder="git pull"
          aria-label="Pre-run command"
          className="field font-mono text-xs"
        />
        <div className="mt-1.5 grid grid-cols-2 gap-2">
          <input
            type="number"
            min={1}
            max={120}
            value={scriptTimeout}
            onChange={(e) => setScriptTimeout(e.target.value)}
            placeholder="timeout s (≤ 120)"
            aria-label="Pre-run timeout (seconds)"
            className="field text-xs"
          />
          {/* The daemon accepts exactly two places (scheduling/knobs.SCRIPT_CWDS):
              the run's workspace or the app home — never a free path. */}
          <select
            value={scriptCwd}
            onChange={(e) => setScriptCwd(e.target.value)}
            aria-label="Pre-run folder"
            className="field text-xs"
          >
            <option value="">Run in the job's folder (workspace)</option>
            <option value="workspace">workspace</option>
            <option value="home">Iron Jarvis home</option>
          </select>
        </div>
        <div className="mt-1 text-[11px] text-zinc-600">
          Runs before the agent, under the shell tool&apos;s confinement; its output is
          handed to the run.
        </div>
      </div>
      <label className="flex items-center gap-2 text-xs text-zinc-300">
        <input
          type="checkbox"
          checked={skipMemory}
          onChange={(e) => setSkipMemory(e.target.checked)}
          aria-label="Skip memory"
          className="accent-current"
        />
        Skip memory — no lessons or memory are injected into this run
      </label>
    </>
  );
}

/** Edit one schedule (v1.299.0): name, trigger, task text and the knobs, sent
 *  as ONE PATCH /schedules/{name} — top-level name/cron/run_at/interval_seconds,
 *  `payload_set` for knobs set, `payload_unset` for knobs cleared. Only what
 *  changed is sent; nothing changed = nothing sent. */
function ScheduleEditor({
  schedule,
  others,
  onClose,
  onSaved,
}: {
  schedule: Schedule;
  others: string[];
  onClose: () => void;
  onSaved: (msg: string) => void;
}) {
  const was = knobsOf(schedule);
  const tt = (schedule.trigger_type ?? "").toLowerCase();
  const isOnce = tt === "date" || (!schedule.cron && !!schedule.run_at);
  const isInterval = tt === "interval" || (!schedule.cron && !!schedule.interval_seconds);
  const name = schedule.name;
  const [cron, setCron] = useState(schedule.cron ?? "");
  const [runAt, setRunAt] = useState("");
  const [intervalSeconds, setIntervalSeconds] = useState(
    schedule.interval_seconds ? String(schedule.interval_seconds) : "",
  );
  const [task, setTask] = useState(was.task);
  const [skills, setSkills] = useState<string[]>(was.skills);
  const [workspaceRoot, setWorkspaceRoot] = useState(was.workspace_root);
  const [contextFrom, setContextFrom] = useState(was.context_from);
  const [scriptCommand, setScriptCommand] = useState(was.script?.command ?? "");
  const [scriptTimeout, setScriptTimeout] = useState(
    was.script?.timeout_s ? String(was.script.timeout_s) : "",
  );
  const [scriptCwd, setScriptCwd] = useState(was.script?.cwd ?? "");
  const [skipMemory, setSkipMemory] = useState(was.skip_memory);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const body = editBody(schedule, {
      name,
      cron: isOnce || isInterval ? "" : cron,
      runAt: isOnce ? runAt : "",
      intervalSeconds: isInterval ? intervalSeconds : "",
      task,
      skills,
      workspaceRoot,
      contextFrom,
      scriptCommand,
      scriptTimeout,
      scriptCwd,
      skipMemory,
    });
    if (Object.keys(body).length === 0) {
      onClose();
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await patch(`/schedules/${encodeURIComponent(schedule.name)}`, body);
      onSaved(`Schedule "${schedule.name}" updated.`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError("This daemon cannot edit schedules yet — update Iron Jarvis, or delete and re-add it.");
      } else {
        setError(err instanceof ApiError ? err.message : String(err));
      }
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      label={`Edit schedule ${schedule.name}`}
      onClose={onClose}
      busy={saving}
      className="w-full max-w-lg"
      testId="schedule-editor"
    >
      <header className="flex shrink-0 items-center gap-2 border-b hairline px-4 py-3">
        <Pencil size={14} className="text-accent-soft" aria-hidden />
        <h2 className="text-[13px] font-semibold tracking-wide text-zinc-100">
          Edit “{schedule.name}”
        </h2>
      </header>
      <form onSubmit={save} className="min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
        <div>
          <label className="mb-1 block text-[10px] uppercase tracking-[0.12em] text-zinc-500">
            Name
          </label>
          <input
            value={name}
            readOnly
            aria-label="Schedule name"
            title="The name is the schedule's key — delete and re-add to rename it"
            className="field text-sm opacity-70"
          />
        </div>
        {isOnce ? (
          <div>
            <label className="mb-1 block text-[10px] uppercase tracking-[0.12em] text-zinc-500">
              Run at
            </label>
            <input
              type="datetime-local"
              value={runAt}
              onChange={(e) => setRunAt(e.target.value)}
              aria-label="Run at"
              className="field text-sm"
            />
            <div className="mt-1 text-[11px] text-zinc-600">
              Currently {schedule.run_at ? new Date(schedule.run_at).toLocaleString() : "—"}; leave
              empty to keep it.
            </div>
          </div>
        ) : isInterval ? (
          <div>
            <label className="mb-1 block text-[10px] uppercase tracking-[0.12em] text-zinc-500">
              Every (seconds)
            </label>
            <input
              type="number"
              min={1}
              value={intervalSeconds}
              onChange={(e) => setIntervalSeconds(e.target.value)}
              aria-label="Interval seconds"
              className="field text-sm"
            />
          </div>
        ) : (
          <div>
            <label className="mb-1 block text-[10px] uppercase tracking-[0.12em] text-zinc-500">
              Cron expression
            </label>
            <input
              value={cron}
              onChange={(e) => setCron(e.target.value)}
              aria-label="Cron expression"
              className="field font-mono text-sm"
            />
            <div className="mt-1 font-mono text-[11px] text-zinc-600">
              min hour day month weekday{CRON_TO_LABEL.get(cron.trim()) ? ` · ${CRON_TO_LABEL.get(cron.trim())}` : ""}
            </div>
          </div>
        )}
        {schedule.kind === "task" && (
          <>
            <div>
              <label className="mb-1 block text-[10px] uppercase tracking-[0.12em] text-zinc-500">
                The task
              </label>
              <textarea
                value={task}
                onChange={(e) => setTask(e.target.value)}
                rows={3}
                aria-label="Task text"
                className="field resize-y text-sm leading-relaxed"
              />
            </div>
            <SkillChips value={skills} onChange={setSkills} scope={schedule.name} testId="edit-skills" />
            <KnobFields
              others={others}
              self={schedule.name}
              workspaceRoot={workspaceRoot}
              setWorkspaceRoot={setWorkspaceRoot}
              contextFrom={contextFrom}
              setContextFrom={setContextFrom}
              scriptCommand={scriptCommand}
              setScriptCommand={setScriptCommand}
              scriptTimeout={scriptTimeout}
              setScriptTimeout={setScriptTimeout}
              scriptCwd={scriptCwd}
              setScriptCwd={setScriptCwd}
              skipMemory={skipMemory}
              setSkipMemory={setSkipMemory}
            />
          </>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
        <footer className="flex items-center justify-end gap-2 border-t hairline pt-3">
          <button type="button" onClick={onClose} disabled={saving} className="btn-ghost py-1.5 text-xs">
            Cancel
          </button>
          <button type="submit" disabled={saving} data-testid="schedule-editor-save" className="btn-accent py-1.5 text-xs">
            {saving ? <LoaderInline label="Saving…" /> : "Save changes"}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
