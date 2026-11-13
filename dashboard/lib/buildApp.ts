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

import { onceReasonOf } from "./onceReason";
import { type CarryMark, decodeCarryMark } from "./buildCodeCarry";
import { SUBSCRIPTION_CLI_ROWS } from "./subscriptionClis";
import { secondsText } from "./durationWords";

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
  /** v1.412.0: what the mode does, in the daemon's plain words ("" = an
   *  older daemon said nothing). */
  about?: string;
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
  /** v1.426.0 (E4): how the tool takes it — "$" for a Codex skill (typed
   *  `$<name>` in the message, Codex's own way); absent = a `/` command. */
  prefix?: "$";
  /** v1.429.0 (Pi): what kind of command — one of Pi's own ("pi"), an
   *  extension's, a prompt template or a skill. Absent = not said. */
  source?: CommandSource;
  /** v1.429.0 (Pi): why this one is not done here (only in Pi's own
   *  terminal). The menu shows such a row only when its name is typed, and
   *  picking it shows this line instead of typing it. */
  note?: string;
}

/** v1.429.0: the kinds of command Pi lists (rpc `get_commands` "source"),
 *  plus Pi's own built-ins. */
export type CommandSource = "pi" | "extension" | "prompt" | "skill";
const COMMAND_SOURCES: readonly CommandSource[] = ["pi", "extension", "prompt", "skill"];

/** v1.429.0: the words the `/` menu shows for a command's kind. */
export const COMMAND_SOURCE_WORDS: Record<CommandSource, string> = {
  pi: "Pi",
  extension: "extension",
  prompt: "template",
  skill: "skill",
};

/** v1.426.0 (E4): a command row's prefix as the daemon said it ("$" only). */
export function commandPrefix(raw: unknown): { prefix?: "$" } {
  return raw === "$" ? { prefix: "$" } : {};
}

/** v1.429.0: a command row's kind and note, as the daemon said them. */
export function commandExtras(raw: Record<string, unknown>): { source?: CommandSource; note?: string } {
  const out: { source?: CommandSource; note?: string } = {};
  const source = raw.source;
  if (typeof source === "string" && (COMMAND_SOURCES as readonly string[]).includes(source)) out.source = source as CommandSource;
  const note = typeof raw.note === "string" ? raw.note.trim().slice(0, 300) : "";
  if (note) out.note = note;
  return out;
}

/** v1.429.0: the `/` menu's rows for what is typed after the slash. A row
 *  that is only a note (one of Pi's terminal-only commands) shows only once
 *  its name is being typed — never a silent gap, never a crowded menu. */
export function slashMenuRows(commands: readonly ToolCommand[], query: string, max: number): ToolCommand[] {
  const q = query.toLowerCase();
  return commands
    .filter((c) => (c.note ? q.length > 0 && c.name.toLowerCase().startsWith(q) : c.name.toLowerCase().includes(q)))
    .slice(0, max);
}

/** v1.429.0: a model as the menus show it — `group` is its provider (Pi
 *  lists every model under the provider that serves it). */
export interface ToolModel {
  id: string;
  label: string;
  efforts?: string[];
  group?: string;
}

/** v1.447.0: the chip's words for a Claude Code model picked with `[1m]`
 *  (typed `/model sonnet[1m]`): the listed model's own name + "(1M context)",
 *  "" when the base model is not listed (the id is shown as it is). */
export function oneMillionLabel(model: string, models: readonly ToolModel[]): string {
  if (!/\[1m\]$/i.test(model)) return "";
  const base = model.slice(0, -4).toLowerCase();
  const row = models.find((m) => m.id.toLowerCase() === base);
  return row ? `${row.label} (1M context)` : "";
}

/** v1.429.0: the models in their groups, in the daemon's order (Pi's: by
 *  provider). A list with no group at all is one group named "". */
export function modelGroups(models: readonly ToolModel[]): { group: string; models: ToolModel[] }[] {
  const byGroup = new Map<string, ToolModel[]>();
  for (const m of models) {
    const g = m.group ?? "";
    const list = byGroup.get(g);
    if (list) list.push(m);
    else byGroup.set(g, [m]);
  }
  return [...byGroup].map(([group, list]) => ({ group, models: list }));
}

export interface CodingTool {
  id: string;
  label: string;
  installed: boolean;
  /** v1.411.0: the tool signs in BY ITSELF (Pi: per model provider, its own
   *  `/login`) — no account to choose; the chip says "Pi's own sign-in". */
  ownSignIn: boolean;
  /** v1.413.0: how THIS tool signs in by itself, in the daemon's words (the
   *  chip's title) — "" when the daemon did not say. */
  signInHint?: string;
  /** null = not known (the daemon never starts a check to find out). */
  signedIn: boolean | null;
  version: string;
  modes: ToolMode[];
  hiddenModes: HiddenMode[];
  /** v1.412.0 review: `efforts` on a model = the levels THAT model takes when
   *  the daemon knows them (`[]` = none: the effort chip hides); absent = the
   *  tool's own `efforts` apply (lib/buildApp effortsFor). v1.429.0: `group`
   *  = the provider (Pi). */
  models: ToolModel[];
  efforts: string[];
  /** v1.412.0 review: the levels the tool's DEFAULT model takes, when known. */
  defaultEfforts?: string[];
  /** v1.429.0 (Pi): the name of the model the tool starts on by itself. */
  defaultName?: string;
  /** v1.429.0 (Pi): the tool's model list is being read right now (the page
   *  looks again shortly). */
  modelsReading?: boolean;
  /** v1.429.0 (Pi): why the tool lists no model (one line, the daemon's). */
  modelsNote?: string;
  /** v1.443.0 (Antigravity): did its own sign-in through `agy` finish (its
   *  onboarding file)? A HINT for Connections only — the Code view reads
   *  `signedIn`, which the daemon never sets to false from this. Absent =
   *  not said. */
  signinFinished?: boolean;
  accounts: ToolAccount[];
  /** The tool's own `/` commands, when the daemon lists them. */
  commands: ToolCommand[];
  /** v1.441.0 (g1): an effort change reaches the RUNNING tool at once (Claude
   *  Code's own apply_flag_settings) — the chip stays open while a reply
   *  runs. Absent (an older Iron Jarvis) = the old rule. */
  effortLive?: boolean;
}

/** The plain name of a coding tool id (the daemon's label wins). */
export function toolLabel(id: string, tools?: readonly CodingTool[] | null): string {
  const t = tools?.find((x) => x.id === id);
  if (t?.label) return t.label;
  if (id === "claude-code") return "Claude Code";
  if (id === "codex") return "Codex";
  if (id === "pi") return "Pi";
  if (id === "opencode") return "OpenCode";
  if (id === "grok") return "Grok";
  if (id === "antigravity") return "Antigravity";
  return id || "Coding tool";
}

/** The four tools the Code view always names (each listed, installed or not). */
const ALWAYS_NAMED: readonly [string, string][] = [
  ["claude-code", "Claude Code"],
  ["codex", "Codex"],
  ["pi", "Pi"],
  ["opencode", "OpenCode"],
];

/** v1.416.0: the tools a session can start, in words ("Claude Code, Codex,
 *  Pi, OpenCode or Grok"): the four always, then any other tool the daemon
 *  lists — Grok and Antigravity only when installed (decodeCodingTools). */
export function toolChoiceWords(tools?: readonly CodingTool[] | null): string {
  const known = new Set(ALWAYS_NAMED.map(([id]) => id));
  const names = [
    ...ALWAYS_NAMED.map(([, name]) => name),
    ...(tools ?? []).filter((t) => !known.has(t.id)).map((t) => t.label).filter(Boolean),
  ];
  return `${names.slice(0, -1).join(", ")} or ${names[names.length - 1]}`;
}

export function decodeCodingTools(raw: unknown): CodingTool[] {
  const out: CodingTool[] = [];
  for (const item of listOf(raw, "harnesses")) {
    const r = obj(item);
    if (!r) continue;
    const id = str(r.id, 64).trim();
    if (!id) continue;
    // v1.416.0: a tool the daemon lists only when it is installed (Grok) is
    // never offered, nor named, on a PC that does not have it.
    if (r.only_when_installed === true && r.installed !== true) continue;
    const modes: ToolMode[] = [];
    for (const m of Array.isArray(r.modes) ? r.modes : []) {
      const mo = obj(m);
      const mid = mo ? str(mo.id, 64).trim() : "";
      if (!mo || !mid) continue;
      const about = str(mo.about, 300).trim();
      modes.push({ id: mid, label: str(mo.label, 80) || mid, dangerous: mo.dangerous === true, ...(about ? { about } : {}) });
    }
    const hiddenModes: HiddenMode[] = [];
    for (const m of Array.isArray(r.hidden_modes) ? r.hidden_modes : []) {
      const mo = obj(m);
      const mid = mo ? str(mo.id, 64).trim() : "";
      if (!mo || !mid || modes.some((x) => x.id === mid)) continue;
      hiddenModes.push({ id: mid, label: str(mo.label, 80) || mid, reason: str(mo.reason, 400) });
    }
    const models: ToolModel[] = [];
    let defaultEfforts: string[] | undefined;
    let defaultName = "";
    for (const m of Array.isArray(r.models) ? r.models : []) {
      const mo = obj(m);
      const mid = mo ? str(mo.id, 200).trim() : typeof m === "string" ? m.trim() : "";
      const levels =
        mo && Array.isArray(mo.efforts)
          ? mo.efforts.map((e) => (typeof e === "string" ? e.trim() : "")).filter(Boolean)
          : undefined;
      if (!mid) {
        // The tool's default (id ""): never a row of its own (the menus add
        // it), but the levels it takes — and (Pi) its name — are kept.
        if (mo && levels) defaultEfforts = levels;
        if (mo) defaultName = str(mo.default_name, 120).trim();
        continue;
      }
      const group = mo ? str(mo.group, 80).trim() : "";
      models.push({
        id: mid,
        label: (mo && str(mo.label, 120)) || mid,
        ...(levels ? { efforts: levels } : {}),
        ...(group ? { group } : {}),
      });
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
      commands.push({ name, description: str(co.description, 300), ...commandPrefix(co.prefix), ...commandExtras(co) });
    }
    const modelsNote = str(r.models_note, 300).trim();
    out.push({
      id,
      label: str(r.label, 80) || toolLabel(id),
      installed: r.installed === true,
      ownSignIn: r.own_sign_in === true,
      ...(str(r.sign_in_hint, 400).trim() ? { signInHint: str(r.sign_in_hint, 400).trim() } : {}),
      signedIn: r.signed_in === true ? true : r.signed_in === false ? false : null,
      version: str(r.version, 60),
      modes,
      hiddenModes,
      models,
      efforts,
      ...(defaultEfforts ? { defaultEfforts } : {}),
      ...(defaultName ? { defaultName } : {}),
      ...(r.models_reading === true ? { modelsReading: true } : {}),
      ...(modelsNote ? { modelsNote } : {}),
      ...(r.signin_finished === true || r.signin_finished === false
        ? { signinFinished: r.signin_finished === true }
        : {}),
      accounts,
      commands,
      ...(r.effort_live === true ? { effortLive: true } : {}),
    });
  }
  return out;
}

