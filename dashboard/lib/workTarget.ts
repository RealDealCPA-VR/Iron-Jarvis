/**
 * Calm chat wave 4 F1 (v1.329.0): what a tool step was done TO, in a few safe
 * words, so a folded "Worked for …" row can read "Read sales-harbor-st.xlsx"
 * or "Searched the web for pier 9 hours" after the turn has ended.
 *
 * A step's target is taken from the call's arguments on the stream lane and
 * saved on the message with the step. It is deliberately SMALL and SAFE:
 *   - a file or folder: its base name only, never the full path;
 *   - a search: the query or pattern, one line, cut to ~60 characters;
 *   - a web page: the site's host only (never the path, the query string or
 *     anything before an "@");
 *   - a command: the program's name only ("git"), never its arguments;
 *   - any tool this file does not know by its exact name: nothing at all,
 *     because the app does not describe what it cannot vouch for.
 * A value that looks like a credential is dropped whole (the row then falls
 * back to the tool's id), never masked into something that still hints at it.
 *
 * Kept outside lib/useChatStream (many tests mock that module with a fixed
 * export list) and outside components/ (the hook imports this file).
 */

import { looksLikeSecret } from "@/lib/configCards";

export type WorkKind =
  | "read"
  | "make"
  | "change"
  | "folder"
  | "search"
  | "web"
  | "page"
  | "command"
  | "tool";

/** Built-in tools by EXACT name. Anything else is a plain "tool". */
const KIND_OF: Record<string, WorkKind> = {
  read_file: "read",
  read_document: "read",
  extract_pdf: "read",
  view_image: "read",
  excel_read: "read",
  excel_profile: "read",
  image_info: "read",
  pdf_form_fields: "read",
  write_file: "make",
  write_document: "make",
  convert_document: "make",
  image_convert: "make",
  image_resize: "make",
  pdf_split: "make",
  redact_pii: "make",
  edit_file: "change",
  docx_edit: "change",
  excel_edit: "change",
  excel_apply_spec: "change",
  rename_file: "change",
  pdf_arrange: "change",
  pdf_form_fill: "change",
  list_files: "folder",
  list_folder: "folder",
  grep: "search",
  web_search: "web",
  web_fetch: "page",
  shell: "command",
  run_code: "command",
  repl: "command",
};

/** The kind of a tool id. Exact match only: never trimmed, never a prefix. */
export function toolKind(name: string): WorkKind {
  return typeof name === "string" && Object.prototype.hasOwnProperty.call(KIND_OF, name)
    ? KIND_OF[name]
    : "tool";
}

/** The longest target kept, in characters (the cut adds one "…"). */
export const TARGET_MAX = 60;

/** Shapes the receipt must never carry, beyond lib/configCards' key shapes. */
const EXTRA_SECRET_SHAPES: RegExp[] = [
  // key=value or key: value for words that name a credential.
  /\b(?:pass(?:word|wd)?|pwd|secret|token|api[_-]?key|apikey|access[_-]?key|auth|credential|private[_-]?key|session[_-]?id)\s*[:=]\s*\S/i,
  /-----BEGIN [A-Z ]*KEY-----/,
  // A JSON web token.
  /\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\./,
];

/** How much of a value is checked: everything that could be shown, and more. */
const CHECK_SPAN = 240;

/** A long unbroken run that reads like a key or a hash, not like words:
 *  32+ letters and digits (both present), or 40+ with "_" / "-" when it
 *  mixes upper case, lower case and digits. File names made of words
 *  joined by "_" or "-" are not caught by either. */
function keyLikeRun(text: string): boolean {
  for (const run of text.match(/[A-Za-z0-9+/_-]{32,}/g) ?? []) {
    const plain = /^[A-Za-z0-9+/]+$/.test(run);
    const digits = /\d/.test(run);
    const letters = /[A-Za-z]/.test(run);
    if (plain && digits && letters) return true;
    if (run.length >= 40 && digits && /[a-z]/.test(run) && /[A-Z]/.test(run)) return true;
  }
  return false;
}

/** True when the text looks like it carries a credential. */
export function secretLooking(text: string): boolean {
  if (!text) return false;
  const head = text.slice(0, CHECK_SPAN);
  return looksLikeSecret(head) || EXTRA_SECRET_SHAPES.some((rx) => rx.test(head)) || keyLikeRun(head);
}

/** One line of plain text: control characters out, whitespace collapsed. */
function oneLine(s: string): string {
  return s.replace(/[\u0000-\u001f\u007f]+/g, " ").replace(/\s+/g, " ").trim();
}

function cut(s: string): string {
  return s.length > TARGET_MAX ? `${s.slice(0, TARGET_MAX - 1).trimEnd()}…` : s;
}

