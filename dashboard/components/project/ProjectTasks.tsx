"use client";

// Direct an agent to do real work INSIDE this project's folder, with a chosen
// deliverable. Flow: Run → POST /task/plan (which permissioned tools it needs) →
// a single bundled "Allow all & run" grant → POST /task with allow_tools → live
// status strip polling the session (NESTED shape) → on completion the summary
// (chat) or "Saved: <path>" plus any produced media/files inline.

import { useEffect, useState } from "react";
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
import type { SessionDetail, SessionView } from "@/lib/types";
import { Card, Badge, ErrorNote, LoaderInline } from "@/components/ui";

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
}: {
  projectId: string;
  hasRoot: boolean;
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
        if (alive) setTaskPollError(errText(err));
      }
    };
    void tick();
    const timer = setInterval(() => void tick(), 2000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [taskRun, taskDone]);

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
    try {
      const body: Record<string, unknown> = {
        text,
        output: taskOutput,
        allow_tools: allowTools,
      };
      if (taskOutput !== "chat" && taskFilename.trim()) body.filename = taskFilename.trim();
      const started = await post<ProjectTaskStart>(`${base}/task`, body);
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

  return (
    <Card title="Run a task" icon={<Bot size={15} />}>
      <div className="space-y-2">
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
          className="field resize-y text-sm"
        />
        <div className="flex flex-wrap items-center gap-2">
          <select
            aria-label="Deliverable"
            value={taskOutput}
            onChange={(e) => setTaskOutput(e.target.value as TaskOutput)}
            className="field min-w-0 flex-1 text-sm"
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
              className="field w-44 min-w-0 font-mono text-sm"
            />
          )}
          <button
            type="button"
            onClick={() => void onRun()}
            disabled={taskStarting || planning || pendingPlan !== null || !taskText.trim()}
            title="Start an agent session on this task"
            className="btn-accent shrink-0"
          >
            {taskStarting ? (
              <LoaderInline label="Starting…" />
            ) : planning ? (
              <LoaderInline label="Checking…" />
            ) : (
              <>
                <Send size={13} /> Run
              </>
            )}
          </button>
        </div>

        {!hasRoot && (
          <p className="text-[11px] text-zinc-600">
            A file deliverable needs the project to have a folder — this one has none, so only
            “Reply in chat” is available.
          </p>
        )}

        {/* Bundled tool-permission grant — one confirm covers the task. */}
        {pendingPlan && pendingPlan.tools.length > 0 && (
          <div className="rounded-lg border border-amber-500/25 bg-amber-500/[0.06] px-3 py-2.5">
            <div className="flex items-center gap-1.5 text-xs font-medium text-amber-200">
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
          <div className="rounded-lg border border-white/[0.05] bg-white/[0.02] px-3 py-2">
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
                    className="inline-flex items-center gap-1 rounded-md border border-rose-500/25 px-1.5 py-0.5 text-[11px] font-medium text-rose-300 transition-colors hover:bg-rose-500/[0.1] disabled:opacity-50"
                  >
                    <X size={11} /> {cancelling ? "Stopping…" : "Stop"}
                  </button>
                </span>
              ) : (
                <Badge value={taskSession?.status ?? "unknown"} />
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
                status check failed ({taskPollError}) — retrying…
              </p>
            )}

            {taskDone && taskSession?.status === "completed" && (
              <>
                {taskRun.output !== "chat" && taskRun.target_path && (
                  deliverable && !deliverable.exists ? (
                    <div className="mt-1.5 flex items-start gap-1.5 text-xs text-amber-200">
                      <span className="shrink-0 text-amber-300/80">Not written:</span>
                      <span className="min-w-0">
                        the agent finished but{" "}
                        <span className="font-mono">{taskRun.target_path}</span> isn’t on disk —
                        open the session to see what happened.
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
                    The agent finished without a summary — open the session for the full
                    transcript.
                  </p>
                )}
              </>
            )}

            {taskDone && taskSession?.status !== "completed" && (
              <p className="mt-1.5 whitespace-pre-wrap text-xs text-rose-200">
                {taskSession?.summary ||
                  `The session ${taskSession?.status} without a summary — open it for details.`}
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
                .map((s) => (
                  <li key={s.id}>
                    <Link
                      href={`/sessions/${encodeURIComponent(s.id)}`}
                      className="group flex items-center gap-2 rounded-md px-1.5 py-1 transition-colors hover:bg-white/[0.03]"
                    >
                      <Badge value={s.status} />
                      <span className="min-w-0 flex-1 truncate text-xs text-zinc-400">
                        {s.summary || s.task}
                      </span>
                      <span className="shrink-0 text-[11px] text-accent-soft opacity-0 transition-opacity group-hover:opacity-100">
                        open →
                      </span>
                    </Link>
                  </li>
                ))}
            </ul>
          </div>
        )}
      </div>
    </Card>
  );
}