/** v1.412.0 review: the effort levels a session on `model` can take — that
 *  model's own list when the daemon knows it (`[]`: none, the chip hides),
 *  else the tool's list. */
export function effortsFor(tool: CodingTool | null | undefined, model: string): string[] {
  if (!tool) return [];
  if (!model) return tool.defaultEfforts ?? tool.efforts;
  const row = tool.models.find((m) => m.id === model);
  return row && Array.isArray(row.efforts) ? row.efforts : tool.efforts;
}

/** Why a coding tool cannot start a session, in words; null when it can.
 *  Signed-in "not known" is never a reason (the tool says so itself).
 *  v1.414.2 (a USER BUG: "it states that I'm signed out of Claude, but I am
 *  signed in"): `signedIn` is THIS PC's own login only. An Iron-Proxy account
 *  that is ready runs the tool all the same (a session started with no pick
 *  goes to Iron-Proxy's first free account), so the tool stays available. */
export function toolUnavailable(t: CodingTool): string | null {
  if (!t.installed) return `${t.label} is not installed on this PC.`;
  if (toolSignedOut(t))
    // v1.443.0: a tool with the app's one-press Sign in says only that it is
    // signed out — the Sign in press sits right beside these words
    // (components/connections/CliSignInPress). A tool with no press keeps
    // the way to do it by hand.
    return toolSignInProvider(t.id)
      ? `${t.label} is signed out.`
      : `${t.label} is signed out. Open a terminal, run it, and sign in there first.`;
  return null;
}

/** v1.443.0: the tool is installed but cannot start because it is signed out
 *  on this PC and no Iron-Proxy account of it is usable (toolUnavailable's
 *  rule). */
export function toolSignedOut(t: CodingTool): boolean {
  return t.installed && t.signedIn === false && !t.accounts.some((a) => accountStatusWords(a.status).usable);
}

/** v1.443.0: the provider id whose one-press Sign in (lib/cliSignIn) starts
 *  this coding tool on this PC's own login — "claude-cli" for Claude Code,
 *  "codex-cli" for Codex, "grok-cli" for Grok — "" for a tool with no press
 *  (Pi, OpenCode and Antigravity sign in by themselves). Read off the one
 *  subscription-CLI table (lib/subscriptionClis, generated). */
export function toolSignInProvider(toolId: string): string {
  return SUBSCRIPTION_CLI_ROWS.find((c) => c.harness === toolId)?.provider ?? "";
}

/** v1.443.0: no coding tool in the list can start a session (none installed,
 *  or every installed one signed out). False while the list is still read. */
export function noToolCanStart(tools: readonly CodingTool[] | null | undefined): boolean {
  return !!tools && !tools.some((t) => !toolUnavailable(t));
}

/** v1.443.0: no coding tool in the list is installed on this PC at all. */
export function noToolInstalled(tools: readonly CodingTool[] | null | undefined): boolean {
  return !!tools && !tools.some((t) => t.installed);
}

/** v1.443.0: Build › Code with nothing installed says so, in one line. */
export const NO_TOOL_ON_PC = "None of these coding tools is on this PC yet.";

/** v1.443.0: the start box's way to get a tool when none is on this PC: the
 *  coding tool's id, the Launch catalog's id (`GET /terminals/ai-clis`, whose
 *  `url` is the daemon's install page) and the link's words. */
export const GET_TOOL_LINKS: readonly { harness: string; cli: string; label: string }[] = [
  { harness: "claude-code", cli: "claude", label: "Get Claude Code" },
  { harness: "codex", cli: "codex", label: "Get Codex" },
];

/** v1.443.0: the Get links with their pages. The daemon's page (the Launch
 *  catalog row's `url`) wins; `fallback` (the dashboard's own install page
 *  for that CLI, lib/onboarding SUBSCRIPTION_CLIS) is used while the catalog
 *  is not read; a link with no known page is left out. Only http(s). */
export function getToolLinks(
  catalog: readonly { id: string; url?: string }[] | null | undefined,
  fallback: (cli: string) => string | undefined = () => undefined,
): { harness: string; label: string; url: string }[] {
  const out: { harness: string; label: string; url: string }[] = [];
  for (const g of GET_TOOL_LINKS) {
    const url = (catalog?.find((c) => c.id === g.cli)?.url ?? "").trim() || (fallback(g.cli) ?? "").trim();
    if (/^https?:\/\//i.test(url)) out.push({ harness: g.harness, label: g.label, url });
  }
  return out;
}

/** v1.443.0: the two "Code" views point at each other, one quiet line each. */
export const TO_CHAT_CODE_LEAD = "Want Jarvis to write the code itself? Use";
export const TO_BUILD_CODE_LEAD = "Want your own Claude Code or Codex instead? Open";
export const BUILD_CODE_PLACE = "Build › Code";
export const LET_JARVIS_CODE_LEAD = "Or let Jarvis write the code in";

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
  /** v1.417.0: background tasks the tool says are still open (helper agents
   *  and background commands) while its process runs; 0 otherwise. */
  background: number;
  /** v1.417.0: of those, the helper AGENTS (a dev server is not a helper). */
  backgroundHelpers: number;
  /** v1.417.0: the tool is writing a reply no message of the user's owns
   *  (Claude Code's report once its helpers end). */
  selfReply: boolean;
  /** v1.422.0 (B4): the programs the coding tool started that still serve
   *  (a dev server listening on a port) and that keep the session open past
   *  its idle stop — masked command + ports, never a pid. [] otherwise. */
  programs: KeptProgram[];
  /** v1.422.0 (B4): the daemon's line while it keeps the session open for
   *  them, and how to stop it ("" when it keeps nothing). */
  keepNote: string;
  /** v1.422.0 (B4): what the last idle stop or the pool ENDED with the tool
   *  (named, until the next message starts it again; "" otherwise). */
  stoppedNote: string;
  /** v1.412.0: started with no folder chosen — its own folder is made with
   *  its first message (`folder` names it once made). */
  noFolder: boolean;
  /** v1.412.0: where such a session's own folder goes (the chat files
   *  folder); "" otherwise. */
  workRoot: string;
  /** v1.414.0: which of Iron Jarvis's own tools the coding tool may use in
   *  this session (all off until the user ticks one). Absent from an older
   *  Iron Jarvis and from a conversation found on disk. */
  jarvisTools?: JarvisTools;
  /** v1.441.0 (review A5): false when this session's tool said it takes no
   *  Iron Jarvis tools (Antigravity 1.5.2) — the chip is hidden. Absent =
   *  it takes them (or an older Iron Jarvis, which never says). */
  jarvisToolsTaken?: false;
  /** v1.474.0: this session is a helper another session's coding tool
   *  started (that session's id); "" for a session the user started. */
  handedFrom: string;
  /** v1.441.0: the row's "Branch" — the tool can branch a conversation and
   *  this session has one. Absent from an older Iron Jarvis (no Branch). */
  canBranch?: boolean;
}

/** v1.422.0 (B4): one program a Code-view tool started that serves. */
export interface KeptProgram {
  command: string;
  ports: number[];
}

/** v1.414.0: the two Capabilities Iron Jarvis serves to a coding tool (the
 *  same two a Build terminal's boxes give: mcpserver CAPABILITY_TOOLS). */
export interface JarvisTools {
  memory: boolean;
  browser: boolean;
  /** v1.474.0: Hand off — the coding tool may start another coding tool on a
   *  piece of its work (`hosted/handoff`). Never offered in a helper session. */
  handoff: boolean;
}

export const JARVIS_TOOL_KEYS: readonly (keyof JarvisTools)[] = ["memory", "browser", "handoff"];

/** Each box's name. */
export const JARVIS_TOOL_LABELS: Record<keyof JarvisTools, string> = {
  memory: "Memory",
  browser: "Browser",
  handoff: "Hand off",
};

/** v1.474.0: the boxes a session offers (a helper never hands off again). */
export function jarvisToolKeysFor(s: Pick<CodeSession, "handedFrom">): (keyof JarvisTools)[] {
  return JARVIS_TOOL_KEYS.filter((k) => k !== "handoff" || !s.handedFrom);
}

/** Who sees what the coding tool reads (the privacy line on the boxes). */
export function toolVendor(tool: string): string {
  if (tool === "claude-code") return "Anthropic";
  if (tool === "codex") return "OpenAI";
  if (tool === "pi") return "the model provider you chose in Pi";
  if (tool === "opencode") return "the model provider you chose in OpenCode";
  if (tool === "grok") return "xAI";
  if (tool === "antigravity") return "Google";
  return "the coding tool's model provider";
}

/** The chip's words: which of Iron Jarvis's tools are on. */
export function jarvisToolsWords(t: JarvisTools | null | undefined): string {
  const on = JARVIS_TOOL_KEYS.filter((k) => t?.[k]).map((k) => JARVIS_TOOL_LABELS[k]);
  return on.length ? `Iron Jarvis tools: ${on.join(", ")}` : "Iron Jarvis tools: off";
}

/** What each box gives, and what it costs (said before the press). */
export function jarvisToolHint(key: keyof JarvisTools, toolName: string, tool: string): string {
  if (key === "memory") {
    return `${toolName} may search and read your memory and your memory profile (read-only). What it reads goes to ${toolVendor(tool)} with the conversation.`;
  }
  if (key === "handoff") {
    return `${toolName} may start another coding tool you have here (Codex, Claude Code, Pi…) on part of the work, in this folder. The work it hands over goes to that tool's own service. Each helper is its own session you can open; it asks you before anything its own rules ask about, and never gets Full access.`;
  }
  return `${toolName} may use your browser through Iron Jarvis. Every click, typing or page change asks you first.`;
}

/** v1.441.0 review: a Pi session runs a message that starts with `!` as a
 *  command in Pi's own shell (`!!` keeps it out of the conversation), as Pi's
 *  terminal does — where the box changes colour. The Code view says it in one
 *  quiet line while the words would run, so "!important…" is never sent as a
 *  command unseen. Pi's own reading: the trimmed words start with `!` and a
 *  command follows. */
