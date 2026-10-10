// THE MISSION VIEW (v1.307.0) — shapes, decoders and pure helpers for the
// Agents page's mission layout: one objective, Jarvis runs the team, the
// deliverable first and the workforce underneath it.
//
// Lives OUTSIDE lib/api.ts on purpose: ~71 test files mock that module
// wholesale, and a decoder imported from it would read as "failed" under every
// one of those mocks (the lib/etag.ts / lib/preferences.ts rule).
//
// Every decoder is a WHITELIST: a field the daemon did not send reads as its
// honest empty value, never as a guess. Progress in particular keeps the
// daemon's `pct: null` — "no count behind this bar" — instead of inventing one.

export type MemberStatus =
  | "queued"
  | "working"
  | "waiting_you"
  | "done"
  | "failed"
  | "cancelled";

export interface MissionProgress {
  /** 0–100, or null when nothing COUNTS this member's progress. */
  pct: number | null;
  label: string;
  /** done | queued | plan | steps | waiting | failed | cancelled */
  basis: string;
}

export interface WaitingOn {
  approval_id: string;
  tool: string;
}

export interface MissionMember {
  session_id: string | null;
  agent: string;
  name: string;
  kind: "builtin" | "custom" | "remote";
  task: string;
  status: MemberStatus;
  progress: MissionProgress;
  activity: string;
  waiting_on: WaitingOn | null;
  steps: number;
  result: string;
  files: string[];
  started_at: string | null;
  finished_at: string | null;
  /** v1.309.0: the provider / model this teammate's run asked for ("" when
   *  the daemon did not say — an older daemon, or the default route). */
  provider: string;
  model: string;
}

export type ActivityTone = "ok" | "warn" | "info" | "ask";

export interface MissionActivity {
  at: string | null;
  who: string;
  session_id: string | null;
  text: string;
  tone: ActivityTone;
}

export interface WorklistSummary {
  total: number;
  done: number;
  failed: number;
  pending: number;
  doing: number;
}

export interface MissionView {
  session: {
    id: string;
    /** The MODEL-FACING task (a continuation's carries the recap). */
    task: string;
    /** v1.309.0: the user's OWN words — a continuation's follow-up, not the
     *  recap wrapped around it. Falls back to `task` on an older daemon. */
    objective: string;
    status: string;
    outcome: string | null;
    project_id: string | null;
    created_at: string | null;
    finished_at: string | null;
    /** v1.309.0: the app stopped (an update, a crash) while this ran. */
    interrupted: boolean;
    /** v1.309.0: the newest session continuing this one, or null. */
    continued_as: string | null;
    provider: string;
    model: string;
    /** v1.309.0: one plain sentence when a failover / downgrade touched this
     *  mission, else "". The daemon words it; the page only shows it. */
    route_note: string;
  };
  coordinator: { name: string; status: MemberStatus; waiting_on: WaitingOn | null; steps: number };
  members: MissionMember[];
  progress: { done: number; total: number };
  activity: MissionActivity[];
  deliverable: { text: string; documents: string[]; worklist: WorklistSummary | null };
}

export interface MissionRow {
  id: string;
  objective: string;
  status: string;
  /** v1.309.0: the mission or one of its teammates is parked on an ask. */
  waiting: boolean;
  outcome: string | null;
  project_id: string | null;
  created_at: string | null;
  finished_at: string | null;
}

export const MISSION_TERMINAL = new Set(["completed", "failed", "cancelled"]);
const STATUSES: MemberStatus[] = ["queued", "working", "waiting_you", "done", "failed", "cancelled"];
const TONES: ActivityTone[] = ["ok", "warn", "info", "ask"];

type Obj = Record<string, unknown>;

function obj(v: unknown): Obj {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Obj) : {};
}
function str(v: unknown): string {
  return typeof v === "string" ? v : "";
}
function strOrNull(v: unknown): string | null {
  return typeof v === "string" && v ? v : null;
}
function num(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}
function strList(v: unknown): string[] {
  return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string" && x !== "") : [];
}
function status(v: unknown): MemberStatus {
  return STATUSES.includes(v as MemberStatus) ? (v as MemberStatus) : "queued";
}
function waiting(v: unknown): WaitingOn | null {
  const o = obj(v);
  const id = str(o.approval_id);
  return id ? { approval_id: id, tool: str(o.tool) } : null;
}

