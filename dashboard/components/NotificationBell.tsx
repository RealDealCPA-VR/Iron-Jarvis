"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  AlertTriangle,
  Bell,
  CheckCircle2,
  SquareTerminal,
  GitBranch,
  GraduationCap,
  KeyRound,
  Layers,
  Lightbulb,
  MonitorCog,
  Inbox,
  ArrowRight,
  MessageSquare,
  CalendarClock,
  OctagonAlert,
  PauseCircle,
  RotateCcw,
  ShieldAlert,
  ShieldCheck,
  Wallet,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { ApiError, get, post, put } from "@/lib/api";
import { useEvents } from "@/lib/useEvents";
import { usePolledApi } from "@/lib/useApi";
import { useDesktopNotifications } from "@/lib/useDesktopNotifications";
import { quietWindow } from "@/lib/desktopShell";
import type { ComputerUseStatus, IJEvent, WorkflowRun } from "@/lib/types";
import { clockTime, formatTokens } from "@/lib/format";
import { InterruptedJobRow, useInterruptedJobs } from "@/components/InterruptedJobs";
import { agentsPath, missionPath } from "@/lib/mission";
import { MISSION_ORIGIN, sessionHref } from "@/lib/missionLinks";
import {
  announceBuildSessionLink,
  BUILD_STALLED_HEADLINE,
  buildFinishedBody,
  buildFinishedHeadline,
  buildStalledBody,
  buildLimitHeadline,
  buildLimitWaitingBody,
  buildMovedHeadline,
  buildSessionHref,
  buildThreadOf,
  sessionLinkFor,
} from "@/lib/buildLinks";
import { CODING_SESSION } from "@/lib/buildWords";
import { lookingAt, noteWatched, wasWatched } from "@/lib/buildWatching";
import { CHAT_ASKS_CHANGED_EVENT, chatHereVersion, shownHere, subscribeChatHere } from "@/lib/chatAway";
// v1.351.0 (G04): what happened while the window was closed (dependency-free).
import { auditEntriesToEvents, awayPath, awaySince, writeBellLastSeen } from "@/lib/bellAway";
// v1.350.0: the plan approval's name (dependency-free; the bell is on every page).
import { PLAN_TOOL } from "@/lib/toolWords";
// v1.358.0 (lane 8A): a run's model-written summary flattened to plain words —
// the SAME helper the Schedules row uses (dependency-free, never the chat's
// Markdown renderer), and a browser task's ending in words and tone.
import { summaryLine } from "@/lib/plainText";
import { browserRunWords, type BrowserRunTone } from "@/lib/browserRunWords";
// v1.357.0 (G76): a task another Jarvis sent, in the one set of words.
import { REMOTE_KIND, remoteTaskSubject } from "@/lib/remoteTask";
// v1.352.0 (G24/G25): the chat card's own answer words, dependency-free.
import {
  ALLOW_ONCE,
  ALWAYS_EXACT,
  ANSWERED_LINGER_MS,
  DENY,
  answeredLine,
  askPhrase,
  askSentence,
  runSubject,
  scopeLabel,
  undoLine,
} from "@/lib/approvalWords";
// v1.353.0 (G52): a Build pane waiting on its CLI's question (dependency-free).
import {
  blockedPanes,
  onBuildPage,
  paneWaitingHref,
  paneWaitingTitle,
  type BlockedPane,
} from "@/lib/paneAsks";
// v1.355.0 (G32): a failing background task in the user's words, with the
// page its fix lives on (dependency-free).
import { STATUS_HREF, loopWords } from "@/lib/loopWords";
import { EVERYTHING_TAB_EVENT } from "@/lib/surfaces";

/** v1.355.0 (G32): what a failed pack does NOT take down, said on its row. */
export const PACK_STILL_WORKS = "Chat and your other tools keep working.";

/** The pack row's body: its own fix (when the daemon gave one) and then what
 *  keeps working. A fix with no closing mark gets a full stop first. */
export function packFailedBody(fix: unknown): string {
  const f = typeof fix === "string" ? fix.trim() : "";
  if (!f) return PACK_STILL_WORKS;
  return `${/[.!?]$/.test(f) ? f : `${f}.`} ${PACK_STILL_WORKS}`;
}

/** One /diagnostics → background_loops entry (daemon/app.py `_tick`). */
/** v1.355.0: `since` = when the failing run STARTED (`at` = the latest
 *  failure); an older daemon sends `at` only. */
type LoopHealth = { ok?: boolean; last_error?: string; error?: string; at?: string; since?: string };

/** v1.229.0 (audit OBS2): a background loop still failing after this long
 *  becomes a bell item. Shorter and a single blip (a fleet node rebooting)
 *  would ping the desktop; a loop dead for five minutes is not a blip. */
const LOOP_FAILING_AFTER_MS = 5 * 60 * 1000;

interface FailingLoop {
  name: string;
  minutes: number;
  error: string;
}

/** The loops reporting ok:false whose failure is older than the threshold —
 *  one entry per loop (the map is keyed by name, so a loop can never appear
 *  twice). An entry with no parseable `at` is skipped: its age is unknown. */
function staleFailingLoops(
  loops: Record<string, LoopHealth> | undefined,
  now: number,
): FailingLoop[] {
  const out: FailingLoop[] = [];
  for (const [name, v] of Object.entries(loops ?? {})) {
    // v1.355.0 integrate: age from the START of the failing run (`since`);
    // `at` moves with every retry, so a loop retrying every few minutes
    // never looked 5 minutes old.
    const started = v?.since ?? v?.at;
    if (!v || v.ok !== false || !started) continue;
    const at = new Date(started).getTime();
    if (Number.isNaN(at)) continue;
    const age = now - at;
    if (age < LOOP_FAILING_AFTER_MS) continue;
    out.push({
      name,
      minutes: Math.floor(age / 60000),
      error: v.last_error ?? v.error ?? "failed",
    });
  }
  return out;
}

/** Best-effort session id for a review event (top-level wins, then payload). */
function reviewKey(e: IJEvent): string {
  return String(e.session_id ?? (e.payload?.session_id as string | undefined) ?? e.id);
}

/** An informational, stream-driven notification (nothing waits on the user):
 *  an inbound comm message that spawned a session, a schedule that fired, or a
 *  computer-use run that finished, or (v1.290.0) a high/critical safety
 *  finding from the post-session scan. comm.rejected / webhook.received are
 *  deliberately NOT notified — they'd be pure noise; the event stream has them. */
export interface ActivityItem {
  id: string;
  ts: string;
  href: string;
  icon: LucideIcon;
  title: string;
  body: string;
  /** v1.305.0: a row for the dropdown only — never pinged to the desktop. */
  quiet?: boolean;
  /** v1.374.0: the Build session a `build.turn_finished` row is about (its
   *  thread id). Its ping is skipped, and its badge count dropped, while the
   *  user is LOOKING at that session (lib/buildWatching) — they saw it end. */
  build?: string;
  /** v1.351.0 (G03): unattended work that FAILED or FELL SHORT. Counted on
   *  the bell's badge (and the tab title) until the dropdown is opened —
   *  nobody was watching when it happened, so the badge is how they learn.
   *  Never set together with `quiet`. */
  attention?: boolean;
  /** v1.360.0 (lane 10A): the run a workflow STEP's row is about (its
   *  session id). Set only on those rows, so the workflow's own failed row
   *  can stand in for them (`oneRowPerWorkflow`). */
  session?: string;
  /** v1.360.0 (lane 10A): on a FAILED workflow's row, the step sessions its
   *  run held (`workflow.completed` `sessions`): their rows fold into it. */
  covers?: readonly string[];
  /** v1.360.0 (lane 10A): the step rows this workflow row folds in (set by
   *  `oneRowPerWorkflow`): they leave the list, one row per failed workflow. */
  absorbs?: readonly string[];
  /** v1.361.0 (lane 11A): on a FAILED workflow's row, the run of the step
   *  that ENDED it (`workflow.completed` `failed_session`; "" = a step with
   *  no run of its own — a tool, notify or ask step, or an expectation).
   *  Absent on an event an older Iron Jarvis wrote. */
  endedBy?: string;
  /** v1.361.0 (lane 11A): the folded rows that say THIS outcome — only the
   *  row of the step that ended the run (set by `oneRowPerWorkflow`). Only
   *  they keep the workflow's row from being pinged or counted again: an
   *  earlier attempt that failed and was retried, or a step the run went on
   *  past, is another outcome, and the workflow's own failure is still said
   *  (v1.360.0 skipped it when ANY folded row had been pinged or seen). */
  standsInFor?: readonly string[];
}

/** v1.309.0 (contract 8): where an agent's row opens — THAT agent on Your
 *  team. Bare `/agents` has been the New-task composer since v1.308.0, so the
 *  old link landed the user on a blank objective box that says nothing about
 *  the agent that paused, ran low or was blocked. The payload's bare slug is
 *  the roster name; no name (or a placeholder) opens Your team itself. */
function agentHref(name: unknown): string {
  return typeof name === "string" && name.trim()
    ? agentsPath({ kind: "team", agent: name.trim() })
    : agentsPath({ kind: "team" });
}

/** Exactly the types `toActivity` maps (v1.351.0, G04). Also the ONE list
 *  the bell's replay read names in `GET /audit?type=…`, so a type added to
 *  toActivity is remembered across a closed window in the same change. Part
 *  of BELL_EVENT_TYPES below. */
export const BELL_ACTIVITY_TYPES: readonly string[] = [
  "session.completed",
  // v1.351.0 (G03): a workflow run that ended (failed rings, done is quiet)
  "workflow.completed",
  "preference.suggested",
  "detection.finding",
  "trust.lowered",
  "context.blocked",
  "comm.received",
  "schedule.fired",
  "agent.paused",
  "agent.allowance_warning",
  "assignment.blocked",
  "assignment.finished",
  "assignment.requeued",
  "grant.revoked",
  "coach.proposal",
  "computeruse.run_finished",
  // v1.342.0: a pack that failed to load or whose calls fail (once per episode)
  "mcp.pack_failed",
  // v1.346.0: a media generation that finished in the background, or failed
  "media.completed",
  "media.failed",
  // v1.369.0 (IJ harness Phase 6, on Phase 5's events): a Build session's
  // account could not serve (it stopped), and a "Resume when ready" wait
  // that carried on by itself.
  "build.account_limit",
  "build.resumed",
  // v1.370.0 (IJ harness Phase 7): the experience library suggested a lesson
  // (or an edit to the folder's harness files) for a Build session's folder.
  "library.proposal",
  // v1.374.0 (IJ harness part 2, Phase 3): a Build turn that ran longer than
  // a minute ended — said unless the user watched it end.
  "build.turn_finished",
  // v1.392.0 (long jobs, Phase 3): a Build reply made no progress for ten
  // minutes — once per episode, said unless the user is looking at it.
  "build.turn_stalled",
];

/** Every /events type the bell reads (v1.311.0, wave 3,
 *  events-unfiltered-page-subscribers): what `toActivity` maps, the review
 *  badge, the approval and workflow refetch triggers. The bell is in the
 *  layout on every page, and every other frame (add-on tab switches,
 *  provider.routed per LLM call) cost it a render for nothing. ADD A TYPE
 *  HERE in the same change that makes the bell read it — a missing one is a
 *  silent "the bell never showed it" (pinned against this file's own
 *  comparisons in hooks-wave3-typesets-v1311). */
export const BELL_EVENT_TYPES: readonly string[] = [
  // the review badge (review.requested, and its resolutions)
  "review.*",
  // the agent-asks refetch + the workflow-question refetch
  "approval.requested",
  "approval.resolved",
  "workflow.waiting",
  // toActivity
  ...BELL_ACTIVITY_TYPES,
  // the busy-runs ask (v1.333.0)
  "runs.busy",
];

/** v1.346.0: the media kinds `generate_media` makes; anything else reads as
 *  plain "media" so a newer daemon's kind never puts a wrong word in a row. */
const MEDIA_KINDS: ReadonlySet<string> = new Set(["image", "video", "audio", "music"]);

/** Where a media row opens: the chat it was asked in; a FINISHED result
 *  with no chat (v1.347.0: nobody could say which chat yet) opens the
 *  Creative library, where the file is; anything else the chat page. */
function mediaHref(threadId: unknown, path?: unknown): string {
  if (typeof threadId === "string" && threadId.trim()) {
    return `/chat?thread=${encodeURIComponent(threadId.trim())}`;
  }
  return typeof path === "string" && path.trim() ? "/creative" : "/chat";
}

