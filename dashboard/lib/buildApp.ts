/**
 * BUILD'S CODE VIEW (v1.409.0): the REAL Claude Code or Codex, driven from a
 * GUI. Build's header carries one switch, **Code | Terminal** (the chat
 * page's Chat | Code switch, shared: components/ViewSwitch). Terminal is the
 * terminals, unchanged. Code is this: a list of sessions, grouped by folder,
 * and the open session's conversation, drawn from the events the daemon
 * hands over (docs/BUILD-APP-VIEW-PLAN.md "Normalized events" — THE
 * CONTRACT).
 *
 * Different from the chat page's Code view: there Iron Jarvis runs every
 * tool and the model only thinks; HERE the coding tool runs everything
 * itself (its own tools, settings, permission rules, its own saved session)
 * and Iron Jarvis only starts it, draws what it does, relays the user's
 * messages and answers its permission questions.
 *
 * This file is the PURE half: the address and the window's memory, the
 * decoders (every field whitelisted, every text cut), the folding of the
 * event list into a conversation, the open questions, the Enter rule and the
 * words. It lives OUTSIDE lib/api.ts (dozens of test files mock that module
 * wholesale) and imports nothing that talks to the daemon.
 *
 * The wire keeps its own names (`/build/app/…`, `build_app`, `harness`);
 * what a user reads never says "app view" or "harness" (VOCABULARY.md).
 */

// ── the view and the address ────────────────────────────────────────────────

export type BuildView = "code" | "terminal";

/** localStorage key: the view Build last showed. */
export const BUILD_VIEW_KEY = "ij.build.view";

/** `?view=code` is the Code view; `&code=<id>` opens one of its sessions.
 *  (`?session=` is taken: it is the OLD link to a chat Code session, which
 *  Build sends on to the chat page — lib/buildLinks.) */
export const BUILD_CODE_VIEW_PARAM = "code";
export const BUILD_CODE_SESSION_PARAM = "code";

/** The Build page's own path. */
export const BUILD_PATH = "/terminals";

/** The switch's words, in its order: Code first. */
export const BUILD_CODE_VIEW_NAME = "Code";
export const BUILD_TERMINAL_VIEW_NAME = "Terminal";

/** A terminal whose Claude Code / Codex has finished offers this. */
export const OPEN_IN_CODE_VIEW = "Open in Code view";

/** A session's ⋯ offers this (the same conversation, in a terminal). */
export const OPEN_IN_TERMINAL = "Open in terminal";

/** A link to a Code view session pressed while Build is open: announced
 *  (Next keeps the page's state on a query-only navigation). */
export const BUILD_CODE_EVENT = "ij:open-build-code";

/** The view an address asks for, or null (it says nothing). `view=code` or a
 *  `code=<id>` is Code; `view=terminal` or a `focus=<id>` (a terminal link)
 *  is Terminal. */
export function buildViewFromSearch(search: string | null | undefined): BuildView | null {
  let q: URLSearchParams;
  try {
    q = new URLSearchParams(search ?? "");
  } catch {
    return null;
  }
  const v = q.get("view");
  if (v === BUILD_CODE_VIEW_PARAM) return "code";
  if (v === "terminal") return "terminal";
  if ((q.get("focus") ?? "").trim()) return "terminal";
  if ((q.get(BUILD_CODE_SESSION_PARAM) ?? "").trim()) return "code";
  return null;
}

/** The Code view session an address names, else null. */
export function codeSessionFromSearch(search: string | null | undefined): string | null {
  try {
    const id = new URLSearchParams(search ?? "").get(BUILD_CODE_SESSION_PARAM);
    return id && id.trim() ? id.trim() : null;
  } catch {
    return null;
  }
}

/** Where a Code view session opens (the bell's row, a link). */
export function buildCodeHref(id?: string | null): string {
  const base = `${BUILD_PATH}?view=${BUILD_CODE_VIEW_PARAM}`;
  return id ? `${base}&${BUILD_CODE_SESSION_PARAM}=${encodeURIComponent(id)}` : base;
}

/** The session a Build Code link names (`/terminals?view=code&code=<id>`). */
export function codeSessionOfHref(href: string | null | undefined): string | null {
  const h = typeof href === "string" ? href.trim() : "";
  const q = h.indexOf("?");
  if (q < 0 || h.slice(0, q).replace(/\/+$/, "") !== BUILD_PATH) return null;
  return codeSessionFromSearch(h.slice(q + 1));
}

/** A Code view link pressed while Build is open: announce it. Answers
 *  whether it was announced (only on Build; elsewhere the navigation opens
 *  it). */
export function announceBuildCodeLink(href: string | null | undefined): boolean {
  if (typeof window === "undefined") return false;
  const id = codeSessionOfHref(href);
  if (!id) return false;
  if (window.location.pathname.replace(/\/+$/, "") !== BUILD_PATH) return false;
  window.dispatchEvent(new CustomEvent(BUILD_CODE_EVENT, { detail: { id } }));
  return true;
}

let windowView: BuildView | null = null;

/** The module half of the memory (null on a fresh load, like the server):
 *  what the first frame may read without a hydration mismatch. */
export function cachedBuildView(): BuildView | null {
  return windowView;
}

/** The view Build last showed: this window first, else the saved choice. */
export function rememberedBuildView(): BuildView | null {
  if (windowView) return windowView;
  try {
    const saved = window.localStorage.getItem(BUILD_VIEW_KEY);
    if (saved === "code" || saved === "terminal") return saved;
  } catch {
    /* storage blocked */
  }
  return null;
}

