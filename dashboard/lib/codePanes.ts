/**
 * BUILD › CODE'S RIGHT PANES (v1.414.0): the decoders and the words for the
 * Changes, Usage and Session tabs of a Code-view session — the same column
 * Chat › Code has (the user: "the icons for changes, remaining usage, files
 * and sessions to the right just as in the native harness").
 *
 * The daemon's three reads (`GET /build/app/sessions/{id}/changes | usage |
 * overview`) are read-only and never start the coding tool. Every number here
 * is one the daemon or the tool said; a number nobody said is "not known
 * yet", never 0%.
 */

import type { FileChange } from "@/components/chat/ChangedFiles";
import type { CodeEvent } from "@/lib/buildApp";

function obj(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}
function str(v: unknown, max = 2_000): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}
function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

// ── changes ─────────────────────────────────────────────────────────────────

export type ChangesSource = "git" | "tool" | "none";

export interface CodeChange extends FileChange {
  /** The tool's own file tools reported changing this file. */
  byTool: boolean;
}

export interface CodeChanges {
  source: ChangesSource;
  /** What the list covers and its limits, in the daemon's words. */
  words: string;
  changes: CodeChange[];
  files: number;
  more: boolean;
  byTool: number;
  /** Why there is no Undo here. */
  noUndo: string;
}

export function decodeCodeChanges(raw: unknown): CodeChanges | null {
  const r = obj(raw);
  if (!r) return null;
  const source = r.source === "git" || r.source === "tool" ? r.source : "none";
  const changes: CodeChange[] = [];
  for (const item of Array.isArray(r.changes) ? r.changes : []) {
    const c = obj(item);
    if (!c) continue;
    const path = str(c.path, 1_000);
    if (!path) continue;
    const status = c.status === "created" || c.status === "deleted" ? c.status : "modified";
    changes.push({
      path,
      rel: str(c.rel, 1_000) || undefined,
      name: str(c.name, 300) || undefined,
      status,
      added: num(c.added),
      removed: num(c.removed),
      diff: str(c.diff, 400_000),
      truncated: c.truncated === true,
      binary: c.binary === true,
      size: num(c.size),
      note: str(c.note, 300) || undefined,
      byTool: c.by_tool === true,
    });
  }
  return {
    source,
    words: str(r.words, 600),
    changes,
    files: num(r.files) ?? changes.length,
    more: r.truncated_files === true,
    byTool: num(r.by_tool) ?? changes.filter((c) => c.byTool).length,
    noUndo: str(r.no_undo, 300),
  };
}

/** The Session tab's one line for the changes ("3 files changed in this
 *  folder · 2 by Claude Code"). */
export function changesCountWords(c: CodeChanges | null, tool: string): string {
  if (!c) return "Reading…";
  if (c.source === "none") return c.words || "Nothing to compare yet.";
  if (c.files === 0) return "No files changed yet.";
  const n = `${c.files}${c.more ? "+" : ""}`;
  const files = `${n} file${c.files === 1 && !c.more ? "" : "s"}`;
  if (c.source === "tool") return `${files} ${tool} edited`;
  const by = c.byTool ? ` · ${c.byTool} by ${tool}` : "";
  return `${files} changed in this folder${by}`;
}

// ── usage ───────────────────────────────────────────────────────────────────

export interface UsageWindow {
  label: string;
  percent: number;
  resetsAt: string | null;
}

export interface CodeUsage {
  account: {
    id: string;
    name: string;
    planLabel: string;
    status: string;
    windows: UsageWindow[];
    /** When Iron-Proxy saw its OLDEST number shown ("" = not said). */
    at: string;
  } | null;
  seen: { windows: UsageWindow[]; at: string; plan: string } | null;
  totals: {
    replies: number;
    input: number;
    output: number;
    costUsd: number | null;
    listPrice: boolean;
    costReportedBy: string;
  };
  context: { used: number; window: number; percent: number; at: string } | null;
}

function windowsOf(raw: unknown): UsageWindow[] {
  const out: UsageWindow[] = [];
  for (const item of Array.isArray(raw) ? raw : []) {
    const w = obj(item);
    const p = num(w?.percent);
    if (!w || p === null) continue;
    out.push({
      label: str(w.label, 60) || "Usage window",
      percent: Math.max(0, Math.min(100, Math.round(p))),
      resetsAt: str(w.resets_at, 40) || null,
    });
  }
  return out;
}

export function decodeContext(raw: unknown): CodeUsage["context"] {
  const c = obj(raw);
  const used = num(c?.used);
  const window = num(c?.window);
  if (!c || used === null || window === null || window <= 0) return null;
  const percent = num(c.percent) ?? Math.round((100 * used) / window);
  return { used, window, percent: Math.max(0, Math.min(100, percent)), at: str(c.at, 40) };
}