/** How a finished run ended, read off a `session.completed` payload (the
 *  orchestrator's `_completion_payload`: status + ok + outcome). Shared by the
 *  mission rows and the unattended rows (v1.351.0) so the two can never
 *  disagree about what "done" means. `status` says the RUN ended cleanly;
 *  `outcome` (Session.outcome, the ledger's verdict on the JOB) says whether
 *  the work was done — "done" needs POSITIVE proof: outcome "completed". */
function runVerdict(p: Record<string, unknown>): {
  cancelled: boolean;
  ran: boolean;
  done: boolean;
  outcome: string;
} {
  const status = typeof p.status === "string" ? p.status : "";
  const ran = status === "completed" && p.ok !== false;
  const outcome = typeof p.outcome === "string" ? p.outcome : "";
  return { cancelled: status === "cancelled", ran, done: ran && outcome === "completed", outcome };
}

/** v1.351.0 (G03): the doors that start work with NOBODY watching — a
 *  schedule, a goal, a workflow step, a reflex rule, a message from the
 *  phone, autonomy, a task another Jarvis sent (v1.357.0) — and how a row
 *  names each run (`Session.origin` is
 *  `<door>:<name>`; autonomy stamps a bare "autonomy"). A goal's suffix is
 *  its internal id, never shown (the OriginChip rule). A workflow's STEP
 *  says so: whether the WORKFLOW finished is its own row
 *  (`workflow.completed`). */
const UNATTENDED_SUBJECT: Record<string, (name: string) => string> = {
  schedule: (n) => (n ? `Your schedule ${n}` : "A schedule"),
  goal: () => "Your goal's run",
  workflow: (n) => (n ? `A step of your workflow ${n}` : "A workflow step"),
  reflex: (n) => (n ? `Your reflex ${n}` : "A reflex rule"),
  comm: (n) => (n ? `Your request from ${n}` : "A request from your phone"),
  autonomy: () => "An autonomy run",
  // v1.357.0 (G76, with G61): a task another computer running Iron Jarvis
  // sent (`remote:<caller>`). Nobody at this PC watches it, so a failure
  // rings like the other doors; keyed on the prefix (lib/remoteTask).
  [REMOTE_KIND]: (n) => remoteTaskSubject(n),
};

/** The unattended door a run came through, or null (chat, a mission, a job
 *  the user started, an untagged run — those keep their own rules). */
export function unattendedDoor(origin: unknown): { kind: string; name: string } | null {
  if (typeof origin !== "string") return null;
  const value = origin.trim();
  const i = value.indexOf(":");
  const kind = (i >= 0 ? value.slice(0, i) : value).trim();
  if (!Object.prototype.hasOwnProperty.call(UNATTENDED_SUBJECT, kind)) return null;
  return { kind, name: i >= 0 ? value.slice(i + 1).trim() : "" };
}

/** A run's summary as a one-line row: flattened to plain words first
 *  (v1.358.0, lane 8A — lib/plainText.summaryLine, the Schedules row's own
 *  helper), so "# Final Report ## What happened …" never reaches a row,
 *  then cut. */
function clip(text: unknown): string {
  const s = summaryLine(text).trim();
  return s.length > 140 ? `${s.slice(0, 139)}…` : s;
}

/** v1.351.0 (G03): an unattended run's `session.completed` as a bell row.
 *  It FAILED or FELL SHORT → a row that RINGS: the mission rows' words
 *  ("could not finish" / "finished, but part of it failed" / "needs you"),
 *  the warning icon, counted on the badge until the bell is opened, a
 *  desktop ping while a window is open. It finished cleanly → a QUIET row
 *  (no badge, no ping). A workflow STEP that finished cleanly says nothing:
 *  its workflow's own row does. A run the user STOPPED says nothing either.
 *  The row opens the run (`missionLinks.sessionHref`). */
function unattendedCompletion(
  e: IJEvent,
  p: Record<string, unknown>,
  door: { kind: string; name: string },
): ActivityItem | null {
  const sid = typeof e.session_id === "string" ? e.session_id : "";
  if (!sid) return null;
  const v = runVerdict(p);
  if (v.cancelled) return null;
  if (v.done && door.kind === "workflow") return null;
  // v1.358.0 (lane 8A): a run that ENDED FAILED reads "could not finish"
  // even when an ask expired on the way — the schedule's row reads "Failed"
  // for that same run (scheduling/run_outcome: a failed run is `error`), and
  // the run's own badge says Failed. "needs you" is a run that finished
  // while the user is the next step. One verdict, pinned by
  // __tests__/fixtures/wave1-seams-v13510.json.
  const words = !v.ran
    ? "could not finish"
    : v.outcome === "needs_you"
      ? "needs you"
      : v.done
        ? "finished"
        : v.outcome === "completed_with_failures"
          ? "finished, but part of it failed"
          : "finished. Check what it did";
  return {
    id: e.id,
    ts: e.ts,
    href: sessionHref({
      id: sid,
      origin: typeof p.origin === "string" ? p.origin : null,
      project_id: typeof p.project_id === "string" ? p.project_id : null,
    }),
    icon: v.done ? CheckCircle2 : AlertTriangle,
    title: `${UNATTENDED_SUBJECT[door.kind](door.name)} ${words}`,
    body: clip(p.summary),
    ...(v.done ? { quiet: true } : { attention: true }),
    // v1.360.0 (lane 10A): a workflow step's row names its run, so the
    // workflow's own failed row can say it once (`oneRowPerWorkflow`).
    ...(door.kind === "workflow" ? { session: sid } : {}),
  };
}

/** v1.361.0 (lane 11A): does this bell leave the desktop pings to another
 *  window? A popped-out module's bell is quiet while the main window is open
 *  (the main window speaks for the app); with the main window closed to the
 *  tray the desktop app names ONE pop-out that speaks, so its notices are
 *  not lost (v1.360.1 silenced every pop-out, and with the main window in
 *  the tray most notices reached no desktop toast at all). Never throws (a
 *  test's partial `lib/desktopShell` mock reads as the main window). */
function quietHere(): boolean {
  try {
    return quietWindow();
  } catch {
    return false;
  }
}

/**
 * v1.360.0 (lane 10A, the re-audit's G03 regression): ONE row per failed
 * workflow. A step that failed rang "A step of your workflow X could not
 * finish" and, a moment later, its run rang "Your workflow X could not
 * finish" — two rows (and two desktop pings) for one outcome. The FAILED
 * workflow's row (`covers` = its run's step sessions) stands in for the
 * step rows of that run: they leave the list and are recorded on it
 * (`absorbs`). v1.361.0 (lane 11A): only the folded row of the step that
 * ENDED the run (`endedBy`) says the same outcome (`standsInFor`), so only
 * a step row already pinged or seen for THAT step keeps the workflow row
 * from being pinged or counted again — an earlier attempt that failed and
 * was retried is another outcome (an event an older Iron Jarvis wrote names
 * no step: every folded row stands in, as in v1.360.0). A step row whose
 * workflow finished (a step its author let fail, a retry) stays: nothing
 * else says it. Rows from `alsoCovered` (the live list, for the replayed
 * one) fold the same way. Order is kept; nothing else changes.
 */
export function oneRowPerWorkflow(
  rows: readonly ActivityItem[],
  alsoCovered: readonly ActivityItem[] = [],
): ActivityItem[] {
  const coverOf = new Map<string, number>();
  rows.forEach((r, i) => {
    for (const s of r.covers ?? []) if (!coverOf.has(s)) coverOf.set(s, i);
  });
  const coveredElsewhere = new Set<string>();
  for (const r of alsoCovered) for (const s of r.covers ?? []) coveredElsewhere.add(s);
  if (!coverOf.size && !coveredElsewhere.size) return [...rows];
  const absorbed = new Map<number, string[]>();
  const standsIn = new Map<number, string[]>();
  const out: (ActivityItem | null)[] = rows.map((r) => r);
  rows.forEach((r, i) => {
    if (!r.session) return;
    const at = coverOf.get(r.session);
    if (at !== undefined && at !== i) {
      out[i] = null;
      absorbed.set(at, [...(absorbed.get(at) ?? []), r.id]);
      const ended = rows[at].endedBy;
      if (ended === undefined || ended === r.session) {
        standsIn.set(at, [...(standsIn.get(at) ?? []), r.id]);
      }
    } else if (coveredElsewhere.has(r.session)) {
      out[i] = null;
    }
  });
  return out
    .map((r, i) =>
      r && absorbed.has(i)
        ? { ...r, absorbs: absorbed.get(i), standsInFor: standsIn.get(i) ?? [] }
        : r,
    )
    .filter((r): r is ActivityItem => r !== null);
}

/** v1.358.0 (lane 8A): a browser task's tone, drawn by the row's icon — an
 *  ask wears the bell's ask mark, never the warning a failure wears. */
const BROWSER_RUN_ICON: Record<BrowserRunTone, LucideIcon> = {
  done: MonitorCog,
  ask: ShieldAlert,
  warn: AlertTriangle,
  danger: AlertTriangle,
};

