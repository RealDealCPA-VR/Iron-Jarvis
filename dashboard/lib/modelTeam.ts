/**
 * v1.394.0 (the long-jobs plan's Phase 5): YOUR MODEL TEAM as Settings ›
 * Model team reads it.
 *
 * A custom agent with one or more roles is a team member (the daemon's
 * `agents/model_team.py`). `GET /team` lists the members with their model,
 * where it runs, its cost type and its usage (Iron-Proxy's CACHED snapshot);
 * `GET /team/starter` is the **Make a starter team** preview (nothing is made
 * by looking) and `POST /team/starter` makes the ticked rows. Every shape
 * crosses a JSON boundary, so each field is checked; junk reads as absent.
 *
 * PLACE_WORDS and COST_WORDS are the TWINS of the daemon's own words, pinned
 * equal by `tests/test_model_team_v1394.py`.
 */

import { cleanRoles, TEAM_ROLES, type TeamRole } from "@/lib/teamRoles";

export const PLACE_WORDS: Record<string, string> = { this_pc: "this PC", network: "your network", cloud: "cloud" };
export const COST_WORDS: Record<string, string> = { free: "free", plan: "your plan", pay: "pay per use" };

export type TeamPlace = "this_pc" | "network" | "cloud" | "";
export type TeamCost = "free" | "plan" | "pay" | "";

export interface TeamUsage {
  account: string;
  accountName: string;
  /** The account's status in the snapshot: ready | usage | limit | needs_sign_in. */
  status: string;
  /** Only when the vendor said: a real number, never a 0 for "unknown". */
  percent: number | null;
  windowMins: number | null;
  resetsAt: string | null;
}

export interface TeamMember {
  name: string;
  rosterName: string;
  description: string;
  provider: string;
  model: string;
  place: TeamPlace;
  cost: TeamCost;
  roles: string[];
  account: string;
  accountName: string;
  /** "claude" / "codex" when its model runs on that subscription (its
   *  account picker lists that vendor's accounts); "" otherwise. */
  vendor: string;
  usage: TeamUsage | null;
  /** Can its model be reached right now? null = not known. */
  available: boolean | null;
  paused: boolean;
}

export interface StarterRow {
  key: string;
  provider: string;
  model: string;
  group: "local" | "subscription" | "paid";
  roles: string[];
  place: TeamPlace;
  cost: TeamCost;
  ticked: boolean;
  /** The member that already runs on this model, or null. */
  exists: string | null;
  description: string;
  label: string;
  name: string;
}

export interface StarterPreview {
  rows: StarterRow[];
  paid: boolean;
  paidAvailable: number;
  note: string;
}

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const str = (v: unknown, max = 400): string => (typeof v === "string" ? v.slice(0, max) : "");
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const place = (v: unknown): TeamPlace => (v === "this_pc" || v === "network" || v === "cloud" ? v : "");
const cost = (v: unknown): TeamCost => (v === "free" || v === "plan" || v === "pay" ? v : "");

function decodeUsage(raw: unknown): TeamUsage | null {
  const r = rec(raw);
  if (!r) return null;
  const pct = num(r.percent);
  return {
    account: str(r.account, 120),
    accountName: str(r.account_name, 120),
    status: str(r.status, 40),
    percent: pct === null ? null : Math.max(0, Math.round(pct)),
    windowMins: num(r.window_mins),
    resetsAt: str(r.resets_at, 60) || null,
  };
}

export function decodeMember(raw: unknown): TeamMember | null {
  const r = rec(raw);
  const name = str(r?.name, 120);
  if (!r || !name) return null;
  return {
    name,
    rosterName: str(r.roster_name, 140) || `custom:${name}`,
    description: str(r.description, 400),
    provider: str(r.provider, 120),
    model: str(r.model, 200),
    place: place(r.place),
    cost: cost(r.cost),
    roles: cleanRoles(r.roles),
    account: str(r.account, 120),
    accountName: str(r.account_name, 120),
    vendor: r.vendor === "claude" || r.vendor === "codex" ? r.vendor : "",
    usage: decodeUsage(r.usage),
    available: typeof r.available === "boolean" ? r.available : null,
    paused: r.paused === true,
  };
}

export function decodeTeam(raw: unknown): { roles: TeamRole[]; members: TeamMember[] } {
  const r = rec(raw);
  const members = (Array.isArray(r?.members) ? r!.members : [])
    .map(decodeMember)
    .filter((m): m is TeamMember => m !== null);
  return { roles: [...TEAM_ROLES], members };
}

export function decodeStarter(raw: unknown): StarterPreview {
  const r = rec(raw);
  const rows: StarterRow[] = [];
  for (const item of Array.isArray(r?.rows) ? r!.rows : []) {
    const x = rec(item);
    const key = str(x?.key, 300);
    const provider = str(x?.provider, 120);
    if (!x || !key || !provider) continue;
    const group = x.group === "local" || x.group === "subscription" || x.group === "paid" ? x.group : null;
    if (!group) continue;
    rows.push({
      key,
      provider,
      model: str(x.model, 200),
      group,
      roles: cleanRoles(x.roles),
      place: place(x.place),
      cost: cost(x.cost),
      ticked: x.ticked === true,
      exists: str(x.exists, 120) || null,
      description: str(x.description, 300),
      label: str(x.label, 120),
      name: str(x.name, 60),
    });
  }
  return {
    rows,
    paid: r?.paid === true,
    paidAvailable: Math.max(0, Math.round(num(r?.paid_available) ?? 0)),
    note: str(r?.note, 400),
  };
}

/** "300" -> "5-hour", "10080" -> "7-day" ("" when unknown). */
export function windowAdjective(mins: number | null): string {
  if (!mins || mins <= 0) return "";
  if (mins % 1440 === 0) return `${mins / 1440}-day`;
  if (mins % 60 === 0) return `${mins / 60}-hour`;
  return `${mins}-minute`;
}

/** Where it runs, in the team's words: "This PC" / "Your network" / "Cloud". */
export function placeWords(p: TeamPlace): string {
  const w = PLACE_WORDS[p] ?? "";
  return w ? w.charAt(0).toUpperCase() + w.slice(1) : "";
}

/** What it costs, in the team's words: "Free" / "Your plan" / "Pay per use". */
export function costWords(c: TeamCost): string {
  const w = COST_WORDS[c] ?? "";
  return w ? w.charAt(0).toUpperCase() + w.slice(1) : "";
}

/** A member's current usage, from the cached account snapshot — never "0%"
 *  for an unknown. */
export function usageWords(m: Pick<TeamMember, "cost" | "usage" | "account" | "provider">): string {
  if (!m.provider) return "Uses the model of whoever hands it work";
  if (m.cost === "free") return "No usage limit";
  if (m.cost === "pay") return "Billed per use";
  const u = m.usage;
  if (!u) return "Not known yet";
  const who = u.accountName ? (m.account ? ` (${u.accountName})` : ` (${u.accountName}, Iron-Proxy's pick)`) : "";
  if (u.status === "limit") return `At its usage limit${who}`;
  if (u.status === "needs_sign_in") return `Needs sign-in${who}`;
  if (u.percent !== null) {
    const adj = windowAdjective(u.windowMins);
    return `${u.percent}% of the ${adj ? `${adj} ` : ""}window used${who}`;
  }
  return `Not known yet${who}`;
}
