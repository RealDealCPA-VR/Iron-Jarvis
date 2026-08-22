"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import {
  Bell,
  GitBranch,
  MonitorCog,
  Inbox,
  ArrowRight,
  MessageSquare,
  CalendarClock,
  ShieldAlert,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { ApiError, post } from "@/lib/api";
import { useEvents } from "@/lib/useEvents";
import { usePolledApi } from "@/lib/useApi";
import { useDesktopNotifications } from "@/lib/useDesktopNotifications";
import type { ComputerUseStatus, IJEvent, WorkflowRun } from "@/lib/types";
import { shortId, clockTime } from "@/lib/format";

/** Best-effort session id for a review event (top-level wins, then payload). */
function reviewKey(e: IJEvent): string {
  return String(e.session_id ?? (e.payload?.session_id as string | undefined) ?? e.id);
}

/** An informational, stream-driven notification (nothing waits on the user):
 *  an inbound comm message that spawned a session, a schedule that fired, or a
 *  computer-use run that finished. comm.rejected / webhook.received are
 *  deliberately NOT notified — they'd be pure noise; the event stream has them. */
interface ActivityItem {
  id: string;
  ts: string;
  href: string;
  icon: LucideIcon;
  title: string;
  body: string;
}

/** Map one live event to an activity notification (null = not a notified type). */
function toActivity(e: IJEvent): ActivityItem | null {
  const p = e.payload ?? {};
  if (e.type === "comm.received") {
    // Payload: {channel, sender, task} + session_id on the event (comm/inbound.py).
    const channel = typeof p.channel === "string" && p.channel ? p.channel : "a channel";
    const sender = typeof p.sender === "string" && p.sender ? ` (${p.sender})` : "";
    return {
      id: e.id,
      ts: e.ts,
      href: "/sessions",
      icon: MessageSquare,
      title: `Inbound message from ${channel}${sender} started a session`,
      body: typeof p.task === "string" ? p.task : "",
    };
  }
  if (e.type === "schedule.fired") {
    // Payload is the schedule's own payload dict — name/workflow when present.
    const name =
      (typeof p.name === "string" && p.name) ||
      (typeof p.workflow === "string" && p.workflow) ||
      (typeof p.type === "string" && p.type) ||
      "event";
    return {
      id: e.id,
      ts: e.ts,
      href: "/schedules",
      icon: CalendarClock,
      title: `Scheduled job ran: ${name}`,
      body: "",
    };
  }
  if (e.type === "computeruse.run_finished") {
    // Payload: {run_id, status, steps}; "completed" is the only good terminal
    // status (failed/blocked/awaiting_approval all mean the task didn't finish).
    const status = typeof p.status === "string" && p.status ? p.status : "finished";
    const ok = status === "completed";
    const steps =
      typeof p.steps === "number" ? `${p.steps} step${p.steps === 1 ? "" : "s"}` : "";
    const runId = typeof p.run_id === "string" ? p.run_id : "";
    return {
      id: e.id,
      ts: e.ts,
      href: "/computeruse",
      icon: MonitorCog,
      title: `Computer-use run finished — ${ok ? "ok" : "failed"}`,
      body: [status, steps, runId].filter(Boolean).join(" · "),
    };
  }
  return null;
}

/** A workflow run parked on an `ask` step (v1.121.0) — it WAITS on the user,
 *  so it counts toward the badge and is answerable right in the dropdown. */
interface WaitingAsk {
  runId: string;
  /** Stable identity of THIS ask: run id + parked step index, so a run that
   *  later parks on a DIFFERENT ask is a new item even after we suppressed
   *  the answered one locally. */
  key: string;
  workflow: string;
  question: string;
  /** The engine writes {index, step, question} today (workflows/engine.py) —
   *  options are rendered only if a future park carries them. */
  options: string[];
  startedAt: string;
}

