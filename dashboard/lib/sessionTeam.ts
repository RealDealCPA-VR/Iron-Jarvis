/**
 * v1.403.0: THE CODING-SESSION TEAM as the screen reads it.
 *
 * The user's decision (2026-10-28), after "Try on several models" left each
 * model unsure of its duties: "Build the coding-session team: Lead plans and
 * checks but never edits files, Workers do the code, Reviewer checks every
 * change. If no worker can take a piece, the lead may do it and must say so."
 *
 *   - the TEAM LEAD is the session's own model (its provider, model and
 *     account): it plans, hands out the pieces, checks each report and talks
 *     with the user — and never edits a file (the daemon takes its tools away);
 *   - WORKERS (1 or 2 model-team members) do the code and run the tests;
 *   - the REVIEWER (0 or 1 member) checks every change before the reply ends.
 *
 * A session with no workers works ALONE, exactly as before.
 *
 * Every shape here crosses a JSON boundary (the session view, a saved reply
 * line, a route's answer), so each field is checked; junk reads as absent,
 * never as a crash. Kept OUTSIDE lib/api.ts (~70 test files mock it
 * wholesale) and lib/buildSessions.ts (which reads the decoders from here).
 */

import { get, post, put } from "@/lib/api";

/** At most this many workers (the daemon refuses a third). */
export const MAX_WORKERS = 2;

/** The role names, the words the daemon's notes to the model use too. */
export const TEAM_LEAD_WORDS = "Team lead";
export const WORKERS_WORDS = "Workers";
export const REVIEWER_WORDS = "Reviewer";

/** What each part of the team does, in one line each. */
export const LEAD_DUTY = "Plans the work and checks it. It never edits files.";
export const WORKER_DUTY = "Write the code and run the tests, one piece at a time.";
export const REVIEWER_DUTY = "Checks every change before the reply ends.";

/** v1.403.0 (the contract's words): Try on is not the team. */
export const TRY_ON_ALONE = "Try on runs each model alone, without the team";

/** A member's state, from the daemon's CACHED team view (never a live call). */
export type MemberState = "ready" | "busy" | "limit" | "unavailable" | "unknown";

/** One member as the session view lists it. */
export interface TeamMemberView {
  name: string;
  provider: string;
  model: string;
  label: string;
  state: MemberState;
}

/** The session's team (the view's `team`, present only while one is set). */
export interface SessionTeam {
  lead: { provider: string; model: string; label: string } | null;
  workers: TeamMemberView[];
  reviewer: TeamMemberView | null;
}

/** A team by its members' names: what PATCH, the folder's default and the
 *  session's setup carry. */
export interface TeamNames {
  workers: string[];
  reviewer: string;
}

/** A team turn's record (the reply line's `team`). */
export interface ReplyTeam {
  lead: string;
  workers: string[];
  reviewer: string;
  pieces: number;
  /** Every time the lead was let do a piece itself (no worker could take
   *  it), with the router's own one sentence of why ("" when none was said). */
  leadDid: { reason: string }[];
}

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const str = (v: unknown, max = 200): string => (typeof v === "string" ? v.slice(0, max).trim() : "");

const STATES: ReadonlySet<string> = new Set(["ready", "busy", "limit", "unavailable", "unknown"]);

function decodeMember(raw: unknown): TeamMemberView | null {
  const r = rec(raw);
  const name = str(r?.name, 120);
  if (!r || !name) return null;
  const state = typeof r.state === "string" && STATES.has(r.state) ? (r.state as MemberState) : "unknown";
  return {
    name,
    provider: str(r.provider, 120),
    model: str(r.model, 300),
    label: str(r.label, 120),
    state,
  };
}

/** The session view's `team`, or null (no team, junk, or no worker — a
 *  session with no workers works alone). */
export function decodeSessionTeam(raw: unknown): SessionTeam | null {
  const r = rec(raw);
  if (!r) return null;
  const workers: TeamMemberView[] = [];
  for (const w of Array.isArray(r.workers) ? r.workers : []) {
    const m = decodeMember(w);
    if (m && !workers.some((x) => x.name === m.name)) workers.push(m);
    if (workers.length >= MAX_WORKERS) break;
  }
  if (!workers.length) return null;
  const lead = rec(r.lead);
  const reviewer = decodeMember(r.reviewer);
  return {
    lead: lead
      ? { provider: str(lead.provider, 120), model: str(lead.model, 300), label: str(lead.label, 120) }
      : null,
    workers,
    reviewer: reviewer && !workers.some((w) => w.name === reviewer.name) ? reviewer : null,
  };
}

