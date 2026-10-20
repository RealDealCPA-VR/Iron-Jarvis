/**
 * v1.354.0 (G49 "import that explains itself", G50 "import reads the files
 * current tools use and respects off"): the words and small rules of the
 * Tools page's add area (components/tools/AddPack.tsx), kept pure so the
 * tests read them without a DOM.
 *
 *  - the doors' names: "Bring your tools from Claude, Codex, Cursor…" first,
 *    the paste box as "Paste from a website or README (advanced)";
 *  - what a found tool does in ONE line: the catalog's blurb the daemon
 *    matched by package (`blurb`), else (v1.360.0, G49) what it IS from what
 *    the import already knows: its name in words, and the program it runs
 *    on this PC (`program`) or the host of its web address (`host`); once
 *    it is added, the tools it loaded take over that line;
 *  - "turned off in <app>" for a server its app has switched off (`disabled`)
 *    and the "already …" words for one connected here another way
 *    (`exists_as`);
 *  - the outcome: the tool names a pack loaded, "Use it in a chat: + ›
 *    Connections", and whether a reason names Node.js;
 *  - the HTTP door's transport read off the address (the twin of
 *    `mcp/import_config`'s rule: an address ending in /sse is SSE, else HTTP);
 *  - the jump to a pack's row on the Tools page (scroll, open, focus Retry).
 *
 * Dependency-free apart from the two word tables it reuses (lib/api.ts is
 * mocked wholesale by ~71 test files, so helpers live in their own file).
 */

import { appToolNameWords } from "@/lib/toolWords";
import type { PreviewRow } from "@/lib/mcpImport";

/* ------------------------------------------------------------------ rows */

/** A preview / source row as the v1.354.0 daemon sends it: the v1.343.0 row
 *  plus three keys an older daemon leaves out (read as "unknown", never as a
 *  claim). */
/** A preview row as the door reads it. v1.354.0 integrate: `disabled`,
 *  `exists_as` and `blurb` now live on `PreviewRow` itself (lib/mcpImport);
 *  the name stays for the callers that use it. v1.360.0 (G49): `program`
 *  (a stdio row: what its command line runs, read off the masked line by
 *  mcp/import_config.program_of) and `host` (an address row: its host). An
 *  older daemon sends neither, and the line then says less, never more. */
export type ImportRow = PreviewRow & { program?: string | null; host?: string | null };

/** The doors, in the order a new user meets them. */
export const DOOR_LABELS = {
  sources: "Bring your tools from Claude, Codex, Cursor…",
  paste: "Paste from a website or README (advanced)",
  http: "HTTP server",
} as const;

/** Where the paste door's rows came from, said in the row's words. */
export const PASTE_APP = "the pasted text";

/** Casing for words a server's name is often written with. Casing ONLY:
 *  nothing here says what a server does. */
const NAME_CASE: Readonly<Record<string, string>> = {
  ai: "AI",
  api: "API",
  aws: "AWS",
  crm: "CRM",
  csv: "CSV",
  db: "DB",
  erp: "ERP",
  gcp: "GCP",
  http: "HTTP",
  llm: "LLM",
  ocr: "OCR",
  pdf: "PDF",
  qb: "QB",
  sql: "SQL",
  ssh: "SSH",
  url: "URL",
  github: "GitHub",
  gitlab: "GitLab",
  hubspot: "HubSpot",
  linkedin: "LinkedIn",
  mongodb: "MongoDB",
  mysql: "MySQL",
  onedrive: "OneDrive",
  openai: "OpenAI",
  paypal: "PayPal",
  postgresql: "PostgreSQL",
  quickbooks: "QuickBooks",
  sharepoint: "SharePoint",
  sqlite: "SQLite",
  youtube: "YouTube",
};

/** Words that name the mechanism, not the thing ("quickbooks-mcp-server"). */
const MECHANISM_WORDS = new Set(["mcp", "server", "mcpserver"]);

/**
 * A server's name in words: "quickbooks-desktop" -> "QuickBooks Desktop",
 * "brave_search" -> "Brave Search", "mcp-server-git" -> "Git". A word the name
 * already capitalises keeps its case; "mcp"/"server" are dropped unless
 * nothing else is left. Never more than the name says.
 */
