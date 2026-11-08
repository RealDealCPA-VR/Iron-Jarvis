// RUNNING NOW (v1.352.0, G26): what is running, in words, one click away.
//
// Background work used to show only as a pulsing orb whose hover said
// "Thinking — a session is in flight", beside a footer that read "Running"
// (meaning the APP was up), and Status said "Working on 2 tasks" without
// which. The orb (and Status's Running number) now open ONE list built here
// from data the daemon already serves — no new route:
//
//   * GET /sessions rows whose status is `active` or `queued`, each worded by
//     the ONE origin vocabulary (`OriginChip.originLabel`: "Schedule ·
//     nightly-brief", "Mission", …) with its elapsed time, and opened where
//     every other list opens a run (`missionLinks.sessionHref`: a mission on
//     its mission screen, anything else on its session page);
//   * GET /chat/threads rows the daemon marks `running` or `waiting`, worded
//     by `threadStatus.statusWords`, opened at `/chat?thread=<id>`;
//   * (harness gaps, fix 1) GET /chat/turns/running rows whose surface is
//     "build": a Build coding session answering, which has no Session row
//     and is left out of the chat list on purpose — named by its session's
//     title, opened on that session in Build (`buildLinks.buildSessionHref`).
//     The route is the durable turn registry's ONE truth for "which reply is
//     running"; chats keep coming from the chat list (the same registry's
//     per-chat flags), so a chat is never listed twice.
//
// An origin the vocabulary has no words for (a chat's escalation stamps
// "chat") is not guessed at: the row names the agent that runs it instead
// (`agentWorlds.agentLabel`), and the raw origin stays in the row's `title`.
//
//   * (v1.449.0, plan 0h.5 item 13) three more kinds of work the daemon
//     already lists and the orb never showed: WORKFLOW runs (GET
//     /workflows/runs, the live statuses: running, resuming, waiting,
//     cancelling — the ONE status table's words, opened on /workflows), MEDIA
//     jobs (GET /media/jobs, queued or running — opened on their chat, else
//     the Studio) and NIGHT TASKS (GET /build/night, state running — opened
//     on their coding session). Each is one row in the same shape. An older
//     Iron Jarvis without a route lists none of that kind, as before.
//
// Pure: no React, no fetch. Kept out of lib/api.ts (tests mock it wholesale).

import { originLabel } from "@/components/sessions/OriginChip";
import { agentLabel } from "./agentWorlds";
import { buildCodeHref, toolLabel } from "./buildApp";
import { buildSessionHref, CODE_VIEW_LINK } from "./buildLinks";
import { CODING_SESSION_JARVIS, CODING_SESSION_LABEL, codingSessionWith } from "./buildWords";
import { normalizeIso } from "./format";
import { sessionHref } from "./missionLinks";
import { statusWords as tableWords } from "./statusWords";
import { statusWords, type ThreadSummary } from "./threadStatus";

/** The /sessions read the list makes (newest first; an active run older than
 *  this window is rare — a mission's teammates are rows too). */
export const RUNNING_SESSIONS_PATH = "/sessions?limit=100";
/** The running replies the list reads (harness gaps, fix 1): a Build coding
 *  session answering is listed from here. In memory on the daemon. */
export const RUNNING_REPLIES_PATH = "/chat/turns/running";
/** How often the open list re-reads the sessions (ms). Only while open. */
export const RUNNING_POLL_MS = 10_000;
/** How often the open list re-words its elapsed times (ms). */
export const RUNNING_TICK_MS = 15_000;
/** At most this many rows; the rest are counted. */
export const MAX_RUNNING_ROWS = 8;

/** v1.449.0 (plan 0h.5 item 13): the other three lists the orb reads, each
 *  the daemon's own route. Slim workflow runs (no step blobs); the newest
 *  media jobs; tonight's tasks. A 404 (an older daemon) or a 503 (media not
 *  available) lists none of that kind. */
