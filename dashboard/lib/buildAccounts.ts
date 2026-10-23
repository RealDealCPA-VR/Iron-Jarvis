/**
 * Build's IJ harness ACCOUNTS (v1.369.0, plan Phase 6) — the screen's side of
 * Phase 5 ("accounts per session, through Iron-Proxy", v1.368.0).
 *
 * Every Phase 5 shape the screen reads lives in THIS ONE FILE, each with a
 * decoder that whitelists it, so the account pieces have one place to change:
 *
 *   GET  /build/accounts                 {claude: [row], codex: [row],
 *                                         iron_proxy: {enabled, running,
 *                                         needs_update, note}}
 *        row = {id, name, vendor, vendor_label, provider, default, plan,
 *               plan_label, status: ready | usage | limit | needs_sign_in,
 *               usage?: {percent, window, window_mins, resets_at},
 *               resets_at?, limit_kind?}
 *        (the CACHED snapshot: cheap, so it is read again while the list is
 *        open)
 *   POST /build/accounts/{id}/default    → the list again
 *   PATCH /build/sessions/{id} {account} → the session ("" = none)
 *   POST /build/sessions/{id}/account {account, continue: true}
 *                                        → {session, turn_id}; follow turn_id
 *                                          with GET /chat/turns/{id}/stream
 *   POST|DELETE /build/sessions/{id}/resume-when-ready → the session
 *   the session view: account (a row, or {id, name, status: null |
 *        "missing"}), account_vendor ("claude" | "codex" | ""),
 *        account_limit (the card, kept across a reload), account_wait (set
 *        while "Resume when ready" is armed)
 *   SSE  account_limit {account, account_name, provider, vendor, kind,
 *        resets_at, resumable, turn_id} right before the turn's last frame;
 *        kind = usage | rate | busy | other | signin | missing | disabled
 *   a reply line's `accountLimit` (the same card), a continuation question's
 *        `continuation: true` (hidden); done.route.account {id, name, note?}
 *
 * THE OLDER-DAEMON RULE: a 404 on GET /build/accounts hides the chip and the
 * list (`fetchAccounts` answers null); nothing is guessed. A PERCENT IS THE
 * VENDOR'S: a usage bar is drawn only for a number the daemon sent.
 *
 * Kept OUTSIDE lib/api.ts: ~71 test files mock that module wholesale.
 */

import { ApiError, del, get, patch, post } from "@/lib/api";
import { localTime } from "@/lib/ironProxy";

/** The two vendors whose CLI accounts a Build session can run on. */
export type AccountVendor = "claude" | "codex";
export const ACCOUNT_VENDORS: readonly AccountVendor[] = ["claude", "codex"];

/** The account list's group headings (the mockup's words). */
export const GROUP_TITLES: Record<AccountVendor, string> = {
  claude: "Claude Code accounts",
  codex: "Codex accounts",
};

/** The vendor's product name, when a row does not carry its own label. */
export const VENDOR_NAMES: Record<AccountVendor, string> = {
  claude: "Claude Code",
  codex: "Codex",
};

/** A row's status: Phase 5's four, plus what a SESSION's account may be
 *  ("off" on the Iron-Proxy card, "missing" from Iron-Proxy, "unknown" when
 *  Iron-Proxy is off or not read yet — the header still names it). */
export type AccountStatus = "ready" | "usage" | "limit" | "needs_sign_in" | "off" | "missing" | "unknown";

export interface AccountUsage {
  /** The vendor's own percent used (0..100). */
  percent: number;
  /** The window that percent is over, in words ("7 days"). */
  window: string;
  resetsAt: string | null;
}