export function serverLabel(name: string): string {
  const raw = (name ?? "").trim();
  const words = raw.split(/[\s_.\-\/@]+/).filter(Boolean);
  const kept = words.filter((w) => !MECHANISM_WORDS.has(w.toLowerCase()));
  const shown = (kept.length > 0 ? kept : words).map((w) => {
    const known = NAME_CASE[w.toLowerCase()];
    if (known) return known;
    return /[A-Z]/.test(w) ? w : w.charAt(0).toUpperCase() + w.slice(1);
  });
  return shown.join(" ") || raw;
}

/** What a found server that runs on this PC is, after its name. */
export const RUNS_HERE = "runs a program on this PC";
/** What a found server reached by a web address is, after its name. */
export const AT_ADDRESS = "reached at a web address";

/** The host of an address, for an older daemon that sent no `host`. */
function hostFromUrl(url: string | null | undefined): string {
  try {
    return new URL((url ?? "").trim()).hostname.toLowerCase();
  } catch {
    return "";
  }
}

/**
 * What a found tool is, in one line.
 *  - once added, the tools it loaded take over ("QuickBooks Desktop · 3
 *    tools: create invoice, list customers and get report");
 *  - the catalog's blurb when the daemon matched the package;
 *  - else (v1.360.0, G49) its name in words and what the import knows about
 *    it: "QuickBooks Desktop · runs a program on this PC
 *    (quickbooks-desktop-mcp)" or "Linear · reached at a web address
 *    (mcp.linear.app)". Never a capability the import does not know.
 * `app` names where the rows came from; it is only said for a row with no
 * name at all.
 */
export function rowAbout(row: ImportRow, app: string, tools?: readonly string[] | null): string {
  const label = serverLabel(row.name ?? "");
  const loaded = (tools ?? []).filter((t) => typeof t === "string" && t.trim() !== "");
  if (label && loaded.length > 0) {
    return `${label} · ${loaded.length} tool${loaded.length === 1 ? "" : "s"}: ${toolNamesLine(loaded)}`;
  }
  const blurb = typeof row.blurb === "string" ? row.blurb.trim() : "";
  if (blurb) return blurb;
  if (!label) return `A tool from ${app || "another app"}`;
  if (row.transport === "http" || row.transport === "sse") {
    const host = (typeof row.host === "string" ? row.host.trim() : "") || hostFromUrl(row.url);
    return host ? `${label} · ${AT_ADDRESS} (${host})` : `${label} · ${AT_ADDRESS}`;
  }
  const program = typeof row.program === "string" ? row.program.trim() : "";
  return program ? `${label} · ${RUNS_HERE} (${program})` : `${label} · ${RUNS_HERE}`;
}

/** True when the app it came from has it switched off. Only a real `true`
 *  counts: an older daemon sends nothing and nothing is claimed. */
export function isOff(row: ImportRow): boolean {
  return row.disabled === true;
}

/** "turned off in Claude Desktop". */
export function offWords(app: string): string {
  return `turned off in ${app || "its app"}`;
}

/** The badge for a row that is already here, by how it is here. v1.355.0:
 *  a pack added under another spelling of the name says that name. */
export function existsWords(row: ImportRow): string {
  if (row.exists_as === "app") return "already connected";
  if (row.exists_as === "memory") return "already a memory base";
  const other = typeof row.exists_name === "string" ? row.exists_name.trim() : "";
  if (other && other !== row.name) return `already added as ${other}`;
  return "already added";
}

/** v1.355.0: a name that reads as a secret gets a hidden input — the twin
 *  of mcp/secrets.SECRET_KEY_RX (a key, a token, a password, an auth header,
 *  a connection string or a `_URL`). */
const SECRET_NAME_RX = /(token|key|secret|passw|auth|credential|dsn|connection|(?:^|[_.-])pat(?:$|[_.-])|_url$)/i;

export function looksSecretName(name: string): boolean {
  return SECRET_NAME_RX.test(name ?? "");
}