/** Parse one /workflows/runs row into a WaitingAsk (null = not waiting). */
function parseWaitingRun(r: WorkflowRun): WaitingAsk | null {
  if (r.status !== "waiting" || !r.id) return null;
  let w: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(String(r.waiting_json ?? "") || "{}");
    if (parsed && typeof parsed === "object") w = parsed as Record<string, unknown>;
  } catch {
    /* corrupt blob — still show the row with the fallback question */
  }
  const question =
    typeof w.question === "string" && w.question ? w.question : "This run needs your answer.";
  const options = Array.isArray(w.options)
    ? w.options.filter((o): o is string => typeof o === "string" && o.length > 0)
    : [];
  return {
    runId: String(r.id),
    key: `${r.id}#${typeof w.index === "number" ? w.index : question}`,
    workflow: String(r.workflow_name || "workflow"),
    question,
    options,
    startedAt: String(r.started_at ?? ""),
  };
}

/** One parked run in the dropdown: the actual question + an inline answer box
 *  (same idiom as the chat WorkflowDraftCard's waiting banner). Success hands
 *  the ask back up so the row leaves the list; a 409 means someone answered it
 *  elsewhere first (the atomic waiting→resuming claim lost) — surfaced
 *  honestly, never retried. */
function WaitingRunRow({
  ask,
  onAnswered,
  onConflict,
}: {
  ask: WaitingAsk;
  onAnswered: (ask: WaitingAsk) => void;
  onConflict: (ask: WaitingAsk, message: string) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(answer: string) {
    const trimmed = answer.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    try {
      // Encode the id (same idiom as the Workflows page / canvas callers): the
      // id arrives from a polled response, and one containing "/" or "?" would
      // otherwise silently hit the wrong path.
      await post(`/workflows/runs/${encodeURIComponent(ask.runId)}/answer`, {
        answer: trimmed,
      });
    } catch (e) {
      const msg = e instanceof ApiError ? e.message : String(e);
      if (e instanceof ApiError && e.status === 409) {
        onConflict(ask, msg); // answered elsewhere — the row leaves, the note stays
      } else {
        setError(msg);
        setBusy(false); // daemon blip / validation — keep the row answerable
      }
      return;
    }
    onAnswered(ask); // unmounts this row — no state updates past this point
  }

  return (
    <li data-testid="bell-waiting-run" className="px-4 py-3">
      <div className="flex items-start gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-amber-500/25 bg-amber-500/[0.08] text-amber-300">
          <Workflow size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-zinc-100">
            Workflow &ldquo;{ask.workflow}&rdquo; needs an answer
          </span>
          <p className="mt-0.5 text-[12px] leading-snug text-amber-200">{ask.question}</p>
          {ask.options.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {ask.options.map((o) => (
                <button
                  key={o}
                  type="button"
                  onClick={() => void submit(o)}
                  disabled={busy}
                  className="rounded-full border border-amber-500/30 bg-amber-500/[0.08] px-2.5 py-1 text-[11px] text-amber-200 transition-colors hover:bg-amber-500/[0.16] disabled:opacity-50"
                >
                  {o}
                </button>
              ))}
            </div>
          )}
          <div className="mt-1.5 flex items-center gap-1.5">
            <input
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void submit(text);
              }}
              placeholder="Type your answer — the run continues"
              aria-label={`Answer workflow ${ask.workflow}`}
              className="field flex-1 py-1.5 text-[12px]"
            />
            <button
              type="button"
              onClick={() => void submit(text)}
              disabled={busy || !text.trim()}
              className="btn-accent px-3 py-1.5 text-[12px] disabled:opacity-50"
            >
              {busy ? "Sending…" : "Answer"}
            </button>
          </div>
          {error && <p className="mt-1 text-[11px] text-rose-300">{error}</p>}
          <span className="mt-1 block font-mono text-[10px] text-zinc-600">
            {shortId(ask.runId)} · {clockTime(ask.startedAt)}
          </span>
        </div>
      </div>
    </li>
  );
}

