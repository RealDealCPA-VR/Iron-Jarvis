/**
 * "Check usage now" (v1.439.0): ONE account's usage read from its own CLI.
 *
 * `POST /build/accounts/{id}/usage/refresh` asks the account's Claude Code
 * (`get_usage`) or Codex (`account/rateLimits/read`) — no model call — tells
 * Iron-Proxy, and answers `{account: <the account row>, read: {at, ok, words,
 * throttled}}`. A read that ran and failed is a 200 with `ok: false` and the
 * daemon's own words; a refusal (Iron-Proxy off, a signed-out account, a Grok
 * account) is a 409 whose `detail` is one sentence (the ApiError's message).
 *
 * Dependency-light: only lib/api (the request).
 */

import { post } from "./api";

export interface UsageRead {
  /** When the read happened (ISO), "" when not said. */
  at: string;
  ok: boolean;
  /** The daemon's words ("Usage checked.", "Codex did not answer in 15 seconds."). */
  words: string;
  /** A press inside 20 s of the last read: the last answer, nothing new. */
  throttled: boolean;
}

export interface UsageRefreshResult {
  /** The account row as every list now sees it (raw: each surface decodes
   *  it its own way), or null. */
  account: unknown;
  read: UsageRead;
}

function str(v: unknown, max = 400): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

export function decodeUsageRead(raw: unknown): UsageRead | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (typeof r.ok !== "boolean") return null;
  return { at: str(r.at, 40), ok: r.ok, words: str(r.words), throttled: r.throttled === true };
}

export function decodeUsageRefresh(raw: unknown): UsageRefreshResult | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const read = decodeUsageRead(r.read);
  if (!read) return null;
  return { account: r.account ?? null, read };
}

/** Read `accountId`'s usage now. Throws the daemon's sentence (ApiError) on
 *  a refusal; a read that failed comes back with `read.ok === false`. */
export async function refreshAccountUsage(accountId: string): Promise<UsageRefreshResult> {
  const raw = await post<unknown>(`/build/accounts/${encodeURIComponent(accountId)}/usage/refresh`, {});
  const got = decodeUsageRefresh(raw);
  if (!got) throw new Error("The usage could not be read.");
  return got;
}

/** "as of just now" / "as of 4 min ago" / "as of 2 h ago"; "" for no time. */
export function asOfWords(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "as of just now";
  if (s < 3600) return `as of ${Math.round(s / 60)} min ago`;
  if (s < 86_400) return `as of ${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86_400);
  return `as of ${d} day${d === 1 ? "" : "s"} ago`;
}

/** The button's own words: the tooltip and the accessible name. */
export const CHECK_USAGE_NOW = "Check usage now";

export function checkUsageLabel(accountName: string): string {
  return accountName ? `${CHECK_USAGE_NOW} for ${accountName}` : CHECK_USAGE_NOW;
}
