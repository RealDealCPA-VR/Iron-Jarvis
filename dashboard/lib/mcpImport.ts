/**
 * v1.343.0 ("add a pack in one paste"), lane K: the pure half of the Tools
 * page's "Add a pack" area and the pack editor.
 *
 * Everything here is data in, data out, so the tests can pin the shapes the
 * routes receive without a DOM:
 *   - the preview / sources / apply payloads (`PreviewRow`, `ImportSource`,
 *     `ApplyResult`) as the daemon sends them, VALUES NEVER INCLUDED;
 *   - `safeName`, the dashboard twin of `mcp/import_config.safe_name` (the
 *     server is the authority; the twin only shows the user what the server
 *     will call the pack before the press);
 *   - `buildLaunch` / `guessLauncher` for the npm door's launcher choice;
 *   - `rowStatus`, the one rule that turns a preview row into a StatusChip;
 *   - `editPatch`, which turns an edit form into a PARTIAL PATCH body —
 *     omitted = keep, "" is a value (the v1.164.0 rule), a Clear press rides
 *     `clear_secrets` / `clear_headers`.
 */

/* ------------------------------------------------------------------ rows */

/** One row of POST /mcp/import/preview and of a source group. No values. */
export interface PreviewRow {
  name: string;
  transport: "stdio" | "http" | "sse" | string;
  command?: string | null;
  args?: string[] | null;
  url?: string | null;
  header_names?: string[] | null;
  env_keys?: string[] | null;
  secret_keys?: string[] | null;
  exists?: boolean;
  runtime_ready?: boolean | null;
  launcher_note?: string | null;
  source?: string | null;
  origin?: string | null;
  /** v1.354.0 (G50): the app it came from has this server switched off — a
   *  preview fact, never stored; the door leaves the row unticked. */
  disabled?: boolean | null;
  /** v1.354.0: how it is already here: "pack" | "app" | "memory" ("" = new). */
  exists_as?: string | null;
  /** v1.354.0 (G49): the catalog's one line about the package ("" = none). */
  blurb?: string | null;
}

/** POST /mcp/import/preview's answer. */
export interface PreviewResult {
  servers: PreviewRow[];
  unrecognised: boolean;
}

/** One group of GET /mcp/import/sources. */
export interface ImportSource {
  source: string;
  label: string;
  origin: string;
  readable: boolean;
  note?: string | null;
  servers: PreviewRow[];
}

/** POST /mcp/import/apply's answer. */
export interface ApplyResult {
  added: { name: string; tools_loaded: number; note?: string | null; state?: string | null }[];
  skipped: { name: string; why: string }[];
}

/* ------------------------------------------------------------------ names */

/** The server's name rule, mirrored: lowercase, every char outside
 *  [a-zA-Z0-9_-] becomes "-", runs collapse, leading non-letters go (a name
 *  starts with a letter), at most 40 chars. */
export function safeName(raw: string): string {
  let s = (raw ?? "").trim().toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  s = s.replace(/-{2,}/g, "-");
  s = s.replace(/^[^a-z]+/, "");
  s = s.replace(/-+$/, "");
  return s.slice(0, 40).replace(/-+$/, "");
}

/** A name for an HTTP pack, read off its URL's host ("api.example.com" ->
 *  "api-example-com"); "" when the URL does not parse. */
export function nameFromUrl(url: string): string {
  try {
    const u = new URL(url.trim());
    return safeName(u.hostname);
  } catch {
    return "";
  }
}

/* -------------------------------------------------------------- launchers */

export type Launcher = "npx" | "uvx" | "pipx" | "node" | "python" | "command";

/** The launchers the npm door offers, in menu order, with their words. */
export const LAUNCHER_CHOICES: { id: Launcher; label: string }[] = [
  { id: "npx", label: "npx (Node package)" },
  { id: "uvx", label: "uvx (Python package)" },
  { id: "command", label: "A command I type" },
];

/** The dashboard twin of `mcp/import_config.guess_launcher`: a "pip:" /
 *  "pypi:" prefix or a PyPI-looking bare name (underscores, which npm names
 *  do not carry, or the `mcp-server-` prefix) reads as uvx; everything else
 *  npx. The server's guess is the authority; this one only sets the form's
 *  default, and the pick is sent as `launcher` either way. */
