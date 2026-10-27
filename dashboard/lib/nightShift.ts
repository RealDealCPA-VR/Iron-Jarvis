/**
 * NIGHT SHIFT — Coding sessions › Tonight (v1.398.0, phase N1 of
 * docs/NIGHT-SHIFT-PLAN.md; the daemon's routes/night.py and harness/night.py).
 *
 * The user queues coding tasks; inside the night window each starts by
 * itself as a NEW coding session on its own branch. This is the screen's
 * reading of `/build/night` (decoders that WHITELIST, the calls, the words).
 * Kept OUTSIDE lib/api.ts (~71 test files mock it wholesale).
 */

import { del, get, patch, post } from "@/lib/api";
import { outcomeWords, statusWords, type StatusWords } from "@/lib/statusWords";

export type NightState = "queued" | "running" | "done" | "failed" | "needs_you" | "stopped";

export const NIGHT_STATES: readonly NightState[] = ["queued", "running", "done", "failed", "needs_you", "stopped"];

export interface NightPick {
  provider: string;
  model: string;
}

export interface NightNote {
  kind: string;
  text: string;
  at: string | null;
}

export interface NightTask {
  id: string;
  folder: string;
  words: string;
  provider: string;
  model: string;
  team: boolean;
  tryOn: NightPick[];
  state: NightState;
  threadId: string;
  branch: string;
  notes: NightNote[];
  /** v1.398.0 review: the user's MAIN folder changed while the task ran
   *  (its commands are not confined to its branch) — shown un-folded. */
  mainChanged: boolean;
  startedAt: string | null;
  endedAt: string | null;
}

/** The note the row shows OPEN when the main folder changed (the daemon's
 *  "outside" note), else "" — never folded under "What happened". */
export function mainChangedWords(t: Pick<NightTask, "mainChanged" | "notes">): string {
  if (!t.mainChanged) return "";
  return t.notes.find((n) => n.kind === "outside")?.text ?? "Your main folder changed while this task ran.";
}

export interface NightWindow {
  start: string;
  end: string;
  open: boolean;
  /** The window in one plain sentence ("Starts at 11:00 PM tonight."). */
  words: string;
}

export interface NightList {
  tasks: NightTask[];
  window: NightWindow;
  atOnce: number;
  taskMinutes: number;
  cardWaitMinutes: number;
  emergencyStop: boolean;
}

function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown, max = 20_000): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function num(v: unknown, fallback: number): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fallback;
}

function decodePick(raw: unknown): NightPick | null {
  const r = rec(raw);
  const provider = str(r?.provider, 120).trim();
  return provider ? { provider, model: str(r?.model, 300).trim() } : null;
}

function decodeNote(raw: unknown): NightNote | null {
  const r = rec(raw);
  const text = str(r?.text, 400).trim();
  if (!text) return null;
  return { kind: str(r?.kind, 40), text, at: typeof r?.at === "string" ? r.at : null };
}

export function decodeTask(raw: unknown): NightTask | null {
  const r = rec(raw);
  const id = str(r?.id, 120);
  if (!r || !id) return null;
  const state = (NIGHT_STATES as readonly string[]).includes(str(r.state, 20)) ? (r.state as NightState) : "queued";
  return {
    id,
    folder: str(r.folder, 1000),
    words: str(r.words),
    provider: str(r.provider, 120),
    model: str(r.model, 300),
    team: r.team === true,
    tryOn: (Array.isArray(r.try_on) ? (r.try_on as unknown[]) : []).map(decodePick).filter((p): p is NightPick => p !== null),
    state,
    threadId: str(r.thread_id, 120),
    branch: str(r.branch, 300),
    notes: (Array.isArray(r.notes) ? (r.notes as unknown[]) : []).map(decodeNote).filter((n): n is NightNote => n !== null),
    mainChanged: r.main_changed === true,
    startedAt: typeof r.started_at === "string" ? r.started_at : null,
    endedAt: typeof r.ended_at === "string" ? r.ended_at : null,
  };
}

export function decodeList(raw: unknown): NightList {
  const r = rec(raw);
  const w = rec(r?.window);
  const limits = rec(r?.limits);
  const rows: unknown[] = Array.isArray(r?.tasks) ? (r?.tasks as unknown[]) : [];
  return {
    tasks: rows.map(decodeTask).filter((t): t is NightTask => t !== null),
    window: {
      start: str(w?.start, 5),
      end: str(w?.end, 5),
      open: w?.open === true,
      words: str(w?.words, 300),
    },
    atOnce: num(limits?.at_once, 2),
    taskMinutes: num(limits?.task_minutes, 90),
    cardWaitMinutes: num(limits?.card_wait_minutes, 15),
    emergencyStop: r?.emergency_stop === true,
  };
}

