"use client";

/**
 * An agent's INBOX (v1.296.0) — give an agent a job and the job waits for it.
 *
 * Until now "Give work" meant "start a session this second": a busy or paused
 * agent either refused or ran the job on top of what it was already doing.
 * The daemon now keeps a queue per assignee (`GET /agents/{name}/inbox`), and
 * this panel is where that queue is READ and FED: a health line (when it
 * last ran, how it went), what is running, what is waiting, what is blocked
 * and needs a decision, and what it did recently — with a composer on top
 * that posts a new assignment to `POST /assignments`.
 *
 * It polls (8 s, paused while the window is hidden — `usePolledApi`) and
 * refetches the moment an `assignment.*` event lands for this assignee, so a
 * job that starts, finishes or blocks shows up without waiting out the poll.
 *
 * An OLDER DAEMON has no inbox route: the GET 404s and the whole panel
 * renders nothing — the agents room looks exactly as it did in v1.295.0.
 *
 * `AssignmentRow` is exported: the project page's "Assignments" list
 * (`ProjectTasks`) draws the same row, so a queued job looks the same from
 * both ends.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { Inbox, Send } from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { useApi, usePolledApi } from "@/lib/useApi";
import { useEvents } from "@/lib/useEvents";
import type {
  AgentHealth,
  AgentInboxView,
  Assignment,
  AssignmentStatus,
  Project,
} from "@/lib/types";
import { Badge, ErrorNote, LoaderInline, SuccessNote } from "@/components/ui";
import { timeAgo } from "@/lib/format";

/** The composer's three priorities → the wire's -10..10 scale. */
export const PRIORITY_OPTIONS = [
  { label: "Low", value: -5 },
  { label: "Normal", value: 0 },
  { label: "High", value: 5 },
] as const;

/** Builtins the daemon REFUSES as an assignee: the supervisor (its base type)
 *  and any builtin whose definition carries `delegate` — today supervisor and
 *  planner. A coordinator is handed a queue of its own, never a place in one.
 *  Guide and the rest stay offered. */
export const COORDINATOR_BUILTINS: ReadonlySet<string> = new Set(["supervisor", "planner"]);

/** Can this roster name take a queued job? "custom:<slug>" always; a builtin
 *  unless it is a coordinator. (Remotes and the Team sentinel are judged by
 *  the callers — they are not roster assignees at all.) */
export function canBeAssignee(name: string): boolean {
  if (name.startsWith("custom:")) return true;
  return !COORDINATOR_BUILTINS.has(name);
}

/** The shown name: "custom:analyst" → "analyst". Builtins are bare already.
 *  Local on purpose — importing RosterStrip's `bareName` would drag the
 *  rail (and framer-motion) into the project page's chunk. */
export function bareAssignee(name: string): string {
  return name.startsWith("custom:") ? name.slice("custom:".length) : name;
}

/** The sentence every "Queue" surface says after a successful POST. */
export function queuedSentence(assignee: string): string {
  const bare = bareAssignee(assignee);
  return `Queued for ${bare} — it runs when ${bare} is free.`;
}

/** A row's one-line title: the daemon's `title`, else the task's first line. */
export function assignmentTitle(a: Assignment): string {
  if (a.title && a.title.trim()) return a.title.trim();
  const first = (a.task ?? "").split(/\r?\n/)[0]?.trim() ?? "";
  return first.length > 90 ? `${first.slice(0, 90)}…` : first || "Untitled job";
}

/** The priority badge word for a queued row, or null for Normal. */
export function priorityLabel(priority: number): "high" | "low" | null {
  if (typeof priority !== "number" || !Number.isFinite(priority)) return null;
  if (priority > 0) return "high";
  if (priority < 0) return "low";
  return null;
}

/** Which buttons a row offers, by status. Cancel while it can still be
 *  stopped; Unblock only for a blocked row; Retry for one that failed or
 *  was cancelled (the daemon mints a NEW row for a retry). */
export function rowActions(status: AssignmentStatus): {
  cancel: boolean;
  unblock: boolean;
  retry: boolean;
} {
  return {
    cancel: status === "queued" || status === "claimed" || status === "running" || status === "blocked",
    unblock: status === "blocked",
    retry: status === "failed" || status === "cancelled",
  };
}

