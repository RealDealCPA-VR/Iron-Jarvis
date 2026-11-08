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
  /** v1.420.0 (C4.12): the file's bytes as the daemon read them (git's blob
   *  id; "" for a deleted file) — Revert refuses when the file changed since. */
  blob: string;
  /** A renamed file's old name (Revert puts it back too). */
  oldRel: string;
}

/** v1.420.0 (C4.12): which changes the list covers. */
export type ChangesScope = "session" | "turn";

export interface CodeChanges {
  source: ChangesSource;
  /** What the list covers and its limits, in the daemon's words. */
  words: string;
  changes: CodeChange[];
  files: number;
  more: boolean;
  byTool: number;
  /** Why there is no Undo here (and, for git, what Revert does). */
  noUndo: string;
  /** The scope the daemon REALLY listed (a turn it could not tell apart is
   *  listed as the whole session, and its words say why). */
  scope: ChangesScope;
  /** The message the turn scope covers ("" otherwise). */
  turn: string;
  /** A file can be put back (git only). */
  canRevert: boolean;
}

/** The two scopes' names on the toggle. */
export const SCOPE_WORDS: Record<ChangesScope, string> = {
  turn: "This message",
  session: "Whole session",
};

/** The Revert press's words. */
export const REVERT_LABEL = "Revert this file";
export function revertConfirmWords(name: string): string {
  return `Put ${name} back?`;
}
/** Said instead of the Revert presses while a reply runs. */
export const REVERT_WAITS = "Revert is offered once the reply has finished.";

/** The body `POST /build/app/sessions/{id}/changes/revert` takes. */
export function revertBody(c: CodeChange, data: CodeChanges): Record<string, string> {
  return {
    path: c.rel || c.path,
    blob: c.blob,
    old_rel: c.oldRel,
    scope: data.scope,
    turn: data.turn,
  };
}

/** The read's address for one scope. */
export function changesQuery(scope: ChangesScope): string {
  return scope === "turn" ? "?scope=turn" : "";
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
      blob: str(c.blob, 80),
      oldRel: str(c.old_rel, 1_000),
    });
  }
  return {
    source,
    words: str(r.words, 600),
    changes,
    files: num(r.files) ?? changes.length,
    more: r.truncated_files === true,
    byTool: num(r.by_tool) ?? changes.filter((c) => c.byTool).length,
    noUndo: str(r.no_undo, 400),
    scope: r.scope === "turn" ? "turn" : "session",
    turn: str(r.turn, 120),
    // An older daemon sends no flag: no Revert press (it has no route).
    canRevert: r.can_revert === true && source === "git",
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
    /** v1.439.0: its usage can be read now ("Check usage now"). */
    canRefresh: boolean;
    /** v1.439.0: when it was last read now (ISO), "" when never. */
    usageReadAt: string;
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
          canRefresh: a.can_refresh === true,
          usageReadAt: str(a.usage_read_at, 40),
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
 *  protocol (Pi's RPC, OpenCode's, Grok's and Antigravity's ACP). */
export const NO_WINDOWS_TOOLS: readonly string[] = ["pi", "opencode", "grok", "antigravity"];

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
  /** v1.426.0 (E4): the tool's OWN MCP servers as it reported them; null =
   *  an older Iron Jarvis that does not say. */
  mcp: CodeMcp | null;
  /** v1.441.0 (g1): the tool's own background work it can stop (Claude Code's
   *  open tasks, Codex's background terminals); null = the tool offers no list
   *  (or an older Iron Jarvis): the tab shows what it showed before. */
  backgroundWork: BackgroundWork[] | null;
  /** v1.448.0 (Wave A, A3): where the tool's model answers — `false` =
   *  someone else's service ("cloud"), `true` = a model server on this
   *  network; null = not known (an older Iron Jarvis, a Pi/OpenCode provider
   *  the daemon cannot place), and the tab draws no row. `words` are the
   *  daemon's own ("cloud" / "on this network"). */
  where: { onThisNetwork: boolean | null; words: string };
}

/** The Session tab's "Runs · cloud" / "Runs · on this network" line, or ""
 *  when where it runs is not known (the row is then not drawn). */
export function whereLine(o: Pick<CodeOverview, "where"> | null): string {
  if (!o || o.where.onThisNetwork === null || !o.where.words) return "";
  return `Runs · ${o.where.words}`;
}

function decodeWhere(raw: unknown): CodeOverview["where"] {
  const w = obj(raw);
  if (!w || typeof w.on_this_network !== "boolean") return { onThisNetwork: null, words: "" };
  return { onThisNetwork: w.on_this_network, words: str(w.words, 40) };
}

/** v1.441.0 (g1): one piece of background work the tool runs. */
export interface BackgroundWork {
  id: string;
  title: string;
  /** helper | command | task */
  kind: string;
  canStop: boolean;
}