/** `{workers: [names], reviewer: name | ""}` (the setup key, the folder's
 *  default), or null — junk, or no worker. */
export function decodeTeamNames(raw: unknown): TeamNames | null {
  const r = rec(raw);
  if (!r) return null;
  const workers: string[] = [];
  for (const w of Array.isArray(r.workers) ? r.workers : []) {
    const n = str(w, 120);
    if (n && !workers.includes(n)) workers.push(n);
    if (workers.length >= MAX_WORKERS) break;
  }
  if (!workers.length) return null;
  const reviewer = str(r.reviewer, 120);
  return { workers, reviewer: workers.includes(reviewer) ? "" : reviewer };
}

/** A session team by its members' names. */
export function teamNamesOf(team: SessionTeam | null): TeamNames | null {
  if (!team || !team.workers.length) return null;
  return { workers: team.workers.map((w) => w.name), reviewer: team.reviewer?.name ?? "" };
}

/** The same team (the workers in any order)? Two "no team"s are the same. */
export function sameTeam(a: TeamNames | null, b: TeamNames | null): boolean {
  if (!a || !b) return !a && !b;
  if (a.reviewer !== b.reviewer || a.workers.length !== b.workers.length) return false;
  return a.workers.every((w) => b.workers.includes(w));
}

/** Everyone on the team, the lead included (0 = it works alone). */
export function teamCount(names: TeamNames | null): number {
  if (!names || !names.workers.length) return 0;
  return 1 + names.workers.length + (names.reviewer ? 1 : 0);
}

/** The chip's words: "Team" alone, "Team · 3" with a team. */
export function teamChipWords(count: number): string {
  return count > 0 ? `Team · ${count}` : "Team";
}

/** A member's state in words. */
export function memberStateWords(state: MemberState): string {
  switch (state) {
    case "ready":
      return "ready";
    case "busy":
      return "busy";
    case "limit":
      return "at its limit";
    case "unavailable":
      return "not available";
    default:
      return "not known";
  }
}

/** A state that keeps the member from working now (the warn tone, beside its
 *  words — never the colour alone). */
export function memberStateWarn(state: MemberState): boolean {
  return state === "limit" || state === "unavailable";
}

/** "Team: lead Opus 5.5 · workers glm-5.3-flash, Qwen3.8-27B · reviewer
 *  Codex" — one worker reads "worker glm-5.3-flash"; a part nobody holds is
 *  left out. */
export function teamLineWords(t: { lead: string; workers: readonly string[]; reviewer: string }): string {
  const parts: string[] = [];
  if (t.lead) parts.push(`lead ${t.lead}`);
  const workers = t.workers.filter(Boolean);
  if (workers.length) parts.push(`${workers.length === 1 ? "worker" : "workers"} ${workers.join(", ")}`);
  if (t.reviewer) parts.push(`reviewer ${t.reviewer}`);
  return parts.length ? `Team: ${parts.join(" · ")}` : "Team";
}

/** A team turn's record, whitelisted — or null (no team, junk, an older
 *  Iron Jarvis). The daemon's twin is the reply line's `team`. */
export function decodeReplyTeam(raw: unknown): ReplyTeam | null {
  const r = rec(raw);
  if (!r) return null;
  const workers = (Array.isArray(r.workers) ? r.workers : [])
    .map((w) => str(w, 120))
    .filter(Boolean)
    .slice(0, 4);
  if (!workers.length) return null;
  const pieces =
    typeof r.pieces === "number" && Number.isInteger(r.pieces) && r.pieces >= 0 ? Math.min(r.pieces, 100_000) : 0;
  const leadDid: { reason: string }[] = [];
  for (const d of Array.isArray(r.lead_did) ? r.lead_did.slice(0, 20) : []) {
    const dr = rec(d);
    if (!dr) continue;
    leadDid.push({ reason: str(dr.reason, 300) });
  }
  return { lead: str(r.lead, 120), workers, reviewer: str(r.reviewer, 120), pieces, leadDid };
}

/** The warn lines of a team turn: one per DIFFERENT reason the lead did a
 *  piece itself, "(N times)" when the same reason came again. */
export function leadDidLines(t: ReplyTeam): string[] {
  const order: string[] = [];
  const times = new Map<string, number>();
  for (const d of t.leadDid) {
    if (!times.has(d.reason)) order.push(d.reason);
    times.set(d.reason, (times.get(d.reason) ?? 0) + 1);
  }
  return order.map((reason) => {
    const n = times.get(reason) ?? 1;
    const head = reason ? `The team lead did this itself: ${reason.replace(/[.\s]+$/, "")}` : "The team lead did this itself";
    return n > 1 ? `${head} (${n} times).` : `${head}.`;
  });
}

