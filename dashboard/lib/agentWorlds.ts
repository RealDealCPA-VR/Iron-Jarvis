// Project WORLDS on the Agents page (v1.304.0) — types and pure helpers.
//
// A world is one active project seen from the agents' side: its TEAM (the
// agents the user put on it), its round table (a project-bound thread), and
// what the project needs from the user. The landing page is a grid of worlds
// plus the "General" card (today's global rooms); `/agents?project=<id>`
// enters one.
//
// PURE ON PURPOSE. No React, no `lib/api` import: ~71 test files mock
// `lib/api` wholesale, and a helper that lived behind that mock would be a
// helper every one of those tests silently replaced.

/** Mirrors components/agents/identity.tsx — kept as a string union here so
 *  this module stays import-free. */
export type WorldAgentSource = "builtin" | "dynamic" | "remote";

/** One face on a team: a roster row (GET /projects/{id}/team) or the slim
 *  shape GET /agents/worlds carries. Every field but `name` is optional. */
export interface WorldMember {
  /** The roster WIRE name: "builder" | "custom:<slug>" | "remote:<name>". */
  name: string;
  /** The daemon says builtin | custom | remote (the roster says dynamic for
   *  custom); `memberSource` reads both. */
  kind?: WorldAgentSource | "custom" | string;
  description?: string;
  /** Serve path of a stored portrait (the roster's field). */
  avatar?: string | null;
  /** Same, under the name the worlds contract also allows. */
  portrait?: string | null;
  last_active?: string | null;
  healthy?: boolean;
  delegable?: boolean;
  paused?: boolean;
  activity?: string | null;
  /** Suggestions only: why Jarvis suggests this agent. */
  why?: string;
  /** What this member is shown at the table (remote agents: never the
   *  project's files or the other agents' replies). */
  sees?: string;
}

export interface WorldCounts {
  waiting: number;
  running: number;
  queued: number;
  done_7d: number;
}

export interface WorldProject {
  id: string;
  name: string;
  status?: string;
  color?: string | null;
}

/** One card on the worlds grid (GET /agents/worlds → worlds[]). */
export interface WorldCard {
  project: WorldProject;
  team: WorldMember[];
  thread_id: string | null;
  counts: WorldCounts;
}

export interface WorldsResponse {
  worlds: WorldCard[];
  general?: { thread_count?: number };
}

/** Something the project is waiting on the USER for. */
export interface WaitingItem {
  id: string;
  /** "ask" | "blocked" | "held" | "review" | … — shown, never switched on
   *  beyond a fallback link. */
  kind: string;
  title: string;
  agent?: string | null;
  since?: string | null;
  /** The dashboard path where it is resolved (the daemon's answer). */
  link?: string | null;
  reason?: string | null;
}

export interface CompletedItem {
  id?: string;
  title: string;
  agent?: string | null;
  finished_at?: string | null;
  outcome?: string | null;
  files?: string[];
  link?: string | null;
}

/** GET /projects/{id}/world. */
export interface WorldDetail {
  project: WorldProject & { root?: string };
  team: WorldMember[];
  thread_id: string | null;
  counts: WorldCounts;
  waiting: WaitingItem[];
  completed: CompletedItem[];
}

/** GET /projects/{id}/team. */
export interface TeamResponse {
  team: WorldMember[];
  suggestions: WorldMember[];
}

/* ------------------------------------------------------------- the URL --- */

/** Which world the URL asks for. `auto` = no say: the page decides (the grid
 *  when there are project worlds, else General). */
export type WorldRoute =
  | { kind: "auto" }
  /** `thread`: a room opened BY ID (`?thread=`) — the page asks the daemon
   *  whose room it is before deciding it is General. */
  | { kind: "general"; thread?: string }
  /** `thread`: a specific project room (an older one, from a deep link);
   *  absent = the project's own (most recent) room. */
  | { kind: "project"; id: string; thread?: string };

/** The query keys that already meant "the General room" before worlds
 *  existed — `?thread=` (palette), `?talk=`/`?ask=` (Help's Ask the Guide).
 *  They keep opening General, never the grid. */
