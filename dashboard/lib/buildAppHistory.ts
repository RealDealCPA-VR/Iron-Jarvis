/**
 * BUILD › CODE, v1.418.0: history that stays (C4.6), keys and a queue like
 * the chat's (C4.8). Pure decoders and words — the view
 * (components/buildCode/BuildCodeView) reads the daemon's answers through
 * these; lib/buildApp keeps the events' own contract.
 *
 * - `older` (a view's or a page's): the cursor "Load earlier" reads before
 *   (`GET /build/app/sessions/{id}/events?before=…`), null when nothing is
 *   older. `events_total` beyond what a read holds = "N earlier steps".
 * - `queued` / `queue_held`: the messages waiting behind the running reply;
 *   held = the reply ended any way but cleanly (Stop, a failure, an account's
 *   limit card), so the page puts the words back in the box.
 * - The `/` menu opens on `/` at the start of ANY line, up to 20 rows, with
 *   each command's argument hint.
 * - Shift+Tab steps the mode chip through its ordinary modes — never INTO
 *   Full access (that stays two presses in the menu).
 * - Which chips change while a reply runs: a change the tool takes without
 *   starting again stays open; one that needs a new start is held, with a
 *   title saying why.
 */

import { decodeCodeEvent, isFullAccess, type CodeEvent, type CodingTool } from "@/lib/buildApp";