/** The pieces the lead handed out, for the line's hover ("" for none). */
export function piecesWords(pieces: number): string {
  if (pieces <= 0) return "";
  return pieces === 1 ? "The team lead handed out 1 piece." : `The team lead handed out ${pieces} pieces.`;
}

// --------------------------------------------------------- the helper rows

/** A part of the team, by the ROLE a helper was given: code, test and read
 *  are a worker's; review is the reviewer's. */
export function helperPart(role: string | undefined): "worker" | "reviewer" | null {
  const r = (role ?? "").trim().toLowerCase();
  if (r === "code" || r === "test" || r === "read") return "worker";
  if (r === "review") return "reviewer";
  return null;
}

/** The names and words the helper rows read in a team turn. */
export interface TeamRoster {
  workers: { name: string; label: string }[];
  reviewer: { name: string; label: string } | null;
}

/** A team turn whose members the screen no longer knows still names the
 *  parts by their roles. */
export const EMPTY_ROSTER: TeamRoster = { workers: [], reviewer: null };

/**
 * ONE rule for a helper row in a team turn: "Worker glm-5.3-flash",
 * "Reviewer Codex" — the member a session's team names (its words), else the
 * part its role gives (code, test, read: a worker; review: the reviewer).
 * Null outside a team turn, for a helper the router has not given a member
 * yet ("a team member for coding" stays), and for any other helper.
 */
export function helperTeamName(agent: string, role: string | undefined, roster: TeamRoster | null): string | null {
  if (!roster) return null;
  const a = (agent ?? "").trim();
  if (!a || a.startsWith("role:") || a.startsWith("remote:")) return null;
  const bare = (a.startsWith("custom:") ? a.slice("custom:".length) : a).trim();
  if (!bare) return null;
  const worker = roster.workers.find((w) => w.name === bare);
  if (worker) return `Worker ${worker.label || bare}`;
  if (roster.reviewer && roster.reviewer.name === bare) return `Reviewer ${roster.reviewer.label || bare}`;
  const part = helperPart(role);
  if (part === "worker") return `Worker ${bare}`;
  if (part === "reviewer") return `Reviewer ${bare}`;
  return null;
}

/** The roster a session team gives the helper rows. */
export function rosterOf(team: SessionTeam | null | undefined): TeamRoster | null {
  if (!team) return null;
  return {
    workers: team.workers.map((w) => ({ name: w.name, label: w.label })),
    reviewer: team.reviewer ? { name: team.reviewer.name, label: team.reviewer.label } : null,
  };
}

// ----------------------------------------------------------- the routes

/** POST /team/members/from-model's answer. */
export interface MemberFromModel {
  name: string;
  provider: string;
  model: string;
  label: string;
  roles: string[];
  created: boolean;
}

/** Make (or reuse) the team member for a model, in the part it is picked
 *  for. A refusal is thrown as the ApiError the route answered. */
export async function memberFromModel(
  provider: string,
  model: string,
  role: "worker" | "reviewer",
): Promise<MemberFromModel> {
  const raw = rec(await post<unknown>("/team/members/from-model", { provider, model, role }));
  const name = str(raw?.name, 120);
  if (!raw || !name) throw new Error("Iron Jarvis did not say which team member it made.");
  return {
    name,
    provider: str(raw.provider, 120),
    model: str(raw.model, 300),
    label: str(raw.label, 120),
    roles: (Array.isArray(raw.roles) ? raw.roles : []).filter((x): x is string => typeof x === "string"),
    created: raw.created === true,
  };
}

/** The body PATCH /build/sessions/{id} takes: null = work alone. */
export function teamPatch(names: TeamNames | null): { team: TeamNames | null } {
  if (!names || !names.workers.length) return { team: null };
  return { team: { workers: names.workers.slice(0, MAX_WORKERS), reviewer: names.reviewer || "" } };
}

export function folderTeamPath(folder: string): string {
  return `/build/folders/team?folder=${encodeURIComponent(folder)}`;
}

/** The team new sessions in a folder start with (null = none). */
export async function getFolderTeam(folder: string): Promise<TeamNames | null> {
  return decodeTeamNames(rec(await get<unknown>(folderTeamPath(folder)))?.team);
}

/** Save (or, with null, clear) a folder's team for new sessions. */
export async function putFolderTeam(folder: string, names: TeamNames | null): Promise<TeamNames | null> {
  const body = { folder, ...teamPatch(names) };
  return decodeTeamNames(rec(await put<unknown>("/build/folders/team", body))?.team);
}
