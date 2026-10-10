/**
 * Iron-Proxy on the Connections page (v1.301.0) — the wire types of
 * `GET /iron-proxy` and the pure helpers the card renders with.
 *
 * Kept OUT of `lib/types.ts` and `lib/api.ts` on purpose: ~71 test files mock
 * `lib/api.ts` wholesale, so a helper living there would vanish under every
 * one of those mocks.
 *
 * Iron-Proxy is the ACCOUNT MANAGER (which accounts exist, their order, who is
 * parked until when). Iron Jarvis keeps its own adapters and runs each
 * Claude/Codex/Grok call AS the first usable account of THAT provider — a
 * limit moves to the next account of the same provider, never to another one.
 */
import { normalizeIso } from "./format";

/** `service.status()` — never carries the Iron-Proxy token. */
export interface IronProxyStatus {
  enabled: boolean;
  running: boolean;
  owned: boolean;
  url?: string | null;
  version?: string | null;
  error?: string | null;
  bundled?: boolean;
}

export interface IronProxyAccountState {
  /** ready | active | parked | unauthenticated | disabled | unknown */
  status: string;
  parkedUntil?: string | null;
  parkedReason?: { kind: string; message?: string | null } | null;
  lastUsedAt?: string | null;
  served?: number;
}

export interface IronProxyUsage {
  requests_5h?: number | null;
  requests_7d?: number | null;
  parks_7d?: number | null;
  minutes_left?: number | null;
}

export interface IronProxyAccount {
  id: string;
  title: string;
  provider: string;
  /** "cli" (a subscription login) or "api" (an API key). */
  lane: string;
  order: number;
  enabled: boolean;
  state: IronProxyAccountState;
  usage?: IronProxyUsage | null;
  home_adopted?: boolean;
  /** Optional: the account's CLI home, when the daemon passes it through. */
  home?: string | null;
}

export interface IronProxyDiscovered {
  provider: string;
  home: string;
  title?: string | null;
  signed_in: boolean;
  /** Optional: set when an account already uses this home (Iron-Proxy's
   *  `adoptedProfileId`, in either spelling). */
  adopted_profile_id?: string | null;
  adoptedProfileId?: string | null;
  adopted?: boolean;
}

export interface IronProxySnapshot {
  status: IronProxyStatus;
  accounts: IronProxyAccount[];
  discovered: IronProxyDiscovered[];
  providers_used_by_jarvis?: Record<string, string>;
}

/** The answer has the shape the card needs (an older or mocked daemon may
 *  answer `{}` — the card then renders nothing). */
export function isSnapshot(v: unknown): v is IronProxySnapshot {
  if (!v || typeof v !== "object") return false;
  const s = (v as { status?: unknown }).status;
  return !!s && typeof s === "object" && "enabled" in (s as object);
}

/** Display order of the provider groups. Unknown providers follow, A-Z. */
export const PROVIDER_ORDER = ["anthropic", "openai", "xai", "google", "openai-compatible"];

const PROVIDER_LABEL: Record<string, string> = {
  anthropic: "Claude",
  openai: "Codex",
  xai: "Grok",
  google: "Gemini",
  "openai-compatible": "OpenAI-compatible",
};

export function providerLabel(provider: string): string {
  return PROVIDER_LABEL[provider] ?? provider;
}

/** What "Add account" offers: the providers Iron Jarvis runs through accounts. */
export const ADDABLE_PROVIDERS = ["anthropic", "openai", "xai"] as const;

/** Iron Jarvis's own provider id for each Iron-Proxy provider (the daemon's
 *  `providers_used_by_jarvis` wins; this is the fallback). */
export const DEFAULT_USED_BY: Record<string, string> = {
  anthropic: "claude-cli",
  openai: "codex-cli",
  xai: "grok-cli",
};

export interface AccountGroup {
  provider: string;
  accounts: IronProxyAccount[];
}

/** Accounts grouped by provider (PROVIDER_ORDER first), each group in the
 *  account order Iron-Proxy tries them. */
export function groupAccounts(accounts: IronProxyAccount[]): AccountGroup[] {
  const by = new Map<string, IronProxyAccount[]>();
  for (const a of accounts) {
    const list = by.get(a.provider) ?? [];
    list.push(a);
    by.set(a.provider, list);
  }
  const rank = (p: string) => {
    const i = PROVIDER_ORDER.indexOf(p);
    return i === -1 ? PROVIDER_ORDER.length : i;
  };
  return [...by.keys()]
    .sort((a, b) => rank(a) - rank(b) || a.localeCompare(b))
    .map((provider) => ({
      provider,
      accounts: [...(by.get(provider) ?? [])].sort(
        (a, b) => (a.order ?? 0) - (b.order ?? 0) || a.title.localeCompare(b.title),
      ),
    }));
}

