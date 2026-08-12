"use client";

// Job post (v1.166.0) — GIVE WORK, not just talk. The round-table below is
// conversation; this card dispatches a real agent session. The default target
// is the Team (a supervisor session that plans and delegates across the
// roster); any single delegable roster agent can take the job directly. Every
// session posted here carries origin "job:agents", which is how the recent-
// jobs list below the form finds its own dispatches in GET /sessions.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowUpRight, Briefcase, Send } from "lucide-react";
import { post, ApiError } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import type { Project, SessionView } from "@/lib/types";
import {
  Badge,
  Card,
  ErrorNote,
  LoaderInline,
  StatusDot,
  SuccessNote,
} from "@/components/ui";
import { timeAgo } from "@/lib/format";
import { SOURCE_LABEL, type AgentSource } from "@/components/agents/identity";
import type { RosterEntry } from "@/components/agents/RosterStrip";

/** Origin stamped on every session this card dispatches — the recent-jobs
 *  list filters GET /sessions down to `origin.startsWith("job:")`. */
export const JOB_ORIGIN = "job:agents";

/** <select> sentinel for the default target. Not a roster name on purpose:
 *  "Team" means a supervisor session that plans and delegates, and the roster
 *  lists supervisor as non-delegable (it carries the delegate tool). */
export const TEAM_TARGET = "__team__";

/** How many recent jobs render before the honest "showing the latest N" line. */
const MAX_JOBS = 8;

/** A roster "Give work" click: which agent to preselect. The nonce makes a
 *  repeat click on the same agent a distinct value, so it still lands. */
export interface JobAssign {
  kind: AgentSource;
  name: string;
  nonce: number;
}

/** kind + BARE name → the roster wire name ("custom:x" / "remote:y";
 *  builtins are bare on the wire already). */
export function wireTarget(kind: AgentSource, name: string): string {
  if (kind === "dynamic") return `custom:${name}`;
  if (kind === "remote") return `remote:${name}`;
  return name;
}

/** The shown name: bare slug — provenance rides in the kind label. */
function bareTargetName(name: string): string {
  if (name.startsWith("custom:")) return name.slice("custom:".length);
  if (name.startsWith("remote:")) return name.slice("remote:".length);
  return name;
}

export interface JobRequest {
  path: string;
  body: Record<string, unknown>;
}

/**
 * Target → the exact dispatch (pure, so tests can pin every field):
 *  - Team / builtin → POST /sessions (Team means agent_type "supervisor");
 *  - dynamic "custom:<slug>" → POST /agents/<slug>/spawn — POST /sessions
 *    would silently downgrade a custom agent to Builder;
 *  - remote "remote:<name>" → a SUPERVISOR session with the task prefixed to
 *    delegate to that remote via the delegate tool. A remote has no session
 *    shape of its own; the supervisor wrapper is the honest bridge (never
 *    silently reroute to builder — that's the exact bug class this repo bans).
 */
export function jobRequest(
  target: string,
  task: string,
  projectId: string,
): JobRequest {
  const base: Record<string, unknown> = { wait: false, origin: JOB_ORIGIN };
  if (projectId) base.project_id = projectId;
  if (target.startsWith("custom:")) {
    const slug = target.slice("custom:".length);
    return {
      path: `/agents/${encodeURIComponent(slug)}/spawn`,
      body: { task, ...base },
    };
  }
  if (target.startsWith("remote:")) {
    return {
      path: "/sessions",
      body: {
        task:
          `Delegate this job to the remote agent "${target}" via the ` +
          `delegate tool, verify its reply, and report honestly:\n\n${task}`,
        agent_type: "supervisor",
        ...base,
      },
    };
  }
  return {
    path: "/sessions",
    body: {
      task,
      agent_type: target === TEAM_TARGET ? "supervisor" : target,
      ...base,
    },
  };
}

/** The card's own dispatches out of GET /sessions: origin "job:*", newest
 *  first (sorted here — the list must not depend on server ordering). */
export function jobSessions(sessions: SessionView[]): SessionView[] {
  return sessions
    .filter((s) => Boolean(s.origin?.startsWith("job:")))
    .sort(
      (a, b) =>
        new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    );
}

/**
 * The job-post card at the top of the Agents page. `roster` is the page's
 * GET /agents/roster data (empty on older daemons — the Team default still
 * works); `assign` is RosterStrip's "Give work" preselect.
 */