export const RUNNING_WORKFLOWS_PATH = "/workflows/runs?limit=50&slim=true";
export const RUNNING_MEDIA_PATH = "/media/jobs?limit=50";
export const RUNNING_NIGHT_PATH = "/build/night";
/** Workflow run statuses that are still live (workflows/engine + the Stop
 *  press's own `cancelling`): everything in `WORKFLOW_RUN_TERMINAL` is not. */
export const LIVE_WORKFLOW_STATUSES: ReadonlySet<string> = new Set(["running", "resuming", "waiting", "cancelling"]);
/** Media job statuses still in flight (media/models.LIVE_STATUSES). */
export const LIVE_MEDIA_STATUSES: ReadonlySet<string> = new Set(["queued", "running"]);

/** Session statuses that are still running work (models.SessionStatus). */
export const RUNNING_STATUSES: ReadonlySet<string> = new Set(["active", "queued"]);

/** The fields of a GET /sessions row this module reads. */
export interface RunningSessionRow {
  id: string;
  status?: string | null;
  task?: string | null;
  origin?: string | null;
  project_id?: string | null;
  agent_type?: string | null;
  agent_name?: string | null;
  created_at?: string | null;
  waiting_on?: unknown;
}

/** One row of GET /chat/turns/running (durable_turns.running_replies). */
export interface RunningReplyRow {
  turn_id: string;
  thread_id?: string | null;
  /** "chat" | "build" | (v1.414.0) "build_app" — Build › Code, the user's
   *  own coding tool. */
  surface?: string | null;
  /** v1.414.0: a Build › Code reply's tool ("claude-code", "codex"…). */
  harness?: string | null;
  /** When the reply started (ISO, UTC). */
  started_at?: string | null;
  /** Parked on the user (an approval card, a question). */
  waiting?: boolean | null;
  /** The chat's or the coding session's title. */
  title?: string | null;
  /** v1.417.0: a Build › Code session whose reply ENDED while its helpers
   *  work on in the background (or the tool writes a report of its own). */
  background?: boolean | null;
  /** v1.417.0: how many helper agents are still at work (0 = the tool is
   *  writing its own report). */
  helpers?: number | null;
}

/** The fields of a GET /workflows/runs row this module reads (lib/types
 *  WorkflowRun; the slim read drops the step blobs). */
export interface RunningWorkflowRow {
  id?: string | number | null;
  workflow_name?: string | null;
  status?: string | null;
  started_at?: string | null;
  created_at?: string | null;
}

/** The fields of a GET /media/jobs row this module reads (media/jobs
 *  `_view`): never a key, the user's own prompt cut to one line. */
export interface RunningMediaRow {
  id?: string | null;
  kind?: string | null;
  status?: string | null;
  prompt?: string | null;
  model?: string | null;
  thread_id?: string | null;
  created_at?: string | null;
}

/** The fields of a GET /build/night task this module reads (lib/nightShift
 *  decodes the whole row; the orb needs only these). */
export interface RunningNightRow {
  id?: string | null;
  words?: string | null;
  folder?: string | null;
  state?: string | null;
  thread_id?: string | null;
  started_at?: string | null;
}

/** v1.449.0: the three further lists `runningItems` folds in. Each may be
 *  null (not read, or the daemon has no such route) and reads as empty. */
export interface RunningExtra {
  workflows?: RunningWorkflowRow[] | null;
  media?: RunningMediaRow[] | null;
  night?: RunningNightRow[] | null;
}

export interface RunningItem {
  key: string;
  kind: "session" | "chat" | "build" | "build_app" | "workflow" | "media" | "night";
  href: string;
  /** Who or what: "Schedule · nightly-brief", "Mission", "Builder", "Chat". */
  who: string;
  /** How long, or its state: "3 min", "Queued", "Waiting for you". */
  when: string;
  /** The task or the chat's title, one line. */
  detail: string;
  /** The first line read aloud and shown: `${who} · ${when}`. */
  line: string;
  /** The raw record (origin, id) for the row's tooltip. */
  title: string;
  waiting: boolean;
  queued: boolean;
}