export type HealthTone = "green" | "amber" | "red" | "slate";

/**
 * The health line, in words, plus the dot's tone:
 *   "never ran"                                  (slate)
 *   "Last ran 12 min ago — completed"            (green)
 *   "Last ran 12 min ago — needs you"            (amber)
 *   "Last ran 12 min ago — failed: <last_error>" (red)
 */
export function healthLine(h: AgentHealth | null | undefined): { text: string; tone: HealthTone } {
  if (!h || !h.last_run_at) return { text: "never ran", tone: "slate" };
  const when = `Last ran ${timeAgo(h.last_run_at)}`;
  const outcome = String(h.last_outcome ?? "").toLowerCase();
  if (outcome === "failed" || outcome === "error") {
    return { text: `${when} — failed: ${h.last_error || "no error recorded"}`, tone: "red" };
  }
  if (outcome === "needs_you" || outcome === "completed_with_failures") {
    return { text: `${when} — ${outcome.replace(/_/g, " ")}`, tone: "amber" };
  }
  return { text: `${when} — ${outcome ? outcome.replace(/_/g, " ") : "completed"}`, tone: "green" };
}

const HEALTH_DOT: Record<HealthTone, string> = {
  green: "bg-emerald-400",
  amber: "bg-amber-400",
  red: "bg-rose-400",
  slate: "bg-zinc-500",
};

function errText(err: unknown): string {
  return err instanceof ApiError ? err.message : String(err);
}

/* -------------------------------------------------------------------- row --- */

/**
 * One assignment, the same from the inbox and from a project: status chip,
 * title, priority (queued rows), the held / blocked reason, the session
 * link once there is one, and the actions its status allows. Every action
 * POSTs and then asks the owner to refetch — the row never guesses at the
 * daemon's new state.
 */
export function AssignmentRow({
  assignment: a,
  onChanged,
  showAssignee = false,
}: {
  assignment: Assignment;
  onChanged: () => void;
  /** On a project's list the assignee is the fact that varies per row. */
  showAssignee?: boolean;
}) {
  const [busy, setBusy] = useState<"cancel" | "unblock" | "retry" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const actions = rowActions(a.status);
  const prio = a.status === "queued" ? priorityLabel(a.priority) : null;

  async function act(kind: "cancel" | "unblock" | "retry") {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      await post(`/assignments/${encodeURIComponent(a.id)}/${kind}`);
      onChanged();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(null);
    }
  }

  const btn = "rounded-md border px-1.5 py-0.5 text-[10.5px] font-medium transition-colors disabled:opacity-50";

  return (
    <li
      data-testid={`inbox-${a.status}-${a.id}`}
      className="rounded-lg px-1.5 py-1 transition-colors hover:bg-white/[0.03]"
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Badge value={a.status === "done" ? "completed" : a.status} />
        <span
          className="min-w-0 flex-1 truncate text-[12.5px] text-zinc-200"
          title={a.task}
        >
          {showAssignee && (
            <span className="mr-1.5 text-zinc-500">{bareAssignee(a.assignee)} ·</span>
          )}
          {assignmentTitle(a)}
        </span>
        {prio && (
          <span
            data-testid={`inbox-priority-${a.id}`}
            className={`shrink-0 rounded-md border px-1 py-px text-[9.5px] font-medium ${
              prio === "high"
                ? "border-amber-400/25 bg-amber-400/10 text-amber-200"
                : "border-zinc-500/25 bg-zinc-500/10 text-zinc-400"
            }`}
          >
            {prio}
          </span>
        )}
        {a.coalesced_count > 0 && (
          <span
            className="shrink-0 text-[10px] text-zinc-600"
            title="The same job was asked for again while this one waited"
          >
            ×{a.coalesced_count + 1}
          </span>
        )}
        {a.session_id && (
          <Link
            href={`/sessions/${encodeURIComponent(a.session_id)}`}
            className="shrink-0 text-[11px] text-accent-soft transition-colors hover:text-accent"
          >
            session →
          </Link>
        )}
        <span className="shrink-0 text-[10.5px] tabular-nums text-zinc-600">
          {timeAgo(a.updated_at ?? a.created_at)}
        </span>
        {actions.unblock && (
          <button
            type="button"
            data-testid={`inbox-unblock-${a.id}`}
            onClick={() => void act("unblock")}
            disabled={busy !== null}
            className={`${btn} border-emerald-500/25 text-emerald-300 hover:bg-emerald-500/[0.1]`}
          >
            {busy === "unblock" ? "Unblocking…" : "Unblock"}
          </button>
        )}
        {actions.retry && (
          <button
            type="button"
            data-testid={`inbox-retry-${a.id}`}
            onClick={() => void act("retry")}
            disabled={busy !== null}
            className={`${btn} border-accent/30 text-accent-soft hover:bg-accent/[0.1]`}
          >
            {busy === "retry" ? "Retrying…" : "Retry"}
          </button>
        )}
        {actions.cancel && (
          <button
            type="button"
            data-testid={`inbox-cancel-${a.id}`}
            onClick={() => void act("cancel")}
            disabled={busy !== null}
            className={`${btn} border-rose-500/25 text-rose-300 hover:bg-rose-500/[0.1]`}
          >
            {busy === "cancel" ? "Cancelling…" : "Cancel"}
          </button>
        )}
      </div>
      {a.held_reason && a.status === "queued" && (
        <p data-testid={`inbox-held-${a.id}`} className="mt-0.5 text-[11px] text-amber-300/90">
          held: {a.held_reason}
        </p>
      )}
      {a.status === "blocked" && (
        <p data-testid={`inbox-blocked-reason-${a.id}`} className="mt-0.5 text-[11px] text-rose-200/90">
          {a.blocked_reason || "blocked — no reason recorded"}
        </p>
      )}
      {a.status === "failed" && a.last_error && (
        <p className="mt-0.5 truncate text-[11px] text-rose-200/80" title={a.last_error}>
          {a.last_error}
        </p>
      )}
      {error && <p className="mt-0.5 text-[11px] text-rose-300">{error}</p>}
    </li>
  );
}

