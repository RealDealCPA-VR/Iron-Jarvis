/**
 * Build panes on Iron-Proxy accounts (v1.302.0) — the wire types of a pane
 * row's `accounts` and the pure helpers the header chip, the rail and the
 * Launch menu render with.
 *
 * WHY AN ACCOUNT BELONGS TO A PANE. A vendor CLI's account is its home folder
 * (CLAUDE_CONFIG_DIR / CODEX_HOME / GROK_HOME), read ONCE when the program
 * starts. A running Claude Code cannot change account, so the account is
 * chosen when a pane's shell starts and "switching" means a pane started on
 * another account. The daemon records which one on the pane row:
 * `accounts: {anthropic?, openai?, xai?}`, each `{id, title, source, state…}`
 * (`id: null` = this PC's own login).
 *
 * Kept OUT of `lib/api.ts` and `lib/types.ts`: ~71 test files mock
 * `lib/api.ts` wholesale, so a helper living there would vanish under every
 * one of those mocks.
 */
import {
  isSnapshot,
  localTime,
  parkReasonWords,
  providerLabel,
  type IronProxyAccount,
} from "./ironProxy";

/** How often an OPEN Launch menu re-reads `GET /iron-proxy` (the card's own
 *  cadence). A closed menu reads nothing — no poller per pane (S-05). */
export const LAUNCH_ACCOUNTS_POLL_MS = 5000;

/** The providers a Build pane can hold an account for, in chip order. */
export const PANE_ACCOUNT_PROVIDERS = ["anthropic", "openai", "xai"] as const;

/** Which account provider each catalog CLI signs in to. A CLI not listed here
 *  has no account choice and launches exactly as before. */
export const CLI_ACCOUNT_PROVIDER: Record<string, string> = {
  claude: "anthropic",
  codex: "openai",
  grok: "xai",
};

/** The CLI a provider's account is for — the tooltip names the program. */
const PROVIDER_CLI_LABEL: Record<string, string> = {
  anthropic: "Claude Code",
  openai: "Codex",
  xai: "Grok CLI",
};

export function cliAccountProvider(cliId: string | null | undefined): string | null {
  return (cliId && CLI_ACCOUNT_PROVIDER[cliId]) || null;
}

/** One provider's account on a pane, as the daemon records it. */
export interface PaneAccount {
  /** Iron-Proxy account id; null = this PC's own login. */
  id: string | null;
  title: string;
  /** "iron-proxy" | "default" */
  source: string;
  /** Live, from the daemon's cached Iron-Proxy snapshot:
   *  active | ready | parked | needs-sign-in | missing | default | unknown. */
  state?: string | null;
  /** When `state` is parked: until when (ISO). */
  until?: string | null;
  /** When `state` is parked: the reason kind (rate-limit, billing…); when it
   *  is missing: "disabled" for an account switched off in Iron-Proxy. */
  reason?: string | null;
  /** A plain sentence from the daemon (e.g. Iron-Proxy was not answering). */
  note?: string | null;
}

export type PaneAccounts = Record<string, PaneAccount>;

const str = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

/** One raw `accounts[provider]` entry, read tolerantly: `state` may be a word
 *  or an object (`{status|state, until|parked_until, reason|kind}`). */
export function normalizePaneAccount(raw: unknown): PaneAccount | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const id = str(r.id);
  let state: string | null = null;
  let until = str(r.until) ?? str(r.parked_until) ?? str(r.parkedUntil);
  let reason = str(r.reason) ?? str(r.reason_kind);
  if (typeof r.state === "string") state = r.state || null;
  else if (r.state && typeof r.state === "object") {
    const s = r.state as Record<string, unknown>;
    state = str(s.status) ?? str(s.state);
    until = until ?? str(s.until) ?? str(s.parked_until) ?? str(s.parkedUntil);
    const pr = s.parkedReason ?? s.parked_reason;
    reason =
      reason ??
      str(s.reason) ??
      str(s.kind) ??
      (pr && typeof pr === "object" ? str((pr as Record<string, unknown>).kind) : null);
  }
  if (!reason && r.reason && typeof r.reason === "object") {
    reason = str((r.reason as Record<string, unknown>).kind);
  }
  return {
    id,
    title: str(r.title) ?? (id ? id : "this PC's login"),
    source: str(r.source) ?? (id ? "iron-proxy" : "default"),
    state,
    until,
    reason,
    note: str(r.note),
  };
}

