/**
 * Build's IJ harness SESSIONS (v1.369.0, plan Phase 6) — the screen's reading
 * of the Phase 4 backend (`/build/sessions…`, daemon routes/build.py).
 *
 * An IJ harness session IS a chat thread with `surface = "build"`; the DAEMON
 * writes its transcript (the question when a turn starts, the reply when it
 * ends), so the screen never saves a session's messages — it re-reads the
 * session when a turn ends and adopts the lines the daemon wrote
 * (`PUT /chat/threads/{id}` answers 409 for a session).
 *
 * Every decoder WHITELISTS: a field the daemon did not promise, or one of the
 * wrong type, dies here. Kept OUTSIDE lib/api.ts (~71 test files mock it
 * wholesale) and lib/useChatStream.ts (~50 mock it with a fixed export list).
 */

import { ApiError, get, patch, post, del } from "@/lib/api";
import { BUILD_SESSION_PREFIX } from "@/lib/buildLinks";
import type { PlanStepState, PlanView } from "@/lib/planChecklist";
import type { PaneMessage } from "@/components/terminal/paneWork";
import { decodeQueuedItem, type QueuedItem } from "@/lib/buildComposer";

// Where a Build session opens lives in the import-free lib/buildLinks (the
// bell and the Activity rows read it); re-exported here for the screen.
export {
  BUILD_SESSION_PREFIX,
  buildSessionHref,
  buildThreadOf,
  isBuildSessionId,
  sessionLinkFor,
} from "@/lib/buildLinks";

// ----------------------------------------------------------------- the list

export interface BuildSessionRow {
  id: string;
  title: string;
  updatedAt: string | null;
  running: boolean;
  waiting: boolean;
  archived: boolean;
}

export interface BuildFolder {
  path: string;
  name: string;
  isProject: boolean;
  projectId: string | null;
  sessions: BuildSessionRow[];
}

function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown, max = 2000): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

function decodeRow(raw: unknown): BuildSessionRow | null {
  const r = rec(raw);
  const id = str(r?.id, 120).trim();
  if (!r || !id) return null;
  return {
    id,
    title: str(r.title, 400).trim() || "New session",
    updatedAt: typeof r.updated_at === "string" ? r.updated_at : null,
    running: r.running === true,
    waiting: r.waiting === true,
    archived: r.archived === true,
  };
}

/** GET /build/sessions → the folders, in the daemon's order (newest first). */
export function decodeFolders(raw: unknown): BuildFolder[] {
  const list = rec(raw)?.folders;
  if (!Array.isArray(list)) return [];
  const out: BuildFolder[] = [];
  for (const f of list) {
    const r = rec(f);
    const path = str(r?.path, 1000);
    if (!r || !path) continue;
    const sessions = (Array.isArray(r.sessions) ? r.sessions : [])
      .map(decodeRow)
      .filter((x): x is BuildSessionRow => x !== null);
    out.push({
      path,
      name: str(r.name, 200).trim() || path,
      isProject: r.is_project === true,
      projectId: typeof r.project_id === "string" && r.project_id ? r.project_id : null,
      sessions,
    });
  }
  return out;
}

// ------------------------------------------------------------- one session

export type BuildMode = "act" | "plan";

export interface BuildPlanStep {
  step: string;
  status: "pending" | "in_progress" | "completed";
}

export interface BuildPlan {
  text: string;
  steps: BuildPlanStep[];
  approvedAt: string | null;
}

export interface BuildSetup {
  provider: string;
  model: string;
  reasoning: string;
  approvalMode: string;
  localOnly: boolean;
  grantedTools: string[];
  /** Phase 5: the account the session runs on ("" = none), and its name. */
  account: string;
  accountName: string;
}

/** A waiting message (Ctrl+Enter): its words and, since v1.373.0, its own
 *  skill and files (lib/buildComposer decodes them). */
export type { QueuedItem } from "@/lib/buildComposer";