export const BACKGROUND_KINDS: readonly string[] = ["helper", "command", "task"];
export const BACKGROUND_KIND_WORDS: Record<string, string> = {
  helper: "Helper",
  command: "Command",
  task: "Task",
};

/** v1.441.0 (g1): the MCP actions a row may offer, and their words. */
export const MCP_ACTIONS: readonly string[] = ["reconnect", "off", "on", "sign_in"];
export const MCP_ACTION_WORDS: Record<string, string> = {
  reconnect: "Reconnect",
  off: "Turn off",
  on: "Turn on",
  sign_in: "Sign in…",
};
/** What an action does, beyond its name (the menu item's title), per tool. */
export function mcpActionTitle(action: string, tool: string, toolName: string): string {
  if (action === "off" && tool === "claude-code") return "Claude Code remembers this for this folder, as /mcp does.";
  if (action === "sign_in") return `Opens ${toolName}'s sign-in page for it in your browser; Iron Jarvis never signs in for you.`;
  if (action === "reconnect") return `Asks ${toolName} to start it again.`;
  return "";
}

function mcpActionsOf(raw: unknown): { actions?: string[] } {
  const acts = (Array.isArray(raw) ? raw : []).filter(
    (a, i, all): a is string => typeof a === "string" && MCP_ACTIONS.includes(a) && all.indexOf(a) === i,
  );
  return acts.length ? { actions: acts } : {};
}

export function decodeBackgroundWork(raw: unknown): BackgroundWork[] | null {
  if (!Array.isArray(raw)) return null;
  const out: BackgroundWork[] = [];
  for (const item of raw) {
    const w = obj(item);
    const id = w ? str(w.id, 80).trim() : "";
    if (!w || !id) continue;
    const kind = str(w.kind, 20);
    out.push({
      id,
      title: str(w.title, 300).trim() || "A task",
      kind: BACKGROUND_KINDS.includes(kind) ? kind : "task",
      canStop: w.can_stop === true,
    });
    if (out.length >= 50) break;
  }
  return out;
}

/** v1.426.0 (E4): one of the tool's own MCP servers, in the daemon's words
 *  (`hosted/tool_setup.view`). */
export interface CodeMcpServer {
  name: string;
  /** connected | starting | failed | needs_sign_in | off | on (v1.447.0:
   *  turned on in the tool's own settings, connection not reported —
   *  Antigravity's `agy mcp list`) */
  state: string;
  words: string;
  error: string;
  /** The tool's OWN way to sign in ("" = none needed). */
  signIn: string;
  /** v1.441.0 (g1): what the running tool can do to it from here (only the
   *  actions that fit its state); absent = none (the row shows no menu). */
  actions?: string[];
}

export interface CodeMcp {
  /** false = the tool never said (a note says why). */
  known: boolean;
  servers: CodeMcpServer[];
  at: string;
  note: string;
  /** v1.447.0: the running tool can read its MCP settings again (Codex's
   *  `config/mcpServer/reload`); absent = no such press. */
  reload?: boolean;
}

/** v1.447.0: the whole list's own reload (Codex). */
// Wave D item 33: "MCP" is the wire's name; v1.455.0 (Wave E): a person reads
// "extensions" (VOCABULARY.md row 22), never "tool servers".
export const MCP_RELOAD_WORDS = "Reload its extensions";
export function mcpReloadTitle(toolName: string): string {
  return `${toolName} reads its extension settings again: extensions you added or changed start, ones you took out go. Nothing else restarts.`;
}

export const MCP_STATES: readonly string[] = ["connected", "starting", "failed", "needs_sign_in", "off", "on"];

export function decodeCodeMcp(raw: unknown): CodeMcp | null {
  const r = obj(raw);
  if (!r) return null;
  const servers: CodeMcpServer[] = [];
  for (const item of Array.isArray(r.servers) ? r.servers : []) {
    const s = obj(item);
    const name = s ? str(s.name, 120).trim() : "";
    if (!s || !name) continue;
    const state = str(s.state, 40);
    servers.push({
      name,
      state: MCP_STATES.includes(state) ? state : "failed",
      words: str(s.words, 80) || "Did not start",
      error: str(s.error, 300),
      signIn: str(s.sign_in, 400),
      ...mcpActionsOf(s.actions),
    });
    if (servers.length >= 50) break;
  }
  return {
    known: r.known === true,
    servers,
    at: str(r.at, 64),
    note: str(r.note, 300),
    ...(r.reload === true ? { reload: true } : {}),
  };
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
    mcp: decodeCodeMcp(r.mcp),
    backgroundWork: decodeBackgroundWork(r.background_work),
    where: decodeWhere(r.where),
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