/** The pane row's `accounts`, normalised; null when the row has none (Iron-Proxy
 *  off when the pane started, an older daemon, or a CLI with no accounts). */
export function paneAccountsOf(info: unknown): PaneAccounts | null {
  if (!info || typeof info !== "object") return null;
  const raw = (info as { accounts?: unknown }).accounts;
  if (!raw || typeof raw !== "object") return null;
  const out: PaneAccounts = {};
  for (const [provider, v] of Object.entries(raw as Record<string, unknown>)) {
    const acc = normalizePaneAccount(v);
    if (acc) out[provider] = acc;
  }
  return Object.keys(out).length ? out : null;
}

/** The pane's accounts as the page knows them NOW: the `/terminals/activity`
 *  row's (polled every 2.5 s, carries the live state) when it has them, else the
 *  pane row's from `GET /terminals` (read once, when the page loaded). */
export function livePaneAccounts(row: unknown, activity?: unknown): PaneAccounts | null {
  return paneAccountsOf(activity) ?? paneAccountsOf(row);
}

/** Which provider the chip names: the one of the CLI running in the pane when
 *  the pane holds an account for it, else the first of anthropic/openai/xai. */
export function chipProvider(
  accounts: PaneAccounts | null,
  agentCli?: string | null,
): string | null {
  if (!accounts) return null;
  const own = cliAccountProvider(agentCli);
  if (own && accounts[own]) return own;
  for (const p of PANE_ACCOUNT_PROVIDERS) if (accounts[p]) return p;
  const rest = Object.keys(accounts).sort();
  return rest[0] ?? null;
}

const isDefault = (a: PaneAccount) => a.id === null || a.source === "default";

/** "Work Max", or "this PC's login". */
export function accountTitle(a: PaneAccount): string {
  return isDefault(a) ? "this PC's login" : a.title;
}

/** "Claude · Work Max" / "Claude · this PC's login". */
export function accountLabel(provider: string, a: PaneAccount): string {
  return `${providerLabel(provider)} · ${accountTitle(a)}`;
}

export type AccountTone = "plain" | "amber" | "red";

/** The state the chip adds after the label, and its tone. Ready, active and
 *  default add nothing — the chip only speaks up when something is wrong. */
export function accountStateWords(
  a: PaneAccount,
  now: Date = new Date(),
): { word: string | null; tone: AccountTone } {
  const s = a.state ?? "";
  if (s === "parked") {
    return { word: a.until ? `until ${localTime(a.until, now)}` : "parked", tone: "amber" };
  }
  if (s === "needs-sign-in" || s === "unauthenticated") return { word: "needs sign-in", tone: "amber" };
  if (s === "missing") {
    // The daemon says `reason: "disabled"` for an account switched off in
    // Iron-Proxy (it still exists, it just will not be used).
    return { word: a.reason === "disabled" ? "switched off" : "missing", tone: "red" };
  }
  return { word: null, tone: "plain" };
}

/** What the chip (and the rail) show for a pane: its label, tone and tooltip. */
export interface PaneAccountBadge {
  provider: string;
  label: string;
  tone: AccountTone;
  tooltip: string;
}

export function paneAccountBadge(
  accounts: PaneAccounts | null,
  agentCli?: string | null,
  now: Date = new Date(),
): PaneAccountBadge | null {
  const provider = chipProvider(accounts, agentCli);
  if (!accounts || !provider) return null;
  const a = accounts[provider];
  const { word, tone } = accountStateWords(a, now);
  const label = `${accountLabel(provider, a)}${word ? ` · ${word}` : ""}`;
  return { provider, label, tone, tooltip: paneAccountTooltip(provider, accounts, now) };
}

