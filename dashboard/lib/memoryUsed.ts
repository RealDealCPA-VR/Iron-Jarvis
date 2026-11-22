/**
 * FROM MEMORY (next leaps 15-a, master plan R2.7, decision D-15.1).
 *
 * A chat reply carries `used`: what Iron Jarvis was GIVEN from memory for it
 * (the daemon's `memory/used.py`, recorded where each grounding hop built its
 * text, so it never lists more than the turn was told). This module decodes
 * it, says it in plain words and holds the pop-up's rules:
 *
 * - the pop-up appears BY ITSELF only for the newest reply, only while that
 *   chat is on screen, only when memory shaped the reply (an item with
 *   `shaped`: the reply carries words from it that the owner did not type),
 *   never twice for the same items in one chat (an item already shown in
 *   this chat, by itself or by a press, does not bring it back), and never
 *   when Settings says "Only when I press" (`memory_used_popup`);
 * - it fades after {@link SHOW_MS}, and stays while the pointer or the
 *   keyboard focus is on it, or a correction is being typed;
 * - "From memory: N" under the reply opens it again.
 *
 * The heading is "From memory", never "I used": the list is what Jarvis was
 * given, not a claim about what the model relied on.
 */

import { clientPrefPath, isClientPrefId } from "@/lib/clients";

/** How long the pop-up stays when nothing holds it (about eight seconds). */
export const SHOW_MS = 8000;
/** How many items the pop-up lists; the rest are in the reply's details. */
export const SHOWN_MAX = 5;
/** The most items one reply carries (the daemon's cap; what it was given
 *  past it is COUNTED as `used_more`, never dropped without a word). */
export const USED_MAX = 24;

export type MemoryKind =
  | "preference"
  | "client_preference"
  | "work"
  | "recap"
  | "memory"
  | "note"
  | "chat"
  | "run"
  | "file"
  | "project";

export type MemoryFix = "correct" | "forget";

export interface MemoryUsedItem {
  kind: MemoryKind;
  id: string;
  label: string;
  /** ISO time, or "". */
  when: string;
  /** A page in the app, or "". */
  href: string;
  fix: MemoryFix[];
  /** The reply carries words that came from this item (not from the owner's message). */
  shaped: boolean;
  /** A client's preference: the client it is kept for. */
  client?: string;
}

const KINDS: readonly MemoryKind[] = [
  "preference",
  "client_preference",
  "work",
  "recap",
  "memory",
  "note",
  "chat",
  "run",
  "file",
  "project",
];

/** What each kind is called in the list. */
export const KIND_WORDS: Record<MemoryKind, string> = {
  preference: "Your preference",
  client_preference: "Kept for this client",
  work: "Earlier work",
  recap: "Recap of earlier work",
  memory: "Memory",
  note: "Note",
  chat: "Earlier chat",
  run: "Agent run",
  file: "File",
  project: "Project knowledge",
};

export const HEADING = "From memory";
export const HEADING_NOTE = "What Iron Jarvis was given for this reply.";
export const CLOSE_WORDS = "Close";
export const WRONG = "That's wrong";
export const RIGHT_PLACEHOLDER = "The right preference";
export const SAVE = "Save";
export const FORGET = "Forget it";
export const CANCEL = "Cancel";
export const FIXED = "Fixed.";
export const FORGOTTEN = "Forgotten.";
export const UNDO = "Undo";
export const PUT_BACK = "Put back as it was.";
export const NO_UNDO = "This one can't be undone here.";
export const OPEN = "Open";

/** "From memory: 3". */
export function lineWords(n: number): string {
  return `${HEADING}: ${n}`;
}

/** "and 2 more in the reply's details" (the pop-up lists five); with
 *  `notListed`, ", 4 more not listed" (past the cap of {@link USED_MAX}). */
export function moreWords(n: number, notListed = 0): string {
  const head = `and ${n} more in the reply's details`;
  return notListed > 0 ? `${head}, ${notListed} more not listed` : head;
}

/** "and 4 more not listed here": what the reply was given past the cap of
 *  {@link USED_MAX} (the daemon's `used_more`), in the reply's details. */
export function notListedWords(n: number): string {
  return `and ${n} more not listed here`;
}

/** The reply's `used_more` (how many more it was given than `used` names):
 *  a whole number above 0, else 0. Never throws. */
export function decodeUsedMore(raw: unknown): number {
  return typeof raw === "number" && Number.isInteger(raw) && raw > 0 ? Math.min(raw, 100000) : 0;
}

function str(v: unknown, max: number): string {
  return typeof v === "string" ? v.slice(0, max) : "";
}

/** The reply's `used` list, whitelisted (a reopened chat hands back whatever
 *  the disk held). Never throws; [] for anything else. */