export function guessLauncher(pkg: string): Launcher {
  const s = pkg.trim();
  const low = s.toLowerCase();
  if (/^(pip|pypi):/.test(low)) return "uvx";
  if (/^(npm:|npx:|@|github:|https?:\/\/|git\+)/.test(low)) return "npx";
  if (/[/. ]/.test(low)) return "npx";
  const pypi = /^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+$/.test(low);
  if (pypi && (low.includes("_") || low.startsWith("mcp-server-"))) return "uvx";
  return "npx";
}

/** Strip a "pip:" / "pypi:" / "npm:" prefix off a package spec. */
export function barePackage(pkg: string): string {
  return pkg.trim().replace(/^(pip|pypi|npm):/i, "");
}

/** The launch command for a package under a launcher. `command` takes the
 *  text as a command line (first word runs, the rest are its args). */
export function buildLaunch(pkg: string, launcher: Launcher): { command: string; args: string[] } {
  const p = barePackage(pkg);
  switch (launcher) {
    case "uvx":
      return { command: "uvx", args: [p] };
    case "pipx":
      return { command: "pipx", args: ["run", p] };
    case "node":
      return { command: "node", args: [p] };
    case "python":
      return { command: "python", args: ["-m", p] };
    case "command": {
      const words = p.split(/\s+/).filter(Boolean);
      return { command: words[0] ?? "", args: words.slice(1) };
    }
    default:
      return { command: "npx", args: ["-y", p] };
  }
}

/** The runtime a stdio command needs, in the catalog's words. */
export function needsFor(command: string | null | undefined): string {
  const c = (command ?? "").trim().toLowerCase().replace(/\.(cmd|exe)$/, "");
  if (c === "npx" || c === "node" || c === "npm") return "Node";
  if (c === "uvx" || c === "uv" || c === "python" || c === "pipx" || c === "python3") return "Python (uv)";
  if (c === "docker") return "Docker";
  return c ? c : "a runtime";
}

/** The StatusChip for a preview row: already added wins; an HTTP pack needs
 *  no runtime; a stdio pack reads the daemon's `runtime_ready`. */
export function rowStatus(
  row: PreviewRow,
): { status: "added" | "ready" | "blocked" | "available"; needs?: string } {
  if (row.exists) return { status: "added" };
  if (row.transport === "http" || row.transport === "sse") return { status: "available" };
  const needs = needsFor(row.command);
  if (row.runtime_ready === true) return { status: "ready", needs };
  return { status: "blocked", needs };
}

/** The one-line launch of a row: the command line, or the URL. */
export function rowLaunch(row: PreviewRow): string {
  if (row.transport === "http" || row.transport === "sse") return row.url ?? "";
  return [row.command ?? "", ...(row.args ?? [])].filter(Boolean).join(" ");
}

/* ------------------------------------------------------------- edit patch */

/** What the editor knows about a pack when it opens (names only, never a
 *  value — the daemon sends none). */
export interface EditSeed {
  name: string;
  transport: string;
  command: string;
  args: string[];
  cwd: string;
  url: string;
  envKeys: string[];
  secretKeys: string[];
  headerNames: string[];
  tokenSet: boolean;
}

/** The editor's state. Strings the user typed; `envValues` holds typed
 *  values for existing or new plain keys; `secretValues` typed replacements;
 *  `clearSecrets` / `clearHeaders` the keys pressed Clear; `token` a typed
 *  replacement (null = untouched), `clearToken` the Clear press. */
export interface EditState {
  command: string;
  args: string;
  cwd: string;
  transport: string;
  url: string;
  envValues: Record<string, string>;
  secretValues: Record<string, string>;
  clearSecrets: string[];
  headerValues: Record<string, string>;
  clearHeaders: string[];
  token: string | null;
  clearToken: boolean;
}

export function seedState(seed: EditSeed): EditState {
  return {
    command: seed.command,
    args: seed.args.join(" "),
    cwd: seed.cwd,
    transport: seed.transport,
    url: seed.url,
    envValues: {},
    secretValues: {},
    clearSecrets: [],
    headerValues: {},
    clearHeaders: [],
    token: null,
    clearToken: false,
  };
}