const GENERAL_DEEP_LINKS = ["thread", "talk", "ask"] as const;

export function parseWorldRoute(search: string): WorldRoute {
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(search);
  } catch {
    return { kind: "auto" };
  }
  const project = (params.get("project") || "").trim();
  const thread = (params.get("thread") || "").trim();
  if (project) return thread ? { kind: "project", id: project, thread } : { kind: "project", id: project };
  if (thread) return { kind: "general", thread };
  if ((params.get("world") || "").trim() === "general") return { kind: "general" };
  for (const k of GENERAL_DEEP_LINKS) {
    if ((params.get(k) || "").trim()) return { kind: "general" };
  }
  return { kind: "auto" };
}

/** The shareable path for a world. `auto` is the bare page (the grid). */
export function worldPath(route: WorldRoute): string {
  if (route.kind === "project")
    return `/agents?project=${encodeURIComponent(route.id)}${
      route.thread ? `&thread=${encodeURIComponent(route.thread)}` : ""
    }`;
  if (route.kind === "general")
    return route.thread
      ? `/agents?thread=${encodeURIComponent(route.thread)}`
      : "/agents?world=general";
  return "/agents";
}

/* ---------------------------------------------------------- the shapes --- */

function count(v: unknown): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

/** A counts object the daemon may have sent partially (or not at all). */
export function normaliseCounts(c: Partial<WorldCounts> | null | undefined): WorldCounts {
  return {
    waiting: count(c?.waiting),
    running: count(c?.running),
    queued: count(c?.queued),
    done_7d: count(c?.done_7d),
  };
}

/** The worlds list, with rows that cannot be entered (no id) dropped. */
export function worldCards(resp: WorldsResponse | null | undefined): WorldCard[] {
  const rows = Array.isArray(resp?.worlds) ? resp!.worlds : [];
  return rows
    .filter((w) => w && w.project && typeof w.project.id === "string" && w.project.id)
    .map((w) => ({
      project: w.project,
      team: Array.isArray(w.team) ? w.team.filter((m) => m && typeof m.name === "string") : [],
      thread_id: typeof w.thread_id === "string" && w.thread_id ? w.thread_id : null,
      counts: normaliseCounts(w.counts),
    }));
}

/** The count line on a world card: "3 waiting on you · 1 running · 4 done
 *  this week". Zero parts are left out; an idle world says so. */
export function countsLine(c: WorldCounts): string {
  const parts: string[] = [];
  if (c.waiting > 0) parts.push(`${c.waiting} waiting on you`);
  if (c.running > 0) parts.push(`${c.running} running`);
  if (c.queued > 0) parts.push(`${c.queued} queued`);
  if (c.done_7d > 0) parts.push(`${c.done_7d} done this week`);
  return parts.length ? parts.join(" · ") : "Quiet — nothing running";
}

/** A thread row / GET /agents/threads/{id} → the project it belongs to, or
 *  "" for a General room (or an answer without the field: an older daemon). */
export function threadProjectId(row: unknown): string {
  if (!row || typeof row !== "object") return "";
  const v = (row as Record<string, unknown>).project_id;
  return typeof v === "string" ? v.trim() : "";
}

/** What a REMOTE agent at a project's table is shown — said where the team
 *  is chosen. The daemon's `sees` wins when it sends one. */
export const REMOTE_SEES =
  "Remote agents see only what you type here — never the project's files or the other agents' replies";

/* ----------------------------------------------------------- the team --- */

/** "custom:x" / "remote:x" → "x"; builtins pass through. */
export function bareMemberName(name: string): string {
  if (name.startsWith("custom:")) return name.slice("custom:".length);
  if (name.startsWith("remote:")) return name.slice("remote:".length);
  return name;
}

/** A member's source, from `kind` when the daemon sent it, else the prefix. */
export function memberSource(m: Pick<WorldMember, "name" | "kind">): WorldAgentSource {
  if (m.kind === "builtin" || m.kind === "dynamic" || m.kind === "remote") return m.kind;
  if (m.kind === "custom") return "dynamic";
  if (m.name.startsWith("custom:")) return "dynamic";
  if (m.name.startsWith("remote:")) return "remote";
  return "builtin";
}