export const PI_SHELL_HINT = "Runs as a command in Pi's shell, not as a message to Pi.";
export const PI_SHELL_HINT_KEPT_OUT = "Runs as a command in Pi's shell; Pi's model won't see it or its output.";
export function piShellHint(tool: string, text: string): string {
  if (tool !== "pi") return "";
  const t = text.trim();
  if (!t.startsWith("!")) return "";
  const keptOut = t.startsWith("!!");
  if (!t.slice(keptOut ? 2 : 1).trim()) return "";
  return keptOut ? PI_SHELL_HINT_KEPT_OUT : PI_SHELL_HINT;
}

/** v1.414.0: the "Share my profile with" switch (profile/share.CLIS) of a
 *  coding tool. v1.447.0: Antigravity too. */
export const SHARE_CLIS: readonly string[] = ["claude-code", "codex", "pi", "opencode", "grok", "antigravity"];
export function shareCliOf(tool: string): string {
  return SHARE_CLIS.includes(tool) ? tool : "";
}

/** localStorage: the share offer was dismissed for good, per tool. */
export function shareOfferDismissKey(cli: string): string {
  return `ij.code.shareOffer.dismissed.${cli}`;
}

/** Archived: the daemon's flag, or a stamp. */
export function isArchived(s: Pick<CodeSession, "archivedAt"> & { archived?: boolean }): boolean {
  return !!s.archivedAt || s.archived === true;
}

/** A whole, non-negative count off the wire (anything else reads 0). */
function count(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0;
}

/** v1.417.0: how often an open session is read again while work goes on
 *  that no reply of the user's owns — helper agents in the background, or the
 *  tool writing a report of its own (it appears as it is written). */
export const CODE_BACKGROUND_REREAD_MS = 4_000;
/** v1.417.0: …and while only background COMMANDS run (a dev server can run
 *  for hours; its end is rarely news). */
export const CODE_COMMANDS_REREAD_MS = 20_000;

/** v1.417.0: how often to read the session again for work no reply owns
 *  (0 = not at all). Never while a reply of the user's runs (it is followed). */
export function backgroundRereadMs(
  s: Pick<CodeSession, "background" | "backgroundHelpers" | "selfReply"> & Partial<Pick<CodeSession, "programs">> | null,
  running: boolean,
): number {
  if (!s || running) return 0;
  if (s.selfReply || s.backgroundHelpers > 0) return CODE_BACKGROUND_REREAD_MS;
  // v1.422.0 (B4): a session kept open for a program it runs is read again
  // like one with background commands — its line stays true (the program can
  // end), and each read tells Iron Jarvis the session is still in front.
  if (s.background > 0 || (s.programs?.length ?? 0) > 0) return CODE_COMMANDS_REREAD_MS;
  return 0;
}

/** v1.422.0 (C4.7): how often the Code view tells Iron Jarvis it still shows
 *  a session (only while the window is seen): the session in front is closed
 *  after 60 minutes with nothing to do, not 15 (hosted/programs BEAT_S). */
export const CODE_SEEN_BEAT_MS = 60_000;

/** v1.422.0 (B4): the press that closes a session kept open for its
 *  programs — they stop with it (two presses). */
export function closeToolWords(toolName: string): { idle: string; armed: string; title: string } {
  return {
    idle: `Close ${toolName}`,
    armed: "Close it and stop what it runs?",
    title: `Closes ${toolName} and every program it started. The conversation stays: your next message starts it again.`,
  };
}

function keptPrograms(v: unknown): KeptProgram[] {
  if (!Array.isArray(v)) return [];
  const out: KeptProgram[] = [];
  for (const item of v.slice(0, 20)) {
    const r = obj(item);
    if (!r) continue;
    const command = str(r.command, 300);
    const ports = Array.isArray(r.ports)
      ? r.ports.filter((p): p is number => typeof p === "number" && Number.isInteger(p) && p > 0 && p < 65536)
      : [];
    if (command) out.push({ command, ports });
  }
  return out;
}

/** v1.417.0: the one quiet line under the conversation while work goes on in
 *  the background after a reply ended ("" = none). */
export function backgroundWorkWords(
  s: Pick<CodeSession, "background" | "backgroundHelpers" | "selfReply"> | null,
  toolName: string,
  running: boolean,
): string {
  if (!s || running) return "";
  const helpers = s.backgroundHelpers;
  if (helpers === 1) return "1 helper is still working in the background. Its report will appear here when it finishes.";
  if (helpers > 1) return `${helpers} helpers are still working in the background. Their report will appear here when they finish.`;
  if (s.selfReply) return `${toolName} is writing a reply of its own. It appears here as it goes.`;
  const commands = s.background;
  if (commands === 1) return "1 background command is still running.";
  if (commands > 1) return `${commands} background commands are still running.`;
  return "";
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
    background: count(r.background),
    backgroundHelpers: count(r.background_helpers),
    selfReply: r.self_reply === true,
    programs: keptPrograms(r.programs),
    keepNote: str(r.keep_note, 1_000),
    stoppedNote: str(r.stopped_note, 2_000),
    noFolder: r.no_folder === true,
    workRoot: r.no_folder === true ? str(r.work_root, 1_000) : "",
    handedFrom: str(r.handed_from, 200).trim(),
    ...(r.jarvis_tools === false ? { jarvisToolsTaken: false as const } : {}),
    ...(obj(r.capabilities)
      ? {
          jarvisTools: {
            memory: obj(r.capabilities)!.memory === true,
            browser: obj(r.capabilities)!.browser === true,
            handoff: obj(r.capabilities)!.handoff === true,
          },
        }
      : {}),
    ...(typeof r.can_branch === "boolean" ? { canBranch: r.can_branch } : {}),
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
  /** v1.412.0: the sessions started with no folder (one group, "No folder";
   *  each keeps its files in a folder of its own). */
  noFolder?: boolean;
}

/** v1.412.0: the group of sessions started with no folder. */
export const NO_FOLDER_GROUP = "No folder";

/** The folder a session works in, in words, for its header. */
export function sessionFolderWords(s: Pick<CodeSession, "folder" | "noFolder">): string {
  if (s.noFolder) return s.folder ? `its own folder, ${s.folder}` : "no folder yet: one is made with your first message";
  return s.folder;
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
    // v1.412.0: every session started with no folder shares ONE group (the
    // native harness's v1.379.0 rule), its own folder made or not.
    const k = s.noFolder ? "\u0000no-folder" : key(s.folder);
    let g = groups.get(k);
    if (!g) {
      g = s.noFolder
        ? { folder: "", name: NO_FOLDER_GROUP, sessions: [], noFolder: true }
        : { folder: s.folder, name: folderName(s.folder), sessions: [] };
      groups.set(k, g);
    }
    g.sessions.push(s);
  }
  return [...groups.values()];
}

/** The account a session runs on, in words. */
export function accountWords(s: Pick<CodeSession, "account" | "accountName" | "tool">, tools?: readonly CodingTool[] | null): string {
  if (!s.account) return signsInItself(s.tool, tools) ? ownSignInWords(s.tool, tools) : "this PC's sign-in";
  if (s.accountName) return s.accountName;
  const t = tools?.find((x) => x.id === s.tool);
  return t?.accounts.find((a) => a.id === s.account)?.name || s.account;
}

/** v1.411.0: does this tool sign in by itself (Pi)? The daemon's word, else
 *  Pi by name (a list not read yet). */
export function signsInItself(tool: string, tools?: readonly CodingTool[] | null): boolean {
  const t = tools?.find((x) => x.id === tool);
  return t ? t.ownSignIn : tool === "pi" || tool === "opencode" || tool === "antigravity";
}

/** "Pi's own sign-in". */
export function ownSignInWords(tool: string, tools?: readonly CodingTool[] | null): string {
  return `${toolLabel(tool, tools)}'s own sign-in`;
}

/** What the chip's title says about a tool that signs in by itself. */
export const OWN_SIGN_IN_TITLE =
  "Pi signs in by itself, per model provider: to change it, open Pi in a Build terminal and type /login there. " +
  "Iron Jarvis's accounts do not apply.";

/** v1.413.0: what the chip's title says about THIS tool's own sign-in. */
export const OPENCODE_SIGN_IN_TITLE =
  "OpenCode signs in by itself, per provider: to change it, run opencode auth login in a Build terminal. " +
  "Iron Jarvis's accounts do not apply.";

/** v1.439.0: Antigravity signs in by itself, with its own Google sign-in. */
export const ANTIGRAVITY_SIGN_IN_TITLE =
  "Antigravity signs in by itself, with its own Google sign-in: to change it, run agy in a Build terminal and " +
  "sign in there. Iron Jarvis's accounts do not apply.";

/** v1.413.0: the own-sign-in title for a tool — the daemon's words when it
 *  sent them, else the tool's own (Pi's for Pi, OpenCode's for OpenCode,
 *  v1.439.0: Antigravity's for Antigravity). */
export function ownSignInTitle(tool: string, tools?: readonly CodingTool[] | null): string {
  const hint = tools?.find((x) => x.id === tool)?.signInHint;
  if (hint) return hint;
  if (tool === "antigravity") return ANTIGRAVITY_SIGN_IN_TITLE;
  return tool === "opencode" ? OPENCODE_SIGN_IN_TITLE : OWN_SIGN_IN_TITLE;
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
  // v1.411.0: a tool that has ONE mode, its own (Pi asks nothing itself).
  own: "Its own rules",
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
  /** v1.411.0: may the user type an answer of their own? False for a Pi
   *  extension's `select`, which takes one of ITS choices only. */
  freeText: boolean;
}

/** Every event may name the turn it belongs to (the daemon's `turn`): a
 *  rejoin cuts the saved list by it. */
export type CodeEvent = CodeEventBody & { turn?: string };

