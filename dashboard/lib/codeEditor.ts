/**
 * v1.405.0 — "Open in VS Code / Cursor at this line" in a CODING SESSION.
 *
 * The daemon (src/iron_jarvis/harness/code_editor.py) finds the editors on
 * this PC, builds the editor's URL itself and opens it: this file only says
 * WHICH file and line (`POST /editor/open {path, line?, column?}`), never a
 * URL. It holds the pure half:
 *
 *  - `readEditorOptions`: `GET /editor/options`, read ONCE per page visit
 *    (a failed read is forgotten, so the next mount asks again);
 *  - `EditorContext`: what a coding session (components/harness/
 *    HarnessSession) lends everything inside it — its folder, the editor's
 *    name and `open(path, line)`. The chat page provides NONE, so a step row
 *    or a report there shows nothing new;
 *  - `resolveInFolder`: a path as a step or a finding names it, made absolute
 *    against the session's folder. Never a URL, never a network share, never
 *    `..` out of the folder; a finding's path must stay INSIDE the folder;
 *  - `stepEditorTarget`: the file (and line) a coding step worked on, read
 *    from its live args or its saved, redacted `detail.input` (cut at 300
 *    characters, so read defensively: a half path opens nothing);
 *  - `findEditorRefs`: the `path:line`, `path:line:col` and `path#L12`
 *    mentions in a reviewer's report (a TIGHT pattern: a path with a slash or
 *    a known source extension, then a line).
 */

import { createContext, useContext } from "react";
import { get, post } from "@/lib/api";

/* ----------------------------------------------------------- the options */

export type EditorId = "vscode" | "cursor";

export interface EditorRow {
  id: EditorId;
  label: string;
  /** True / false on Windows; null = not known (elsewhere). */
  installed: boolean | null;
}

export interface EditorOptions {
  editors: EditorRow[];
  /** The editor a press opens; null = no editor (no button). */
  preferred: EditorId | null;
  preferredLabel: string;
  note: string;
}

const IDS = new Set(["vscode", "cursor"]);
const LABEL: Record<EditorId, string> = { vscode: "VS Code", cursor: "Cursor" };

const rec = (v: unknown): Record<string, unknown> | null =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;

/** `GET /editor/options`, checked at the JSON boundary; null = unreadable. */
export function decodeEditorOptions(raw: unknown): EditorOptions | null {
  const r = rec(raw);
  if (!r) return null;
  const editors: EditorRow[] = [];
  for (const item of Array.isArray(r.editors) ? r.editors : []) {
    const e = rec(item);
    if (!e || typeof e.id !== "string" || !IDS.has(e.id)) continue;
    const id = e.id as EditorId;
    editors.push({
      id,
      label: typeof e.label === "string" && e.label.trim() ? e.label.trim().slice(0, 40) : LABEL[id],
      installed: typeof e.installed === "boolean" ? e.installed : null,
    });
  }
  const preferred = typeof r.preferred === "string" && IDS.has(r.preferred) ? (r.preferred as EditorId) : null;
  return {
    editors,
    preferred,
    preferredLabel: preferred
      ? typeof r.preferred_label === "string" && r.preferred_label.trim()
        ? r.preferred_label.trim().slice(0, 40)
        : LABEL[preferred]
      : "",
    note: typeof r.note === "string" ? r.note.slice(0, 400) : "",
  };
}

let optionsRead: Promise<EditorOptions | null> | null = null;

/** The options, read once per page visit. A failed read is forgotten (the
 *  next caller asks again); an answer — even "no editor" — is kept. */
export function readEditorOptions(): Promise<EditorOptions | null> {
  if (!optionsRead) {
    // Through a resolved promise: a `get` that throws (or a test's stand-in
    // that answers nothing) is a failed read, never a crash in an effect.
    optionsRead = Promise.resolve()
      .then(() => get<unknown>("/editor/options"))
      .then((raw) => decodeEditorOptions(raw))
      .catch(() => {
        optionsRead = null;
        return null;
      });
  }
  return optionsRead;
}