/** Remember `view` (this window, and the saved choice). */
export function keepBuildView(view: BuildView): void {
  windowView = view;
  try {
    window.localStorage.setItem(BUILD_VIEW_KEY, view);
  } catch {
    /* the module copy still holds it */
  }
}

/** Forget the window's choice (tests). */
export function resetBuildViewMemory(): void {
  windowView = null;
}

/** Put the view (and the open session) in the address without a navigation.
 *  Terminal clears the Code view's parameters and leaves everything else
 *  (`focus=`) as it was. */
export function writeBuildViewToAddress(view: BuildView, sessionId?: string | null): void {
  try {
    const url = new URL(window.location.href);
    if (view === "code") {
      url.searchParams.set("view", BUILD_CODE_VIEW_PARAM);
      if (sessionId) url.searchParams.set(BUILD_CODE_SESSION_PARAM, sessionId);
      else if (sessionId === null) url.searchParams.delete(BUILD_CODE_SESSION_PARAM);
    } else {
      url.searchParams.delete("view");
      url.searchParams.delete(BUILD_CODE_SESSION_PARAM);
    }
    window.history.replaceState(window.history.state, "", url.toString());
  } catch {
    /* no history API: the memory still holds it */
  }
}

// ── small readers ───────────────────────────────────────────────────────────

