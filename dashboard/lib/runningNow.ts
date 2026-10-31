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
// Pure: no React, no fetch. Kept out of lib/api.ts (tests mock it wholesale).

import { originLabel } from "@/components/sessions/OriginChip";
import { agentLabel } from "./agentWorlds";
import { buildCodeHref, toolLabel } from "./buildApp";
import { buildSessionHref } from "./buildLinks";
import { CODING_SESSION_LABEL } from "./buildWords";
import { normalizeIso } from "./format";
import { sessionHref } from "./missionLinks";
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
}

export interface RunningItem {
  key: string;
  kind: "session" | "chat" | "build" | "build_app";
  href: string;
  /** Who or what: "Schedule · nightly-brief", "Mission", "Builder", "Chat". */
  who: string;
  /** How long, or its state: "3 min", "Queued", "Waiting on you". */
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
  if (queued) parts.push("Queued");
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

/** A Build coding session answering (harness gaps, fix 1): "Coding session ·
 *  12 min", its title under it, opened on that session in Build. */
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
    who: CODING_SESSION_LABEL,
    when,
    detail: oneLine(title) || `Untitled ${CODING_SESSION_LABEL.toLowerCase()}`,
    line: when ? `${CODING_SESSION_LABEL} · ${when}` : CODING_SESSION_LABEL,
    title: title || CODING_SESSION_LABEL,
    waiting,
    queued: false,
  };
}

/** v1.414.0: a Build › Code reply (the user's own Claude Code, Codex, Pi or
 *  OpenCode): "Coding session · Claude Code · 12 min", its title under it,
 *  opened on that session in Build › Code. */
function buildAppItem(r: RunningReplyRow, now: number): RunningItem {
  const session = String(r.thread_id ?? "").trim();
  const waiting = r.waiting === true;
  const started = startedMs(r.started_at);
  const parts: string[] = [];
  if (started != null) parts.push(elapsedWords(now - started));
  if (waiting) parts.push(statusWords("waiting"));
  const when = parts.filter(Boolean).join(" · ");
  const h = String(r.harness ?? "").trim();
  const tool = h ? toolLabel(h) : "";
  const who = tool ? `${CODING_SESSION_LABEL} · ${tool}` : CODING_SESSION_LABEL;
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

/**
 * Everything running right now, most urgent first: what waits on the user,
 * then what runs (newest start first, chats after runs, coding sessions
 * after chats), then what is queued. `sessions` / `threads` / `replies` may
 * be null (not read yet) and read as empty.
 */
export function runningItems(
  sessions: RunningSessionRow[] | null | undefined,
  threads: ThreadSummary[] | null | undefined,
  now: number = Date.now(),
  replies: RunningReplyRow[] | null | undefined = null,
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
  const builds = [...buildReplyItems(replies, now), ...buildAppReplyItems(replies, now)];
  const waiting = [
    ...runs.filter((i) => i.waiting),
    ...chats.filter((i) => i.waiting),
    ...builds.filter((i) => i.waiting),
  ];
  const running = [
    ...runs.filter((i) => !i.waiting && !i.queued),
    ...chats.filter((i) => !i.waiting),
    ...builds.filter((i) => !i.waiting),
  ];
  const queued = runs.filter((i) => i.queued);
  return [...waiting, ...running, ...queued];
}