type CodeEventBody =
  | { type: "session"; at: string; harness: string; harnessSessionId: string; model: string; cwd: string; mode: string }
  /** v1.422.0: `carry` = a message Iron Jarvis sent BY ITSELF to carry the
   *  reply on (after a restart, an account's reset, a move) — drawn as a
   *  quiet line, never as the user's bubble. */
  | { type: "user"; at: string; text: string; files: string[]; carry?: CarryMark }
  /** v1.418.0 (C4.2): `parent` = the helper step (Claude Code's Task /
   *  Agent) this text, thinking or step ran inside (absent = the
   *  conversation itself). */
  | { type: "text"; at: string; item: string; delta: string; parent?: string }
  | { type: "text_done"; at: string; item: string; text: string; parent?: string }
  | { type: "thinking"; at: string; item: string; delta: string; parent?: string }
  | {
      type: "tool";
      at: string;
      item: string;
      kind: ToolKind;
      name: string;
      title: string;
      input: string;
      /** v1.427.2: "stopped" = the coding tool (or Iron Jarvis) stopped
       *  before the step finished — drawn "did not finish", never a spinner. */
      status: "running" | "done" | "failed" | "stopped";
      output: string;
      path: string;
      line: number | null;
      diff: string;
      /** v1.418.0 (C4.2): the helper step this step ran inside. */
      parent?: string;
      /** v1.418.0 (C4.2): a running row whose input is still being written
       *  or whose output is still arriving. */
      partial?: boolean;
      /** v1.418.0 (C4.4): the input or output was cut for the screen; the
       *  session's record keeps more (read back with `onExpand`). */
      more?: boolean;
      /** v1.418.0: how much of the input / output the record keeps (on a
       *  partial row: how much input has been written so far). */
      inputChars?: number;
      outputChars?: number;
      /** v1.418.0 (C4.4): a command whose title is the tool's own
       *  description of it carries the command here. */
      subtitle?: string;
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
      /** v1.417.0 (C1.1): the card takes "Allow all commands this session"
       *  (the answer `allow_commands`). */
      canAllCommands: boolean;
      /** v1.417.0 (C4.1): the plan the tool asks to start on (Markdown),
       *  "" for any other card. */
      plan: string;
      /** v1.417.0 (C1.7): WHY a once-only card asks every time
       *  (lib/onceReason); a safety reason keeps Enter from allowing it. */
      onceReason: string;
      /** v1.439.0: on Claude Code's plan card, the mode Allow once leaves
       *  the session in (absent = not known). */
      thenMode?: string;
      /** v1.439.0: on Claude Code's plan card, the mode the session-wide
       *  allow switches to (absent = it sets none). */
      setsMode?: string;
    }
  | { type: "question"; at: string; requestId: string; questions: QuestionItem[] }
  | { type: "resolved"; at: string; requestId: string; decision: string }
  | {
      type: "usage";
      at: string;
      input: number;
      output: number;
      cacheRead: number;
      costUsd: number | null;
      listPrice: boolean;
      /** v1.411.0: the tool that reported the cost when nobody can say
       *  whether it is list price or real spend (Pi), else "". */
      costReportedBy: string;
    }
  | { type: "notice"; at: string; text: string }
  | { type: "error"; at: string; title: string; fix: string; technical: string; fixKind: string; account: string }
  | { type: "done"; at: string; stop: "completed" | "interrupted" | "failed" | "max_turns"; result: string }
  /** v1.412.0: a NOTE the user typed while the tool worked (a steer): one
   *  event per state — sent (with `how` it arrives), read (the tool took it),
   *  returned (never read: it goes back to the message box). */
  | { type: "steer"; at: string; id: string; state: SteerState; text: string; files: string[]; how: string };

export type SteerState = "sent" | "read" | "returned";

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
    case "user": {
      const carry = decodeCarryMark(r.carry);
      return {
        type,
        at,
        text: str(r.text, 100_000),
        files: Array.isArray(r.files) ? r.files.filter((f): f is string => typeof f === "string").slice(0, 20) : [],
        ...(carry ? { carry } : {}),
      };
    }
    case "text":
    case "thinking": {
      const item = str(r.item, 200);
      if (!item) return null;
      return withParent({ type, at, item, delta: str(r.delta, 100_000) }, r);
    }
    case "text_done": {
      const item = str(r.item, 200);
      if (!item) return null;
      return withParent({ type, at, item, text: str(r.text, 200_000) }, r);
    }
    case "tool": {
      const item = str(r.item, 200);
      if (!item) return null;
      const kind = TOOL_KINDS.includes(r.kind as ToolKind) ? (r.kind as ToolKind) : "other";
      const status = r.status === "done" || r.status === "failed" || r.status === "stopped" ? r.status : "running";
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
        ...toolExtras(r),
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
        canAllCommands: r.can_all_commands === true,
        plan: str(r.plan, 20_000),
        onceReason: str(r.once_reason, 40),
        ...(PLAN_MODES.includes(r.then_mode as string) ? { thenMode: r.then_mode as string } : {}),
        ...(PLAN_MODES.includes(r.sets_mode as string) ? { setsMode: r.sets_mode as string } : {}),
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
        questions.push({
          question,
          header: str(qo.header, 80),
          options,
          multi: qo.multi === true,
          freeText: qo.free_text !== false,
        });
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
        costReportedBy: str(r.cost_reported_by, 40).trim(),
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
    case "steer": {
      const id = str(r.id, 200);
      const state = r.state === "read" || r.state === "returned" ? r.state : r.state === "sent" ? "sent" : null;
      if (!id || !state) return null;
      return {
        type,
        at,
        id,
        state,
        text: str(r.text, 100_000),
        files: Array.isArray(r.files) ? r.files.filter((f): f is string => typeof f === "string").slice(0, 20) : [],
        how: str(r.how, 300),
      };
    }
    case "done": {
      const stop =
        r.stop === "interrupted" || r.stop === "failed" || r.stop === "max_turns" ? r.stop : "completed";
      return { type, at, stop, result: str(r.result, 20_000) };
    }
    default:
      return null;
  }
}

/** v1.418.0 (C4.2): the helper step an event ran inside, when it names one. */
function withParent<T extends object>(ev: T, r: Record<string, unknown>): T & { parent?: string } {
  const parent = str(r.parent, 200).trim();
  return parent ? { ...ev, parent } : ev;
}

interface ToolExtras {
  parent?: string;
  partial?: boolean;
  more?: boolean;
  inputChars?: number;
  outputChars?: number;
  subtitle?: string;
}

/** v1.418.0 (C4.2, C4.4): a step's optional keys, each only when it says
 *  something (an older daemon's events decode exactly as before). */
function toolExtras(r: Record<string, unknown>): ToolExtras {
  const out: ToolExtras = {};
  const parent = str(r.parent, 200).trim();
  if (parent) out.parent = parent;
  if (r.partial === true) out.partial = true;
  if (r.more === true) out.more = true;
  const ic = num(r.input_chars);
  if (ic !== null && ic > 0) out.inputChars = Math.floor(ic);
  const oc = num(r.output_chars);
  if (oc !== null && oc > 0) out.outputChars = Math.floor(oc);
  const subtitle = str(r.subtitle, 300).trim();
  if (subtitle) out.subtitle = subtitle;
  return out;
}

/** v1.418.0 (C4.4): the whole text the session's record keeps for one step
 *  — the answer of `GET /build/app/sessions/{id}/events/{item}`
 *  (`{item, input, output}`); null for any other shape. */
export interface ItemText {
  input: string;
  output: string;
}

export function decodeItemText(raw: unknown): ItemText | null {
  const r = obj(raw);
  if (!r || (typeof r.input !== "string" && typeof r.output !== "string")) return null;
  return { input: str(r.input, 25_000), output: str(r.output, 25_000) };
}

/** v1.418.0 (C4.4): the press under a step whose text was cut for the
 *  screen ("Show all 6,759 characters"). */
export function showAllWords(t: { inputChars?: number; outputChars?: number }): string {
  const n = Math.max(t.outputChars ?? 0, t.inputChars ?? 0);
  return n > 0 ? `Show all ${n.toLocaleString("en-US")} characters` : "Show all";
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
  /** v1.411.0: the conversation keeps its LAST model and effort (Pi restores
   *  them when it reopens a conversation with messages), so "the tool's
   *  default" is not offered. */
  keepsLast: boolean;
  /** v1.431.0: a Pi session's folder has its own Pi extensions, skills or
   *  settings that Pi skips here (it can only ask in its terminal), or this
   *  session already loads them; null = nothing to say. */
  piFolder: PiFolder | null;
  /** v1.474.0: the helpers this session's coding tool started. */
  helpers: CodeHelper[];
  /** v1.474.0: for a helper, the session that started it. */
  handedFrom: { id: string; title: string; label: string } | null;
}

/** v1.474.0 (Hand off): one helper session another session's tool started. */
export interface CodeHelper {
  id: string;
  title: string;
  label: string;
  state: "working" | "waiting" | "done" | "failed" | "stopped" | "idle";
}

const HELPER_STATES = new Set(["working", "waiting", "done", "failed", "stopped", "idle"]);

/** How a helper stands, in words (statusWords' vocabulary). */
export function helperStateWords(state: CodeHelper["state"]): string {
  return {
    working: "Working",
    waiting: "Waiting for you",
    done: "Done",
    failed: "Failed",
    stopped: "Stopped",
    idle: "Not started",
  }[state];
}

export function decodeHelpers(raw: unknown): CodeHelper[] {
  const out: CodeHelper[] = [];
  for (const h of Array.isArray(raw) ? raw.slice(0, 20) : []) {
    const ho = obj(h);
    const id = ho ? str(ho.id, 200).trim() : "";
    if (!ho || !id || out.some((x) => x.id === id)) continue;
    const state = str(ho.state, 20);
    out.push({
      id,
      title: str(ho.title, 300).trim(),
      label: str(ho.label, 60).trim(),
      state: (HELPER_STATES.has(state) ? state : "idle") as CodeHelper["state"],
    });
  }
  return out;
}

/** v1.431.0: what the daemon says about a Pi folder's own resources
 *  (`hosted/pi_trust`). `offer`: the folder is undecided and this session
 *  does not load them yet — the one press loads them (`pi --approve`). */
export interface PiFolder {
  line: string;
  title: string;
  offer: boolean;
  loaded: boolean;
}

/** The press that loads a Pi folder's own resources for this session. */
export const PI_FOLDER_LOAD_LABEL = "Load them for this session";
/** The press that goes back to Pi's own rule (skip them). */
export const PI_FOLDER_SKIP_LABEL = "Stop loading them";

/** An answer's `pi_folder`: the facts, null (said: nothing), or undefined
 *  when the answer does not carry the key at all (an older daemon, another
 *  tool). Only the daemon's own words are shown. */