export interface BuildSessionView extends BuildSessionRow {
  folder: string;
  folderName: string;
  isProject: boolean;
  projectId: string | null;
  mode: BuildMode;
  plan: BuildPlan | null;
  setup: BuildSetup;
  messages: BuildMessage[];
  sessionId: string;
  queued: QueuedItem[];
  /** The running turn's id ("" = none) — rejoin with GET /chat/turns/{id}/stream. */
  turnId: string;
  /** Phase 5 (lib/buildAccounts decodes these): the session's account (a
   *  row, or {id, name, status}), its vendor ("" = a local model or an API
   *  key — no account), the limit card it is parked on (kept across a
   *  reload) and the armed "Resume when ready" wait. Raw. */
  account: unknown;
  accountVendor: string;
  accountLimit: unknown;
  accountWait: unknown;
  /** The daemon keeps accounts on the view (Phase 5): its limit card and
   *  wait are then the truth (null = none), not "an older Iron Jarvis". */
  knowsAccounts: boolean;
}

/** A transcript line: the chat page's own message shape (the daemon keeps
 *  only the keys of `harness/sessions.LINE_KEYS`), plus `turnId` / `steer` /
 *  `error`, which a session's lines carry. */
export type BuildMessage = PaneMessage & {
  turnId?: string;
  steer?: boolean;
  error?: string;
  truncated?: boolean;
  folderRules?: string[];
  /** Phase 5: the daemon's own "carry on" question (Resume when ready,
   *  Choose account) — not shown, like the chat's hidden Continue turn. */
  continuation?: boolean;
  /** Phase 5: the account-limit card the reply ended on (raw). */
  accountLimit?: unknown;
  /** v1.372.0: the helper agents the reply's turn started (raw; read by
   *  lib/buildHelpers decodeSavedHelpers). */
  helpers?: unknown;
  /** v1.373.0: on a question, the files picked with "@" in the session's
   *  folder, and the skill picked with "/" (what rode it beside the words). */
  filePaths?: string[];
  skill?: string;
};

/** The keys a line may carry (the daemon's LINE_KEYS). Anything else dies. */
const LINE_KEYS = [
  "role",
  "content",
  "at",
  "turnId",
  "attachmentNames",
  "attachmentPaths",
  "steer",
  "route",
  "toolsUsed",
  "documents",
  "deniedTools",
  "usage",
  "steps",
  "thinking",
  "truncated",
  "interrupted",
  "error",
  "folderRules",
  "approved",
  "refit",
  "doors",
  "trust",
  "trustReason",
  "trustNote",
  "timing",
  // Phase 5 (v1.368.0): a continuation question, and a reply's limit card.
  "continuation",
  "accountLimit",
  // v1.372.0 (part 2, Phase 1): the helper agents a reply's turn started.
  "helpers",
  // v1.373.0 (part 2, Phase 2): what rode a question — "@" files, a "/" skill.
  "filePaths",
  "skill",
] as const;

export function decodeMessage(raw: unknown): BuildMessage | null {
  const r = rec(raw);
  if (!r) return null;
  const role = r.role;
  if (role !== "user" && role !== "assistant") return null;
  const out: Record<string, unknown> = {};
  for (const k of LINE_KEYS) if (k in r && r[k] !== null && r[k] !== undefined) out[k] = r[k];
  out.role = role;
  out.content = typeof r.content === "string" ? r.content : "";
  return out as unknown as BuildMessage;
}

function decodePlan(raw: unknown): BuildPlan | null {
  const r = rec(raw);
  if (!r) return null;
  const steps: BuildPlanStep[] = [];
  for (const s of Array.isArray(r.steps) ? r.steps : []) {
    const st = rec(s);
    const text = str(st?.step, 500).trim();
    const status = st?.status;
    if (!text) continue;
    steps.push({
      step: text,
      status: status === "in_progress" || status === "completed" ? status : "pending",
    });
  }
  const text = str(r.text, 20_000);
  if (!text.trim() && !steps.length) return null;
  return { text, steps, approvedAt: typeof r.approved_at === "string" ? r.approved_at : null };
}