/** The chip's tooltip: which account, that it is fixed for this pane's life,
 *  how to switch, what is wrong with it (if anything), and the daemon's note. */
export function paneAccountTooltip(
  provider: string,
  accounts: PaneAccounts,
  now: Date = new Date(),
): string {
  const a = accounts[provider];
  const cli = PROVIDER_CLI_LABEL[provider] ?? providerLabel(provider);
  const lines: string[] = [];
  lines.push(
    isDefault(a)
      ? `This pane runs ${cli} on this PC's own login (outside Iron-Proxy).`
      : `This pane runs ${cli} as the Iron-Proxy account “${a.title}”.`,
  );
  const s = a.state ?? "";
  if (s === "parked") {
    const until = a.until ? ` until ${localTime(a.until, now)}` : "";
    const why = parkReasonWords(a.reason ? { kind: a.reason } : null);
    lines.push(
      `That account is parked${until}${why ? ` (${why})` : ""} — ${cli} here will be refused until then.`,
    );
  } else if (s === "needs-sign-in" || s === "unauthenticated") {
    lines.push("That account needs signing in again — use Sign in on the Connections page.");
  } else if (s === "missing" && a.reason === "disabled") {
    lines.push(
      "That account is switched off in Iron-Proxy — turn it back on from the Connections page, or start a new pane on another account.",
    );
  } else if (s === "missing") {
    lines.push("That account is no longer in Iron-Proxy — start a new pane to choose another.");
  }
  lines.push(
    "The account is fixed for this pane's life: a running CLI cannot change accounts. " +
      `To use another one, open Launch (the rocket) → ${cli} → “as …”, which opens a new pane on it.`,
  );
  const others = Object.keys(accounts)
    .filter((p) => p !== provider)
    .map((p) => accountLabel(p, accounts[p]));
  if (others.length) lines.push(`Also on this pane: ${others.join(", ")}.`);
  const notes = Object.values(accounts)
    .map((x) => x.note)
    .filter((n): n is string => !!n);
  for (const n of [...new Set(notes)]) lines.push(n);
  return lines.join("\n");
}

/** The Launch menu's account rows for one CLI: the provider and its usable
 *  CLI-lane accounts in Iron-Proxy's order — or null, and the menu is exactly
 *  what it was before v1.302.0 (Iron-Proxy off / not running / 404 / no CLI
 *  account for that provider / a CLI with no account provider). */
export function launchOffer(
  cliId: string,
  snap: unknown,
): { provider: string; accounts: IronProxyAccount[] } | null {
  const provider = cliAccountProvider(cliId);
  if (!provider || !isSnapshot(snap)) return null;
  if (!snap.status.enabled || !snap.status.running) return null;
  const accounts = (snap.accounts ?? [])
    .filter((a) => a.provider === provider && a.lane === "cli" && a.enabled)
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.title.localeCompare(b.title));
  return accounts.length ? { provider, accounts } : null;
}

/** One "as <account>" row: its state word, and — for an account that cannot
 *  start a session now — `disabled` with the reason (the row's tooltip). */
export function offerRowState(
  a: IronProxyAccount,
  now: Date = new Date(),
): { word: string; disabled: boolean; why: string | null } {
  const s = a.state?.status ?? "unknown";
  if (s === "parked") {
    const until = a.state.parkedUntil ? ` until ${localTime(a.state.parkedUntil, now)}` : "";
    const why = parkReasonWords(a.state.parkedReason);
    return {
      word: `Parked${until}`,
      disabled: true,
      why: `Parked${until}${why ? ` — ${why}` : ""}. Unpark it on the Connections page, or pick another account.`,
    };
  }
  if (s === "unauthenticated") {
    return {
      word: "Needs sign-in",
      disabled: true,
      why: "This account needs signing in — use Sign in on the Connections page first.",
    };
  }
  if (s === "active") return { word: "Active", disabled: false, why: null };
  if (s === "ready") return { word: "Ready", disabled: false, why: null };
  return { word: "Not checked yet", disabled: false, why: null };
}