export interface BuildAccount {
  id: string;
  name: string;
  isDefault: boolean;
  vendor: AccountVendor | null;
  /** "Claude Code" / "Codex" (the daemon's own label). */
  vendorLabel: string;
  /** "Max" / "Plus" — the plan Iron-Proxy read; "" when not known. */
  planLabel: string;
  status: AccountStatus;
  /** When a reached limit resets (ISO), else null. */
  resetsAt: string | null;
  /** Why it is parked: usage | rate | busy | other ("" when not parked). */
  limitKind: string;
  /** The window nearest its limit, when the vendor sent a percent. */
  usage: AccountUsage | null;
}

export type AccountGroups = Record<AccountVendor, BuildAccount[]>;

/** Iron-Proxy as the list sees it: one sentence when it cannot serve. */
export interface IronProxyState {
  enabled: boolean;
  running: boolean;
  needsUpdate: boolean;
  note: string;
}

export interface AccountsView {
  groups: AccountGroups;
  ironProxy: IronProxyState | null;
}

/** Why an account could not serve a turn. */
export type LimitKind = "usage" | "rate" | "busy" | "other" | "signin" | "missing" | "disabled" | "unknown";

/** A reached limit: the turn's `account_limit` frame, the view's
 *  `account_limit` and a reply line's `accountLimit` are the same card. */
export interface AccountLimit {
  account: string;
  accountName: string;
  provider: string;
  vendor: AccountVendor | null;
  kind: LimitKind;
  resetsAt: string | null;
  /** False = no reset will bring it back (Resume is not offered); null =
   *  not said (offered for the kinds a reset ends). */
  resumable: boolean | null;
  turnId: string;
}

/** The armed "Resume when ready" wait (the view's `account_wait`). */
export interface AccountWait {
  account: string;
  accountName: string;
  resetsAt: string | null;
}

function rec(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown, max = 200): string {
  return typeof v === "string" ? v.trim().slice(0, max) : "";
}

function isoOrNull(v: unknown): string | null {
  const s = str(v, 64);
  return s && !Number.isNaN(Date.parse(s)) ? s : null;
}

/** A vendor from the daemon's word ("claude" / "codex"), else null. */
export function vendorOf(raw: unknown): AccountVendor | null {
  const v = str(raw, 40).toLowerCase();
  return v === "claude" || v === "codex" ? v : null;
}

/** "7 days" from words, or from minutes when only a number came. */
export function windowWords(words: unknown, mins?: unknown): string {
  const w = str(words, 40);
  if (w) return w;
  if (typeof mins === "number" && Number.isFinite(mins) && mins > 0) {
    const m = Math.round(mins);
    if (m % 1440 === 0) return m === 1440 ? "1 day" : `${m / 1440} days`;
    if (m % 60 === 0) return m === 60 ? "1 hour" : `${m / 60} hours`;
    return `${m} minutes`;
  }
  return "";
}

function decodeUsage(raw: unknown): AccountUsage | null {
  const u = rec(raw);
  if (!u) return null;
  const p = u.percent;
  if (typeof p !== "number" || !Number.isFinite(p)) return null; // never a guess
  return {
    percent: Math.max(0, Math.min(100, p)),
    window: windowWords(u.window, u.window_mins),
    resetsAt: isoOrNull(u.resets_at),
  };
}

const STATUSES: readonly AccountStatus[] = ["ready", "usage", "limit", "needs_sign_in", "off", "missing"];

/** One account row (or a session's account), or null when it has no id. */
export function decodeAccount(raw: unknown, vendorHint?: AccountVendor | null): BuildAccount | null {
  const r = rec(raw);
  const id = str(r?.id, 120);
  if (!r || !id) return null;
  const vendor = vendorOf(r.vendor) ?? vendorHint ?? null;
  const status = STATUSES.find((s) => s === r.status) ?? "unknown";
  return {
    id,
    name: str(r.name, 80) || id,
    isDefault: r.default === true,
    vendor,
    vendorLabel: str(r.vendor_label, 40) || (vendor ? VENDOR_NAMES[vendor] : ""),
    planLabel: str(r.plan_label, 40),
    status,
    resetsAt: isoOrNull(r.resets_at),
    limitKind: str(r.limit_kind, 20),
    usage: decodeUsage(r.usage),
  };
}