/** Elapsed time in words, minute-grained (a list re-worded every few seconds
 *  must not flicker seconds): "under a minute", "3 min", "1 h", "2 h 5 min".
 *  "" for a value that is not a finite number. A clock running behind the
 *  daemon's (negative) reads "under a minute". */
export function elapsedWords(ms: number): string {
  if (typeof ms !== "number" || !Number.isFinite(ms)) return "";
  const min = Math.floor(Math.max(0, ms) / 60_000);
  if (min < 1) return "under a minute";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const rest = min % 60;
  return rest ? `${h} h ${rest} min` : `${h} h`;
}

/** When a session started, in ms (the daemon stores naive UTC). */
function startedMs(iso: string | null | undefined): number | null {
  if (!iso) return null;
  const t = new Date(normalizeIso(iso)).getTime();
  return Number.isNaN(t) ? null : t;
}

/** One line of a task: the first non-blank line, whitespace folded, cut. */
function oneLine(text: string | null | undefined, max = 140): string {
  const first = String(text ?? "")
    .split(/\r?\n/)
    .map((l) => l.trim())
    .find((l) => l.length > 0);
  const s = (first ?? "").replace(/\s+/g, " ");
  return s.length > max ? `${s.slice(0, max - 1).trimEnd()}…` : s;
}

/** Who a running session is, in words: the origin's words when the shared
 *  vocabulary has them, else the agent that runs it. */
export function sessionWho(row: RunningSessionRow): string {
  const origin = (row.origin ?? "").trim();
  const words = originLabel(origin);
  if (words && words !== origin) return words;
  const type = (row.agent_type ?? "").trim();
  const name = (row.agent_name ?? "").trim();
  const agent = name || type;
  if (agent) return agentLabel(agent, { builtin: !name || name === type, origin });
  // Nothing better known: the raw origin (never a guess), else "A run".
  return words || "A run";
}

function sessionItem(row: RunningSessionRow, now: number): RunningItem {
  const who = sessionWho(row);
  const queued = row.status === "queued";
  const waiting = row.waiting_on != null && row.waiting_on !== false;
  const started = startedMs(row.created_at);
  const parts: string[] = [];
  if (queued) parts.push(tableWords("queued").word);
  else if (started != null) parts.push(elapsedWords(now - started));
  if (waiting) parts.push(statusWords("waiting"));
  const when = parts.filter(Boolean).join(" · ");
  const origin = (row.origin ?? "").trim();
  return {
    key: `s:${row.id}`,
    kind: "session",
    href: sessionHref({ id: row.id, origin: row.origin ?? null, project_id: row.project_id ?? null }),
    who,
    when,
    detail: oneLine(row.task) || "No task text",
    line: when ? `${who} · ${when}` : who,
    title: origin ? `Started by: ${origin}` : `Run ${row.id}`,
    waiting,
    queued: queued && !waiting,
  };
}

function chatItem(t: ThreadSummary): RunningItem {
  const waiting = t.waiting === true;
  const when = statusWords(waiting ? "waiting" : "running");
  const title = (t.title || "").trim() || "Untitled chat";
  return {
    key: `c:${t.id}`,
    kind: "chat",
    href: `/chat?thread=${encodeURIComponent(t.id)}`,
    who: "Chat",
    when,
    detail: oneLine(title),
    line: `Chat · ${when}`,
    title,
    waiting,
    queued: false,
  };
}

/** A Chat › Code coding session answering (harness gaps, fix 1): "Coding
 *  session (Jarvis) · 12 min" (v1.420.0, C5.1: the tool in brackets tells it
 *  from a Build › Code session), its title under it, opened on that session. */