export function JobPostCard({
  roster = [],
  assign = null,
}: {
  roster?: RosterEntry[];
  assign?: JobAssign | null;
} = {}) {
  const [task, setTask] = useState("");
  const [target, setTarget] = useState(TEAM_TARGET);
  const [projectId, setProjectId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [posted, setPosted] = useState<SessionView | null>(null);

  // Optional project grounding, same live list the sessions page uses.
  const { data: projData } = useApi<{ projects: Project[] }>("/projects");
  const projects = useMemo(
    () =>
      (projData?.projects ?? [])
        .filter((p) => p.status !== "archived")
        .sort((a, b) => a.name.localeCompare(b.name)),
    [projData],
  );

  // Recent jobs — the sessions this card (or any job surface) dispatched.
  const { data: sessionsData, reload: reloadJobs } = usePolledApi<{
    sessions: SessionView[];
  }>("/sessions", 8000);
  const jobs = useMemo(
    () => jobSessions(sessionsData?.sessions ?? []),
    [sessionsData],
  );

  // Only agents that can actually take a spawned session. Offline remotes
  // stay listed but disabled — hiding them would look like they don't exist.
  const candidates = roster.filter((e) => e.delegable);

  // RosterStrip's "Give work" → preselect that agent.
  useEffect(() => {
    if (assign) setTarget(wireTarget(assign.kind, assign.name));
  }, [assign]);

  // The select can only honestly SHOW a value that has a rendered <option>.
  // The roster can shift under the selection — this card renders the PAGE's
  // /agents/roster fetch while RosterStrip runs its own, so a Give-work click
  // can name an agent this card's list doesn't hold (page fetch failed or
  // lagged), and an agent can vanish between polls. A controlled <select>
  // with an unmatched value renders BLANK (selectedIndex -1) while a submit
  // would still post to the invisible target — the UI showing one thing and
  // dispatching another. Deriving the value once here, and using the SAME
  // derivation for the select and the dispatch, makes that split impossible:
  // an unmatched target falls back to the Team, visibly.
  const effectiveTarget =
    target === TEAM_TARGET || candidates.some((c) => c.name === target)
      ? target
      : TEAM_TARGET;

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = task.trim();
    if (!t) return;
    setBusy(true);
    setError(null);
    setPosted(null);
    try {
      const req = jobRequest(effectiveTarget, t, projectId);
      const session = await post<SessionView>(req.path, req.body);
      setPosted(session);
      setTask("");
      reloadJobs();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="Give work" icon={<Briefcase size={15} />}>
      <form onSubmit={submit} className="space-y-3">
        <div>
          <label
            htmlFor="job-task"
            className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400"
          >
            Job
          </label>
          <textarea
            id="job-task"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            rows={2}
            placeholder="Describe the job — the team plans it, delegates, and reports back"
            className="field resize-y"
          />
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <label
              htmlFor="job-target"
              className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400"
            >
              Who takes it
            </label>
            <select
              id="job-target"
              value={effectiveTarget}
              onChange={(e) => setTarget(e.target.value)}
              className="field"
            >
              <option value={TEAM_TARGET}>
                Team — supervisor plans & delegates
              </option>
              {candidates.map((e) => {
                const offline = e.kind === "remote" && !e.healthy;
                return (
                  <option key={e.name} value={e.name} disabled={offline}>
                    {bareTargetName(e.name)} — {SOURCE_LABEL[e.kind] ?? e.kind}
                    {offline ? " (offline)" : ""}
                  </option>
                );
              })}
            </select>
          </div>
          <div>
            <label
              htmlFor="job-project"
              className="mb-1.5 block text-[11px] uppercase tracking-[0.1em] text-zinc-400"
            >
              Project (optional)
            </label>
            <select
              id="job-project"
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
          </div>
        </div>

        <div className="flex items-center justify-end">
          <button
            type="submit"
            disabled={busy || !task.trim()}
            className="btn-accent"
          >
            {busy ? (
              <LoaderInline label="Posting…" />
            ) : (
              <>
                <Send size={14} /> Post job
              </>
            )}
          </button>
        </div>

        {posted?.id && (
          <SuccessNote>
            Job posted —{" "}
            <Link
              href={`/sessions/${posted.id}`}
              className="font-medium text-emerald-100 underline underline-offset-2"
            >
              watch it run
            </Link>
          </SuccessNote>
        )}
        {error && <ErrorNote>{error}</ErrorNote>}
      </form>

      {jobs.length > 0 && (
        <div className="mt-4 border-t hairline pt-3">
          <div className="mb-2 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
            Recent jobs · {jobs.length}
          </div>
          <div className="space-y-1">
            {jobs.slice(0, MAX_JOBS).map((s) => (
              <Link
                key={s.id}
                href={`/sessions/${s.id}`}
                className="group flex items-center gap-2 rounded-lg px-1.5 py-1 transition-colors hover:bg-white/[0.04]"
                title={s.task || "Untitled job"}
              >
                <StatusDot status={s.status} />
                <span className="min-w-0 flex-1 truncate text-[12.5px] text-zinc-200 transition-colors group-hover:text-accent-soft">
                  {s.task || "Untitled job"}
                </span>
                <Badge value={s.status} />
                <span className="shrink-0 text-[11px] text-zinc-500">
                  {timeAgo(s.created_at)}
                </span>
                <ArrowUpRight
                  size={12}
                  className="shrink-0 text-zinc-600 opacity-0 transition-opacity group-hover:opacity-100"
                />
              </Link>
            ))}
          </div>
          {jobs.length > MAX_JOBS && (
            <div className="mt-1.5 text-[11px] text-zinc-600">
              showing the latest {MAX_JOBS} of {jobs.length} jobs
            </div>
          )}
        </div>
      )}
    </Card>
  );
}
