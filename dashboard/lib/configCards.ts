/**
 * Settings cards in chat (calm UI redesign S3/S4) — the wire shape and its
 * WHITELIST decoder. Kept outside lib/api.ts (which ~70 test files mock
 * wholesale) and outside useChatStream so both chat lanes decode identically.
 *
 * The daemon sends `config_cards` on every turn (possibly empty):
 *   { kind: "change", key, label, old, new, restart, change_id }
 *   { kind: "secret", name, label, help, why, request_id }
 * A secret card never carries a value — the model never had one.
 */

export interface ConfigChangeCard {
  kind: "change";
  /** Redesign S5: a RECORD's change names itself ("Schedule changed",
   *  "Workflow removed"); absent on a setting's card ("Setting changed"). */
  title?: string;
  key: string;
  label: string;
  old: unknown;
  new: unknown;
  /** v1.448.0 (Wave A, A3): the option's DISPLAY NAME for `old` / `new`
   *  when the setting's row has one ("Ask before commands", "Daylight",
   *  "Claude Code"); absent for free text, a number, a switch. The card
   *  shows the name, never the wire value. */
  old_label?: string;
  new_label?: string;
  restart?: boolean;
  change_id: string;
  /** Set on the message once the user pressed Undo. */
  undone?: boolean;
  /** Wave 3 group G review: what the Undo REALLY did when that was not
   *  "put it back as it was" (a made project ARCHIVED because work was filed
   *  in it, a change that was already undone) — the daemon's own words, so
   *  the card never claims more than the Undo did. */
  undone_note?: string;
}

export interface SecretRequestCard {
  kind: "secret";
  name: string;
  label: string;
  help?: string;
  why?: string;
  request_id: string;
  /** Set on the message once saved: "stored" | "replaced" (never a value). */
  saved?: "stored" | "replaced";
  /** The ledger action of the save, for its Undo. */
  action_id?: string;
  undone?: boolean;
}

export type ConfigCard = ConfigChangeCard | SecretRequestCard;

const str = (v: unknown): string => (typeof v === "string" ? v : "");

/** Keep only well-formed cards; junk and unknown kinds die here. */
export function decodeConfigCards(raw: unknown): ConfigCard[] {
  if (!Array.isArray(raw)) return [];
  const out: ConfigCard[] = [];
  for (const c of raw.slice(0, 12)) {
    if (!c || typeof c !== "object") continue;
    const r = c as Record<string, unknown>;
    if (r.kind === "change" && str(r.key) && str(r.change_id)) {
      out.push({
        kind: "change",
        ...(str(r.title) ? { title: str(r.title).slice(0, 60) } : {}),
        key: str(r.key),
        label: str(r.label) || str(r.key),
        old: r.old,
        new: r.new,
        ...(str(r.old_label) ? { old_label: str(r.old_label).slice(0, 120) } : {}),
        ...(str(r.new_label) ? { new_label: str(r.new_label).slice(0, 120) } : {}),
        restart: r.restart === true,
        change_id: str(r.change_id),
        ...(r.undone === true ? { undone: true } : {}),
        ...(r.undone === true && str(r.undone_note) ? { undone_note: str(r.undone_note).slice(0, 300) } : {}),
      });
    } else if (r.kind === "secret" && str(r.name) && str(r.request_id)) {
      out.push({
        kind: "secret",
        name: str(r.name),
        label: str(r.label) || str(r.name),
        help: str(r.help),
        why: str(r.why),
        request_id: str(r.request_id),
        ...(r.saved === "stored" || r.saved === "replaced" ? { saved: r.saved } : {}),
        ...(str(r.action_id) ? { action_id: str(r.action_id) } : {}),
        ...(r.undone === true ? { undone: true } : {}),
      });
    }
  }
  return out;
}

/** Wave 3 group G review: a record Undo's own answer (``POST /undo``'s
 *  ``output``, "undo: archived the project 'Brown' (work was already filed in
 *  it, so it was kept)") as one sentence — ONLY when it says the Undo kept
 *  something or found it already back ("" otherwise: the card's own "is back
 *  to how it was" is then true). */
export function undoAnswerNote(output: unknown): string {
  const text = typeof output === "string" ? output.replace(/^undo:\s*/i, "").trim() : "";
  if (!text || !/\b(?:kept|already)\b/i.test(text)) return "";
  const s = text.charAt(0).toUpperCase() + text.slice(1);
  return (/[.!?]$/.test(s) ? s : `${s}.`).slice(0, 300);
}

/** Wave 3 group G review: an Undo the daemon REFUSED in words (a 409 —
 *  "the project was changed again after this change…") says why; "" for any
 *  other failure (the card keeps its own "try again" line). */
export function undoRefusalWords(err: unknown): string {
  const e = err as { status?: unknown; message?: unknown } | null;
  if (!e || e.status !== 409 || typeof e.message !== "string") return "";
  const text = e.message.replace(/^undo failed:\s*/i, "").trim();
  if (!text) return "";
  const s = text.charAt(0).toUpperCase() + text.slice(1);
  return (/[.!?]$/.test(s) ? s : `${s}.`).slice(0, 300);
}

/** A setting's value, in words. v1.448.0 (Wave A, A3): `label` — the
 *  option's display name the daemon sent beside the value — wins when it is
 *  there ("Ask before commands", never "approve_for_me"); a dict with no
 *  label reads as its keys, never a JSON dump. */