/** The participant key the round table speaks ("<source>:<bare name>"). */
export function memberKey(m: Pick<WorldMember, "name" | "kind">): string {
  return `${memberSource(m)}:${bareMemberName(m.name)}`;
}

/** A member's stored portrait path, under either field name. */
export function memberAvatar(m: WorldMember): string | null {
  return m.avatar || m.portrait || null;
}

/** Builtins the daemon refuses as an assignee (mirrors AgentInbox's set —
 *  `assignments.store.resolve_assignee`: supervisor + delegate-carrying). */
const COORDINATORS = new Set(["supervisor", "planner"]);

/**
 * "New task" assignee options for a world: ONLY the team, and only the
 * members the queue accepts (no remotes, no coordinators). The "" option —
 * "Whole team — Jarvis decides" — is the caller's, and means NO assignee:
 * the plain project task flow.
 */
export function teamAssigneeChoices(
  team: WorldMember[],
): Array<{ value: string; label: string }> {
  const out: Array<{ value: string; label: string }> = [];
  const seen = new Set<string>();
  for (const m of team) {
    if (!m || typeof m.name !== "string" || !m.name) continue;
    const src = memberSource(m);
    if (src === "remote") continue;
    const wire = src === "dynamic" ? `custom:${bareMemberName(m.name)}` : m.name;
    if (src === "builtin" && COORDINATORS.has(wire)) continue;
    if (seen.has(wire)) continue;
    seen.add(wire);
    out.push({
      value: wire,
      label: src === "dynamic" ? `${bareMemberName(wire)} — yours` : wire,
    });
  }
  return out;
}

/** The wire names a PUT /projects/{id}/team sends, de-duplicated in order. */
export function teamWireNames(team: WorldMember[]): string[] {
  const out: string[] = [];
  for (const m of team) {
    if (m && typeof m.name === "string" && m.name && !out.includes(m.name)) out.push(m.name);
  }
  return out;
}

/** POST /projects/{id}/world/room's answer ({thread_id, thread}) → the room
 *  id. `thread_id` is the contract; `id`/`thread.id` are read defensively. */
export function roomIdOf(resp: unknown): string | null {
  if (!resp || typeof resp !== "object") return null;
  const r = resp as Record<string, unknown>;
  if (typeof r.thread_id === "string" && r.thread_id) return r.thread_id;
  if (typeof r.id === "string" && r.id) return r.id;
  const t = r.thread as Record<string, unknown> | undefined;
  if (t && typeof t.id === "string" && t.id) return t.id;
  return null;
}

/* --------------------------------------------------- waiting / completed --- */

/** Where a waiting item is resolved. The daemon's `link` wins; a missing one
 *  falls back to the project's own page (never a dead link). Only same-app
 *  paths are followed — a link must start with one "/". */
export function waitingHref(item: WaitingItem, projectId: string): string {
  const link = typeof item.link === "string" ? item.link.trim() : "";
  if (link.startsWith("/") && !link.startsWith("//")) return link;
  return `/projects/${encodeURIComponent(projectId)}`;
}

/** Same rule for a completed item. */
export function completedHref(item: CompletedItem, projectId: string): string {
  const link = typeof item.link === "string" ? item.link.trim() : "";
  if (link.startsWith("/") && !link.startsWith("//")) return link;
  return `/projects/${encodeURIComponent(projectId)}`;
}

/** The plain words for a waiting item's kind. */
export function waitingKindLabel(kind: string): string {
  switch (kind) {
    case "ask":
      return "Asked you";
    case "blocked":
      return "Blocked";
    case "held":
      return "On hold";
    case "review":
      return "Needs review";
    case "needs_you":
      return "Stopped — needs your answer";
    case "interrupted":
      return "Cut off by a restart — Continue?";
    default:
      return kind ? kind.charAt(0).toUpperCase() + kind.slice(1) : "Waiting";
  }
}