export function piFolderOf(raw: unknown): PiFolder | null | undefined {
  const r = obj(raw);
  if (!r || !("pi_folder" in r)) return undefined;
  const p = obj(r.pi_folder);
  if (!p) return null;
  const line = str(p.line, 400).trim();
  if (!line) return null;
  return { line, title: str(p.title, 600).trim(), offer: p.offer === true, loaded: p.loaded === true };
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
    out.push({ name, description: str(co.description, 300), ...commandPrefix(co.prefix), ...commandExtras(co) });
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
    keepsLast: r.keeps_last === true,
    piFolder: piFolderOf(r) ?? null,
    helpers: decodeHelpers(r.helpers),
    handedFrom: (() => {
      const h = obj(r.handed_from);
      const id = h ? str(h.id, 200).trim() : "";
      return h && id ? { id, title: str(h.title, 300).trim(), label: str(h.label, 60).trim() } : null;
    })(),
  };
}

/** A view's or a change's `keeps_last` (null when the answer does not say). */
export function keepsLastOf(raw: unknown): boolean | null {
  const r = obj(raw);
  return r && typeof r.keeps_last === "boolean" ? r.keeps_last : null;
}

/** Why the model and effort chips offer no "default" for such a conversation. */
export const KEEPS_LAST_TITLE =
  "This conversation keeps the model and thinking level it last used: Pi restores them when it reopens it.";

/** v1.413.0: the keeps-last title for a tool (Pi's words stay Pi's). */
export function keepsLastTitle(tool: string): string {
  if (tool === "grok") return "This conversation keeps the model it last used: Grok keeps it when it reopens it.";
  return tool === "opencode"
    ? "This conversation keeps the model and effort it last used: OpenCode keeps them when it reopens it."
    : KEEPS_LAST_TITLE;
}

/** The "no model chosen" row of a session's model picker (v1.475.0: the
 *  panel is already about one tool, so it does not repeat the tool's name). */
export function defaultModelWords(tool: CodingTool | null, keepsLast: boolean): string {
  if (keepsLast) return "Its last model";
  return tool?.defaultName ? `Default (${tool.defaultName})` : "Default model";
}

/** The first row of the effort chip. */
export function defaultEffortWords(keepsLast: boolean): string {
  return keepsLast ? "Its last effort" : "Default effort";
}

// ── the conversation ────────────────────────────────────────────────────────

export type ToolEvent = Extract<CodeEvent, { type: "tool" }>;
export type ApprovalEvent = Extract<CodeEvent, { type: "approval" }>;
export type QuestionEvent = Extract<CodeEvent, { type: "question" }>;
export type OpenAsk = ApprovalEvent | QuestionEvent;

export type ConvItem =
  /** v1.441.0: `at` names the message (Branch from here / Rewind to here). */
  | { kind: "user"; key: string; text: string; files: string[]; carry?: CarryMark; at?: string }
  | { kind: "text"; key: string; text: string; done: boolean }
  /** v1.418.0 (C4.3): `live` while the thinking is the newest thing the
   *  running turn said; `seconds` = how long it thought (null = unknown). */
  | { kind: "thinking"; key: string; text: string; live: boolean; seconds: number | null }
  /** v1.418.0 (C4.2): a helper step (Task / Agent) carries the steps, text
   *  and thinking that ran inside it, folded the same way. */
  | { kind: "tool"; key: string; tool: ToolEvent; children?: ConvItem[] }
  | { kind: "plan"; key: string; steps: { text: string; status: PlanStatus }[] }
  | { kind: "resolved"; key: string; requestId: string; decision: string; about: string; question: boolean }
  | { kind: "notice"; key: string; text: string }
  | { kind: "error"; key: string; title: string; fix: string; technical: string; fixKind: string; account: string }
  | {
      kind: "usage";
      key: string;
      input: number;
      output: number;
      costUsd: number | null;
      listPrice: boolean;
      costReportedBy: string;
      /** v1.417.0 (C4.9): this turn's `done` carries it in its receipt (the
       *  line then draws nothing of its own). */
      folded?: boolean;
    }
  | {
      kind: "done";
      key: string;
      stop: Extract<CodeEvent, { type: "done" }>["stop"];
      result: string;
      /** v1.417.0 (C4.9): what the turn did, read off its own events. */
      receipt?: TurnReceipt | null;
    }
  /** v1.412.0: a note sent while it worked, at the place it was sent, with
   *  its newest state. */
  | { kind: "steer"; key: string; id: string; text: string; files: string[]; state: SteerState; how: string };

/** v1.417.0 (C4.9): one turn's receipt, read off its own events — the model
 *  the newest `session` event named, how long it took (its `user` to its
 *  `done`, null when either time is missing), the steps it took (a plan
 *  update is not a step), the files it changed (edits and writes that
 *  finished), and its usage line. */
export interface TurnReceipt {
  model: string;
  seconds: number | null;
  actions: number;
  /** v1.449.0 (plan 0h.5 item 10): the steps that FAILED (a step whose end
   *  said so and that never landed afterwards). Counted inside `actions`,
   *  said apart on the line, and what turns the receipt's glyph amber. */
  failed: number;
  files: number;
  usage: { input: number; output: number; costUsd: number | null; listPrice: boolean; costReportedBy: string } | null;
}

function secondsBetween(a: string, b: string): number | null {
  const x = Date.parse(a);
  const y = Date.parse(b);
  if (!Number.isFinite(x) || !Number.isFinite(y) || y < x) return null;
  // v1.449.0: kept to the millisecond — the app's duration words
  // (lib/durationWords) say "5.3 s" under ten seconds, as the chat does.
  return (y - x) / 1000;
}

/** The app's duration words: "9.0 s", "14 s", "2 min 14 s", "63 min". v1.449.0
 *  (plan 0h.5 item 10): the same clock vocabulary as every chat receipt
 *  (lib/durationWords.secondsText) — never "2 m 14 s" or "1 h 3 m". */
export function receiptDuration(seconds: number): string {
  return secondsText(Math.max(0, seconds) * 1000) ?? "";
}

/** v1.449.0 (plan 0h.5 item 10): how the turn ended, for the receipt's
 *  glyph — "ok" (a ✓) for a reply that ran to its end with no failed step,
 *  "warn" (a ⚠) for a stopped, failed or cut-off reply or one with a step
 *  that failed; the outcome's tone, as WorkLine draws a reply's steps. */
export type ReceiptOutcome = "ok" | "warn";
export function receiptOutcome(stop: Extract<CodeEvent, { type: "done" }>["stop"], r: Pick<TurnReceipt, "failed"> | null): ReceiptOutcome {
  if (stop !== "completed") return "warn";
  return r && r.failed > 0 ? "warn" : "ok";
}

/** v1.455.0 (Wave F, item 18): the outcome a screen reader hears — the ✓ /
 *  ⚠ glyph is drawn for the eye only. */
export const RECEIPT_OUTCOME_WORDS: Record<ReceiptOutcome, string> = {
  ok: "Finished",
  warn: "Needs a look",
};

/** v1.455.0 (Wave F, item 18): the coding tools whose model always answers
 *  on the vendor's service — the daemon's `hosted/panes.CLOUD_TOOLS` (a
 *  Python test keeps the two equal). Pi and OpenCode pick a provider of their
 *  own, so where they ran is not known from the tool alone (nothing said). */
export const CODE_CLOUD_TOOLS: readonly string[] = ["claude-code", "codex", "grok", "antigravity"];

/** Where a Build › Code reply ran, for its receipt: "cloud" for a tool in
 *  CODE_CLOUD_TOOLS (the daemon's WHERE_CLOUD word), else "" (not known). */
export function codeReceiptWhere(tool: string): string {
  return CODE_CLOUD_TOOLS.includes((tool || "").trim().toLowerCase()) ? "cloud" : "";
}

/** v1.417.0 (C4.9): the reply line — "answered by <model> on <account> ·
 *  2 min 14 s · 7 actions · 3 files changed", the way Chat › Code's receipt
 *  reads. A part with nothing to say is left out; "" when nothing is known.
 *  v1.449.0: a failed step is said apart ("7 actions · 1 failed"), the
 *  WorkLine way. */
export function receiptLine(r: TurnReceipt, account = ""): string {
  const parts: string[] = [];
  const who = r.model ? `answered by ${r.model}${account ? ` on ${account}` : ""}` : account ? `on ${account}` : "";
  if (who) parts.push(who);
  if (r.seconds !== null) parts.push(receiptDuration(r.seconds));
  if (r.actions > 0) parts.push(`${r.actions} action${r.actions === 1 ? "" : "s"}`);
  if (r.failed > 0) parts.push(`${r.failed} failed`);
  if (r.files > 0) parts.push(`${r.files} file${r.files === 1 ? "" : "s"} changed`);
  return parts.join(" · ");
}

export interface Conversation {
  items: ConvItem[];
  /** Approvals and questions still waiting on the user, in asking order. */
  asks: OpenAsk[];
  /** The tool's own session id, when a `session` event named it. */
  toolSessionId: string;
  /** The last turn ended (a `done` after the last `user`), or none ran. */
  settled: boolean;
}

/** What the fold of one level (the conversation, or one helper's rows)
 *  shares with the levels inside it. */
interface FoldShared {
  /** Each step's helper (a helper's helper folds inside its own row). */
  parentOf: Map<string, string>;
  /** True while folding event by event: `parentOf` is learned as tool
   *  events arrive (a whole fold reads it from every event first). */
  learn: boolean;
  /** Helpers looked up before any event named THEIR helper. */
  missed: Set<string>;
  /** A step's helper became known after an earlier event was placed without
   *  it: folding event by event no longer matches a whole fold. */
  broken: boolean;
}

/** What the fold of one level holds between events. v1.472.0: kept, so a
 *  stream adds only the events that arrived (`foldConversationFrom`)
 *  instead of folding the whole session again on every frame. */
export interface FoldState {
  root: string;
  shared: FoldShared;
  items: ConvItem[];
  children: Map<string, FoldState>;
  /** The last children rows made for each helper (made again only when
   *  that helper's own events changed). */
  childCache: Map<string, { version: number; row: ConvItem; out: ConvItem }>;
  /** Events this level was given (its own and its helpers'). */
  version: number;
  thinkOpen: number | null;
  thinkStart: string;
  textAt: Map<string, number>;
  thinkAt: Map<string, number>;
  toolAt: Map<string, number>;
  steerAt: Map<string, number>;
  planAt: number | null;
  asks: Map<string, OpenAsk>;
  askTitle: Map<string, string>;
  askWasQuestion: Set<string>;
  resolved: Set<string>;
  toolSessionId: string;
  settled: boolean;
  n: number;
  // v1.417.0 (C4.9): what the current turn did, for its receipt.
  model: string;
  turnAt: string;
  usageAt: number | null;
  turnSteps: Set<string>;
  turnFiles: Set<string>;
  // v1.449.0: the steps that failed (and never landed afterwards).
  turnFailed: Set<string>;
  // (review) the turn's last moment of WORK: a turn the daemon's restart cut
  // off gets its error and done only when Iron Jarvis starts again
  // (service.reconcile_at_boot), hours later — such a turn is timed to its
  // last step, never to the next start. The app's own words (a notice, an
  // error) are not work.
  workAt: string;
}

