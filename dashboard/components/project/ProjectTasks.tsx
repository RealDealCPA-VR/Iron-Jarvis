"use client";

// Direct an agent to do real work INSIDE this project's folder, with a chosen
// deliverable. Flow: Run → POST /task/plan (which permissioned tools it needs) →
// a single bundled "Allow all & run" grant → POST /task with allow_tools → live
// status strip polling the session (NESTED shape) → on completion the summary
// (chat) or "Saved: <path>" plus any produced media/files inline.

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import {
  Bot,
  Check,
  ExternalLink,
  Music,
  Paperclip,
  Play,
  Send,
  ShieldCheck,
  X,
} from "lucide-react";
import { get, post, ApiError, API_BASE, ijToken } from "@/lib/api";
import { composerChipClass } from "@/lib/composerChips";
import { useApi, usePolledApi } from "@/lib/useApi";
import type { AgentsResponse, Assignment, SessionDetail, SessionView } from "@/lib/types";
import { Card, Badge, ErrorNote, LoaderInline, SuccessNote } from "@/components/ui";
import {
  AssignmentRow,
  bareAssignee,
  canBeAssignee,
  queuedSentence,
} from "@/components/agents/AgentInbox";
import { SessionStatusBadge } from "@/components/sessions/SessionStatusBadge";
import { plainText } from "@/components/Markdown";
import { timeAgo } from "@/lib/format";
import { agentLabel } from "@/lib/agentWorlds";
import { VoiceInput, appendDictation } from "@/components/VoiceInput";

/** Deliverable choices for POST /projects/{id}/task (mirrors the backend). */
const TASK_OUTPUTS = [
  { value: "chat", label: "Reply in chat" },
  { value: "md", label: "Markdown (.md)" },
  { value: "docx", label: "Word (.docx)" },
  { value: "xlsx", label: "Excel (.xlsx)" },
  { value: "pdf", label: "PDF (.pdf)" },
  { value: "txt", label: "Text (.txt)" },
  { value: "csv", label: "CSV (.csv)" },
  { value: "pptx", label: "PowerPoint (.pptx)" },
  { value: "html", label: "HTML (.html)" },
] as const;
type TaskOutput = (typeof TASK_OUTPUTS)[number]["value"];

/** POST /projects/{id}/task → the started session FLAT, plus the deliverable. */
interface ProjectTaskStart extends SessionView {
  output: string;
  target_path?: string | null;
}

/** v1.296.0: the same POST with an `assignee` answers 202 with the QUEUED
 *  assignment instead of a session — the job waits for that agent. */
interface ProjectTaskQueued {
  assignment: Assignment;
  queued: true;
}

function isQueued(r: ProjectTaskStart | ProjectTaskQueued): r is ProjectTaskQueued {
  return Boolean(r) && (r as ProjectTaskQueued).queued === true;
}

/** "Assign to" choices (v1.296.0): "" = you, run now (today's behaviour);
 *  every builtin by name; every custom agent as "custom:<name>" — the roster
 *  names the queue is keyed by. Pure so a test can pin the wire names. */
export function assigneeOptions(
  agents: AgentsResponse | null | undefined,
): Array<{ value: string; label: string }> {
  const out: Array<{ value: string; label: string }> = [];
  for (const b of agents?.builtin ?? []) {
    // A coordinator (supervisor, planner) is refused by the daemon as an
    // assignee — see `canBeAssignee`; guide and the rest are offered.
    // v1.316.0: a built-in reads as a name ("Builder"); the VALUE stays the id.
    if (typeof b === "string" && b && canBeAssignee(b)) out.push({ value: b, label: agentLabel(b, { builtin: true }) });
  }
  for (const d of agents?.dynamic ?? []) {
    if (d && typeof d.name === "string" && d.name) {
      out.push({ value: `custom:${d.name}`, label: `${d.name} (yours)` });
    }
  }
  return out;
}

/** One `GET /projects/{id}/team` row, as far as "Assign to" reads it. */
export interface TeamAssigneeRow {
  name?: string;
  label?: string;
  missing?: boolean;
}

/** An "Assign to" choice; `group` renders it under an `<optgroup>`. */
export interface AssigneeChoice {
  value: string;
  label: string;
  group?: string;
}

export const TEAM_GROUP = "This project's team";
export const OTHERS_GROUP = "Other agents";

/** v1.309.0: "Assign to" for a project WITH a team. v1.308.0 made the team
 *  real for objectives (delegate/spawn refuse an off-team agent), but this
 *  picker still offered every agent flat — the same project ran objectives
 *  through its team on one screen and handed jobs to anyone on another. The
 *  team comes FIRST, under its own heading; everyone else follows under
 *  "Other agents" rather than vanishing, because an assignment is a different
 *  door from a mission and the daemon still accepts any assignee (the
 *  verifier's adjustment: default to the team, do not refuse the rest).
 *  Seats that cannot take a queued job are left off the team list — a
 *  remote (the queue refuses `remote:*`), a coordinator (`canBeAssignee`)
 *  and a member the roster no longer knows. null = no assignable team, so
 *  the caller keeps today's flat list. Pure so a test can pin the order. */