// ------------------------------------------------------------------ calls

export async function listNight(): Promise<NightList> {
  return decodeList(await get<unknown>("/build/night"));
}

export interface NightDraft {
  folder: string;
  words: string;
  provider?: string;
  model?: string;
  team?: boolean;
  tryOn?: NightPick[];
}

function draftBody(d: NightDraft): Record<string, unknown> {
  return {
    folder: d.folder,
    words: d.words,
    provider: d.provider ?? "",
    model: d.model ?? "",
    team: !!d.team,
    try_on: (d.tryOn ?? []).map((p) => ({ provider: p.provider, model: p.model })),
  };
}

export async function addNight(d: NightDraft): Promise<NightTask | null> {
  return decodeTask(await post<unknown>("/build/night", draftBody(d)));
}

export async function editNight(id: string, d: NightDraft): Promise<NightTask | null> {
  return decodeTask(await patch<unknown>(`/build/night/${encodeURIComponent(id)}`, draftBody(d)));
}

export function removeNight(id: string): Promise<unknown> {
  return del(`/build/night/${encodeURIComponent(id)}`);
}

export function moveNight(id: string, direction: "up" | "down"): Promise<unknown> {
  return post(`/build/night/${encodeURIComponent(id)}/move`, { direction });
}

export function cancelNight(id: string): Promise<unknown> {
  return post(`/build/night/${encodeURIComponent(id)}/cancel`, {});
}

// ------------------------------------------------------------------ words

/** The row's state in the ONE status table's words (lib/statusWords). */
export function nightStateWords(state: NightState): StatusWords {
  if (state === "needs_you") return outcomeWords("needs_you") ?? statusWords("blocked");
  return statusWords(state);
}

/** The view's one line under "Tonight": the window, then the limits. */
export function nightLimitsLine(list: NightList): string {
  const each = list.taskMinutes === 1 ? "1 minute" : `${list.taskMinutes} minutes`;
  const atOnce = list.atOnce === 1 ? "One at a time" : `Up to ${list.atOnce} at a time`;
  return `${atOnce}, never two in one folder, ${each} each. Each runs on its own branch.`;
}

/** v1.398.0 review: what a night task may do without asking, said where the
 *  task is queued (harness/night.RUNS_UNASKED): its files and its commands
 *  in its own branch folder; anything else waits for the user's OK — on the
 *  phone too, when one is set up — and is skipped after the wait. Its
 *  commands are not confined to that folder, which the record checks. */
export function nightRulesLine(list: Pick<NightList, "cardWaitMinutes">): string {
  const wait = list.cardWaitMinutes === 1 ? "1 minute" : `${list.cardWaitMinutes} minutes`;
  return (
    "It edits files and runs commands in its branch folder without asking. Anything else, like the web or a " +
    `commit, waits ${wait} for your OK (on your phone too, if set up), then is skipped and listed for you.`
  );
}

/** What Emergency stop means for the list, said once above it. */
export const EMERGENCY_LINE =
  "Emergency stop is on, so nothing here starts. Turn it off on the Autonomy page to let tonight's tasks run.";

/** How a task runs, in words ("Your default model" / a model / the team /
 *  "Try on 2 models"). `nameOf` names a model pick the way the model menu does. */
export function howItRunsWords(t: Pick<NightTask, "provider" | "model" | "team" | "tryOn">,
                               nameOf: (p: NightPick) => string): string {
  if (t.tryOn.length) return `Try on ${t.tryOn.map(nameOf).join(", ")}`;
  if (t.team) return "Your model team";
  if (t.provider) return nameOf({ provider: t.provider, model: t.model });
  return "Your default model";
}