function decodeSetup(raw: unknown): BuildSetup {
  const r = rec(raw) ?? {};
  return {
    provider: str(r.provider, 120),
    model: str(r.model, 300),
    reasoning: str(r.reasoning, 20),
    approvalMode: str(r.approval_mode, 40),
    localOnly: r.local_only === true,
    grantedTools: Array.isArray(r.granted_tools)
      ? r.granted_tools.filter((t): t is string => typeof t === "string" && !!t)
      : [],
    account: str(r.account, 120),
    accountName: str(r.account_name, 80),
  };
}

/** GET /build/sessions/{id} (and POST/PATCH's answer) → the view, or null. */
export function decodeSessionView(raw: unknown): BuildSessionView | null {
  const r = rec(raw);
  const row = decodeRow(raw);
  if (!r || !row) return null;
  const folder = str(r.folder, 1000);
  return {
    ...row,
    folder,
    folderName: str(r.folder_name, 200).trim() || folder,
    isProject: r.is_project === true,
    projectId: typeof r.project_id === "string" && r.project_id ? r.project_id : null,
    mode: r.mode === "plan" ? "plan" : "act",
    plan: decodePlan(r.plan),
    setup: decodeSetup(r.setup),
    messages: (Array.isArray(r.messages) ? r.messages : [])
      .map(decodeMessage)
      .filter((m): m is BuildMessage => m !== null),
    sessionId: str(r.session_id, 140) || `${BUILD_SESSION_PREFIX}${row.id}`,
    // v1.373.0: a waiting message keeps its own skill and files.
    queued: (Array.isArray(r.queued) ? r.queued : [])
      .map(decodeQueuedItem)
      .filter((q): q is QueuedItem => q !== null),
    turnId: str(r.turn_id, 80),
    account: r.account ?? null,
    accountVendor: r.account_vendor === "claude" || r.account_vendor === "codex" ? r.account_vendor : "",
    accountLimit: r.account_limit ?? null,
    accountWait: r.account_wait ?? null,
    knowsAccounts: "account_limit" in r || "account_wait" in r || "account" in r,
  };
}

/** The lines a session SHOWS: its transcript without the daemon's own
 *  continuation questions. */
export function shownMessages(messages: readonly BuildMessage[]): BuildMessage[] {
  return messages.filter((m) => m.continuation !== true);
}

// ------------------------------------------------------------- the fetchers

export async function listSessions(archived = false): Promise<BuildFolder[]> {
  return decodeFolders(await get<unknown>(archived ? "/build/sessions?archived=1" : "/build/sessions"));
}

export async function getSession(id: string): Promise<BuildSessionView | null> {
  return decodeSessionView(await get<unknown>(`/build/sessions/${encodeURIComponent(id)}`));
}

export async function createSession(folder: string, extra: Record<string, unknown> = {}): Promise<BuildSessionView | null> {
  return decodeSessionView(await post<unknown>("/build/sessions", { folder, ...extra }));
}

/** PARTIAL: an omitted field keeps its value. */
export async function patchSession(id: string, body: Record<string, unknown>): Promise<BuildSessionView | null> {
  return decodeSessionView(await patch<unknown>(`/build/sessions/${encodeURIComponent(id)}`, body));
}

export function renameFolder(path: string, name: string): Promise<unknown> {
  return post("/build/folders/rename", { path, name });
}

export function deleteSession(id: string): Promise<unknown> {
  return del(`/build/sessions/${encodeURIComponent(id)}`);
}

/** Archive: the chat archive's rules — a 409 lists what is still running
 *  (thrown as an ApiError); `stop` stops it first. */
export function archiveSession(id: string, stop = false): Promise<unknown> {
  return post(`/build/sessions/${encodeURIComponent(id)}/archive`, stop ? { stop: true } : {});
}