/** The provider's full id list with the account at `index` moved one place
 *  (`-1` up, `+1` down). Unchanged when the move would leave the list. */
export function movedIds(ids: string[], index: number, dir: -1 | 1): string[] {
  const to = index + dir;
  if (index < 0 || index >= ids.length || to < 0 || to >= ids.length) return [...ids];
  const out = [...ids];
  [out[index], out[to]] = [out[to], out[index]];
  return out;
}

/** A clock time in the viewer's LOCAL zone; the date is added when it is not
 *  today. Never the raw ISO string. */
export function localTime(iso: string, now: Date = new Date()): string {
  const d = new Date(normalizeIso(iso));
  if (Number.isNaN(d.getTime())) return iso;
  const time = d.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  if (d.toDateString() === now.toDateString()) return time;
  const date = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  return `${date}, ${time}`;
}

const KIND_WORDS: Record<string, string> = {
  "rate-limit": "rate limit",
  "quota-exhausted": "plan limit reached",
  billing: "billing problem",
  "auth-expired": "sign-in expired",
  overloaded: "provider busy",
};

export function parkReasonWords(reason: IronProxyAccountState["parkedReason"]): string | null {
  if (!reason) return null;
  return KIND_WORDS[reason.kind] ?? (reason.message?.trim() || reason.kind || null);
}

export type ChipTone = "green" | "amber" | "red" | "cyan" | "slate";

/** The account's state chip: Active / Ready / Parked until <local time> ·
 *  <reason> / Needs sign-in / Off. v1.330.0 (calm M3): the reason follows a
 *  middle dot like the rest of the card ("12 requests in 5 h · 80 in 7
 *  days"), not a dash aside. */
export function stateChip(
  a: IronProxyAccount,
  now: Date = new Date(),
): { label: string; tone: ChipTone } {
  const s = a.state?.status ?? "unknown";
  if (!a.enabled || s === "disabled") return { label: "Off", tone: "slate" };
  if (s === "unauthenticated") return { label: "Needs sign-in", tone: "red" };
  if (s === "parked") {
    const until = a.state.parkedUntil ? ` until ${localTime(a.state.parkedUntil, now)}` : "";
    const why = parkReasonWords(a.state.parkedReason);
    return { label: `Parked${until}${why ? ` · ${why}` : ""}`, tone: "amber" };
  }
  if (s === "active") return { label: "Active", tone: "green" };
  if (s === "ready") return { label: "Ready", tone: "cyan" };
  return { label: "Not checked yet", tone: "slate" };
}

function fmtMinutes(m: number): string {
  const mins = Math.max(0, Math.round(m));
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const r = mins % 60;
  return r ? `${h} h ${r} min` : `${h} h`;
}

/** One usage line, or null when the daemon sent no usage. */
export function usageLine(u: IronProxyUsage | null | undefined): string | null {
  if (!u) return null;
  const parts: string[] = [];
  const n = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);
  if (n(u.requests_5h)) parts.push(`${u.requests_5h} request${u.requests_5h === 1 ? "" : "s"} in 5 h`);
  if (n(u.requests_7d)) parts.push(`${u.requests_7d} in 7 days`);
  if (n(u.parks_7d) && u.parks_7d > 0) parts.push(`parked ${u.parks_7d}× this week`);
  if (n(u.minutes_left)) parts.push(`${fmtMinutes(u.minutes_left)} left`);
  return parts.length ? parts.join(" · ") : null;
}

const samePath = (a: string, b: string) =>
  a.replace(/[\\/]+$/, "").toLowerCase() === b.replace(/[\\/]+$/, "").toLowerCase();

/** Discovered logins no account uses yet — each gets "Use this PC's login". */
export function adoptable(
  discovered: IronProxyDiscovered[],
  accounts: IronProxyAccount[],
): IronProxyDiscovered[] {
  return discovered.filter(
    (d) =>
      !d.adopted &&
      !d.adopted_profile_id &&
      !d.adoptedProfileId &&
      !accounts.some(
        (a) => a.provider === d.provider && typeof a.home === "string" && samePath(a.home, d.home),
      ),
  );
}

/** The Build page focused on one terminal pane (the `?focus=` deep link the
 *  Terminals page reads on load — the same link Creative Studio uses). */
export function terminalsHref(terminalId: string): string {
  return `/terminals?focus=${encodeURIComponent(terminalId)}`;
}

/** The new account's id out of POST /iron-proxy/accounts' answer (the bare
 *  account, or wrapped as `account`/`profile`). */
export function createdId(res: unknown): string | null {
  if (!res || typeof res !== "object") return null;
  const r = res as { id?: unknown; account?: { id?: unknown }; profile?: { id?: unknown } };
  const id = r.id ?? r.account?.id ?? r.profile?.id;
  return typeof id === "string" && id ? id : null;
}
