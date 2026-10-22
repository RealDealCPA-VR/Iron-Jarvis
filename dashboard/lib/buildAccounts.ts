/**
 * Build's IJ harness ACCOUNTS (v1.369.0, plan Phase 6) — the screen's side of
 * Phase 5 ("accounts per session, through Iron-Proxy").
 *
 * Phase 5 was built IN PARALLEL with this screen, so every shape it answers
 * lives in THIS ONE FILE, with a decoder that whitelists it: reconciling the
 * screen with the daemon Phase 5 actually ships is one file. The shapes the
 * screen assumes (from the plan, docs/IJ-HARNESS-PLAN.md Phase 5):
 *
 *   GET  /build/accounts                 → accounts grouped `claude` / `codex`
 *        each {id, name, default, vendor, plan,
 *              status: "ready" | "limit" | "needs_sign_in",
 *              resets_at?, usage?: {percent, window}}
 *        (accepted as {claude: [...], codex: [...]}, {groups: {...}} or
 *        {accounts: [...]} with each row's `vendor`)
 *   POST /build/accounts/{id}/default    → that account is its vendor's default
 *   the session's setup.account          → the account it runs on (profile id)
 *   PATCH /build/sessions/{id} {account} → the session moves to that account
 *   SSE  account_limit {account, account_name, provider, kind, resets_at}
 *   POST /build/sessions/{id}/resume-when-ready   → arm the wait (DELETE cancels)
 *   POST /build/sessions/{id}/account {account, continue: true}
 *                                         → set the account and continue
 *   the session view's `resume_when_ready` {account, resets_at} while armed
 *
 * THE OLDER-DAEMON RULE: a 404 on GET /build/accounts hides the chip and the
 * list (`fetchAccounts` answers `null`); nothing is guessed. A PERCENT IS THE
 * VENDOR'S: a usage bar is drawn only for a finite number the daemon sent —
 * never an estimate (plan "Risks": "it never estimates a percentage").
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

/** The vendor's product name, as a card's "Claude Code · Max" line says it. */
export const VENDOR_NAMES: Record<AccountVendor, string> = {
  claude: "Claude Code",
  codex: "Codex",
};

export type AccountStatus = "ready" | "limit" | "needs_sign_in" | "unknown";

export interface AccountUsage {
  /** The vendor's own percent used (0..100). */
  percent: number;
  /** The window that percent is over, in words ("7 days", "5 hours"). */
  window: string;
}

export interface BuildAccount {
  id: string;
  name: string;
  isDefault: boolean;
  vendor: AccountVendor;
  /** The plan the vendor names ("Max", "Plus"); "" when not known. */
  plan: string;
  status: AccountStatus;
  /** When a reached limit resets (ISO), else null. */
  resetsAt: string | null;
  /** The window nearest its limit, when the vendor sent a percent. */
  usage: AccountUsage | null;
}

export type AccountGroups = Record<AccountVendor, BuildAccount[]>;

/** A reached limit, as the turn's `account_limit` frame says it. */
export interface AccountLimit {
  account: string;
  accountName: string;
  provider: string;
  kind: string;
  resetsAt: string | null;
}