export function unarchiveSession(id: string): Promise<unknown> {
  return post(`/build/sessions/${encodeURIComponent(id)}/unarchive`, {});
}

/** The user's answer to a question the session's turn asked (`ask_user`). */
export function answerQuestion(
  sessionThreadId: string,
  askId: string,
  answer: string | string[] | Record<string, string | string[]> | null,
): Promise<unknown> {
  return post(
    `/build/sessions/${encodeURIComponent(sessionThreadId)}/answer`,
    answer === null ? { ask_id: askId, skip: true } : { ask_id: askId, answer },
  );
}

/** A 404 from a route an older Iron Jarvis does not have. */
export function isMissing(e: unknown): boolean {
  return e instanceof ApiError && e.status === 404;
}

// --------------------------------------------------------- the model's question

export interface AskUserOption {
  label: string;
  description: string;
}

export interface AskUserQuestion {
  id: string;
  question: string;
  header: string;
  options: AskUserOption[];
  multi: boolean;
}

export type AskUserOutcome = "answered" | "skipped" | "stopped";

/** A question the session's model asked (the `ask_user` frame). */
export interface AskUserAsk {
  id: string;
  callId: string;
  questions: AskUserQuestion[];
  outcome?: AskUserOutcome;
}

const MAX_QUESTIONS = 4;
const MAX_OPTIONS = 8;

/** The `ask_user` frame, or null when it is not one. */
export function decodeAskUser(raw: unknown): AskUserAsk | null {
  const r = rec(raw);
  const id = str(r?.id, 80).trim();
  if (!r || !id || !Array.isArray(r.questions)) return null;
  const questions: AskUserQuestion[] = [];
  for (const [n, q] of r.questions.slice(0, MAX_QUESTIONS).entries()) {
    const qr = rec(q);
    const text = str(qr?.question, 1000).trim();
    if (!qr || !text) continue;
    const options: AskUserOption[] = [];
    for (const o of (Array.isArray(qr.options) ? qr.options : []).slice(0, MAX_OPTIONS)) {
      const or = rec(o);
      const label = str(or?.label, 200).trim();
      if (label) options.push({ label, description: str(or?.description, 500).trim() });
    }
    questions.push({
      id: str(qr.id, 40).trim() || `q${n + 1}`,
      question: text,
      header: str(qr.header, 60).trim(),
      options,
      multi: qr.multi === true,
    });
  }
  if (!questions.length) return null;
  return { id, callId: str(r.call_id, 120), questions };
}

/** `mcp_resolved {id, kind: "ask_user", outcome}` → {id, outcome}, else null. */
export function decodeAskResolved(raw: unknown): { id: string; outcome: AskUserOutcome } | null {
  const r = rec(raw);
  if (!r || r.kind !== "ask_user") return null;
  const id = str(r.id, 80).trim();
  if (!id) return null;
  const o = r.outcome;
  return { id, outcome: o === "answered" || o === "skipped" ? o : "stopped" };
}

// ------------------------------------------------------------------ the plan

const STATUS_STATE: Record<string, PlanStepState> = {
  pending: "waiting",
  in_progress: "running",
  completed: "done",
};

function planView(steps: { step: string; status: string }[]): PlanView | null {
  if (!steps.length) return null;
  return {
    steps: steps.map((s, index) => ({ index, goal: s.step, state: STATUS_STATE[s.status] ?? "waiting" })),
    total: steps.length,
  };
}

/** The steps of a `plan_update` call's arguments (`plan`, or Claude Code's
 *  `todos`; `step` or `content`), or null when they are not a plan. */
export function planFromArgs(args: unknown): PlanView | null {
  const a = rec(args);
  const list = a ? (a.plan ?? a.todos) : null;
  if (!Array.isArray(list)) return null;
  const steps: { step: string; status: string }[] = [];
  for (const item of list.slice(0, 50)) {
    const r = rec(item);
    const text = str(r?.step ?? r?.content, 500).trim();
    const status = str(r?.status, 20).trim().toLowerCase();
    if (text) steps.push({ step: text, status });
  }
  return planView(steps);
}