export function decodeMemoryUsed(raw: unknown): MemoryUsedItem[] {
  if (!Array.isArray(raw)) return [];
  const out: MemoryUsedItem[] = [];
  const seen = new Set<string>();
  for (const r of raw) {
    if (!r || typeof r !== "object") continue;
    const o = r as Record<string, unknown>;
    const kind = o.kind as MemoryKind;
    if (!KINDS.includes(kind)) continue;
    const id = str(o.id, 200).trim();
    const label = str(o.label, 80).trim();
    if (!id || !label) continue;
    const key = `${kind}:${id}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const href = str(o.href, 300);
    const fix = Array.isArray(o.fix)
      ? (o.fix.filter((f) => f === "correct" || f === "forget") as MemoryFix[])
      : [];
    const item: MemoryUsedItem = {
      kind,
      id,
      label,
      when: str(o.when, 40),
      href: href.startsWith("/") && !href.startsWith("//") ? href : "",
      fix: Array.from(new Set(fix)),
      shaped: o.shaped === true,
    };
    if (kind === "client_preference") {
      const client = str(o.client, 40);
      if (/^client_[0-9a-f]{12}$/.test(client) && isClientPrefId(id)) item.client = client;
      else item.fix = [];
    }
    out.push(item);
    if (out.length >= USED_MAX) break;
  }
  return out;
}

/** One item's key: the same item in two replies is the same key. */
export function itemKey(it: Pick<MemoryUsedItem, "kind" | "id">): string {
  return `${it.kind}:${it.id}`;
}

/** "Mar 12" for an ISO time, "" for none. */
export function whenWords(iso: string): string {
  if (!iso) return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  try {
    return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return "";
  }
}

/** The whole line for one item: "Your preference: Prices in euros". */
export function itemWords(it: MemoryUsedItem): string {
  return `${KIND_WORDS[it.kind]}: ${it.label}`;
}

export type PopupMode = "auto" | "press";

/** The setting's value (`memory_used_popup`): only "press" turns the
 *  pop-up off by itself; anything else is the default. */
export function decodePopupMode(v: unknown): PopupMode {
  return v === "press" ? "press" : "auto";
}

/** What a chat message carries for this (a subset of the page's ChatMessage). */
export interface UsedCarrier {
  role?: string;
  used?: unknown;
  usedShown?: boolean;
  usedMore?: unknown;
}

/** Every item this chat has already shown in a pop-up (by itself or by a
 *  press): the items of each message marked `usedShown`. */
export function shownKeys(messages: readonly UsedCarrier[]): Set<string> {
  const out = new Set<string>();
  for (const m of messages) {
    if (!m || m.role !== "assistant" || m.usedShown !== true) continue;
    for (const it of decodeMemoryUsed(m.used)) out.add(itemKey(it));
  }
  return out;
}

/**
 * Does the NEWEST reply of `messages` open its pop-up by itself? Only when
 * the setting allows it, the reply is the last message, it carries an item
 * memory shaped it with, and at least one such item was not shown in this
 * chat before. (Whether the chat is on screen is the page's to say.)
 */
export function opensByItself(
  messages: readonly UsedCarrier[],
  mode: PopupMode,
  /** Items this chat showed this visit that a message may not carry yet. */
  alsoShown?: ReadonlySet<string>,
): boolean {
  if (mode !== "auto" || messages.length === 0) return false;
  const last = messages[messages.length - 1];
  if (!last || last.role !== "assistant" || last.usedShown === true) return false;
  const used = decodeMemoryUsed(last.used);
  if (!used.length) return false;
  const shown = shownKeys(messages.slice(0, -1));
  return used.some((it) => it.shaped && !shown.has(itemKey(it)) && !alsoShown?.has(itemKey(it)));
}

/** The items the pop-up lists: those memory shaped the reply with first,
 *  then the rest, at most {@link SHOWN_MAX}. */
export function popupItems(used: readonly MemoryUsedItem[]): MemoryUsedItem[] {
  const ordered = [...used.filter((u) => u.shaped), ...used.filter((u) => !u.shaped)];
  return ordered.slice(0, SHOWN_MAX);
}

/** Can this item be corrected or forgotten from the pop-up (15-a: a kept
 *  preference, and a client's preference)? */
export function canFix(it: MemoryUsedItem): boolean {
  if (!it.fix.length) return false;
  if (it.kind === "preference") return true;
  return it.kind === "client_preference" && !!it.client;
}

/** The route a fix calls: the EXISTING routes of the store it is kept in. */
export function fixPath(it: MemoryUsedItem): string {
  if (it.kind === "preference") return `/memory/preferences/${encodeURIComponent(it.id)}`;
  if (it.kind === "client_preference" && it.client) return clientPrefPath(it.client, it.id);
  throw new Error("This item cannot be changed here.");
}

/** The Undo id a fix's answer carries ("" when it could not be recorded). */
export function actionIdOf(res: unknown): string {
  if (!res || typeof res !== "object") return "";
  const id = (res as { action_id?: unknown }).action_id;
  return typeof id === "string" ? id : "";
}