/**
 * A saved or computed target made safe to show: one line, at most
 * TARGET_MAX characters, null when blank or credential-shaped. Applied when
 * the target is made AND again when a saved one is read back (a thread file
 * crosses a JSON boundary and may be older or hand-edited).
 */
export function cleanTarget(v: unknown): string | null {
  if (typeof v !== "string") return null;
  const line = oneLine(v);
  if (!line || secretLooking(line)) return null;
  return cut(line);
}

/** The last segment of a path, either separator; "" when there is none. */
function baseName(p: string): string {
  const parts = p.trim().split(/[/\\]/).filter(Boolean);
  const last = parts.length ? parts[parts.length - 1] : "";
  // A bare drive ("C:") is not a name.
  return /^[A-Za-z]:$/.test(last) ? "" : last;
}

function str(args: Record<string, unknown>, key: string): string | null {
  const v = args[key];
  return typeof v === "string" && v.trim() ? v : null;
}

const PATH_KEYS = ["path", "file_path", "file", "filename", "source", "src", "folder", "directory", "dir", "root"];
/** A tool that MAKES a file names the file it made, not the one it read. */
const MADE_KEYS = ["target", "output", "out_dir", ...PATH_KEYS];

function fileTarget(args: Record<string, unknown>, keys: string[]): string | null {
  for (const k of keys) {
    const v = str(args, k);
    if (v) return cleanTarget(baseName(v));
  }
  const paths = args.paths;
  if (Array.isArray(paths)) {
    const names = paths.filter((p): p is string => typeof p === "string" && !!p.trim()).map(baseName).filter(Boolean);
    if (names.length) {
      const first = cleanTarget(names[0]);
      if (!first) return null;
      return names.length > 1 ? `${first} and ${names.length - 1} more` : first;
    }
  }
  return null;
}

/** A search that holds a full path keeps only the path's last segment
 *  ("C:\Users\me\notes.txt" → "notes.txt"); a URL keeps only its host. */
function scrubPaths(q: string): string {
  return q
    .split(/\s+/)
    .map((w) => {
      if (/^[a-z][a-z0-9+.-]*:\/\//i.test(w)) return hostOf(w) ?? "";
      // A drive path, a network path, a home path, or an absolute path of two
      // or more folders. (A regex like "\bTODO\b" is not a path.)
      if (/^[A-Za-z]:[\\/]/.test(w) || /^\\\\[^\\]/.test(w) || /^~[\\/]/.test(w) || /^\/[^/\s]+\/./.test(w)) {
        return baseName(w);
      }
      return w;
    })
    .filter(Boolean)
    .join(" ");
}

/** The site of a URL: its host, lower case, with no "www.". */
function hostOf(raw: string): string | null {
  let u: URL;
  try {
    u = new URL(raw.trim());
  } catch {
    return null;
  }
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  const host = u.hostname.toLowerCase().replace(/^www\./, "");
  return host ? cleanTarget(host) : null;
}

/** A command's program: the first word, its base name, no ".exe". Null for
 *  anything that is not a plain program name (an assignment, a quote, …). */
function programOf(command: string): string | null {
  const s = command.trim();
  const quoted = /^(["'])(.*?)\1/.exec(s);
  const first = quoted ? quoted[2] : (s.split(/\s+/)[0] ?? "");
  if (!first || first.includes("=")) return null;
  const name = baseName(first).replace(/\.(exe|cmd|bat|ps1)$/i, "");
  return /^[A-Za-z][A-Za-z0-9._+-]{0,31}$/.test(name) ? name : null;
}

/**
 * The safe target of one call, or null when there is nothing safe to say.
 * `args` are the call's arguments as the stream lane carried them.
 */
export function stepTarget(name: string, args: Record<string, unknown> | null | undefined): string | null {
  if (!args || typeof args !== "object" || Array.isArray(args)) return null;
  const kind = toolKind(name);
  switch (kind) {
    case "read":
    case "change":
    case "folder":
      return fileTarget(args, PATH_KEYS);
    case "make":
      return fileTarget(args, MADE_KEYS);
    case "search":
    case "web": {
      const q = str(args, "query") ?? str(args, "q") ?? str(args, "pattern");
      return q ? cleanTarget(scrubPaths(q)) : null;
    }
    case "page": {
      const u = str(args, "url");
      return u ? hostOf(u) : null;
    }
    case "command": {
      if (name === "run_code") {
        const lang = str(args, "language");
        return lang && /^[A-Za-z][A-Za-z0-9+#.-]{0,19}$/.test(lang.trim()) ? lang.trim().toLowerCase() : null;
      }
      const c = str(args, "command");
      return c ? programOf(c) : null;
    }
    default:
      return null;
  }
}