function obj(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function str(v: unknown, max = 2_000): string {
  return typeof v === "string" ? (v.length > max ? v.slice(0, max) : v) : "";
}

function count(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? Math.floor(v) : 0;
}

// ── history (C4.6) ──────────────────────────────────────────────────────────

/** What a view says about what is older than it. */
export interface OlderInfo {
  /** The cursor to read the page before (null = nothing older). */
  cursor: string | null;
  /** Events in the record beyond those the read holds (0 = all shown). */
  earlier: number;
}

/** A view's `older` and `events_total` against the events it holds. */
export function olderOf(raw: unknown, held: number): OlderInfo {
  const r = obj(raw);
  if (!r) return { cursor: null, earlier: 0 };
  const cursor = str(r.older, 200).trim() || null;
  const total = count(r.events_total);
  const earlier = Math.max(0, total - Math.max(0, held));
  return { cursor, earlier: cursor ? Math.max(earlier, 1) : earlier };
}

/** One "Load earlier" page. */
export interface EventsPage {
  events: CodeEvent[];
  /** The cursor of the page before this one (null = this was the first). */
  older: string | null;
  /** How many events are older still. */
  earlier: number;
}

export function decodeEventsPage(raw: unknown): EventsPage {
  const r = obj(raw);
  const events: CodeEvent[] = [];
  for (const e of Array.isArray(r?.events) ? (r!.events as unknown[]) : []) {
    const ev = decodeCodeEvent(e);
    if (ev) events.push(ev);
  }
  const older = r ? str(r.older, 200).trim() || null : null;
  return { events, older, earlier: older ? Math.max(1, count(r?.earlier)) : 0 };
}

/** The line above the conversation when older steps are not shown. */
export function earlierStepsWords(n: number): string {
  if (n <= 0) return "";
  return n === 1 ? "1 earlier step" : `${n.toLocaleString("en-US")} earlier steps`;
}

export const LOAD_EARLIER = "Load earlier";

/** The page path (the cursor is the daemon's own). */
export function eventsPagePath(sessionsPath: string, id: string, cursor: string, limit = 500): string {
  return `${sessionsPath}/${encodeURIComponent(id)}/events?before=${encodeURIComponent(cursor)}&limit=${limit}`;
}

/** Events loaded earlier, put in front of those loaded before them (the
 *  daemon's cursor is exact: a page ends right before the oldest shown). */
export function prependEvents(page: readonly CodeEvent[], shown: readonly CodeEvent[]): CodeEvent[] {
  return [...page, ...shown];
}

// ── the queue (C4.8) ────────────────────────────────────────────────────────

export interface QueuedMessage {
  id: string;
  text: string;
  files: string[];
}

export interface QueueState {
  queued: QueuedMessage[];
  /** The reply ended but not cleanly: the words go back to the box. */
  held: boolean;
}

export function decodeQueue(raw: unknown): QueueState | null {
  const r = obj(raw);
  if (!r || !Array.isArray(r.queued)) return null;
  const queued: QueuedMessage[] = [];
  for (const q of r.queued as unknown[]) {
    const o = obj(q);
    const id = o ? str(o.id, 80).trim() : "";
    if (!o || !id) continue;
    queued.push({
      id,
      text: str(o.text, 100_000),
      files: Array.isArray(o.files) ? o.files.filter((f): f is string => typeof f === "string").slice(0, 20) : [],
    });
  }
  return { queued, held: r.queue_held === true };
}

/** Words returned to the box from held messages, after what is typed. */
export function wordsBack(typed: string, items: readonly QueuedMessage[]): string {
  const back = items.map((i) => i.text.trim()).filter(Boolean).join("\n\n");
  if (!back) return typed;
  return typed.trim() ? `${typed.replace(/\s+$/, "")}\n\n${back}` : back;
}

export function queuedBackWords(toolName: string, n: number): string {
  return n === 1
    ? `${toolName} stopped before your waiting message went — it is back in your message box.`
    : `${toolName} stopped before your ${n} waiting messages went — they are back in your message box.`;
}

/** The box's placeholder while the tool works (C4.8: Enter queues, Shift+Enter
 *  sends a note to the running reply). */
export function workingPlaceholder(toolName: string): string {
  return `${toolName} is working — Enter queues your message for when it finishes; Shift+Enter sends it a note now.`;
}

/** A queued row's one line. */
export function queuedLine(q: QueuedMessage): string {
  const text = q.text.trim().split(/\r?\n/)[0] ?? "";
  if (text) return text.length > 120 ? `${text.slice(0, 119)}…` : text;
  return q.files.length === 1 ? "1 file" : `${q.files.length} files`;
}

export const MAX_QUEUED = 5;

// ── the `/` menu (C4.8) ─────────────────────────────────────────────────────

/** `/word` at the START of a line, ending at the caret: where it starts and
 *  what is typed after the slash. null = no `/` menu. */
export function slashAt(text: string, caret: number = text.length): { start: number; query: string } | null {
  const before = text.slice(0, Math.max(0, Math.min(caret, text.length)));
  const m = /(^|\n)\/(\S*)$/.exec(before);
  if (!m) return null;
  return { start: m.index + m[1].length, query: m[2] };
}

/** The text with the `/word` at `start` replaced by the picked command —
 *  v1.426.0 (E4): typed with its own prefix (a Codex skill is `$<name>`). */
export function pickCommand(
  text: string,
  at: { start: number; query: string },
  name: string,
  prefix: string = "/",
): { text: string; caret: number } {
  const end = at.start + 1 + at.query.length;
  const inserted = `${prefix}${name} `;
  return { text: text.slice(0, at.start) + inserted + text.slice(end), caret: at.start + inserted.length };
}

export const SLASH_ROWS = 20;

/** Each command's argument hint, from a view's or a list's `commands`. */
export function commandHints(raw: unknown): Record<string, string> {
  const r = obj(raw);
  const list = Array.isArray(raw) ? raw : r && Array.isArray(r.commands) ? (r.commands as unknown[]) : [];
  const out: Record<string, string> = {};
  for (const c of list) {
    const o = obj(c);
    const name = o ? str(o.name, 80).trim().replace(/^\//, "") : "";
    const hint = o ? str(o.argument_hint, 80).trim() : "";
    if (name && hint) out[name] = hint;
  }
  return out;
}

// ── Shift+Tab (C4.8) ────────────────────────────────────────────────────────

/** The next ordinary mode after `current` (Full access is never stepped
 *  into; from Full access the first ordinary mode). null = nothing to step. */
export function nextMode(tool: CodingTool | null | undefined, current: string): string | null {
  const ordinary = (tool?.modes ?? []).filter((m) => !m.dangerous && !isFullAccess(m.id, tool));
  if (ordinary.length === 0) return null;
  const i = ordinary.findIndex((m) => m.id === current);
  const next = ordinary[(i + 1) % ordinary.length];
  return next && next.id !== current ? next.id : null;
}

// ── chips while a reply runs (C4.8) ─────────────────────────────────────────

/** The tools that take a new model WITHOUT starting again (Claude Code's
 *  `set_model`, Pi's and OpenCode's own switch; Codex from the next
 *  message). Going back to the tool's DEFAULT needs a new start for Claude
 *  Code, Pi and OpenCode. */
const RESTART_FOR_DEFAULT_MODEL = new Set(["claude-code", "pi", "opencode"]);
/** Claude Code reads its effort when it starts; Pi and OpenCode change theirs
 *  live (back to the default is a new start); Codex from the next message. */
const RESTART_FOR_EFFORT = new Set(["claude-code"]);
const RESTART_FOR_DEFAULT_EFFORT = new Set(["claude-code", "pi", "opencode"]);

export interface LiveChange {
  /** The whole chip is held while the reply runs. */
  held: boolean;
  /** Its default row is held while the reply runs. */
  defaultHeld: boolean;
  /** Why (the chip's or the row's title), "" when nothing is held. */
  title: string;
}

export function modelWhileRunning(tool: string, toolName: string, running: boolean): LiveChange {
  if (!running) return { held: false, defaultHeld: false, title: "" };
  const defaultHeld = RESTART_FOR_DEFAULT_MODEL.has(tool);
  return {
    held: false,
    defaultHeld,
    title: defaultHeld ? `Going back to ${toolName}'s default starts it again — choose it once this reply ends.` : "",
  };
}

export function effortWhileRunning(
  tool: string,
  toolName: string,
  running: boolean,
  /** v1.441.0 (g1): the daemon says this tool takes a new effort live
   *  (`effort_live`, Claude Code's apply_flag_settings) — nothing is held. */
  effortLive = false,
): LiveChange {
  if (!running || effortLive) return { held: false, defaultHeld: false, title: "" };
  if (RESTART_FOR_EFFORT.has(tool)) {
    return {
      held: true,
      defaultHeld: true,
      title: `${toolName} takes a new effort when it starts again — change it once this reply ends.`,
    };
  }
  const defaultHeld = RESTART_FOR_DEFAULT_EFFORT.has(tool);
  return {
    held: false,
    defaultHeld,
    title: defaultHeld ? `Going back to ${toolName}'s default effort starts it again — choose it once this reply ends.` : "",
  };
}