/** The last part of a folder path (the row's place). */
export function folderName(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

/** The first line of the words (the row's title). */
export function taskTitle(words: string): string {
  const first = words.split(/\r?\n/).map((l) => l.trim()).find(Boolean) ?? "";
  return first.length > 120 ? `${first.slice(0, 119)}…` : first;
}

/** How often the list is read while the window is visible. */
export const NIGHT_POLL_MS = 10_000;

// ===================================================================== N2
// LAST NIGHT (v1.399.0, night shift N2 — the daemon's harness/night_morning.py
// and GET /build/night/summaries): ONE summary per night, built from the
// records (never a model's words). Each task's row: its branch, how it ended,
// its tests, the files it changed, its time, its usage per account, what
// needs the user; failed and needs-you first (the daemon sorts them).

export interface NightUsage {
  /** The Iron-Proxy account's name ("" = this PC's own sign-in or key). */
  account: string;
  provider: string;
  model: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number | null;
  /** How it is paid: "plan" (a subscription: its money is a list-price
   *  equivalent), "pay" (billed per use), "free" (this PC or your network). */
  kind: "plan" | "pay" | "free" | "";
  /** The daemon's own words for it ("12,345 tokens · ≈$0.42 at list price
   *  (your plan)"). */
  words: string;
}

/** The session's branch as it is NOW (no git call: its stored facts). */
export interface NightRowSession {
  exists: boolean;
  archived: boolean;
  hasBranch: boolean;
  removed: boolean;
  pushed: boolean;
  prUrl: string;
  merged: boolean;
}

export interface NightRow {
  id: string;
  threadId: string;
  title: string;
  folder: string;
  folderName: string;
  branch: string;
  state: NightState;
  tests: "passed" | "failed" | "";
  testsTrusted: boolean;
  files: number;
  added: number;
  removed: number;
  seconds: number;
  usage: NightUsage[];
  commit: { sha: string; subject: string } | null;
  /** Why there is no commit, in words ("Nothing changed, …"). */
  commitNote: string;
  /** A failed / needs-you / stopped task's commit: "Its work so far is
   *  committed on ij/…, so nothing is lost." */
  committedWords: string;
  /** What needs the user ("Needs your OK: …", low trust, an account limit). */
  needs: string[];
  mainChanged: boolean;
  mainChangedWords: string;
  notes: NightNote[];
  failure: string;
  tryOn: number;
  session: NightRowSession;
}

export interface NightSummary {
  night: string;
  /** "Monday, October 26". */
  label: string;
  rows: NightRow[];
  counts: Record<NightState, number>;
  /** The notice's own words ("Last night: 1 failed, 1 needs you, 1 done"). */
  title: string;
  body: string;
}

function int(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.trunc(v) : 0;
}

function decodeUsage(raw: unknown, words: unknown): NightUsage | null {
  const r = rec(raw);
  if (!r) return null;
  const kind = str(r.kind, 10);
  return {
    account: str(r.account, 200),
    provider: str(r.provider, 120),
    model: str(r.model, 300),
    inputTokens: int(r.input_tokens),
    outputTokens: int(r.output_tokens),
    costUsd: typeof r.cost_usd === "number" && Number.isFinite(r.cost_usd) ? r.cost_usd : null,
    kind: kind === "plan" || kind === "pay" || kind === "free" ? kind : "",
    words: str(words, 300),
  };
}

function decodeRowSession(raw: unknown): NightRowSession {
  const r = rec(raw);
  return {
    exists: r?.exists === true,
    archived: r?.archived === true,
    hasBranch: r?.has_branch === true,
    removed: r?.removed === true,
    pushed: r?.pushed === true,
    prUrl: str(r?.pr_url, 500),
    merged: r?.merged === true,
  };
}

export function decodeRow(raw: unknown): NightRow | null {
  const r = rec(raw);
  const id = str(r?.id, 120);
  if (!r || !id) return null;
  const state = (NIGHT_STATES as readonly string[]).includes(str(r.state, 20)) ? (r.state as NightState) : "stopped";
  const tests = str(r.tests, 10);
  const words: unknown[] = Array.isArray(r.usage_words) ? (r.usage_words as unknown[]) : [];
  const usage = (Array.isArray(r.usage) ? (r.usage as unknown[]) : [])
    .map((u, i) => decodeUsage(u, words[i]))
    .filter((u): u is NightUsage => u !== null);
  const c = rec(r.commit);
  return {
    id,
    threadId: str(r.thread_id, 120),
    title: str(r.title, 300) || "Night task",
    folder: str(r.folder, 1000),
    folderName: str(r.folder_name, 300),
    branch: str(r.branch, 300),
    state,
    tests: tests === "passed" || tests === "failed" ? tests : "",
    testsTrusted: r.tests_trusted !== false,
    files: int(r.files),
    added: int(r.added),
    removed: int(r.removed),
    seconds: typeof r.seconds === "number" && Number.isFinite(r.seconds) ? r.seconds : 0,
    usage,
    commit: c && str(c.sha, 64) ? { sha: str(c.sha, 64), subject: str(c.subject, 300) } : null,
    commitNote: str(r.commit_note, 600),
    committedWords: str(r.committed_words, 400),
    needs: (Array.isArray(r.needs) ? (r.needs as unknown[]) : []).map((x) => str(x, 400)).filter(Boolean),
    mainChanged: r.main_changed === true,
    mainChangedWords: str(r.main_changed_words, 600),
    notes: (Array.isArray(r.notes) ? (r.notes as unknown[]) : []).map(decodeNote).filter((n): n is NightNote => n !== null),
    failure: str(r.failure, 400),
    tryOn: int(r.try_on),
    session: decodeRowSession(r.session),
  };
}

export function decodeSummary(raw: unknown): NightSummary | null {
  const r = rec(raw);
  const night = str(r?.night, 20);
  if (!r || !night) return null;
  const counts = rec(r.counts);
  const w = rec(r.words);
  return {
    night,
    label: str(r.label, 80) || night,
    rows: (Array.isArray(r.rows) ? (r.rows as unknown[]) : []).map(decodeRow).filter((x): x is NightRow => x !== null),
    counts: Object.fromEntries(NIGHT_STATES.map((s) => [s, int(counts?.[s])])) as Record<NightState, number>,
    title: str(w?.title, 300),
    body: str(w?.body, 300),
  };
}

export async function listSummaries(): Promise<NightSummary[]> {
  const raw = rec(await get<unknown>("/build/night/summaries"));
  const nights: unknown[] = Array.isArray(raw?.nights) ? (raw?.nights as unknown[]) : [];
  return nights.map(decodeSummary).filter((s): s is NightSummary => s !== null);
}

/** Push the task's branch (the session's own Push: never a force). */
export function pushNightBranch(threadId: string): Promise<unknown> {
  return post(`/build/sessions/${encodeURIComponent(threadId)}/git/push`, {});
}

/** Open a pull request of the task's branch with the branch's own prefill
 *  (the session's Branch view reads the same `pr_draft`). Answers the
 *  request's link. */
export async function openNightPullRequest(threadId: string): Promise<string> {
  const base = `/build/sessions/${encodeURIComponent(threadId)}/git`;
  const status = rec(await get<unknown>(base));
  const draft = rec(status?.pr_draft);
  const made = rec(
    await post<unknown>(`${base}/pr`, { title: str(draft?.title, 300), body: str(draft?.body, 20_000) }),
  );
  return str(made?.url, 500);
}

/** Throw the task's branch folder away (the session's own Discard, after
 *  the row's two presses): the folder to Iron Jarvis's trash, the branch
 *  KEPT. Answers the daemon's own sentence. */
export async function throwAwayNightFolder(threadId: string): Promise<string> {
  const done = rec(
    await post<unknown>(`/build/sessions/${encodeURIComponent(threadId)}/git/discard`, { confirm: true }),
  );
  return str(done?.note, 600);
}

/** How long a task ran, in words ("45 s", "12 min", "1 h 5 min"). */
export function ranWords(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const min = Math.round(s / 60);
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60);
  const rest = min % 60;
  return rest ? `${h} h ${rest} min` : `${h} h`;
}