export function decodeProgress(v: unknown): MissionProgress {
  const o = obj(v);
  const raw = o.pct;
  const pct =
    typeof raw === "number" && Number.isFinite(raw) ? Math.max(0, Math.min(100, Math.round(raw))) : null;
  return { pct, label: str(o.label), basis: str(o.basis) };
}

function decodeMember(v: unknown): MissionMember | null {
  const o = obj(v);
  const agent = str(o.agent);
  if (!agent) return null;
  const kind = o.kind === "custom" || o.kind === "remote" ? o.kind : "builtin";
  return {
    session_id: strOrNull(o.session_id),
    agent,
    name: str(o.name) || agent,
    kind,
    task: str(o.task),
    status: status(o.status),
    progress: decodeProgress(o.progress),
    activity: str(o.activity),
    waiting_on: waiting(o.waiting_on),
    steps: num(o.steps),
    result: str(o.result),
    files: strList(o.files),
    started_at: strOrNull(o.started_at),
    finished_at: strOrNull(o.finished_at),
    provider: str(o.provider),
    model: str(o.model),
  };
}

function decodeActivity(v: unknown): MissionActivity | null {
  const o = obj(v);
  const text = str(o.text);
  if (!text) return null;
  return {
    at: strOrNull(o.at),
    who: str(o.who),
    session_id: strOrNull(o.session_id),
    text,
    tone: TONES.includes(o.tone as ActivityTone) ? (o.tone as ActivityTone) : "info",
  };
}

/** `GET /sessions/{id}/mission` → a view, or null when not found / not a shape. */
export function decodeMission(raw: unknown): MissionView | null {
  const o = obj(raw);
  if (o.found !== true) return null;
  const s = obj(o.session);
  if (!str(s.id)) return null;
  const c = obj(o.coordinator);
  const p = obj(o.progress);
  const d = obj(o.deliverable);
  const w = d.worklist == null ? null : obj(d.worklist);
  return {
    session: {
      id: str(s.id),
      task: str(s.task),
      objective: str(s.objective) || str(s.task),
      status: str(s.status),
      outcome: strOrNull(s.outcome),
      project_id: strOrNull(s.project_id),
      created_at: strOrNull(s.created_at),
      finished_at: strOrNull(s.finished_at),
      interrupted: s.interrupted === true,
      continued_as: strOrNull(s.continued_as),
      provider: str(s.provider),
      model: str(s.model),
      route_note: str(s.route_note),
    },
    coordinator: {
      name: str(c.name) || "Jarvis",
      status: status(c.status),
      waiting_on: waiting(c.waiting_on),
      steps: num(c.steps),
    },
    members: (Array.isArray(o.members) ? o.members : [])
      .map(decodeMember)
      .filter((m): m is MissionMember => m !== null),
    progress: { done: num(p.done), total: num(p.total) },
    activity: (Array.isArray(o.activity) ? o.activity : [])
      .map(decodeActivity)
      .filter((a): a is MissionActivity => a !== null),
    deliverable: {
      text: str(d.text),
      documents: strList(d.documents),
      worklist: w
        ? { total: num(w.total), done: num(w.done), failed: num(w.failed), pending: num(w.pending), doing: num(w.doing) }
        : null,
    },
  };
}

/** `GET /missions` → rows, newest first as the daemon sent them. */
export function decodeMissions(raw: unknown): MissionRow[] {
  const list = obj(raw).missions;
  if (!Array.isArray(list)) return [];
  const out: MissionRow[] = [];
  for (const v of list) {
    const o = obj(v);
    const id = str(o.id);
    if (!id) continue;
    out.push({
      id,
      objective: str(o.objective),
      status: str(o.status),
      waiting: o.waiting === true,
      outcome: strOrNull(o.outcome),
      project_id: strOrNull(o.project_id),
      created_at: strOrNull(o.created_at),
      finished_at: strOrNull(o.finished_at),
    });
  }
  return out;
}

/* ------------------------------------------------------------- the URL --- */