/** v1.355.0: the names a row's inputs ask for — the secret env keys, then
 *  every placeholder the config left (`asks`), each once. */
export function askedNames(row: ImportRow): string[] {
  const out: string[] = [];
  for (const n of [...(row.secret_keys ?? []), ...(row.asks ?? [])]) {
    if (typeof n === "string" && n && !out.includes(n)) out.push(n);
  }
  return out;
}

/** The hint inside one input: a placeholder must be typed; a secret key the
 *  config may already hold is optional. */
export function askHint(row: ImportRow, name: string): string {
  return (row.asks ?? []).includes(name)
    ? "Type its value (the config left a placeholder)"
    : "Type a value if the config has none";
}

/** v1.355.0: a sign-in of the same name is NOT this server (a Google Drive
 *  sign-in gives memory search; the server gives its own tools). One quiet
 *  line under the row; "" when there is none. */
export function signedInWords(row: ImportRow): string {
  if (row.exists) return "";
  const app = typeof row.signed_in_app === "string" ? row.signed_in_app.trim() : "";
  if (app) return `You're signed in to ${app} here for memory search; this adds the server's own tools.`;
  // v1.356.0 integrate: a saved KEY of the same name (Pixio's) is not this
  // server either — it powers a built-in tool.
  const keyed = typeof row.key_app === "string" ? row.key_app.trim() : "";
  if (keyed) return `Your ${keyed} key is saved here for its built-in tools; this adds the server's own tools.`;
  return "";
}

/** A row ticked when the door opens: new here and switched on in its app. */
export function startsTicked(row: ImportRow): boolean {
  return !row.exists && !isOff(row);
}

/* --------------------------------------------------------------- outcome */

/** How many tool names the outcome lists before "and N more". */
export const TOOL_NAMES_SHOWN = 6;

/** "search repositories, get issue and 4 more"; "" for none. */
export function toolNamesLine(names: readonly string[] | null | undefined, shown = TOOL_NAMES_SHOWN): string {
  const words = (names ?? [])
    .filter((n): n is string => typeof n === "string" && n.trim() !== "")
    .map((n) => appToolNameWords(n));
  if (words.length === 0) return "";
  if (words.length <= shown) {
    if (words.length === 1) return words[0];
    return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
  }
  return `${words.slice(0, shown).join(", ")} and ${words.length - shown} more`;
}

/** Where a pack's tools are switched on for a conversation. */
// v1.358.0 (integrate): the chat's Connections switches sit in the Tools
// menu (the wrench chip), never a "+" menu.
export const USE_IN_CHAT = "Use it in a chat: Tools › Connections";

/** The tail of the not-started sentence (lib/packStartWords) that the add
 *  area turns into a button that goes to the row. */
export const ROW_TAIL = "Open its row below to retry.";

/** v1.356.0 (G78): a server its app had switched off, ticked anyway, is
 *  added SWITCHED OFF (the apply answer's `state: "off"`): saved with its
 *  values, loaded when the user turns it on. Not a failure. */
export function addedSwitchedOff(added: { state?: string | null } | null | undefined): boolean {
  return added?.state === "off";
}

/** What the outcome says for such a row, and the press that goes to it. */
export const ADDED_OFF_WORDS = "Added switched off, as it was in its app.";
export const ADDED_OFF_TAIL = "Turn it on in its row below.";
export const ADDED_OFF_FAR = "Turn it on in Tools.";

/** The row's on/off switch (components/tools/PackSwitch on the Tools row). */
const SWITCH_TESTID = "mcp-switch-";

/** Split "Added, but it didn't start: x. Open its row below to retry." into
 *  the sentence and the tail, so the tail can be a button. A sentence without
 *  the tail comes back whole with an empty tail. */
export function splitRowTail(sentence: string): { head: string; tail: string } {
  const s = (sentence ?? "").trim();
  if (s.endsWith(ROW_TAIL)) return { head: s.slice(0, -ROW_TAIL.length).trimEnd(), tail: ROW_TAIL };
  return { head: s, tail: "" };
}