export function decodeCodeUsage(raw: unknown): CodeUsage | null {
  const r = obj(raw);
  if (!r) return null;
  const a = obj(r.account);
  const s = obj(r.seen);
  const t = obj(r.totals) ?? {};
  const seenWindows = windowsOf(s?.windows);
  return {
    account: a
      ? {
          id: str(a.id, 200),
          name: str(a.name, 120) || str(a.id, 200),
          planLabel: str(a.plan_label, 60),
          status: str(a.status, 40),
          windows: windowsOf(a.windows),
          at: str(a.at, 40),
        }
      : null,
    seen: s && seenWindows.length ? { windows: seenWindows, at: str(s.at, 40), plan: str(s.plan, 40) } : null,
    totals: {
      replies: num(t.replies) ?? 0,
      input: num(t.input) ?? 0,
      output: num(t.output) ?? 0,
      costUsd: num(t.cost_usd),
      listPrice: t.list_price === true,
      costReportedBy: str(t.cost_reported_by, 40),
    },
    context: decodeContext(r.context),
  };
}

/** "Resets 3:40 PM" / "Resets Fri 9:00 AM" ("" when not said). */
export function resetWords(iso: string | null, now: number = Date.now()): string {
  if (!iso) return "";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "";
  const d = new Date(t);
  const time = d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
  if (t - now < 20 * 3600_000 && d.getDate() === new Date(now).getDate()) return `resets ${time}`;
  return `resets ${d.toLocaleDateString("en-US", { weekday: "short" })} ${time}`;
}

/** "63% used · 37% left · resets 3:40 PM" — `percent` is what the vendor
 *  said; "left" is only ever 100 minus it. */
export function windowLine(w: UsageWindow, now: number = Date.now()): string {
  const reset = resetWords(w.resetsAt, now);
  return [`${w.percent}% used`, `${100 - w.percent}% left`, reset].filter(Boolean).join(" · ");
}

/** "just now" / "4 min ago" / "2 h ago" / "3 days ago". */
export function agoWords(iso: string, now: number = Date.now()): string {
  const t = new Date(iso).getTime();
  if (!iso || Number.isNaN(t)) return "";
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86_400) return `${Math.round(s / 3600)} h ago`;
  const d = Math.round(s / 86_400);
  return `${d} day${d === 1 ? "" : "s"} ago`;
}

/** Tools that never report their account's windows over their machine
 *  protocol (Pi's RPC, OpenCode's ACP). */
export const NO_WINDOWS_TOOLS: readonly string[] = ["pi", "opencode"];

/** Why there are no window numbers (never "0%"). */
export function noWindowsWords(tool: string, toolName: string): string {
  if (NO_WINDOWS_TOOLS.includes(tool)) return `${toolName} does not report its account's limits here, so they are not known.`;
  return `Not known yet: ${toolName} reports its limits while it works — they show after its next reply.`;
}

// ── session ─────────────────────────────────────────────────────────────────

export interface CodeOverview {
  folder: string;
  folderExists: boolean;
  onShare: boolean;
  git: { repo: boolean; branch: string; detached: boolean; noCommits: boolean };
  rules: string[];
  context: CodeUsage["context"];
  toolRunning: boolean;
  programs: { pid: number; name: string; command: string }[];
  /** v1.414.0 review: false = the listing could not be read (never "nothing
   *  is running"); an older daemon that does not say reads as known. */
  programsKnown: boolean;
  /** The listing was cut at its bound: more run than are listed. */
  programsMore: boolean;
}

export function decodeCodeOverview(raw: unknown): CodeOverview | null {
  const r = obj(raw);
  if (!r) return null;
  const g = obj(r.git) ?? {};
  const programs: CodeOverview["programs"] = [];
  for (const item of Array.isArray(r.programs) ? r.programs : []) {
    const p = obj(item);
    const pid = num(p?.pid);
    if (!p || pid === null) continue;
    programs.push({ pid, name: str(p.name, 80), command: str(p.command, 200) });
  }
  return {
    folder: str(r.folder, 1_000),
    folderExists: r.folder_exists === true,
    onShare: r.on_share === true,
    git: { repo: g.repo === true, branch: str(g.branch, 200), detached: g.detached === true, noCommits: g.no_commits === true },
    rules: (Array.isArray(r.rules) ? r.rules : []).filter((x): x is string => typeof x === "string").slice(0, 10),
    context: decodeContext(r.context),
    toolRunning: r.tool_running === true,
    programs,
    programsKnown: r.programs_known !== false,
    programsMore: r.programs_more === true,
  };
}

export function branchLine(o: CodeOverview | null): string {
  if (!o) return "";
  if (!o.git.repo) return "Not in a git repository.";
  if (o.git.noCommits) return "A git repository with no commits yet.";
  if (o.git.detached) return "Git: not on a branch (a commit is checked out).";
  return `On branch ${o.git.branch}`;
}

/** The newest message or note that carried files (the Session tab's Last
 *  attachment), with its words. */
export function lastCodeAttachment(
  events: readonly CodeEvent[],
): { files: string[]; at: string; words: string } | null {
  for (let i = events.length - 1; i >= 0; i -= 1) {
    const e = events[i];
    if ((e.type === "user" || (e.type === "steer" && e.state === "sent")) && e.files.length) {
      const words = e.text.trim().replace(/\s+/g, " ");
      return { files: [...e.files], at: e.at, words: words.length > 80 ? `${words.slice(0, 79)}…` : words };
    }
  }
  return null;
}

/** How full, as the tool said ("" before it said). */
export function contextNotKnownWords(tool: string, toolName: string): string {
  if (tool === "pi") return `${toolName} does not report how full its conversation is here.`;
  return "Measured after the next reply.";
}
