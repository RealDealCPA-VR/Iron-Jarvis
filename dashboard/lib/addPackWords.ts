/**
 * v1.354.0 (G49 "import that explains itself", G50 "import reads the files
 * current tools use and respects off"): the words and small rules of the
 * Tools page's add area (components/tools/AddPack.tsx), kept pure so the
 * tests read them without a DOM.
 *
 *  - the doors' names: "Bring your tools from Claude, Codex, Cursor…" first,
 *    the paste box as "Paste from a website or README (advanced)";
 *  - what a found tool does in ONE line: the catalog's blurb the daemon
 *    matched by package (`blurb`), else "A tool from <app>";
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
export type ImportRow = PreviewRow & {
  /** The app it came from has this server switched off. */
  disabled?: boolean | null;
  /** How it is already here: "pack" | "app" | "memory" ("" = new). */
  exists_as?: string | null;
  /** The catalog's one line about the package ("" = not in the catalog). */
  blurb?: string | null;
};

/** The doors, in the order a new user meets them. */
export const DOOR_LABELS = {
  sources: "Bring your tools from Claude, Codex, Cursor…",
  paste: "Paste from a website or README (advanced)",
  http: "HTTP server",
} as const;

/** Where the paste door's rows came from, said in the row's words. */
export const PASTE_APP = "the pasted text";

/** What a found tool does, in one line: the catalog's blurb when the daemon
 *  matched the package, else where it came from. */
export function rowAbout(row: ImportRow, app: string): string {
  const blurb = typeof row.blurb === "string" ? row.blurb.trim() : "";
  if (blurb) return blurb;
  return `A tool from ${app || "another app"}`;
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

/** The badge for a row that is already here, by how it is here. */
export function existsWords(row: ImportRow): string {
  if (row.exists_as === "app") return "already connected";
  if (row.exists_as === "memory") return "already a memory source";
  return "already added";
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
export const USE_IN_CHAT = "Use it in a chat: + › Connections";

/** The tail of the not-started sentence (lib/packStartWords) that the add
 *  area turns into a button that goes to the row. */
export const ROW_TAIL = "Open its row below to retry.";

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
): { anchor: HTMLElement; retry: HTMLElement | null; output: HTMLDetailsElement | null } | null {
  if (!doc || !name) return null;
  const n = attr(name);
  const row = doc.querySelector<HTMLElement>(`[data-pack-row="${n}"]`);
  const scope: ParentNode = row ?? doc;
  const retry = scope.querySelector<HTMLElement>(`[data-testid="mcp-retry-${n}"]`);
  const output = scope.querySelector<HTMLDetailsElement>(`details[data-testid="mcp-stderr-${n}"]`);
  const badge = scope.querySelector<HTMLElement>(`[data-testid="mcp-state-${n}"]`);
  const anchor = row ?? retry ?? badge;
  return anchor ? { anchor, retry, output } : null;
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
  const target = retry ?? anchor;
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