/** The row's tests in words. */
export function testsWords(row: Pick<NightRow, "tests" | "testsTrusted">): string {
  if (row.tests === "passed") return row.testsTrusted ? "Tests passed" : "Tests passed, but not trusted";
  if (row.tests === "failed") return "Tests failed";
  return "No tests ran";
}

/** The row's files in words ("2 files changed (+10 −3)"). */
export function filesWords(row: Pick<NightRow, "files" | "added" | "removed">): string {
  if (!row.files) return "No files changed";
  const lines = row.added || row.removed ? ` (+${row.added} −${row.removed})` : "";
  return `${row.files} file${row.files === 1 ? "" : "s"} changed${lines}`;
}

/** One usage row: whose account (or the provider), then its words. */
export function usageLine(u: NightUsage, providerName: (p: string) => string): string {
  const who = u.account || providerName(u.provider) || "Your model";
  return u.words ? `${who}: ${u.words}` : who;
}

/** The view's heading of a night: the newest is "Last night". */
export function nightHeading(s: Pick<NightSummary, "label">, newest: boolean): string {
  return newest ? `Last night · ${s.label}` : s.label;
}

/** A night's counts in words ("1 failed, 1 needs you, 1 done"). */
export function countsWords(counts: Record<NightState, number>): string {
  const order: [NightState, string][] = [
    ["failed", "failed"],
    ["needs_you", "needs you"],
    ["running", "still running"],
    ["done", "done"],
    ["stopped", "stopped"],
  ];
  const parts = order.filter(([s]) => counts[s] > 0).map(([s, w]) => `${counts[s]} ${w}`);
  return parts.join(", ") || "no tasks";
}