/* ----------------------------------------------------------------- inbox --- */

function Section({
  label,
  rows,
  onChanged,
}: {
  label: string;
  rows: Assignment[];
  onChanged: () => void;
}) {
  if (rows.length === 0) return null;
  return (
    <div>
      <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-zinc-500">
        {label} · {rows.length}
      </div>
      <ul className="space-y-0.5">
        {rows.map((a) => (
          <AssignmentRow key={a.id} assignment={a} onChanged={onChanged} />
        ))}
      </ul>
    </div>
  );
}

/** "Assign work": task, optional reason, priority, optional project →
 *  POST /assignments for this agent. */
function AssignComposer({
  name,
  onQueued,
  defaultProjectId = "",
}: {
  name: string;
  onQueued: () => void;
  /** v1.304.0: opened from a PROJECT room's seat, the work belongs to that
   *  project unless the user picks another. */
  defaultProjectId?: string;
}) {
  const bare = bareAssignee(name);
  const [task, setTask] = useState("");
  const [reason, setReason] = useState("");
  const [priority, setPriority] = useState("0");
  const [projectId, setProjectId] = useState(defaultProjectId);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // The same live project list the job card offers (non-archived, by name).
  const { data: projData } = useApi<{ projects: Project[] }>("/projects");
  const projects = useMemo(
    () =>
      (projData?.projects ?? [])
        .filter((p) => p.status !== "archived")
        .sort((a, b) => a.name.localeCompare(b.name)),
    [projData],
  );

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const t = task.trim();
    if (!t || busy) return;
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const body: Record<string, unknown> = {
        assignee: name,
        task: t,
        priority: Number(priority),
      };
      if (projectId) body.project_id = projectId;
      if (reason.trim()) body.reason = reason.trim();
      await post<{ assignment: Assignment; created: boolean }>("/assignments", body);
      setTask("");
      setReason("");
      setNote(queuedSentence(name));
      onQueued();
    } catch (err) {
      setError(errText(err));
    } finally {
      setBusy(false);
    }
  }

  const id = `inbox-${bare}`;
  return (
    <form
      onSubmit={submit}
      data-testid={`inbox-assign-${name}`}
      className="space-y-2 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3"
    >
      <label
        htmlFor={`${id}-task`}
        className="block text-[11px] uppercase tracking-[0.1em] text-zinc-400"
      >
        Assign work
      </label>
      <textarea
        id={`${id}-task`}
        value={task}
        onChange={(e) => setTask(e.target.value)}
        rows={2}
        placeholder={`What should ${bare} do next? It waits its turn.`}
        className="field resize-y"
      />
      <div className="grid gap-2 sm:grid-cols-3">
        <input
          type="text"
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          aria-label="Reason (optional)"
          placeholder="Reason (optional)"
          className="field sm:col-span-1"
        />
        <select
          aria-label="Priority"
          value={priority}
          onChange={(e) => setPriority(e.target.value)}
          className="field"
        >
          {PRIORITY_OPTIONS.map((o) => (
            <option key={o.value} value={String(o.value)}>
              {o.label}
            </option>
          ))}
        </select>
        <select
          aria-label="Project (optional)"
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
      <div className="flex items-center justify-end">
        <button
          type="submit"
          disabled={busy || !task.trim()}
          className="btn-accent px-2.5 py-1.5 text-[11.5px]"
        >
          {busy ? (
            <LoaderInline label="Queueing…" />
          ) : (
            <>
              <Send size={12} /> Queue for {bare}
            </>
          )}
        </button>
      </div>
      {note && <SuccessNote>{note}</SuccessNote>}
      {error && <ErrorNote>{error}</ErrorNote>}
    </form>
  );
}