export function teamAssigneeChoices(
  team: TeamAssigneeRow[] | null | undefined,
  agents: AgentsResponse | null | undefined,
): AssigneeChoice[] | null {
  const all = assigneeOptions(agents);
  const onTeam: AssigneeChoice[] = [];
  const seen = new Set<string>();
  for (const t of team ?? []) {
    const name = typeof t?.name === "string" ? t.name.trim() : "";
    if (!name || t.missing || seen.has(name)) continue;
    if (name.startsWith("remote:") || !canBeAssignee(name)) continue;
    seen.add(name);
    const known = all.find((o) => o.value === name);
    onTeam.push({ value: name, label: known?.label ?? bareAssignee(name), group: TEAM_GROUP });
  }
  if (onTeam.length === 0) return null;
  const others = all
    .filter((o) => !seen.has(o.value))
    .map((o) => ({ ...o, group: OTHERS_GROUP }));
  return [...onTeam, ...others];
}

/** The "" (no assignee) choice's words: the user runs it now. */
export const SELF_LABEL = "Me, run it now";

/** v1.329.0 (calm chat W4 F6): a ghost select/input in the bare form, the
 *  composer's chip language (transparent at rest, a soft fill on hover). */
const GHOST_FIELD = `${composerChipClass()} bg-transparent`;

/** The shared mic button, ghosted while idle (its listening look, the rose
 *  ring and fill, is left alone: that state must stay loud). */
const GHOST_MIC =
  "[&>button[aria-pressed=false]]:border-transparent [&>button[aria-pressed=false]]:bg-transparent [&>button[aria-pressed=false]:hover]:bg-white/[0.06]";

/** How many of the project's assignments the compact list shows. */
const MAX_ASSIGNMENTS = 10;

/** One permissioned tool the task is likely to need (POST …/task/plan). */
interface PlanTool {
  name: string;
  perm_key: string;
  why: string;
}
interface TaskPlan {
  tools: PlanTool[];
  note?: string;
}

/** One artifact a session GENERATED (GET /artifacts?session_id=…). */
interface TaskArtifact {
  name: string;
  version: number;
  kind: string;
  filename: string;
  media: "image" | "video" | "audio" | null;
  size: number;
  created_at: string;
  url: string;
}

const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

/** v1.315.0 (UX wave 3): what the user ASKED, read back out of a run's task.
 *  `Session.task` for a run started from this page's Run box is NOT what the
 *  user typed: `POST /projects/{id}/task` (routes/projects.py
 *  run_project_task) stores the whole prompt it built — "You are working
 *  directly inside the project folder…\nTask: <words>\n\nDeliverable: …\nWork
 *  autonomously…" (the first line only when the project has a folder), and a
 *  QUEUED run gets "\n\nWhy this was assigned: …" appended by the assignment
 *  dispatcher. Shown raw, every row of a folder project led with the same two
 *  lines of preamble. So: the text after the first line-initial "Task: ", up
 *  to the LAST "\n\nDeliverable:" (the route appends its own Deliverable line
 *  after the user's words, so a "Deliverable:" the user typed stays theirs),
 *  else up to "\n\nWhy this was assigned:", else the end. Any other task (a
 *  chat escalation, an older run, an API caller) is returned unchanged.
 *  tests/test_project_task_prompt_shape_v1315.py pins the backend half of this
 *  contract: change that prompt's shape and it goes red. */
export function runAsk(task: string): string {
  const t = task.replace(/\r\n/g, "\n");
  const m = /(^|\n)Task: /.exec(t);
  if (!m) return task;
  const start = m.index + m[0].length;
  let end = t.lastIndexOf("\n\nDeliverable:");
  if (end < start) end = t.indexOf("\n\nWhy this was assigned:", start);
  if (end < start) end = t.length;
  const ask = t.slice(start, end).trim();
  return ask || task;
}

/** Media tags can't send the Authorization header — the token rides as ?token=. */
function mediaSrc(url: string): string {
  const t = ijToken();
  const sep = url.includes("?") ? "&" : "?";
  return `${API_BASE}${url}${t ? `${sep}token=${encodeURIComponent(t)}` : ""}`;
}