/** Is `choiceId` (an Iron-Proxy id, or null for this PC's login) the account
 *  this pane already runs that provider on? A pane with no record runs on
 *  this PC's own login. */
export function isPaneOwnAccount(paneAccount: PaneAccount | null | undefined, choiceId: string | null): boolean {
  if (!paneAccount) return choiceId === null;
  return (isDefault(paneAccount) ? null : paneAccount.id) === choiceId;
}

/** `POST /terminals/launch`'s body: a NEW pane on that account, next to this one. */
export function launchBody(
  cliId: string,
  choiceId: string | null,
  nearPaneId: string,
): { cli: string; account: string; near: string } {
  return { cli: cliId, account: choiceId ?? "default", near: nearPaneId };
}

/** The new pane's id out of a launch/open answer (the pane row's `id`, or the
 *  card helper's `terminal_id`). */
export function openedPaneId(res: unknown): string | null {
  if (!res || typeof res !== "object") return null;
  const r = res as { id?: unknown; terminal_id?: unknown };
  const id = r.terminal_id ?? r.id;
  return typeof id === "string" && id ? id : null;
}

/* ---- v1.303.0: continue on the next account -------------------------------- */

/** The light Iron-Proxy read (status + accounts, `discovered: []`, no vendor
 *  CLI status runs — v1.302.0 review F3). Every Build surface reads this one. */
export const IRON_PROXY_LIGHT = "/iron-proxy?discover=0";

/** A Claude pane at its usage limit, as the activity row reports it. */
export interface PaneLimit {
  /** The CLI's own limit line (shown nowhere verbatim). A countdown repaints
   *  it, so it never keys Dismiss — `limitKey` does. */
  line: string;
  /** session | weekly | credits … when the daemon names the kind of limit. */
  kind?: string | null;
  /** When the limit resets (ISO), when the daemon read an exact time. */
  reset_at?: string | null;
  /** Best-effort reset words read off the line ("resets 3:45pm (…)"). */
  reset_words?: string | null;
  /** When this limit was first seen — stable while the limit lasts. */
  since?: string | null;
}

/** What Dismiss remembers: the limit EPISODE (its `since`, else its kind),
 *  never the line — a repainting countdown changes the line every minute and
 *  would bring a dismissed strip straight back (review F5). */
export function limitKey(l: PaneLimit): string {
  return l.since || l.kind || l.line;
}

/** The strip's words for every `limit.kind` the daemon reports. */
export const LIMIT_WORDS: Record<string, string> = {
  session: "hit its session limit",
  weekly: "hit its weekly limit",
  daily: "hit its daily limit",
  monthly: "hit its monthly limit",
  usage: "hit its usage limit",
  credits: "ran out of usage credits",
  model: "hit its limit for that model",
};

/** "hit its session limit" / "hit its weekly limit" / … by the limit's kind. */
export function limitWords(l: PaneLimit | null | undefined): string {
  return (l?.kind && LIMIT_WORDS[l.kind.toLowerCase()]) || "hit its usage limit";
}

/** The activity row's `limit`, or null (no limit, an older daemon). */
export function paneLimitOf(row: unknown): PaneLimit | null {
  if (!row || typeof row !== "object") return null;
  const raw = (row as { limit?: unknown }).limit;
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const line = str(r.line);
  if (!line) return null;
  return {
    line,
    kind: str(r.kind),
    reset_at: str(r.reset_at),
    reset_words: str(r.reset_words),
    since: str(r.since),
  };
}

/** The pane's Claude account when it is an Iron-Proxy one (the only kind
 *  that can be continued elsewhere), else null. */