/** A mid-turn tool approval an AGENT RUN is paused on (v1.200.0). Job-origin
 *  runs (the Agents page) genuinely pause on ask-tier tools, but only the chat
 *  stream rendered a card — the job-poster never saw the ask and it timed out
 *  into a silent degrade. GET /chat/approvals/pending lists them (id + tool +
 *  session, NEVER args — the registry's own no-secrets posture) and the answer
 *  goes through the same POST /chat/approvals/{id} the chat card uses. */
interface PendingAgentApproval {
  id: string;
  tool: string;
  sessionId: string;
  requestedAt: string;
}

/** Parse one /chat/approvals/pending row (null = not a usable row). */
function parseAgentApproval(raw: unknown): PendingAgentApproval | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = typeof r.id === "string" ? r.id : "";
  if (!id) return null;
  return {
    id,
    tool: typeof r.tool === "string" && r.tool ? r.tool : "unknown",
    sessionId: typeof r.session_id === "string" ? r.session_id : "",
    requestedAt: typeof r.requested_at === "string" ? r.requested_at : "",
  };
}

/** One paused agent ask in the dropdown: tool name + session link + Approve
 *  once / Deny, POSTing the same route the chat card posts. A 404 means it was
 *  answered elsewhere or timed out (the pause window is bounded) — the row
 *  leaves without a retry; any other failure keeps the row answerable. */
function AgentApprovalRow({
  ask,
  onGone,
}: {
  ask: PendingAgentApproval;
  onGone: (id: string) => void;
}) {
  const [busy, setBusy] = useState<"once" | "deny" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function answer(decision: "once" | "deny") {
    if (busy) return;
    setBusy(decision);
    setError(null);
    try {
      // Encode the id (same idiom as the workflow-answer POST): it arrives
      // from a polled response and must not silently reroute the path.
      await post(`/chat/approvals/${encodeURIComponent(ask.id)}`, { decision });
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        onGone(ask.id); // answered elsewhere or expired — already resolved
        return;
      }
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(null); // daemon blip — keep the row answerable
      return;
    }
    onGone(ask.id); // unmounts this row — no state updates past this point
  }

  return (
    <li data-testid="bell-agent-approval" className="px-4 py-3">
      <div className="flex items-start gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-amber-500/25 bg-amber-500/[0.08] text-amber-300">
          <ShieldAlert size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-zinc-100">
            An agent is asking permission
          </span>
          <p className="mt-0.5 text-[12px] leading-snug text-amber-200">
            It wants to run <span className="font-mono">{ask.tool}</span> — the
            run is paused until you answer.
          </p>
          <div className="mt-1.5 flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => void answer("once")}
              disabled={busy !== null}
              className="btn-accent px-3 py-1.5 text-[12px] disabled:opacity-50"
            >
              {busy === "once" ? "Approving…" : "Approve once"}
            </button>
            <button
              type="button"
              onClick={() => void answer("deny")}
              disabled={busy !== null}
              className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-1.5 text-[12px] text-zinc-300 transition-colors hover:border-rose-500/30 hover:text-rose-200 disabled:opacity-50"
            >
              {busy === "deny" ? "Denying…" : "Deny"}
            </button>
          </div>
          {error && <p className="mt-1 text-[11px] text-rose-300">{error}</p>}
          <span className="mt-1 flex items-center gap-1.5 font-mono text-[10px] text-zinc-600">
            {ask.sessionId ? (
              <Link
                href={`/sessions/${encodeURIComponent(ask.sessionId)}`}
                className="text-accent-soft transition-colors hover:text-accent"
              >
                {shortId(ask.sessionId)}
              </Link>
            ) : (
              "—"
            )}
            {ask.requestedAt ? <>· {clockTime(ask.requestedAt)}</> : null}
          </span>
        </div>
      </div>
    </li>
  );
}

/**
 * Notification center: a bell + unread badge counting work that needs a human —
 * unresolved review requests (from the live event stream) plus any pending
 * computer-use approvals (polled). Clicking opens a dropdown of deep links.
 * Self-contained; renders a calm "all clear" state when nothing is pending.
 */