export function ProjectTasks({
  projectId,
  hasRoot,
  sessions,
  reloadSessions,
  assigneeChoices,
  selfLabel,
  team,
  bare = false,
}: {
  projectId: string;
  hasRoot: boolean;
  /** v1.304.0 (Agents → project world): the ONLY "Assign to" choices, in
   *  place of every agent the daemon lists — a world offers its team. Absent
   *  = today's list (and today's GET /agents). */
  assigneeChoices?: AssigneeChoice[];
  /** v1.309.0: the project's team rows (`GET /projects/{id}/team` → `team`)
   *  when the caller already reads them (the project page's header does);
   *  null = the caller's read has not landed or failed (no team yet).
   *  Absent = read it here, so every door to this panel (the project page,
   *  chat's project surfaces) offers the same team first. */
  team?: TeamAssigneeRow[] | null;
  /** v1.304.0: the words on the "" (no assignee) choice. Absent =
   *  SELF_LABEL ("Me, run it now"; v1.329.0 dropped the em-dash aside). The
   *  world says "Whole team — Jarvis decides": the same plain project task,
   *  no assignee on the wire. */
  selfLabel?: string;
  /** v1.329.0 (calm chat W4 F6): no card. The chat's Tasks tab draws the form
   *  as plain, hairline-separated rows with ghost controls and one primary
   *  action, so it never reads as a second composer. The project page keeps
   *  the card (absent = false). */
  bare?: boolean;
  /** This project's recent sessions, fetched ONCE by the parent workspace
   *  (avoids a second identical GET /projects/{id} just to read `sessions`). */
  sessions: SessionView[];
  /** Ask the parent to re-fetch its sessions (e.g. once a run finishes). */
  reloadSessions?: () => void;
}) {
  const [taskText, setTaskText] = useState("");
  const [taskOutput, setTaskOutput] = useState<TaskOutput>("chat");
  const [taskFilename, setTaskFilename] = useState("");
  const [taskStarting, setTaskStarting] = useState(false);
  const [taskError, setTaskError] = useState<string | null>(null);
  /** The last started run (start response, immutable). */
  const [taskRun, setTaskRun] = useState<ProjectTaskStart | null>(null);
  /** Latest polled view of that run's session. */
  const [taskSession, setTaskSession] = useState<SessionView | null>(null);
  const [taskPollError, setTaskPollError] = useState<string | null>(null);
  /** Artifacts the finished run produced (null = not fetched for this run yet). */
  const [taskArtifacts, setTaskArtifacts] = useState<TaskArtifact[] | null>(null);
  /** Whether the file deliverable ACTUALLY exists on disk (null = not checked). */
  const [deliverable, setDeliverable] = useState<{ exists: boolean; size: number } | null>(
    null,
  );

  const [cancelling, setCancelling] = useState(false);

  /* v1.296.0 — ASSIGN TO. "" = you, run now (the whole flow above, exactly
     as before); a roster name queues the job for that agent instead. */
  const [assignee, setAssignee] = useState("");
  const [queuedNote, setQueuedNote] = useState<string | null>(null);
  // A caller that names the choices (a project world's team) needs no catalog.
  const { data: agentsData } = useApi<AgentsResponse>(assigneeChoices ? null : "/agents");
  // v1.309.0: the team, first — read here only when the caller did not hand
  // it over (an older daemon's 404 leaves it null: today's flat list).
  const ownTeam = useApi<{ team?: TeamAssigneeRow[] }>(
    assigneeChoices || team !== undefined
      ? null
      : `/projects/${encodeURIComponent(projectId)}/team`,
  );
  const teamRows = team !== undefined ? team : (ownTeam.data?.team ?? null);
  const assignees: AssigneeChoice[] =
    assigneeChoices ?? teamAssigneeChoices(teamRows, agentsData) ?? assigneeOptions(agentsData);
  // Ungrouped choices first, then each heading in first-seen order (team,
  // then the other agents) — a flat list renders exactly as before.
  const ungrouped = assignees.filter((o) => !o.group);
  const groups: Array<[string, AssigneeChoice[]]> = [];
  for (const o of assignees) {
    if (!o.group) continue;
    const slot = groups.find(([g]) => g === o.group);
    if (slot) slot[1].push(o);
    else groups.push([o.group, [o]]);
  }
  // The chosen assignee must still exist in the list the user can see; a
  // custom agent deleted since is not quietly posted anyway.
  const effectiveAssignee = assignees.some((o) => o.value === assignee) ? assignee : "";
  // v1.316.0: the button and its queued note name the agent the same way
  // the picker does (the option's label), never a raw lowercase id.
  const assigneeName =
    assignees.find((o) => o.value === effectiveAssignee)?.label ?? bareAssignee(effectiveAssignee);
  // This project's queue — the same rows the agents' inboxes show. An older
  // daemon 404s and the list stays absent.
  const asg = usePolledApi<{ assignments: Assignment[] }>(
    `/assignments?project_id=${encodeURIComponent(projectId)}`,
    10000,
  );
  const assignments =
    asg.error?.status === 404 ? [] : (asg.data?.assignments ?? []).slice(0, MAX_ASSIGNMENTS);

  /* Two-tap tool permission — planning → bundled grant → run. */
  const [planning, setPlanning] = useState(false);
  const [pendingPlan, setPendingPlan] = useState<TaskPlan | null>(null);
  const [checkedKeys, setCheckedKeys] = useState<Record<string, boolean>>({});
  const [planNote, setPlanNote] = useState<string | null>(null);

  const taskDone = taskSession !== null && TERMINAL_STATUSES.has(taskSession.status);

  const base = `/projects/${encodeURIComponent(projectId)}`;
  // Where the active run id is stashed so it survives a tab-switch/reload.
  const runKey = `ij:projtask:${projectId}`;

  // Task history: this project's recent runs — passed down from the parent
  // workspace's already-live /projects/{id} fetch (no duplicate poller here).
  const history = sessions ?? [];

  // Rehydrate the active run on mount (per project) so a running task keeps its
  // live strip across a reload; clearing taskSession forces a fresh poll.
  useEffect(() => {
    if (typeof window === "undefined") return;
    let saved: ProjectTaskStart | null = null;
    try {
      const raw = window.localStorage.getItem(runKey);
      if (raw) saved = JSON.parse(raw) as ProjectTaskStart;
    } catch {
      saved = null;
    }
    if (saved?.id) {
      setTaskRun(saved);
      setTaskSession(null);
      setTaskArtifacts(null);
      setDeliverable(null);
    } else {
      setTaskRun(null);
      setTaskSession(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Refresh the history once a run reaches a terminal state (it's now in it) —
  // ask the parent to re-fetch its sessions rather than polling our own.
  useEffect(() => {
    if (taskDone) reloadSessions?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskDone]);

  // Watch the started session every 2s until terminal.
  useEffect(() => {
    if (!taskRun || taskDone) return;
    let alive = true;
    const tick = async () => {
      try {
        // NB: GET /sessions/{id} returns {session, transcript} — NESTED.
        const d = await get<SessionDetail>(`/sessions/${encodeURIComponent(taskRun.id)}`);
        if (!alive) return;
        setTaskSession(d.session);
        setTaskPollError(null);
      } catch (err) {
        if (!alive) return;
        if (err instanceof ApiError && err.status === 404) {
          // The session no longer exists (deleted from the Sessions page, or
          // the database was reset). The run is rehydrated from localStorage
          // on every mount, so without this the strip would retry a 404
          // every 2s forever — "status check failed — retrying…" with no way
          // out short of clearing browser storage. Drop the run and forget it.
          try {
            window.localStorage.removeItem(runKey);
          } catch {
            /* storage unavailable — nothing to forget */
          }
          setTaskRun(null);
          setTaskSession(null);
          setTaskPollError(null);
          return;
        }
        setTaskPollError(errText(err));
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [taskRun, taskDone, runKey]);

  // On completion, VERIFY the file deliverable actually exists — the strip used
  // to assert "Saved: <path>" from the intended path even if the agent never
  // wrote it (fabricated success). Honest signal instead.
  useEffect(() => {
    if (!taskDone || !taskRun || deliverable !== null) return;
    if (taskRun.output === "chat" || !taskRun.target_path) return;
    if (taskSession?.status !== "completed") return;
    let alive = true;
    get<{ exists: boolean; size: number }>(
      `${base}/deliverable?path=${encodeURIComponent(taskRun.target_path)}`,
    )
      .then((d) => {
        if (alive) setDeliverable({ exists: d.exists, size: d.size });
      })
      .catch(() => {
        if (alive) setDeliverable(null); // couldn't check — don't claim either way
      });
    return () => {
      alive = false;
    };
  }, [taskDone, taskRun, taskSession?.status, deliverable, base]);

  // Once a run reaches a terminal status, fetch what it produced (once per run).
  useEffect(() => {
    if (!taskDone || !taskRun || taskArtifacts !== null) return;
    let alive = true;
    get<{ artifacts: TaskArtifact[] }>(
      `/artifacts?session_id=${encodeURIComponent(taskRun.id)}`,
    )
      .then((d) => {
        if (alive) setTaskArtifacts(d.artifacts ?? []);
      })
      .catch(() => {
        if (alive) setTaskArtifacts([]);
      });
    return () => {
      alive = false;
    };
  }, [taskDone, taskRun, taskArtifacts]);

  /** Fire the task with an explicit tool grant (the approved perm_keys). */
  async function startTask(allowTools: string[]) {
    const text = taskText.trim();
    if (!text) return;
    setTaskStarting(true);
    setTaskError(null);
    setQueuedNote(null);
    try {
      const body: Record<string, unknown> = {
        text,
        output: taskOutput,
        allow_tools: allowTools,
      };
      if (taskOutput !== "chat" && taskFilename.trim()) body.filename = taskFilename.trim();
      // v1.296.0: ONLY with an assignee — without one the body is byte-for-
      // byte what it was, so an older daemon never sees a key it would 422 on.
      if (effectiveAssignee) body.assignee = effectiveAssignee;
      const started = await post<ProjectTaskStart | ProjectTaskQueued>(`${base}/task`, body);
      if (isQueued(started)) {
        // 202: the job waits for the agent. No strip — there is no session
        // yet; the assignments list below shows it in line.
        setQueuedNote(queuedSentence(started.assignment?.assignee || effectiveAssignee));
        setTaskText("");
        setTaskFilename("");
        setPendingPlan(null);
        setCheckedKeys({});
        asg.reload();
        return;
      }
      setTaskRun(started); // replaces any previous strip
      try {
        // Persist so the live run survives a tab-switch/reload (rehydrated on mount).
        window.localStorage.setItem(runKey, JSON.stringify(started));
      } catch {
        /* storage unavailable — the run still works, just won't rehydrate */
      }
      setTaskSession(started); // flat SessionView snapshot until the first poll
      setTaskPollError(null);
      setTaskArtifacts(null); // new run → re-fetch artifacts when it finishes
      setDeliverable(null); // new run → re-verify the deliverable
      setTaskText("");
      setTaskFilename("");
      setPendingPlan(null);
      setCheckedKeys({});
    } catch (err) {
      setTaskError(errText(err));
    } finally {
      setTaskStarting(false);
    }
  }

  // Run click: plan first, then either show a bundled grant panel or run through.
  async function onRun() {
    const text = taskText.trim();
    if (!text || taskStarting || planning) return;
    setTaskError(null);
    setPlanNote(null);
    setPendingPlan(null);
    setPlanning(true);
    let plan: TaskPlan | null = null;
    try {
      plan = await post<TaskPlan>(`${base}/task/plan`, { text });
    } catch {
      plan = null; // planning is best-effort — proceed without a grant
    } finally {
      setPlanning(false);
    }
    const tools = plan?.tools ?? [];
    if (tools.length > 0) {
      setCheckedKeys(Object.fromEntries(tools.map((t) => [t.perm_key, true])));
      setPendingPlan(plan);
      return; // wait for the user to confirm the grant
    }
    if (plan?.note) setPlanNote(plan.note);
    await startTask([]);
  }

  function confirmGrant() {
    const keys = (pendingPlan?.tools ?? [])
      .filter((t) => checkedKeys[t.perm_key])
      .map((t) => t.perm_key);
    void startTask(keys);
  }

  function cancelGrant() {
    setPendingPlan(null);
    setCheckedKeys({});
  }

  /** Stop a running task — a long/looping agent shouldn't strand the strip. */
  async function cancelRun() {
    if (!taskRun || cancelling) return;
    setCancelling(true);
    try {
      await post(`/sessions/${encodeURIComponent(taskRun.id)}/cancel`);
      // the 2s poll picks up the 'cancelled' status
    } catch (err) {
      setTaskPollError(errText(err));
    } finally {
      setCancelling(false);
    }
  }

  // A plain function, never a component defined in render (that would be a
  // new type each render and remount the textarea on every keystroke).
  const frame = (children: ReactNode) =>
    bare ? (
      <section data-testid="project-tasks" data-bare="true" aria-label="Run a task">
        <div className="mb-2">
          <h2 className="text-[13px] font-medium text-zinc-200">Run a task</h2>
          <p className="text-[12px] text-zinc-500">
            An agent works in this project and reports back here.
          </p>
        </div>
        {children}
      </section>
    ) : (
      <Card title="Run a task" icon={<Bot size={15} />}>
        {children}
      </Card>
    );

  return frame(
      <div className="space-y-2">
        <div
          className={
            bare ? "border-b hairline transition-colors focus-within:border-accent/40" : undefined
          }
        >
        <textarea
          value={taskText}
          onChange={(e) => setTaskText(e.target.value)}
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
              e.preventDefault();
              void onRun();
            }
          }}
          rows={3}
          aria-label="Task for an agent in this project"
          placeholder="Ask an agent to do something in this project… (e.g. 'summarize every PDF in here into one report')"
          className={
            bare
              ? "block w-full resize-y bg-transparent py-1.5 text-[14px] text-zinc-100 outline-none placeholder:text-zinc-500"
              : "field resize-y text-sm"
          }
        />
        </div>
        <div className={`flex flex-wrap items-center ${bare ? "gap-1" : "gap-2"}`}>
          {/* Dictate the task (offline in the desktop app), same as chat + Build. */}
          <VoiceInput
            size="sm"
            className={bare ? GHOST_MIC : ""}
            onTranscript={(chunk) => setTaskText((p) => appendDictation(p, chunk))}
          />
          {/* v1.315.0 (UX wave 3): a 10rem floor. With a bare `min-w-0
              flex-1` on this wrap row the select gave up ALL its width before
              anything wrapped — on a phone it was a ~36px chevron box and you
              could not see what the task would produce. Now the assignee and
              Run wrap to the next line instead. */}
          <select
            aria-label="Deliverable"
            value={taskOutput}
            onChange={(e) => setTaskOutput(e.target.value as TaskOutput)}
            className={bare ? `${GHOST_FIELD} min-w-[10rem]` : "field min-w-[10rem] flex-1 text-sm"}
          >
            {TASK_OUTPUTS.map((o) => (
              <option key={o.value} value={o.value} disabled={o.value !== "chat" && !hasRoot}>
                {o.label}
              </option>
            ))}
          </select>
          {taskOutput !== "chat" && (
            <input
              value={taskFilename}
              onChange={(e) => setTaskFilename(e.target.value)}
              placeholder="filename (optional)"
              aria-label="Deliverable filename"
              className={
                bare
                  ? `${GHOST_FIELD} w-44 min-w-0 font-mono placeholder:text-zinc-500 focus:bg-white/[0.06]`
                  : "field w-44 min-w-0 font-mono text-sm"
              }
            />
          )}
          {/* ASSIGN TO (v1.296.0). Absent on a daemon that lists no agents
              — the select then holds only "You" and reads as today. */}
          <select
            aria-label="Assign to"
            data-testid="project-task-assignee"
            value={effectiveAssignee}
            onChange={(e) => setAssignee(e.target.value)}
            className={
              bare ? `${GHOST_FIELD} min-w-[9rem]` : "field min-w-[9rem] flex-1 text-sm sm:w-40 sm:flex-none"
            }
            title="Run it now yourself, or queue it for an agent. A queued task runs when that agent is free."
          >
            <option value="">{selfLabel || SELF_LABEL}</option>
            {ungrouped.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
            {groups.map(([g, opts]) => (
              <optgroup key={g} label={g}>
                {opts.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void onRun()}
            disabled={taskStarting || planning || pendingPlan !== null || !taskText.trim()}
            title={
              effectiveAssignee
                ? `Queue this for ${assigneeName}. It runs when ${assigneeName} is free.`
                : "Start an agent session on this task"
            }
            className={bare ? "btn-accent ml-auto shrink-0" : "btn-accent shrink-0"}
          >
            {taskStarting ? (
              <LoaderInline label={effectiveAssignee ? "Queueing…" : "Starting…"} />
            ) : planning ? (
              <LoaderInline label="Checking…" />
            ) : effectiveAssignee ? (
              <>
                <Send size={13} /> Queue for {assigneeName}
              </>
            ) : (
              <>
                <Send size={13} /> Run
              </>
            )}
          </button>
        </div>

        {queuedNote && (
          <SuccessNote>
            <span data-testid="project-task-queued">{queuedNote}</span>
          </SuccessNote>
        )}

        {!hasRoot && (
          <p className="text-[11px] text-zinc-600">
            A file deliverable needs a project folder. This project has none, so only
            “Reply in chat” is available.
          </p>
        )}

        {/* Bundled tool-permission grant — one confirm covers the task.
            v1.329.0 (calm chat wave 5, G4): the warning TONE token, which
            every theme re-inks (the literal amber read on Daylight only
            through the generated overrides). In the calm (bare) form it is a
            plain section over a hairline, like the run row below it. */}
        {pendingPlan && pendingPlan.tools.length > 0 && (
          <div
            data-testid="project-task-grant"
            className={
              bare
                ? "border-t hairline pt-2.5"
                : "rounded-lg border border-tone-warn/25 bg-tone-warn/[0.06] px-3 py-2.5"
            }
          >
            <div className="flex items-center gap-1.5 text-xs font-medium text-tone-warn">
              <ShieldCheck size={13} /> This task will use these tools
            </div>
            <ul className="mt-2 space-y-1.5">
              {pendingPlan.tools.map((t) => (
                <li key={t.perm_key} className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    checked={checkedKeys[t.perm_key] ?? false}
                    onChange={(e) =>
                      setCheckedKeys((m) => ({ ...m, [t.perm_key]: e.target.checked }))
                    }
                    className="mt-0.5 shrink-0 accent-[color:var(--accent,#22d3ee)]"
                    aria-label={`Allow ${t.name}`}
                  />
                  <div className="min-w-0">
                    <div className="text-xs font-medium text-zinc-200">{t.name}</div>
                    {t.why && <div className="text-[11px] text-zinc-500">{t.why}</div>}
                  </div>
                </li>
              ))}
            </ul>
            {pendingPlan.note && (
              <p className="mt-2 text-[11px] text-zinc-500">{pendingPlan.note}</p>
            )}
            <div className="mt-2.5 flex items-center gap-2">
              <button
                type="button"
                onClick={confirmGrant}
                disabled={taskStarting}
                className="btn-accent"
              >
                {taskStarting ? (
                  <LoaderInline label="Starting…" />
                ) : (
                  <>
                    <Check size={13} /> Allow all & run
                  </>
                )}
              </button>
              <button
                type="button"
                onClick={cancelGrant}
                disabled={taskStarting}
                className="btn-ghost"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        {planNote && !pendingPlan && <p className="text-[11px] text-zinc-500">{planNote}</p>}

        {taskError && <ErrorNote>{taskError}</ErrorNote>}

        {taskRun && (
          <div
            data-testid="project-task-run"
            className={
              bare
                ? "border-t hairline pt-2.5"
                : "rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2"
            }
          >
            <div className="flex items-center justify-between gap-3">
              {!taskDone ? (
                <span className="flex items-center gap-2.5">
                  <span className="text-xs text-zinc-400">
                    <LoaderInline label="Agent working…" />
                  </span>
                  <button
                    type="button"
                    onClick={() => void cancelRun()}
                    disabled={cancelling}
                    className="inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[12px] font-medium text-tone-danger transition-colors hover:bg-white/[0.06] disabled:opacity-50"
                  >
                    <X size={11} /> {cancelling ? "Stopping…" : "Stop"}
                  </button>
                </span>
              ) : taskSession ? (
                // Amber for "needs you" / "with failures" (v1.227.0) — a
                // finished task that fell short must not wear green beside
                // its own "Task NOT complete" summary.
                <SessionStatusBadge session={taskSession} />
              ) : (
                <Badge value="unknown" />
              )}
              <Link
                href={`/sessions/${encodeURIComponent(taskRun.id)}`}
                className="shrink-0 text-[11px] text-accent-soft transition-colors hover:text-accent"
              >
                open session →
              </Link>
            </div>

            {!taskDone && taskPollError && (
              <p className="mt-1.5 text-[11px] text-zinc-500">
                status check failed ({taskPollError}), retrying…
              </p>
            )}

            {taskDone && taskSession?.status === "completed" && (
              <>
                {taskRun.output !== "chat" && taskRun.target_path && (
                  deliverable && !deliverable.exists ? (
                    <div
                      data-testid="project-task-not-written"
                      className="mt-1.5 flex items-start gap-1.5 text-xs text-tone-warn"
                    >
                      <span className="shrink-0 font-medium">Not written:</span>
                      <span className="min-w-0">
                        the agent finished but{" "}
                        <span className="font-mono">{taskRun.target_path}</span> isn’t on disk.
                        Open the session to see what happened.
                      </span>
                    </div>
                  ) : (
                    <div className="mt-1.5 flex items-center gap-1.5 text-xs text-zinc-300">
                      <span className="shrink-0 text-zinc-500">Saved:</span>
                      <span className="min-w-0 truncate font-mono" title={taskRun.target_path}>
                        {taskRun.target_path}
                      </span>
                      {deliverable?.exists && deliverable.size > 0 && (
                        <span className="shrink-0 text-zinc-500">
                          · {Math.max(1, Math.round(deliverable.size / 1024))} KB
                        </span>
                      )}
                      {deliverable?.exists && (
                        <a
                          href={mediaSrc(
                            `/creative/file-by-path?path=${encodeURIComponent(taskRun.target_path)}`,
                          )}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="Open the produced file"
                          className="inline-flex shrink-0 items-center gap-0.5 text-accent-soft transition-colors hover:text-accent"
                        >
                          <ExternalLink size={11} /> open
                        </a>
                      )}
                    </div>
                  )
                )}
                {taskSession.summary ? (
                  <div
                    className={`mt-1.5 whitespace-pre-wrap text-xs text-zinc-300 ${
                      taskRun.output === "chat" ? "max-h-56 overflow-y-auto" : "line-clamp-3"
                    }`}
                  >
                    {taskSession.summary}
                  </div>
                ) : (
                  <p className="mt-1.5 text-xs text-zinc-500">
                    The agent finished without a summary. Open the session for the full
                    transcript.
                  </p>
                )}
              </>
            )}

            {taskDone && taskSession?.status !== "completed" && (
              <p
                data-testid="project-task-failed"
                className="mt-1.5 whitespace-pre-wrap text-xs text-tone-danger"
              >
                {taskSession?.summary ||
                  `The session ${taskSession?.status} without a summary. Open it for details.`}
              </p>
            )}

            {/* What the run PRODUCED — media as thumbs, else a chip. */}
            {taskDone && taskArtifacts && taskArtifacts.length > 0 && (
              <div className="mt-2.5 border-t hairline pt-2.5">
                <div className="mb-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                  Produced
                </div>
                <div className="flex flex-wrap gap-2">
                  {taskArtifacts.map((a) =>
                    a.media === "image" ? (
                      <Link
                        key={a.name}
                        href="/creative"
                        title={a.filename}
                        className="block overflow-hidden rounded-lg border border-white/10 transition-colors hover:border-accent/40"
                      >
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img
                          src={mediaSrc(a.url)}
                          alt={a.filename}
                          className="h-14 w-14 object-cover"
                        />
                      </Link>
                    ) : a.media === "video" ? (
                      <Link
                        key={a.name}
                        href="/creative"
                        title={a.filename}
                        className="relative flex h-14 w-14 items-center justify-center rounded-lg border border-white/10 bg-black/40 transition-colors hover:border-accent/40"
                      >
                        <Play size={18} className="text-zinc-200" />
                      </Link>
                    ) : a.media === "audio" ? (
                      <Link
                        key={a.name}
                        href="/creative"
                        title={a.filename}
                        className="inline-flex max-w-[12rem] items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.02] px-2.5 py-1.5 text-[11px] text-zinc-300 transition-colors hover:border-accent/40"
                      >
                        <Music size={12} className="shrink-0" />
                        <span className="min-w-0 truncate font-mono">{a.filename}</span>
                      </Link>
                    ) : (
                      <Link
                        key={a.name}
                        href="/creative"
                        title={a.filename}
                        className="inline-flex max-w-[12rem] items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.02] px-2.5 py-1.5 text-[11px] text-zinc-300 transition-colors hover:border-accent/40"
                      >
                        <Paperclip size={12} className="shrink-0" />
                        <span className="min-w-0 truncate font-mono">{a.filename}</span>
                      </Link>
                    ),
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* THE PROJECT'S QUEUE (v1.296.0): what is waiting for, running on, or
            recently done by an agent for this project — the same rows the
            agents' inboxes draw. Absent on an older daemon (404) and when
            there is nothing in line. */}
        {assignments.length > 0 && (
          <div data-testid="project-assignments" className="border-t hairline pt-2.5">
            <div className="mb-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-500">
              Assignments
            </div>
            <ul className="space-y-0.5">
              {assignments.map((a) => (
                <AssignmentRow key={a.id} assignment={a} onChanged={asg.reload} showAssignee />
              ))}
            </ul>
          </div>
        )}

        {/* Task history — this project's recent runs, not just the last one. */}
        {history.length > 0 && (
          <div className="border-t hairline pt-2.5">
            <div className="mb-1.5 text-[11px] uppercase tracking-[0.1em] text-zinc-500">
              Recent runs
            </div>
            <ul className="space-y-0.5">
              {history
                .filter((s) => s.id !== taskRun?.id)
                .slice(0, 8)
                .map((s) => {
                  // plainText: a task that did not come from the Run box (a delegation,
                  // a schedule) can be model-written markdown (v1.230.0 rule).
                  const ask = s.task ? plainText(runAsk(s.task)) : "";
                  return (
                    <li key={s.id}>
                      {/* v1.315.0 (UX wave 3): rows that read apart. A row
                          printed `summary || task`, so four runs read "Done.
                          Wrote RESULT.md…" and nothing said what was asked or
                          when. Now what was ASKED leads — `runAsk` reads the
                          user's words back out of the prompt the task route
                          stored (the raw task opens with the same machine
                          preamble on every folder run, so showing it would
                          make the rows identical again) — and the summary
                          follows as its own muted line — still plainText
                          (v1.230.0, U2: markers stripped, not printed) — and
                          each row says when, like the sessions table's
                          Created column. "open →" also shows on keyboard
                          focus, not only on hover; below sm (no hover, no
                          keyboard) it gives its width to the task, which may
                          take two lines. */}
                      <Link
                        href={`/sessions/${encodeURIComponent(s.id)}`}
                        title={ask || undefined}
                        className="group flex items-center gap-2 rounded-md px-1.5 py-1 transition-colors hover:bg-white/[0.03] focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/50"
                      >
                        <SessionStatusBadge session={s} />
                        <span className="min-w-0 flex-1">
                          {ask ? (
                            <span className="line-clamp-2 break-words text-xs text-zinc-200">{ask}</span>
                          ) : null}
                          {s.summary ? (
                            <span
                              className={`block truncate ${
                                ask ? "text-[11px] text-zinc-500" : "text-xs text-zinc-400"
                              }`}
                            >
                              {plainText(s.summary)}
                            </span>
                          ) : null}
                        </span>
                        <span className="shrink-0 text-[11px] tabular-nums text-zinc-500">
                          {timeAgo(s.created_at)}
                        </span>
                        <span className="hidden shrink-0 text-[11px] text-accent-soft opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100 sm:inline">
                          open →
                        </span>
                      </Link>
                    </li>
                  );
                })}
            </ul>
          </div>
        )}
      </div>,
  );
}