function decodeIronProxy(raw: unknown): IronProxyState | null {
  const r = rec(raw);
  if (!r) return null;
  return {
    enabled: r.enabled === true,
    running: r.running === true,
    needsUpdate: r.needs_update === true,
    note: str(r.note, 400),
  };
}

/** GET /build/accounts (and Make default's answer) → the view, or null when
 *  the body is not one. */
export function decodeAccounts(raw: unknown): AccountsView | null {
  const r = rec(raw);
  if (!r) return null;
  if (!Array.isArray(r.claude) && !Array.isArray(r.codex)) return null;
  const groups: AccountGroups = { claude: [], codex: [] };
  for (const v of ACCOUNT_VENDORS) {
    for (const row of Array.isArray(r[v]) ? (r[v] as unknown[]) : []) {
      const a = decodeAccount(row, v);
      if (a && a.vendor === v) groups[v].push(a);
    }
  }
  return { groups, ironProxy: decodeIronProxy(r.iron_proxy) };
}

/** The accounts, or null when this Iron Jarvis has no account list (a 404:
 *  the chip and the list are hidden). Other failures throw. */
export async function fetchAccounts(): Promise<AccountsView | null> {
  try {
    return decodeAccounts(await get<unknown>("/build/accounts"));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

/** "Make default": that account is its vendor's Default. Answers the list. */
export async function makeDefaultAccount(id: string): Promise<AccountsView | null> {
  return decodeAccounts(await post<unknown>(`/build/accounts/${encodeURIComponent(id)}/default`, {}));
}

/** The session moves to `account` (from the next turn). Answers the session
 *  (raw — lib/buildSessions decodes it).
 *  v1.376.0: with `model`, the account AND the model in ONE request — a pick
 *  under an account in the model menu. The daemon sets both or, refusing the
 *  account, neither (routes/build `patch_build_session`: the account decides
 *  the vendor, the user's model wins over the vendor's default). */
export function setSessionAccount(sessionId: string, account: string, model?: string): Promise<unknown> {
  return patch<unknown>(
    `/build/sessions/${encodeURIComponent(sessionId)}`,
    model === undefined ? { account } : { account, model },
  );
}

/** "Choose account" on the limit card: set it and continue at once.
 *  Answers the session (raw) and the continuing turn's id ("" = none). */
export async function chooseAccountAndContinue(
  sessionId: string,
  account: string,
): Promise<{ session: unknown; turnId: string }> {
  const res = rec(
    await post<unknown>(`/build/sessions/${encodeURIComponent(sessionId)}/account`, { account, continue: true }),
  );
  return { session: res?.session ?? null, turnId: str(res?.turn_id, 80) };
}

/** "Resume when ready": the daemon waits for the reset and continues.
 *  Answers the session (raw). */
export function armResumeWhenReady(sessionId: string): Promise<unknown> {
  return post<unknown>(`/build/sessions/${encodeURIComponent(sessionId)}/resume-when-ready`, {});
}

/** "Cancel" on an armed wait. Answers the session (raw). */
export function cancelResumeWhenReady(sessionId: string): Promise<unknown> {
  return del<unknown>(`/build/sessions/${encodeURIComponent(sessionId)}/resume-when-ready`);
}

const LIMIT_KINDS: readonly LimitKind[] = ["usage", "rate", "busy", "other", "signin", "missing", "disabled"];

/** The `account_limit` card, or null for junk. */
export function decodeAccountLimit(raw: unknown): AccountLimit | null {
  const r = rec(raw);
  if (!r) return null;
  const account = str(r.account, 120);
  const accountName = str(r.account_name, 80);
  if (!account && !accountName) return null;
  return {
    account,
    accountName: accountName || account,
    provider: str(r.provider, 40),
    vendor: vendorOf(r.vendor),
    kind: LIMIT_KINDS.find((k) => k === r.kind) ?? "unknown",
    resetsAt: isoOrNull(r.resets_at),
    resumable: typeof r.resumable === "boolean" ? r.resumable : null,
    turnId: str(r.turn_id, 80),
  };
}

/** The view's armed wait, or null. */
export function decodeAccountWait(raw: unknown): AccountWait | null {
  const r = rec(raw);
  if (!r) return null;
  return {
    account: str(r.account, 120),
    accountName: str(r.account_name, 80),
    resetsAt: isoOrNull(r.resets_at),
  };
}

/** "resets 4:12 PM" (the date added when not today), or "" when unknown. */
export function resetWords(iso: string | null, now: Date = new Date()): string {
  return iso ? `resets ${localTime(iso, now)}` : "";
}

/** A card's ONE status line. `percent` only for a usage bar. */
export interface AccountStatusLine {
  kind: "ready" | "limit" | "sign_in" | "usage" | "off" | "missing" | "unknown";
  text: string;
  percent?: number;
}

export function accountStatusLine(a: BuildAccount, now: Date = new Date()): AccountStatusLine {
  if (a.status === "limit") {
    const when = resetWords(a.resetsAt, now);
    const head = a.limitKind === "busy" ? "Busy for now" : "Limit reached";
    return { kind: "limit", text: when ? `${head} · ${when}` : head };
  }
  if (a.status === "needs_sign_in") return { kind: "sign_in", text: "Needs to sign in again" };
  if (a.status === "off") return { kind: "off", text: "Turned off" };
  if (a.status === "missing") return { kind: "missing", text: "Removed from Iron-Proxy" };
  if (a.usage) {
    const pct = Math.round(a.usage.percent);
    return {
      kind: "usage",
      text: a.usage.window ? `${pct}% · ${a.usage.window}` : `${pct}%`,
      percent: a.usage.percent,
    };
  }
  if (a.status === "ready" || a.status === "usage") return { kind: "ready", text: "Ready" };
  return { kind: "unknown", text: "" };
}

/** From this percent used, a window is NEAR its limit: the card's bar turns
 *  to the warning tone, and the model menu says "Near its limit". */
export const NEAR_LIMIT_PERCENT = 90;

/** An account's state as the IJ harness's MODEL MENU says it (v1.376.0): the
 *  card's own words (`accountStatusLine`), with "used" after a percent (the
 *  menu draws no meter label) and "Near its limit" from NEAR_LIMIT_PERCENT.
 *  `warn` = the state asks the user to act; the menu draws a mark beside the
 *  words then, so no state is told by colour alone. */
export interface AccountMenuState {
  kind: "ready" | "usage" | "near" | "limit" | "sign_in" | "off" | "missing" | "unknown";
  text: string;
  /** The vendor's percent used, only when it sent one. */
  percent?: number;
  warn: boolean;
}

export function accountMenuState(a: BuildAccount, now: Date = new Date()): AccountMenuState {
  const line = accountStatusLine(a, now);
  if (line.kind === "usage" && typeof line.percent === "number") {
    const pct = Math.round(line.percent);
    const used = a.usage?.window ? `${pct}% used · ${a.usage.window}` : `${pct}% used`;
    // Review: a window the vendor says is all used is AT its limit, not near
    // it (the decoder caps a percent at 100). Iron-Proxy has not parked the
    // account yet, so it is still pickable; the words say what the next turn
    // will most likely meet.
    if (line.percent >= 100) {
      return { kind: "limit", text: `At its limit · ${used}`, percent: line.percent, warn: true };
    }
    return line.percent >= NEAR_LIMIT_PERCENT
      ? { kind: "near", text: `Near its limit · ${used}`, percent: line.percent, warn: true }
      : { kind: "usage", text: used, percent: line.percent, warn: false };
  }
  return { kind: line.kind, text: line.text, warn: warnsKind(line.kind) };
}

function warnsKind(kind: AccountStatusLine["kind"]): boolean {
  return kind === "limit" || kind === "sign_in" || kind === "off" || kind === "missing";
}

/** Why an account cannot be switched to, in words ("" = it can). Signed out,
 *  turned off and removed cannot serve a turn at all; an account AT its limit
 *  can be chosen (the chip's list allows it, and the daemon too: the next
 *  turn then shows the limit card, with Resume when ready). */
export function accountBlockedReason(a: BuildAccount): string {
  if (a.status === "needs_sign_in") return "Sign it in again on the Connections page to use it.";
  if (a.status === "off") return "Turn it on in Connections to use it.";
  if (a.status === "missing") return "Iron-Proxy no longer has this account. Choose another one.";
  return "";
}

/** "Claude Code · Max" (the plan only when Iron-Proxy read one). */
export function vendorPlanLine(a: BuildAccount): string {
  const vendor = a.vendorLabel || (a.vendor ? VENDOR_NAMES[a.vendor] : "");
  return a.planLabel ? `${vendor} · ${a.planLabel}` : vendor;
}

/** The session's account as the chip shows it: the list's row (its live
 *  status) when the list has it, else the view's own record. */
export function sessionAccount(
  viewAccount: unknown,
  vendor: AccountVendor | null,
  view: AccountsView | null,
): BuildAccount | null {
  const own = decodeAccount(viewAccount, vendor);
  if (!own) return null;
  const listed = view ? [...view.groups.claude, ...view.groups.codex].find((a) => a.id === own.id) : undefined;
  return listed ?? own;
}

// ------------------------------------------------------------- the limit card

/** The second half of every card (the plan's words: nothing is lost). */
export const LIMIT_KEPT = "Your conversation and work are preserved.";
export const RESUME_WHEN_READY = "Resume when ready";
export const CHOOSE_ACCOUNT = "Choose account";

/** The kinds a RESET ends (Resume when ready means something). */
const RESET_KINDS: readonly LimitKind[] = ["usage", "rate", "busy", "other", "unknown"];

/** The card's words for a limit: its headline, what to do, whether a reset
 *  time is said, and whether "Resume when ready" is offered. Sign-in, a
 *  removed account and a turned-off one are NOT resets: no reset time, and
 *  no Resume unless the daemon says it is resumable. */
export function limitCardWords(limit: AccountLimit): {
  title: string;
  body: string;
  showsReset: boolean;
  canResume: boolean;
} {
  const reset = RESET_KINDS.includes(limit.kind);
  const canResume = limit.resumable === true || (reset && limit.resumable !== false);
  if (limit.kind === "signin") {
    return {
      title: "This account needs to sign in again.",
      body: `Sign it in on the Connections page, or choose another account. ${LIMIT_KEPT}`,
      showsReset: false,
      canResume,
    };
  }
  if (limit.kind === "missing") {
    return {
      title: "The account this session used was removed.",
      body: `Choose another account. ${LIMIT_KEPT}`,
      showsReset: false,
      canResume,
    };
  }
  if (limit.kind === "disabled") {
    return {
      title: "This account is turned off.",
      body: `Turn it on in Connections, or choose another account. ${LIMIT_KEPT}`,
      showsReset: false,
      canResume,
    };
  }
  if (limit.kind === "busy") {
    return {
      title: "This account is busy right now.",
      body: `Wait for it to be free, or choose another account. ${LIMIT_KEPT}`,
      showsReset: true,
      canResume,
    };
  }
  return {
    title: "This account reached its limit.",
    body: `Wait for it to reset, or choose another account. ${LIMIT_KEPT}`,
    showsReset: true,
    canResume,
  };
}

/** "<account> · resets 4:12 PM" (just the name when no reset is said). */
export function limitCardLine(limit: AccountLimit, now: Date = new Date()): string {
  const when = limitCardWords(limit).showsReset ? resetWords(limit.resetsAt, now) : "";
  return when ? `${limit.accountName} · ${when}` : limit.accountName;
}
