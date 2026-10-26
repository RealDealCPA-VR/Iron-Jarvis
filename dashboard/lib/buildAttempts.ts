/**
 * v1.397.0: TRY ONE TASK ON SEVERAL MODELS — the screen's reading of the
 * daemon's harness/attempts.py (the long-jobs plan's Phase 5 Part B, the IJ
 * harness part 3 Phase 8).
 *
 * "Try this with Claude, Codex and the local model" (or the box's "Try on…"
 * control) starts up to three ATTEMPTS, each on its own branch and its own
 * model. While they run, the turn's `attempt` frames draw one row each (like
 * a helper's row: its state, its steps, an approval card of its own). When
 * they end, the reply line's `attempts` card shows them side by side: the
 * model (and account), why it stopped, the finish check's tests, the files it
 * changed, its time and its usage or cost — and Keep on one.
 *
 * Every decoder WHITELISTS (a field the daemon did not promise dies here).
 * Kept OUTSIDE lib/api.ts (~71 test files mock it wholesale); the press uses
 * `post` from it like every other Build action.
 */

import { ApiError, post } from "@/lib/api";
import { onceReasonOf, type OnceReason } from "@/lib/onceReason";

/** At most this many attempts start for one message (the daemon refuses more). */
export const MAX_ATTEMPTS = 3;

export type AttemptState = "starting" | "running" | "waiting" | "done" | "failed" | "stopped" | "limit";
export type AttemptWhy = "finished" | "failed" | "stopped" | "limit" | "cut_off";
export type AttemptCost = "free" | "plan" | "pay" | "";
export type AttemptTests = "passed" | "failed" | "none" | "not_run" | "";

const STATES: ReadonlySet<string> = new Set(["starting", "running", "waiting", "done", "failed", "stopped", "limit"]);
const FINAL: ReadonlySet<string> = new Set(["done", "failed", "stopped", "limit"]);
const WHYS: ReadonlySet<string> = new Set(["finished", "failed", "stopped", "limit", "cut_off"]);

/** An attempt's own approval card (the stream lane's, redacted arguments). */
export interface AttemptAsk {
  id: string;
  tool: string;
  args: Record<string, unknown>;
  costNote?: string;
  onceOnly?: boolean;
  onceReason?: OnceReason;
}

export interface AttemptUsage {
  inputTokens: number;
  outputTokens: number;
  /** Known only when priced (a metered call, or a plan's list price). */
  costUsd?: number;
  /** A subscription served it: the dollars are a list-price equivalent. */
  listPrice?: boolean;
}

/** One attempt, live or as the card keeps it. */
export interface AttemptView {
  id: string;
  label: string;
  provider: string;
  model: string;
  member: string;
  account: string;
  accountName: string;
  cost: AttemptCost;
  branch: string;
  state: AttemptState;
  why: AttemptWhy | "";
  words: string;
  steps: number;
  since: number | null;
  ended: number | null;
  ms: number | null;
  tests: AttemptTests;
  files: string[];
  usage: AttemptUsage;
  reply: string;
  error: string;
  /** A live card waiting for the user (never on a saved card). */
  ask: AttemptAsk | null;
  /** After Keep: this one was kept / its folder went to the trash. */
  kept: boolean;
  trashed: boolean;
  /** It has a branch Keep can act on (its git facts are on the card). */
  keepable: boolean;
}

/** The card the reply line keeps. */
export interface AttemptsCard {
  task: string;
  base: string;
  /** A session that worked in its folder directly: Keep MOVES it onto the
   *  kept attempt's branch (nothing is merged into the folder's branch). */
  adopt: boolean;
  items: AttemptView[];
  kept: string;
  mergedInto: string;
  keepNote: string;
}

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
const str = (v: unknown, max = 2000): string => (typeof v === "string" ? v.slice(0, max) : "");
const int = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) && v >= 0 ? Math.round(v) : null;

function decodeUsage(raw: unknown): AttemptUsage {
  const r = rec(raw);
  const out: AttemptUsage = { inputTokens: int(r?.input_tokens) ?? 0, outputTokens: int(r?.output_tokens) ?? 0 };
  const cost = r?.cost_usd;
  if (typeof cost === "number" && Number.isFinite(cost) && cost >= 0) out.costUsd = cost;
  if (r?.list_price_equivalent === true) out.listPrice = true;
  return out;
}