function str(v: unknown, max = 2_000): string {
  return typeof v === "string" ? (v.length > max ? v.slice(0, max) : v) : "";
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function obj(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function listOf(raw: unknown, key: string): unknown[] {
  if (Array.isArray(raw)) return raw;
  const o = obj(raw);
  return o && Array.isArray(o[key]) ? (o[key] as unknown[]) : [];
}

// ── the coding tools (GET /build/app/harnesses) ─────────────────────────────

export interface ToolMode {
  id: string;
  label: string;
  /** Full access: two presses in the menu, and `confirm: true` on the wire. */
  dangerous: boolean;
}

/** A mode the tool does NOT offer here, and why (Codex on Windows: no
 *  Auto edits, no Plan). Listed in the mode menu, never chosen. */
export interface HiddenMode {
  id: string;
  label: string;
  reason: string;
}

export interface ToolAccount {
  id: string;
  name: string;
  status: string;
}

export interface ToolCommand {
  name: string;
  description: string;
}

export interface CodingTool {
  id: string;
  label: string;
  installed: boolean;
  /** null = not known (the daemon never starts a check to find out). */
  signedIn: boolean | null;
  version: string;
  modes: ToolMode[];
  hiddenModes: HiddenMode[];
  models: { id: string; label: string }[];
  efforts: string[];
  accounts: ToolAccount[];
  /** The tool's own `/` commands, when the daemon lists them. */
  commands: ToolCommand[];
}

/** The plain name of a coding tool id (the daemon's label wins). */
export function toolLabel(id: string, tools?: readonly CodingTool[] | null): string {
  const t = tools?.find((x) => x.id === id);
  if (t?.label) return t.label;
  if (id === "claude-code") return "Claude Code";
  if (id === "codex") return "Codex";
  return id || "Coding tool";
}

export function decodeCodingTools(raw: unknown): CodingTool[] {
  const out: CodingTool[] = [];
  for (const item of listOf(raw, "harnesses")) {
    const r = obj(item);
    if (!r) continue;
    const id = str(r.id, 64).trim();
    if (!id) continue;
    const modes: ToolMode[] = [];
    for (const m of Array.isArray(r.modes) ? r.modes : []) {
      const mo = obj(m);
      const mid = mo ? str(mo.id, 64).trim() : "";
      if (!mo || !mid) continue;
      modes.push({ id: mid, label: str(mo.label, 80) || mid, dangerous: mo.dangerous === true });
    }
    const hiddenModes: HiddenMode[] = [];
    for (const m of Array.isArray(r.hidden_modes) ? r.hidden_modes : []) {
      const mo = obj(m);
      const mid = mo ? str(mo.id, 64).trim() : "";
      if (!mo || !mid || modes.some((x) => x.id === mid)) continue;
      hiddenModes.push({ id: mid, label: str(mo.label, 80) || mid, reason: str(mo.reason, 400) });
    }
    const models: { id: string; label: string }[] = [];
    for (const m of Array.isArray(r.models) ? r.models : []) {
      const mo = obj(m);
      const mid = mo ? str(mo.id, 200).trim() : typeof m === "string" ? m.trim() : "";
      if (!mid) continue;
      models.push({ id: mid, label: (mo && str(mo.label, 120)) || mid });
    }
    const efforts: string[] = [];
    for (const e of Array.isArray(r.efforts) ? r.efforts : []) {
      const eo = obj(e);
      const eid = typeof e === "string" ? e.trim() : eo ? str(eo.id, 40).trim() : "";
      if (eid && !efforts.includes(eid)) efforts.push(eid);
    }
    const accounts: ToolAccount[] = [];
    for (const a of Array.isArray(r.accounts) ? r.accounts : []) {
      const ao = obj(a);
      const aid = ao ? str(ao.id, 200).trim() : "";
      if (!ao || !aid) continue;
      accounts.push({ id: aid, name: str(ao.name, 120) || aid, status: str(ao.status, 40) });
    }
    const commands: ToolCommand[] = [];
    for (const c of Array.isArray(r.commands) ? r.commands : []) {
      const co = obj(c);
      const name = co ? str(co.name, 80).trim().replace(/^\//, "") : "";
      if (!co || !name) continue;
      commands.push({ name, description: str(co.description, 300) });
    }
    out.push({
      id,
      label: str(r.label, 80) || toolLabel(id),
      installed: r.installed === true,
      signedIn: r.signed_in === true ? true : r.signed_in === false ? false : null,
      version: str(r.version, 60),
      modes,
      hiddenModes,
      models,
      efforts,
      accounts,
      commands,
    });
  }
  return out;
}

/** Why a coding tool cannot start a session, in words; null when it can.
 *  Signed-in "not known" is never a reason (the tool says so itself). */
export function toolUnavailable(t: CodingTool): string | null {
  if (!t.installed) return `${t.label} is not installed on this PC.`;
  if (t.signedIn === false)
    return `${t.label} is signed out. Open a terminal, run it, and sign in there first.`;
  return null;
}

// ── sessions (GET /build/app/sessions) ──────────────────────────────────────

export interface CodeSession {
  id: string;
  tool: string;
  folder: string;
  title: string;
  /** The Iron-Proxy account id, or "" = this PC's own sign-in. */
  account: string;
  accountName: string;
  model: string;
  effort: string;
  mode: string;
  toolSessionId: string;
  createdAt: string;
  updatedAt: string;
  archivedAt: string;
  lastError: string;
  /** "disk": found in the tool's own saved sessions on this PC (not started
   *  here). "" otherwise. */
  source: "disk" | "";
  running: boolean;
  /** An approval or question is waiting on the user right now. */
  waiting: boolean;
}

/** Archived: the daemon's flag, or a stamp. */
export function isArchived(s: Pick<CodeSession, "archivedAt"> & { archived?: boolean }): boolean {
  return !!s.archivedAt || s.archived === true;
}

export function decodeCodeSession(raw: unknown): CodeSession | null {
  let r = obj(raw);
  if (r && obj(r.session) && !str(r.id)) r = obj(r.session);
  if (!r) return null;
  const id = str(r.id, 200).trim();
  if (!id) return null;
  return {
    id,
    tool: str(r.harness, 64) || str(r.tool, 64),
    folder: str(r.folder, 1_000),
    title: str(r.title, 300),
    account: str(r.account, 200),
    accountName: str(r.account_name, 120),
    model: str(r.model, 200),
    effort: str(r.effort, 40),
    mode: str(r.mode, 64),
    toolSessionId: str(r.harness_session_id, 200),
    createdAt: str(r.created_at, 64),
    updatedAt: str(r.updated_at, 64),
    archivedAt: str(r.archived_at, 64) || (r.archived === true ? "archived" : ""),
    lastError: str(r.last_error, 1_000),
    source: r.source === "disk" ? "disk" : "",
    running: r.running === true || (typeof r.running === "string" && r.running.trim() !== ""),
    waiting: r.waiting === true,
  };
}

export function decodeCodeSessions(raw: unknown): CodeSession[] {
  const out: CodeSession[] = [];
  const seen = new Set<string>();
  for (const item of listOf(raw, "sessions")) {
    const s = decodeCodeSession(item);
    if (!s || seen.has(s.id)) continue;
    seen.add(s.id);
    out.push(s);
  }
  return out;
}

/** A folder's last part, for its heading ("C:\\work\\shop" → "shop"). */
export function folderName(path: string): string {
  const parts = path.replace(/[\\/]+$/, "").split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] || path || "No folder";
}

export interface FolderGroup {
  folder: string;
  name: string;
  sessions: CodeSession[];
}

function newestStamp(s: CodeSession): string {
  return s.updatedAt || s.createdAt;
}

/** One folder's key: case- and slash-blind. */
export function folderKey(p: string): string {
  return p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

/** Sessions grouped by folder (case- and slash-blind), the group with the
 *  newest session first, newest first inside a group. Archived ones are
 *  left out, unless the list IS the archived list (`archived`). */
export function groupByFolder(sessions: readonly CodeSession[], opts: { archived?: boolean } = {}): FolderGroup[] {
  const key = folderKey;
  const groups = new Map<string, FolderGroup>();
  const live = opts.archived ? [...sessions] : sessions.filter((s) => !s.archivedAt);
  const sorted = [...live].sort((a, b) => (newestStamp(b) > newestStamp(a) ? 1 : newestStamp(b) < newestStamp(a) ? -1 : 0));
  for (const s of sorted) {
    const k = key(s.folder);
    let g = groups.get(k);
    if (!g) {
      g = { folder: s.folder, name: folderName(s.folder), sessions: [] };
      groups.set(k, g);
    }
    g.sessions.push(s);
  }
  return [...groups.values()];
}

/** The account a session runs on, in words. */
export function accountWords(s: Pick<CodeSession, "account" | "accountName" | "tool">, tools?: readonly CodingTool[] | null): string {
  if (!s.account) return "this PC's sign-in";
  if (s.accountName) return s.accountName;
  const t = tools?.find((x) => x.id === s.tool);
  return t?.accounts.find((a) => a.id === s.account)?.name || s.account;
}

/** A session row's second line: which tool, on which account. */
export function sessionRowLine(s: CodeSession, tools?: readonly CodingTool[] | null): string {
  return `${toolLabel(s.tool, tools)} · ${accountWords(s, tools)}`;
}

/** A session's title, never empty. */
export function sessionTitle(s: Pick<CodeSession, "title" | "tool">, tools?: readonly CodingTool[] | null): string {
  return s.title.trim() || `New ${toolLabel(s.tool, tools)} session`;
}

const MODE_WORDS: Record<string, string> = {
  ask: "Ask first",
  edits: "Auto edits",
  plan: "Plan",
  full: "Full access",
};

/** A mode's words: the tool's own list, else the daemon's four names —
 *  never the raw id a user cannot read. */
export function modeLabel(modeId: string, tool?: CodingTool | null): string {
  const own = tool?.modes.find((m) => m.id === modeId)?.label || tool?.hiddenModes.find((m) => m.id === modeId)?.label;
  if (own) return own;
  return MODE_WORDS[modeId] ?? (modeId ? "Another mode" : "Ask first");
}

/** Is this mode Full access (the daemon's id, whatever the tool list says). */
export function isFullAccess(modeId: string, tool?: CodingTool | null): boolean {
  return modeId === "full" || modeIsDangerous(modeId, tool);
}

/** An account's state in words, and whether a new session can start on it
 *  (the cached Iron-Proxy statuses: ready | usage | limit | needs_sign_in |
 *  off). */
export function accountStatusWords(status: string): { words: string; usable: boolean } {
  switch (status) {
    case "needs_sign_in":
      return { words: "needs to sign in again", usable: false };
    case "limit":
      return { words: "at its limit", usable: false };
    case "off":
      return { words: "turned off", usable: false };
    case "usage":
      return { words: "in use", usable: true };
    default:
      return { words: "", usable: true };
  }
}

export function modeIsDangerous(modeId: string, tool?: CodingTool | null): boolean {
  return tool?.modes.find((m) => m.id === modeId)?.dangerous === true;
}

// ── the events (THE CONTRACT) ───────────────────────────────────────────────

export type ToolKind = "read" | "edit" | "write" | "command" | "search" | "web" | "mcp" | "task" | "todo" | "other";
const TOOL_KINDS: readonly ToolKind[] = ["read", "edit", "write", "command", "search", "web", "mcp", "task", "todo", "other"];

export type AskKind = "command" | "edit" | "tool" | "network" | "other";
const ASK_KINDS: readonly AskKind[] = ["command", "edit", "tool", "network", "other"];

export type PlanStatus = "pending" | "in_progress" | "completed";

export interface QuestionItem {
  question: string;
  header: string;
  options: { label: string; description: string }[];
  multi: boolean;
}

/** Every event may name the turn it belongs to (the daemon's `turn`): a
 *  rejoin cuts the saved list by it. */
export type CodeEvent = CodeEventBody & { turn?: string };

type CodeEventBody =
  | { type: "session"; at: string; harness: string; harnessSessionId: string; model: string; cwd: string; mode: string }
  | { type: "user"; at: string; text: string; files: string[] }
  | { type: "text"; at: string; item: string; delta: string }
  | { type: "text_done"; at: string; item: string; text: string }
  | { type: "thinking"; at: string; item: string; delta: string }
  | {
      type: "tool";
      at: string;
      item: string;
      kind: ToolKind;
      name: string;
      title: string;
      input: string;
      status: "running" | "done" | "failed";
      output: string;
      path: string;
      line: number | null;
      diff: string;
    }
  | { type: "plan"; at: string; steps: { text: string; status: PlanStatus }[] }
  | {
      type: "approval";
      at: string;
      requestId: string;
      kind: AskKind;
      title: string;
      detail: string;
      command: string;
      path: string;
      diff: string;
      canSession: boolean;
      /** An approval the tool asks ONCE, every time (no session-wide
       *  allow): Enter never allows it. */
      onceOnly: boolean;
      /** The tool's own name for what it wants to use ("PowerShell"). */
      tool: string;
    }
  | { type: "question"; at: string; requestId: string; questions: QuestionItem[] }
  | { type: "resolved"; at: string; requestId: string; decision: string }
  | { type: "usage"; at: string; input: number; output: number; cacheRead: number; costUsd: number | null; listPrice: boolean }
  | { type: "notice"; at: string; text: string }
  | { type: "error"; at: string; title: string; fix: string; technical: string; fixKind: string; account: string }
  | { type: "done"; at: string; stop: "completed" | "interrupted" | "failed" | "max_turns"; result: string };

export type CodeEventType = CodeEvent["type"];

/** One event by whitelist; null for an unknown type or a missing key. The
 *  type is the event's own `type`, else the SSE frame's name (`hint`). */
export function decodeCodeEvent(raw: unknown, hint = ""): CodeEvent | null {
  const ev = decodeEventBody(raw, hint);
  if (!ev) return null;
  const turn = str(obj(raw)?.turn, 200).trim();
  return turn ? { ...ev, turn } : ev;
}

function decodeEventBody(raw: unknown, hint: string): CodeEventBody | null {
  const r = obj(raw);
  if (!r) return null;
  const type = str(r.type, 40) || hint;
  const at = str(r.at, 64);
  switch (type) {
    case "session":
      return {
        type,
        at,
        harness: str(r.harness, 64),
        harnessSessionId: str(r.harness_session_id, 200),
        model: str(r.model, 200),
        cwd: str(r.cwd, 1_000),
        mode: str(r.mode, 64),
      };
    case "user":
      return {
        type,
        at,
        text: str(r.text, 100_000),
        files: Array.isArray(r.files) ? r.files.filter((f): f is string => typeof f === "string").slice(0, 20) : [],
      };
    case "text":
    case "thinking": {
      const item = str(r.item, 200);
      if (!item) return null;
      return { type, at, item, delta: str(r.delta, 100_000) };
    }
    case "text_done": {
      const item = str(r.item, 200);
      if (!item) return null;
      return { type, at, item, text: str(r.text, 200_000) };
    }
    case "tool": {
      const item = str(r.item, 200);
      if (!item) return null;
      const kind = TOOL_KINDS.includes(r.kind as ToolKind) ? (r.kind as ToolKind) : "other";
      const status = r.status === "done" || r.status === "failed" ? r.status : "running";
      const line = num(r.line);
      return {
        type,
        at,
        item,
        kind,
        name: str(r.name, 200),
        title: str(r.title, 300),
        input: str(r.input, 300),
        status,
        output: str(r.output, 1_500),
        path: str(r.path, 1_000),
        line: line !== null && line > 0 ? Math.floor(line) : null,
        diff: str(r.diff, 20_000),
      };
    }
    case "plan": {
      const steps: { text: string; status: PlanStatus }[] = [];
      for (const s of Array.isArray(r.steps) ? r.steps : []) {
        const so = obj(s);
        const text = so ? str(so.text, 300).trim() : "";
        if (!so || !text) continue;
        const st = so.status === "in_progress" || so.status === "completed" ? so.status : "pending";
        steps.push({ text, status: st });
        if (steps.length >= 50) break;
      }
      return { type, at, steps };
    }
    case "approval": {
      const requestId = str(r.request_id, 200);
      if (!requestId) return null;
      return {
        type,
        at,
        requestId,
        kind: ASK_KINDS.includes(r.kind as AskKind) ? (r.kind as AskKind) : "other",
        title: str(r.title, 300),
        detail: str(r.detail, 2_000),
        command: str(r.command, 2_000),
        path: str(r.path, 1_000),
        diff: str(r.diff, 20_000),
        canSession: r.can_session === true,
        onceOnly: r.once_only === true,
        tool: str(r.tool, 120),
      };
    }
    case "question": {
      const requestId = str(r.request_id, 200);
      if (!requestId) return null;
      const questions: QuestionItem[] = [];
      for (const q of Array.isArray(r.questions) ? r.questions : []) {
        const qo = obj(q);
        const question = qo ? str(qo.question, 1_000).trim() : "";
        if (!qo || !question) continue;
        const options: { label: string; description: string }[] = [];
        for (const o of Array.isArray(qo.options) ? qo.options : []) {
          const oo = obj(o);
          const label = oo ? str(oo.label, 200).trim() : "";
          if (!oo || !label) continue;
          options.push({ label, description: str(oo.description, 500) });
        }
        questions.push({ question, header: str(qo.header, 80), options, multi: qo.multi === true });
      }
      if (!questions.length) return null;
      return { type, at, requestId, questions };
    }
    case "resolved": {
      const requestId = str(r.request_id, 200);
      if (!requestId) return null;
      return { type, at, requestId, decision: str(r.decision, 40) };
    }
    case "usage":
      return {
        type,
        at,
        input: num(r.input) ?? 0,
        output: num(r.output) ?? 0,
        cacheRead: num(r.cache_read) ?? 0,
        costUsd: num(r.cost_usd),
        listPrice: r.list_price === true,
      };
    case "notice": {
      const text = str(r.text, 2_000).trim();
      return text ? { type, at, text } : null;
    }
    case "error":
      return {
        type,
        at,
        title: str(r.title, 300) || str(r.detail, 300) || "Something went wrong.",
        fix: str(r.fix, 600),
        technical: str(r.technical, 2_000),
        fixKind: str(r.fix_kind, 40),
        account: str(r.account, 200),
      };
    case "done": {
      const stop =
        r.stop === "interrupted" || r.stop === "failed" || r.stop === "max_turns" ? r.stop : "completed";
      return { type, at, stop, result: str(r.result, 20_000) };
    }
    default:
      return null;
  }
}

export function decodeCodeEvents(raw: unknown): CodeEvent[] {
  const out: CodeEvent[] = [];
  for (const e of listOf(raw, "events")) {
    const ev = decodeCodeEvent(e);
    if (ev) out.push(ev);
  }
  return out;
}

// ── one session (GET /build/app/sessions/{id}) ──────────────────────────────

export interface CodeSessionView {
  session: CodeSession;
  events: CodeEvent[];
  /** Is a turn running now. */
  running: boolean;
  /** The running turn's id, when the daemon names it (a rejoin follows it). */
  runningTurn: string | null;
  /** Open approvals and questions (in case their events were trimmed). */
  pending: CodeEvent[];
  /** How many of the oldest events were left out of this read. */
  dropped: number;
  /** The tool's own `/` commands (what its process said). */
  commands: ToolCommand[] | null;
}

/** The `/` commands an answer lists (null when the answer has no list). */
export function decodeCommands(raw: unknown): ToolCommand[] | null {
  const list = Array.isArray(raw) ? raw : obj(raw) && Array.isArray(obj(raw)!.commands) ? (obj(raw)!.commands as unknown[]) : null;
  if (!list) return null;
  const out: ToolCommand[] = [];
  for (const c of list) {
    const co = obj(c);
    const name = co ? str(co.name, 80).trim().replace(/^\//, "") : "";
    if (!co || !name || out.some((x) => x.name === name)) continue;
    out.push({ name, description: str(co.description, 300) });
  }
  return out;
}

export function decodeCodeSessionView(raw: unknown): CodeSessionView | null {
  const r = obj(raw);
  if (!r) return null;
  const session = decodeCodeSession(r.session ?? r);
  if (!session) return null;
  let runningTurn: string | null = null;
  if (typeof r.running === "string" && r.running.trim()) runningTurn = r.running.trim();
  const ro = obj(r.running);
  if (ro && typeof ro.turn_id === "string" && ro.turn_id.trim()) runningTurn = ro.turn_id.trim();
  if (!runningTurn && typeof r.turn_id === "string" && r.turn_id.trim() && r.running) runningTurn = r.turn_id.trim();
  const running = !!runningTurn || r.running === true || !!ro;
  const pending: CodeEvent[] = [];
  for (const p of Array.isArray(r.pending) ? r.pending : []) {
    const po = obj(p);
    if (!po) continue;
    const hint = po.questions ? "question" : "approval";
    const ev = decodeCodeEvent(p, hint);
    if (ev && (ev.type === "approval" || ev.type === "question")) pending.push(ev);
  }
  const dropped = num(r.dropped);
  return {
    session,
    events: decodeCodeEvents(r.events),
    running,
    runningTurn,
    pending,
    dropped: dropped !== null && dropped > 0 ? Math.floor(dropped) : 0,
    commands: decodeCommands(r.commands),
  };
}

// ── the conversation ────────────────────────────────────────────────────────

export type ToolEvent = Extract<CodeEvent, { type: "tool" }>;
export type ApprovalEvent = Extract<CodeEvent, { type: "approval" }>;
export type QuestionEvent = Extract<CodeEvent, { type: "question" }>;
export type OpenAsk = ApprovalEvent | QuestionEvent;

export type ConvItem =
  | { kind: "user"; key: string; text: string; files: string[] }
  | { kind: "text"; key: string; text: string; done: boolean }
  | { kind: "thinking"; key: string; text: string }
  | { kind: "tool"; key: string; tool: ToolEvent }
  | { kind: "plan"; key: string; steps: { text: string; status: PlanStatus }[] }
  | { kind: "resolved"; key: string; requestId: string; decision: string; about: string; question: boolean }
  | { kind: "notice"; key: string; text: string }
  | { kind: "error"; key: string; title: string; fix: string; technical: string; fixKind: string; account: string }
  | { kind: "usage"; key: string; input: number; output: number; costUsd: number | null; listPrice: boolean }
  | { kind: "done"; key: string; stop: Extract<CodeEvent, { type: "done" }>["stop"]; result: string };

export interface Conversation {
  items: ConvItem[];
  /** Approvals and questions still waiting on the user, in asking order. */
  asks: OpenAsk[];
  /** The tool's own session id, when a `session` event named it. */
  toolSessionId: string;
  /** The last turn ended (a `done` after the last `user`), or none ran. */
  settled: boolean;
}

/** Fold the event list into what the screen draws. Text and thinking are
 *  joined per `item`; a tool's later events update its one row; one plan per
 *  turn, at the place it first appeared, showing its newest steps. An ask
 *  is open until its `resolved` (or the turn's `done`) arrives. `extra`:
 *  the session read's open asks, listed when their events were trimmed. */
export function foldConversation(events: readonly CodeEvent[], extra: readonly CodeEvent[] = []): Conversation {
  const items: ConvItem[] = [];
  const textAt = new Map<string, number>();
  const thinkAt = new Map<string, number>();
  const toolAt = new Map<string, number>();
  let planAt: number | null = null;
  const asks = new Map<string, OpenAsk>();
  const askTitle = new Map<string, string>();
  const askWasQuestion = new Set<string>();
  const resolved = new Set<string>();
  let toolSessionId = "";
  let settled = true;
  let n = 0;
  const key = (p: string) => `${p}-${n++}`;
  for (const ev of events) {
    switch (ev.type) {
      case "session":
        if (ev.harnessSessionId) toolSessionId = ev.harnessSessionId;
        break;
      case "user":
        items.push({ kind: "user", key: key("u"), text: ev.text, files: ev.files });
        planAt = null;
        settled = false;
        break;
      case "text": {
        const i = textAt.get(ev.item);
        if (i === undefined) {
          textAt.set(ev.item, items.length);
          items.push({ kind: "text", key: key("t"), text: ev.delta, done: false });
        } else {
          const it = items[i] as Extract<ConvItem, { kind: "text" }>;
          if (!it.done) items[i] = { ...it, text: it.text + ev.delta };
        }
        break;
      }
      case "text_done": {
        const i = textAt.get(ev.item);
        if (i === undefined) {
          textAt.set(ev.item, items.length);
          items.push({ kind: "text", key: key("t"), text: ev.text, done: true });
        } else {
          const it = items[i] as Extract<ConvItem, { kind: "text" }>;
          items[i] = { ...it, text: ev.text || it.text, done: true };
        }
        break;
      }
      case "thinking": {
        const i = thinkAt.get(ev.item);
        if (i === undefined) {
          thinkAt.set(ev.item, items.length);
          items.push({ kind: "thinking", key: key("k"), text: ev.delta });
        } else {
          const it = items[i] as Extract<ConvItem, { kind: "thinking" }>;
          items[i] = { ...it, text: it.text + ev.delta };
        }
        break;
      }
      case "tool": {
        const i = toolAt.get(ev.item);
        if (i === undefined) {
          toolAt.set(ev.item, items.length);
          items.push({ kind: "tool", key: key("x"), tool: ev });
        } else {
          const prev = (items[i] as Extract<ConvItem, { kind: "tool" }>).tool;
          items[i] = {
            kind: "tool",
            key: items[i].key,
            tool: {
              ...ev,
              name: ev.name || prev.name,
              title: ev.title || prev.title,
              input: ev.input || prev.input,
              output: ev.output || prev.output,
              path: ev.path || prev.path,
              line: ev.line ?? prev.line,
              diff: ev.diff || prev.diff,
            },
          };
        }
        break;
      }
      case "plan":
        if (planAt === null) {
          planAt = items.length;
          items.push({ kind: "plan", key: key("p"), steps: ev.steps });
        } else {
          items[planAt] = { kind: "plan", key: items[planAt].key, steps: ev.steps };
        }
        break;
      case "approval":
      case "question":
        if (!resolved.has(ev.requestId)) asks.set(ev.requestId, ev);
        askTitle.set(ev.requestId, ev.type === "approval" ? ev.title : ev.questions[0]?.question ?? "");
        if (ev.type === "question") askWasQuestion.add(ev.requestId);
        break;
      case "resolved":
        resolved.add(ev.requestId);
        asks.delete(ev.requestId);
        items.push({
          kind: "resolved",
          key: key("r"),
          requestId: ev.requestId,
          decision: ev.decision,
          about: askTitle.get(ev.requestId) ?? "",
          question: askWasQuestion.has(ev.requestId),
        });
        break;
      case "notice":
        items.push({ kind: "notice", key: key("n"), text: ev.text });
        break;
      case "error":
        items.push({
          kind: "error",
          key: key("e"),
          title: ev.title,
          fix: ev.fix,
          technical: ev.technical,
          fixKind: ev.fixKind,
          account: ev.account,
        });
        break;
      case "usage":
        items.push({ kind: "usage", key: key("g"), input: ev.input, output: ev.output, costUsd: ev.costUsd, listPrice: ev.listPrice });
        break;
      case "done":
        items.push({ kind: "done", key: key("d"), stop: ev.stop, result: ev.result });
        // A turn that ended asks nothing any more.
        asks.clear();
        settled = true;
        break;
    }
  }
  if (!settled) {
    for (const p of extra) {
      if (p.type !== "approval" && p.type !== "question") continue;
      if (resolved.has(p.requestId) || asks.has(p.requestId)) continue;
      asks.set(p.requestId, p);
    }
  }
  return { items, asks: [...asks.values()], toolSessionId, settled };
}

/** The steps of a plan, as the chat's checklist draws them. */
export function planChecklistSteps(steps: readonly { text: string; status: PlanStatus }[]) {
  return steps.map((s, index) => ({
    index,
    goal: s.text,
    state: (s.status === "completed" ? "done" : s.status === "in_progress" ? "running" : "waiting") as
      | "done"
      | "running"
      | "waiting",
  }));
}

// ── rejoin ──────────────────────────────────────────────────────────────────

/**
 * Rejoining a running turn: the turn's stream REPLAYS the turn from its
 * start, while the session read already holds what was saved of it. So the
 * saved list loses every event of THAT turn (`turn === runningTurn`), and the
 * replay draws the turn again; its question is kept aside (`carry`) and put
 * back first when the replay does not begin with it. Only when no saved
 * event names a turn (an older record) is the cut made at the last `user`.
 */
export function cutForRejoin(
  events: readonly CodeEvent[],
  runningTurn: string | null = null,
): { base: CodeEvent[]; carry: CodeEvent | null } {
  if (runningTurn && events.some((e) => e.turn)) {
    const mine = events.filter((e) => e.turn === runningTurn);
    return {
      base: events.filter((e) => e.turn !== runningTurn),
      carry: mine.find((e) => e.type === "user") ?? null,
    };
  }
  for (let i = events.length - 1; i >= 0; i--) {
    if (events[i].type === "user") return { base: events.slice(0, i), carry: events[i] };
  }
  return { base: [...events], carry: null };
}

// ── the questions' keys and words ───────────────────────────────────────────

/** Whether Enter may press an ask's main answer. NEVER for an approval that
 *  asks once, every time, and never while the session runs in a dangerous
 *  mode (Full access: a card that still comes up there is the one the tool
 *  itself would not run alone). Esc still declines; Allow is a click. A
 *  question (the tool asking the user to choose) keeps Enter = Send. */
export function enterAllowsCodeAsk(ask: OpenAsk | null | undefined, dangerousMode: boolean): boolean {
  if (!ask || ask.type !== "approval") return true;
  return !(ask.onceOnly || dangerousMode);
}

export const CODE_CLICK_TO_ALLOW_HINT = "Click Allow once to allow it · Esc to deny";

export function codeAskKeyHint(ask: OpenAsk | null | undefined, dangerousMode: boolean): string {
  if (!ask) return "";
  if (ask.type === "question") return "Enter to send · Esc to skip";
  return enterAllowsCodeAsk(ask, dangerousMode) ? "Enter to allow once · Esc to deny" : CODE_CLICK_TO_ALLOW_HINT;
}

/** What an approval is about, in a title line. */
export function approvalHeading(a: ApprovalEvent, tool: string): string {
  if (a.title) return `${tool} wants to: ${a.title}`;
  switch (a.kind) {
    case "command":
      return `${tool} wants to run a command`;
    case "edit":
      return `${tool} wants to change a file`;
    case "network":
      return `${tool} wants to reach the internet`;
    default:
      return `${tool} wants your OK`;
  }
}

/** The answers of a question card, keyed by each question's own words (the
 *  shape Claude Code's question tool reads; several choices joined). */
export function questionAnswers(
  q: QuestionEvent,
  picked: Readonly<Record<number, readonly string[]>>,
  other: Readonly<Record<number, string>>,
): Record<string, string> {
  const out: Record<string, string> = {};
  q.questions.forEach((item, i) => {
    const typed = (other[i] ?? "").trim();
    const labels = [...(picked[i] ?? [])];
    if (typed) labels.push(typed);
    if (labels.length) out[item.question] = labels.join(", ");
  });
  return out;
}

/** "Allowed" / "Denied" for a settled ask's quiet line ("Answered" /
 *  "Skipped" for a question). "expired": the tool stopped asking. */
export function resolvedWords(decision: string, question = false): string {
  if (decision === "cancelled" || decision === "expired") return "No longer asked";
  if (question) return decision === "deny" || decision === "denied" ? "Skipped" : "Answered";
  if (decision === "deny" || decision === "denied") return "Denied";
  if (decision === "allow_session") return "Allowed for this session";
  if (decision === "answered") return "Answered";
  return "Allowed";
}

/** How a turn ended, said under it (nothing for an ordinary finish). */
export function doneWords(stop: Extract<CodeEvent, { type: "done" }>["stop"]): string {
  if (stop === "interrupted") return "Stopped.";
  if (stop === "failed") return "This reply did not finish.";
  if (stop === "max_turns") return "Stopped at its turn limit.";
  return "";
}

/** The usage line ("1,240 in · 310 out · about $0.02 at list price"). */
export function usageWords(u: { input: number; output: number; costUsd: number | null; listPrice: boolean }): string {
  const parts = [`${u.input.toLocaleString("en-US")} in`, `${u.output.toLocaleString("en-US")} out`];
  if (u.costUsd !== null && u.costUsd > 0) {
    const dollars = u.costUsd < 0.01 ? "under $0.01" : `$${u.costUsd.toFixed(2)}`;
    parts.push(u.listPrice ? `about ${dollars} at list price` : dollars);
  }
  return parts.join(" · ");
}

// ── the folder's saved sessions (GET /build/app/sessions?folder=&disk=1) ─────

/** A conversation the coding tool saved on this PC by itself, for a folder
 *  (Claude Code's / Codex's own session files; read-only, names only). */
export interface DiskSession {
  tool: string;
  toolSessionId: string;
  title: string;
  folder: string;
  started: string;
  ended: string;
}

export function decodeDiskSessions(raw: unknown): DiskSession[] {
  const o = obj(raw);
  const list = o && Array.isArray(o.disk) ? o.disk : Array.isArray(raw) ? raw : [];
  const out: DiskSession[] = [];
  const seen = new Set<string>();
  for (const d of list) {
    const r = obj(d);
    if (!r) continue;
    const id = str(r.harness_session_id, 200).trim();
    const tool = str(r.harness, 64).trim();
    if (!id || !tool || seen.has(`${tool}:${id}`)) continue;
    seen.add(`${tool}:${id}`);
    out.push({
      tool,
      toolSessionId: id,
      title: str(r.title, 300),
      folder: str(r.folder, 1_000),
      started: str(r.started, 64),
      ended: str(r.ended, 64),
    });
  }
  return out;
}

/** A saved conversation's row words ("saved by Claude Code"). */
export function diskRowLine(d: DiskSession, tools?: readonly CodingTool[] | null): string {
  return `saved by ${toolLabel(d.tool, tools)}`;
}

/** The address that lists a folder's saved conversations. */
export function diskListPath(folder: string): string {
  return `/build/app/sessions?folder=${encodeURIComponent(folder)}&disk=1`;
}

// ── a change that waits for the next message (PATCH … `applied`) ────────────

const FIELD_WORDS: Record<string, string> = { model: "The model", effort: "The effort", mode: "The mode" };

/** "The effort applies from your next message." for every field the daemon
 *  applied `next` (a running tool reads it when it starts again); "" when
 *  everything applied now. */
export function appliedNextWords(raw: unknown): string {
  const o = obj(raw);
  const applied = o && obj(o.applied) ? obj(o.applied)! : null;
  if (!applied) return "";
  const later = Object.keys(applied).filter((k) => applied[k] === "next" && FIELD_WORDS[k]);
  if (!later.length) return "";
  const names = later.map((k, i) => (i === 0 ? FIELD_WORDS[k] : FIELD_WORDS[k].toLowerCase()));
  const subject = names.length === 1 ? names[0] : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
  return `${subject} ${names.length === 1 ? "applies" : "apply"} from your next message.`;
}

/** The quiet line when older events were left out of a session read. */
export function droppedWords(n: number): string {
  return n === 1
    ? "1 older step was left out here to keep this quick."
    : `${n.toLocaleString("en-US")} older steps were left out here to keep this quick.`;
}

/** The /health provider id of a coding tool (the Sign in press reads it). */
export function signInProvider(tool: string): string {
  if (tool === "claude-code") return "claude-cli";
  if (tool === "codex") return "codex-cli";
  return "";
}

/** "Continue on another account": the words of the press. */
export const CONTINUE_ON_ANOTHER = "Continue on another account";

// ── drawing only what changed (the v1.250.0 rule) ───────────────────────────

function sameValue(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((x, i) => sameValue(x, b[i]));
  if (a && b && typeof a === "object" && typeof b === "object") {
    const ka = Object.keys(a as object);
    const kb = Object.keys(b as object);
    return (
      ka.length === kb.length &&
      ka.every((k) => sameValue((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]))
    );
  }
  return false;
}

/** The new fold's items, with every item whose content did not change
 *  replaced by the PREVIOUS object (same key, same content), so a memoised
 *  row draws again only when its own item changed. */
export function reuseItems(prev: readonly ConvItem[] | null, next: ConvItem[]): ConvItem[] {
  if (!prev || !prev.length) return next;
  const byKey = new Map(prev.map((it) => [it.key, it]));
  return next.map((it) => {
    const old = byKey.get(it.key);
    return old && sameValue(old, it) ? old : it;
  });
}

/** How long a title from the first message may be (the daemon's
 *  hosted/service.TITLE_CHARS). */
export const TITLE_CHARS = 60;

/** A long run of key-like characters (a pasted key or token). */
const KEYISH = /[A-Za-z0-9_\-]{24,}/;

/** The list's echo of the title the daemon gives an untitled session from
 *  its first message (hosted/service.first_message_title): the first line
 *  with words, spaces collapsed, cut to TITLE_CHARS with an ellipsis. The
 *  daemon also masks secrets; this file stays import-free (the bell reads
 *  it), so a line that looks like it holds a key is never echoed ("") — the
 *  list's next read brings the daemon's masked title. */
export function firstMessageTitle(text: string): string {
  for (const line of String(text ?? "").split("\n")) {
    const words = line.split(/\s+/).filter(Boolean).join(" ");
    if (!words) continue;
    if (KEYISH.test(words)) return "";
    return words.length <= TITLE_CHARS ? words : `${words.slice(0, TITLE_CHARS - 1)}…`;
  }
  return "";
}
