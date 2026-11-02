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

/** v1.395.0: a member's RECORD for one role (the daemon's
 *  agents/team_record RoleRecord.view): pieces, how many passed, failures in a
 *  row, its judgement (good | unknown | not_good) and the plain words. */
export interface TeamRoleRecord {
  runs: number;
  passed: number;
  streak: number;
  status: "good" | "unknown" | "not_good";
  words: string;
}

/** The role as a noun, as the team router's reasons say it — the TWIN of the
 *  daemon's agents/model_team.ROLE_NOUNS, pinned equal by
 *  tests/test_team_words_v1395.py. */
export const ROLE_NOUNS: Record<string, string> = { read: "reading", code: "coding", review: "reviewing", test: "testing" };

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
  /** "claude" / "codex" / "grok" when its model runs on that subscription (its
   *  account picker lists that vendor's accounts); "" otherwise. */
  vendor: string;
  usage: TeamUsage | null;
  /** Can its model be reached right now? null = not known. */
  available: boolean | null;
  paused: boolean;
  /** v1.395.0: its record per role it holds ({} from an older daemon). */
  records: Record<string, TeamRoleRecord>;
  /** v1.423.0 (Team notes): its reviewers' findings over the last 30 days
   *  (null from an older daemon, which sends none). */
  findings: TeamFindings | null;
  /** v1.423.0 (Team notes, Phase 2): its WORKING HABITS — the accepted ones
   *  and how many wait on its coach card (null from an older daemon). */
  notes: TeamNotes | null;
}

/** v1.423.0 (Team notes, Phase 2): one working habit the owner accepted
 *  (the daemon's agents/team_notes.note_view). */
export interface TeamNoteRow {
  id: string;
  text: string;
  /** How many confirmed findings it generalizes. */
  findings: number;
  acceptedAt: string | null;
  /** v1.423.0 (Team notes, Phase 3): the member's record before and after
   *  the habit was accepted (the daemon's agents/team_measure) — ABSENT when
   *  it could not be read or an older daemon sends none (never zeros). */
  measure?: HabitMeasure;
}

/** v1.423.0 (Team notes, Phase 3): one side of a habit's acceptance. Under
 *  5 pieces (`enough` false) there are NO numbers — "not enough work yet". */
export interface HabitSide {
  enough: boolean;
  pieces: number;
  /** Replies whose first judged piece a reviewer approved, of those judged. */
  firstPass: { approved: number; of: number } | null;
  confirmed: number;
  /** Confirmed findings per piece (rounded by the daemon). */
  confirmedPerPiece: number | null;
  /** Pieces the finish check judged, and how many passed. */
  checks: { passed: number; of: number } | null;
}

/** A habit's MEASURE: before and after its acceptance, and whether things
 *  look WORSE since (the daemon's rule — only SHOWN, nothing removes a habit
 *  by itself). */
export interface HabitMeasure {
  before: HabitSide;
  after: HabitSide;
  worse: boolean;
}

/** A member's working habits (the daemon's `notes` on a GET /team row). */
export interface TeamNotes {
  accepted: TeamNoteRow[];
  /** Suggested habits waiting for Accept / Decline on its coach card. */
  suggested: number;
  /** Its TEAM-NOTES.md holds text Iron Jarvis did not write (kept as is). */
  handEdited: boolean;
}

/** v1.423.0 (Team notes, Phase 1b): what became of the problems a reviewer
 *  named in a member's work (the daemon's agents/team_findings.findings_for):
 *  confirmed = the lead said fixed AND a later piece changed that file;
 *  saidFixed = the lead said fixed, nothing later changed the file;
 *  open = never answered. firstPass = the replies where a reviewer approved
 *  the member's FIRST judged piece, of those it judged. */