export function NotificationBell() {
  const { events } = useEvents(100);
  // Computer-use approvals don't ride the event stream, so poll their count.
  const cu = usePolledApi<ComputerUseStatus>("/computeruse", 15000);
  const pendingApprovals = cu.data?.pending_approvals ?? 0;
  // The live event buffer is empty right after a page reload, so seed the pending
  // review count from /diagnostics (the authoritative current count) — otherwise
  // a reload silently hides reviews that are still waiting on the user.
  const diag = usePolledApi<{ pending_reviews?: number }>("/diagnostics", 15000);
  const polledReviews = diag.data?.pending_reviews ?? 0;
  // Workflow runs parked on an `ask` step wait on the user too — same polled
  // cadence as the other bell sources (they don't ride a replayable stream).
  // Server-side `status=waiting` (v1.168.0) so an old parked question can
  // never fall out of a newest-first page and vanish from the very badge that
  // promises to count it; `slim=true` drops steps/outputs blobs — this poll
  // runs on EVERY page, and it only needs waiting_json (the question).
  const runsApi = usePolledApi<{ runs?: WorkflowRun[] }>(
    "/workflows/runs?status=waiting&slim=true&limit=200",
    15000,
  );
  // Agent runs paused on an ask-tier tool (v1.200.0) — same 15s cadence as
  // the other polled bell sources. The pause is bounded (it degrades on
  // timeout), so this is the difference between the user answering and a
  // silent deny they never saw.
  const agentAsksApi = usePolledApi<{ approvals?: unknown[] }>(
    "/chat/approvals/pending",
    15000,
  );

  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Asks answered (or conflicted) from THIS dropdown: suppressed locally so the
  // row leaves immediately instead of lingering until the next poll lands.
  const [resolvedAsks, setResolvedAsks] = useState<ReadonlySet<string>>(new Set());
  // 409s — someone answered the same ask elsewhere (chat card / Workflows
  // page). Shown as an informational note, cleared when the dropdown closes.
  const [conflicts, setConflicts] = useState<
    { key: string; workflow: string; message: string }[]
  >([]);

  const waiting = useMemo(() => {
    const out: WaitingAsk[] = [];
    for (const r of runsApi.data?.runs ?? []) {
      const ask = parseWaitingRun(r);
      if (ask && !resolvedAsks.has(ask.key)) out.push(ask);
    }
    return out;
  }, [runsApi.data, resolvedAsks]);

  // Agent asks answered from THIS dropdown: suppressed locally so the row
  // leaves immediately instead of lingering until the next poll lands.
  const [answeredAgentAsks, setAnsweredAgentAsks] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const agentAsks = useMemo(() => {
    const out: PendingAgentApproval[] = [];
    for (const raw of agentAsksApi.data?.approvals ?? []) {
      const ask = parseAgentApproval(raw);
      if (ask && !answeredAgentAsks.has(ask.id)) out.push(ask);
    }
    return out;
  }, [agentAsksApi.data, answeredAgentAsks]);

  // A live approval.requested/resolved event means the polled list is stale —
  // refetch right away (same idiom as the workflow.waiting refresh below).
  const reloadAgentAsks = agentAsksApi.reload;
  const prevApprovalEventId = useRef<string | null>(null);
  useEffect(() => {
    const latest = events.find(
      (e) => e.type === "approval.requested" || e.type === "approval.resolved",
    );
    if (!latest || prevApprovalEventId.current === latest.id) return;
    prevApprovalEventId.current = latest.id;
    reloadAgentAsks();
  }, [events, reloadAgentAsks]);

  const handleAgentAskGone = (id: string) => {
    setAnsweredAgentAsks((prev) => new Set(prev).add(id));
    reloadAgentAsks();
  };

  // A live `workflow.waiting` event means the polled list is already stale —
  // refetch right away so the question appears without the up-to-15s lag.
  const reloadRuns = runsApi.reload;
  const prevWaitingEventId = useRef<string | null>(null);
  useEffect(() => {
    const latest = events.find((e) => e.type === "workflow.waiting");
    if (!latest || prevWaitingEventId.current === latest.id) return;
    prevWaitingEventId.current = latest.id;
    reloadRuns();
  }, [events, reloadRuns]);

  const handleAnswered = (ask: WaitingAsk) => {
    setResolvedAsks((prev) => new Set(prev).add(ask.key));
    reloadRuns();
  };
  const handleConflict = (ask: WaitingAsk, message: string) => {
    setResolvedAsks((prev) => new Set(prev).add(ask.key));
    setConflicts((prev) => [...prev, { key: ask.key, workflow: ask.workflow, message }]);
    reloadRuns();
  };

  useEffect(() => {
    if (!open) setConflicts((c) => (c.length ? [] : c));
  }, [open]);

  // Unresolved review.requested events: dedupe by session and drop any whose
  // review later resolved/approved/rejected (defensive — those types may not
  // exist yet, in which case every requested review simply stays pending).
  const reviews = useMemo(() => {
    const resolved = new Set<string>();
    for (const e of events) {
      if (e.type.startsWith("review.") && e.type !== "review.requested") {
        resolved.add(reviewKey(e));
      }
    }
    const seen = new Set<string>();
    const out: IJEvent[] = [];
    for (const e of events) {
      if (e.type !== "review.requested") continue;
      const key = reviewKey(e);
      if (resolved.has(key) || seen.has(key)) continue;
      seen.add(key);
      out.push(e);
    }
    return out;
  }, [events]);

  // Informational activity (inbound comm / schedule fires / finished
  // computer-use runs): shown in the dropdown + pinged to the desktop, but NOT
  // counted as pending — nothing here waits on the user, so it must not
  // inflate the badge or the tab title. Dedupe by event id, keep the 6 newest
  // (the events buffer is already newest-first).
  const activity = useMemo(() => {
    const out: ActivityItem[] = [];
    const seen = new Set<string>();
    for (const e of events) {
      const item = toActivity(e);
      if (!item || seen.has(item.id)) continue;
      seen.add(item.id);
      out.push(item);
      if (out.length >= 6) break;
    }
    return out;
  }, [events]);

  // Use the larger of live-streamed vs polled reviews so neither a fresh reload
  // (no events yet) nor a just-arrived live event under-reports the badge/title.
  const reviewish = Math.max(reviews.length, polledReviews) + pendingApprovals;
  // Parked workflow questions and paused agent asks wait on the user exactly
  // like reviews/approvals.
  const count = reviewish + waiting.length + agentAsks.length;

  // Desktop notifications + browser tab title are owned here, app-wide.
  const { permission, requestPermission, notify } = useDesktopNotifications();
  const prevCount = useRef(count);
  const askedPermission = useRef(false);

  // Reflect pending work in the tab title (so a backgrounded user notices) and
  // ping a desktop notification on each UPWARD transition of the count.
  useEffect(() => {
    const prev = prevCount.current;
    prevCount.current = count;

    if (count === 0) {
      document.title = "Iron Jarvis";
    } else {
      document.title = `(${count}) Iron Jarvis`;
    }

    if (count > prev) {
      const parts: string[] = [];
      if (reviews.length)
        parts.push(`${reviews.length} review${reviews.length === 1 ? "" : "s"} awaiting approval`);
      if (pendingApprovals)
        parts.push(
          `${pendingApprovals} computer-use approval${pendingApprovals === 1 ? "" : "s"}`,
        );
      if (waiting.length)
        parts.push(
          `${waiting.length} workflow question${waiting.length === 1 ? "" : "s"} waiting`,
        );
      if (agentAsks.length)
        parts.push(
          `${agentAsks.length} agent${agentAsks.length === 1 ? "" : "s"} asking permission`,
        );
      const body = parts.join(" · ") || "Something needs your attention.";
      notify(`Iron Jarvis — ${count} pending`, body, () => setOpen(true));
    }
  }, [count, reviews.length, pendingApprovals, waiting.length, agentAsks.length, notify]);

  // Ping a desktop notification when a NEW activity event arrives. The event
  // buffer starts empty on load and /events only streams (never replays
  // history), so a page reload can't re-fire a backlog of old notifications.
  const prevActivityId = useRef<string | null>(null);
  useEffect(() => {
    const latest = activity[0];
    if (!latest || prevActivityId.current === latest.id) return;
    prevActivityId.current = latest.id;
    notify(latest.title, latest.body || "Open the dashboard for details.", () =>
      setOpen(true),
    );
  }, [activity, notify]);

  // Lazily request notification permission the first time the user opens the
  // bell — a real user gesture, so the browser actually shows the prompt.
  const toggleOpen = () => {
    setOpen((o) => !o);
    if (!askedPermission.current && permission === "default") {
      askedPermission.current = true;
      void requestPermission();
    }
  };

  // Close the dropdown on an outside click or Escape.
  useEffect(() => {
    if (!open) return;
    function onClick(ev: MouseEvent) {
      if (ref.current && !ref.current.contains(ev.target as Node)) setOpen(false);
    }
    function onKey(ev: KeyboardEvent) {
      if (ev.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={toggleOpen}
        aria-label={count ? `${count} notifications` : "Notifications"}
        className={`relative grid h-9 w-9 place-items-center rounded-xl border transition-colors ${
          open
            ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
            : "border-white/10 bg-white/[0.02] text-zinc-400 hover:border-white/20 hover:text-zinc-100"
        }`}
      >
        <Bell size={17} strokeWidth={2} />
        {count > 0 && (
          <span className="absolute -right-1 -top-1 grid h-4 min-w-[1rem] place-items-center rounded-full bg-accent px-1 text-[10px] font-bold text-ink-950 shadow-glow-sm">
            {count > 99 ? "99+" : count}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 z-50 mt-2 w-80 origin-top-right">
          <div className="card-surface overflow-hidden">
            <header className="flex items-center justify-between border-b hairline px-4 py-2.5">
              <span className="flex items-center gap-2 text-[13px] font-semibold text-zinc-200">
                <Bell size={14} className="text-accent-soft/80" />
                Notifications
              </span>
              {count > 0 && (
                <span className="rounded-full border border-accent/30 bg-accent/[0.1] px-2 py-0.5 text-[10px] font-medium text-accent-soft">
                  {count} pending
                </span>
              )}
            </header>

            <div className="max-h-[22rem] overflow-y-auto">
              {count === 0 && activity.length === 0 && conflicts.length === 0 ? (
                <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
                  <Inbox size={22} className="text-zinc-600" />
                  <div className="text-sm text-zinc-500">You&apos;re all caught up.</div>
                  <div className="max-w-[15rem] text-[11px] text-zinc-600">
                    Reviews, approvals, and workflow questions that need you will
                    show up here.
                  </div>
                </div>
              ) : (
                <ul className="divide-y divide-white/[0.04]">
                  {/* Paused agent asks first — the pause window is bounded, so
                      these degrade into a silent deny if left waiting. Section
                      absent entirely when none pend (honest empty behavior). */}
                  {agentAsks.map((ask) => (
                    <AgentApprovalRow
                      key={ask.id}
                      ask={ask}
                      onGone={handleAgentAskGone}
                    />
                  ))}

                  {pendingApprovals > 0 && (
                    <li>
                      <Link
                        href="/computeruse"
                        onClick={() => setOpen(false)}
                        className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.04]"
                      >
                        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-amber-500/25 bg-amber-500/[0.08] text-amber-300">
                          <MonitorCog size={15} />
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-sm font-medium text-zinc-100">
                            {pendingApprovals} computer-use approval
                            {pendingApprovals === 1 ? "" : "s"}
                          </span>
                          <span className="block text-[11px] text-zinc-500">
                            A sensitive action is waiting for your OK.
                          </span>
                        </span>
                        <ArrowRight size={13} className="shrink-0 text-zinc-600" />
                      </Link>
                    </li>
                  )}

                  {waiting.map((ask) => (
                    <WaitingRunRow
                      key={ask.key}
                      ask={ask}
                      onAnswered={handleAnswered}
                      onConflict={handleConflict}
                    />
                  ))}

                  {/* 409 notes: the ask was answered from another surface while
                      this dropdown held it — say so instead of vanishing. */}
                  {conflicts.map((c) => (
                    <li
                      key={`conflict-${c.key}`}
                      data-testid="bell-waiting-conflict"
                      className="px-4 py-2.5"
                    >
                      <p className="text-[11px] leading-snug text-amber-200/90">
                        Couldn&apos;t answer &ldquo;{c.workflow}&rdquo;: {c.message}
                      </p>
                    </li>
                  ))}

                  {reviews.map((e) => {
                    const summary =
                      (e.payload?.summary as string | undefined) ||
                      (e.payload?.risk ? `risk: ${String(e.payload.risk)}` : "") ||
                      "Changes are ready for your review.";
                    return (
                      <li key={e.id}>
                        <Link
                          href="/kanban"
                          onClick={() => setOpen(false)}
                          className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.04]"
                        >
                          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-accent/25 bg-accent/[0.08] text-accent-soft">
                            <GitBranch size={15} />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-sm font-medium text-zinc-100">
                              Review requested
                            </span>
                            <span className="block truncate text-[11px] text-zinc-500">
                              {summary}
                            </span>
                            <span className="mt-0.5 block font-mono text-[10px] text-zinc-600">
                              {e.session_id ? shortId(e.session_id) : "—"} · {clockTime(e.ts)}
                            </span>
                          </span>
                          <ArrowRight size={13} className="shrink-0 text-zinc-600" />
                        </Link>
                      </li>
                    );
                  })}

                  {/* Informational activity — same row pattern, neutral chrome,
                      never counted in the pending badge. */}
                  {activity.length > 0 && (
                    <li className="bg-white/[0.02] px-4 py-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-600">
                      Recent activity
                    </li>
                  )}
                  {activity.map((n) => {
                    const Icon = n.icon;
                    return (
                      <li key={n.id}>
                        <Link
                          href={n.href}
                          onClick={() => setOpen(false)}
                          className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.04]"
                        >
                          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-white/10 bg-white/[0.03] text-zinc-300">
                            <Icon size={15} />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block truncate text-sm font-medium text-zinc-100">
                              {n.title}
                            </span>
                            {n.body && (
                              <span className="block truncate text-[11px] text-zinc-500">
                                {n.body}
                              </span>
                            )}
                            <span className="mt-0.5 block font-mono text-[10px] text-zinc-600">
                              {clockTime(n.ts)}
                            </span>
                          </span>
                          <ArrowRight size={13} className="shrink-0 text-zinc-600" />
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              )}
            </div>

            {count > 0 && (
              <footer className="border-t hairline px-4 py-2">
                {/* Point at the surface that actually holds the pending work:
                    only-parked-workflows pending → the Workflows page; only
                    paused agent asks → the Agents page (they came from a job). */}
                <Link
                  href={
                    reviewish > 0
                      ? "/kanban"
                      : waiting.length > 0
                        ? "/workflows"
                        : "/agents"
                  }
                  onClick={() => setOpen(false)}
                  className="flex items-center justify-center gap-1.5 text-[11px] font-medium text-accent-soft transition-colors hover:text-accent"
                >
                  {reviewish > 0
                    ? "Open the review board"
                    : waiting.length > 0
                      ? "Open the Workflows page"
                      : "Open the Agents page"}{" "}
                  <ArrowRight size={12} />
                </Link>
              </footer>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