function decodeAsk(raw: unknown): AttemptAsk | null {
  const r = rec(raw);
  const id = str(r?.id, 120);
  const tool = str(r?.tool, 200);
  if (!r || !id || !tool) return null;
  const ask: AttemptAsk = { id, tool, args: rec(r.args) ?? {} };
  const cost = str(r.cost_note, 300);
  if (cost) ask.costNote = cost;
  if (r.once_only === true) {
    ask.onceOnly = true;
    const reason = onceReasonOf(r.once_reason);
    if (reason) ask.onceReason = reason;
  }
  return ask;
}

function blank(id: string): AttemptView {
  return {
    id,
    label: "",
    provider: "",
    model: "",
    member: "",
    account: "",
    accountName: "",
    cost: "",
    branch: "",
    state: "starting",
    why: "",
    words: "",
    steps: 0,
    since: null,
    ended: null,
    ms: null,
    tests: "",
    files: [],
    usage: { inputTokens: 0, outputTokens: 0 },
    reply: "",
    error: "",
    ask: null,
    kept: false,
    trashed: false,
    keepable: false,
  };
}

/** Fold one row's data (a live frame, or a saved item) into ``base``. */
function fold(base: AttemptView, r: Record<string, unknown>): AttemptView {
  const a: AttemptView = { ...base };
  const take = (key: keyof AttemptView, raw: unknown, max: number) => {
    const v = str(raw, max);
    if (v) (a[key] as string) = v;
  };
  take("label", r.label, 80);
  take("provider", r.provider, 120);
  take("model", r.model, 200);
  take("member", r.member, 80);
  take("account", r.account, 120);
  take("accountName", r.account_name, 80);
  take("branch", r.branch, 200);
  take("words", r.words, 600);
  take("reply", r.reply, 1200);
  take("error", r.error, 400);
  if (r.cost === "free" || r.cost === "plan" || r.cost === "pay") a.cost = r.cost;
  if (typeof r.state === "string" && STATES.has(r.state)) a.state = r.state as AttemptState;
  if (typeof r.why === "string" && WHYS.has(r.why)) a.why = r.why as AttemptWhy;
  const steps = int(r.steps);
  if (steps !== null) a.steps = Math.max(a.steps, steps);
  const since = int(r.since);
  if (since !== null && a.since === null) a.since = since;
  const ended = int(r.ended);
  if (ended !== null) a.ended = ended;
  const ms = int(r.ms);
  if (ms !== null) a.ms = ms;
  const tests = typeof r.tests === "string" ? r.tests : rec(r.finish)?.tests;
  if (tests === "passed" || tests === "failed" || tests === "none" || tests === "not_run") a.tests = tests;
  if (Array.isArray(r.files)) a.files = r.files.filter((f): f is string => typeof f === "string" && !!f).slice(0, 50);
  if ("usage" in r) a.usage = decodeUsage(r.usage);
  if ("ask" in r) a.ask = decodeAsk(r.ask);
  if (r.kept === true) a.kept = true;
  if (r.trashed === true) a.trashed = true;
  const git = rec(r.git);
  if (git && typeof git.worktree === "string" && typeof git.branch === "string") a.keepable = true;
  return a;
}

/** Fold one live `attempt` frame into the turn's rows (their order kept). */
export function applyAttemptFrame(prev: readonly AttemptView[], data: Record<string, unknown>): AttemptView[] {
  const id = str(data.id, 20);
  if (!id) return prev as AttemptView[];
  const at = prev.findIndex((a) => a.id === id);
  if (at === -1 && prev.length >= MAX_ATTEMPTS) return prev as AttemptView[];
  const next = fold(at === -1 ? blank(id) : prev[at], data);
  if (at === -1) return [...prev, next];
  const out = prev.slice();
  out[at] = next;
  return out;
}

/** The reply line's `attempts`, or null for junk / none. A saved card never
 *  waits on anyone (its `ask` dies here) and an attempt it holds is never
 *  "running" (the daemon settles each one before the line is written). */
