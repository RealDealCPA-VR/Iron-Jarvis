/**
 * Your day (next leaps 40-a, master plan R2.18, idea 40 "Rewind the day"):
 * the Activity page's "Your day" tab.
 *
 * The wire, from `src/iron_jarvis/daemon/routes/undo.py`
 * (`settings/records_day.py` reads it):
 *   GET  /changes/day?date=YYYY-MM-DD     one local day, grouped by where each
 *                                         change came from; what cannot be
 *                                         undone apart
 *   GET  /changes/{id}/before-after       one change's before and after, read
 *                                         on this PC (D-40.2)
 *   POST /changes/undo {action_ids}       "Undo these": newest first, after the
 *                                         one confirmation that lists them
 *                                         (D-40.1); at most 100
 *
 * Every read is decoded defensively: a file's or a run's name is text, never
 * markup. Its own file (not lib/api.ts, which ~70 test files mock).
 */

import { get, post } from "@/lib/api";

/** The Activity page's tabs (`?view=day`). Not a page of its own (G2). */
export const DAY_VIEW = "day";
export const TIMELINE_VIEW = "timeline";
export type ActivityView = typeof DAY_VIEW | typeof TIMELINE_VIEW;
/** Where the day view lives (one address per place). */
export const DAY_HREF = "/activity?view=day";
/** The most changes one "Undo these" puts back (the daemon's own cap). */
export const UNDO_MANY_MAX = 100;
/** A refresh after a change lands, while today is on screen (G7). */
export const DAY_REFRESH_MS = 4000;
/** The events that mean today's list may have changed. */
export const DAY_EVENT_TYPES = ["action.reverted", "tool.executed"] as const;

export type ChangeState = "can" | "undone" | "cannot";
export type SourceKind = "chat" | "run" | "schedule" | "coding" | "settings" | "other";

export interface DaySource {
  kind: SourceKind;
  name: string;
  id: string;
  href: string;
  started: string | null;
}

export interface DayChange {
  id: string;
  at: string | null;
  hour: number | null;
  clock: string;
  kind: string;
  target: string;
  what: string;
  state: ChangeState;
  why: string;
  hasBeforeAfter: boolean;
}

export interface DayCannot {
  id: string;
  ids: string[];
  at: string | null;
  clock: string;
  firstClock: string;
  what: string;
  why: string;
  count: number;
  source: DaySource | null;
}

export interface DayGroup {
  source: DaySource;
  changes: DayChange[];
}

export interface DayChanges {
  date: string;
  words: string;
  today: boolean;
  groups: DayGroup[];
  cannot: DayCannot[];
  count: number;
  more: boolean;
}

export interface Side {
  exists: boolean;
  kept: boolean;
  size: number | null;
  modified: string | null;
  words: string;
  text: string | null;
}

export interface BeforeAfterItem {
  name: string;
  isText: boolean;
  diff: string[];
  cut: boolean;
  before: Side;
  now: Side;
}

export interface BeforeAfterView {
  id: string;
  kind: string;
  what: string;
  note: string;
  items: BeforeAfterItem[];
}

export interface UndoManyRow {
  id: string;
  target: string;
  what: string;
  kind: string;
  reason: string;
}

export interface UndoManyResult {
  reverted: UndoManyRow[];
  skipped: UndoManyRow[];
  words: string;
}

/* ---- decoders -------------------------------------------------------- */

const str = (v: unknown, max = 400): string => (typeof v === "string" ? v.slice(0, max) : "");
const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const obj = (v: unknown): Record<string, unknown> =>
  v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : {};
const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

const KINDS: readonly SourceKind[] = ["chat", "run", "schedule", "coding", "settings", "other"];
const STATES: readonly ChangeState[] = ["can", "undone", "cannot"];

/** A source's address: only a page of this app (a leading "/", never "//"). */
function safeHref(v: unknown): string {
  const h = str(v, 300);
  return h.startsWith("/") && !h.startsWith("//") ? h : "";
}

export function decodeSource(raw: unknown): DaySource | null {
  const r = obj(raw);
  const id = str(r.id, 200);
  if (!id && !str(r.name)) return null;
  const kind = KINDS.includes(r.kind as SourceKind) ? (r.kind as SourceKind) : "other";
  return {
    kind,
    name: str(r.name, 120) || "Iron Jarvis itself",
    id,
    href: safeHref(r.href),
    started: str(r.started, 60) || null,
  };
}

export function decodeChange(raw: unknown): DayChange | null {
  const r = obj(raw);
  const id = str(r.id, 120);
  if (!id) return null;
  return {
    id,
    at: str(r.at, 60) || null,
    hour: num(r.hour),
    clock: str(r.clock, 20),
    kind: str(r.kind, 20) || "other",
    target: str(r.target, 200),
    what: str(r.what, 300) || "a change",
    state: STATES.includes(r.state as ChangeState) ? (r.state as ChangeState) : "cannot",
    why: str(r.why, 300),
    hasBeforeAfter: r.has_before_after === true,
  };
}

export function decodeCannot(raw: unknown): DayCannot | null {
  const r = obj(raw);
  const id = str(r.id, 120);
  if (!id) return null;
  const ids = arr(r.ids).map((x) => str(x, 120)).filter(Boolean);
  return {
    id,
    ids: ids.length ? ids : [id],
    at: str(r.at, 60) || null,
    clock: str(r.clock, 20),
    firstClock: str(r.first_clock, 20),
    what: str(r.what, 300) || "an action",
    why: str(r.why, 300),
    count: Math.max(1, num(r.count) ?? 1),
    source: decodeSource(r.source),
  };
}