/** The armed "Resume when ready" wait, as the session view carries it. */
export interface ResumeWait {
  account: string;
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

/** A vendor from any of the spellings the daemon may use. */
export function vendorOf(raw: unknown): AccountVendor | null {
  const v = str(raw, 40).toLowerCase();
  if (v === "claude" || v === "claude-cli" || v === "claude_code" || v === "claude code" || v === "anthropic")
    return "claude";
  if (v === "codex" || v === "codex-cli" || v === "openai") return "codex";
  return null;
}

/** The vendor whose accounts a provider id runs on (a Build session's
 *  provider): claude-cli → claude, codex-cli → codex, else null (a local
 *  model or an API key has no account to pick). */
export function vendorOfProvider(provider: string | null | undefined): AccountVendor | null {
  const p = (provider ?? "").trim().toLowerCase();
  if (p === "claude-cli") return "claude";
  if (p === "codex-cli") return "codex";
  return null;
}

/** "7 days" / "5 hours" from words, or from minutes when a number came. */
export function windowWords(raw: unknown): string {
  if (typeof raw === "number" && Number.isFinite(raw) && raw > 0) {
    const mins = Math.round(raw);
    if (mins % (60 * 24) === 0) {
      const d = mins / (60 * 24);
      return d === 1 ? "1 day" : `${d} days`;
    }
    if (mins % 60 === 0) {
      const h = mins / 60;
      return h === 1 ? "1 hour" : `${h} hours`;
    }
    return `${mins} min`;
  }
  return str(raw, 40);
}

function decodeUsage(raw: unknown): AccountUsage | null {
  const u = rec(raw);
  if (!u) return null;
  const p = u.percent ?? u.used_percent ?? u.usedPercent;
  if (typeof p !== "number" || !Number.isFinite(p)) return null; // never a guess
  const pct = Math.max(0, Math.min(100, p));
  const window = windowWords(u.window ?? u.window_mins ?? u.windowMins);
  return { percent: pct, window };
}

/** One account row, or null when it cannot be shown honestly. */
export function decodeAccount(raw: unknown, vendorHint?: AccountVendor): BuildAccount | null {
  const r = rec(raw);
  if (!r) return null;
  const id = str(r.id, 120);
  if (!id) return null;
  const vendor = vendorOf(r.vendor) ?? vendorOf(r.provider) ?? vendorHint ?? null;
  if (!vendor) return null;
  const statusRaw = str(r.status, 40).toLowerCase();
  const status: AccountStatus =
    statusRaw === "ready" || statusRaw === "usage"
      ? "ready"
      : statusRaw === "limit" || statusRaw === "parked"
        ? "limit"
        : statusRaw === "needs_sign_in" || statusRaw === "needs-sign-in"
          ? "needs_sign_in"
          : "unknown";
  return {
    id,
    name: str(r.name, 80) || id,
    isDefault: r.default === true || r.is_default === true,
    vendor,
    plan: str(r.plan, 40),
    status,
    resetsAt: isoOrNull(r.resets_at ?? r.resetsAt),
    usage: decodeUsage(r.usage),
  };
}

/** GET /build/accounts → the two groups, or null when the shape is not one. */
export function decodeAccounts(raw: unknown): AccountGroups | null {
  const r = rec(raw);
  if (!r) return null;
  const out: AccountGroups = { claude: [], codex: [] };
  let found = false;
  const groups = rec(r.groups) ?? r;
  for (const v of ACCOUNT_VENDORS) {
    const list = groups[v];
    if (Array.isArray(list)) {
      found = true;
      for (const row of list) {
        const a = decodeAccount(row, v);
        if (a && a.vendor === v) out[v].push(a);
      }
    }
  }
  if (Array.isArray(r.accounts)) {
    found = true;
    for (const row of r.accounts) {
      const a = decodeAccount(row);
      if (a) out[a.vendor].push(a);
    }
  }
  return found ? out : null;
}

/** The accounts, or null when this Iron Jarvis has no account list (a 404:
 *  the chip and the list are hidden). Other failures throw. */
export async function fetchAccounts(): Promise<AccountGroups | null> {
  try {
    return decodeAccounts(await get<unknown>("/build/accounts"));
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export function makeDefaultAccount(id: string): Promise<unknown> {
  return post(`/build/accounts/${encodeURIComponent(id)}/default`, {});
}

/** The session moves to `account` (from the next turn). */
export function setSessionAccount(sessionId: string, account: string): Promise<unknown> {
  return patch(`/build/sessions/${encodeURIComponent(sessionId)}`, { account });
}

/** "Choose account" on the limit card: set it and continue the conversation. */
export function chooseAccountAndContinue(sessionId: string, account: string): Promise<unknown> {
  return post(`/build/sessions/${encodeURIComponent(sessionId)}/account`, { account, continue: true });
}

/** "Resume when ready": the daemon waits for the reset and continues. */
export function armResumeWhenReady(sessionId: string): Promise<unknown> {
  return post(`/build/sessions/${encodeURIComponent(sessionId)}/resume-when-ready`, {});
}

/** "Cancel" on an armed wait. */
export function cancelResumeWhenReady(sessionId: string): Promise<unknown> {
  return del(`/build/sessions/${encodeURIComponent(sessionId)}/resume-when-ready`);
}

/** The `account_limit` frame, or null for junk. */
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
    kind: str(r.kind, 40),
    resetsAt: isoOrNull(r.resets_at),
  };
}

/** The session view's armed wait, or null. */
export function decodeResumeWait(raw: unknown): ResumeWait | null {
  const r = rec(raw);
  if (!r) return null;
  const account = str(r.account, 120);
  const resetsAt = isoOrNull(r.resets_at);
  if (!account && !resetsAt) return null;
  return { account, resetsAt };
}

/** The account a session's setup names ("" = the vendor's default). */
export function sessionAccountOf(setup: unknown): string {
  return str(rec(setup)?.account, 120);
}

/** "resets 4:12 PM" (the date added when not today), or "" when unknown. */
export function resetWords(iso: string | null, now: Date = new Date()): string {
  return iso ? `resets ${localTime(iso, now)}` : "";
}

/** A card's ONE status line. `percent` only for a usage bar. */
export interface AccountStatusLine {
  kind: "ready" | "limit" | "sign_in" | "usage" | "unknown";
  text: string;
  percent?: number;
}

export function accountStatusLine(a: BuildAccount, now: Date = new Date()): AccountStatusLine {
  if (a.status === "limit") {
    const when = resetWords(a.resetsAt, now);
    return { kind: "limit", text: when ? `Limit reached · ${when}` : "Limit reached" };
  }
  if (a.status === "needs_sign_in") return { kind: "sign_in", text: "Needs sign-in" };
  if (a.usage) {
    const pct = Math.round(a.usage.percent);
    return {
      kind: "usage",
      text: a.usage.window ? `${pct}% · ${a.usage.window}` : `${pct}%`,
      percent: a.usage.percent,
    };
  }
  if (a.status === "ready") return { kind: "ready", text: "Ready" };
  return { kind: "unknown", text: "Not checked yet" };
}

/** "Claude Code · Max" (the plan only when the vendor named one). */
export function vendorPlanLine(a: BuildAccount): string {
  return a.plan ? `${VENDOR_NAMES[a.vendor]} · ${a.plan}` : VENDOR_NAMES[a.vendor];
}

/** The account a session runs on: its own pick, else its vendor's default. */
export function accountFor(
  groups: AccountGroups | null,
  vendor: AccountVendor | null,
  picked: string,
): BuildAccount | null {
  if (!groups) return null;
  const all = [...groups.claude, ...groups.codex];
  if (picked) return all.find((a) => a.id === picked) ?? null;
  if (!vendor) return null;
  return groups[vendor].find((a) => a.isDefault) ?? groups[vendor][0] ?? null;
}

// ------------------------------------------------------------- the limit card

/** The card's words (the mockup, word for word). */
export const LIMIT_CARD_TITLE = "This account reached its limit.";
export const LIMIT_CARD_BODY =
  "Wait for it to reset, or choose another account. Your conversation and work are preserved.";
export const RESUME_WHEN_READY = "Resume when ready";
export const CHOOSE_ACCOUNT = "Choose account";

/** "<account> · resets 4:12 PM" (just the name when the reset is unknown). */
export function limitCardLine(limit: AccountLimit, now: Date = new Date()): string {
  const when = resetWords(limit.resetsAt, now);
  return when ? `${limit.accountName} · ${when}` : limit.accountName;
}