function newFold(root: string, shared: FoldShared): FoldState {
  return {
    root,
    shared,
    items: [],
    children: new Map(),
    childCache: new Map(),
    version: 0,
    thinkOpen: null,
    thinkStart: "",
    textAt: new Map(),
    thinkAt: new Map(),
    toolAt: new Map(),
    steerAt: new Map(),
    planAt: null,
    asks: new Map(),
    askTitle: new Map(),
    askWasQuestion: new Set(),
    resolved: new Set(),
    toolSessionId: "",
    settled: true,
    n: 0,
    model: "",
    turnAt: "",
    usageAt: null,
    turnSteps: new Set(),
    turnFiles: new Set(),
    turnFailed: new Set(),
    workAt: "",
  };
}

/** The helper of this level that `parent` sits under (null = not this
 *  level's). */
function ownChild(s: FoldState, parent: string): string | null {
  let p = parent;
  for (let depth = 0; depth < 8; depth += 1) {
    const known = s.shared.parentOf.get(p);
    if (known === undefined) s.shared.missed.add(p);
    const up = known ?? "";
    if (up === s.root) return p;
    if (!up) return s.root ? null : p;
    p = up;
  }
  return null;
}

/** Fold one event into `s`. */
function foldStep(s: FoldState, ev: CodeEvent): void {
  s.version += 1;
  const sh = s.shared;
  if (sh.learn && ev.type === "tool" && ev.parent) {
    const had = sh.parentOf.get(ev.item);
    if (had !== ev.parent) {
      // A whole fold reads every step's helper first: one learned after it
      // was needed (or changed) means event-by-event no longer matches it.
      if (had !== undefined || sh.missed.has(ev.item)) sh.broken = true;
      sh.parentOf.set(ev.item, ev.parent);
    }
  }
  const key = (p: string) => `${p}-${s.n++}`;
  if (s.turnAt && ev.type !== "user" && ev.type !== "done" && ev.type !== "error" && ev.type !== "notice"
    // v1.427.2: a step closed "did not finish" is the app's own row too.
    && !(ev.type === "tool" && ev.status === "stopped")
    && Number.isFinite(Date.parse(ev.at))) {
    s.workAt = ev.at;
  }
  // v1.418.0 (C4.3): anything said after a thinking item ends it.
  if (s.thinkOpen !== null
    && !(ev.type === "thinking" && (ev.parent ?? "") === s.root && s.thinkAt.get(ev.item) === s.thinkOpen)) {
    const it = s.items[s.thinkOpen] as Extract<ConvItem, { kind: "thinking" }>;
    s.items[s.thinkOpen] = { ...it, seconds: secondsBetween(s.thinkStart, ev.at) };
    s.thinkOpen = null;
  }
  // v1.418.0 (C4.2): a helper's own events go into its row.
  const parent = (ev.type === "tool" || ev.type === "text" || ev.type === "text_done" || ev.type === "thinking")
    ? ev.parent ?? ""
    : "";
  if (parent && parent !== s.root) {
    const top = ownChild(s, parent);
    if (top === null) return;
    if (!s.toolAt.has(top)) {
      // Its own step was trimmed (or has not arrived): a row to hold it.
      s.toolAt.set(top, s.items.length);
      s.items.push({
        kind: "tool",
        key: key("x"),
        tool: { type: "tool", at: ev.at, item: top, kind: "task", name: "", title: "", input: "", status: "running",
                output: "", path: "", line: null, diff: "" },
      });
    }
    let child = s.children.get(top);
    if (!child) {
      child = newFold(top, sh);
      s.children.set(top, child);
    }
    foldStep(child, ev);
    if (ev.type === "tool" && (ev.kind === "edit" || ev.kind === "write") && ev.status === "done" && ev.path && s.turnAt) {
      s.turnFiles.add(ev.path);
    }
    return;
  }
  switch (ev.type) {
    case "session":
      if (ev.harnessSessionId) s.toolSessionId = ev.harnessSessionId;
      if (ev.model) s.model = ev.model;
      break;
    case "user":
      s.items.push({ kind: "user", key: key("u"), text: ev.text, files: ev.files, at: ev.at, ...(ev.carry ? { carry: ev.carry } : {}) });
      s.planAt = null;
      s.settled = false;
      s.turnAt = ev.at;
      s.workAt = "";
      s.usageAt = null;
      s.turnSteps.clear();
      s.turnFiles.clear();
      s.turnFailed.clear();
      break;
    case "text": {
      const i = s.textAt.get(ev.item);
      if (i === undefined) {
        s.textAt.set(ev.item, s.items.length);
        s.items.push({ kind: "text", key: key("t"), text: ev.delta, done: false });
      } else {
        const it = s.items[i] as Extract<ConvItem, { kind: "text" }>;
        if (!it.done) s.items[i] = { ...it, text: it.text + ev.delta };
      }
      break;
    }
    case "text_done": {
      const i = s.textAt.get(ev.item);
      if (i === undefined) {
        s.textAt.set(ev.item, s.items.length);
        s.items.push({ kind: "text", key: key("t"), text: ev.text, done: true });
      } else {
        const it = s.items[i] as Extract<ConvItem, { kind: "text" }>;
        s.items[i] = { ...it, text: ev.text || it.text, done: true };
      }
      break;
    }
    case "thinking": {
      const i = s.thinkAt.get(ev.item);
      if (i === undefined) {
        s.thinkAt.set(ev.item, s.items.length);
        s.thinkOpen = s.items.length;
        s.thinkStart = ev.at;
        s.items.push({ kind: "thinking", key: key("k"), text: ev.delta, live: false, seconds: null });
      } else {
        const it = s.items[i] as Extract<ConvItem, { kind: "thinking" }>;
        s.items[i] = { ...it, text: it.text + ev.delta };
      }
      break;
    }
    case "tool": {
      if (ev.kind !== "todo") s.turnSteps.add(ev.item);
      if (ev.kind !== "todo" && ev.status === "failed") s.turnFailed.add(ev.item);
      else if (ev.status === "done") s.turnFailed.delete(ev.item);
      const i = s.toolAt.get(ev.item);
      if ((ev.kind === "edit" || ev.kind === "write") && ev.status === "done") {
        // (review) the step's END may name no path: the one it began with.
        const p = ev.path || (i === undefined ? "" : (s.items[i] as Extract<ConvItem, { kind: "tool" }>).tool.path);
        if (p) s.turnFiles.add(p);
      }
      if (i === undefined) {
        s.toolAt.set(ev.item, s.items.length);
        s.items.push({ kind: "tool", key: key("x"), tool: ev });
      } else {
        const prev = (s.items[i] as Extract<ConvItem, { kind: "tool" }>).tool;
        s.items[i] = {
          kind: "tool",
          key: s.items[i].key,
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
      if (s.planAt === null) {
        s.planAt = s.items.length;
        s.items.push({ kind: "plan", key: key("p"), steps: ev.steps });
      } else {
        s.items[s.planAt] = { kind: "plan", key: s.items[s.planAt].key, steps: ev.steps };
      }
      break;
    case "approval":
    case "question":
      if (!s.resolved.has(ev.requestId)) s.asks.set(ev.requestId, ev);
      s.askTitle.set(ev.requestId, ev.type === "approval" ? ev.title : ev.questions[0]?.question ?? "");
      if (ev.type === "question") s.askWasQuestion.add(ev.requestId);
      break;
    case "resolved":
      s.resolved.add(ev.requestId);
      s.asks.delete(ev.requestId);
      s.items.push({
        kind: "resolved",
        key: key("r"),
        requestId: ev.requestId,
        decision: ev.decision,
        about: s.askTitle.get(ev.requestId) ?? "",
        question: s.askWasQuestion.has(ev.requestId),
      });
      break;
    case "notice":
      s.items.push({ kind: "notice", key: key("n"), text: ev.text });
      break;
    case "error":
      s.items.push({
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
      s.usageAt = s.items.length;
      s.items.push({
        kind: "usage",
        key: key("g"),
        input: ev.input,
        output: ev.output,
        costUsd: ev.costUsd,
        listPrice: ev.listPrice,
        costReportedBy: ev.costReportedBy,
      });
      break;
    case "steer": {
      const i = s.steerAt.get(ev.id);
      if (i === undefined) {
        s.steerAt.set(ev.id, s.items.length);
        s.items.push({ kind: "steer", key: key("s"), id: ev.id, text: ev.text, files: ev.files, state: ev.state, how: ev.how });
      } else {
        const it = s.items[i] as Extract<ConvItem, { kind: "steer" }>;
        s.items[i] = { ...it, state: ev.state, text: it.text || ev.text, files: it.files.length ? it.files : ev.files, how: it.how || ev.how };
      }
      break;
    }
    case "done": {
      // v1.417.0 (C4.9): the turn's receipt; its usage line folds into it.
      let usage: TurnReceipt["usage"] = null;
      if (s.usageAt !== null) {
        const u = s.items[s.usageAt] as Extract<ConvItem, { kind: "usage" }>;
        usage = { input: u.input, output: u.output, costUsd: u.costUsd, listPrice: u.listPrice, costReportedBy: u.costReportedBy };
        s.items[s.usageAt] = { ...u, folded: true };
      }
      const receipt: TurnReceipt = {
        model: s.model,
        // (review) a turn that was stopped (or cut off by a restart) is
        // timed to its last step: its done may be written much later.
        seconds: !s.turnAt
          ? null
          : ev.stop === "interrupted"
            ? s.workAt ? secondsBetween(s.turnAt, s.workAt) : null
            : ev.at ? secondsBetween(s.turnAt, ev.at) : null,
        actions: s.turnSteps.size,
        failed: s.turnFailed.size,
        files: s.turnFiles.size,
        usage,
      };
      const worth = !!(receipt.model || receipt.seconds !== null || receipt.actions || receipt.files || receipt.usage);
      s.items.push({ kind: "done", key: key("d"), stop: ev.stop, result: ev.result, receipt: worth ? receipt : null });
      s.usageAt = null;
      s.turnSteps.clear();
      s.turnFiles.clear();
      s.turnFailed.clear();
      s.turnAt = "";
      s.workAt = "";
      // A turn that ended asks nothing any more.
      s.asks.clear();
      s.settled = true;
      break;
    }
  }
}

/** What `s` draws now (`s` itself is not changed: more events may follow). */
function foldResult(s: FoldState, extra: readonly CodeEvent[]): Conversation {
  const items = s.items.slice();
  // v1.418.0 (C4.3): a thinking item nothing has followed yet is live while
  // the turn runs and it is the newest thing said.
  if (s.thinkOpen !== null && !s.settled && s.thinkOpen === items.length - 1) {
    const it = items[s.thinkOpen] as Extract<ConvItem, { kind: "thinking" }>;
    items[s.thinkOpen] = { ...it, live: true };
  }
  // v1.418.0 (C4.2): each helper step gets what ran inside it (made again
  // only when that helper's events or its own row changed).
  for (const [top, child] of s.children) {
    const i = s.toolAt.get(top);
    if (i === undefined) continue;
    const row = items[i];
    const cached = s.childCache.get(top);
    if (cached && cached.version === child.version && cached.row === row) {
      items[i] = cached.out;
      continue;
    }
    const children = foldResult(child, []).items;
    const out = children.length ? { ...(row as Extract<ConvItem, { kind: "tool" }>), children } : row;
    s.childCache.set(top, { version: child.version, row, out });
    items[i] = out;
  }
  const asks = new Map(s.asks);
  if (!s.settled) {
    for (const p of extra) {
      if (p.type !== "approval" && p.type !== "question") continue;
      if (s.resolved.has(p.requestId) || asks.has(p.requestId)) continue;
      asks.set(p.requestId, p);
    }
  }
  return { items, asks: [...asks.values()], toolSessionId: s.toolSessionId, settled: s.settled };
}

/** Fold the event list into what the screen draws. Text and thinking are
 *  joined per `item`; a tool's later events update its one row; one plan per
 *  turn, at the place it first appeared, showing its newest steps. An ask
 *  is open until its `resolved` (or the turn's `done`) arrives. `extra`:
 *  the session read's open asks, listed when their events were trimmed.
 *
 *  v1.418.0 (C4.2): an event that ran inside a helper step (its `parent`)
 *  is folded into that step's `children` (`root`: the helper whose own
 *  events these are — "" = the conversation). (C4.3) a thinking item is
 *  `live` while it is the newest thing the running turn said, and knows how
 *  long it lasted (to the next event) once something follows it. */
export function foldConversation(
  events: readonly CodeEvent[],
  extra: readonly CodeEvent[] = [],
  root = "",
): Conversation {
  const shared: FoldShared = { parentOf: new Map(), learn: false, missed: new Set(), broken: false };
  for (const ev of events) {
    if (ev.type === "tool" && ev.parent) shared.parentOf.set(ev.item, ev.parent);
  }
  const s = newFold(root, shared);
  for (const ev of events) foldStep(s, ev);
  return foldResult(s, extra);
}

/** v1.472.0: a fold that can take more events (`foldConversationFrom`). */
export interface FoldMemo {
  events: readonly CodeEvent[];
  state: FoldState;
}

/** `foldConversation(events, extra)`, folding only what came after the
 *  events `memo` already holds — the answer is the same (pinned against a
 *  whole fold, event by event). A list that is not the old one plus more (a
 *  session read again, earlier steps loaded, the echo of a question that
 *  replaced the one drawn when it was sent), or a helper whose own helper
 *  became known late, folds whole. */
export function foldConversationFrom(
  memo: FoldMemo | null,
  events: readonly CodeEvent[],
  extra: readonly CodeEvent[] = [],
): { conversation: Conversation; memo: FoldMemo | null } {
  let state: FoldState | null = null;
  if (memo && events.length >= memo.events.length) {
    const old = memo.events;
    let same = true;
    for (let i = 0; i < old.length; i += 1) {
      if (events[i] !== old[i]) {
        same = false;
        break;
      }
    }
    if (same) {
      state = memo.state;
      for (let i = old.length; i < events.length; i += 1) foldStep(state, events[i]);
      if (state.shared.broken) state = null;
    }
  }
  if (!state) {
    state = newFold("", { parentOf: new Map(), learn: true, missed: new Set(), broken: false });
    for (const ev of events) foldStep(state, ev);
    // A helper's helper became known after its events were placed: the
    // whole fold (which reads every step's helper first), nothing kept.
    if (state.shared.broken) return { conversation: foldConversation(events, extra), memo: null };
  }
  return { conversation: foldResult(state, extra), memo: { events, state } };
}

/** v1.418.0 (C4.2): what a helper step's folded row says before it opens —
 *  how many steps it took (its own and its helpers') and the last thing it
 *  wrote (its answer, "" until it wrote one). */
export function helperSummary(children: readonly ConvItem[] | undefined): { steps: number; finalText: string } {
  let steps = 0;
  let finalText = "";
  for (const c of children ?? []) {
    if (c.kind === "tool") {
      if (c.tool.kind !== "todo") steps += 1;
      steps += helperSummary(c.children).steps;
    } else if (c.kind === "text" && c.text.trim()) {
      finalText = c.text;
    }
  }
  return { steps, finalText };
}

/** v1.427.2: a step the coding tool (or Iron Jarvis) stopped before it
 *  finished — beside its title, where a running step shows its spinner. */
export const STEP_STOPPED_WORDS = "did not finish";

/** "3 steps" · "1 step". */
export function helperStepsWords(n: number): string {
  return `${n} step${n === 1 ? "" : "s"}`;
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

/** v1.483.1: `drawn` plus every notice in `saved` newer than anything in
 *  `drawn` (by its `at`). Used when a re-read keeps what is drawn because the
 *  saved record holds fewer events than the live pieces on screen. Stamps are
 *  compared as times (`stampKey`: the daemon leaves the fraction out of a
 *  whole-second stamp, and "…:07Z" sorts after "…:07.3Z" as text), and a
 *  notice stamped AT the newest drawn time is added unless it is drawn
 *  already (the stamps tick about once a millisecond). */
export function withNewerNotices(drawn: CodeEvent[], saved: CodeEvent[]): CodeEvent[] {
  let last = "";
  const shown = new Set<string>();
  for (const e of drawn) {
    const at = stampKey((e as { at?: unknown }).at);
    if (at && at > last) last = at;
    if (e.type === "notice") shown.add(noticeKey(e));
  }
  const newer = saved.filter((e) => {
    if (e.type !== "notice") return false;
    const at = stampKey((e as { at?: unknown }).at);
    return !!at && at >= last && !shown.has(noticeKey(e));
  });
  return newer.length ? [...drawn, ...newer] : drawn;
}

/** v1.483.1: how many of the daemon's "Model changed" / "Effort changed"
 *  lines (hosted/words.model_change_words) are drawn. */
export function changeLines(events: readonly CodeEvent[]): number {
  let n = 0;
  for (const e of events) {
    const text = (e as { text?: unknown }).text;
    if (e.type === "notice" && typeof text === "string" && /^(Model|Effort) changed:/.test(text)) n += 1;
  }
  return n;
}

/** An ISO stamp in one comparable shape: its fraction padded to six digits
 *  (a whole-second stamp has none) and a plain "Z" ending; "" when it is not
 *  a stamp. */
export function stampKey(at: unknown): string {
  if (typeof at !== "string") return "";
  const m = /^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|\+00:00)?$/.exec(at);
  if (!m) return "";
  return `${m[1]}.${(m[2] ?? "").padEnd(9, "0")}Z`;
}

function noticeKey(e: CodeEvent): string {
  const r = e as { at?: unknown; text?: unknown };
  return `${stampKey(r.at)}\u0000${typeof r.text === "string" ? r.text : ""}`;
}

// ── rejoin ──────────────────────────────────────────────────────────────────

/**
 * Rejoining a running turn: the turn's stream REPLAYS the turn from its
 * start, while the session read already holds what was saved of it. So the
 * saved list loses every event of THAT turn (`turn === runningTurn`), and the
 * replay draws the turn again; its question is kept aside (`carry`) and put
 * back first when the replay does not begin with it. When no saved event
 * names the RUNNING turn (an older record, or one that names it differently)
 * the cut is made at the last `user`.
 */
export function cutForRejoin(
  events: readonly CodeEvent[],
  runningTurn: string | null = null,
): { base: CodeEvent[]; carry: CodeEvent | null } {
  // Cut by turn only when the record really holds the running turn (v1.409.2):
  // a turn the record names differently (or not yet) used to keep EVERY event,
  // its question included, and the replay drew that question a second time,
  // for good. Otherwise the old rule: cut at the last question.
  if (runningTurn && events.some((e) => e.turn === runningTurn)) {
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

/** Whether Enter may press an ask's main answer (Allow once). v1.417.0
 *  (C1.7): the same rule as Chat › Code (lib/dockAsk.enterAllows) — Enter
 *  allows ONCE on every card EXCEPT one that asks every time for a SAFETY
 *  reason (`once_reason`, lib/onceReason; none comes from a coding tool yet,
 *  the hook is kept), and never while the session runs in a dangerous mode
 *  (Full access: a card that still comes up there is the one the tool itself
 *  would not run alone). It used to do nothing on any card with no
 *  session-wide allow, silently. Esc still declines; the first moment after
 *  a card appears still takes no key (DOCK_KEY_ARM_MS). A question keeps
 *  Enter = Send. */
export function enterAllowsCodeAsk(ask: OpenAsk | null | undefined, dangerousMode: boolean): boolean {
  if (!ask || ask.type !== "approval") return true;
  if (dangerousMode) return false;
  // v1.441.0 (review D3): a plan card whose allow leaves the session in Full
  // access is a click, never a key — Enter presses the card's main allow, and
  // "Approve, then full access" is a posture change Full access's own rule
  // (two presses) keeps off the keyboard.
  if (ask.thenMode === "full" || ask.setsMode === "full") return false;
  return !(ask.onceOnly && onceReasonOf(ask.onceReason) !== undefined);
}

export const CODE_CLICK_TO_ALLOW_HINT = "Click Allow once to allow it · Esc to deny";

/** v1.441.0 (review D3): the hint names the button Enter presses (a plan
 *  card's main allow is "Approve, then …", never "Allow once"). */
export function codeAskKeyHint(ask: OpenAsk | null | undefined, dangerousMode: boolean): string {
  if (!ask) return "";
  if (ask.type === "question") return "Enter to send · Esc to skip";
  const named = planAllowLabels(ask).once;
  if (enterAllowsCodeAsk(ask, dangerousMode)) {
    return named ? `Enter to ${named.charAt(0).toLowerCase()}${named.slice(1)} · Esc to deny` : "Enter to allow once · Esc to deny";
  }
  return named ? `Click “${named}” to approve the plan · Esc to deny` : CODE_CLICK_TO_ALLOW_HINT;
}

/** v1.439.0: the modes a plan card's allow can leave the session in. */
const PLAN_MODES: readonly string[] = ["ask", "edits", "auto", "full"];

/** v1.439.0: a plan card's allow button, named by the mode it leaves the
 *  session in ("" = not known: the button keeps its usual words). */
export function planApproveWords(mode: string | undefined): string {
  switch (mode) {
    case "ask":
      return "Approve, then ask first";
    case "edits":
      return "Approve and edit freely";
    case "auto":
      return "Approve, then Auto";
    case "full":
      return "Approve, then full access";
    default:
      return "";
  }
}

/** v1.439.0: the two allow buttons of Claude Code's plan card — `once` for
 *  Allow once, `session` for the session-wide allow ("" = the usual words).
 *  When both leave the same mode, Allow once says only that it approves. */
export function planAllowLabels(a: Pick<ApprovalEvent, "thenMode" | "setsMode">): {
  once: string;
  session: string;
} {
  const session = planApproveWords(a.setsMode);
  const thenWords = planApproveWords(a.thenMode);
  const once = thenWords && thenWords === session ? "Approve the plan" : thenWords;
  return { once, session };
}

/** A step's words when the tool sent no title (the conversation's rows and
 *  the Markdown export share them). */
export function toolStepWords(t: ToolEvent): string {
  if (t.title) return t.title;
  const name = t.name || "a tool";
  switch (t.kind) {
    case "read":
      return t.path ? `Read ${t.path}` : "Read a file";
    case "edit":
      return t.path ? `Changed ${t.path}` : "Changed a file";
    case "write":
      return t.path ? `Wrote ${t.path}` : "Wrote a file";
    case "command":
      return "Ran a command";
    case "search":
      return "Searched";
    case "web":
      return "Looked on the web";
    default:
      return `Used ${name}`;
  }
}

/** v1.439.0: a session as Markdown, from the events this window already
 *  holds: the user's messages, the replies, and each step by its title —
 *  never a step's input or output, never thinking. */
export function conversationMarkdown(
  title: string,
  events: readonly CodeEvent[],
  toolName: string,
  /** v1.441.0 (review D1): lines said under the title (what is NOT in the file). */
  notes: readonly string[] = [],
): string {
  const { items } = foldConversation(events);
  const out: string[] = [`# ${title.trim() || "Coding session"}`];
  for (const n of notes) if (n.trim()) out.push(`_${n.trim()}_`);
  let speaker: "user" | "tool" | "" = "";
  const head = (who: "user" | "tool") => {
    if (speaker !== who) out.push(`## ${who === "user" ? "You" : toolName || "Assistant"}`);
    speaker = who;
  };
  for (const it of items) {
    if (it.kind === "user") {
      if (it.carry) continue;
      head("user");
      if (it.text.trim()) out.push(it.text.trim());
      if (it.files.length) out.push(`Attached: ${it.files.map((f) => f.split(/[\\/]/).pop() || f).join(", ")}`);
      speaker = "user";
    } else if (it.kind === "steer") {
      head("user");
      if (it.text.trim()) out.push(`> Note: ${it.text.trim()}`);
    } else if (it.kind === "text") {
      if (!it.text.trim()) continue;
      head("tool");
      out.push(it.text.trim());
    } else if (it.kind === "tool") {
      head("tool");
      out.push(`- ${toolStepWords(it.tool).replace(/\s+/g, " ").trim()}`);
    }
  }
  return `${out.join("\n\n")}\n`;
}

/** v1.441.0 (review D1): what the export says under its title about the part
 *  of the session it does not hold. */
export function exportGapWords(dropped: number, unread: boolean): string[] {
  const out: string[] = [];
  if (dropped > 0) {
    out.push(
      dropped === 1
        ? "1 older step was trimmed from this session's record and is not in this file."
        : `${dropped.toLocaleString("en-US")} older steps were trimmed from this session's record and are not in this file.`,
    );
  }
  if (unread) out.push("The earlier part of this session could not be read, so it is not in this file.");
  return out;
}

/** v1.441.0 (review D2): the export, refused in words. */
export const EXPORT_NOT_READ = "This session has not been read yet, so there is nothing to export.";
export const EXPORT_NOTHING = "There is nothing in this session to export yet.";
/** v1.441.0 review: said while the export reads the session's earlier pages
 *  (up to 200 of them, one after another). */
export const EXPORT_READING = "Reading the whole session for the export…";

/** Does a session hold anything the export would write (a message, a reply
 *  or a step)? */
export function hasExportableItems(events: readonly CodeEvent[]): boolean {
  return foldConversation(events).items.some(
    (it) =>
      (it.kind === "user" && !it.carry && (it.text.trim() !== "" || it.files.length > 0)) ||
      (it.kind === "steer" && it.text.trim() !== "") ||
      (it.kind === "text" && it.text.trim() !== "") ||
      it.kind === "tool",
  );
}

/** v1.439.0: the export's file name — the session's title, made safe for
 *  every file system, ending ".md". */
export function exportFileName(title: string): string {
  const base = title
    .replace(/[<>:"/\\|?*\u0000-\u001f]/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/[. ]+$/g, "")
    .slice(0, 80)
    .trim();
  return `${base || "coding-session"}.md`;
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
  if (decision === "allow_commands") return "Allowed all commands for this session";
  if (decision === "answered") return "Answered";
  return "Allowed";
}

/** v1.412.0: what became of a note sent while the tool worked. */
export function steerWords(state: SteerState, how: string, toolName: string): string {
  if (state === "read") return `Read by ${toolName}.`;
  if (state === "returned") return STEER_RETURNED_WORDS(toolName);
  return how || `Sent — ${toolName} reads it at its next step.`;
}

/** v1.412.0: a note the tool never read (said under it AND above the box). */
export function STEER_RETURNED_WORDS(toolName: string): string {
  return `${toolName} did not read this note before it stopped — it is back in your message box.`;
}

/** v1.412.0: the box's placeholder while the tool works (Enter steers it). */
export function steerPlaceholder(toolName: string): string {
  return `${toolName} is working — type a note to steer it (Enter), or press Stop.`;
}

/** How a turn ended, said under it (nothing for an ordinary finish). */
export function doneWords(stop: Extract<CodeEvent, { type: "done" }>["stop"]): string {
  if (stop === "interrupted") return "Stopped.";
  if (stop === "failed") return "This reply did not finish.";
  if (stop === "max_turns") return "Stopped at its turn limit.";
  return "";
}

/** The usage line ("1,240 in · 310 out · about $0.02 at list price"). A
 *  cost only the tool can vouch for (Pi prices a subscription and a
 *  pay-per-use key alike) says who reported it, claiming neither. */
export function usageWords(u: {
  input: number;
  output: number;
  costUsd: number | null;
  listPrice: boolean;
  costReportedBy?: string;
}): string {
  const parts = [`${u.input.toLocaleString("en-US")} in`, `${u.output.toLocaleString("en-US")} out`];
  if (u.costUsd !== null && u.costUsd > 0) {
    const dollars = u.costUsd < 0.01 ? "under $0.01" : `$${u.costUsd.toFixed(2)}`;
    if (u.listPrice) parts.push(`about ${dollars} at list price`);
    else if (u.costReportedBy) parts.push(`${dollars}, the cost ${u.costReportedBy} reported`);
    else parts.push(dollars);
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

const FIELD_WORDS: Record<string, string> = {
  model: "The model",
  effort: "The effort",
  mode: "The mode",
  // v1.414.0: turning Iron Jarvis's tools on or off for the session.
  capabilities: "The change to Iron Jarvis's tools",
};

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

/** The /health provider id of a coding tool (the Sign in press reads it).
 *  v1.439.0: Grok too — its "isn't signed in" error carries fix_kind
 *  sign_in with provider grok-cli (lib/cliSignIn GROK_SIGN_IN). Pi and
 *  OpenCode sign in per provider in their own screens: no press. */
export function signInProvider(tool: string): string {
  if (tool === "claude-code") return "claude-cli";
  if (tool === "codex") return "codex-cli";
  if (tool === "grok") return "grok-cli";
  return "";
}

/** "Continue on another account": the words of the press. */
export const CONTINUE_ON_ANOTHER = "Continue on another account";

/** v1.427.3 (a USER BUG): Continue on another account found none free, but
 *  Iron-Proxy's "at its limit" can be Iron Jarvis's own guess — the accounts
 *  the daemon offers to try anyway (`choices` of its `none_free` refusal). */
export interface TryChoice {
  id: string;
  title: string;
  /** "at its usage limit until 10:30 PM" — the button's tooltip. */
  why: string;
}

// Wave D item 33 (canon row 29): "your accounts", never the account manager's
// program name, on a coding screen.
export const TRY_ACCOUNTS_LEAD = "These accounts are marked as at their limit. If you know one still has usage:";

export function tryAccountWords(title: string): string {
  return `Try ${title} anyway`;
}

/** The `choices` of a refused Continue on another account (`ApiError.body`),
 *  or [] for anything else. */
export function decodeTryChoices(body: unknown): TryChoice[] {
  if (!body || typeof body !== "object") return [];
  const o = body as { code?: unknown; choices?: unknown };
  if (o.code !== "none_free" || !Array.isArray(o.choices)) return [];
  const out: TryChoice[] = [];
  for (const c of o.choices.slice(0, 10)) {
    if (!c || typeof c !== "object") continue;
    const r = c as { id?: unknown; title?: unknown; why?: unknown };
    if (typeof r.id !== "string" || !r.id) continue;
    out.push({
      id: r.id,
      title: typeof r.title === "string" && r.title ? r.title : r.id,
      why: typeof r.why === "string" ? r.why : "",
    });
  }
  return out;
}

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
  const out = next.map((it) => {
    const old = byKey.get(it.key);
    if (!old) return it;
    if (sameValue(old, it)) return old;
    // v1.418.0 review: a helper step that changed keeps the children that
    // did not (an open helper's rows draw again only for their own change).
    if (it.kind === "tool" && old.kind === "tool" && it.children && old.children) {
      return { ...it, children: reuseItems(old.children, it.children) };
    }
    return it;
  });
  // v1.417.0: nothing changed at all (a read again while helpers work): the
  // SAME list, so nothing draws and "Jump to latest" never lights up for it.
  if (out.length === prev.length && out.every((it, i) => it === prev[i])) return prev as ConvItem[];
  return out;
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