export function continuableAccount(accounts: PaneAccounts | null | undefined): PaneAccount | null {
  const a = accounts?.anthropic;
  if (!a || isDefault(a) || !a.id) return null;
  return a;
}

/** "resets 3:00 PM", in the viewer's LOCAL time: the CLI's own reset time
 *  when the daemon read one (`reset_at`), else Iron-Proxy's parked-until,
 *  else the words off the CLI's line; null when there is nothing. */
export function limitResetWords(
  account: PaneAccount | null,
  limit: PaneLimit | null,
  now: Date = new Date(),
): string | null {
  if (limit?.reset_at && !Number.isNaN(Date.parse(limit.reset_at))) {
    return `resets ${localTime(limit.reset_at, now)}`;
  }
  if (account?.state === "parked" && account.until) return `resets ${localTime(account.until, now)}`;
  const w = limit?.reset_words?.trim();
  if (!w) return null;
  return /^resets?\b/i.test(w) ? w : `resets ${w}`;
}

export type NextAccount =
  /** Iron-Proxy's snapshot is not in hand (loading, off, 404): the daemon picks. */
  | { kind: "unknown" }
  | { kind: "next"; account: IronProxyAccount }
  /** Every other account is parked: when the first one frees. */
  | { kind: "wait"; account: IronProxyAccount; until: string | null }
  /** No other Claude account could take over (none, signed out, switched off). */
  | { kind: "none" };

/** Which account "Continue on …" would move to: the first usable Claude CLI
 *  account other than the pane's own, in Iron-Proxy's order — the same order
 *  the daemon's `pick("anthropic")` walks. */
export function nextAccount(snap: unknown, currentId: string | null): NextAccount {
  if (!isSnapshot(snap) || !snap.status.enabled || !snap.status.running) return { kind: "unknown" };
  const others = (snap.accounts ?? [])
    .filter((a) => a.provider === "anthropic" && a.lane === "cli" && a.enabled && a.id !== currentId)
    .sort((a, b) => (a.order ?? 0) - (b.order ?? 0) || a.title.localeCompare(b.title));
  const usable = others.find((a) => {
    const s = a.state?.status ?? "unknown";
    return s === "ready" || s === "active" || s === "unknown";
  });
  if (usable) return { kind: "next", account: usable };
  const parked = others
    .filter((a) => a.state?.status === "parked")
    .sort((a, b) => {
      const ta = a.state.parkedUntil ? Date.parse(a.state.parkedUntil) : Infinity;
      const tb = b.state.parkedUntil ? Date.parse(b.state.parkedUntil) : Infinity;
      return ta - tb;
    });
  if (parked.length) {
    return { kind: "wait", account: parked[0], until: parked[0].state.parkedUntil ?? null };
  }
  return { kind: "none" };
}

/** `POST /terminals/{id}/continue-on-next`'s answer. */
export interface ContinueAnswer {
  pane: { id: string; [k: string]: unknown };
  resumed: boolean;
  session_id?: string | null;
  parked: boolean;
  from: { id: string; title: string };
  to: { id: string; title: string };
  note?: string | null;
}

export function isContinueAnswer(v: unknown): v is ContinueAnswer {
  if (!v || typeof v !== "object") return false;
  const r = v as Record<string, unknown>;
  const pane = r.pane as Record<string, unknown> | undefined;
  return !!pane && typeof pane === "object" && typeof pane.id === "string" && !!pane.id;
}

/** What the new pane says about how it started: the daemon's note when it
 *  sent one, else a sentence built from `resumed` / `parked`. */
export function continueNote(a: ContinueAnswer): string {
  const note = a.note?.trim();
  if (note) return note;
  const from = a.from?.title ? `“${a.from.title}”` : "the old account";
  const head = a.resumed
    ? "Carried your conversation over."
    : "Started fresh — there was no conversation to carry over.";
  return a.parked
    ? head
    : `${head} Iron-Proxy did not mark ${from} as out of usage, so it may be tried again.`;
}