/** Map one live event to an activity notification (null = not a notified type). */
export function toActivity(e: IJEvent): ActivityItem | null {
  const p = e.payload ?? {};
  if (e.type === "session.completed") {
    // v1.351.0 (G03): a run nobody was watching (a schedule, a goal, a
    // workflow step, a reflex, the phone, autonomy) tells the user how it
    // ended — before this only missions rang, so a 7 am schedule that failed
    // said nothing at all.
    const door = unattendedDoor(p.origin);
    if (door) return unattendedCompletion(e, p, door);
    // v1.309.0: a finished MISSION tells the user (contract 2: the payload
    // carries `origin` + `project_id`). Before this the bell had no
    // session.completed branch at all, so an objective the user walked away
    // from finished in silence. Only the ROOT origin rings: a teammate
    // ("job:mission-member") finishing is the team's business — a
    // five-member mission must not buzz six times — and chat / untagged runs
    // stay quiet as before. A mission the USER stopped rings nothing either:
    // they pressed Stop, a "could not finish" ping would be noise.
    if (p.origin !== MISSION_ORIGIN) return null;
    const sid = typeof e.session_id === "string" ? e.session_id : "";
    if (!sid) return null;
    const { cancelled, ran, done, outcome } = runVerdict(p);
    if (cancelled) return null;
    // `status` says the RUN ended cleanly; `outcome` (Session.outcome, the
    // ledger's verdict on the JOB) says whether the work was done. A run whose
    // ask timed out ends COMPLETED with outcome "needs_you" — the exact false
    // "Task complete" headline Session.outcome exists to stop. So "is done"
    // needs POSITIVE proof: outcome "completed" and nothing less. A payload
    // with no outcome (fix round, v1.309.0 review: the orchestrator's
    // completion payload once carried none, and the dashboard and daemon
    // ship in one installer, so "an older daemon" is no excuse for the
    // optimistic default) reads a neutral "finished" with the warning icon —
    // the mission screen then says what actually happened. The words match
    // the mission screen's own headline (lib/mission.missionHeadline).
    // (v1.351.0: the reading lives in `runVerdict`, shared with the
    // unattended rows.)
    const title = !ran
      ? "Your objective could not be finished"
      : done
        ? "Your objective is done"
        : outcome === "needs_you"
          ? "Your objective finished, but something needs you"
          : outcome === "completed_with_failures"
            ? "Your objective finished, but part of it failed"
            : outcome === ""
              ? "Your objective finished"
              : "Your objective finished. Check what it did";
    const project = typeof p.project_id === "string" ? p.project_id : "";
    return {
      id: e.id,
      ts: e.ts,
      href: missionPath(sid, project),
      icon: done ? CheckCircle2 : AlertTriangle,
      // The exact words the Handbook promises. Nothing short of done borrows them.
      title,
      // v1.358.0 (lane 8A): the coordinator's report in plain words, never
      // its raw markdown.
      body: clip(p.summary),
    };
  }
  if (e.type === "workflow.completed") {
    // v1.351.0 (G03): a workflow run ended (workflows/engine.py publishes
    // {workflow, status, run_id, sessions} at its end). A FAILED run rings —
    // a schedule may have started it at 3 am — and opens the Workflows page,
    // where the run says which step stopped; a finished run is a quiet row
    // (its clean steps say nothing on their own); a cancelled one says
    // nothing (the user pressed Stop).
    const status = typeof p.status === "string" ? p.status : "";
    const name = typeof p.workflow === "string" ? p.workflow.trim() : "";
    const subject = name ? `Your workflow ${name}` : "A workflow";
    if (status === "failed") {
      // v1.360.0 (lane 10A): the run's step sessions, so this row stands in
      // for its failed step's row (`oneRowPerWorkflow`) — one row, one ping.
      const covers = Array.isArray(p.sessions)
        ? p.sessions.filter((s): s is string => typeof s === "string" && s !== "")
        : [];
      return {
        id: e.id,
        ts: e.ts,
        href: "/workflows",
        icon: AlertTriangle,
        title: `${subject} could not finish`,
        body: "The Workflows page shows which step stopped.",
        attention: true,
        ...(covers.length ? { covers } : {}),
        // v1.361.0 (lane 11A): which step's run ended it, so only THAT
        // step's row stands in for this one.
        ...(typeof p.failed_session === "string" ? { endedBy: p.failed_session } : {}),
      };
    }
    if (status === "completed") {
      return {
        id: e.id,
        ts: e.ts,
        href: "/workflows",
        icon: CheckCircle2,
        title: `${subject} finished`,
        body: "",
        quiet: true,
      };
    }
    return null;
  }
  if (e.type === "preference.suggested") {
    // v1.305.0: a preference the user's OWN press minted ("Look through my
    // Claude Code and Codex sessions") waits on the Memory page. Payload:
    // {id, text, count, via}. ONLY via "scan": the in-chat suggestion is
    // already the line under the reply the user is reading — a bell row for
    // it would say the same thing twice. Quiet: a dropdown row, no desktop
    // ping — the user is on the page that asked.
    if (p.via !== "scan") return null;
    const text = typeof p.text === "string" ? p.text.trim() : "";
    return {
      id: e.id,
      ts: e.ts,
      href: "/memory#prefs-suggested",
      icon: Lightbulb,
      title: "A suggested preference is waiting on the Memory page",
      body: text.length > 140 ? `${text.slice(0, 139)}…` : text,
      quiet: true,
    };
  }
  if (e.type === "detection.finding") {
    // v1.290.0: a post-session safety scan found something high/critical.
    // Payload: Finding.to_dict(text=False) — {rule_id, title, severity,
    // reason, session_id, source, count, events} (routes/detections.py).
    const title = typeof p.title === "string" && p.title ? p.title : "a finding";
    return {
      id: e.id,
      ts: e.ts,
      href: "/activity#safety",
      icon: ShieldAlert,
      title: `Safety check: ${title}`,
      body: typeof p.reason === "string" ? p.reason : "",
    };
  }
  if (e.type === "trust.lowered") {
    // v1.298.0: a run dropped to low trust mid-way — it read content flagged
    // as injection. Payload: {session_id, tool, category, reason}. Links to
    // the run, where the banner says what it can no longer change.
    const sid =
      typeof p.session_id === "string" && p.session_id
        ? p.session_id
        : typeof e.session_id === "string"
          ? e.session_id
          : "";
    return {
      id: e.id,
      ts: e.ts,
      // A chat turn has no session row: its taint says session_id "chat".
      // v1.369.0: a Build session's ("build:<id>") opens in Build.
      href: sid === "chat" ? "/chat" : sid ? sessionLinkFor(sid) : "/activity",
      icon: ShieldAlert,
      title: sid === "chat" ? "A chat turn dropped to low trust" : "A run dropped to low trust",
      body: typeof p.reason === "string" ? p.reason : "",
    };
  }
  if (e.type === "context.blocked") {
    // v1.298.0: the daemon kept suspicious passages OUT of a run's context.
    // Payload: {session_id, source, count, categories}. The system did its
    // job (ShieldCheck, not Alert); the body names what it caught.
    const sid =
      typeof p.session_id === "string" && p.session_id
        ? p.session_id
        : typeof e.session_id === "string"
          ? e.session_id
          : "";
    const n = typeof p.count === "number" && Number.isFinite(p.count) ? p.count : 0;
    const source = typeof p.source === "string" && p.source ? p.source : "the context";
    const cats = Array.isArray(p.categories)
      ? (p.categories as unknown[]).filter((c): c is string => typeof c === "string" && !!c)
      : [];
    return {
      id: e.id,
      ts: e.ts,
      href: sid === "chat" ? "/chat" : sid ? sessionLinkFor(sid) : "/activity",
      icon: ShieldCheck,
      title: `Blocked ${n} suspicious passage${n === 1 ? "" : "s"} from ${source}`,
      body: cats.join(", "),
    };
  }
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
      title: `Scheduled task ran: ${name}`,
      body: "",
    };
  }
  if (e.type === "agent.paused") {
    // v1.295.0: a custom agent was put on a day off — by hand, or by the
    // daemon when its monthly allowance ran out. Payload: {name, reason,
    // spent_tokens, spent_usd, allowance_tokens, allowance_usd, month}.
    const name = typeof p.name === "string" && p.name ? p.name : "an agent";
    return {
      id: e.id,
      ts: e.ts,
      href: agentHref(p.name),
      icon: PauseCircle,
      title: `Agent paused: ${name}`,
      body: typeof p.reason === "string" ? p.reason : "",
    };
  }
  if (e.type === "agent.allowance_warning") {
    // v1.295.0: a custom agent crossed the warning line of its monthly
    // allowance. Payload: {name, pct, spent_tokens, spent_usd,
    // allowance_tokens, allowance_usd, month}. The body names the bound
    // that is actually set — a zero bound is "no bound", never "0 tokens".
    const name = typeof p.name === "string" && p.name ? p.name : "An agent";
    const pct = typeof p.pct === "number" && Number.isFinite(p.pct) ? Math.round(p.pct) : 0;
    const tokBound = typeof p.allowance_tokens === "number" ? p.allowance_tokens : 0;
    const usdBound = typeof p.allowance_usd === "number" ? p.allowance_usd : 0;
    const spentTok = typeof p.spent_tokens === "number" ? p.spent_tokens : 0;
    const spentUsd = typeof p.spent_usd === "number" ? p.spent_usd : 0;
    const body =
      tokBound > 0
        ? `${formatTokens(spentTok)} of ${formatTokens(tokBound)} tokens`
        : usdBound > 0
          ? `$${spentUsd.toFixed(2)} of $${usdBound}`
          : "";
    return {
      id: e.id,
      ts: e.ts,
      href: agentHref(p.name),
      icon: Wallet,
      title: `${name} has used ${pct}% of its monthly allowance`,
      body,
    };
  }
  if (e.type === "assignment.blocked") {
    // v1.296.0: a queued job hit something only the user can settle.
    // Payload: {id, assignee, title, blocked_reason}.
    const title = typeof p.title === "string" && p.title ? p.title : "a task";
    const assignee = typeof p.assignee === "string" && p.assignee ? p.assignee : "an agent";
    const why = typeof p.blocked_reason === "string" ? p.blocked_reason : "";
    return {
      id: e.id,
      ts: e.ts,
      // The agent's inbox lives in its detail on Your team.
      href: agentHref(p.assignee),
      icon: OctagonAlert,
      title: `Blocked: ${title}`,
      body: why ? `${assignee}: ${why}` : assignee,
    };
  }
  if (e.type === "assignment.finished") {
    // v1.296.0: a queued job ended. Success is QUIET (the inbox and the
    // session list already say so); only a failure rings the bell.
    // Payload: {id, assignee, title, ok, session_id, error, status}.
    // v1.351.0 (G03): a job that ended "completed" but whose run's outcome
    // fell short (`outcome` needs_you / completed_with_failures) rings too;
    // a payload without `outcome` keeps the old behaviour exactly. Every
    // ringing row counts on the badge — unless the user cancelled the job
    // (status "cancelled": the old "Failed:" row stays, with no badge).
    const outcome = typeof p.outcome === "string" ? p.outcome : "";
    const shortfall =
      p.ok !== false && (outcome === "needs_you" || outcome === "completed_with_failures");
    if (p.ok !== false && !shortfall) return null;
    const title = typeof p.title === "string" && p.title ? p.title : "a task";
    const assignee = typeof p.assignee === "string" && p.assignee ? p.assignee : "an agent";
    const sid =
      typeof p.session_id === "string" && p.session_id
        ? p.session_id
        : typeof e.session_id === "string"
          ? e.session_id
          : "";
    const cancelledByUser = p.status === "cancelled";
    return {
      id: e.id,
      ts: e.ts,
      href: sid ? `/sessions/${encodeURIComponent(sid)}` : agentHref(p.assignee),
      icon: AlertTriangle,
      title: shortfall
        ? outcome === "needs_you"
          ? `Needs you: ${title}`
          : `Part of it failed: ${title}`
        : `Failed: ${title}`,
      body: shortfall
        ? assignee
        : `${assignee}: ${typeof p.error === "string" ? p.error : ""}`,
      ...(cancelledByUser ? {} : { attention: true }),
    };
  }
  if (e.type === "grant.revoked") {
    // v1.299.0: a standing grant ("always allow exactly this") was revoked.
    // Payload: {id, scope_kind, scope_id, tool, label, expires_at}. Creation
    // is NOT notified — the user just pressed the button that made it.
    const label =
      typeof p.label === "string" && p.label
        ? p.label
        : typeof p.tool === "string" && p.tool
          ? p.tool
          : "a grant";
    return {
      id: e.id,
      ts: e.ts,
      href: "/autonomy",
      icon: KeyRound,
      title: `Standing grant revoked: ${label}`,
      body: typeof p.tool === "string" && p.tool && p.tool !== label ? p.tool : "",
    };
  }
  if (e.type === "coach.proposal") {
    // v1.297.0: the reflection coach wrote a proposal for an agent's
    // instructions. Payload: {id, agent, categories, rationale}. The body is
    // the rationale, cut at 140 characters — the card in the agents room
    // has the whole of it, with the diff.
    const agent = typeof p.agent === "string" && p.agent ? p.agent : "an agent";
    const rationale = typeof p.rationale === "string" ? p.rationale.trim() : "";
    return {
      id: e.id,
      ts: e.ts,
      href: agentHref(p.agent),
      icon: GraduationCap,
      title: `The coach has a suggestion for ${agent}`,
      body: rationale.length > 140 ? `${rationale.slice(0, 139)}…` : rationale,
    };
  }
  if (e.type === "assignment.requeued") {
    // v1.296.0: the daemon restarted with jobs mid-flight and put them back
    // in line. Payload: {count, ids}.
    // (Named `picked`, not `count`: interrupted-jobs-v1249 pins the first
    // count-declaration in this file as the interrupted-jobs badge count.)
    const picked =
      typeof p.count === "number" ? p.count : Array.isArray(p.ids) ? p.ids.length : 0;
    return {
      id: e.id,
      ts: e.ts,
      // Names no agent (several jobs, several assignees): Your team itself.
      href: agentHref(null),
      icon: RotateCcw,
      title: `${picked} queued task${picked === 1 ? "" : "s"} picked back up after a restart`,
      body: "",
    };
  }
  if (e.type === "mcp.pack_failed") {
    // v1.342.0: a tool pack failed to load, or its calls started failing.
    // Payload: {name, error, reason, fix}, published ONCE per pack per
    // episode. The TITLE is the plain reason (the error can carry a masked
    // tail); the fix is the body; the row opens the Tools page.
    // v1.355.0 (G32): the body also says what keeps working (packFailedBody).
    const name = typeof p.name === "string" && p.name ? p.name : "an extension";
    const reason =
      typeof p.reason === "string" && p.reason.trim()
        ? p.reason.trim()
        : typeof p.error === "string" && p.error.trim()
          ? p.error.trim()
          : "it did not start";
    return {
      id: e.id,
      ts: e.ts,
      href: "/tools",
      icon: AlertTriangle,
      title: `Extension ${name} is failing: ${reason}`,
      body: packFailedBody(p.fix),
    };
  }
  if (e.type === "media.completed" || e.type === "media.failed") {
    // v1.346.0: a media generation (image/video/audio/music) finished in the
    // background. The daemon appended the result to the chat it was asked
    // in; the row says so in the Handbook's words and opens THAT chat.
    // Payload: completed {job_id, kind, provider, thread_id, path, cost_note};
    // failed {job_id, kind, provider, thread_id, error} (plain words, masked
    // by the daemon). An agent-run job carries no thread_id: the row still
    // rings and opens the chat page.
    const kind =
      typeof p.kind === "string" && MEDIA_KINDS.has(p.kind) ? p.kind : "media";
    const ok = e.type === "media.completed";
    const body = ok
      ? typeof p.cost_note === "string"
        ? p.cost_note.trim()
        : ""
      : typeof p.error === "string"
        ? p.error.trim()
        : "";
    return {
      id: e.id,
      ts: e.ts,
      href: mediaHref(p.thread_id, ok ? p.path : undefined),
      icon: ok ? CheckCircle2 : AlertTriangle,
      title: ok ? `Your ${kind} is ready` : `Your ${kind} could not be made`,
      body: body.length > 140 ? `${body.slice(0, 139)}…` : body,
    };
  }
  if (e.type === "build.account_limit" || e.type === "build.resumed") {
    // v1.369.0: a Build session (Phase 5). Payload: {thread_id, title,
    // account_name, …}; session_id "build:<id>". The row opens Build on THAT
    // session. A limit stopped work nobody may be watching: it counts on the
    // badge. A resume is good news, said once.
    const thread =
      (typeof p.thread_id === "string" && p.thread_id.trim()) ||
      buildThreadOf(typeof e.session_id === "string" ? e.session_id : "") ||
      "";
    const title = typeof p.title === "string" ? p.title.trim() : "";
    const name = typeof p.account_name === "string" ? p.account_name.trim() : "";
    const limitRow = e.type === "build.account_limit";
    // v1.391.0 (long jobs, Phase 2): a reply a restart cut off while it
    // worked, carried on by itself at the next start (`reason: "restart"`).
    const restartRow = !limitRow && p.reason === "restart";
    // v1.393.0 (long jobs, Phase 4): the reply WAITS for the reset and
    // carries on by itself (a wait armed with no press) — news, not a
    // question: nothing to press, so it is not counted on the badge.
    const waitingRow = limitRow && p.waiting === true;
    // v1.393.0: …or it carried on with ANOTHER account of the same group.
    const from = typeof p.moved_from_name === "string" ? p.moved_from_name.trim() : "";
    const clipped = title.length > 140 ? `${title.slice(0, 139)}…` : title;
    return {
      id: e.id,
      ts: e.ts,
      href: thread ? buildSessionHref(thread) : "/terminals",
      icon: limitRow ? AlertTriangle : CheckCircle2,
      title: limitRow
        ? buildLimitHeadline(p.kind, name)
        : restartRow
          ? `A ${CODING_SESSION} carried on after Iron Jarvis restarted`
          : from
            ? buildMovedHeadline(from, name)
            : `A Build session resumed${name ? ` on the ${name} account` : ""}`,
      body: waitingRow ? buildLimitWaitingBody(title, p.resets_at) : clipped,
      ...(limitRow && !waitingRow ? { attention: true } : {}),
    };
  }
  if (e.type === "build.turn_finished") {
    // v1.374.0 (IJ harness part 2, Phase 3): a Build turn that ran longer
    // than a minute ENDED. Payload: {thread_id, turn_id, title, seconds,
    // outcome: "done" | "failed"}; session_id "build:<id>". It rings and
    // counts on the badge (the session waits for the user's next step) —
    // unless the user is looking at that session (the ping effect below);
    // the desktop app says it from the tray in these same words
    // (desktop/main.js runOutcomeNotice, one fixture).
    const thread =
      (typeof p.thread_id === "string" && p.thread_id.trim()) ||
      buildThreadOf(typeof e.session_id === "string" ? e.session_id : "") ||
      "";
    if (!thread) return null;
    return {
      id: e.id,
      ts: e.ts,
      href: buildSessionHref(thread),
      icon: p.outcome === "failed" ? AlertTriangle : CheckCircle2,
      title: buildFinishedHeadline(p.outcome),
      body: buildFinishedBody(p.title, p.seconds),
      attention: true,
      build: thread,
    };
  }
  if (e.type === "build.turn_stalled") {
    // v1.392.0 (long jobs, Phase 3): a Build reply made NO PROGRESS for ten
    // minutes (no frame, no tool output) while not waiting on the user —
    // ONE per stall episode. Payload: {thread_id, turn_id, title, words,
    // minutes, step?}; session_id "build:<id>". It rings and counts like a
    // finished long turn — unless the user is looking at that session (its
    // live line says it there, with Stop the step); the desktop app says it
    // from the tray in these same words (main.js runOutcomeNotice).
    const thread =
      (typeof p.thread_id === "string" && p.thread_id.trim()) ||
      buildThreadOf(typeof e.session_id === "string" ? e.session_id : "") ||
      "";
    if (!thread) return null;
    return {
      id: e.id,
      ts: e.ts,
      href: buildSessionHref(thread),
      icon: AlertTriangle,
      title: BUILD_STALLED_HEADLINE,
      body: buildStalledBody(p.title, p.words),
      attention: true,
      build: thread,
    };
  }
  if (e.type === "library.proposal") {
    // v1.370.0 (IJ harness Phase 7): a suggestion waits for the user's
    // Accept on a Build session's screen. Payload: {id, kind, folder_name,
    // thread_id, summary} — the folder's NAME, never its path. Quiet: it is
    // a suggestion, not news that stopped anything.
    const thread =
      (typeof p.thread_id === "string" && p.thread_id.trim()) ||
      buildThreadOf(typeof e.session_id === "string" ? e.session_id : "") ||
      "";
    const folder = typeof p.folder_name === "string" && p.folder_name.trim() ? p.folder_name.trim() : "";
    const summary = typeof p.summary === "string" ? p.summary.trim() : "";
    const lesson = p.kind !== "layer_edit";
    return {
      id: e.id,
      ts: e.ts,
      href: thread ? buildSessionHref(thread) : "/terminals",
      icon: Lightbulb,
      title: lesson
        ? `A lesson is suggested${folder ? ` for ${folder}` : ""}`
        : // Harness gaps (fix 4): the folder's `.ironjarvis/harness/` files
          // are its coding-session instructions — "harness" is the engine
          // room's word (lib/buildWords).
          `A change to the ${CODING_SESSION} instructions is suggested${folder ? ` for ${folder}` : ""}`,
      body: summary.length > 140 ? `${summary.slice(0, 139)}…` : summary,
    };
  }
  if (e.type === "computeruse.run_finished") {
    // Payload: {run_id, status, steps}. v1.358.0 (lane 8A): a run that
    // stopped to ASK is not a failure (its own words, the ask icon), a
    // failure says what happened in plain words, and the raw status and the
    // run id never reach the visible text (lib/browserRunWords). The row
    // opens the Browser page, where the run is.
    const words = browserRunWords(p.status, p.steps);
    return {
      id: e.id,
      ts: e.ts,
      href: "/computeruse",
      icon: BROWSER_RUN_ICON[words.tone],
      title: words.title,
      body: words.body,
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
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-tone-warn/25 bg-tone-warn/[0.08] text-tone-warn">
          <Workflow size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-zinc-100">
            Workflow &ldquo;{ask.workflow}&rdquo; needs an answer
          </span>
          <p className="mt-0.5 text-[12px] leading-snug text-tone-warn">{ask.question}</p>
          {ask.options.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {ask.options.map((o) => (
                <button
                  key={o}
                  type="button"
                  onClick={() => void submit(o)}
                  disabled={busy}
                  className="rounded-full border border-tone-warn/30 bg-tone-warn/[0.08] px-2.5 py-1 text-[11px] text-tone-warn transition-colors hover:bg-tone-warn/[0.16] disabled:opacity-50"
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
              placeholder="Type your answer. The run continues."
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
          {error && <p className="mt-1 text-[11px] text-tone-danger">{error}</p>}
          {/* v1.359.0 (lane 9B): the run is named by its workflow above;
              its id is the hover, never the visible words. */}
          <span
            data-testid="bell-waiting-run-when"
            title={runLinkTitle(ask.workflow, ask.runId)}
            className="mt-1 block font-mono text-[10px] text-zinc-600"
          >
            {clockTime(ask.startedAt)}
          </span>
        </div>
      </div>
    </li>
  );
}

/** v1.333.0: the daemon saw an unusual number of BACKGROUND runs (schedules,
 *  goals, workflows, assignments, the phone) going at once with no cap set,
 *  and asks whether to start a queue. The user's decision: no cap by
 *  default, ask instead. Payload {running, threshold, cap}; cap is what
 *  "Queue extras" sets. */
export interface BusyRunsAsk {
  id: string;
  ts: string;
  running: number;
  cap: number;
}

/** The newest runs.busy event as an ask (null = none, or a malformed one). */
export function parseBusyRuns(events: readonly IJEvent[]): BusyRunsAsk | null {
  const e = events.find((ev) => ev.type === "runs.busy");
  if (!e) return null;
  const p = e.payload ?? {};
  const running = typeof p.running === "number" && Number.isFinite(p.running) ? p.running : 0;
  const cap =
    typeof p.cap === "number" && Number.isInteger(p.cap) && p.cap > 0 ? p.cap : 3;
  if (running < 2) return null;
  return { id: e.id, ts: e.ts, running, cap };
}

/** The busy-runs ask as a bell row: the interrupted-job row's exact look
 *  (same tile, title, amber line and the same two buttons). "Queue extras"
 *  saves `max_concurrent_sessions` through the one settings writer (a ledger
 *  row with Undo, like any setting); "Not now" keeps the daemon quiet about
 *  it for a day. */
function BusyRunsRow({
  ask,
  onGone,
}: {
  ask: BusyRunsAsk;
  onGone: (id: string) => void;
}) {
  const [busy, setBusy] = useState<"queue" | "later" | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(kind: "queue" | "later") {
    if (busy) return;
    setBusy(kind);
    setError(null);
    try {
      if (kind === "queue") {
        await put("/settings/values", { values: { max_concurrent_sessions: ask.cap } });
      } else {
        await post("/runs/busy/not-now", {});
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(null); // a daemon blip keeps the buttons live
      return;
    }
    onGone(ask.id); // unmounts this row: no state updates past this point
  }

  return (
    <li data-testid="bell-busy-runs" className="px-4 py-3">
      <div className="flex items-start gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-tone-warn/25 bg-tone-warn/[0.08] text-tone-warn">
          <Layers size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-zinc-100">
            {ask.running} background runs are going at once
          </span>
          <p className="mt-0.5 text-[12px] leading-snug text-tone-warn">
            Queue the extras so only {ask.cap} run at a time?
          </p>
          <div className="mt-1.5 flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => void act("queue")}
              disabled={busy !== null}
              className="btn-accent px-3 py-1.5 text-[12px] disabled:opacity-50"
            >
              {busy === "queue" ? "Queuing…" : "Queue extras"}
            </button>
            <button
              type="button"
              onClick={() => void act("later")}
              disabled={busy !== null}
              title="Don't ask about this again today"
              className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-1.5 text-[12px] text-zinc-300 transition-colors hover:border-white/20 hover:text-zinc-100 disabled:opacity-50"
            >
              Not now
            </button>
          </div>
          {error && <p className="mt-1 text-[11px] text-tone-danger">{error}</p>}
          <span className="mt-1 block font-mono text-[10px] text-zinc-600">
            {clockTime(ask.ts)}
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
  /** Calls one answer covers — a batched ask (v1.247.0). 1 = a single call. */
  count: number;
  /** Seconds the run waits; 0/absent = until answered (v1.247.0). */
  timeoutS?: number;
  /** v1.299.0: the daemon offers "always" — this call AND exactly these
   *  arguments in the run's scope for 30 days. Absent = not offered. */
  canAlways?: boolean;
  /** v1.310.0 (W2-5): the mission ROOT this ask belongs to (a coordinator's
   *  own ask or a teammate's), and the project its screen lives in — so the
   *  row can open the mission and the user answers IN CONTEXT. Absent for a
   *  plain job and on an older daemon (no key). */
  missionId?: string;
  projectId?: string;
  /** v1.350.0: approved once or declined, nothing wider (the plan approval
   *  says `once_only`): the row offers no "Allow for this run". */
  onceOnly?: boolean;
  /** v1.352.0 (G24): the first line of the asking run's task ("for: …") and
   *  its origin ("schedule:nightly-brief" → "Your nightly-brief run"). Absent
   *  on an older daemon: the row says "An agent run" and no "for:" line. */
  task?: string;
  origin?: string;
  /** v1.352.0 (G25): the daemon's undo verdict for the tool — true = it can
   *  be undone, false = it may not be; absent = nothing to undo (a read). */
  reversible?: boolean;
  /** v1.372.0: the Build session (its thread id) a HELPER agent belongs to
   *  — the row opens Build on it, where the helper's row and card are.
   *  Absent for every other run and on an older daemon. */
  buildSession?: string;
}

/** v1.351.0 (G03, with G05): a CHAT turn parked on its own approval card
 *  while nobody watches it. GET /chat/approvals/pending lists one
 *  metadata-only row per such chat — {id: "chat:<thread_id>", kind: "chat",
 *  thread_id, tool}, never arguments — and the card can only be answered IN
 *  the chat (it shows what the call would do), so the bell row is a link,
 *  never Approve/Deny buttons. */
export interface ChatWaitingAsk {
  id: string;
  threadId: string;
  /** v1.369.0: the row is a Build session's (`surface: "build"`), not a
   *  chat's: it reads as the session and opens it in Build. */
  build?: boolean;
}

/** Parse one pending row of kind "chat" (null = not a chat row). */
export function parseChatAsk(raw: unknown): ChatWaitingAsk | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (r.kind !== "chat") return null;
  const id = typeof r.id === "string" ? r.id : "";
  if (!id) return null;
  const threadId = typeof r.thread_id === "string" ? r.thread_id.trim() : "";
  return r.surface === "build" ? { id, threadId, build: true } : { id, threadId };
}

/** Where a waiting chat opens: that chat, else the chat page. A Build
 *  session (v1.369.0) opens in Build, on that session. */
export function chatAskHref(ask: ChatWaitingAsk): string {
  if (ask.build) return ask.threadId ? buildSessionHref(ask.threadId) : "/terminals";
  return ask.threadId ? `/chat?thread=${encodeURIComponent(ask.threadId)}` : "/chat";
}

/** The waiting row's words: a chat's, or (v1.369.0) a Build session's. */
export function chatAskRowWords(ask: ChatWaitingAsk): { title: string; open: string } {
  return ask.build
    ? { title: "A Build session is waiting for your OK", open: "Open the session in Build" }
    : { title: "A chat is waiting for your OK", open: "Open the chat" };
}

/** The waiting chat as a bell row: the computer-use row's exact look (the
 *  amber tile, a title, one quiet line, the arrow). */
function ChatWaitingRow({ ask, onOpen }: { ask: ChatWaitingAsk; onOpen: () => void }) {
  const words = chatAskRowWords(ask);
  return (
    <li data-testid="bell-chat-waiting" data-surface={ask.build ? "build" : "chat"}>
      <Link
        href={chatAskHref(ask)}
        onClick={onOpen}
        className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.04]"
      >
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-tone-warn/25 bg-tone-warn/[0.08] text-tone-warn">
          <MessageSquare size={15} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-zinc-100">{words.title}</span>
          <span className="block text-[11px] text-zinc-500">{words.open}</span>
        </span>
        <ArrowRight size={13} className="shrink-0 text-zinc-600" />
      </Link>
    </li>
  );
}

/** v1.353.0 (G52): a Build pane whose coding CLI is waiting on the user
 *  ("Allow this edit?"), as a bell row — the waiting chat's exact look (the
 *  amber tile, a title, one quiet line, the arrow). The answer is given IN
 *  the pane (the question is on its screen), so the row is a link to it. */
function PaneWaitingRow({ pane, onOpen }: { pane: BlockedPane; onOpen: () => void }) {
  return (
    <li data-testid="bell-pane-waiting" data-pane={pane.id}>
      <Link
        href={paneWaitingHref(pane)}
        onClick={onOpen}
        className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.04]"
      >
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-tone-warn/25 bg-tone-warn/[0.08] text-tone-warn">
          <SquareTerminal size={15} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-zinc-100">
            {paneWaitingTitle(pane)}
          </span>
          <span className="block truncate text-[11px] text-zinc-500">
            {pane.name ? `Open ${pane.name}` : "Open the terminal"}
          </span>
        </span>
        <ArrowRight size={13} className="shrink-0 text-zinc-600" />
      </Link>
    </li>
  );
}

/** Parse one /chat/approvals/pending row (null = not a usable row). A chat
 *  row (v1.351.0) is not an agent ask: `parseChatAsk` reads it. */
function parseAgentApproval(raw: unknown): PendingAgentApproval | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (r.kind === "chat") return null;
  const id = typeof r.id === "string" ? r.id : "";
  if (!id) return null;
  return {
    id,
    tool: typeof r.tool === "string" && r.tool ? r.tool : "unknown",
    sessionId: typeof r.session_id === "string" ? r.session_id : "",
    requestedAt: typeof r.requested_at === "string" ? r.requested_at : "",
    count: typeof r.count === "number" && r.count > 1 ? r.count : 1,
    ...(typeof r.timeout_s === "number" ? { timeoutS: r.timeout_s } : {}),
    ...(r.can_always === true ? { canAlways: true } : {}),
    ...(typeof r.mission_id === "string" && r.mission_id ? { missionId: r.mission_id } : {}),
    ...(typeof r.project_id === "string" && r.project_id ? { projectId: r.project_id } : {}),
    ...(r.once_only === true ? { onceOnly: true } : {}),
    // v1.352.0: words about the run (a string, never arguments) and the undo
    // verdict (a real boolean only).
    ...(typeof r.task === "string" && r.task.trim() ? { task: r.task.trim() } : {}),
    ...(typeof r.origin === "string" && r.origin.trim() ? { origin: r.origin.trim() } : {}),
    ...(typeof r.reversible === "boolean" ? { reversible: r.reversible } : {}),
    // v1.372.0: a Build session's helper names its session.
    ...(typeof r.build_session === "string" && r.build_session.trim()
      ? { buildSession: r.build_session.trim() }
      : {}),
  };
}

/** One paused agent ask in the dropdown: WHO asks and WHAT it wants, in the
 *  chat card's words (v1.352.0, G24): "Your nightly-brief run wants to save
 *  a file" (the raw tool id in the title), "for: <the run's task>", whether
 *  it can be undone (G25), and the card's three answers — Allow once / Allow
 *  for this run / Deny (lib/approvalWords) — POSTing the same route the chat
 *  card posts. "Allow for this run" (v1.232.0, audit A9) is the card's
 *  "conversation" answer: the daemon widens the run's grant, releases every
 *  sibling ask of the same tool at once (a 28-file batch is one click, not
 *  28 at 15 s poll lag each) and keeps the grant for the run's continues. A
 *  404 means it was answered elsewhere or timed out (the pause window is
 *  bounded) — the row leaves without a retry; any other failure keeps the
 *  row answerable. A landed answer hands the row to the bell, which says
 *  what happened for a few seconds (`AgentAnsweredRow`) before it leaves. */
type AgentAnswer = "once" | "conversation" | "deny" | "always";

/** v1.359.0 (lane 9B): the hover on a link to a run: the task it is named
 *  by (cut on the row) and the run's id, the record. The id is never the
 *  link's visible words. */
export function runLinkTitle(task: string, runId: string): string {
  const what = task.trim();
  return what ? `${what} · run ${runId}` : `Run ${runId}`;
}

/* ---- v1.361.0 (lane 11A): the ONE new ask, named in the desktop ping ----
 * While a window is open the bell's "N pending" ping is the only desktop
 * notice for an ask ("This PC" stays quiet for asks since v1.360.0), and a
 * bare count lost what This PC's toast used to say: WHICH workflow asks
 * what, which run wants to do what. When exactly one ask arrived since the
 * last look, its count is followed by the bell row's own words. */

/** A parked workflow question, in its row's words: “Nightly” asks “Send it?”. */
export function waitingAskWords(ask: { workflow: string; question: string }): string {
  const name = ask.workflow.trim() || "workflow";
  const question = clip(ask.question);
  return question ? `“${name}” asks “${question}”` : `“${name}” needs an answer`;
}

/** An agent ask, in its row's words: "Your nightly-brief run wants to save a
 *  file for “Draft the Q3 letter”" (the task the row's "for:" line shows). */
export function agentAskWords(ask: {
  tool: string;
  origin?: string;
  task?: string;
  count?: number;
}): string {
  const head =
    ask.tool === PLAN_TOOL
      ? `${runSubject(ask.origin)} wants you to approve its plan`
      : !ask.tool || ask.tool === "unknown"
        ? `${runSubject(ask.origin)} is waiting for your OK`
        : askSentence(ask.origin, ask.tool);
  const times = ask.count && ask.count > 1 ? ` × ${ask.count}` : "";
  const task = clip(ask.task ?? "");
  return `${head}${times}${task ? ` for “${task}”` : ""}`;
}

/** A chat parked on its card: what the call would do, when the listing
 *  names the tool (names only, never arguments). "" when it names none. */
export function chatAskWords(tool: string | undefined): string {
  const id = (tool ?? "").trim();
  return id && id !== "unknown" ? `it wants to ${askPhrase(id)}` : "";
}

/** The pending listing's chat rows: id → the tool it names. */
function chatToolsById(raw: unknown): ReadonlyMap<string, string> {
  const out = new Map<string, string>();
  for (const r of Array.isArray(raw) ? raw : []) {
    if (!r || typeof r !== "object") continue;
    const row = r as Record<string, unknown>;
    if (row.kind === "chat" && typeof row.id === "string" && typeof row.tool === "string") {
      out.set(row.id, row.tool);
    }
  }
  return out;
}

/** Every waiting ask's identity, by kind, at one look. */
function askIdsOf(
  waiting: readonly { key: string }[],
  agentAsks: readonly { id: string }[],
  chatAsks: readonly { id: string }[],
): ReadonlySet<string> {
  return new Set([
    ...waiting.map((w) => `w:${w.key}`),
    ...agentAsks.map((a) => `a:${a.id}`),
    ...chatAsks.map((c) => `c:${c.id}`),
  ]);
}

/** The words to put after ONE new ask's count, by kind — only when the ping
 *  adds exactly one (`added`) and exactly one ask is new since the last look
 *  (`seen`); several at once keep their counts alone. */
export function newAskWords(
  added: number,
  asks: {
    waiting: readonly WaitingAsk[];
    agentAsks: readonly PendingAgentApproval[];
    chatAsks: readonly ChatWaitingAsk[];
  },
  seen: ReadonlySet<string>,
  chatTools: ReadonlyMap<string, string> = new Map(),
): { waiting: string; agent: string; chat: string } {
  const none = { waiting: "", agent: "", chat: "" };
  if (added !== 1) return none;
  const w = asks.waiting.filter((x) => !seen.has(`w:${x.key}`));
  const a = asks.agentAsks.filter((x) => !seen.has(`a:${x.id}`));
  const c = asks.chatAsks.filter((x) => !seen.has(`c:${x.id}`));
  if (w.length + a.length + c.length !== 1) return none;
  return {
    waiting: w.length ? waitingAskWords(w[0]) : "",
    agent: a.length ? agentAskWords(a[0]) : "",
    chat: c.length ? chatAskWords(chatTools.get(c[0].id)) : "",
  };
}

/** Where a run's ask opens: its mission screen, a Build session's helper its
 *  Build session (v1.372.0), else its session page. */
function agentAskHref(ask: PendingAgentApproval): string | null {
  if (ask.missionId) return missionPath(ask.missionId, ask.projectId ?? "");
  if (ask.buildSession) return buildSessionHref(ask.buildSession);
  return ask.sessionId ? `/sessions/${encodeURIComponent(ask.sessionId)}` : null;
}

function AgentApprovalRow({
  ask,
  onGone,
  onAnswered,
}: {
  ask: PendingAgentApproval;
  onGone: (id: string) => void;
  /** The answer landed: the bell shows the outcome, then drops the row. */
  onAnswered: (ask: PendingAgentApproval, outcome: "approved" | "denied") => void;
}) {
  const [busy, setBusy] = useState<AgentAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function answer(decision: AgentAnswer) {
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
    // Unmounts this row (the bell draws the outcome in its place) — no state
    // updates past this point.
    onAnswered(ask, decision === "deny" ? "denied" : "approved");
  }

  const planAsk = ask.tool === PLAN_TOOL;
  const undo = planAsk ? null : undoLine(ask.reversible);
  return (
    <li data-testid="bell-agent-approval" className="px-4 py-3">
      <div className="flex items-start gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-tone-warn/25 bg-tone-warn/[0.08] text-tone-warn">
          <ShieldAlert size={15} />
        </span>
        <div className="min-w-0 flex-1">
          <span className="block text-sm font-medium text-zinc-100">
            An agent is asking permission
          </span>
          <p data-testid="bell-approval-line" className="mt-0.5 text-[12px] leading-snug text-tone-warn">
            {planAsk ? (
              // v1.350.0: the plan approval is not a tool to run.
              <>{runSubject(ask.origin)} wants you to approve its plan</>
            ) : (
              // v1.352.0: the tool in plain words; the exact id it will run
              // stays in the title (the record a grant is keyed on).
              <span title={`The exact tool: ${ask.tool}`}>{askSentence(ask.origin, ask.tool)}</span>
            )}
            {ask.count > 1 ? (
              <span data-testid="bell-approval-count"> × {ask.count} (one answer covers all)</span>
            ) : null}
            .{" "}
            {ask.timeoutS && ask.timeoutS > 0
              ? `The run waits up to ${Math.max(1, Math.round(ask.timeoutS / 60))} min for you.`
              : "The run is waiting for you."}
          </p>
          {ask.task ? (
            <p
              data-testid="bell-approval-task"
              title={ask.task}
              className="mt-0.5 truncate text-[11px] text-zinc-500"
            >
              for:{" "}
              {ask.sessionId ? (
                // v1.359.0 (lane 9B): the run is named by its task and this
                // is its link; the run's id lives only in the title (it was
                // the link's visible text, "session_job42…").
                <Link
                  data-testid="bell-approval-run"
                  href={`/sessions/${encodeURIComponent(ask.sessionId)}`}
                  title={runLinkTitle(ask.task, ask.sessionId)}
                  className="text-accent-soft transition-colors hover:text-accent"
                >
                  {ask.task}
                </Link>
              ) : (
                ask.task
              )}
            </p>
          ) : null}
          {undo ? (
            <p
              data-testid="bell-approval-undo"
              data-reversible={ask.reversible ? "true" : "false"}
              className="mt-0.5 text-[11px] text-zinc-500"
            >
              {undo}
            </p>
          ) : null}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
            <button
              type="button"
              onClick={() => void answer("once")}
              disabled={busy !== null}
              className="btn-accent px-3 py-1.5 text-[12px] disabled:opacity-50"
            >
              {busy === "once" ? "Approving…" : ALLOW_ONCE}
            </button>
            {!ask.onceOnly && !planAsk && (
              <button
                type="button"
                onClick={() => void answer("conversation")}
                disabled={busy !== null}
                // v1.358.0 (lane 8A): the tool in words (lib/toolWords via
                // askPhrase), never its raw id.
                title={`Let this run ${askPhrase(ask.tool)} for the rest of the run. Every waiting ask like this one clears at once.`}
                className="rounded-lg border border-accent/25 bg-accent/[0.06] px-3 py-1.5 text-[12px] text-accent-soft transition-colors hover:border-accent/40 disabled:opacity-50"
              >
                {busy === "conversation" ? "Allowing…" : scopeLabel("run")}
              </button>
            )}
            {ask.canAlways && (
              // v1.299.0: a standing grant on the EXACT arguments — offered
              // only when the daemon said it can key one (never for a batch).
              <button
                type="button"
                data-testid="bell-approval-always"
                onClick={() => void answer("always")}
                disabled={busy !== null}
                title={`Always let it ${askPhrase(ask.tool)} with exactly these details, in this run's scope, for 30 days. You can take it back on the Autonomy page.`}
                className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-1.5 text-[12px] text-zinc-300 transition-colors hover:border-accent/40 hover:text-accent-soft disabled:opacity-50"
              >
                {busy === "always" ? "Allowing…" : ALWAYS_EXACT}
              </button>
            )}
            <button
              type="button"
              onClick={() => void answer("deny")}
              disabled={busy !== null}
              className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-1.5 text-[12px] text-zinc-300 transition-colors hover:border-tone-danger/30 hover:text-tone-danger disabled:opacity-50"
            >
              {busy === "deny" ? "Denying…" : DENY}
            </button>
          </div>
          {error && <p className="mt-1 text-[11px] text-tone-danger">{error}</p>}
          {ask.missionId ? (
            // v1.310.0: a mission's ask opens its mission screen — the
            // objective, the team and this same question inline — so the
            // user can see WHY it asks before answering. The buttons above
            // still answer from here.
            <Link
              data-testid="bell-approval-mission"
              href={missionPath(ask.missionId, ask.projectId ?? "")}
              className="mt-1 inline-block text-[12px] text-accent-soft transition-colors hover:text-accent"
            >
              Open the objective →
            </Link>
          ) : ask.buildSession ? (
            // v1.372.0: a Build session's helper opens that session in Build
            // — the helper's row is there, with this same question on it.
            <Link
              data-testid="bell-approval-build"
              href={buildSessionHref(ask.buildSession)}
              className="mt-1 inline-block text-[12px] text-accent-soft transition-colors hover:text-accent"
            >
              Open the Build session →
            </Link>
          ) : null}
          <span className="mt-1 flex items-center gap-1.5 font-mono text-[10px] text-zinc-600 empty:hidden">
            {ask.sessionId && !ask.task ? (
              // No task to name it by: the link says what it opens, in the
              // words the answered row uses; the id stays in the title.
              <Link
                data-testid="bell-approval-run"
                href={`/sessions/${encodeURIComponent(ask.sessionId)}`}
                title={runLinkTitle("", ask.sessionId)}
                className="font-sans text-accent-soft transition-colors hover:text-accent"
              >
                Open run
              </Link>
            ) : ask.sessionId ? null : (
              "—"
            )}
            {ask.requestedAt ? (
              <>
                {ask.sessionId && ask.task ? null : "· "}
                {clockTime(ask.requestedAt)}
              </>
            ) : null}
          </span>
        </div>
      </div>
    </li>
  );
}

/** An agent ask answered from this dropdown (v1.352.0, G24): the row stays
 *  for `ANSWERED_LINGER_MS` saying what happened — "Approved. The run
 *  continues · Open run" or "Denied. That action will not run." — so an
 *  answer never just vanishes. It never counts on the badge (nothing waits
 *  on the user any more). A screen reader hears it from the dropdown's own
 *  live region (`bell-answered-live`, v1.352.0 re-review): this row mounts
 *  with its words already in it, which many screen readers never announce. */
function AgentAnsweredRow({
  ask,
  outcome,
}: {
  ask: PendingAgentApproval;
  outcome: "approved" | "denied";
}) {
  const href = agentAskHref(ask);
  return (
    <li data-testid="bell-agent-answered" data-outcome={outcome} className="px-4 py-3">
      <div className="flex items-start gap-3">
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-white/10 bg-white/[0.03] text-zinc-400">
          {outcome === "denied" ? <ShieldAlert size={15} /> : <ShieldCheck size={15} />}
        </span>
        <p
          data-testid="bell-agent-answered-line"
          className="min-w-0 flex-1 pt-1.5 text-[12px] leading-snug text-zinc-300"
        >
          {answeredLine(outcome)}
          {outcome === "approved" && !href ? "." : null}
          {outcome === "approved" && href ? (
            <>
              {" · "}
              <Link
                data-testid="bell-agent-answered-open"
                href={href}
                className="text-accent-soft transition-colors hover:text-accent"
              >
                Open run
              </Link>
            </>
          ) : null}
        </p>
      </div>
    </li>
  );
}

/** How many rows each activity list shows ("Recent activity" and, since
 *  v1.351.0, "While you were away"). */
const ACTIVITY_SHOWN = 6;

/** The rows a list shows (v1.351.0): at most `max`, in the order given
 *  (newest first), keeping the rows that need the user (`attention`) ahead
 *  of the rest — a run of quiet "finished" rows must never push a failure
 *  out of sight (and off the badge). With no attention row this is exactly
 *  the first `max`, as before. */
export function pickShown(
  items: readonly ActivityItem[],
  max: number = ACTIVITY_SHOWN,
): ActivityItem[] {
  const keep = new Set<string>();
  for (const it of items) if (it.attention && keep.size < max) keep.add(it.id);
  for (const it of items) if (keep.size < max) keep.add(it.id);
  return items.filter((it) => keep.has(it.id));
}

/** One informational row — the same markup for the live list and the
 *  replayed one (v1.351.0: lifted out of the list so both share it). */
function ActivityRow({
  item,
  onOpen,
  testId,
}: {
  item: ActivityItem;
  onOpen: () => void;
  testId?: string;
}) {
  const Icon = item.icon;
  return (
    <li data-testid={testId} data-attention={item.attention ? "true" : undefined}>
      <Link
        href={item.href}
        onClick={onOpen}
        className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.04]"
      >
        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-white/10 bg-white/[0.03] text-zinc-300">
          <Icon size={15} />
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-zinc-100">
            {item.title}
          </span>
          {item.body && (
            <span className="block truncate text-[11px] text-zinc-500">{item.body}</span>
          )}
          <span className="mt-0.5 block font-mono text-[10px] text-zinc-600">
            {clockTime(item.ts)}
          </span>
        </span>
        <ArrowRight size={13} className="shrink-0 text-zinc-600" />
      </Link>
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
  const { events } = useEvents(100, { types: BELL_EVENT_TYPES });
  // Computer-use approvals don't ride the event stream, so poll their count.
  const cu = usePolledApi<ComputerUseStatus>("/computeruse", 15000);
  const pendingApprovals = cu.data?.pending_approvals ?? 0;
  // The live event buffer is empty right after a page reload, so seed the pending
  // review count from /diagnostics (the authoritative current count) — otherwise
  // a reload silently hides reviews that are still waiting on the user.
  const diag = usePolledApi<{
    pending_reviews?: number;
    background_loops?: Record<string, LoopHealth>;
  }>("/diagnostics", 15000);
  const polledReviews = diag.data?.pending_reviews ?? 0;
  // v1.229.0: a background loop that has been failing for 5+ minutes is
  // something only the user can fix (a dead fleet box, a revoked Slack
  // token) — it rides the same poll and is named with its last error.
  const failingLoops = useMemo(
    () => staleFailingLoops(diag.data?.background_loops, Date.now()),
    [diag.data],
  );
  // v1.355.0 (G32): the desktop ping's words for them — one failing task is
  // named in the row's words, more are counted (the rows name each one).
  const failingLoopsPing =
    failingLoops.length === 1
      ? loopWords(failingLoops[0].name).problem
      : failingLoops.length > 1
        ? `${failingLoops.length} background tasks failing`
        : "";
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
  // v1.249.0 (R-02): work a restart cut off. It waits on the user exactly like
  // the rows above — an update used to end these silently and offer nothing.
  const interrupted = useInterruptedJobs(15000);
  // v1.353.0 (G52): Build panes whose coding CLI stopped on a question. The
  // same tiny listing the Build page polls (names + state only); a pane that
  // waits is told where the user is, not only on Build. On Build itself the
  // pane already wears "needs you", so the bell stays quiet there.
  const panesApi = usePolledApi<unknown>("/terminals/activity", 15000);
  const pathname = usePathname();

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
  // v1.351.0 (G03): chat turns parked on their own card while nobody looks
  // (the same pending list, kind "chat"). They wait on the user like the
  // asks above, so they count; the row only opens the chat.
  // Readiness 12B review: a chat whose card a chat page in THIS window shows
  // (or is mounting again to show — a reload, `/` ⇄ `/chat`) is not listed or
  // pinged here: the card is its one notice (lib/chatAway.shownHere).
  const chatHere = useSyncExternalStore(subscribeChatHere, chatHereVersion, () => 0);
  const chatAsks = useMemo(() => {
    const out: ChatWaitingAsk[] = [];
    for (const raw of agentAsksApi.data?.approvals ?? []) {
      const ask = parseChatAsk(raw);
      if (ask && !(!ask.build && shownHere(ask))) out.push(ask);
    }
    return out;
    // `chatHere` is the registry's version: a page that opens or leaves a
    // chat here re-reads the rule.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentAsksApi.data, chatHere]);
  // v1.353.0 (G52): one row per pane in state `blocked` — never on Build.
  const paneAsks = useMemo(
    () => (onBuildPage(pathname) ? [] : blockedPanes(panesApi.data)),
    [panesApi.data, pathname],
  );

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

  // Readiness 12B (G05): a chat page in THIS window that left the screen with
  // a turn parked on the user's OK told the daemon so (lib/chatAway); read
  // the list now, so the row is here on the page the user went to — not on
  // the next 15-second poll.
  useEffect(() => {
    const onChanged = () => reloadAgentAsks();
    window.addEventListener(CHAT_ASKS_CHANGED_EVENT, onChanged);
    return () => window.removeEventListener(CHAT_ASKS_CHANGED_EVENT, onChanged);
  }, [reloadAgentAsks]);

  const handleAgentAskGone = (id: string) => {
    setAnsweredAgentAsks((prev) => new Set(prev).add(id));
    reloadAgentAsks();
  };
  // v1.352.0 (G24): an ask answered HERE says what happened for a few
  // seconds before it leaves. Held by the bell, not the row: the answer's own
  // `approval.resolved` re-polls the list at once, and a row that lived on
  // the poll would vanish with it.
  const [answeredRows, setAnsweredRows] = useState<
    { ask: PendingAgentApproval; outcome: "approved" | "denied" }[]
  >([]);
  const answeredTimers = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  useEffect(() => {
    const timers = answeredTimers.current;
    return () => {
      for (const t of timers.values()) clearTimeout(t);
      timers.clear();
    };
  }, []);
  const handleAgentAskAnswered = (ask: PendingAgentApproval, outcome: "approved" | "denied") => {
    setAnsweredRows((prev) => [...prev.filter((r) => r.ask.id !== ask.id), { ask, outcome }]);
    handleAgentAskGone(ask.id);
    const timers = answeredTimers.current;
    const old = timers.get(ask.id);
    if (old) clearTimeout(old);
    timers.set(
      ask.id,
      setTimeout(() => {
        timers.delete(ask.id);
        setAnsweredRows((prev) => prev.filter((r) => r.ask.id !== ask.id));
      }, ANSWERED_LINGER_MS),
    );
  };

  // v1.333.0: the busy-runs ask (the newest runs.busy), until it is answered
  // here. The daemon sends one per episode, so nothing re-polls it.
  const [answeredBusy, setAnsweredBusy] = useState<ReadonlySet<string>>(new Set());
  const busyAsk = useMemo(() => {
    const ask = parseBusyRuns(events);
    return ask && !answeredBusy.has(ask.id) ? ask : null;
  }, [events, answeredBusy]);
  const handleBusyGone = (id: string) => {
    setAnsweredBusy((prev) => new Set(prev).add(id));
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
  // inflate `count`. (v1.351.0: an `attention` row — unattended work that
  // failed or fell short — is added to the BADGE and the tab title as
  // `unseen` until the bell is opened; `count` and its "N pending" ping are
  // unchanged.) Dedupe by event id (the events buffer is already
  // newest-first).
  // v1.351.0: every mapped live row (newest first) — the desktop ping reads
  // the newest of THESE — and the 6 the list shows, failures kept in sight.
  const allActivity = useMemo(() => {
    const out: ActivityItem[] = [];
    const seen = new Set<string>();
    for (const e of events) {
      const item = toActivity(e);
      if (!item || seen.has(item.id)) continue;
      seen.add(item.id);
      out.push(item);
    }
    // v1.360.0 (lane 10A): a failed workflow is ONE row (its step's row
    // folds into it).
    return oneRowPerWorkflow(out);
  }, [events]);
  const activity = useMemo(() => pickShown(allActivity), [allActivity]);

  // v1.351.0 (G04): WHAT HAPPENED WHILE THE WINDOW WAS CLOSED. Closing to the
  // tray destroys this renderer and the live socket replays nothing, so on
  // first mount the bell reads the persisted events it maps, newer than the
  // last time it was opened (lib/bellAway), through the same toActivity.
  // Shown under "While you were away", never pinged (they are not new to the
  // desktop's clock — a reload must not re-fire a backlog). Nothing is set
  // when nothing came back, so an empty read costs no render. `get` is
  // reached through a resolved promise: a test's bare lib/api mock (or an
  // older daemon's 4xx) degrades to no rows, never a crash.
  const [away, setAway] = useState<ActivityItem[]>([]);
  useEffect(() => {
    let alive = true;
    const path = awayPath(BELL_ACTIVITY_TYPES, awaySince());
    void Promise.resolve()
      .then(() => get<{ entries?: unknown }>(path))
      .then((res) => {
        if (!alive) return;
        const rows: ActivityItem[] = [];
        for (const ev of auditEntriesToEvents(res?.entries, BELL_ACTIVITY_TYPES)) {
          const item = toActivity(ev);
          // v1.374.0 review: a Build turn the user WATCHED end is not news
          // after a reload (lib/buildWatching noteWatched) — never "away".
          if (item && !(item.build && wasWatched(item.id))) rows.push(item);
        }
        if (rows.length) setAway(rows);
      })
      .catch(() => {
        /* the bell's memory is a bonus: the live rows still work */
      });
    return () => {
      alive = false;
    };
  }, []);
  const awayShown = useMemo(() => {
    const live = new Set(allActivity.map((a) => a.id));
    return pickShown(
      oneRowPerWorkflow(
        away.filter((a) => !live.has(a.id)),
        allActivity,
      ),
    );
  }, [away, allActivity]);

  // v1.351.0 (G03/G04): failures and shortfalls of unattended work count on
  // the badge until the user OPENS the bell. Opening records the moment
  // (`ij.bell.lastSeen`), so the next window's replay starts after it and
  // what was seen is not shown again.
  const [seenAttention, setSeenAttention] = useState<ReadonlySet<string>>(new Set());
  const attentionKey = useMemo(
    () =>
      [...activity, ...awayShown]
        .filter((a) => a.attention)
        .map((a) => a.id)
        .join("\n"),
    [activity, awayShown],
  );
  // v1.360.0 (lane 10A): a failed workflow's row that stands in for a step
  // row the user already saw is seen too — one outcome, counted once.
  // v1.361.0 (lane 11A): only the row of the step that ENDED the run stands
  // in (`standsInFor`) — a retried attempt the user saw is another outcome.
  const absorbedBy = useMemo(() => {
    const m = new Map<string, readonly string[]>();
    for (const a of [...activity, ...awayShown]) if (a.standsInFor?.length) m.set(a.id, a.standsInFor);
    return m;
  }, [activity, awayShown]);
  const unseen = attentionKey
    ? attentionKey
        .split("\n")
        .filter(
          (id) =>
            !seenAttention.has(id) && !(absorbedBy.get(id) ?? []).some((s) => seenAttention.has(s)),
        ).length
    : 0;
  useEffect(() => {
    if (!open) return;
    writeBellLastSeen();
    if (!attentionKey) return;
    setSeenAttention((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (const id of attentionKey.split("\n")) {
        if (!next.has(id)) {
          next.add(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [open, attentionKey]);

  // Use the larger of live-streamed vs polled reviews so neither a fresh reload
  // (no events yet) nor a just-arrived live event under-reports the badge/title.
  const reviewish = Math.max(reviews.length, polledReviews) + pendingApprovals;
  // Parked workflow questions and paused agent asks wait on the user exactly
  // like reviews/approvals.
  const count =
    reviewish +
    waiting.length +
    agentAsks.length +
    chatAsks.length +
    paneAsks.length +
    failingLoops.length +
    interrupted.jobs.length +
    (busyAsk ? 1 : 0);
  // v1.351.0: what the badge and the tab title show — the work waiting on
  // the user PLUS the unattended failures they have not looked at yet. The
  // "N pending" desktop ping below still keys on `count` alone: a failure
  // row pings once, as itself (the activity ping), never twice.
  const badge = count + unseen;
  // v1.226.0: a polled source that FAILED (non-0 status, e.g. a 500) makes the
  // badge under-count — "You're all caught up" would then be a false empty
  // state. Offline (status 0) is the banner's story, not the bell's.
  const pollError =
    [cu.error, diag.error, runsApi.error, agentAsksApi.error, interrupted.error].find(
      (e) => e && e.status !== 0,
    ) ?? null;

  // Desktop notifications + browser tab title are owned here, app-wide.
  const { permission, requestPermission, notify } = useDesktopNotifications();
  const prevCount = useRef(count);
  const askedPermission = useRef(false);
  // v1.361.0 (lane 11A): the asks there were at the last look, so a ping
  // that adds exactly ONE can say which (see `newAskWords`).
  const prevAskIds = useRef<ReadonlySet<string>>(new Set());
  const chatAskTools = useMemo(() => chatToolsById(agentAsksApi.data?.approvals), [agentAsksApi.data]);

  // Reflect pending work in the tab title (so a backgrounded user notices) and
  // ping a desktop notification on each UPWARD transition of the count.
  useEffect(() => {
    const prev = prevCount.current;
    prevCount.current = count;
    const seenAsks = prevAskIds.current;
    prevAskIds.current = askIdsOf(waiting, agentAsks, chatAsks);

    // v1.283.0: a popped-out module titles its window by name (TitleBar
    // leaves the base title on <html data-ij-title>); the count rides in front.
    const base = document.documentElement.dataset.ijTitle || "Iron Jarvis";
    if (badge === 0) {
      document.title = base;
    } else {
      document.title = `(${badge}) ${base}`;
    }

    // v1.360.0 integrate (lane 10A note g): the "N pending" ping is THE desktop
    // notice for an ask on this PC ("This PC" stays quiet for asks), so it is
    // said once per app, by the window that speaks for it — the main window,
    // else (closed to the tray) the one pop-out the desktop app names
    // (v1.361.0, lane 11A); any other pop-out keeps its title count only.
    if (count > prev && !quietHere()) {
      // v1.361.0 (lane 11A): ONE new ask is named after its count, in the
      // bell row's own words — the only desktop notice for it while a window
      // is open, so it says WHAT waits (This PC's toast did, until v1.360.0
      // quieted it). Several at once keep the counts alone.
      const named = newAskWords(
        count - prev,
        { waiting, agentAsks, chatAsks },
        seenAsks,
        chatAskTools,
      );
      const parts: string[] = [];
      if (reviews.length)
        parts.push(`${reviews.length} review${reviews.length === 1 ? "" : "s"} awaiting approval`);
      if (pendingApprovals)
        // v1.353.0 (G54): what it is, in the user's words.
        parts.push(
          pendingApprovals === 1
            ? "A website action is waiting for your OK"
            : `${pendingApprovals} website actions are waiting for your OK`,
        );
      if (waiting.length)
        parts.push(
          `${waiting.length} workflow question${waiting.length === 1 ? "" : "s"} waiting` +
            (named.waiting ? `: ${named.waiting}` : ""),
        );
      if (agentAsks.length)
        parts.push(
          `${agentAsks.length} agent${agentAsks.length === 1 ? "" : "s"} asking permission` +
            (named.agent ? `: ${named.agent}` : ""),
        );
      // v1.369.0 review: a Build session parked on the user lists as a chat
      // row (`surface: "build"`) but is said as the SESSION it is — its row
      // already does; the ping read "1 chat waiting for your OK" for it.
      const plainChats = chatAsks.filter((c) => !c.build);
      const buildAsks = chatAsks.filter((c) => c.build);
      const newIsBuild = !!named.chat && buildAsks.some((c) => !seenAsks.has(`c:${c.id}`));
      if (plainChats.length)
        parts.push(
          `${plainChats.length} chat${plainChats.length === 1 ? "" : "s"} waiting for your OK` +
            (named.chat && !newIsBuild ? `: ${named.chat}` : ""),
        );
      if (buildAsks.length)
        parts.push(
          `${buildAsks.length} Build session${buildAsks.length === 1 ? "" : "s"} waiting for your OK` +
            (named.chat && newIsBuild ? `: ${named.chat}` : ""),
        );
      // v1.353.0 (G52): each waiting pane by its CLI's name.
      for (const pane of paneAsks) parts.push(paneWaitingTitle(pane));
      // v1.355.0 (G32): the failing tasks in the rows' words (failingLoopsPing).
      if (failingLoopsPing) parts.push(failingLoopsPing);
      if (interrupted.jobs.length)
        parts.push(
          // v1.354.0: the words the bell's own rows and the run page say.
          `${interrupted.jobs.length} task${interrupted.jobs.length === 1 ? "" : "s"} stopped when Iron Jarvis restarted`,
        );
      if (busyAsk) parts.push(`${busyAsk.running} background runs going at once`);
      const body = parts.join(" · ") || "Something needs your attention.";
      notify(`Iron Jarvis: ${count} pending`, body, () => setOpen(true));
    }
  }, [
    count,
    badge,
    reviews.length,
    pendingApprovals,
    waiting,
    agentAsks,
    chatAsks,
    chatAskTools,
    paneAsks,
    failingLoopsPing,
    interrupted.jobs.length,
    busyAsk,
    notify,
  ]);

  // Ping a desktop notification when a NEW activity event arrives. The event
  // buffer starts empty on load and /events only streams (never replays
  // history), so a page reload can't re-fire a backlog of old notifications.
  // (v1.351.0: the replayed "While you were away" rows are a separate list
  // and never ping; the newest LIVE row is read before the 6-row cut.)
  //
  // v1.360.0 (lane 10A, G03): this ping is the ONE desktop notice for a run's
  // outcome while a window is open — the daemon keeps "This PC" quiet for
  // exactly these rows (comm/notifier.bell_pings), and the desktop app says
  // the same rows itself while it sits in the tray. A failed workflow's row
  // that stands in for the row of the step that ENDED it, already pinged, is
  // not pinged again (v1.361.0: only THAT step's row — `standsInFor`).
  // A popped-out module's window holds its own bell (and its own events
  // socket): ONE window speaks for the app, so one outcome is never pinged
  // once per window — the main window while it is open, else (v1.361.0,
  // lane 11A) the one pop-out the desktop app names, whose tray watcher is
  // then quiet. A quiet window still RECORDS the row as pinged (the speaking
  // window pinged it), so if it becomes the one that speaks it never says a
  // workflow whose step was already said.
  const prevActivityId = useRef<string | null>(null);
  const pingedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const latest = allActivity[0];
    if (!latest || prevActivityId.current === latest.id) return;
    prevActivityId.current = latest.id;
    if (latest.quiet) return; // v1.305.0: a dropdown row, never a desktop ping
    if (latest.standsInFor?.some((id) => pingedRef.current.has(id))) return;
    pingedRef.current.add(latest.id);
    if (pingedRef.current.size > 200) pingedRef.current = new Set([...pingedRef.current].slice(-100));
    // v1.374.0 (IJ harness part 2, Phase 3): a Build turn that ended while
    // the user was LOOKING at that session (shown, in front, focused — in this
    // window or another of the app's, lib/buildWatching) says nothing: they
    // saw it end. Its row is kept, but seen (never counted on the badge).
    if (latest.build && lookingAt(latest.build)) {
      setSeenAttention((prev) => (prev.has(latest.id) ? prev : new Set(prev).add(latest.id)));
      noteWatched(latest.id); // ...and stays seen after a reload (review)
      return;
    }
    if (quietHere()) return;
    notify(latest.title, latest.body || "Open the dashboard for details.", () =>
      setOpen(true),
    );
  }, [allActivity, notify]);

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
        aria-label={badge ? `${badge} notifications` : "Notifications"}
        // v1.313.0: the bar's one control height (TitleBar's BAR_CONTROL_H) —
        // 36px tap target on a phone, 32px with a pointer, same radius.
        className={`relative grid h-9 w-9 shrink-0 place-items-center rounded-lg border transition-colors sm:h-8 sm:w-8 ${
          open
            ? "border-accent/40 bg-accent/[0.1] text-accent-soft"
            : "border-white/10 bg-white/[0.02] text-zinc-400 hover:border-white/20 hover:text-zinc-100"
        }`}
      >
        <Bell size={17} strokeWidth={2} />
        {badge > 0 && (
          <span className="absolute -right-1 -top-1 grid h-4 min-w-[1rem] place-items-center rounded-full bg-accent px-1 text-[10px] font-bold text-ink-950 shadow-glow-sm">
            {badge > 99 ? "99+" : badge}
          </span>
        )}
      </button>

      {open && (
        <div
          className="absolute right-0 z-50 mt-2 w-80 origin-top-right"
          // v1.369.0 review: a row that opens a Build session, pressed while
          // Build is already open, tells Build which session (the same-page
          // navigation alone changes only the address — lib/buildLinks).
          onClickCapture={(e) => {
            const a = (e.target as HTMLElement | null)?.closest?.("a");
            if (a) announceBuildSessionLink(a.getAttribute("href"));
          }}
        >
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

            {/* v1.352.0 re-review (G24, screen readers): what an answer given
                here did, ANNOUNCED. A live region has to be in the page BEFORE
                its words change; the answered row mounts with its words already
                in it, which many screen readers never read. This one is mounted
                with the dropdown and filled when an answer lands. */}
            <p role="status" data-testid="bell-answered-live" className="sr-only">
              {answeredRows.length > 0
                ? answeredRows[answeredRows.length - 1].outcome === "denied"
                  ? answeredLine("denied")
                  : `${answeredLine("approved")}.`
                : ""}
            </p>

            <div className="max-h-[22rem] overflow-y-auto">
              {count === 0 &&
              activity.length === 0 &&
              awayShown.length === 0 &&
              conflicts.length === 0 &&
              answeredRows.length === 0 ? (
                <div className="flex flex-col items-center gap-2 px-4 py-8 text-center">
                  <Inbox size={22} className="text-zinc-600" />
                  {pollError ? (
                    <div role="alert" className="text-sm text-tone-danger">
                      Could not load pending work: {pollError.message}
                    </div>
                  ) : (
                    <div className="text-sm text-zinc-500">You&apos;re all caught up.</div>
                  )}
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
                  {/* v1.352.0 (G24): what an answer given here did, for a
                      few seconds, where its row was. */}
                  {answeredRows.map(({ ask, outcome }) => (
                    <AgentAnsweredRow key={`answered:${ask.id}`} ask={ask} outcome={outcome} />
                  ))}
                  {agentAsks.map((ask) => (
                    <AgentApprovalRow
                      key={ask.id}
                      ask={ask}
                      onGone={handleAgentAskGone}
                      onAnswered={handleAgentAskAnswered}
                    />
                  ))}

                  {/* v1.351.0 (G03): a chat parked on its own card while
                      nobody looks — answered in the chat, so a link. */}
                  {chatAsks.map((ask) => (
                    <ChatWaitingRow key={ask.id} ask={ask} onOpen={() => setOpen(false)} />
                  ))}

                  {/* v1.353.0 (G52): a Build pane whose CLI waits on the
                      user — answered in the pane, so a link to it. */}
                  {paneAsks.map((pane) => (
                    <PaneWaitingRow key={`pane-${pane.id}`} pane={pane} onOpen={() => setOpen(false)} />
                  ))}

                  {/* v1.249.0 (R-02): jobs a restart cut off, with the Continue
                      the user had to go hunting for before. */}
                  {interrupted.jobs.map((job) => (
                    <InterruptedJobRow key={job.id} job={job} onGone={interrupted.forget} />
                  ))}

                  {/* v1.333.0: many background runs at once, no cap set:
                      the ask whether to start a queue. */}
                  {busyAsk && <BusyRunsRow ask={busyAsk} onGone={handleBusyGone} />}

                  {pendingApprovals > 0 && (
                    <li>
                      <Link
                        href="/computeruse"
                        onClick={() => setOpen(false)}
                        className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.04]"
                      >
                        <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-tone-warn/25 bg-tone-warn/[0.08] text-tone-warn">
                          <MonitorCog size={15} />
                        </span>
                        {/* v1.353.0 (G54): a queued website action, in
                            words. Approving it does not resume the step:
                            the agent's next identical call runs once. */}
                        <span className="min-w-0 flex-1">
                          <span
                            data-testid="bell-website-action"
                            className="block text-sm font-medium text-zinc-100"
                          >
                            {pendingApprovals === 1
                              ? "A website action is waiting for your OK"
                              : `${pendingApprovals} website actions are waiting for your OK`}
                          </span>
                          <span className="block text-[11px] text-zinc-500">
                            Approve it, then ask the agent to do that step again.
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
                      <p className="text-[11px] leading-snug text-tone-warn/90">
                        Couldn&apos;t answer &ldquo;{c.workflow}&rdquo;: {c.message}
                      </p>
                    </li>
                  ))}

                  {/* v1.229.0: background loops failing for 5+ min, one row
                      per loop. v1.355.0 (G32): named in the user's words
                      ("Slack messages aren't arriving"), how long, what keeps
                      working, and the row opens the page its fix lives on
                      (lib/loopWords; it used to open the chat home). The
                      engine name and the last error stay on the hover. */}
                  {failingLoops.map((l) => {
                    const w = loopWords(l.name);
                    return (
                      <li key={`loop-${l.name}`} data-testid="bell-loop-failing" data-loop={l.name}>
                        <Link
                          href={w.href}
                          title={`${l.name}: ${l.error}`}
                          onClick={() => {
                            setOpen(false);
                            if (w.href === STATUS_HREF)
                              window.dispatchEvent(new CustomEvent(EVERYTHING_TAB_EVENT, { detail: "status" }));
                          }}
                          className="flex items-center gap-3 px-4 py-3 transition-colors hover:bg-white/[0.04]"
                        >
                          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-tone-warn/25 bg-tone-warn/[0.08] text-tone-warn">
                            <ShieldAlert size={15} />
                          </span>
                          <span className="min-w-0 flex-1">
                            <span className="block text-sm font-medium text-zinc-100">{w.problem}</span>
                            <span className="block truncate text-[11px] text-zinc-500">
                              For {l.minutes} min{w.still ? ` · ${w.still}` : ""}
                            </span>
                          </span>
                          <ArrowRight size={13} className="shrink-0 text-zinc-600" />
                        </Link>
                      </li>
                    );
                  })}

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
                            {/* v1.359.0 (lane 9B): the run's id is the
                                hover, never the visible words. */}
                            <span
                              data-testid="bell-review-when"
                              title={e.session_id ? runLinkTitle("", e.session_id) : undefined}
                              className="mt-0.5 block font-mono text-[10px] text-zinc-600"
                            >
                              {clockTime(e.ts)}
                            </span>
                          </span>
                          <ArrowRight size={13} className="shrink-0 text-zinc-600" />
                        </Link>
                      </li>
                    );
                  })}

                  {/* Informational activity — same row pattern, neutral chrome,
                      never counted as pending (an unattended failure counts
                      on the badge only until the bell is opened, v1.351.0). */}
                  {activity.length > 0 && (
                    <li className="bg-white/[0.02] px-4 py-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-600">
                      Recent activity
                    </li>
                  )}
                  {activity.map((n) => (
                    <ActivityRow key={n.id} item={n} onOpen={() => setOpen(false)} />
                  ))}

                  {/* v1.351.0 (G04): what finished, failed or fell short
                      while no window was open — read once from the event
                      log, same rows, under its own quiet heading. */}
                  {awayShown.length > 0 && (
                    <li
                      data-testid="bell-away-heading"
                      className="bg-white/[0.02] px-4 py-1.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-zinc-600"
                    >
                      While you were away
                    </li>
                  )}
                  {awayShown.map((n) => (
                    <ActivityRow
                      key={`away-${n.id}`}
                      item={n}
                      testId="bell-away-row"
                      onOpen={() => setOpen(false)}
                    />
                  ))}
                </ul>
              )}
            </div>

            {count > 0 && (
              <footer className="border-t hairline px-4 py-2">
                {/* Point at the surface that actually holds the pending work:
                    only-parked-workflows pending → the Workflows page; only
                    paused agent asks → the Agents page (they came from a job). */}
                {/* v1.351.0: only waiting CHATS pending → the (first) chat,
                    never the Agents page that cannot answer them. */}
                <Link
                  href={
                    reviewish > 0
                      ? "/kanban"
                      : waiting.length > 0
                        ? "/workflows"
                        : agentAsks.length === 0 && chatAsks.length > 0
                          ? chatAskHref(chatAsks[0])
                          : agentAsks.length === 0 && paneAsks.length > 0
                            ? paneWaitingHref(paneAsks[0])
                            : "/agents"
                  }
                  onClick={() => setOpen(false)}
                  className="flex items-center justify-center gap-1.5 text-[11px] font-medium text-accent-soft transition-colors hover:text-accent"
                >
                  {reviewish > 0
                    ? "Open the review board"
                    : waiting.length > 0
                      ? "Open the Workflows page"
                      : agentAsks.length === 0 && chatAsks.length > 0
                        ? chatAsks[0].build
                          ? "Open the session in Build"
                          : "Open the chat"
                        : agentAsks.length === 0 && paneAsks.length > 0
                          ? "Open Build"
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