/**
 * The Agents page's screens (v1.308.0 — the round table is gone):
 *
 *   mission  `/agents` (New task), `?mission=<id>`, and a PROJECT's mission
 *            screen `?project=<pid>[&mission=<id>]` — objectives scoped to the
 *            project and worked by its team.
 *   team     `?view=team` — "Your team": every agent, create / edit / faces /
 *            inbox / files / coach. `?world=general` (the old General rooms)
 *            lands here too. v1.309.0: `&agent=<roster name>` opens it with
 *            that agent selected — where an agent's notifications land.
 *   room     `?thread=<id>` — a conversation from the old round table (or a
 *            chat @-mention panel), READ-ONLY. Every link that ever pointed
 *            at a room (the palette, chat's "open in Agents") still resolves.
 *   guide    `?talk=…&ask=…` — the old "Ask the Guide" link; it now hands the
 *            question to CHAT as an @guide mention.
 */
export type AgentsRoute =
  | { kind: "mission"; id: string; project: string }
  | { kind: "team"; agent?: string }
  | { kind: "room"; thread: string }
  | { kind: "guide"; ask: string };

export function parseAgentsRoute(search: string): AgentsRoute {
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(search);
  } catch {
    return { kind: "mission", id: "", project: "" };
  }
  const v = (k: string) => (params.get(k) || "").trim();
  if (v("thread")) return { kind: "room", thread: v("thread") };
  if (v("talk") || v("ask")) return { kind: "guide", ask: v("ask") };
  if (v("view") === "team" || v("world") === "general") {
    // The key is ABSENT for a bare team link (never `agent: ""`): the bare
    // route is pinned as exactly `{kind: "team"}` since v1.307.0.
    return v("agent") ? { kind: "team", agent: v("agent") } : { kind: "team" };
  }
  return { kind: "mission", id: v("mission"), project: v("project") };
}

export function agentsPath(route: AgentsRoute): string {
  switch (route.kind) {
    case "team":
      return route.agent ? `/agents?view=team&agent=${encodeURIComponent(route.agent)}` : "/agents?view=team";
    case "room":
      return `/agents?thread=${encodeURIComponent(route.thread)}`;
    case "guide":
      return route.ask ? `/agents?talk=guide&ask=${encodeURIComponent(route.ask)}` : "/agents?talk=guide";
    case "mission": {
      const q: string[] = [];
      if (route.project) q.push(`project=${encodeURIComponent(route.project)}`);
      if (route.id) q.push(`mission=${encodeURIComponent(route.id)}`);
      return q.length ? `/agents?${q.join("&")}` : "/agents";
    }
  }
}

/** A mission's path — inside its project when it has one. */
export function missionPath(id?: string, project?: string): string {
  return agentsPath({ kind: "mission", id: id ?? "", project: project ?? "" });
}

/** "Ask the Guide" (v1.308.0): the question goes to CHAT as an @guide
 *  mention — prefilled, never auto-sent (the consent rule). */
export function guideChatPath(ask: string): string {
  return `/chat?ask=${encodeURIComponent(`@guide ${ask.trim()}`.trim())}`;
}

/* ----------------------------------------------------------- the words --- */

export function statusWord(s: MemberStatus): string {
  switch (s) {
    case "queued":
      return "Waiting";
    case "working":
      return "Working";
    case "waiting_you":
      return "Needs you";
    case "done":
      return "Done";
    case "failed":
      return "Failed";
    case "cancelled":
      return "Stopped";
  }
}

/** The one-line headline for the whole mission. */
export function missionHeadline(v: MissionView): string {
  const st = v.session.status;
  if (st === "completed") return v.session.outcome === "needs_you" ? "Finished, but something needs you" : "Finished";
  if (st === "failed") return "Could not finish";
  if (st === "cancelled") return "Stopped";
  if (st === "queued") return "Queued, waiting for a free slot";
  const { done, total } = v.progress;
  if (v.coordinator.waiting_on || v.members.some((m) => m.status === "waiting_you"))
    return "Waiting for your OK";
  if (total === 0) return "Jarvis is planning the work";
  return `${done} of ${total} teammate${total === 1 ? "" : "s"} done`;
}

/** "14:02" for an ISO stamp (local time); "" when unreadable. */
export function clock(iso: string | null): string {
  if (!iso) return "";
  // The daemon stores naive UTC; read a stamp without a zone as UTC.
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** A member's or file's basename for a chip. */
export function baseName(path: string): string {
  return path.split(/[\\/]/).pop() || path;
}