/** Forget the read (tests; a new page visit starts empty anyway). */
export function forgetEditorOptions(): void {
  optionsRead = null;
}

/** Ask the daemon to open `path` at `line` in the preferred editor. Throws
 *  the daemon's own sentence (an ApiError) when it refuses. */
export async function openInEditor(path: string, line?: number | null, column?: number | null): Promise<void> {
  const body: Record<string, unknown> = { path };
  if (typeof line === "number" && line >= 1) body.line = Math.floor(line);
  if (typeof column === "number" && column >= 1) body.column = Math.floor(column);
  await post<unknown>("/editor/open", body);
}

/** The words for a press that failed: the daemon's sentence, else a plain
 *  fallback. */
export function openFailedWords(e: unknown, label: string): string {
  const msg = e instanceof Error ? e.message.trim() : "";
  return msg || `Iron Jarvis could not open ${label}.`;
}

/* ----------------------------------------------------------- the context */

/** What a coding session lends the rows inside it. */
export interface EditorOpener {
  /** The session's folder (relative paths are read against it). */
  folder: string;
  /** "VS Code" / "Cursor": the editor a press opens. */
  label: string;
  /** Open an ABSOLUTE path (already resolved) at a line. Throws the
   *  daemon's sentence on a refusal. */
  open: (path: string, line?: number | null, column?: number | null) => Promise<void>;
}

/** Null outside a coding session (the chat page): no button anywhere. */
export const EditorContext = createContext<EditorOpener | null>(null);

export function useEditor(): EditorOpener | null {
  return useContext(EditorContext);
}

/* ------------------------------------------------------------ the paths */

const DRIVE_RE = /^[A-Za-z]:[\\/]/;
/** A scheme ("https:", "file:", "vscode:") — a drive letter is ONE letter. */
const SCHEME_RE = /^[A-Za-z][A-Za-z0-9+.-]+:/;

function windowsish(folder: string): boolean {
  return DRIVE_RE.test(folder) || folder.includes("\\");
}

/** Path segments with "." dropped and ".." applied; null when ".." climbs
 *  above the first segment of `base` (the root, or the folder). */
function normalize(base: string[], rest: string[], floor: number): string[] | null {
  const out = [...base];
  for (const seg of rest) {
    if (!seg || seg === ".") continue;
    if (seg === "..") {
      if (out.length <= floor) return null;
      out.pop();
      continue;
    }
    out.push(seg);
  }
  return out;
}

const splitPath = (p: string): string[] => p.split(/[\\/]+/).filter((s) => s.length > 0);

/** Is `inner` (segments) inside `outer` (segments)? Case-blind on Windows. */
function within(inner: string[], outer: string[], caseBlind: boolean): boolean {
  if (inner.length < outer.length) return false;
  for (let i = 0; i < outer.length; i++) {
    const a = caseBlind ? inner[i].toLowerCase() : inner[i];
    const b = caseBlind ? outer[i].toLowerCase() : outer[i];
    if (a !== b) return false;
  }
  return true;
}

/**
 * A path a step or a finding names, made ABSOLUTE against the session's
 * folder — or null:
 *  - never a URL ("https://…", "file:…", "vscode:…"), never a network share
 *    or a device path ("\\host\share", "//host/x"), never a masked value;
 *  - a RELATIVE path is joined to the folder and must stay inside it ("..");
 *  - an ABSOLUTE path is kept as it is, unless `confine` (a reviewer's
 *    finding — model-written text): then it too must be inside the folder.
 * The daemon checks the file again (the file policy, that it exists).
 */