export interface TeamFindings {
  confirmed: number;
  didNotApply: number;
  saidFixed: number;
  open: number;
  firstPass: { approved: number; of: number };
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

function decodeRecords(raw: unknown, roles: readonly string[]): Record<string, TeamRoleRecord> {
  const r = rec(raw);
  const out: Record<string, TeamRoleRecord> = {};
  if (!r) return out;
  for (const role of roles) {
    const x = rec(r[role]);
    const words = str(x?.words, 120);
    if (!x || !words) continue;
    const status = x.status === "good" || x.status === "not_good" ? x.status : "unknown";
    out[role] = {
      runs: Math.max(0, Math.round(num(x.runs) ?? 0)),
      passed: Math.max(0, Math.round(num(x.passed) ?? 0)),
      streak: Math.max(0, Math.round(num(x.streak) ?? 0)),
      status,
      words,
    };
  }
  return out;
}

const count = (v: unknown): number => {
  const n = num(v);
  return n === null ? 0 : Math.max(0, Math.round(n));
};

/** A member row's `findings`, defensively: an older daemon sends none (null),
 *  and junk reads as zero. */
export function decodeFindings(raw: unknown): TeamFindings | null {
  const r = rec(raw);
  if (!r) return null;
  const fp = rec(r.first_pass);
  const of = count(fp?.of);
  return {
    confirmed: count(r.confirmed),
    didNotApply: count(r.did_not_apply),
    saidFixed: count(r.said_fixed),
    open: count(r.open),
    firstPass: { approved: Math.min(count(fp?.approved), of), of },
  };
}

/** The member card's ONE quiet line: "Reviewers' findings (30 days):
 *  6 confirmed, 1 did not apply · first-pass approval 3 of 7" — a part that
 *  is zero is left out, and "" (no line at all) when everything is zero. */
export function findingsWords(f: TeamFindings | null): string {
  if (!f) return "";
  const parts = [
    f.confirmed ? `${f.confirmed} confirmed` : "",
    f.saidFixed ? `${f.saidFixed} said fixed but not confirmed` : "",
    f.didNotApply ? `${f.didNotApply} did not apply` : "",
    f.open ? `${f.open} not answered` : "",
  ].filter(Boolean);
  const first = f.firstPass.of ? `first-pass approval ${f.firstPass.approved} of ${f.firstPass.of}` : "";
  const body = [parts.join(", "), first].filter(Boolean).join(" · ");
  return body ? `Reviewers' findings (30 days): ${body}` : "";
}

/** A member row's `notes`, defensively: an older daemon sends none (null),
 *  a habit with no id or text is left out, junk counts read as zero. */
export function decodeNotes(raw: unknown): TeamNotes | null {
  const r = rec(raw);
  if (!r) return null;
  const accepted: TeamNoteRow[] = [];
  for (const item of Array.isArray(r.accepted) ? r.accepted : []) {
    const x = rec(item);
    const id = str(x?.id, 120);
    const text = str(x?.text, 400);
    if (!x || !id || !text) continue;
    const measure = decodeMeasure(x.measure);
    accepted.push({
      id,
      text,
      findings: count(x.findings),
      acceptedAt: str(x.accepted_at, 60) || null,
      ...(measure ? { measure } : {}),
    });
  }
  return { accepted, suggested: count(r.suggested), handEdited: r.hand_edited === true };
}

/** `{<key>, of}` read defensively: null when absent or `of` is 0. */
function pair(raw: unknown, key: string): { x: number; of: number } | null {
  const r = rec(raw);
  if (!r) return null;
  const of = count(r.of);
  return of ? { x: Math.min(count(r[key]), of), of } : null;
}

function decodeSide(raw: unknown): HabitSide | null {
  const r = rec(raw);
  if (!r || typeof r.enough !== "boolean") return null;
  if (!r.enough) {
    // No numbers under 5 pieces — whatever else came with it is ignored.
    return { enough: false, pieces: count(r.pieces), firstPass: null, confirmed: 0, confirmedPerPiece: null, checks: null };
  }
  const fp = pair(r.first_pass, "approved");
  const ck = pair(r.checks, "passed");
  const per = num(r.confirmed_per_piece);
  return {
    enough: true,
    pieces: count(r.pieces),
    firstPass: fp ? { approved: fp.x, of: fp.of } : null,
    confirmed: count(r.confirmed),
    confirmedPerPiece: per === null ? null : Math.max(0, per),
    checks: ck ? { passed: ck.x, of: ck.of } : null,
  };
}

/** v1.423.0 (Team notes, Phase 3): a habit's `measure`, defensively — null
 *  when it is missing (unreadable on the daemon's side, or an older daemon)
 *  or either side is junk. `worse` only when the daemon says exactly true. */
export function decodeMeasure(raw: unknown): HabitMeasure | null {
  const r = rec(raw);
  if (!r) return null;
  const before = decodeSide(r.before);
  const after = decodeSide(r.after);
  if (!before || !after) return null;
  return { before, after, worse: r.worse === true && before.enough && after.enough };
}

const perPiece = (n: number): string => String(Math.round(n * 100) / 100);

/** The habit's ONE quiet line when BOTH sides have enough work: "Since this
 *  habit: first-pass approval 2 of 6 → 5 of 7 · confirmed findings per piece
 *  0.5 → 0.2 · checks passed 3 of 5 → 6 of 6" — each part only when both
 *  sides have it; "" (no line) when either side has under 5 pieces. */
export function measureWords(m: HabitMeasure | null | undefined): string {
  if (!m || !m.before.enough || !m.after.enough) return "";
  const { before: b, after: a } = m;
  const parts = [
    b.firstPass && a.firstPass
      ? `first-pass approval ${b.firstPass.approved} of ${b.firstPass.of} → ${a.firstPass.approved} of ${a.firstPass.of}`
      : "",
    b.confirmedPerPiece !== null && a.confirmedPerPiece !== null
      ? `confirmed findings per piece ${perPiece(b.confirmedPerPiece)} → ${perPiece(a.confirmedPerPiece)}`
      : "",
    b.checks && a.checks
      ? `checks passed ${b.checks.passed} of ${b.checks.of} → ${a.checks.passed} of ${a.checks.of}`
      : "",
  ].filter(Boolean);
  return parts.length ? `Since this habit: ${parts.join(" · ")}` : "";
}

/** The amber line for a habit the daemon flagged (never acted on: the
 *  owner decides, with the habit's own two-press Remove). */
export const HABIT_WORSE_WORDS = "This habit may be making things worse — consider removing it";

/** The member card's ONE quiet line: "Working habits: 2 · 1 suggested" —
 *  a part that is zero is left out ("Working habits: 1 suggested"), and ""
 *  (no line at all) when it has none and none waits. */
export function notesWords(n: TeamNotes | null): string {
  if (!n) return "";
  const kept = n.accepted.length;
  const parts = [kept ? String(kept) : "", n.suggested ? `${n.suggested} suggested` : ""].filter(Boolean);
  return parts.length ? `Working habits: ${parts.join(" · ")}` : "";
}

/** A cited finding in plain words (a `team_note` proposal's evidence):
 *  "src/app.py line 12: X is 1 — set it to 2", "General: …" for none. */
export function findingWords(raw: unknown): string {
  const r = rec(raw);
  if (!r) return "";
  const path = str(r.path, 300);
  const line = num(r.line);
  const what = str(r.what, 600);
  const change = str(r.change, 600);
  if (!what) return "";
  const place = path ? (line && line > 0 ? `${path} line ${Math.round(line)}` : path) : "General";
  return `${place}: ${what}${change ? ` — ${change}` : ""}`;
}

/** "reading: good (4 of 5 passed) · coding: no record yet" — a member's
 *  record, one role after another, in the daemon's own words ("" for none). */
export function recordWords(m: Pick<TeamMember, "roles" | "records">): string {
  return m.roles
    .filter((role) => m.records[role])
    .map((role) => `${ROLE_NOUNS[role] ?? role}: ${m.records[role].words}`)
    .join(" · ");
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
    // v1.424.0 (D6): a Grok member has accounts too.
    vendor: r.vendor === "claude" || r.vendor === "codex" || r.vendor === "grok" ? r.vendor : "",
    usage: decodeUsage(r.usage),
    available: typeof r.available === "boolean" ? r.available : null,
    paused: r.paused === true,
    records: decodeRecords(r.records, cleanRoles(r.roles)),
    findings: decodeFindings(r.findings),
    notes: decodeNotes(r.notes),
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