/** True when a reason or a fix names Node.js (npx and npm come with it). */
export function mentionsNode(text: string | null | undefined): boolean {
  return /node\.?js|\bnpx\b|\bnpm\b/i.test(text ?? "");
}

/** Where the Tools page's pack rows live, from a page that does not show
 *  them (the Directory): the relocated alias, so Settings opens the section
 *  (the v1.353.0 rule: never `/settings?section=` between sections). */
export const TOOLS_ROWS_HREF = "/tools?focus=packs";

/* ------------------------------------------------------------- http door */

/** The HTTP door's transport from the address: one ending in /sse (query and
 *  trailing slashes aside) is SSE, anything else HTTP. The twin of
 *  `mcp/import_config`'s URL rule; the door still sends the choice. */
export function transportFromUrl(url: string): "http" | "sse" {
  const raw = (url ?? "").trim();
  let path = raw;
  try {
    path = new URL(raw).pathname;
  } catch {
    path = raw.split(/[?#]/, 1)[0];
  }
  return path.toLowerCase().replace(/\/+$/, "").endsWith("/sse") ? "sse" : "http";
}

/* -------------------------------------------------------- jump to a row */

function attr(value: string): string {
  const css = (globalThis as { CSS?: { escape?: (s: string) => string } }).CSS;
  if (css?.escape) return css.escape(value);
  return value.replace(/["\\]/g, "\\$&");
}

/** The Tools page row of pack `name`, as far as the page can be read: an
 *  element marked `data-pack-row="<name>"` when the page carries one, else
 *  the row's own Retry (every pack that did not start shows one), else its
 *  state badge. Null when the page shows no row for it. */
export function packRowParts(
  name: string,
  doc: Document | null = typeof document === "undefined" ? null : document,
): {
  anchor: HTMLElement;
  retry: HTMLElement | null;
  output: HTMLDetailsElement | null;
  toggle?: HTMLElement | null;
} | null {
  if (!doc || !name) return null;
  const n = attr(name);
  const row = doc.querySelector<HTMLElement>(`[data-pack-row="${n}"]`);
  const scope: ParentNode = row ?? doc;
  const retry = scope.querySelector<HTMLElement>(`[data-testid="mcp-retry-${n}"]`);
  const output = scope.querySelector<HTMLDetailsElement>(`details[data-testid="mcp-stderr-${n}"]`);
  const badge = scope.querySelector<HTMLElement>(`[data-testid="mcp-state-${n}"]`);
  // v1.356.0 (G78): a switched-off row has no Retry; its next step is its
  // on/off switch.
  const toggle = scope.querySelector<HTMLElement>(`[data-testid="${SWITCH_TESTID}${n}"]`);
  const anchor = row ?? retry ?? badge;
  return anchor ? { anchor, retry, output, toggle } : null;
}

function prefersReducedMotion(): boolean {
  try {
    return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  } catch {
    return false;
  }
}

/**
 * Scroll to pack `name`'s row, open its folded last output and put the focus
 * on its Retry (the one next step). False when the page shows no such row —
 * the caller then goes to the Tools page instead. No smooth scroll under
 * prefers-reduced-motion.
 */
export function showPackRow(name: string, doc?: Document | null): boolean {
  const parts = packRowParts(name, doc);
  if (!parts) return false;
  const { anchor, retry, output } = parts;
  try {
    anchor.scrollIntoView({ block: "center", behavior: prefersReducedMotion() ? "auto" : "smooth" });
  } catch {
    try {
      anchor.scrollIntoView();
    } catch {
      /* an engine without scrollIntoView: the focus below still moves */
    }
  }
  if (output) output.open = true;
  const target = retry ?? parts.toggle ?? anchor;
  // A row or a badge is not focusable by itself; -1 lets the focus land
  // there without adding a stop to the page's tab order.
  if (!target.matches("a[href],button,input,select,textarea,summary,[tabindex]")) {
    target.setAttribute("tabindex", "-1");
  }
  try {
    target.focus({ preventScroll: true });
  } catch {
    target.focus();
  }
  return true;
}