/** The newest `plan_update` of the running turn (its live tool cards), or null. */
export function planFromCards(cards: readonly { name: string; args?: Record<string, unknown> }[]): PlanView | null {
  for (let i = cards.length - 1; i >= 0; i -= 1) {
    if (cards[i].name !== "plan_update") continue;
    const p = planFromArgs(cards[i].args);
    if (p) return p;
  }
  return null;
}

/** The newest `plan_update` a saved reply kept (its step's masked input,
 *  when it is whole JSON), or null — never a guess from a cut input. */
export function planFromMessages(messages: readonly BuildMessage[]): PlanView | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const steps = messages[i].steps;
    if (!Array.isArray(steps)) continue;
    for (let j = steps.length - 1; j >= 0; j -= 1) {
      const st = steps[j] as { name?: unknown; ok?: unknown; detail?: { input?: unknown } };
      if (st?.name !== "plan_update" || st.ok === false) continue;
      const input = st.detail?.input;
      if (typeof input !== "string") continue;
      try {
        const p = planFromArgs(JSON.parse(input));
        if (p) return p;
      } catch {
        /* a cut input is not a plan */
      }
    }
  }
  return null;
}

/** The plan the session KEEPS (an approved plan's steps), or null. */
export function planFromSession(plan: BuildPlan | null): PlanView | null {
  return plan ? planView(plan.steps) : null;
}

// ------------------------------------------------------------- the preview

const PRIVATE_V4 = [
  /^127\./,
  /^10\./,
  /^192\.168\./,
  /^172\.(1[6-9]|2\d|3[01])\./,
  /^169\.254\./,
  /^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\./,
];

/** May this address be previewed here? Only a page on this PC or the user's
 *  own network (the plan's decision): http(s), and a host that is loopback,
 *  a private address, or a local name. */