/** Split an args line into argv (whitespace separated, quotes kept as
 *  typed — the daemon runs argv, not a shell). */
export function splitArgs(line: string): string[] {
  return line.split(/\s+/).map((t) => t.trim()).filter(Boolean);
}

/** The PARTIAL body for PATCH /mcp/servers/{name}: only what changed. A
 *  field the user left as it was is omitted (= keep); a value typed as ""
 *  is sent as "" (a value, never a clear); Clear presses ride the clear
 *  lists. Returns {} when nothing changed. */
export function editPatch(seed: EditSeed, st: EditState): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (st.command !== seed.command) body.command = st.command;
  const args = splitArgs(st.args);
  if (args.join("\u0000") !== seed.args.join("\u0000")) body.args = args;
  if (st.cwd !== seed.cwd) body.cwd = st.cwd;
  if (st.transport !== seed.transport) body.transport = st.transport;
  if (st.url !== seed.url) body.url = st.url;
  const env: Record<string, string> = {};
  for (const [k, v] of Object.entries(st.envValues)) {
    if (k.trim() && v !== undefined) env[k.trim()] = v;
  }
  for (const [k, v] of Object.entries(st.secretValues)) {
    if (k.trim() && v !== "" && !st.clearSecrets.includes(k)) env[k.trim()] = v;
  }
  if (Object.keys(env).length > 0) body.env = env;
  if (st.clearSecrets.length > 0) body.clear_secrets = [...st.clearSecrets];
  const headers: Record<string, string> = {};
  for (const [k, v] of Object.entries(st.headerValues)) {
    if (k.trim() && v !== "" && !st.clearHeaders.includes(k)) headers[k.trim()] = v;
  }
  if (Object.keys(headers).length > 0) body.headers = headers;
  if (st.clearHeaders.length > 0) body.clear_headers = [...st.clearHeaders];
  if (st.clearToken) body.auth = null;
  else if (st.token !== null && st.token !== "") body.auth = bearerAuth(st.token);
  return body;
}

/** The auth shape for a bearer token (the contract's one shape). */
export function bearerAuth(value: string): { header: string; format: string; value: string } {
  return { header: "Authorization", format: "Bearer {value}", value };
}

/** Read the editor's seed off a GET /mcp/servers row. Reads KEY and header
 *  NAMES only: a row never carries a value, and if an older daemon's did,
 *  this is where it is dropped. */
export function seedFromRow(row: Record<string, unknown>): EditSeed {
  const str = (v: unknown) => (typeof v === "string" ? v : "");
  const list = (v: unknown) => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : []);
  const headers = row.headers;
  const headerNames =
    list(row.header_names).length > 0
      ? list(row.header_names)
      : headers && typeof headers === "object"
        ? Object.keys(headers as Record<string, unknown>)
        : [];
  const auth = row.auth;
  const tokenSet =
    !!auth &&
    typeof auth === "object" &&
    (Boolean((auth as Record<string, unknown>).value_set) ||
      typeof (auth as Record<string, unknown>).secret === "string");
  const envKeys = list(row.env_keys).length > 0 || !row.env ? list(row.env_keys) : Object.keys(row.env as object);
  return {
    name: str(row.name),
    transport: str(row.transport) || "stdio",
    command: str(row.command),
    args: list(row.args),
    cwd: str(row.cwd),
    url: str(row.url),
    envKeys,
    secretKeys: list(row.secret_keys),
    headerNames,
    tokenSet,
  };
}

/* ----------------------------------------------------------- http door */

/** The POST /mcp/servers body for the HTTP door. Only non-empty headers
 *  ride; a blank token sends no auth. */
export function httpServerBody(input: {
  name: string;
  url: string;
  transport: "http" | "sse";
  headers: { name: string; value: string }[];
  token: string;
}): Record<string, unknown> {
  const headers: Record<string, string> = {};
  for (const h of input.headers) {
    const n = h.name.trim();
    if (n) headers[n] = h.value;
  }
  const body: Record<string, unknown> = {
    name: safeName(input.name) || nameFromUrl(input.url),
    transport: input.transport,
    url: input.url.trim(),
    headers,
  };
  if (input.token.trim()) body.auth = bearerAuth(input.token.trim());
  return body;
}