export function showValue(v: unknown, label?: string): string {
  if (label && label.trim()) return label.trim();
  if (v === null || v === undefined || v === "") return "(not set)";
  if (v === true) return "On";
  if (v === false) return "Off";
  if (Array.isArray(v)) return v.length ? v.map((x) => String(x)).join(", ") : "(not set)";
  if (typeof v === "object") {
    const keys = Object.keys(v as Record<string, unknown>);
    return keys.length ? keys.join(", ") : "(not set)";
  }
  return String(v);
}

/** v1.448.0 (Wave A, A3): the Settings link on a change card opens the
 *  setting's OWN row (`SettingsHome` reads `?focus=<key>`), not the top of
 *  Settings. A record's card (a schedule, a workflow…) has no row there and
 *  keeps the plain address. */
export function settingsHrefFor(card: Pick<ConfigChangeCard, "key" | "title">): string {
  if (card.title || !card.key) return "/settings";
  return `/settings?focus=${encodeURIComponent(card.key)}`;
}

/**
 * Text that looks like a credential (redesign S4, AUDIT Q9): the shapes the
 * daemon's own masking knows (detections/redact.py) — provider keys, GitHub
 * and Slack tokens, AWS key ids, a bearer header. A message that matches is
 * held and the secure card offered instead of sending it into the chat.
 */
const SECRET_SHAPES: RegExp[] = [
  /\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}/,
  /\bgh[pousr]_[A-Za-z0-9]{20,}/,
  /\bxox[abprs]-[A-Za-z0-9-]{10,}/,
  /\bAKIA[0-9A-Z]{16}\b/,
  /\bBearer\s+[A-Za-z0-9._-]{20,}/,
  /\b\d{8,10}:[A-Za-z0-9_-]{30,}\b/, // a Telegram bot token
  // v1.433.0 (A4): a Discord webhook address IS its secret — anyone holding
  // it can post as the hook (discord.com, the older discordapp.com, the
  // www/ptb/canary hosts, an /api/v10 path; pasted with or without
  // https://). Matched from the host's own name, so any of those prefixes
  // holds it; "mydiscord.com" does not.
  /\bdiscord(?:app)?\.com\/api(?:\/v\d+)?\/webhooks\/\d{5,}\/[A-Za-z0-9_-]{20,}/i,
  // v1.433.0 (A4): a database connection string that carries a password
  // (postgres://user:pass@host, postgresql+driver://…, mysql, MariaDB,
  // MongoDB incl. mongodb+srv, Redis, SQL Server). The password may hold a
  // "/" (a generated one often does), but no "?" or "#" after one — so a
  // query string holding an "@" is not read as a password. A string with no
  // password ("postgres://host/db", "mysql://me@host") is not held.
  /\b(?:postgres(?:ql)?|mysql|mariadb|mongodb|rediss?|mssql|sqlserver)(?:\+[a-z0-9]+)?:\/\/[^\s:/@]*:[^\s@/]+(?:\/[^\s@/?#]*)*@[^\s/]/i,
];

export function looksLikeSecret(text: string): boolean {
  return SECRET_SHAPES.some((rx) => rx.test(text || ""));
}

/** A card's identity: the change it records, or the credential it asks for. */
export function cardId(c: ConfigCard): string {
  return c.kind === "change" ? `change:${c.change_id}` : `secret:${c.request_id}`;
}

/**
 * Cards settled in this window (Undo pressed, a credential saved), applied to
 * every later save — a turn that began before the press still holds the
 * unsettled card in its history and would otherwise write it back.
 */
export function applySettledCards<M extends { configCards?: ConfigCard[] }>(
  msgs: M[],
  settled: ReadonlyMap<string, ConfigCard>,
): M[] {
  if (settled.size === 0) return msgs;
  let changed = false;
  const out = msgs.map((m) => {
    if (!m.configCards?.length) return m;
    let touched = false;
    const cards = m.configCards.map((c) => {
      const done = settled.get(cardId(c));
      if (!done || JSON.stringify(done) === JSON.stringify(c)) return c;
      touched = true;
      return done;
    });
    if (!touched) return m;
    changed = true;
    return { ...m, configCards: cards };
  });
  return changed ? out : msgs;
}

/** The first credential-shaped run in `text` ("" when none). */
export function extractSecret(text: string): string {
  for (const rx of SECRET_SHAPES) {
    const m = rx.exec(text || "");
    if (m) return m[0].replace(/^Bearer\s+/, "");
  }
  return "";
}

/** A best guess at which credential a pasted value is, from its shape. */
export function guessSecretName(value: string): { key: string; arg: string } {
  if (/^sk-ant-/.test(value)) return { key: "connection.{provider}", arg: "anthropic" };
  if (/^sk-/.test(value)) return { key: "connection.{provider}", arg: "openai" };
  if (/^gh[pousr]_/.test(value)) return { key: "app.{name}", arg: "github" };
  if (/^xox[abprs]-/.test(value)) return { key: "channel.{name}", arg: "slack" };
  if (/^\d{8,10}:/.test(value)) return { key: "channel.{name}", arg: "telegram" };
  return { key: "secret.{name}", arg: "" };
}