export function isPreviewAddress(raw: string): boolean {
  let u: URL;
  try {
    u = new URL(raw.trim());
  } catch {
    return false;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return false;
  const host = u.hostname.toLowerCase().replace(/^\[|\]$/g, "");
  if (host === "localhost" || host === "::1" || host === "0.0.0.0") return true;
  if (/^\d+\.\d+\.\d+\.\d+$/.test(host)) return PRIVATE_V4.some((rx) => rx.test(host));
  if (/^(fc|fd)[0-9a-f]{2}:/.test(host) || host.startsWith("fe80:")) return true;
  return (
    host.endsWith(".localhost") ||
    host.endsWith(".local") ||
    host.endsWith(".lan") ||
    host.endsWith(".home.arpa") ||
    host.endsWith(".internal") ||
    host.endsWith(".ts.net") ||
    (!host.includes(".") && !host.includes(":") && host.length > 0)
  );
}

const ADDRESS_RX = /\bhttps?:\/\/(?:localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1\])(?::\d{2,5})?(?:\/[^\s"'<>)\]`]*)?/gi;

/** The local addresses a session's own output named (a dev server printing
 *  "Local: http://localhost:5173/"), newest first, at most `max` — read off
 *  the transcript's replies and step outputs, never guessed. 0.0.0.0 is
 *  opened as localhost. */
export function addressesIn(messages: readonly BuildMessage[], max = 3): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const take = (text: unknown) => {
    if (typeof text !== "string" || !text) return;
    const found = text.match(ADDRESS_RX) ?? [];
    for (let i = found.length - 1; i >= 0; i -= 1) {
      const url = found[i].replace("0.0.0.0", "localhost").replace(/[.,;:]+$/, "");
      if (seen.has(url)) continue;
      seen.add(url);
      out.push(url);
    }
  };
  for (let i = messages.length - 1; i >= 0 && out.length < max; i -= 1) {
    const m = messages[i];
    const steps = Array.isArray(m.steps) ? m.steps : [];
    for (let j = steps.length - 1; j >= 0; j -= 1) {
      take((steps[j] as { detail?: { output?: unknown } })?.detail?.output);
    }
    if (m.role === "assistant") take(m.content);
  }
  return out.slice(0, max);
}

// ------------------------------------------------------- steer notes kept

const STEERS_PREFIX = "ij.build.steers.";

export interface SentSteer {
  turnId: string;
  text: string;
}

/** The steer notes this window sent to a session's turns (so a note the
 *  turn never read goes back in the composer, even when the turn ended
 *  while nobody was watching it). */
export function readSentSteers(threadId: string): SentSteer[] {
  try {
    const raw = window.localStorage.getItem(STEERS_PREFIX + threadId);
    const list = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(list)
      ? list
          .map((x) => rec(x))
          .filter((x): x is Record<string, unknown> => !!x)
          .map((x) => ({ turnId: str(x.turnId, 80), text: str(x.text, 20_000) }))
          .filter((x) => x.turnId && x.text.trim())
      : [];
  } catch {
    return [];
  }
}

export function writeSentSteers(threadId: string, list: SentSteer[]): void {
  try {
    if (list.length) window.localStorage.setItem(STEERS_PREFIX + threadId, JSON.stringify(list.slice(-20)));
    else window.localStorage.removeItem(STEERS_PREFIX + threadId);
  } catch {
    /* private mode — the notes are still restored for a watched turn */
  }
}

/** Which of the notes sent to `turnId` the turn never read: the transcript's
 *  steer lines of that turn are the ones it read (the daemon writes a note as
 *  the user's line the moment a round boundary takes it); `unread` (the done
 *  frame's own list, when the turn was watched) counts too. Order kept. */
export function unreadSteersFor(
  sent: readonly SentSteer[],
  turnId: string,
  messages: readonly BuildMessage[],
  unread: readonly string[] = [],
): string[] {
  const read = new Set(
    messages.filter((m) => m.steer === true && m.turnId === turnId).map((m) => m.content.trim()),
  );
  const out: string[] = [];
  for (const s of sent) {
    if (s.turnId !== turnId) continue;
    if (read.has(s.text.trim())) continue;
    out.push(s.text);
  }
  for (const u of unread) if (!out.some((o) => o.trim() === u.trim())) out.push(u);
  return out;
}

/** Put notes back in the box: kept under what the user has typed. */
export function withSteersRestored(box: string, notes: readonly string[]): string {
  const add = notes.map((n) => n.trim()).filter(Boolean);
  if (!add.length) return box;
  const cur = box.trim();
  return [cur, ...add.filter((n) => n !== cur)].filter(Boolean).join("\n\n");
}

// --------------------------------------------------- the session's terminal

const TERM_PREFIX = "ij.build.session.terminal.";

/** The Build terminal this window opened for a session (its terminal tab). */
export function readSessionTerminal(threadId: string): string | null {
  try {
    return window.localStorage.getItem(TERM_PREFIX + threadId);
  } catch {
    return null;
  }
}

export function writeSessionTerminal(threadId: string, terminalId: string | null): void {
  try {
    if (terminalId) window.localStorage.setItem(TERM_PREFIX + threadId, terminalId);
    else window.localStorage.removeItem(TERM_PREFIX + threadId);
  } catch {
    /* private mode — the tab offers to open one again */
  }
}

/** The session this window last had open (Build remembers it). */
export const LAST_SESSION_KEY = "ij.build.session";

// ------------------------------------------------------------ Build's view

/** Build's two views: the Terminals rail, or the IJ harness. */
export type BuildView = "rail" | "harness";

/** The view a saved `ij.build.shape` opens: the IJ harness for "harness" and
 *  for the retired free-form "canvas" (v1.369.0 — the IJ harness took its
 *  place in the switch); the Terminals rail for anything else. */
export function viewFromSaved(saved: string | null | undefined): BuildView {
  return saved === "harness" || saved === "canvas" ? "harness" : "rail";
}
