/**
 * Settings › Secrets in plain words (v1.358.0, lane 8D).
 *
 * The vault keeps an app's key under an engine name — `mcp.<app id>.<KEY>`
 * (mcp/secrets.vault_name, every add door), `mcp.<app id>.auth.value` for a
 * server's sign-in token, `…header.<H>`, `…arg.<i>`, `…url`, and the
 * Directory's older `conn_<id>_<FIELD>` — with the description
 * "MCP pack <id>: <KEY>". The page printed both as they were stored. A user
 * reads `<App or extension name>: <field label>` instead ("GitHub: GitHub
 * personal access token"), the app's name and the field's label taken from
 * the Directory's own rows (GET /connectors, the one catalog). The vault NAME
 * on disk never changes — it stays in the row's tooltip, because it is what
 * a stored secret is; only what is shown changes.
 *
 * Dependency-free (no lib/api import — ~70 test files mock it wholesale).
 */

/** The Directory row facts this reads (GET /connectors). */
export interface SecretApp {
  id: string;
  name: string;
  /** Set when the row was lit under the app's other saved name. */
  catalog_id?: string | null;
  fields?: { name: string; label: string; kind?: string }[] | null;
}

export interface SecretWords {
  /** "GitHub: GitHub personal access token". */
  label: string;
  /** The app or extension name the key belongs to. */
  app: string;
}

/** Short words that stay in capitals when a key name is read out. */
const CAPS: Readonly<Record<string, string>> = {
  api: "API",
  url: "URL",
  id: "ID",
  pat: "PAT",
  ssh: "SSH",
  aws: "AWS",
  http: "HTTP",
  jwt: "JWT",
  sql: "SQL",
  db: "DB",
  oauth: "OAuth",
};

/** An environment-style key in words: "BRAVE_API_KEY" → "Brave API key". */
export function keyWords(key: string): string {
  const parts = String(key || "")
    .split(/[_\-.\s]+/)
    .map((p) => p.trim())
    .filter(Boolean)
    .map((p) => CAPS[p.toLowerCase()] ?? p.toLowerCase());
  if (parts.length === 0) return "Key";
  const first = parts[0];
  parts[0] = first === first.toLowerCase() ? first.charAt(0).toUpperCase() + first.slice(1) : first;
  return parts.join(" ");
}

function appFor(apps: readonly SecretApp[], id: string): SecretApp | undefined {
  return apps.find((a) => a.id === id) ?? apps.find((a) => a.catalog_id === id);
}

function fieldWords(app: SecretApp | undefined, rest: string): string {
  const fields = app?.fields ?? [];
  if (rest === "auth.value") {
    // A server's sign-in token is the card's one secret field (GitHub's).
    const secrets = fields.filter((f) => (f.kind ?? "secret") === "secret");
    return secrets.length === 1 ? secrets[0].label : "Access token";
  }
  if (rest === "url") return "Server address";
  const header = /^header\.(.+)$/.exec(rest);
  if (header) return `${header[1]} header`;
  const arg = /^arg\.(\d+)(?:\.\d+)?$/.exec(rest);
  if (arg) {
    const args = fields.filter((f) => f.kind === "arg");
    return args.length === 1 ? args[0].label : `Secret setting ${Number(arg[1]) + 1}`;
  }
  const named = fields.find((f) => f.name === rest);
  return named ? named.label : keyWords(rest);
}

/**
 * The plain words for one stored secret, or null when it is not an app's key
 * (a model key, a password the user stored by hand — shown as they are).
 */
export function secretWords(name: string, apps: readonly SecretApp[] = []): SecretWords | null {
  const raw = String(name || "");
  const mcp = /^mcp\.([^.]+)\.(.+)$/.exec(raw);
  if (mcp) {
    const app = appFor(apps, mcp[1]);
    const appName = app?.name || mcp[1];
    return { app: appName, label: `${appName}: ${fieldWords(app, mcp[2])}` };
  }
  // The Directory's own names before v1.355.0: conn_<card id>_<FIELD>. A card
  // id has underscores of its own (microsoft_365), so the longest id wins.
  if (raw.startsWith("conn_")) {
    const byLength = [...apps].sort((a, b) => b.id.length - a.id.length);
    for (const app of byLength) {
      const prefix = `conn_${app.id}_`;
      if (app.id && raw.startsWith(prefix) && raw.length > prefix.length) {
        return { app: app.name, label: `${app.name}: ${fieldWords(app, raw.slice(prefix.length))}` };
      }
    }
  }
  return null;
}

/** What the Description column says for an app's key: the description the
 *  user typed stands; the one an add door wrote for itself ("MCP pack <id>:
 *  <KEY>") is replaced by who uses it. */
export function secretDescription(description: string, words: SecretWords | null): string {
  const text = String(description || "").trim();
  if (!words) return text;
  if (!text || /^MCP pack\b/i.test(text) || /^connector\b/i.test(text)) return `Used by ${words.app}`;
  return text;
}