export function decodeAttemptsCard(raw: unknown): AttemptsCard | null {
  const r = rec(raw);
  if (!r || !Array.isArray(r.items)) return null;
  const items: AttemptView[] = [];
  for (const item of r.items.slice(0, MAX_ATTEMPTS)) {
    const ir = rec(item);
    const id = str(ir?.id, 20);
    if (!ir || !id) continue;
    const a = fold(blank(id), ir);
    a.ask = null;
    if (!FINAL.has(a.state)) a.state = "stopped";
    items.push(a);
  }
  if (!items.length) return null;
  return {
    task: str(r.task, 600),
    base: str(r.base, 200),
    adopt: r.adopt === true,
    items,
    kept: str(r.kept, 20),
    mergedInto: str(r.merged_into, 200),
    keepNote: str(r.keep_note, 600),
  };
}

// ----------------------------------------------------------------- words

/** The model's name on a row: the daemon's label, and the account it ran on. */
export function attemptName(a: AttemptView): string {
  const who = a.label || a.model || a.provider || "A model";
  return a.accountName ? `${who} · ${a.accountName}` : who;
}

/** How a running row reads ("Working · 3 steps", "Waiting for your OK"). */
export function liveWords(a: AttemptView): string {
  if (a.state === "waiting") return "Waiting for your OK";
  if (a.state === "starting") return "Starting";
  if (FINAL.has(a.state)) return endWords(a);
  return a.steps > 0 ? `Working · ${a.steps} step${a.steps === 1 ? "" : "s"}` : "Working";
}

/** Why it stopped, in words (the daemon's own, else by its kind). */
export function endWords(a: AttemptView): string {
  if (a.words) return a.words;
  switch (a.why) {
    case "finished":
      return "Finished";
    case "limit":
      return "Stopped at an account limit";
    case "cut_off":
      return "Cut off when Iron Jarvis stopped";
    case "failed":
      return a.error ? `Failed: ${a.error}` : "Failed";
    default:
      return "Stopped";
  }
}

/** The finish check's tests, in words. */
export function testsWords(t: AttemptTests): string {
  switch (t) {
    case "passed":
      return "Tests passed";
    case "failed":
      return "Tests failed";
    case "not_run":
      return "Tests not run";
    case "none":
      return "No tests found";
    default:
      return "Not checked";
  }
}

export function filesWords(files: readonly string[]): string {
  if (!files.length) return "No files changed";
  return files.length === 1 ? "1 file changed" : `${files.length} files changed`;
}

/** "42 s", "3 min 5 s", "1 h 2 min". */
export function timeWords(ms: number | null): string {
  if (ms === null) return "—";
  const s = Math.max(0, Math.round(ms / 1000));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return s % 60 ? `${m} min ${s % 60} s` : `${m} min`;
  const h = Math.floor(m / 60);
  return m % 60 ? `${h} h ${m % 60} min` : `${h} h`;
}

function tokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10_000 ? 0 : 1)}k` : `${n}`;
}

function dollars(v: number): string {
  if (v > 0 && v < 0.01) return "<$0.01";
  return `$${v.toFixed(2)}`;
}

/** Usage or cost, in the app's money words: a plan's dollars are a LIST-
 *  PRICE equivalent (never spend), pay per use is metered, a local model is
 *  free. Tokens beside it. */
export function usageWords(a: AttemptView): string {
  const t = `${tokens(a.usage.inputTokens)} in · ${tokens(a.usage.outputTokens)} out`;
  if (a.cost === "free") return `Free (on your own hardware) · ${t}`;
  const cost = a.usage.costUsd;
  if (a.cost === "plan") {
    return cost !== undefined ? `≈${dollars(cost)} at list price (your plan) · ${t}` : `Your plan · ${t}`;
  }
  if (cost !== undefined) return `${dollars(cost)} · ${t}`;
  return t;
}

/** The one line under the card before Keep is pressed. */
export function cardLead(card: AttemptsCard): string {
  if (card.kept) return "";
  const keepable = card.items.some((a) => a.keepable && (a.why === "finished" || a.files.length > 0));
  if (!keepable) return "No attempt finished or changed a file, so there is nothing to keep.";
  return card.adopt
    ? "Keep one: this session then works on its branch. The other attempts' folders go to the trash; their branches stay."
    : "Keep one: only its work joins this session's branch. The other attempts' folders go to the trash; their branches stay.";
}

/** What a row says after Keep. */
export function afterKeepWords(card: AttemptsCard, a: AttemptView): string {
  if (!card.kept) return "";
  if (a.id === card.kept) {
    if (card.adopt) return "Kept: this session works on its branch now";
    return card.mergedInto ? `Kept: merged into ${card.mergedInto}` : "Kept";
  }
  return a.trashed ? "Folder in the trash · branch kept" : "Not kept · branch kept";
}

// ----------------------------------------------------------------- Keep

export interface KeepAnswer {
  ok: boolean;
  /** The daemon's sentence: what was kept, or why not. */
  words: string;
  note: string;
}

/** POST /build/sessions/{id}/attempts/keep — a person's press. */
export async function keepAttempt(threadId: string, turnId: string, attempt: string): Promise<KeepAnswer> {
  try {
    const out = rec(
      await post<unknown>(`/build/sessions/${encodeURIComponent(threadId)}/attempts/keep`, {
        turn_id: turnId,
        attempt,
      }),
    );
    return { ok: true, words: str(out?.words, 600), note: str(out?.note, 600) };
  } catch (e) {
    const words =
      e instanceof ApiError && e.status === 404
        ? "Those attempts are not there any more. Reload the session."
        : e instanceof Error && e.message
          ? e.message
          : "The attempt could not be kept.";
    return { ok: false, words, note: "" };
  }
}

// ----------------------------------------------------------------- Try on…

/** A model the "Try on…" control picked (a model-menu row). */
export interface TryOnPick {
  provider: string;
  model: string;
  label: string;
}

/** A model-menu row as the control reads it. */
export interface TryOnRow {
  provider: string;
  model: string;
  label?: string;
  name?: string | null;
  available?: boolean;
  on_this_network?: boolean;
}

/** The rows the control offers: connected ones (never the demo model); under
 *  Local only, only those that stay on this network. */
export function tryOnChoices(rows: readonly TryOnRow[], localOnly: boolean): TryOnRow[] {
  return rows.filter(
    (r) => r.provider !== "mock" && r.available !== false && (!localOnly || r.on_this_network === true),
  );
}

export function sameRow(a: { provider: string; model: string }, b: { provider: string; model: string }): boolean {
  return a.provider === b.provider && a.model === b.model;
}

/** Tick or untick one row; at most MAX_ATTEMPTS stay ticked. */
export function togglePick(picks: readonly TryOnPick[], row: TryOnPick): TryOnPick[] {
  if (picks.some((p) => sameRow(p, row))) return picks.filter((p) => !sameRow(p, row));
  if (picks.length >= MAX_ATTEMPTS) return picks.slice();
  return [...picks, row];
}

/** The body field the turn route reads. */
export function tryOnBody(picks: readonly TryOnPick[]): { provider: string; model: string }[] {
  return picks.map((p) => ({ provider: p.provider, model: p.model }));
}

/** The control's list, FIXED to the window above its chip (a portal: the
 *  session's columns clip what overflows them) and always inside the window —
 *  16 px from either side, as wide as fits up to 320 px, its height capped to
 *  the room above the chip. Pure. */
export function tryOnMenuPosition(
  anchor: { left: number; top: number },
  viewport: { width: number; height: number },
): { left: number; bottom: number; width: number; maxHeight: number } {
  const EDGE = 16;
  const width = Math.max(0, Math.min(320, viewport.width - 2 * EDGE));
  const left = Math.max(EDGE, Math.min(anchor.left, viewport.width - width - EDGE));
  const bottom = Math.max(EDGE, viewport.height - anchor.top + 8);
  const maxHeight = Math.max(120, anchor.top - 8 - EDGE);
  return { left: Math.round(left), bottom: Math.round(bottom), width: Math.round(width), maxHeight: Math.round(maxHeight) };
}

/** The chip's words once models are picked. */
export function tryOnChipWords(picks: readonly TryOnPick[]): string {
  if (!picks.length) return "Try on…";
  return picks.length === 1 ? `Try on ${picks[0].label}` : `Try on ${picks.length} models`;
}