export function decodeDay(raw: unknown): DayChanges {
  const r = obj(raw);
  const groups: DayGroup[] = [];
  for (const g of arr(r.groups)) {
    const go = obj(g);
    const source = decodeSource(go.source) ?? {
      kind: "other" as const, name: "Iron Jarvis itself", id: "", href: "", started: null,
    };
    const changes = arr(go.rows).map(decodeChange).filter((x): x is DayChange => x !== null);
    if (changes.length) groups.push({ source, changes });
  }
  return {
    date: str(r.date, 10),
    words: str(r.words, 60),
    today: r.today === true,
    groups,
    cannot: arr(r.cannot).map(decodeCannot).filter((x): x is DayCannot => x !== null),
    count: num(r.count) ?? groups.reduce((n, g) => n + g.changes.length, 0),
    more: r.more === true,
  };
}

function decodeSide(raw: unknown): Side {
  const r = obj(raw);
  return {
    exists: r.exists !== false,
    kept: r.kept !== false,
    size: num(r.size),
    modified: str(r.modified, 60) || null,
    words: str(r.words, 400),
    text: typeof r.text === "string" ? r.text.slice(0, 20_000) : null,
  };
}

export function decodeBeforeAfter(raw: unknown): BeforeAfterView {
  const r = obj(raw);
  return {
    id: str(r.id, 120),
    kind: str(r.kind, 20),
    what: str(r.what, 300),
    note: str(r.note, 300),
    items: arr(r.items).slice(0, 50).map((i) => {
      const o = obj(i);
      return {
        name: str(o.name, 200) || "a change",
        isText: o.text === true,
        diff: arr(o.diff).slice(0, 1500).map((l) => str(l, 2000)),
        cut: o.cut === true,
        before: decodeSide(o.before),
        now: decodeSide(o.now),
      };
    }),
  };
}

function decodeUndoRow(raw: unknown): UndoManyRow {
  const r = obj(raw);
  return { id: str(r.id, 120), target: str(r.target, 200), what: str(r.what, 300), kind: str(r.kind, 20), reason: str(r.reason, 300) };
}

export function decodeUndoMany(raw: unknown): UndoManyResult {
  const r = obj(raw);
  return {
    reverted: arr(r.reverted).map(decodeUndoRow),
    skipped: arr(r.skipped).map(decodeUndoRow),
    words: str(r.words, 2000),
  };
}

/* ---- reads and the press --------------------------------------------- */

export async function readDay(date: string): Promise<DayChanges> {
  return decodeDay(await get<unknown>(`/changes/day?date=${encodeURIComponent(date)}`));
}

export async function readBeforeAfter(id: string): Promise<BeforeAfterView> {
  return decodeBeforeAfter(await get<unknown>(`/changes/${encodeURIComponent(id)}/before-after`));
}

/** "Undo these": the daemon puts them back newest first, whatever the order. */
export async function undoMany(ids: string[]): Promise<UndoManyResult> {
  return decodeUndoMany(await post<unknown>("/changes/undo", { action_ids: ids.slice(0, UNDO_MANY_MAX) }));
}

/* ---- days and words -------------------------------------------------- */

const pad = (n: number) => String(n).padStart(2, "0");

/** A day on this PC's calendar as YYYY-MM-DD. */
export function dayOf(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function todayLocal(now: Date = new Date()): string {
  return dayOf(now);
}

/** *day* moved by *by* days (local calendar). */
export function shiftDay(day: string, by: number): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!m) return day;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  d.setDate(d.getDate() + by);
  return dayOf(d);
}

export function isDay(v: string | null | undefined): v is string {
  return !!v && /^\d{4}-\d{2}-\d{2}$/.test(v);
}

/** The tab (and day) an address asks for: `?view=day&date=2026-11-22`. */
export function viewFromSearch(search: string): { view: ActivityView | null; date: string | null } {
  let q: URLSearchParams;
  try {
    q = new URLSearchParams(search);
  } catch {
    return { view: null, date: null };
  }
  const v = q.get("view");
  const date = q.get("date");
  return { view: v === DAY_VIEW ? DAY_VIEW : v === TIMELINE_VIEW ? TIMELINE_VIEW : null, date: isDay(date) ? date : null };
}

/** "3 pm" for an hour of the day. */
export function hourWords(hour: number | null): string {
  if (hour === null || hour < 0 || hour > 23) return "Earlier";
  const h12 = hour % 12 || 12;
  return `${h12} ${hour < 12 ? "am" : "pm"}`;
}

/** A change's rows split by the hour they were made, newest hour first. */
export function byHour(changes: DayChange[]): { hour: number | null; changes: DayChange[] }[] {
  const out: { hour: number | null; changes: DayChange[] }[] = [];
  for (const c of changes) {
    const last = out[out.length - 1];
    if (last && last.hour === c.hour) last.changes.push(c);
    else out.push({ hour: c.hour, changes: [c] });
  }
  return out;
}

/** A change's name in the confirmation: the thing it changed, else what it did. */
export function changeName(c: DayChange): string {
  return c.target || c.what;
}

/** "12 bytes", "4.2 KB". */
export function sizeWords(n: number | null): string {
  if (n === null) return "";
  if (n < 1024) return `${n} byte${n === 1 ? "" : "s"}`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}