/**
 * `<AgentInbox name="builder" />` / `<AgentInbox name="custom:analyst" />` —
 * `name` is the ROSTER name, exactly as the daemon keys the queue.
 */
export function AgentInbox({ name, projectId }: { name: string; projectId?: string }) {
  const bare = bareAssignee(name);
  const path = `/agents/${encodeURIComponent(name)}/inbox`;
  const { data, error, reload } = usePolledApi<AgentInboxView>(path, 8000);

  // Refetch on the first unseen assignment.* event that concerns this
  // assignee (a requeue after a restart names no assignee, so it counts too).
  const { events } = useEvents(60);
  const seenRef = useRef<string | null>(null);
  useEffect(() => {
    const hit = events.find((e) => {
      if (typeof e.type !== "string" || !e.type.startsWith("assignment.")) return false;
      if (e.type === "assignment.requeued") return true;
      return e.payload?.assignee === name;
    });
    if (!hit || hit.id === seenRef.current) return;
    seenRef.current = hit.id;
    reload();
  }, [events, name, reload]);

  // An older daemon (404) — or no answer yet — draws nothing at all.
  if (error && error.status === 404) return null;
  if (!data) return null;

  const inbox = data.inbox ?? { queued: [], claimed: [], running: [], blocked: [], recent: [] };
  const running = [...(inbox.running ?? []), ...(inbox.claimed ?? [])];
  const queued = inbox.queued ?? [];
  const blocked = inbox.blocked ?? [];
  const recent = (inbox.recent ?? []).slice(0, 10);
  const health = healthLine(data.health);
  const empty = running.length + queued.length + blocked.length + recent.length === 0;

  return (
    <section data-testid={`agent-inbox-${name}`} className="space-y-3">
      <div className="flex items-center gap-2">
        <Inbox size={13} className="text-accent-soft/80" aria-hidden />
        <h3 className="text-[12px] font-semibold tracking-wide text-zinc-200">Inbox</h3>
        <p
          data-testid={`inbox-health-${name}`}
          data-tone={health.tone}
          className="ml-auto flex min-w-0 items-center gap-1.5 text-[11px] text-zinc-400"
          title={data.health?.last_error ?? undefined}
        >
          <span aria-hidden className={`h-1.5 w-1.5 shrink-0 rounded-full ${HEALTH_DOT[health.tone]}`} />
          <span className="truncate">{health.text}</span>
        </p>
      </div>

      <AssignComposer name={name} onQueued={reload} defaultProjectId={projectId ?? ""} />

      {empty ? (
        <p className="text-[11.5px] leading-relaxed text-zinc-500">
          Nothing queued — give {bare} a job above and it runs when {bare} is free.
        </p>
      ) : (
        <div className="space-y-3">
          <Section label="Running" rows={running} onChanged={reload} />
          <Section label="Queued" rows={queued} onChanged={reload} />
          <Section label="Blocked" rows={blocked} onChanged={reload} />
          <Section label="Recent" rows={recent} onChanged={reload} />
        </div>
      )}
    </section>
  );
}

export default AgentInbox;
