// v1.306.0 — share my profile with Build: the pure half (decoder + words).
//
// `GET /profile/share` answers, per vendor CLI found on this PC, whether the
// user's profile is shared with it, which files hold the generated block, and
// whether the user edited (or removed) that block by hand. This module turns
// that answer into typed shapes and plain sentences; the row component owns
// its own calls. Deliberately OUTSIDE lib/api.ts: test files mock that module
// wholesale, and a decoder living there would vanish under every mock.
// Idea from agent-personalizer (MIT) — no code taken.

/** One file the shared block lives in (or would). */
export interface ShareFile {
  path: string;
  /** The Iron-Proxy account's name, or null for this PC's own login. */
  account: string | null;
  exists: boolean;
  lastWritten: string | null;
  /** null | "edited" | "removed" | "broken" */
  drift: string | null;
  /** The user chose "Keep yours": Jarvis no longer updates this file. */
  held: boolean;
  error: string | null;
}

export interface ShareCli {
  cli: string;
  label: string;
  vendor: string;
  available: boolean;
  on: boolean;
  fileName: string;
  files: ShareFile[];
  /** The files a switch-on writes (named before the press). */
  targets: { path: string; account: string | null }[];
  lastWritten: string | null;
  omitted: number;
}

const str = (v: unknown): string => (typeof v === "string" ? v : "");
const strOrNull = (v: unknown): string | null => (typeof v === "string" && v ? v : null);

function decodeFile(raw: unknown): ShareFile | null {
  if (!raw || typeof raw !== "object") return null;
  const o = raw as Record<string, unknown>;
  const path = str(o.path);
  if (!path) return null;
  return {
    path,
    account: strOrNull(o.account),
    exists: o.exists === true,
    lastWritten: strOrNull(o.last_written),
    drift: strOrNull(o.drift),
    held: o.held === true,
    error: strOrNull(o.error),
  };
}

/** The whole answer, or null when it is not one (an older daemon's 404 never
 *  reaches here — the caller hides the row on any failure). */
export function decodeShareView(raw: unknown): ShareCli[] | null {
  if (!raw || typeof raw !== "object") return null;
  const list = (raw as Record<string, unknown>).clis;
  if (!Array.isArray(list)) return null;
  const out: ShareCli[] = [];
  for (const item of list) {
    if (!item || typeof item !== "object") continue;
    const o = item as Record<string, unknown>;
    const cli = str(o.cli);
    if (!cli) continue;
    const files = Array.isArray(o.files) ? o.files.map(decodeFile).filter((f): f is ShareFile => f !== null) : [];
    const targets = Array.isArray(o.targets)
      ? o.targets
          .map((t) => decodeFile({ ...(t as object), exists: false }))
          .filter((f): f is ShareFile => f !== null)
          .map((f) => ({ path: f.path, account: f.account }))
      : [];
    out.push({
      cli,
      label: str(o.label) || cli,
      vendor: str(o.vendor),
      available: o.available === true,
      on: o.on === true,
      fileName: str(o.file_name) || "",
      files,
      targets,
      lastWritten: strOrNull(o.last_written),
      omitted: typeof o.omitted === "number" ? o.omitted : 0,
    });
  }
  return out;
}

/** "the file" / "these files" — the files the switch writes, by path. */
export function filesWords(c: ShareCli): string {
  const list = c.on && c.files.length > 0 ? c.files : c.targets;
  const own = list.find((f) => !f.account);
  const accounts = list.filter((f) => f.account).map((f) => f.account as string);
  const first = own ? own.path : list[0]?.path || c.fileName || "its instructions file";
  if (accounts.length === 0) return first;
  const n = accounts.length;
  return `${first} and the same file in ${n} Iron-Proxy account folder${n === 1 ? "" : "s"} (${accounts.join(", ")})`;
}

/** v1.316.0: what EVERY switch writes — said once above the switches. */
export const SHARE_EXPLAIN =
  "Writes your profile and the preferences you said or kept — nothing else Jarvis remembers — " +
  "into a marked block in that tool's own instructions file.";

/** One switch's own line: its file(s), and the company that sees them. The
 *  vendor is named on EVERY switch — that is the privacy disclosure. */
export function shareWhere(c: ShareCli): string {
  const vendor = c.vendor || "its maker";
  return `${filesWords(c)} — ${c.label} reads it in every session, so ${vendor} sees it when ${c.label} runs.`;
}

/** The whole sentence for one switch: what is written, where, and who sees
 *  it (SHARE_EXPLAIN + shareWhere in one sentence, for a single-switch use). */
export function shareSentence(c: ShareCli): string {
  const vendor = c.vendor || "its maker";
  return (
    `Writes your profile and the preferences you said or kept — nothing else Jarvis remembers — ` +
    `into a marked block in ${filesWords(c)}. ${c.label} reads it in every session, so ${vendor} ` +
    `sees it when ${c.label} runs.`
  );
}

/** The line for a file that needs the user, or null when it does not. */
export function fileLine(f: ShareFile): string | null {
  if (f.error) return `Could not update ${f.path}: ${f.error}.`;
  if (f.held) return `You kept your own version in ${f.path}; Jarvis no longer updates it.`;
  switch (f.drift) {
    case "edited":
      return `You edited the shared block in ${f.path}.`;
    case "removed":
      return `You removed the shared block from ${f.path}.`;
    case "broken":
      return `The shared block's markers in ${f.path} are damaged — fix or delete them by hand.`;
    default:
      return null;
  }
}