export function resolveInFolder(folder: string, raw: string, opts: { confine?: boolean } = {}): string | null {
  const p = (raw ?? "").trim();
  if (!p || p.length > 2000 || /[\u0000-\u001f]/.test(p) || p.includes("***")) return null;
  if (p.startsWith("\\\\") || p.startsWith("//")) return null;
  if (SCHEME_RE.test(p) && !DRIVE_RE.test(p)) return null;
  const base = (folder ?? "").trim();
  const win = windowsish(base) || DRIVE_RE.test(p) || p.includes("\\");
  const sep = win ? "\\" : "/";
  const folderSegs = splitPath(base);
  const absolute = DRIVE_RE.test(p) || (!win && p.startsWith("/"));
  if (/^[A-Za-z]:(?![\\/])/.test(p)) return null; // "C:foo" — relative to a drive's own folder
  if (absolute) {
    const segs = normalize([], splitPath(p), 1);
    if (!segs || segs.length < 2) return null;
    if (opts.confine) {
      if (!base || !within(segs, folderSegs, win)) return null;
    }
    return win ? segs.join(sep) : `/${segs.join(sep)}`;
  }
  if (p.startsWith("/") || p.startsWith("\\")) return null; // rooted, no drive: ambiguous
  if (!base || !folderSegs.length) return null;
  const segs = normalize(folderSegs, splitPath(p), folderSegs.length);
  if (!segs || segs.length === folderSegs.length) return null;
  return DRIVE_RE.test(base) ? segs.join(sep) : `${base.startsWith("/") ? "/" : ""}${segs.join(sep)}`;
}

/** The last segment of a path, either separator. */
export function pathBase(p: string): string {
  const parts = splitPath(p);
  return parts.length ? parts[parts.length - 1] : p;
}

/* ---------------------------------------------------------- the steps */

/** The coding tools whose step worked on ONE file the editor can open. */
export const EDITOR_STEP_TOOLS: ReadonlySet<string> = new Set([
  "read_file",
  "write_file",
  "edit_file",
  "multi_edit",
  "apply_patch",
  "notebook_edit",
]);

/** A JSON string literal's body, decoded; null when it does not decode. */
function decodeJsonString(body: string): string | null {
  try {
    const v = JSON.parse(`"${body}"`);
    return typeof v === "string" ? v : null;
  } catch {
    return null;
  }
}

/** One string field of a (possibly cut) JSON object's text: only a value
 *  whose closing quote survived the cut — a half path opens nothing. */
function fieldOf(text: string, key: string): string | null {
  const re = new RegExp(`"${key}"\\s*:\\s*"((?:[^"\\\\]|\\\\.)*)"`);
  const m = re.exec(text);
  return m ? decodeJsonString(m[1]) : null;
}

function numberOf(text: string, key: string): number | null {
  const m = new RegExp(`"${key}"\\s*:\\s*(\\d{1,9})(?![\\d.])`).exec(text);
  return m ? Number(m[1]) : null;
}

/** The first file a V4A patch ADDS or UPDATES (a deleted one opens nothing),
 *  from the decoded patch text. */
function patchFile(patch: string): string | null {
  for (const line of patch.split(/\r?\n/)) {
    const m = /^\*\*\* (Add|Update) File: (.+)$/.exec(line.trim());
    if (m) return m[2].trim() || null;
  }
  return null;
}

/** The same from a cut JSON text: the header line must END (its escaped
 *  newline survived), so a path cut in the middle is never opened. */