function buildItem(r: RunningReplyRow, now: number): RunningItem {
  const thread = String(r.thread_id ?? "").trim();
  const waiting = r.waiting === true;
  const started = startedMs(r.started_at);
  const parts: string[] = [];
  if (started != null) parts.push(elapsedWords(now - started));
  if (waiting) parts.push(statusWords("waiting"));
  const when = parts.filter(Boolean).join(" · ");
  const title = (r.title || "").trim();
  return {
    key: `b:${r.turn_id}`,
    kind: "build",
    href: buildSessionHref(thread),
    who: CODING_SESSION_JARVIS,
    when,
    detail: oneLine(title) || `Untitled ${CODING_SESSION_LABEL.toLowerCase()}`,
    line: when ? `${CODING_SESSION_JARVIS} · ${when}` : CODING_SESSION_JARVIS,
    title: title || CODING_SESSION_JARVIS,
    waiting,
    queued: false,
  };
}

/** v1.414.0: a Build › Code reply (the user's own Claude Code, Codex, Pi or
 *  OpenCode): "Coding session (Claude Code) · 12 min" (v1.420.0, C5.1: the
 *  tool in brackets, the same shape as Chat › Code's "(Jarvis)"), its title
 *  under it, opened on that session in Build › Code. An unknown tool is not
 *  guessed at: the label alone, with no tool in brackets. */
function buildAppItem(r: RunningReplyRow, now: number): RunningItem {
  const session = String(r.thread_id ?? "").trim();
  const waiting = r.waiting === true;
  const started = startedMs(r.started_at);
  const parts: string[] = [];
  if (r.background === true) {
    // v1.417.0: no reply runs; its helpers do (or the tool replies on its
    // own — Claude Code's report, Pi's own run: "report" is not true of both).
    parts.push(typeof r.helpers === "number" && r.helpers > 0 ? "helpers working" : "replying on its own");
  } else if (started != null) parts.push(elapsedWords(now - started));
  if (waiting) parts.push(statusWords("waiting"));
  const when = parts.filter(Boolean).join(" · ");
  const h = String(r.harness ?? "").trim();
  const tool = h ? toolLabel(h) : "";
  const who = tool ? codingSessionWith(tool) : CODING_SESSION_LABEL;
  const title = (r.title || "").trim();
  return {
    key: `a:${r.turn_id}`,
    kind: "build_app",
    href: buildCodeHref(session),
    who,
    when,
    detail: oneLine(title) || `Untitled ${CODING_SESSION_LABEL.toLowerCase()}`,
    line: when ? `${who} · ${when}` : who,
    title: title || who,
    waiting,
    queued: false,
  };
}

/** v1.414.0: the Build › Code rows of GET /chat/turns/running, newest start
 *  first (`surface: "build_app"`, each naming its session). */
export function buildAppReplyItems(
  replies: RunningReplyRow[] | null | undefined,
  now: number = Date.now(),
): RunningItem[] {
  return (replies ?? [])
    .filter(
      (r) => r && typeof r.turn_id === "string" && r.surface === "build_app" && String(r.thread_id ?? "").trim() !== "",
    )
    .sort((a, b) => (startedMs(b.started_at) ?? 0) - (startedMs(a.started_at) ?? 0))
    .map((r) => buildAppItem(r, now));
}

/** The Build rows of GET /chat/turns/running, newest start first: only a
 *  coding session's reply that names its session (a chat's comes from the
 *  chat list, so it is never listed twice). */
export function buildReplyItems(
  replies: RunningReplyRow[] | null | undefined,
  now: number = Date.now(),
): RunningItem[] {
  return (replies ?? [])
    .filter(
      (r) => r && typeof r.turn_id === "string" && r.surface === "build" && String(r.thread_id ?? "").trim() !== "",
    )
    .sort((a, b) => (startedMs(b.started_at) ?? 0) - (startedMs(a.started_at) ?? 0))
    .map((r) => buildItem(r, now));
}

