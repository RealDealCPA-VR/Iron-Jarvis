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
  restart?: boolean;
  change_id: string;
  /** Set on the message once the user pressed Undo. */
  undone?: boolean;
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
        restart: r.restart === true,
        change_id: str(r.change_id),
        ...(r.undone === true ? { undone: true } : {}),
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

/** A setting's value, in words. */
export function showValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "(not set)";
  if (v === true) return "On";
  if (v === false) return "Off";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
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