function patchFileFromCut(text: string): string | null {
  const m = /\*\*\* (?:Add|Update) File: ((?:[^"\\]|\\(?![nr])[\s\S])+?)\\[nr]/.exec(text);
  return m ? decodeJsonString(m[1])?.trim() || null : null;
}

/**
 * What a coding step worked on, for "Open in <editor>": the file (as the
 * call named it — relative to the session's folder or absolute) and, for a
 * read with an `offset`, the line. Null for any other tool, a deleted file,
 * or nothing readable.
 */
export function stepEditorTarget(
  name: string,
  args?: Record<string, unknown> | null,
  input?: string | null,
): { path: string; line: number | null } | null {
  if (!EDITOR_STEP_TOOLS.has(name)) return null;
  const pick = (obj: Record<string, unknown>): { path: string; line: number | null } | null => {
    if (name === "apply_patch") {
      const patch = typeof obj.patch === "string" ? obj.patch : "";
      const file = patch ? patchFile(patch) : null;
      return file ? { path: file, line: null } : null;
    }
    let path = typeof obj.path === "string" ? obj.path : typeof obj.file_path === "string" ? obj.file_path : "";
    if (!path && name === "read_file" && Array.isArray(obj.paths) && typeof obj.paths[0] === "string") {
      path = obj.paths[0];
    }
    if (!path.trim()) return null;
    const off = obj.offset;
    const line =
      name === "read_file" && typeof off === "number" && Number.isFinite(off) && off >= 1 ? Math.floor(off) : null;
    return { path: path.trim(), line };
  };
  if (args && typeof args === "object") {
    const got = pick(args);
    if (got) return got;
  }
  const text = typeof input === "string" ? input : "";
  if (!text.trim()) return null;
  try {
    const parsed = rec(JSON.parse(text));
    if (parsed) return pick(parsed);
  } catch {
    /* cut at 300 characters: read the parts that survived */
  }
  if (name === "apply_patch") {
    const file = patchFileFromCut(text);
    return file ? { path: file, line: null } : null;
  }
  const path = fieldOf(text, "path") ?? fieldOf(text, "file_path");
  if (!path || !path.trim()) return null;
  const off = name === "read_file" ? numberOf(text, "offset") : null;
  return { path: path.trim(), line: off && off >= 1 ? off : null };
}

/* ------------------------------------------------------ a report's refs */

/** The source-file endings a bare name (no folder) must have to count. */
const SOURCE_EXT = new Set(
  (
    "py pyi ts tsx js jsx mjs cjs json jsonc md mdx toml yaml yml css scss sass less html htm rs go java kt kts " +
    "c h cc cpp cxx hpp hh cs rb php sh bash zsh ps1 psm1 bat cmd sql vue svelte txt cfg ini xml swift lua r dart " +
    "scala ex exs erl hs ml fs clj zig nim tf gradle ipynb proto graphql gql"
  ).split(" "),
);

/**
 * `path:line`, `path:line:col`, `path#L12` (`#L12C3`, `#L12-L20`). The path
 * is one run of path characters — an optional drive, then segments — that
 * does not follow a letter, a dot, a slash or a colon (so the tail of a URL
 * or of a longer word is never a match).
 */
const REF_RE =
  /(?<![\w.\/\\:@~%-])((?:[A-Za-z]:[\\/])?(?:[\w.@+~-]+[\\/])*[\w.@+~-]+)(?::(\d{1,8})(?::(\d{1,6}))?|#L(\d{1,8})(?:C(\d{1,6}))?(?:-L?\d{1,8})?)(?![\w\/\\])/g;

export interface EditorRef {
  start: number;
  end: number;
  /** The words as written ("src/app.py:12"). */
  text: string;
  path: string;
  line: number;
  column: number | null;
}

/** Every file-and-line mention in `text`, in order. */
export function findEditorRefs(text: string): EditorRef[] {
  const out: EditorRef[] = [];
  if (typeof text !== "string" || !text) return out;
  REF_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = REF_RE.exec(text))) {
    const path = m[1];
    const lineText = m[2] ?? m[4];
    const colText = m[3] ?? m[5];
    const line = Number(lineText);
    if (!Number.isFinite(line) || line < 1) continue;
    const hasSep = /[\\/]/.test(path);
    const dot = path.lastIndexOf(".");
    const ext = dot > 0 ? path.slice(dot + 1).toLowerCase() : "";
    if (!hasSep && !SOURCE_EXT.has(ext)) continue;
    if (hasSep && !/[\w]/.test(pathBase(path))) continue;
    // Never part of a URL: the whitespace-delimited word around it.
    const before = text.lastIndexOf(" ", m.index);
    const wordStart = Math.max(before, text.lastIndexOf("\n", m.index), text.lastIndexOf("\t", m.index)) + 1;
    if (/[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(text.slice(wordStart, m.index + m[0].length))) continue;
    out.push({
      start: m.index,
      end: m.index + m[0].length,
      text: m[0],
      path,
      line: Math.floor(line),
      column: colText ? Math.max(1, Math.floor(Number(colText))) : null,
    });
  }
  return out;
}