/** The last part of a folder path, for a task with no words of its own. */
function folderLeaf(path: string): string {
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

/** v1.449.0 (plan 0h.5 item 13): a workflow run that is still live —
 *  "Workflow · 3 min" with the workflow's name under it, "Waiting for you"
 *  (amber, first) for a run parked on the user, "Stopping" after the Stop
 *  press, in the ONE status table's words; opened on the Workflows page,
 *  where its row has the answer box and the Stop. */
function workflowItem(r: RunningWorkflowRow, now: number): RunningItem {
  const id = String(r.id ?? "").trim();
  const status = String(r.status ?? "").trim();
  const waiting = status === "waiting";
  const started = startedMs(r.started_at ?? r.created_at);
  const parts: string[] = [];
  if (started != null) parts.push(elapsedWords(now - started));
  if (status !== "running") parts.push(tableWords(status).word);
  const when = parts.filter(Boolean).join(" · ");
  const name = oneLine(r.workflow_name) || "Untitled workflow";
  return {
    key: `w:${id}`,
    kind: "workflow",
    href: "/workflows",
    who: "Workflow",
    when,
    detail: name,
    line: when ? `Workflow · ${when}` : "Workflow",
    title: `Workflow run ${id}`,
    waiting,
    queued: false,
  };
}

/** v1.449.0: a media job in flight — "Making an image · 3 min" (the job's
 *  kind in words), "Queued" while it waits for its turn, the prompt under
 *  it; opened on the chat it came from, else the Studio. */
function mediaItem(r: RunningMediaRow, now: number): RunningItem {
  const id = String(r.id ?? "").trim();
  const status = String(r.status ?? "").trim();
  const queued = status === "queued";
  const started = startedMs(r.created_at);
  const kind = String(r.kind ?? "").trim().toLowerCase();
  const who = kind ? `Making ${/^[aeiou]/.test(kind) ? "an" : "a"} ${kind}` : "Making media";
  const parts: string[] = [];
  if (queued) parts.push(tableWords("queued").word);
  else if (started != null) parts.push(elapsedWords(now - started));
  const when = parts.filter(Boolean).join(" · ");
  const thread = String(r.thread_id ?? "").trim();
  const detail = oneLine(r.prompt) || oneLine(r.model) || "No prompt text";
  return {
    key: `m:${id}`,
    kind: "media",
    href: thread ? `/chat?thread=${encodeURIComponent(thread)}` : "/creative",
    who,
    when,
    detail,
    line: when ? `${who} · ${when}` : who,
    title: `Media task ${id}`,
    waiting: false,
    queued,
  };
}

/** v1.449.0: a night task at work — "Night task · 12 min", its words (else
 *  its folder's name) under it; opened on its coding session, else the Code
 *  view. Only a RUNNING task is listed: a queued one waits for the night
 *  window, which Tonight's own list says. */
function nightItem(r: RunningNightRow, now: number): RunningItem {
  const id = String(r.id ?? "").trim();
  const started = startedMs(r.started_at);
  const when = started != null ? elapsedWords(now - started) : "";
  const thread = String(r.thread_id ?? "").trim();
  const folder = String(r.folder ?? "").trim();
  const detail = oneLine(r.words) || (folder ? folderLeaf(folder) : "") || "No task text";
  return {
    key: `n:${id}`,
    kind: "night",
    href: thread ? buildSessionHref(thread) : CODE_VIEW_LINK,
    who: "Night task",
    when,
    detail,
    line: when ? `Night task · ${when}` : "Night task",
    title: folder ? `Night task in ${folder}` : `Night task ${id}`,
    waiting: false,
    queued: false,
  };
}

/** v1.449.0: the live workflow runs, newest start first. */
export function workflowRunItems(rows: RunningWorkflowRow[] | null | undefined, now: number = Date.now()): RunningItem[] {
  return (rows ?? [])
    .filter((r) => r && String(r.id ?? "").trim() !== "" && LIVE_WORKFLOW_STATUSES.has(String(r.status ?? "")))
    .sort((a, b) => (startedMs(b.started_at ?? b.created_at) ?? 0) - (startedMs(a.started_at ?? a.created_at) ?? 0))
    .map((r) => workflowItem(r, now));
}

/** v1.449.0: the media jobs in flight, newest first. */
export function mediaJobItems(rows: RunningMediaRow[] | null | undefined, now: number = Date.now()): RunningItem[] {
  return (rows ?? [])
    .filter((r) => r && String(r.id ?? "").trim() !== "" && LIVE_MEDIA_STATUSES.has(String(r.status ?? "")))
    .sort((a, b) => (startedMs(b.created_at) ?? 0) - (startedMs(a.created_at) ?? 0))
    .map((r) => mediaItem(r, now));
}

/** v1.449.0: the night tasks at work, newest start first. */
export function nightTaskItems(rows: RunningNightRow[] | null | undefined, now: number = Date.now()): RunningItem[] {
  return (rows ?? [])
    .filter((r) => r && String(r.id ?? "").trim() !== "" && String(r.state ?? "") === "running")
    .sort((a, b) => (startedMs(b.started_at) ?? 0) - (startedMs(a.started_at) ?? 0))
    .map((r) => nightItem(r, now));
}

/**
 * Everything running right now, most urgent first: what waits on the user,
 * then what runs (newest start first, chats after runs, coding sessions
 * after chats, then workflow runs, media jobs and night tasks), then what is
 * queued (runs, then media jobs). `sessions` / `threads` / `replies` and
 * every list in `extra` may be null (not read yet) and read as empty.
 */
export function runningItems(
  sessions: RunningSessionRow[] | null | undefined,
  threads: ThreadSummary[] | null | undefined,
  now: number = Date.now(),
  replies: RunningReplyRow[] | null | undefined = null,
  extra: RunningExtra | null | undefined = null,
): RunningItem[] {
  const live = (sessions ?? []).filter(
    (r) => r && typeof r.id === "string" && RUNNING_STATUSES.has(String(r.status ?? "")),
  );
  // Newest start first within each group.
  const byStart = [...live].sort((a, b) => (startedMs(b.created_at) ?? 0) - (startedMs(a.created_at) ?? 0));
  const runs = byStart.map((r) => sessionItem(r, now));
  const chats = (threads ?? [])
    .filter((t) => t && typeof t.id === "string" && (t.running === true || t.waiting === true))
    .map(chatItem);
  const flows = workflowRunItems(extra?.workflows, now);
  const media = mediaJobItems(extra?.media, now);
  const night = nightTaskItems(extra?.night, now);
  // (v1.449.0 review) a night task's reply IS a coding session's running
  // turn, which the replies list carries too: one piece of work, one row —
  // the night row, which says what it is. Matched by the session both open.
  const nightHrefs = new Set(night.map((i) => i.href));
  const builds = [...buildReplyItems(replies, now), ...buildAppReplyItems(replies, now)].filter(
    (i) => !(i.kind === "build" && nightHrefs.has(i.href)),
  );
  const waiting = [
    ...runs.filter((i) => i.waiting),
    ...chats.filter((i) => i.waiting),
    ...builds.filter((i) => i.waiting),
    ...flows.filter((i) => i.waiting),
  ];
  const running = [
    ...runs.filter((i) => !i.waiting && !i.queued),
    ...chats.filter((i) => !i.waiting),
    ...builds.filter((i) => !i.waiting),
    ...flows.filter((i) => !i.waiting),
    ...media.filter((i) => !i.queued),
    ...night,
  ];
  const queued = [...runs.filter((i) => i.queued), ...media.filter((i) => i.queued)];
  return [...waiting, ...running, ...queued];
}
